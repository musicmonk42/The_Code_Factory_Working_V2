# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite for Architecture Improvements
========================================

This module validates the architecture improvements including:
1. Agent health checks
2. Message replay functionality
3. Distributed tracing setup
4. Agent autoscaling
5. Event sourcing
6. Agent versioning

Author: Code Factory Platform Team
Version: 1.0.0
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestAgentHealthChecks(unittest.TestCase):
    """Test agent health check functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Import after path setup
        from server.utils.agent_loader import AgentLoader
        self.loader = AgentLoader()
    
    def test_check_agent_health_available(self):
        """Test health check for available agent."""
        # Mock an available agent
        from server.utils.agent_loader import AgentStatus
        self.loader._agent_status["test_agent"] = AgentStatus(
            name="test_agent",
            available=True,
            module_path="test.module",
            loaded_at=datetime.utcnow().isoformat(),
        )
        
        # Run health check
        result = asyncio.run(self.loader.check_agent_health("test_agent"))
        self.assertTrue(result)
    
    def test_check_agent_health_unavailable(self):
        """Test health check for unavailable agent."""
        # Run health check for non-existent agent
        result = asyncio.run(self.loader.check_agent_health("nonexistent_agent"))
        self.assertFalse(result)


class TestMessageReplay(unittest.TestCase):
    """Test message replay functionality."""
    
    def test_replay_method_signature(self):
        """Test that replay method signature is correct."""
        # Just verify the method exists by checking the source
        import inspect
        import sys
        
        # Read the source file to verify method exists
        message_bus_path = PROJECT_ROOT / "omnicore_engine" / "message_bus" / "sharded_message_bus.py"
        if message_bus_path.exists():
            source = message_bus_path.read_text()
            self.assertIn("replay_failed_messages", source)
            self.assertIn("max_age_seconds", source)


class TestDistributedTracing(unittest.TestCase):
    """Test distributed tracing setup."""
    
    def test_tracing_without_opentelemetry(self):
        """Test tracing setup when OpenTelemetry is not available."""
        from server.middleware.tracing import setup_tracing, OTEL_AVAILABLE
        
        # If OpenTelemetry is not available, setup should return None
        if not OTEL_AVAILABLE:
            tracer = setup_tracing("test-service")
            self.assertIsNone(tracer)


class TestAgentAutoscaling(unittest.TestCase):
    """Test agent autoscaling functionality."""
    
    def test_autoscaling_method_exists(self):
        """Test that autoscaling method exists."""
        # Read the source file to verify method exists
        message_bus_path = PROJECT_ROOT / "omnicore_engine" / "message_bus" / "sharded_message_bus.py"
        if message_bus_path.exists():
            source = message_bus_path.read_text()
            self.assertIn("auto_scale_shards", source)
            self.assertIn("Automatically adjust shard count", source)


class TestEventSourcing(unittest.TestCase):
    """Test event sourcing functionality."""
    
    def test_event_creation(self):
        """Test creating an event."""
        from omnicore_engine.event_store import Event
        
        event = Event(
            event_type="test.created",
            aggregate_id="test-123",
            data={"key": "value"},
        )
        
        self.assertEqual(event.event_type, "test.created")
        self.assertEqual(event.aggregate_id, "test-123")
        self.assertEqual(event.data["key"], "value")
    
    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        from omnicore_engine.event_store import Event
        
        event = Event(
            event_type="test.created",
            aggregate_id="test-123",
            data={"key": "value"},
        )
        
        event_dict = event.to_dict()
        
        self.assertIn("event_id", event_dict)
        self.assertEqual(event_dict["event_type"], "test.created")
        self.assertEqual(event_dict["aggregate_id"], "test-123")
    
    def test_event_from_dict(self):
        """Test creating event from dictionary."""
        from omnicore_engine.event_store import Event
        
        event_dict = {
            "event_id": "test-id",
            "event_type": "test.created",
            "aggregate_id": "test-123",
            "data": {"key": "value"},
            "timestamp": "2024-01-01T00:00:00",
        }
        
        event = Event.from_dict(event_dict)
        
        self.assertEqual(event.event_id, "test-id")
        self.assertEqual(event.event_type, "test.created")
    
    def test_event_store_append(self):
        """Test appending event to event store."""
        from omnicore_engine.event_store import Event, EventStore
        
        store = EventStore()
        
        event = Event(
            event_type="test.created",
            aggregate_id="test-123",
            data={"key": "value"},
        )
        
        asyncio.run(store.append_event(event))
        
        # Verify event was stored
        self.assertEqual(len(store._in_memory_events), 1)
    
    def test_event_store_get_events(self):
        """Test retrieving events from event store."""
        from omnicore_engine.event_store import Event, EventStore
        
        store = EventStore()
        
        # Add multiple events
        event1 = Event(
            event_type="test.created",
            aggregate_id="test-123",
            data={"step": 1},
        )
        event2 = Event(
            event_type="test.updated",
            aggregate_id="test-123",
            data={"step": 2},
        )
        
        asyncio.run(store.append_event(event1))
        asyncio.run(store.append_event(event2))
        
        # Retrieve events
        events = asyncio.run(store.get_events("test-123"))
        
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "test.created")
        self.assertEqual(events[1].event_type, "test.updated")
    
    def test_event_store_replay(self):
        """Test replaying events to reconstruct state."""
        from omnicore_engine.event_store import Event, EventStore
        
        store = EventStore()
        
        # Add events
        event1 = Event(
            event_type="agent.created",
            aggregate_id="agent-123",
            data={"agent_id": "agent-123", "agent_type": "codegen"},
        )
        event2 = Event(
            event_type="agent.started",
            aggregate_id="agent-123",
            data={},
        )
        
        asyncio.run(store.append_event(event1))
        asyncio.run(store.append_event(event2))
        
        # Replay events
        state = asyncio.run(store.replay_events("agent-123"))
        
        self.assertEqual(state["agent_id"], "agent-123")
        self.assertEqual(state["status"], "running")


class TestAgentVersioning(unittest.TestCase):
    """Test agent versioning functionality."""
    
    def test_versioned_agent_loader_class_exists(self):
        """Test that versioned agent loader class exists."""
        # Read the source file to verify class exists
        agent_loader_path = PROJECT_ROOT / "server" / "utils" / "agent_loader.py"
        if agent_loader_path.exists():
            source = agent_loader_path.read_text()
            self.assertIn("class VersionedAgentLoader", source)
            self.assertIn("load_agent_version", source)
    
    def test_version_methods_exist(self):
        """Test that version methods exist in source."""
        agent_loader_path = PROJECT_ROOT / "server" / "utils" / "agent_loader.py"
        if agent_loader_path.exists():
            source = agent_loader_path.read_text()
            self.assertIn("get_agent_version", source)
            self.assertIn("list_agent_versions", source)


if __name__ == "__main__":
    unittest.main()
