"""
Real-time events streaming endpoints.

Provides WebSocket and Server-Sent Events (SSE) for real-time updates
on jobs, errors, fixes, and platform status through OmniCore.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse

from server.schemas import EventMessage, EventType
from server.services import OmniCoreService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["Events"])


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService."""
    return OmniCoreService()


# Active WebSocket connections
# Note: In production with multiple workers, use a shared connection manager
# (e.g., Redis pub/sub) instead of a global list
active_connections: list[WebSocket] = []


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event streaming.

    Streams events from OmniCore's message bus including:
    - Job lifecycle events
    - Stage progress updates
    - Error detections
    - Fix proposals and applications
    - Platform status changes

    **Usage:**
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/events/ws');
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Event:', data);
    };
    ```
    """
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(
        f"WebSocket client connected. Total connections: {len(active_connections)}"
    )

    try:
        while True:
            # In real implementation, subscribe to OmniCore message bus
            # and forward events to WebSocket clients
            # Example:
            # async for event in omnicore_service.subscribe_events():
            #     await websocket.send_json(event)

            # Placeholder: send heartbeat
            await asyncio.sleep(30)
            event = EventMessage(
                event_type=EventType.PLATFORM_STATUS,
                timestamp=datetime.utcnow(),
                message="Platform operational",
                data={"status": "healthy"},
                severity="info",
            )
            await websocket.send_json(event.dict())

    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info(
            f"WebSocket client disconnected. Total connections: {len(active_connections)}"
        )
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


async def event_stream(
    job_id: str = None,
    omnicore_service: OmniCoreService = None,
) -> AsyncGenerator[str, None]:
    """
    Generate Server-Sent Events stream from OmniCore.

    Args:
        job_id: Optional job ID to filter events
        omnicore_service: OmniCore service instance

    Yields:
        SSE-formatted event strings
    """
    # In real implementation, subscribe to OmniCore's message bus
    # and stream events filtered by job_id if provided
    # Example:
    # async for event in omnicore_service.subscribe_events(job_id=job_id):
    #     yield json.dumps(event)

    # Placeholder: Send periodic updates
    counter = 0
    while counter < 100:  # Limit for demo
        counter += 1
        await asyncio.sleep(2)

        event = EventMessage(
            event_type=EventType.LOG_MESSAGE,
            timestamp=datetime.utcnow(),
            job_id=job_id,
            message=f"Progress update {counter}",
            data={"progress": counter},
            severity="info",
        )

        yield {
            "event": event.event_type.value,
            "data": json.dumps(event.dict(), default=str),
        }


@router.get("/sse")
async def sse_endpoint(
    job_id: str = None,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Server-Sent Events (SSE) endpoint for real-time event streaming.

    Streams events from OmniCore's message bus. Optionally filter by job_id.

    **Query Parameters:**
    - job_id: Optional job ID to filter events

    **Returns:**
    - SSE stream of events

    **Usage:**
    ```javascript
    const eventSource = new EventSource('/api/events/sse?job_id=123');
    eventSource.addEventListener('job_updated', (event) => {
        const data = JSON.parse(event.data);
        console.log('Job update:', data);
    });
    ```
    """
    return EventSourceResponse(
        event_stream(job_id=job_id, omnicore_service=omnicore_service)
    )


async def broadcast_event(event: EventMessage):
    """
    Broadcast an event to all connected WebSocket clients.

    This function is called by the platform when events occur
    that need to be broadcast to all listening clients.

    Args:
        event: Event message to broadcast
    """
    if not active_connections:
        return

    logger.debug(f"Broadcasting event to {len(active_connections)} clients")

    # Send to all connected clients
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(event.dict())
        except Exception as e:
            logger.error(f"Error broadcasting to client: {e}")
            disconnected.append(connection)

    # Remove disconnected clients
    for connection in disconnected:
        if connection in active_connections:
            active_connections.remove(connection)
