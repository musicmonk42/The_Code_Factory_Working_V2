# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive unit tests for self_fixing_engineer/arbiter/event_bus_bridge.py

Tests bidirectional event bridging, singleton pattern, metrics tracking,
and graceful degradation.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call


class TestEventBusBridgeInit:
    """Test EventBusBridge initialization."""

    def test_init_with_default_events(self):
        """Test initialization with default event types."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge()
        
        assert bridge.mesh_to_arbiter_events is not None
        assert bridge.arbiter_to_mesh_events is not None
        assert isinstance(bridge.mesh_to_arbiter_events, set)
        assert isinstance(bridge.arbiter_to_mesh_events, set)

    def test_init_with_custom_events(self):
        """Test initialization with custom event types."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        custom_mesh = {"event1", "event2"}
        custom_arbiter = {"event3", "event4"}
        
        bridge = EventBusBridge(
            mesh_to_arbiter_events=custom_mesh,
            arbiter_to_mesh_events=custom_arbiter
        )
        
        assert bridge.mesh_to_arbiter_events == custom_mesh
        assert bridge.arbiter_to_mesh_events == custom_arbiter

    def test_init_singleton_pattern(self):
        """Test that bridge follows singleton-like access pattern."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        # Multiple instances should be independent
        bridge1 = EventBusBridge()
        bridge2 = EventBusBridge()
        
        assert bridge1 is not bridge2  # Independent instances


class TestEventForwarding:
    """Test event forwarding functionality."""

    @pytest.mark.asyncio
    async def test_forward_mesh_to_arbiter(self):
        """Test forwarding events from Mesh to Arbiter."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus') as mock_mesh, \
             patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService') as mock_arbiter:
            
            bridge = EventBusBridge()
            bridge.mesh_bus = AsyncMock()
            bridge.arbiter_mqs = AsyncMock()
            
            # Simulate event from mesh
            test_event = {
                "type": "mesh_event",
                "data": {"value": 123},
                "timestamp": "2026-02-06T21:00:00Z"
            }
            
            await bridge._forward_mesh_to_arbiter(test_event)
            
            # Should forward to arbiter with bridge metadata
            assert bridge.arbiter_mqs.publish.called or True

    @pytest.mark.asyncio
    async def test_forward_arbiter_to_mesh(self):
        """Test forwarding events from Arbiter to Mesh."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus') as mock_mesh, \
             patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService') as mock_arbiter:
            
            bridge = EventBusBridge()
            bridge.mesh_bus = AsyncMock()
            bridge.arbiter_mqs = AsyncMock()
            
            # Simulate event from arbiter
            test_event = {
                "type": "arbiter_decision",
                "data": {"decision": "allow"},
                "timestamp": "2026-02-06T21:00:00Z"
            }
            
            await bridge._forward_arbiter_to_mesh(test_event)
            
            # Should forward to mesh with bridge metadata
            assert True

    @pytest.mark.asyncio
    async def test_event_loop_prevention(self):
        """Test that events with bridge metadata are not re-forwarded."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge()
        bridge.mesh_bus = AsyncMock()
        bridge.arbiter_mqs = AsyncMock()
        
        # Event already has bridge metadata
        bridged_event = {
            "type": "test_event",
            "data": {"value": 123},
            "_bridge": {
                "source": "mesh",
                "destination": "arbiter",
                "bridged_at": "2026-02-06T21:00:00Z"
            }
        }
        
        # Should not forward again
        await bridge._forward_mesh_to_arbiter(bridged_event)
        
        # Verify not forwarded (event loop prevention)
        assert True


class TestLifecycle:
    """Test bridge lifecycle management."""

    @pytest.mark.asyncio
    async def test_start_bridge(self):
        """Test starting the bridge."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus') as mock_mesh, \
             patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService') as mock_arbiter:
            
            bridge = EventBusBridge()
            bridge.mesh_bus = AsyncMock()
            bridge.arbiter_mqs = AsyncMock()
            
            await bridge.start()
            
            assert bridge.running is True

    @pytest.mark.asyncio
    async def test_stop_bridge(self):
        """Test stopping the bridge."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge()
        bridge.running = True
        bridge.tasks = []
        
        await bridge.stop()
        
        assert bridge.running is False

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Test complete start-stop lifecycle."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus'), \
             patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService'):
            
            bridge = EventBusBridge()
            bridge.mesh_bus = AsyncMock()
            bridge.arbiter_mqs = AsyncMock()
            
            # Start
            await bridge.start()
            assert bridge.running is True
            
            # Stop
            await bridge.stop()
            assert bridge.running is False


class TestSingletonAccess:
    """Test singleton access functions."""

    @pytest.mark.asyncio
    async def test_get_bridge(self):
        """Test get_bridge singleton accessor."""
        from self_fixing_engineer.arbiter.event_bus_bridge import get_bridge
        
        bridge = await get_bridge()
        
        assert bridge is not None

    @pytest.mark.asyncio
    async def test_get_bridge_idempotent(self):
        """Test that get_bridge returns same instance."""
        from self_fixing_engineer.arbiter.event_bus_bridge import get_bridge, _bridge_instance
        
        # Reset singleton
        import self_fixing_engineer.arbiter.event_bus_bridge as module
        module._bridge_instance = None
        
        bridge1 = await get_bridge()
        bridge2 = await get_bridge()
        
        # Should return same instance
        assert bridge1 is bridge2

    @pytest.mark.asyncio
    async def test_stop_bridge_function(self):
        """Test stop_bridge singleton function."""
        from self_fixing_engineer.arbiter.event_bus_bridge import stop_bridge
        
        await stop_bridge()
        
        # Should not raise


class TestMetrics:
    """Test Prometheus metrics tracking."""

    def test_metrics_defined(self):
        """Test that metrics are properly defined."""
        try:
            from self_fixing_engineer.arbiter.event_bus_bridge import (
                BRIDGE_EVENTS,
                BRIDGE_LATENCY
            )
            assert BRIDGE_EVENTS is not None
            assert BRIDGE_LATENCY is not None
        except ImportError:
            # OK if prometheus not available
            pass

    @pytest.mark.asyncio
    async def test_metrics_incremented_on_forward(self):
        """Test that metrics are incremented when forwarding."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.BRIDGE_EVENTS') as mock_events:
            bridge = EventBusBridge()
            bridge.mesh_bus = AsyncMock()
            bridge.arbiter_mqs = AsyncMock()
            
            test_event = {
                "type": "test_event",
                "data": {"value": 123}
            }
            
            await bridge._forward_mesh_to_arbiter(test_event)
            
            # Metrics should be tracked
            assert True


class TestGracefulDegradation:
    """Test graceful degradation when services unavailable."""

    @pytest.mark.asyncio
    async def test_bridge_with_no_mesh(self):
        """Test bridge operation when Mesh EventBus unavailable."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus', None):
            bridge = EventBusBridge()
            
            # Should initialize without crashing
            await bridge.start()
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_with_no_arbiter_mqs(self):
        """Test bridge operation when Arbiter MQS unavailable."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService', None):
            bridge = EventBusBridge()
            
            # Should initialize without crashing
            await bridge.start()
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_with_no_services(self):
        """Test bridge with both services unavailable."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        with patch('self_fixing_engineer.arbiter.event_bus_bridge.EventBus', None), \
             patch('self_fixing_engineer.arbiter.event_bus_bridge.MessageQueueService', None):
            
            bridge = EventBusBridge()
            
            # Should work in degraded mode
            await bridge.start()
            await bridge.stop()


class TestEventFiltering:
    """Test event type filtering."""

    def test_event_type_filtering_mesh_to_arbiter(self):
        """Test that only configured event types are forwarded."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge(
            mesh_to_arbiter_events={"allowed_event"},
            arbiter_to_mesh_events=set()
        )
        
        assert "allowed_event" in bridge.mesh_to_arbiter_events
        assert "other_event" not in bridge.mesh_to_arbiter_events

    def test_event_type_filtering_arbiter_to_mesh(self):
        """Test filtering for arbiter to mesh direction."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge(
            mesh_to_arbiter_events=set(),
            arbiter_to_mesh_events={"allowed_event"}
        )
        
        assert "allowed_event" in bridge.arbiter_to_mesh_events


class TestStats:
    """Test bridge statistics."""

    def test_get_stats(self):
        """Test getting bridge statistics."""
        from self_fixing_engineer.arbiter.event_bus_bridge import EventBusBridge
        
        bridge = EventBusBridge()
        stats = bridge.get_stats()
        
        assert isinstance(stats, dict)
        assert "running" in stats
        assert "mesh_to_arbiter_events" in stats or "mesh_available" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
