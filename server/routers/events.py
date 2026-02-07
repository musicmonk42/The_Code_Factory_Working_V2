# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Real-time events streaming endpoints.

Provides WebSocket and Server-Sent Events (SSE) for real-time updates
on jobs, errors, fixes, and platform status through OmniCore.

Industry-standard features:
- Connection rate limiting
- Heartbeat/keepalive
- Graceful error handling
- Comprehensive logging with context
- Metrics and observability
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from sse_starlette.sse import EventSourceResponse

from server.schemas import EventMessage, EventType
from server.services import OmniCoreService
from server.services.omnicore_service import get_omnicore_service as _get_omnicore_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["Events"])

# Constants for rate limiting and connection management
MAX_CONNECTIONS_PER_IP = 5  # Industry standard: limit connections per client
MAX_TOTAL_CONNECTIONS = 1000  # Industry standard: overall connection limit
RATE_LIMIT_WINDOW = 60  # seconds
MAX_CONNECTIONS_PER_WINDOW = 10  # Max new connections per IP per window

# Connection tracking for rate limiting
_connection_attempts: Dict[str, list] = defaultdict(list)
_active_connections_by_ip: Dict[str, int] = defaultdict(int)


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService (uses singleton)."""
    return _get_omnicore_service()


def _is_message_bus_ready(omnicore_service: OmniCoreService) -> bool:
    """
    Check if message bus is fully operational.
    
    Industry-standard readiness check ensuring:
    - Message bus is initialized
    - Dispatcher tasks are running
    - Component is marked as available
    
    Args:
        omnicore_service: OmniCoreService instance to check
        
    Returns:
        bool: True if message bus is ready, False otherwise
    """
    if not hasattr(omnicore_service, '_message_bus'):
        return False
    
    bus = omnicore_service._message_bus
    if not bus:
        return False
    
    # Check if dispatcher tasks are running
    if not hasattr(bus, 'dispatcher_tasks') or not bus.dispatcher_tasks:
        return False
    
    # Check if dispatcher started flag is set
    if not hasattr(bus, '_dispatchers_started') or not bus._dispatchers_started:
        return False
    
    # Check if message bus is marked as available
    if not omnicore_service._omnicore_components_available.get("message_bus", False):
        return False
    
    return True


# Active WebSocket connections
# Note: In production with multiple workers, use a shared connection manager
# (e.g., Redis pub/sub) instead of a global list
active_connections: list[WebSocket] = []


def _remove_connection_safely(websocket: WebSocket) -> None:
    """
    Safely remove a WebSocket from active connections.
    
    Industry-standard connection cleanup with proper tracking.
    
    Args:
        websocket: WebSocket connection to remove
    """
    if websocket in active_connections:
        active_connections.remove(websocket)
        
        # Update per-IP tracking
        if websocket.client:
            client_ip = websocket.client.host
            if client_ip in _active_connections_by_ip:
                _active_connections_by_ip[client_ip] = max(
                    0, _active_connections_by_ip[client_ip] - 1
                )


def _check_rate_limit(client_ip: str) -> tuple[bool, str]:
    """
    Check if client is within rate limits.
    
    Industry-standard rate limiting to prevent abuse.
    
    Args:
        client_ip: Client IP address
        
    Returns:
        Tuple of (allowed: bool, reason: str)
    """
    current_time = time.time()
    
    # Clean up old connection attempts (older than rate limit window)
    if client_ip in _connection_attempts:
        _connection_attempts[client_ip] = [
            t for t in _connection_attempts[client_ip]
            if current_time - t < RATE_LIMIT_WINDOW
        ]
    
    # Check per-IP connection limit
    if _active_connections_by_ip.get(client_ip, 0) >= MAX_CONNECTIONS_PER_IP:
        return False, f"Too many active connections from {client_ip} (max {MAX_CONNECTIONS_PER_IP})"
    
    # Check total connection limit
    if len(active_connections) >= MAX_TOTAL_CONNECTIONS:
        return False, f"Server at max capacity ({MAX_TOTAL_CONNECTIONS} connections)"
    
    # Check rate limit (connections per window)
    if len(_connection_attempts[client_ip]) >= MAX_CONNECTIONS_PER_WINDOW:
        return False, f"Rate limit exceeded: max {MAX_CONNECTIONS_PER_WINDOW} connections per {RATE_LIMIT_WINDOW}s"
    
    return True, "OK"


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event streaming with industry-standard practices.
    
    Features:
    - Rate limiting per IP
    - Connection management
    - Heartbeat/keepalive
    - Graceful error handling
    - Comprehensive logging
    
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
    client_ip = websocket.client.host if websocket.client else "unknown"
    connection_id = f"{client_ip}_{id(websocket)}"
    connection_start = time.time()
    
    # Rate limiting check
    allowed, reason = _check_rate_limit(client_ip)
    if not allowed:
        logger.warning(
            f"WebSocket connection rejected - client_ip={client_ip}, reason={reason}",
            extra={
                "client_ip": client_ip,
                "reason": reason,
                "active_connections": len(active_connections),
                "status": "rate_limited"
            }
        )
        try:
            await websocket.close(code=1008, reason=reason)
        except Exception:
            pass
        return
    
    # Record connection attempt for rate limiting
    _connection_attempts[client_ip].append(connection_start)
    _active_connections_by_ip[client_ip] += 1

    try:
        await websocket.accept()
        active_connections.append(websocket)
        client_info = {
            "host": websocket.client.host if websocket.client else "unknown",
            "port": websocket.client.port if websocket.client else "unknown",
        }
        logger.info(
            f"WebSocket connection accepted - connection_id={connection_id}, "
            f"client_ip={client_ip}, total_connections={len(active_connections)}",
            extra={
                "connection_id": connection_id,
                "client_ip": client_ip,
                "client_port": client_info['port'],
                "total_connections": len(active_connections),
                "status": "connected"
            }
        )
        
        # Send connection acknowledgment with connection metadata
        welcome_msg = EventMessage(
            event_type=EventType.PLATFORM_STATUS,
            timestamp=datetime.now(timezone.utc),
            message="WebSocket connection established",
            data={
                "status": "connected",
                "connection_id": connection_id,
                "client": client_info,
                "server_time": datetime.now(timezone.utc).isoformat(),
            },
            severity="info",
        )
        await websocket.send_json(welcome_msg.to_json_dict())
        
    except Exception as accept_error:
        error_type = type(accept_error).__name__
        logger.error(
            f"Failed to accept WebSocket connection - connection_id={connection_id}, error={error_type}: {accept_error}",
            extra={
                "connection_id": connection_id,
                "client_ip": client_ip,
                "error_type": error_type,
                "error_message": str(accept_error),
                "status": "accept_failed"
            },
            exc_info=True
        )
        _active_connections_by_ip[client_ip] -= 1  # Decrement on failure
        return

    try:
        # Initialize OmniCore service for this connection
        omnicore_service = get_omnicore_service()
        
        # Check if message bus is ready BEFORE subscribing
        if _is_message_bus_ready(omnicore_service):
            
            logger.info("Message bus is ready, using actual event streaming")
            
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
                    
                    try:
                        await websocket.send_json(event_msg.to_json_dict())
                    except Exception as send_error:
                        error_type = type(send_error).__name__
                        logger.error(f"Failed to send event: {error_type} - {send_error}")
                        break
                    
                except asyncio.TimeoutError:
                    # Send heartbeat if no events for 30 seconds
                    heartbeat = EventMessage(
                        event_type=EventType.PLATFORM_STATUS,
                        timestamp=datetime.now(timezone.utc),
                        message="Platform operational",
                        data={"status": "healthy"},
                        severity="info",
                    )
                    try:
                        await websocket.send_json(heartbeat.to_json_dict())
                    except Exception as send_error:
                        logger.error(f"Failed to send heartbeat: {type(send_error).__name__} - {send_error}")
                        break
                    
                except Exception as e:
                    error_type = type(e).__name__
                    logger.error(f"Error processing event: {error_type} - {str(e)}", exc_info=True)
                    break
        else:
            # Fallback: Use mock heartbeats
            logger.warning("Message bus not ready, using fallback heartbeat mode")
            
            while True:
                try:
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
                except Exception as fallback_error:
                    error_type = type(fallback_error).__name__
                    logger.error(f"Fallback mode error: {error_type} - {fallback_error}", exc_info=True)
                    break

    except WebSocketDisconnect:
        _remove_connection_safely(websocket)
        logger.info(
            f"WebSocket client disconnected. Total connections: {len(active_connections)}"
        )
    except Exception as e:
        error_type = type(e).__name__
        connection_count = len(active_connections)
        logger.error(
            f"WebSocket error: {error_type} - {str(e)} | "
            f"Active connections: {connection_count}",
            exc_info=True
        )
        _remove_connection_safely(websocket)


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
    # Check if message bus is ready
    if omnicore_service and _is_message_bus_ready(omnicore_service):
        
        logger.info(f"Message bus ready for SSE events (job_id: {job_id})")
        
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
        _remove_connection_safely(connection)
