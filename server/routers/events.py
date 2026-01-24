"""
Real-time events streaming endpoints.

Provides WebSocket and Server-Sent Events (SSE) for real-time updates
on jobs, errors, fixes, and platform status through OmniCore.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
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
        # Initialize OmniCore service for this connection
        omnicore_service = get_omnicore_service()
        
        # Check if message bus is available
        if (hasattr(omnicore_service, '_message_bus') and 
            omnicore_service._message_bus and 
            omnicore_service._omnicore_components_available.get("message_bus", False)):
            
            logger.info("Using actual message bus for WebSocket events")
            
            # Subscribe to relevant topics
            event_topics = [
                "job.created",
                "job.updated",
                "job.completed",
                "job.failed",
                "sfe.analysis_complete",
                "sfe.fix_proposed",
                "sfe.fix_applied",
                "generator.stage_update",
                "system.health_check",
            ]
            
            # Create event queue for this WebSocket connection
            event_queue = asyncio.Queue(maxsize=100)
            
            # Define handler for message bus events
            def event_handler(message):
                """Handle events from message bus and queue them for WebSocket."""
                try:
                    # Put event in queue (non-blocking)
                    if not event_queue.full():
                        event_queue.put_nowait(message)
                    else:
                        logger.warning("Event queue full, dropping event")
                except Exception as e:
                    logger.error(f"Error queuing event: {e}")
            
            # Subscribe to all event topics
            for topic in event_topics:
                try:
                    omnicore_service._message_bus.subscribe(topic, event_handler)
                    logger.debug(f"Subscribed to topic: {topic}")
                except Exception as e:
                    logger.warning(f"Could not subscribe to {topic}: {e}")
            
            # Main event loop: forward events from queue to WebSocket
            while True:
                try:
                    # Wait for events with timeout
                    event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                    
                    # Convert to EventMessage format
                    event_msg = EventMessage(
                        event_type=EventType.LOG_MESSAGE,
                        timestamp=datetime.now(timezone.utc),
                        message=event.get("message", "Event received"),
                        data=event,
                        severity="info",
                    )
                    
                    await websocket.send_json(event_msg.to_json_dict())
                    
                except asyncio.TimeoutError:
                    # Send heartbeat if no events for 30 seconds
                    heartbeat = EventMessage(
                        event_type=EventType.PLATFORM_STATUS,
                        timestamp=datetime.now(timezone.utc),
                        message="Platform operational",
                        data={"status": "healthy"},
                        severity="info",
                    )
                    await websocket.send_json(heartbeat.to_json_dict())
                    
                except Exception as e:
                    logger.error(f"Error processing event: {e}")
                    break
        else:
            # Fallback: Use mock heartbeats
            logger.info("Message bus not available, using fallback heartbeat mode")
            
            while True:
                # Placeholder: send heartbeat
                await asyncio.sleep(30)
                event = EventMessage(
                    event_type=EventType.PLATFORM_STATUS,
                    timestamp=datetime.now(timezone.utc),
                    message="Platform operational (fallback mode)",
                    data={"status": "healthy", "mode": "fallback"},
                    severity="info",
                )
                await websocket.send_json(event.to_json_dict())

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
    # Check if message bus is available
    if (omnicore_service and 
        hasattr(omnicore_service, '_message_bus') and 
        omnicore_service._message_bus and 
        omnicore_service._omnicore_components_available.get("message_bus", False)):
        
        logger.info(f"Using actual message bus for SSE events (job_id: {job_id})")
        
        # Create event queue for SSE
        event_queue = asyncio.Queue(maxsize=100)
        
        # Define handler for message bus events
        def event_handler(message):
            """Handle events from message bus and queue them for SSE."""
            try:
                # Filter by job_id if specified
                if job_id and message.get("job_id") != job_id:
                    return
                
                # Put event in queue (non-blocking)
                if not event_queue.full():
                    event_queue.put_nowait(message)
                else:
                    logger.warning("SSE event queue full, dropping event")
            except Exception as e:
                logger.error(f"Error queuing SSE event: {e}")
        
        # Subscribe to relevant topics
        event_topics = [
            "job.created",
            "job.updated",
            "job.completed",
            "job.failed",
            "sfe.analysis_complete",
            "generator.stage_update",
        ]
        
        for topic in event_topics:
            try:
                omnicore_service._message_bus.subscribe(topic, event_handler)
                logger.debug(f"SSE subscribed to topic: {topic}")
            except Exception as e:
                logger.warning(f"Could not subscribe to {topic}: {e}")
        
        # Stream events from queue
        counter = 0
        while counter < 1000:  # Limit to prevent infinite streams
            counter += 1
            
            try:
                # Wait for event with timeout
                event_data = await asyncio.wait_for(event_queue.get(), timeout=5.0)
                
                event = EventMessage(
                    event_type=EventType.LOG_MESSAGE,
                    timestamp=datetime.now(timezone.utc),
                    job_id=job_id,
                    message=event_data.get("message", "Event update"),
                    data=event_data,
                    severity="info",
                )
                
                yield {
                    "event": event.event_type.value,
                    "data": json.dumps(event.to_json_dict()),
                }
                
            except asyncio.TimeoutError:
                # Send keepalive
                event = EventMessage(
                    event_type=EventType.PLATFORM_STATUS,
                    timestamp=datetime.now(timezone.utc),
                    job_id=job_id,
                    message="Keepalive",
                    data={"status": "listening"},
                    severity="info",
                )
                
                yield {
                    "event": "keepalive",
                    "data": json.dumps(event.to_json_dict()),
                }
                
    else:
        # Fallback: Send periodic mock updates
        logger.info(f"Using fallback mode for SSE events (job_id: {job_id})")
        
        counter = 0
        while counter < 100:  # Limit for demo
            counter += 1
            await asyncio.sleep(2)

            event = EventMessage(
                event_type=EventType.LOG_MESSAGE,
                timestamp=datetime.now(timezone.utc),
                job_id=job_id,
                message=f"Progress update {counter} (fallback mode)",
                data={"progress": counter, "mode": "fallback"},
                severity="info",
            )

            yield {
                "event": event.event_type.value,
                "data": json.dumps(event.to_json_dict()),
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
            await connection.send_json(event.to_json_dict())
        except Exception as e:
            logger.error(f"Error broadcasting to client: {e}")
            disconnected.append(connection)

    # Remove disconnected clients
    for connection in disconnected:
        if connection in active_connections:
            active_connections.remove(connection)
