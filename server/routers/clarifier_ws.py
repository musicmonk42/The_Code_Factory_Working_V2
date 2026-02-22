# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
clarifier_ws.py — WebSocket Real-Time Clarifier Q&A Endpoint

Provides a WebSocket-based channel for interactive clarification questions
during code-generation jobs, replacing the blocking form-based flow with
real-time bidirectional communication.  The module exposes three FastAPI
routes: a WebSocket endpoint for live Q&A, an HTTP POST for backend question
injection, and an HTTP GET for session status queries.

Key Features
------------
- **Per-Job WebSocket Sessions:** Each generation job receives its own
  isolated session with automatic lifecycle management and timeout
  enforcement.
- **Thread-Safe Session Registry:** A module-level singleton backed by
  ``threading.Lock`` ensures safe concurrent access from async and
  sync contexts.
- **Real-Time Push/Pull:** Questions are pushed to the client the instant
  they arrive; answers flow back immediately and signal waiting
  coroutines via ``asyncio.Event``.
- **Heartbeat Keep-Alive:** A configurable heartbeat (default 30 s)
  prevents idle-connection drops by proxies and load-balancers.
- **Observability:** Prometheus counters/histograms and OpenTelemetry
  spans provide production-grade visibility with graceful no-op
  fallbacks when the libraries are not installed.
- **Input Validation:** ``job_id`` is validated against a strict pattern
  (alphanumeric, hyphens, underscores; max 128 chars) to prevent
  injection and path-traversal attacks.

Industry Standards Applied
--------------------------
- **RFC 6455** (WebSocket Protocol) for full-duplex communication.
- **OpenAPI 3.x** via FastAPI auto-generated schema documentation.
- **Twelve-Factor App** — timeout configured through ``CLARIFIER_WS_TIMEOUT``
  environment variable.
- **Prometheus Exposition Format** for metrics instrumentation.
- **OpenTelemetry Specification** for distributed tracing.

Architecture
------------
::

    ┌──────────┐   POST /questions/{job_id}    ┌──────────────────────┐
    │ Backend  │ ─────────────────────────────► │ PushQuestionsRequest │
    └──────────┘                                └──────────┬───────────┘
                                                           │
                                                  push_questions()
                                                           │
                                                           ▼
                                                ┌──────────────────────┐
                                                │   asyncio.Queue      │
                                                │  (question_queue)    │
                                                └──────────┬───────────┘
                                                           │
                                              wait_for_questions()
                                                           │
                                                           ▼
                                                ┌──────────────────────┐
                                                │   WebSocket /ws/     │
                                                │  {"type":"questions"}│
                                                └──────────┬───────────┘
                                                           │
                                                   Client receives
                                                           │
                                                           ▼
    ┌──────────┐   {"type":"answers", …}       ┌──────────────────────┐
    │  Client  │ ─────────────────────────────► │   WebSocket /ws/     │
    └──────────┘                                └──────────┬───────────┘
                                                           │
                                                 submit_answers()
                                                           │
                                                           ▼
                                                ┌──────────────────────┐
                                                │   asyncio.Event      │
                                                │  (answer_event)      │
                                                └──────────┬───────────┘
                                                           │
                                               wait_for_answers()
                                                           │
                                                           ▼
                                                ┌──────────────────────┐
                                                │  Backend consumer    │
                                                └──────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus — conditional import with no-op stubs
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]


class _NoopMetric:
    """Lightweight no-op stub that silently accepts any Prometheus-style call."""

    def labels(self, *_args: Any, **_kwargs: Any) -> "_NoopMetric":
        return self

    def inc(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: D401
        pass

    def observe(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: D401
        pass


_NOOP = _NoopMetric()


def _safe_create_metric(
    factory: Any,
    name: str,
    description: str,
    labelnames: Optional[List[str]] = None,
) -> Any:
    """Create a Prometheus metric idempotently.

    If the metric is already registered the existing collector is returned
    instead of raising ``ValueError``.

    Args:
        factory: ``Counter``, ``Histogram``, etc.
        name: Metric name.
        description: Help string.
        labelnames: Optional label names.

    Returns:
        A Prometheus metric instance, or ``_NOOP`` when Prometheus is
        unavailable.
    """
    if not PROMETHEUS_AVAILABLE or factory is None:
        return _NOOP
    kwargs: Dict[str, Any] = {}
    if labelnames:
        kwargs["labelnames"] = labelnames
    try:
        return factory(name, description, **kwargs)
    except ValueError:
        from prometheus_client import REGISTRY as _REGISTRY

        return _REGISTRY._names_to_collectors.get(name, _NOOP)


_ws_connections_total: Any = _safe_create_metric(
    Counter,
    "clarifier_ws_connections_total",
    "Total WebSocket connections to the clarifier endpoint",
    labelnames=["status"],
)
_ws_questions_pushed_total: Any = _safe_create_metric(
    Counter,
    "clarifier_ws_questions_pushed_total",
    "Total question batches pushed into clarifier sessions",
)
_ws_answers_received_total: Any = _safe_create_metric(
    Counter,
    "clarifier_ws_answers_received_total",
    "Total answer payloads received from clarifier clients",
)
_ws_session_duration_seconds: Any = _safe_create_metric(
    Histogram,
    "clarifier_ws_session_duration_seconds",
    "Duration of clarifier WebSocket sessions in seconds",
)

# ---------------------------------------------------------------------------
# OpenTelemetry — conditional import with NullTracer fallback
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

    class NullContext:
        """No-op context manager returned when OpenTelemetry is unavailable."""

        def __enter__(self) -> "NullContext":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
            pass

    class NullTracer:
        """No-op tracer that mirrors the OpenTelemetry ``Tracer`` interface."""

        def start_as_current_span(
            self, name: str, *args: Any, **kwargs: Any
        ) -> NullContext:
            return NullContext()

    tracer = NullTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/clarifier", tags=["Clarifier"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

try:
    SESSION_TIMEOUT_SECONDS: int = int(
        os.environ.get("CLARIFIER_WS_TIMEOUT", "300")
    )
except (ValueError, TypeError):
    SESSION_TIMEOUT_SECONDS = 300  # fallback to 5 minutes
HEARTBEAT_INTERVAL_SECONDS: float = 30.0

#: Strict pattern for ``job_id`` — must contain at least one alphanumeric
#: character, may include hyphens/underscores, 1–128 chars total.
_JOB_ID_RE = re.compile(r"^(?=.*[a-zA-Z0-9])[a-zA-Z0-9_-]{1,128}$")


def _validate_job_id(job_id: str) -> None:
    """Validate ``job_id`` format and raise ``HTTPException(422)`` on failure.

    Args:
        job_id: The job identifier to validate.

    Raises:
        HTTPException: If *job_id* does not match the expected pattern.
    """
    if not _JOB_ID_RE.match(job_id):
        logger.warning("Invalid job_id format rejected: %s", job_id)
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid job_id format. Must be 1-128 characters, "
                "alphanumeric with hyphens/underscores only."
            ),
        )


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class ClarifierWebSocketSession:
    """Manages a single WebSocket clarifier session for a job.

    Stores pending questions, pushes them over the WebSocket when a client
    is connected, and collects answers sent back by the client.  Each
    session owns an ``asyncio.Queue`` for outbound questions and an
    ``asyncio.Event`` for signalling inbound answers.

    The session enforces a configurable timeout (``SESSION_TIMEOUT_SECONDS``)
    after which it is considered expired.  Heartbeat messages are sent at
    ``HEARTBEAT_INTERVAL_SECONDS`` to keep the connection alive.

    Attributes:
        job_id: Unique identifier for the code-generation job.
        created_at: Epoch timestamp when the session was created.

    Examples:
        >>> session = ClarifierWebSocketSession("job-abc-123")
        >>> session.connected
        False
        >>> session.is_expired  # just created
        False
    """

    def __init__(self, job_id: str) -> None:
        self.job_id: str = job_id
        self.created_at: float = time.time()
        self._question_queue: asyncio.Queue[List[str]] = asyncio.Queue()
        self._answer_event: asyncio.Event = asyncio.Event()
        self._pending_answers: Dict[str, str] = {}
        self._connected: bool = False
        self._lock: threading.Lock = threading.Lock()

    # -- public helpers -----------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > SESSION_TIMEOUT_SECONDS

    async def push_questions(self, questions: List[str]) -> None:
        """Enqueue a batch of questions to be sent to the client.

        Args:
            questions: List of question strings to push.
        """
        await self._question_queue.put(questions)
        logger.debug(
            "Queued %d question(s) for job %s", len(questions), self.job_id
        )

    async def wait_for_questions(
        self, timeout: float = HEARTBEAT_INTERVAL_SECONDS
    ) -> Optional[List[str]]:
        """Wait for the next batch of questions (returns *None* on timeout).

        Args:
            timeout: Maximum seconds to wait before returning ``None``.

        Returns:
            A list of question strings, or ``None`` if the timeout elapsed.
        """
        try:
            return await asyncio.wait_for(
                self._question_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    def submit_answers(self, answers: Dict[str, str]) -> None:
        """Store answers from the client and signal waiters.

        Args:
            answers: Mapping of question identifiers to answer strings.
        """
        with self._lock:
            self._pending_answers.update(answers)
        self._answer_event.set()

    async def wait_for_answers(
        self, timeout: float = SESSION_TIMEOUT_SECONDS
    ) -> Dict[str, str]:
        """Block until the client submits answers or *timeout* expires.

        Args:
            timeout: Maximum seconds to wait for answers.

        Returns:
            A dict of collected answers (may be empty on timeout).
        """
        try:
            await asyncio.wait_for(self._answer_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Answer timeout for job %s", self.job_id)
        with self._lock:
            answers = dict(self._pending_answers)
            self._pending_answers.clear()
        self._answer_event.clear()
        return answers

    async def send_questions_and_wait(
        self, questions: List[str], timeout: float = SESSION_TIMEOUT_SECONDS
    ) -> List[str]:
        """Push *questions* into the session queue and wait for answers.

        This coroutine combines :meth:`push_questions` and
        :meth:`wait_for_answers` into a single call suitable for use by
        ``WebPrompt`` when a live WebSocket client is connected.

        Args:
            questions: List of question strings to send to the client.
            timeout: Maximum seconds to wait for the client's answers.

        Returns:
            A list of answer strings in the same order as *questions*.
            Returns an empty list if the session times out or no answers
            are received.
        """
        await self.push_questions(questions)
        answers_dict = await self.wait_for_answers(timeout=timeout)
        # Convert the dict (keyed by question index or text) to an ordered list
        if not answers_dict:
            return []
        # If answers are keyed by integer index, return in order; otherwise
        # preserve insertion order (Python 3.7+).
        ordered: List[str] = []
        for i, q in enumerate(questions):
            key = str(i)
            if key in answers_dict:
                ordered.append(answers_dict[key])
            elif q in answers_dict:
                ordered.append(answers_dict[q])
            else:
                # This question has no matching answer key; stop ordered lookup
                logger.debug(
                    "No answer key found for question index %d in job %s; "
                    "falling back to dict values",
                    i,
                    self.job_id,
                )
                break
        if len(ordered) != len(questions):
            # Fall back to raw dict values (best-effort positional ordering)
            ordered = list(answers_dict.values())
        return ordered

    def status(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status snapshot."""
        return {
            "job_id": self.job_id,
            "connected": self.connected,
            "expired": self.is_expired,
            "pending_questions": self._question_queue.qsize(),
            "created_at": self.created_at,
        }

    # -- dunder helpers -----------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ClarifierWebSocketSession(job_id={self.job_id!r}, "
            f"connected={self.connected!r})"
        )

    def __str__(self) -> str:
        return (
            f"<ClarifierWebSocketSession job_id={self.job_id} "
            f"connected={self.connected} expired={self.is_expired}>"
        )


# ---------------------------------------------------------------------------
# Session registry (module-level singleton, thread-safe)
# ---------------------------------------------------------------------------


class ClarifierSessionRegistry:
    """Thread-safe registry that maps *job_id* → ``ClarifierWebSocketSession``.

    The registry is designed as a module-level singleton.  All mutating
    operations are serialised through a ``threading.Lock`` to guarantee
    correctness when accessed from multiple async tasks or threads.

    Examples:
        >>> reg = ClarifierSessionRegistry()
        >>> session = reg.create_session("job-1")
        >>> reg.get_session("job-1") is session
        True
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, ClarifierWebSocketSession] = {}
        self._lock: threading.Lock = threading.Lock()

    def create_session(self, job_id: str) -> ClarifierWebSocketSession:
        """Create (or replace) a session for *job_id*.

        Args:
            job_id: The unique job identifier.

        Returns:
            The newly created ``ClarifierWebSocketSession``.
        """
        session = ClarifierWebSocketSession(job_id)
        with self._lock:
            self._sessions[job_id] = session
        logger.info("Created clarifier session for job %s", job_id)
        return session

    def get_session(self, job_id: str) -> Optional[ClarifierWebSocketSession]:
        """Return the session for *job_id*, or *None*.

        Args:
            job_id: The unique job identifier.

        Returns:
            The session instance or ``None`` if not found.
        """
        with self._lock:
            return self._sessions.get(job_id)

    def remove_session(self, job_id: str) -> None:
        """Remove the session for *job_id* (no-op if absent).

        Args:
            job_id: The unique job identifier.
        """
        with self._lock:
            removed = self._sessions.pop(job_id, None)
        if removed is not None:
            logger.info("Removed clarifier session for job %s", job_id)

    # -- dunder helpers -----------------------------------------------------

    def __repr__(self) -> str:
        with self._lock:
            count = len(self._sessions)
        return f"ClarifierSessionRegistry(active_sessions={count})"

    def __str__(self) -> str:
        with self._lock:
            count = len(self._sessions)
        return f"<ClarifierSessionRegistry active_sessions={count}>"


# Module-level singleton
registry = ClarifierSessionRegistry()


def get_clarifier_registry() -> ClarifierSessionRegistry:
    """Return the module-level ``ClarifierSessionRegistry`` singleton.

    Provides a stable import target for consumers that need to look up
    live WebSocket sessions without importing the ``registry`` name
    directly (which would bind at import time).

    Returns:
        The module-level ``ClarifierSessionRegistry`` instance.
    """
    return registry


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PushQuestionsRequest(BaseModel):
    """Body for the question-push HTTP endpoint.

    Attributes:
        questions: Non-empty list of clarification question strings.

    Examples:
        >>> req = PushQuestionsRequest(questions=["What framework?"])
        >>> req.questions
        ['What framework?']
    """

    questions: List[str] = Field(
        ...,
        min_length=1,
        description="List of clarification questions to push to the client.",
    )


class SessionStatusResponse(BaseModel):
    """Response schema for the session-status endpoint.

    Attributes:
        job_id: The unique job identifier.
        connected: Whether a WebSocket client is currently connected.
        expired: Whether the session has exceeded its timeout.
        pending_questions: Number of questions waiting in the queue.
        created_at: Epoch timestamp when the session was created.

    Examples:
        >>> resp = SessionStatusResponse(
        ...     job_id="abc", connected=True, expired=False,
        ...     pending_questions=0, created_at=1700000000.0,
        ... )
        >>> resp.job_id
        'abc'
    """

    job_id: str = Field(..., description="Unique job identifier.")
    connected: bool = Field(
        ..., description="Whether a WebSocket client is connected."
    )
    expired: bool = Field(
        ..., description="Whether the session has timed out."
    )
    pending_questions: int = Field(
        ..., ge=0, description="Questions waiting in the queue."
    )
    created_at: float = Field(
        ..., description="Epoch timestamp of session creation."
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/{job_id}")
async def clarifier_ws(websocket: WebSocket, job_id: str) -> None:
    """WebSocket endpoint for real-time clarifier Q&A.

    Opens a persistent WebSocket connection for a given ``job_id``.
    Questions are pushed to the client as they arrive; answers flow back
    immediately.  The connection is kept alive with periodic heartbeat
    frames and is closed automatically on timeout or error.

    Protocol
    --------
    * **Server → Client**: ``{"type": "questions", "questions": [...], "job_id": "..."}``
    * **Client → Server**: ``{"type": "answers", "answers": {"q1": "a1", ...}}``
    * **Server → Client**: ``{"type": "ack", "received": N}``
    * **Server → Client**: ``{"type": "heartbeat"}`` (every ~30 s)

    Args:
        websocket: The FastAPI ``WebSocket`` connection.
        job_id: The unique job identifier (validated).
    """
    _validate_job_id(job_id)

    with tracer.start_as_current_span("clarifier_ws.connect") as span:
        span.set_attribute("job_id", job_id)

        session = registry.get_session(job_id)
        if session is None:
            session = registry.create_session(job_id)

        await websocket.accept()
        session.connected = True
        _ws_connections_total.labels(status="connected").inc()
        session_start = time.time()
        logger.info(
            "Clarifier WebSocket connected for job %s",
            job_id,
            extra={"job_id": job_id, "status": "connected"},
        )

        try:
            while True:
                # Wait for questions or heartbeat timeout
                questions = await session.wait_for_questions(
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )

                if session.is_expired:
                    logger.warning("Session expired for job %s", job_id)
                    await websocket.send_json(
                        {"type": "error", "detail": "session_expired"}
                    )
                    break

                if questions is not None:
                    await websocket.send_json(
                        {
                            "type": "questions",
                            "questions": questions,
                            "job_id": job_id,
                        }
                    )
                    logger.debug(
                        "Sent %d question(s) to client for job %s",
                        len(questions),
                        job_id,
                    )

                    # Wait for the client answer
                    try:
                        raw = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=SESSION_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Client answer timeout for job %s", job_id
                        )
                        await websocket.send_json(
                            {"type": "error", "detail": "answer_timeout"}
                        )
                        break

                    msg_type = raw.get("type")
                    if msg_type == "answers":
                        answers: Dict[str, str] = raw.get("answers", {})
                        session.submit_answers(answers)
                        _ws_answers_received_total.inc()
                        await websocket.send_json(
                            {"type": "ack", "received": len(answers)}
                        )
                        logger.info(
                            "Received %d answer(s) for job %s",
                            len(answers),
                            job_id,
                            extra={
                                "job_id": job_id,
                                "answer_count": len(answers),
                            },
                        )
                    else:
                        logger.warning(
                            "Unexpected message type '%s' from client "
                            "for job %s",
                            msg_type,
                            job_id,
                        )
                else:
                    # No questions within the heartbeat window – send keepalive
                    await websocket.send_json({"type": "heartbeat"})

        except WebSocketDisconnect:
            logger.info(
                "Clarifier WebSocket disconnected for job %s", job_id
            )
        except Exception as exc:
            logger.error(
                "Clarifier WebSocket error for job %s: %s",
                job_id,
                exc,
                exc_info=True,
            )
        finally:
            session.connected = False
            duration = time.time() - session_start
            _ws_session_duration_seconds.observe(duration)
            _ws_connections_total.labels(status="closed").inc()
            logger.info(
                "Clarifier WebSocket closed for job %s (%.1fs)",
                job_id,
                duration,
                extra={"job_id": job_id, "status": "closed"},
            )


# ---------------------------------------------------------------------------
# HTTP helper endpoints
# ---------------------------------------------------------------------------


@router.post("/questions/{job_id}")
async def push_questions(
    job_id: str, body: PushQuestionsRequest
) -> Dict[str, Any]:
    """Push clarification questions into a session (used by the backend).

    Creates a session automatically if one does not exist yet.

    Args:
        job_id: The unique job identifier (validated).
        body: Request body containing the list of questions.

    Returns:
        A dict confirming the number of questions queued.
    """
    _validate_job_id(job_id)

    with tracer.start_as_current_span("clarifier_ws.push_questions") as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("question_count", len(body.questions))

        session = registry.get_session(job_id)
        if session is None:
            session = registry.create_session(job_id)

        await session.push_questions(body.questions)
        _ws_questions_pushed_total.inc()
        logger.info(
            "Pushed %d question(s) to session for job %s",
            len(body.questions),
            job_id,
            extra={"job_id": job_id, "question_count": len(body.questions)},
        )
        return {
            "status": "queued",
            "job_id": job_id,
            "question_count": len(body.questions),
        }


@router.get("/status/{job_id}", response_model=SessionStatusResponse)
async def session_status(job_id: str) -> SessionStatusResponse:
    """Return the current status of a clarifier session.

    Args:
        job_id: The unique job identifier (validated).

    Returns:
        A ``SessionStatusResponse`` with the current session state.

    Raises:
        HTTPException: 404 if no session exists for the given *job_id*.
    """
    _validate_job_id(job_id)

    with tracer.start_as_current_span("clarifier_ws.session_status") as span:
        span.set_attribute("job_id", job_id)

        session = registry.get_session(job_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"No clarifier session for job {job_id}",
            )
        return SessionStatusResponse(**session.status())
