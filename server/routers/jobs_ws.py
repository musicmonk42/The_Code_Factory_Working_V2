# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
API v2 WebSocket endpoint for real-time job status updates.

Provides ``/api/v2/jobs/{job_id}/ws`` — a WebSocket endpoint that streams
per-job lifecycle events from OmniCore's message bus to the client.

Message schema
--------------
All messages are JSON objects.  The ``event`` field identifies the type:

``stage_progress``
    Sent when a pipeline stage completes or updates.

    .. code-block:: json

        {"event": "stage_progress", "stage": "CODEGEN", "percent": 40,
         "timestamp": "2025-01-01T00:00:00Z"}

``job_complete``
    Sent when the job finishes successfully.

    .. code-block:: json

        {"event": "job_complete", "result": {...}, "timestamp": "..."}

``job_failed``
    Sent when the job terminates with an error.

    .. code-block:: json

        {"event": "job_failed", "error": "...", "timestamp": "..."}

``heartbeat``
    Sent every 30 seconds when no other event has been emitted, to keep
    the connection alive.

    .. code-block:: json

        {"event": "heartbeat", "timestamp": "..."}

See ``docs/WEBSOCKET_JOB_STATUS.md`` for full documentation and a
JavaScript usage example.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.services.omnicore_service import get_omnicore_service as _get_omnicore_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/jobs", tags=["Jobs v2 WebSocket"])

# ---------------------------------------------------------------------------
# Rate-limiting constants (same pattern as server/routers/events.py)
# ---------------------------------------------------------------------------

MAX_CONNECTIONS_PER_IP: int = 5
MAX_TOTAL_CONNECTIONS: int = 500
RATE_LIMIT_WINDOW: int = 60  # seconds
MAX_CONNECTIONS_PER_WINDOW: int = 10
HEARTBEAT_INTERVAL: float = 30.0  # seconds

# ---------------------------------------------------------------------------
# Connection tracking
# ---------------------------------------------------------------------------

_active_connections_by_ip: Dict[str, int] = defaultdict(int)
_connection_attempts: Dict[str, list] = defaultdict(list)
_all_active_connections: list = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _check_rate_limit(client_ip: str) -> tuple[bool, str]:
    """
    Enforce per-IP and global connection rate limits.

    Args:
        client_ip: IP address of the connecting client.

    Returns:
        A ``(allowed, reason)`` tuple where *allowed* is ``True`` when the
        connection should proceed.
    """
    now = time.time()

    # Evict stale connection-attempt records
    _connection_attempts[client_ip] = [
        t for t in _connection_attempts[client_ip] if now - t < RATE_LIMIT_WINDOW
    ]

    if _active_connections_by_ip.get(client_ip, 0) >= MAX_CONNECTIONS_PER_IP:
        return (
            False,
            f"Too many active connections from {client_ip} (max {MAX_CONNECTIONS_PER_IP})",
        )
    if len(_all_active_connections) >= MAX_TOTAL_CONNECTIONS:
        return False, f"Server at capacity ({MAX_TOTAL_CONNECTIONS} connections)"
    if len(_connection_attempts[client_ip]) >= MAX_CONNECTIONS_PER_WINDOW:
        return (
            False,
            f"Rate limit: max {MAX_CONNECTIONS_PER_WINDOW} connections per {RATE_LIMIT_WINDOW}s",
        )
    return True, "OK"


def _remove_connection(websocket: WebSocket) -> None:
    """Remove *websocket* from all tracking structures."""
    if websocket in _all_active_connections:
        _all_active_connections.remove(websocket)
    if websocket.client:
        client_ip = websocket.client.host
        _active_connections_by_ip[client_ip] = max(
            0, _active_connections_by_ip.get(client_ip, 1) - 1
        )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/{job_id}/ws")
async def job_status_websocket(websocket: WebSocket, job_id: str) -> None:
    """
    Stream real-time status events for a specific job.

    **URL:** ``/api/v2/jobs/{job_id}/ws``

    The endpoint:

    * Enforces per-IP rate limiting (same policy as ``/api/events/ws``).
    * Subscribes to ``job.{job_id}.*`` topics on the OmniCore message bus.
    * Forwards stage-progress, job-complete, and job-failed events as JSON.
    * Sends a heartbeat every 30 seconds when idle.

    Args:
        websocket: The incoming WebSocket connection.
        job_id: The job whose events should be streamed.
    """
    client_ip: str = websocket.client.host if websocket.client else "unknown"
    connection_id: str = f"{client_ip}_{job_id}_{id(websocket)}"
    connection_start: float = time.time()

    # --- Rate-limit check (before accepting) ---
    allowed, reason = _check_rate_limit(client_ip)
    if not allowed:
        logger.warning(
            "Job WS rejected: connection_id=%s reason=%s", connection_id, reason
        )
        try:
            await websocket.close(code=1008, reason=reason)
        except Exception:
            pass
        return

    _connection_attempts[client_ip].append(connection_start)
    _active_connections_by_ip[client_ip] += 1

    try:
        await websocket.accept()
    except Exception as exc:
        logger.error(
            "Failed to accept WS: connection_id=%s error=%s", connection_id, exc
        )
        _active_connections_by_ip[client_ip] -= 1
        return

    _all_active_connections.append(websocket)
    logger.info(
        "Job WS accepted: connection_id=%s job_id=%s total=%d",
        connection_id,
        job_id,
        len(_all_active_connections),
    )

    # Send connection acknowledgement
    try:
        await websocket.send_json(
            {
                "event": "connected",
                "job_id": job_id,
                "connection_id": connection_id,
                "timestamp": _now_iso(),
            }
        )
    except Exception:
        _remove_connection(websocket)
        return

    # --- Message-bus subscription ---
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    subscribed_topics: list = []
    handler_active = threading.Event()
    handler_active.set()
    omnicore_service = None
    event_loop = asyncio.get_event_loop()

    try:
        omnicore_service = _get_omnicore_service()
        bus = getattr(omnicore_service, "_message_bus", None)
        bus_ready = (
            bus is not None
            and getattr(bus, "_dispatchers_started", False)
            and omnicore_service._omnicore_components_available.get(
                "message_bus", False
            )
        )

        if bus_ready:
            job_topics = [
                f"job.{job_id}.stage_progress",
                f"job.{job_id}.complete",
                f"job.{job_id}.failed",
                # Also listen to generic topics that carry job_id in the payload
                "generator.stage_update",
                "job.completed",
                "job.failed",
            ]

            def _message_handler(message) -> None:
                if not handler_active.is_set():
                    return
                try:
                    if isinstance(message, dict):
                        payload = message
                    else:
                        raw = getattr(message, "payload", "{}")
                        try:
                            payload = (
                                json.loads(raw) if isinstance(raw, str) else raw
                            )
                        except (json.JSONDecodeError, TypeError):
                            payload = {"raw": str(raw)}

                    # Filter generic topics by job_id
                    if isinstance(payload, dict):
                        msg_job_id = payload.get("job_id") or payload.get("id")
                        topic = getattr(message, "topic", "")
                        generic_topics = {"generator.stage_update", "job.completed", "job.failed"}
                        if topic in generic_topics and msg_job_id != job_id:
                            return

                    if not event_queue.full():
                        event_loop.call_soon_threadsafe(
                            event_queue.put_nowait, payload
                        )
                    else:
                        logger.warning(
                            "Job WS queue full for connection_id=%s, dropping event",
                            connection_id,
                        )
                except Exception as exc:
                    logger.debug(
                        "Job WS handler error: connection_id=%s error=%s",
                        connection_id,
                        exc,
                    )

            for topic in job_topics:
                try:
                    bus.subscribe(topic, _message_handler)
                    subscribed_topics.append(topic)
                except Exception as exc:
                    logger.warning(
                        "Job WS subscribe failed: topic=%s error=%s", topic, exc
                    )

        # --- Main streaming loop ---
        while True:
            try:
                payload = await asyncio.wait_for(
                    event_queue.get(), timeout=HEARTBEAT_INTERVAL
                )
                msg = _build_message(payload, job_id)
                await websocket.send_json(msg)

                # Stop streaming after terminal events
                if msg.get("event") in {"job_complete", "job_failed"}:
                    logger.info(
                        "Job WS terminal event: connection_id=%s event=%s",
                        connection_id,
                        msg["event"],
                    )
                    break

            except asyncio.TimeoutError:
                # Heartbeat
                try:
                    await websocket.send_json(
                        {"event": "heartbeat", "job_id": job_id, "timestamp": _now_iso()}
                    )
                except Exception:
                    break

            except Exception as exc:
                logger.error(
                    "Job WS stream error: connection_id=%s error=%s",
                    connection_id,
                    exc,
                    exc_info=True,
                )
                break

    except WebSocketDisconnect:
        logger.info("Job WS client disconnected: connection_id=%s", connection_id)
    except Exception as exc:
        logger.error(
            "Job WS unhandled error: connection_id=%s error=%s",
            connection_id,
            exc,
            exc_info=True,
        )
    finally:
        handler_active.clear()
        if bus_ready and omnicore_service:
            _bus = getattr(omnicore_service, "_message_bus", None)
            if _bus:
                for topic in subscribed_topics:
                    try:
                        _bus.unsubscribe(topic, _message_handler)
                    except Exception:
                        pass
        _remove_connection(websocket)
        duration = time.time() - connection_start
        logger.info(
            "Job WS closed: connection_id=%s duration=%.2fs total=%d",
            connection_id,
            duration,
            len(_all_active_connections),
        )


# ---------------------------------------------------------------------------
# Message building helper
# ---------------------------------------------------------------------------


def _build_message(payload: dict, job_id: str) -> dict:
    """
    Translate a raw message-bus payload into the public WebSocket message format.

    Args:
        payload: Raw dict from the message bus.
        job_id: Job identifier, injected if not already present.

    Returns:
        A dict conforming to the message schema documented in the module
        docstring.
    """
    timestamp = payload.get("timestamp") or _now_iso()
    topic = payload.get("topic", "")

    if "complete" in topic or payload.get("status") == "complete":
        return {
            "event": "job_complete",
            "job_id": payload.get("job_id", job_id),
            "result": payload,
            "timestamp": timestamp,
        }

    if "fail" in topic or payload.get("status") == "failed":
        return {
            "event": "job_failed",
            "job_id": payload.get("job_id", job_id),
            "error": payload.get("error") or payload.get("message", "Job failed"),
            "timestamp": timestamp,
        }

    # Default: treat as stage_progress
    return {
        "event": "stage_progress",
        "job_id": payload.get("job_id", job_id),
        "stage": payload.get("stage") or payload.get("message", ""),
        "percent": payload.get("percent", 0),
        "timestamp": timestamp,
        "data": payload,
    }
