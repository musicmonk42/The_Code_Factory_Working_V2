# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for WebSocket and SSE event streaming bug fixes.

This test module validates the following critical bug fixes:
1. Thread-safe asyncio.Queue access from ShardedMessageBus dispatcher threads
2. Proper unsubscription of callbacks on WebSocket disconnect
3. _active_connections_by_ip counter cleanup on all exit paths
4. Same fixes applied to SSE event streaming
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime, timezone

# Import the router and related functionality
from server.routers.events import (
    websocket_endpoint,
    event_stream,
    _remove_connection_safely,
    _active_connections_by_ip,
    active_connections,
)


class TestWebSocketBugFixes:
    """Test WebSocket bug fixes."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = MagicMock()
        ws.client = MagicMock()
        ws.client.host = "127.0.0.1"
        ws.client.port = 12345
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.fixture
    def mock_message_bus(self):
        """Create a mock message bus."""
        bus = MagicMock()
        bus.subscribe = MagicMock()
        bus.unsubscribe = MagicMock()
        bus.dispatcher_tasks = [MagicMock()]
        bus._dispatchers_started = True
        return bus

    @pytest.fixture
    def mock_omnicore_service(self, mock_message_bus):
        """Create a mock OmniCore service."""
        service = MagicMock()
        service._message_bus = mock_message_bus
        service._omnicore_components_available = {"message_bus": True}
        return service

    @pytest.mark.asyncio
    async def test_bug1_thread_safe_queue_access_websocket(
        self, mock_websocket, mock_omnicore_service
    ):
        """
        Test Bug 1 Fix: Verify thread-safe queue access via call_soon_threadsafe.
        
        The event_handler should use call_soon_threadsafe to enqueue events
        when called from ThreadPoolExecutor workers.
        """
        event_loop = asyncio.get_event_loop()
        
        # Track if call_soon_threadsafe was used
        call_soon_threadsafe_called = False
        original_call_soon_threadsafe = event_loop.call_soon_threadsafe
        
        def tracked_call_soon_threadsafe(*args, **kwargs):
            nonlocal call_soon_threadsafe_called
            call_soon_threadsafe_called = True
            return original_call_soon_threadsafe(*args, **kwargs)
        
        with patch("server.routers.events.get_omnicore_service", return_value=mock_omnicore_service):
            with patch.object(event_loop, "call_soon_threadsafe", side_effect=tracked_call_soon_threadsafe):
                # Simulate a send error to exit the loop quickly
                mock_websocket.send_json.side_effect = RuntimeError("Connection closed")
                
                # Store the handler that gets registered
                registered_handler = None
                def capture_subscribe(topic, handler):
                    nonlocal registered_handler
                    registered_handler = handler
                
                mock_omnicore_service._message_bus.subscribe.side_effect = capture_subscribe
                
                # Run the websocket endpoint (it will exit on send error)
                try:
                    await websocket_endpoint(mock_websocket)
                except Exception:
                    pass
                
                # Verify handler was registered
                assert registered_handler is not None, "Event handler should be registered"
                
                # Simulate message from ThreadPoolExecutor (this is the key test)
                mock_message = MagicMock()
                mock_message.payload = json.dumps({"message": "test"})
                mock_message.topic = "test.topic"
                mock_message.trace_id = "trace-123"
                mock_message.timestamp = datetime.now(timezone.utc).timestamp()
                
                # Call the handler (simulating ThreadPoolExecutor call)
                registered_handler(mock_message)
                
                # Allow event loop to process
                await asyncio.sleep(0.1)
                
                # Verify call_soon_threadsafe was used
                assert call_soon_threadsafe_called, "call_soon_threadsafe should be used for thread-safe queue access"

    @pytest.mark.asyncio
    async def test_bug2_unsubscribe_on_disconnect_websocket(
        self, mock_websocket, mock_omnicore_service
    ):
        """
        Test Bug 2 Fix: Verify all subscriptions are cleaned up on disconnect.
        
        When a WebSocket disconnects, all topic subscriptions should be
        properly unsubscribed to prevent ghost subscribers.
        """
        with patch("server.routers.events.get_omnicore_service", return_value=mock_omnicore_service):
            # Simulate a send error to trigger disconnect
            mock_websocket.send_json.side_effect = RuntimeError("Connection closed")
            
            # Track subscriptions
            subscribed_topics = []
            unsubscribed_topics = []
            
            def track_subscribe(topic, handler):
                subscribed_topics.append(topic)
            
            def track_unsubscribe(topic, handler):
                unsubscribed_topics.append(topic)
            
            mock_omnicore_service._message_bus.subscribe.side_effect = track_subscribe
            mock_omnicore_service._message_bus.unsubscribe.side_effect = track_unsubscribe
            
            # Run the websocket endpoint
            try:
                await websocket_endpoint(mock_websocket)
            except Exception:
                pass
            
            # Verify subscriptions were made
            assert len(subscribed_topics) > 0, "Should have subscribed to topics"
            
            # Verify all subscriptions were cleaned up
            assert len(unsubscribed_topics) == len(subscribed_topics), \
                f"All {len(subscribed_topics)} subscriptions should be unsubscribed, but only {len(unsubscribed_topics)} were"
            
            # Verify the same topics were unsubscribed
            assert set(unsubscribed_topics) == set(subscribed_topics), \
                "Unsubscribed topics should match subscribed topics"

    @pytest.mark.asyncio
    async def test_bug3_connection_cleanup_always_called_websocket(
        self, mock_websocket, mock_omnicore_service
    ):
        """
        Test Bug 3 Fix: Verify _remove_connection_safely is always called.
        
        The cleanup should happen regardless of how the connection ends
        (normal exit, exception, or disconnect).
        """
        # Clear the global tracking
        active_connections.clear()
        _active_connections_by_ip.clear()
        
        with patch("server.routers.events.get_omnicore_service", return_value=mock_omnicore_service):
            # Test 1: Normal exit via send error
            mock_websocket.send_json.side_effect = RuntimeError("Connection closed")
            
            initial_count = _active_connections_by_ip.get("127.0.0.1", 0)
            
            try:
                await websocket_endpoint(mock_websocket)
            except Exception:
                pass
            
            # Connection should be cleaned up
            final_count = _active_connections_by_ip.get("127.0.0.1", 0)
            assert final_count == initial_count, \
                f"Connection counter should be cleaned up (initial={initial_count}, final={final_count})"
            
            # Active connections should be empty
            assert len(active_connections) == 0, \
                f"Active connections should be cleaned up, found {len(active_connections)}"

    @pytest.mark.asyncio
    async def test_bug3_connection_cleanup_on_exception_websocket(
        self, mock_websocket, mock_omnicore_service
    ):
        """
        Test Bug 3 Fix: Verify cleanup on exception in main loop.
        """
        active_connections.clear()
        _active_connections_by_ip.clear()
        
        with patch("server.routers.events.get_omnicore_service", return_value=mock_omnicore_service):
            # Simulate an exception in the event processing loop
            mock_websocket.send_json.side_effect = [
                None,  # Welcome message succeeds
                Exception("Unexpected error")  # Then fails
            ]
            
            try:
                await websocket_endpoint(mock_websocket)
            except Exception:
                pass
            
            # Verify cleanup occurred
            assert "127.0.0.1" not in _active_connections_by_ip or _active_connections_by_ip["127.0.0.1"] == 0, \
                "Connection counter should be cleaned up even on exception"
            assert len(active_connections) == 0, \
                "Active connections should be cleaned up even on exception"


class TestSSEBugFixes:
    """Test SSE (Server-Sent Events) bug fixes."""

    @pytest.fixture
    def mock_message_bus(self):
        """Create a mock message bus."""
        bus = MagicMock()
        bus.subscribe = MagicMock()
        bus.unsubscribe = MagicMock()
        bus.dispatcher_tasks = [MagicMock()]
        bus._dispatchers_started = True
        return bus

    @pytest.fixture
    def mock_omnicore_service(self, mock_message_bus):
        """Create a mock OmniCore service."""
        service = MagicMock()
        service._message_bus = mock_message_bus
        service._omnicore_components_available = {"message_bus": True}
        return service

    @pytest.mark.asyncio
    async def test_bug4_thread_safe_queue_access_sse(self, mock_omnicore_service):
        """
        Test Bug 4 Fix: Verify thread-safe queue access in SSE handler.
        
        The SSE event_handler should use call_soon_threadsafe to enqueue events.
        """
        event_loop = asyncio.get_event_loop()
        
        # Track if call_soon_threadsafe was used
        call_soon_threadsafe_called = False
        original_call_soon_threadsafe = event_loop.call_soon_threadsafe
        
        def tracked_call_soon_threadsafe(*args, **kwargs):
            nonlocal call_soon_threadsafe_called
            call_soon_threadsafe_called = True
            return original_call_soon_threadsafe(*args, **kwargs)
        
        with patch.object(event_loop, "call_soon_threadsafe", side_effect=tracked_call_soon_threadsafe):
            # Store the handler that gets registered
            registered_handler = None
            def capture_subscribe(topic, handler):
                nonlocal registered_handler
                registered_handler = handler
            
            mock_omnicore_service._message_bus.subscribe.side_effect = capture_subscribe
            
            # Create generator
            gen = event_stream(job_id="test-job", omnicore_service=mock_omnicore_service)
            
            # Start the generator to trigger subscriptions
            try:
                # Get first event (will be a keepalive after timeout)
                await asyncio.wait_for(gen.__anext__(), timeout=0.2)
            except (asyncio.TimeoutError, StopAsyncIteration):
                pass
            
            # Verify handler was registered
            assert registered_handler is not None, "Event handler should be registered"
            
            # Simulate message from ThreadPoolExecutor
            mock_message = MagicMock()
            mock_message.payload = json.dumps({"message": "test", "job_id": "test-job"})
            mock_message.topic = "test.topic"
            mock_message.trace_id = "trace-123"
            mock_message.timestamp = datetime.now(timezone.utc).timestamp()
            
            # Call the handler
            registered_handler(mock_message)
            
            # Allow event loop to process
            await asyncio.sleep(0.1)
            
            # Clean up generator
            try:
                await gen.aclose()
            except Exception:
                pass
            
            # Verify call_soon_threadsafe was used
            assert call_soon_threadsafe_called, "SSE should use call_soon_threadsafe for thread-safe queue access"

    @pytest.mark.asyncio
    async def test_bug4_unsubscribe_on_stream_end_sse(self, mock_omnicore_service):
        """
        Test Bug 4 Fix: Verify all subscriptions are cleaned up when SSE stream ends.
        
        When an SSE stream ends, all topic subscriptions should be
        properly unsubscribed.
        """
        # Track subscriptions
        subscribed_topics = []
        unsubscribed_topics = []
        
        def track_subscribe(topic, handler):
            subscribed_topics.append(topic)
        
        def track_unsubscribe(topic, handler):
            unsubscribed_topics.append(topic)
        
        mock_omnicore_service._message_bus.subscribe.side_effect = track_subscribe
        mock_omnicore_service._message_bus.unsubscribe.side_effect = track_unsubscribe
        
        # Create and consume generator
        gen = event_stream(job_id="test-job", omnicore_service=mock_omnicore_service)
        
        # Consume a few events then close
        try:
            for _ in range(2):
                try:
                    await asyncio.wait_for(gen.__anext__(), timeout=0.2)
                except asyncio.TimeoutError:
                    break
        except StopAsyncIteration:
            pass
        finally:
            # Close the generator to trigger cleanup
            try:
                await gen.aclose()
            except Exception:
                pass
        
        # Small delay to allow cleanup to complete
        await asyncio.sleep(0.1)
        
        # Verify subscriptions were made
        assert len(subscribed_topics) > 0, "Should have subscribed to topics"
        
        # Verify all subscriptions were cleaned up
        assert len(unsubscribed_topics) == len(subscribed_topics), \
            f"All {len(subscribed_topics)} subscriptions should be unsubscribed, but only {len(unsubscribed_topics)} were"


class TestConnectionCleanup:
    """Test connection cleanup helper functions."""

    def test_remove_connection_safely(self):
        """Test _remove_connection_safely properly updates tracking."""
        # Clear globals
        active_connections.clear()
        _active_connections_by_ip.clear()
        
        # Create mock websocket
        ws = MagicMock()
        ws.client = MagicMock()
        ws.client.host = "192.168.1.1"
        
        # Add to tracking
        active_connections.append(ws)
        _active_connections_by_ip["192.168.1.1"] = 1
        
        # Remove
        _remove_connection_safely(ws)
        
        # Verify removal
        assert ws not in active_connections, "WebSocket should be removed from active_connections"
        assert _active_connections_by_ip["192.168.1.1"] == 0, "Counter should be decremented"
        
        # Test double removal doesn't crash
        _remove_connection_safely(ws)
        assert _active_connections_by_ip["192.168.1.1"] == 0, "Counter should stay at 0"

    def test_remove_connection_safely_handles_missing_ip(self):
        """Test _remove_connection_safely handles missing IP gracefully."""
        active_connections.clear()
        _active_connections_by_ip.clear()
        
        ws = MagicMock()
        ws.client = None  # No client info
        
        active_connections.append(ws)
        
        # Should not crash
        _remove_connection_safely(ws)
        
        assert ws not in active_connections, "WebSocket should still be removed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
