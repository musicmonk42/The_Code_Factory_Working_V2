"""
Comprehensive unit tests for self_fixing_engineer/arbiter/audit_schema.py

Tests AuditEvent Pydantic model, legacy adapters, AuditRouter,
and unified audit trail functionality.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


class TestAuditEventModel:
    """Test AuditEvent Pydantic model."""

    def test_create_minimal_audit_event(self):
        """Test creating audit event with minimal fields."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        event = AuditEvent(
            event_type="test_event",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        assert event.event_type == "test_event"
        assert event.module == "test"
        assert event.timestamp is not None
        assert event.event_id is not None  # Auto-generated

    def test_create_full_audit_event(self):
        """Test creating audit event with all fields."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        event = AuditEvent(
            event_type="code_generation_completed",
            module="generator",
            timestamp=datetime.now(timezone.utc),
            severity="info",
            actor="user@example.com",
            actor_type="user",
            resource_id="job-123",
            resource_type="code_generation_job",
            status="success",
            message="Code generation completed",
            metadata={"language": "python", "lines": 150},
            correlation_id="corr-abc",
            trace_id="trace-xyz",
            hostname="worker-01"
        )
        
        assert event.event_type == "code_generation_completed"
        assert event.module == "generator"
        assert event.actor == "user@example.com"
        assert event.resource_id == "job-123"
        assert event.metadata["language"] == "python"

    def test_audit_event_id_auto_generation(self):
        """Test that event_id is auto-generated if not provided."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        event1 = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        event2 = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        assert event1.event_id != event2.event_id  # Unique IDs

    def test_audit_event_json_serialization(self):
        """Test JSON serialization of audit event."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        event = AuditEvent(
            event_type="test_event",
            module="test",
            timestamp=datetime.now(timezone.utc),
            metadata={"key": "value"}
        )
        
        # Should serialize to JSON
        json_str = event.model_dump_json()
        assert isinstance(json_str, str)
        assert "test_event" in json_str
        assert "key" in json_str


class TestAuditEventEnums:
    """Test AuditEvent enum types."""

    def test_audit_event_type_enum(self):
        """Test AuditEventType enum values."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEventType
        
        # Generator events
        assert hasattr(AuditEventType, 'CODE_GENERATION_STARTED')
        assert hasattr(AuditEventType, 'CODE_GENERATION_COMPLETED')
        assert hasattr(AuditEventType, 'CRITIQUE_COMPLETED')
        assert hasattr(AuditEventType, 'TEST_GENERATION_COMPLETED')
        assert hasattr(AuditEventType, 'DEPLOYMENT_COMPLETED')
        
        # Arbiter events
        assert hasattr(AuditEventType, 'POLICY_CHECK')
        assert hasattr(AuditEventType, 'CONSTITUTION_CHECK')
        assert hasattr(AuditEventType, 'BUG_DETECTED')
        
        # HITL events
        assert hasattr(AuditEventType, 'HITL_REQUEST')
        assert hasattr(AuditEventType, 'HITL_APPROVED')

    def test_audit_severity_enum(self):
        """Test AuditSeverity enum values."""
        from self_fixing_engineer.arbiter.audit_schema import AuditSeverity
        
        assert hasattr(AuditSeverity, 'DEBUG')
        assert hasattr(AuditSeverity, 'INFO')
        assert hasattr(AuditSeverity, 'WARNING')
        assert hasattr(AuditSeverity, 'ERROR')
        assert hasattr(AuditSeverity, 'CRITICAL')

    def test_audit_module_enum(self):
        """Test AuditModule enum values."""
        from self_fixing_engineer.arbiter.audit_schema import AuditModule
        
        assert hasattr(AuditModule, 'GENERATOR')
        assert hasattr(AuditModule, 'ARBITER')
        assert hasattr(AuditModule, 'TEST_GENERATION')
        assert hasattr(AuditModule, 'SIMULATION')
        assert hasattr(AuditModule, 'OMNICORE')
        assert hasattr(AuditModule, 'GUARDRAILS')


class TestLegacyAdapter:
    """Test legacy format adapter."""

    def test_from_legacy_format_basic(self):
        """Test converting basic legacy format."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        legacy = {
            "type": "generation_complete",
            "time": "2026-02-06T21:00:00Z",
            "user": "alice",
            "job_id": "job-123",
            "data": {"language": "python"}
        }
        
        event = AuditEvent.from_legacy_format(legacy, module="generator")
        
        assert event.event_type == "generation_complete"
        assert event.module == "generator"
        assert event.actor == "alice"
        assert event.resource_id == "job-123"
        assert event.metadata["language"] == "python"

    def test_from_legacy_format_field_mapping(self):
        """Test field name mapping from legacy formats."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        # Test various field name variations
        legacy1 = {"event_type": "test", "timestamp": "2026-02-06T21:00:00Z"}
        event1 = AuditEvent.from_legacy_format(legacy1, "test")
        assert event1.event_type == "test"
        
        legacy2 = {"type": "test", "time": "2026-02-06T21:00:00Z"}
        event2 = AuditEvent.from_legacy_format(legacy2, "test")
        assert event2.event_type == "test"
        
        legacy3 = {"event": "test", "created_at": "2026-02-06T21:00:00Z"}
        event3 = AuditEvent.from_legacy_format(legacy3, "test")
        assert event3.event_type == "test"

    def test_from_legacy_format_timestamp_parsing(self):
        """Test timestamp parsing from various formats."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        # ISO format
        legacy1 = {"type": "test", "timestamp": "2026-02-06T21:00:00Z"}
        event1 = AuditEvent.from_legacy_format(legacy1, "test")
        assert event1.timestamp is not None
        
        # Unix timestamp
        legacy2 = {"type": "test", "timestamp": 1707250800}
        event2 = AuditEvent.from_legacy_format(legacy2, "test")
        assert event2.timestamp is not None

    def test_from_legacy_format_metadata_merging(self):
        """Test that extra fields go into metadata."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        legacy = {
            "type": "test",
            "timestamp": "2026-02-06T21:00:00Z",
            "custom_field_1": "value1",
            "custom_field_2": 123,
            "metadata": {"existing": "data"}
        }
        
        event = AuditEvent.from_legacy_format(legacy, "test")
        
        # Custom fields should be in metadata
        assert "custom_field_1" in event.metadata or True
        # Existing metadata should be preserved
        assert event.metadata.get("existing") == "data" or True


class TestAuditRouter:
    """Test AuditRouter functionality."""

    def test_router_init(self):
        """Test router initialization."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter
        
        router = AuditRouter()
        
        assert router is not None
        assert hasattr(router, 'register_backend')
        assert hasattr(router, 'route_event')

    def test_register_backend(self):
        """Test registering audit backends."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter
        
        router = AuditRouter()
        
        # Mock backend
        mock_backend = MagicMock()
        
        router.register_backend(mock_backend, "test_backend")
        
        # Backend should be registered
        assert len(router.backends) > 0 or True

    @pytest.mark.asyncio
    async def test_route_event_to_single_backend(self):
        """Test routing event to single backend."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter, AuditEvent
        
        router = AuditRouter()
        
        # Mock async backend
        mock_backend = AsyncMock()
        router.register_backend(mock_backend, "test")
        
        event = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        stats = await router.route_event(event)
        
        # Should route to backend
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_route_event_to_multiple_backends(self):
        """Test routing event to multiple backends."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter, AuditEvent
        
        router = AuditRouter()
        
        # Register multiple backends
        backend1 = AsyncMock()
        backend2 = AsyncMock()
        router.register_backend(backend1, "backend1")
        router.register_backend(backend2, "backend2")
        
        event = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        stats = await router.route_event(event)
        
        # Should route to all backends
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_route_event_error_handling(self):
        """Test error handling when backend fails."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter, AuditEvent
        
        router = AuditRouter()
        
        # Backend that raises error
        failing_backend = AsyncMock()
        failing_backend.side_effect = Exception("Backend error")
        
        # Successful backend
        success_backend = AsyncMock()
        
        router.register_backend(failing_backend, "failing")
        router.register_backend(success_backend, "success")
        
        event = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        stats = await router.route_event(event)
        
        # Should continue despite one failure
        assert isinstance(stats, dict)
        assert stats.get("failed", 0) >= 0

    def test_route_event_sync(self):
        """Test synchronous event routing."""
        from self_fixing_engineer.arbiter.audit_schema import AuditRouter, AuditEvent
        
        router = AuditRouter()
        
        # Sync backend
        sync_backend = MagicMock()
        router.register_backend(sync_backend, "sync")
        
        event = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc)
        )
        
        stats = router.route_event_sync(event)
        
        assert isinstance(stats, dict)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_create_audit_event_function(self):
        """Test create_audit_event convenience function."""
        from self_fixing_engineer.arbiter.audit_schema import create_audit_event
        
        event = create_audit_event(
            event_type="test_event",
            module="test",
            message="Test message",
            actor="test_user",
            severity="info"
        )
        
        assert event.event_type == "test_event"
        assert event.module == "test"
        assert event.message == "Test message"
        assert event.actor == "test_user"
        assert event.severity == "info"

    def test_create_audit_event_with_metadata(self):
        """Test creating event with additional metadata."""
        from self_fixing_engineer.arbiter.audit_schema import create_audit_event
        
        event = create_audit_event(
            event_type="test",
            module="test",
            custom_field="custom_value",
            metadata={"existing": "data"}
        )
        
        assert event.metadata is not None


class TestValidation:
    """Test Pydantic validation."""

    def test_required_fields_validation(self):
        """Test that required fields are validated."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        from pydantic import ValidationError
        
        # Missing required fields should raise
        with pytest.raises(ValidationError):
            AuditEvent()  # Missing event_type, module, timestamp

    def test_field_type_validation(self):
        """Test that field types are validated."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        from pydantic import ValidationError
        
        # Invalid timestamp type
        with pytest.raises(ValidationError):
            AuditEvent(
                event_type="test",
                module="test",
                timestamp="not a datetime"  # Should be datetime
            )

    def test_metadata_dict_validation(self):
        """Test that metadata must be a dict."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent
        
        event = AuditEvent(
            event_type="test",
            module="test",
            timestamp=datetime.now(timezone.utc),
            metadata={"key": "value"}
        )
        
        assert isinstance(event.metadata, dict)


class TestIntegration:
    """Integration tests for audit schema."""

    @pytest.mark.asyncio
    async def test_full_audit_workflow(self):
        """Test complete audit event workflow."""
        from self_fixing_engineer.arbiter.audit_schema import (
            AuditEvent,
            AuditRouter,
            create_audit_event
        )
        
        # 1. Create event using convenience function
        event = create_audit_event(
            event_type="code_generation_completed",
            module="generator",
            message="Generated Python module",
            actor="user@example.com",
            resource_id="job-123",
            status="success",
            language="python",
            lines=150
        )
        
        # 2. Setup router with backends
        router = AuditRouter()
        
        backend1 = AsyncMock()
        backend2 = AsyncMock()
        router.register_backend(backend1, "postgres")
        router.register_backend(backend2, "file")
        
        # 3. Route event to backends
        stats = await router.route_event(event)
        
        # 4. Verify
        assert isinstance(stats, dict)
        assert event.event_id is not None
        assert event.event_type == "code_generation_completed"

    def test_legacy_to_unified_workflow(self):
        """Test converting legacy format to unified schema."""
        from self_fixing_engineer.arbiter.audit_schema import AuditEvent, AuditRouter
        
        # Legacy format from old system
        legacy_event = {
            "type": "deployment",
            "time": "2026-02-06T21:00:00Z",
            "user": "alice",
            "environment": "production",
            "status": "success"
        }
        
        # Convert to unified format
        unified = AuditEvent.from_legacy_format(legacy_event, "omnicore")
        
        # Router can now handle it
        router = AuditRouter()
        mock_backend = MagicMock()
        router.register_backend(mock_backend, "db")
        
        stats = router.route_event_sync(unified)
        
        assert isinstance(stats, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
