# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
jobs_ws.py — API v2 WebSocket Real-Time Job Status Endpoint

Provides ``/api/v2/jobs/{job_id}/ws`` — a WebSocket endpoint that streams
per-job lifecycle events from the OmniCore message bus to connected clients,
eliminating the need to poll ``GET /api/jobs/{job_id}``.

Architecture
------------
::

    ┌───────────────┐   WS /api/v2/jobs/{job_id}/ws   ┌─────────────────────┐
    │   JS Client   │ ──────────────────────────────► │  JobStatusWebSocket │
    │   Python SDK  │                                  │   (this module)     │
    └───────────────┘                                  └──────────┬──────────┘
                                                                  │
                                                 subscribe(job.{id}.*)
                                                                  │
                                                                  ▼
                                                       ┌──────────────────────┐
                                                       │  OmniCore Message Bus │
                                                       │  (ShardedMessageBus) │
                                                       └──────────┬───────────┘
                                                                  │
                                                   _message_handler(payload)
                                                                  │
                                                                  ▼
                                                       ┌──────────────────────┐
                                                       │  asyncio.Queue       │
                                                       │  (per-connection)    │
                                                       └──────────┬───────────┘
                                                                  │
                                                        build + send JSON
                                                                  │
                                                                  ▼
                                                       ┌──────────────────────┐
                                                       │   WebSocket client   │
                                                       │ {"event":"...", ...} │
                                                       └──────────────────────┘

Message Schema
--------------
All frames are JSON objects with an ``event`` discriminator:

``connected``
    Sent immediately after the handshake is accepted.

``stage_progress``
    Sent when a pipeline stage reports progress.  Contains ``stage`` (str)
    and ``percent`` (int 0-100).

``job_complete``
    Terminal event.  Contains ``result`` (dict).  Server closes after sending.

``job_failed``
    Terminal event.  Contains ``error`` (str).  Server closes after sending.

``heartbeat``
    Sent every ``JOBS_WS_TIMEOUT`` seconds when no other event has arrived.

Key Features
------------
- **Per-Job Filtering:** only events belonging to ``job_id`` are forwarded.
- **Rate Limiting:** per-IP connection limits prevent resource exhaustion.
- **Input Validation:** ``job_id`` validated against strict alphanumeric
  pattern (max 128 chars) — injection / path-traversal protection.
- **Graceful Degradation:** operates cleanly when the OmniCore message bus
  is unavailable (heartbeat-only mode).
- **Observability:** Prometheus counters/histograms + OpenTelemetry spans.
- **Heartbeat Keep-Alive:** configurable via ``JOBS_WS_TIMEOUT`` env var.

Industry Standards Applied
--------------------------
- **RFC 6455** (WebSocket Protocol).
- **OpenAPI 3.x** — auto-documented via FastAPI.
- **Twelve-Factor App** — all tunables via environment variables.
- **Prometheus Exposition Format** — metric instrumentation.
- **OpenTelemetry Specification** — distributed tracing.

Environment Variables
---------------------
JOBS_WS_TIMEOUT
    Heartbeat interval in seconds (default ``30``).  Also doubles as the
    idle-connection timeout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus — conditional import with no-op stubs (same pattern as clarifier_ws)
# ---------------------------------------------------------------------------

from shared.noop_metrics import NOOP as _NOOP, safe_metric as _safe_metric

try:
    from prometheus_client import Counter, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]


_jobs_ws_connections_total: Any = _safe_metric(
    Counter,
    "jobs_ws_connections_total",
    "Total WebSocket connections to the job-status endpoint",
    labelnames=["status"],
)
_jobs_ws_events_forwarded_total: Any = _safe_metric(
    Counter,
    "jobs_ws_events_forwarded_total",
    "Total job events forwarded to WebSocket clients",
    labelnames=["event_type"],
)
_jobs_ws_session_duration_seconds: Any = _safe_metric(
    Histogram,
    "jobs_ws_session_duration_seconds",
    "Duration of job-status WebSocket sessions in seconds",
    labelnames=["terminal_event"],
)
_jobs_ws_active_connections: Any = _safe_metric(
    Counter,
    "jobs_ws_active_connections_current",
    "Current count of active job-status WebSocket connections",
)

# ---------------------------------------------------------------------------
# OpenTelemetry — conditional import with NullTracer fallback
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer(__name__)
    _TRACING_AVAILABLE = True
except ImportError:  # pragma: no cover
    from shared.noop_tracing import NullTracer as _NullTracer  # noqa: E402

    _TRACING_AVAILABLE = False
    _tracer = _NullTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/v2/jobs", tags=["Jobs v2 WebSocket"])

# ---------------------------------------------------------------------------
# Configuration — tunable via environment
# ---------------------------------------------------------------------------

try:
    HEARTBEAT_INTERVAL: float = float(os.environ.get("JOBS_WS_TIMEOUT", "30"))
except (ValueError, TypeError):
    HEARTBEAT_INTERVAL = 30.0

# Rate-limiting constants — mirrors events.py
MAX_CONNECTIONS_PER_IP: int = 5
MAX_TOTAL_CONNECTIONS: int = 500
RATE_LIMIT_WINDOW: int = 60       # seconds
MAX_CONNECTIONS_PER_WINDOW: int = 10

# Queue capacity per connection — drop events silently when full
_EVENT_QUEUE_MAXSIZE: int = 200

# Topics that carry job events but are not scoped to a single job.
# These are filtered server-side by ``job_id`` in the payload.
_GENERIC_TOPICS: frozenset = frozenset({
    "generator.stage_update",
    "job.completed",
    "job.failed",
    "job.status",
})

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

#: job_id must be alphanumeric + hyphens/underscores, 1–128 chars.
_JOB_ID_RE = re.compile(r"^(?=.*[a-zA-Z0-9])[a-zA-Z0-9_-]{1,128}$")


def _validate_job_id(job_id: str) -> None:
    """Validate *job_id* format; raise ``HTTPException(422)`` on failure.

    Args:
        job_id: The job identifier extracted from the URL path.

    Raises:
        HTTPException: 422 Unprocessable Entity if *job_id* is invalid.
    """
    if not _JOB_ID_RE.match(job_id):
        logger.warning("jobs_ws: invalid job_id rejected. job_id=%r", job_id)
        raise HTTPException(
            status_code=422,
            detail=(
                "job_id must be 1–128 alphanumeric characters, hyphens, or "
                "underscores and must contain at least one alphanumeric character."
            ),
        )


# ---------------------------------------------------------------------------
# Connection-state tracking (module-level, thread-safe via threading.Lock)
# ---------------------------------------------------------------------------

_state_lock: threading.Lock = threading.Lock()
_active_connections_by_ip: Dict[str, int] = defaultdict(int)
_connection_attempts: Dict[str, list] = defaultdict(list)
_all_active_connections: List[WebSocket] = []


def _check_rate_limit(client_ip: str) -> tuple:
    """Enforce per-IP and global connection rate limits.

    Args:
        client_ip: IP address of the connecting client.

    Returns:
        ``(allowed: bool, reason: str)`` — *allowed* is ``True`` when the
        connection should proceed.
    """
    now = time.monotonic()
    with _state_lock:
        # Evict stale attempt timestamps
        _connection_attempts[client_ip] = [
            t for t in _connection_attempts[client_ip]
            if now - t < RATE_LIMIT_WINDOW
        ]
        if _active_connections_by_ip[client_ip] >= MAX_CONNECTIONS_PER_IP:
            return (
                False,
                f"Too many active connections from {client_ip} "
                f"(max {MAX_CONNECTIONS_PER_IP})",
            )
        if len(_all_active_connections) >= MAX_TOTAL_CONNECTIONS:
            return False, f"Server at capacity ({MAX_TOTAL_CONNECTIONS} connections)"
        if len(_connection_attempts[client_ip]) >= MAX_CONNECTIONS_PER_WINDOW:
            return (
                False,
                f"Rate limit: max {MAX_CONNECTIONS_PER_WINDOW} new connections "
                f"per {RATE_LIMIT_WINDOW}s from {client_ip}",
            )
        return True, "ok"


def _register_connection(websocket: WebSocket, client_ip: str) -> None:
    """Thread-safely register a new active connection."""
    now = time.monotonic()
    with _state_lock:
        _active_connections_by_ip[client_ip] += 1
        _connection_attempts[client_ip].append(now)
        _all_active_connections.append(websocket)


def _deregister_connection(websocket: WebSocket, client_ip: str) -> None:
    """Thread-safely remove a connection from tracking structures."""
    with _state_lock:
        if websocket in _all_active_connections:
            _all_active_connections.remove(websocket)
        _active_connections_by_ip[client_ip] = max(
            0, _active_connections_by_ip.get(client_ip, 1) - 1
        )


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _build_message(payload: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    """Translate a raw message-bus payload into the public WebSocket message format.

    Routing logic:

    * Topics containing ``"complete"`` or payload ``status == "complete"``
      → ``job_complete`` (terminal).
    * Topics containing ``"fail"`` or payload ``status == "failed"``
      → ``job_failed`` (terminal).
    * All other payloads → ``stage_progress``.

    Args:
        payload: Raw dict received from the OmniCore message bus.
        job_id: Job identifier injected when absent from *payload*.

    Returns:
        A dict conforming to the documented message schema.
    """
    timestamp: str = payload.get("timestamp") or _now_iso()
    topic: str = payload.get("topic", "")
    resolved_job_id: str = payload.get("job_id") or job_id

    if "complete" in topic or payload.get("status") == "complete":
        return {
            "event": "job_complete",
            "job_id": resolved_job_id,
            "result": payload,
            "timestamp": timestamp,
        }

    if "fail" in topic or payload.get("status") == "failed":
        return {
            "event": "job_failed",
            "job_id": resolved_job_id,
            "error": payload.get("error") or payload.get("message", "Job failed"),
            "timestamp": timestamp,
        }

    return {
        "event": "stage_progress",
        "job_id": resolved_job_id,
        "stage": payload.get("stage") or payload.get("message", ""),
        "percent": int(payload.get("percent", 0)),
        "timestamp": timestamp,
        "data": payload,
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/{job_id}/ws")
async def job_status_websocket(websocket: WebSocket, job_id: str) -> None:
    """Stream real-time status events for a specific generation job.

    **URL:** ``/api/v2/jobs/{job_id}/ws``

    The server:

    1. Validates ``job_id`` format (422 on failure, before accepting).
    2. Enforces per-IP and global rate limits (1008 on failure).
    3. Accepts the WebSocket and sends a ``connected`` acknowledgement.
    4. Subscribes to ``job.{job_id}.*`` topics on the OmniCore message bus.
    5. Streams ``stage_progress`` events as pipeline stages complete.
    6. Sends ``job_complete`` or ``job_failed`` (terminal) and then closes.
    7. Sends ``heartbeat`` every ``JOBS_WS_TIMEOUT`` seconds when idle.

    Args:
        websocket: The incoming WebSocket connection (injected by FastAPI).
        job_id: Target job identifier from the URL path.
    """
    # --- Input validation (before accept to save handshake overhead) ---
    try:
        _validate_job_id(job_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=exc.detail)
        return

    client_ip: str = websocket.client.host if websocket.client else "unknown"
    connection_id: str = f"{client_ip}|{job_id}|{id(websocket)}"
    t_start: float = time.monotonic()

    # --- Rate-limit check (before accept) ---
    allowed, reject_reason = _check_rate_limit(client_ip)
    if not allowed:
        logger.warning(
            "jobs_ws: connection rejected. connection_id=%s reason=%s",
            connection_id,
            reject_reason,
        )
        _jobs_ws_connections_total.labels(status="rejected").inc()
        try:
            await websocket.close(code=1008, reason=reject_reason)
        except Exception:
            pass
        return

    # --- Accept ---
    try:
        await websocket.accept()
    except Exception as exc:
        logger.error(
            "jobs_ws: accept failed. connection_id=%s error=%s",
            connection_id,
            exc,
        )
        _jobs_ws_connections_total.labels(status="accept_failed").inc()
        return

    _register_connection(websocket, client_ip)
    _jobs_ws_connections_total.labels(status="accepted").inc()
    terminal_event: str = "disconnected"

    logger.info(
        "jobs_ws: connected. connection_id=%s job_id=%s total_active=%d",
        connection_id,
        job_id,
        len(_all_active_connections),
    )

    with _tracer.start_as_current_span(
        "jobs_ws.session",
        attributes={"ws.job_id": job_id, "ws.client_ip": client_ip},
    ) as span:
        # Send connected acknowledgement
        try:
            await websocket.send_json({
                "event": "connected",
                "job_id": job_id,
                "connection_id": connection_id,
                "timestamp": _now_iso(),
            })
        except Exception as exc:
            logger.error(
                "jobs_ws: failed to send connected ack. connection_id=%s error=%s",
                connection_id,
                exc,
            )
            _deregister_connection(websocket, client_ip)
            return

        # --- Message-bus subscription ---
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)
        subscribed_topics: List[str] = []
        handler_live = threading.Event()
        handler_live.set()
        omnicore_service = None
        bus_ready: bool = False
        message_handler = None
        event_loop = asyncio.get_event_loop()

        try:
            from server.services.omnicore_service import get_omnicore_service

            omnicore_service = get_omnicore_service()
            _bus = getattr(omnicore_service, "_message_bus", None)
            bus_ready = bool(
                _bus is not None
                and getattr(_bus, "_dispatchers_started", False)
                and omnicore_service._omnicore_components_available.get(
                    "message_bus", False
                )
            )
        except Exception as exc:
            logger.debug(
                "jobs_ws: OmniCore service unavailable, heartbeat-only mode. "
                "connection_id=%s error=%s",
                connection_id,
                exc,
            )

        if bus_ready and _bus is not None:
            job_topics: List[str] = [
                f"job.{job_id}.stage_progress",
                f"job.{job_id}.complete",
                f"job.{job_id}.failed",
                *_GENERIC_TOPICS,
            ]

            def message_handler(message: Any) -> None:  # noqa: E731
                """Deliver a bus message into the per-connection asyncio queue."""
                if not handler_live.is_set():
                    return
                try:
                    if isinstance(message, dict):
                        payload: Dict[str, Any] = message
                    else:
                        raw = getattr(message, "payload", "{}")
                        try:
                            payload = json.loads(raw) if isinstance(raw, str) else raw
                        except (json.JSONDecodeError, TypeError):
                            payload = {"raw": str(raw)}

                    # Filter generic topics to only forward events for this job
                    if isinstance(payload, dict):
                        topic = getattr(message, "topic", "") or payload.get("topic", "")
                        if topic in _GENERIC_TOPICS:
                            msg_job_id = payload.get("job_id") or payload.get("id")
                            if msg_job_id != job_id:
                                return

                    if not event_queue.full():
                        event_loop.call_soon_threadsafe(event_queue.put_nowait, payload)
                    else:
                        logger.warning(
                            "jobs_ws: event queue full, dropping event. "
                            "connection_id=%s",
                            connection_id,
                        )
                except Exception as exc:  # pragma: no cover
                    logger.debug(
                        "jobs_ws: message_handler error. connection_id=%s error=%s",
                        connection_id,
                        exc,
                    )

            for topic in job_topics:
                try:
                    _bus.subscribe(topic, message_handler)
                    subscribed_topics.append(topic)
                except Exception as exc:
                    logger.warning(
                        "jobs_ws: subscribe failed. topic=%s error=%s", topic, exc
                    )

        # --- Main streaming loop ---
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(
                        event_queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                    msg = _build_message(payload, job_id)
                    await websocket.send_json(msg)
                    _jobs_ws_events_forwarded_total.labels(
                        event_type=msg["event"]
                    ).inc()
                    span.set_attribute("ws.last_event", msg["event"])

                    if msg["event"] in {"job_complete", "job_failed"}:
                        terminal_event = msg["event"]
                        logger.info(
                            "jobs_ws: terminal event received. "
                            "connection_id=%s event=%s",
                            connection_id,
                            terminal_event,
                        )
                        break

                except asyncio.TimeoutError:
                    # Heartbeat — keep connection alive through proxies/LBs
                    try:
                        await websocket.send_json({
                            "event": "heartbeat",
                            "job_id": job_id,
                            "timestamp": _now_iso(),
                        })
                        _jobs_ws_events_forwarded_total.labels(event_type="heartbeat").inc()
                    except Exception:
                        break

        except WebSocketDisconnect:
            logger.info(
                "jobs_ws: client disconnected. connection_id=%s", connection_id
            )
        except Exception as exc:
            logger.error(
                "jobs_ws: unhandled error in stream loop. "
                "connection_id=%s error=%s",
                connection_id,
                exc,
                exc_info=True,
            )
            span.record_exception(exc)
        finally:
            # Clean up message-bus subscriptions
            handler_live.clear()
            if bus_ready and _bus is not None and message_handler is not None:
                for topic in subscribed_topics:
                    try:
                        _bus.unsubscribe(topic, message_handler)
                    except Exception:
                        pass

            _deregister_connection(websocket, client_ip)
            duration = time.monotonic() - t_start
            _jobs_ws_session_duration_seconds.labels(
                terminal_event=terminal_event
            ).observe(duration)
            span.set_attribute("ws.duration_seconds", round(duration, 3))
            span.set_attribute("ws.terminal_event", terminal_event)

            logger.info(
                "jobs_ws: session closed. connection_id=%s "
                "terminal_event=%s duration=%.2fs active=%d",
                connection_id,
                terminal_event,
                duration,
                len(_all_active_connections),
            )
