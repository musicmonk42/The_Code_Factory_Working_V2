# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
WebSocket Real-Time Clarifier Q&A endpoint.

Provides a WebSocket-based channel for interactive clarification
questions during code generation jobs, replacing the blocking
form-based flow with real-time bidirectional communication.

Features:
- Per-job WebSocket sessions with automatic lifecycle management
- Thread-safe session registry (singleton pattern)
- Push questions to the client and receive answers in real-time
- Timeout handling and graceful disconnection
- HTTP helpers for backend question injection and status queries
"""

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/clarifier", tags=["Clarifier"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TIMEOUT_SECONDS: int = int(
    os.environ.get("CLARIFIER_WS_TIMEOUT", "300")
)  # 5 minutes – matches WebPrompt timeout
HEARTBEAT_INTERVAL_SECONDS: float = 30.0


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class ClarifierWebSocketSession:
    """Manages a single WebSocket clarifier session for a job.

    Stores pending questions, pushes them over the WebSocket when a client
    is connected, and collects answers sent back by the client.
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
        """Enqueue a batch of questions to be sent to the client."""
        await self._question_queue.put(questions)
        logger.debug(
            "Queued %d question(s) for job %s", len(questions), self.job_id
        )

    async def wait_for_questions(self, timeout: float = HEARTBEAT_INTERVAL_SECONDS) -> Optional[List[str]]:
        """Wait for the next batch of questions (returns *None* on timeout)."""
        try:
            return await asyncio.wait_for(self._question_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def submit_answers(self, answers: Dict[str, str]) -> None:
        """Store answers from the client and signal waiters."""
        with self._lock:
            self._pending_answers.update(answers)
        self._answer_event.set()

    async def wait_for_answers(self, timeout: float = SESSION_TIMEOUT_SECONDS) -> Dict[str, str]:
        """Block until the client submits answers or *timeout* expires."""
        try:
            await asyncio.wait_for(self._answer_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Answer timeout for job %s", self.job_id)
        with self._lock:
            answers = dict(self._pending_answers)
            self._pending_answers.clear()
        self._answer_event.clear()
        return answers

    def status(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status snapshot."""
        return {
            "job_id": self.job_id,
            "connected": self.connected,
            "expired": self.is_expired,
            "pending_questions": self._question_queue.qsize(),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Session registry (module-level singleton, thread-safe)
# ---------------------------------------------------------------------------


class ClarifierSessionRegistry:
    """Thread-safe registry that maps *job_id* → `ClarifierWebSocketSession`."""

    def __init__(self) -> None:
        self._sessions: Dict[str, ClarifierWebSocketSession] = {}
        self._lock: threading.Lock = threading.Lock()

    def create_session(self, job_id: str) -> ClarifierWebSocketSession:
        """Create (or replace) a session for *job_id*."""
        session = ClarifierWebSocketSession(job_id)
        with self._lock:
            self._sessions[job_id] = session
        logger.info("Created clarifier session for job %s", job_id)
        return session

    def get_session(self, job_id: str) -> Optional[ClarifierWebSocketSession]:
        """Return the session for *job_id*, or *None*."""
        with self._lock:
            return self._sessions.get(job_id)

    def remove_session(self, job_id: str) -> None:
        """Remove the session for *job_id* (no-op if absent)."""
        with self._lock:
            removed = self._sessions.pop(job_id, None)
        if removed is not None:
            logger.info("Removed clarifier session for job %s", job_id)


# Module-level singleton
registry = ClarifierSessionRegistry()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PushQuestionsRequest(BaseModel):
    """Body for the question-push HTTP endpoint."""
    questions: List[str]


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/{job_id}")
async def clarifier_ws(websocket: WebSocket, job_id: str) -> None:
    """WebSocket endpoint for real-time clarifier Q&A.

    Protocol
    --------
    * **Server → Client**: ``{"type": "questions", "questions": [...], "job_id": "..."}``
    * **Client → Server**: ``{"type": "answers", "answers": {"q1": "a1", ...}}``
    * **Server → Client**: ``{"type": "ack", "received": N}``
    * **Server → Client**: ``{"type": "heartbeat"}`` (every ~30 s)
    """
    session = registry.get_session(job_id)
    if session is None:
        session = registry.create_session(job_id)

    await websocket.accept()
    session.connected = True
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
                await websocket.send_json({"type": "error", "detail": "session_expired"})
                break

            if questions is not None:
                await websocket.send_json(
                    {"type": "questions", "questions": questions, "job_id": job_id}
                )
                logger.debug("Sent %d question(s) to client for job %s", len(questions), job_id)

                # Wait for the client answer
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_json(), timeout=SESSION_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.warning("Client answer timeout for job %s", job_id)
                    await websocket.send_json({"type": "error", "detail": "answer_timeout"})
                    break

                msg_type = raw.get("type")
                if msg_type == "answers":
                    answers: Dict[str, str] = raw.get("answers", {})
                    session.submit_answers(answers)
                    await websocket.send_json({"type": "ack", "received": len(answers)})
                    logger.info(
                        "Received %d answer(s) for job %s",
                        len(answers),
                        job_id,
                        extra={"job_id": job_id, "answer_count": len(answers)},
                    )
                else:
                    logger.warning("Unexpected message type '%s' from client for job %s", msg_type, job_id)
            else:
                # No questions within the heartbeat window – send keepalive
                await websocket.send_json({"type": "heartbeat"})

    except WebSocketDisconnect:
        logger.info("Clarifier WebSocket disconnected for job %s", job_id)
    except Exception as exc:
        logger.error(
            "Clarifier WebSocket error for job %s: %s",
            job_id,
            exc,
            exc_info=True,
        )
    finally:
        session.connected = False
        logger.info(
            "Clarifier WebSocket closed for job %s",
            job_id,
            extra={"job_id": job_id, "status": "closed"},
        )


# ---------------------------------------------------------------------------
# HTTP helper endpoints
# ---------------------------------------------------------------------------


@router.post("/questions/{job_id}")
async def push_questions(job_id: str, body: PushQuestionsRequest) -> Dict[str, Any]:
    """Push clarification questions into a session (used by the backend).

    Creates a session automatically if one does not exist yet.
    """
    session = registry.get_session(job_id)
    if session is None:
        session = registry.create_session(job_id)

    await session.push_questions(body.questions)
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


@router.get("/status/{job_id}")
async def session_status(job_id: str) -> Dict[str, Any]:
    """Return the current status of a clarifier session."""
    session = registry.get_session(job_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No clarifier session for job {job_id}")
    return session.status()
