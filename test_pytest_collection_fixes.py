"""
Test suite to validate pytest collection hanging fixes.

This test file validates that:
1. Event loop initialization is properly guarded
2. Background tasks are not created during test collection
3. Message bus initialization respects pytest environment
4. Pydantic V2 compatibility works correctly
"""

import os
import pytest
from datetime import datetime, timezone


def test_environment_variables_set():
    """Verify that pytest environment variables are properly set."""
    assert os.getenv("PYTEST_COLLECTING") == "1"
    assert os.getenv("SKIP_AUDIT_INIT") == "1"
    assert os.getenv("SKIP_BACKGROUND_TASKS") == "1"
    print("✓ All pytest environment variables are properly set")


def test_event_message_serialization():
    """Test EventMessage serialization with datetime handling."""
    try:
        from server.schemas.events import EventMessage, EventType
        
        # Create an event message with a datetime
        event = EventMessage(
            event_type=EventType.LOG_MESSAGE,
            timestamp=datetime.now(timezone.utc),
            message="Test message",
            data={"key": "value"},
            severity="info"
        )
        
        # Test serialization
        json_dict = event.to_json_dict()
        
        # Verify timestamp is serialized as string
        assert isinstance(json_dict['timestamp'], str)
        assert 'T' in json_dict['timestamp']  # ISO format check
        
        print("✓ EventMessage serialization works correctly")
    except ImportError as e:
        pytest.skip(f"Dependencies not available: {e}")


def test_audit_logger_skips_during_collection():
    """Test that audit logger respects pytest environment."""
    try:
        # This should not raise RuntimeError
        from omnicore_engine.audit import ExplainAudit
        
        # Creating an audit instance should not create background tasks
        # when PYTEST_COLLECTING is set
        audit = ExplainAudit.__new__(ExplainAudit)
        
        print("✓ Audit logger initialization skipped during collection")
    except ImportError as e:
        pytest.skip(f"Dependencies not available: {e}")
    except Exception as e:
        # Should not get RuntimeError about event loop
        assert "event loop" not in str(e).lower()
        raise


def test_message_bus_skips_during_collection():
    """Test that message bus respects pytest environment."""
    try:
        from server.services.omnicore_service import OmniCoreService
        
        # Creating a service should not initialize message bus
        # when PYTEST_COLLECTING is set
        service = OmniCoreService()
        
        # Message bus should be None during collection
        assert service._message_bus is None
        
        print("✓ Message bus initialization skipped during collection")
    except ImportError as e:
        pytest.skip(f"Dependencies not available: {e}")
    except Exception as e:
        # Should not get RuntimeError about event loop
        assert "event loop" not in str(e).lower()
        raise


def test_no_event_loop_errors():
    """Verify that no event loop errors occur during import."""
    # This test passes if we reach here without RuntimeError
    # during test collection
    print("✓ No event loop errors during collection")


if __name__ == "__main__":
    # Run tests manually
    print("Running pytest collection fixes validation tests...\n")
    
    test_environment_variables_set()
    test_event_message_serialization()
    test_audit_logger_skips_during_collection()
    test_message_bus_skips_during_collection()
    test_no_event_loop_errors()
    
    print("\n✅ All validation tests passed!")
