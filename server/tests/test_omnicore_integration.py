# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for OmniCore service with actual module connections.

Tests the integration between server and OmniCore engine components:
- Message bus connectivity
- Plugin registry integration
- Metrics and audit client connections
- Graceful degradation when components unavailable
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
import asyncio

pytestmark = pytest.mark.slow

# Import the service
from server.services.omnicore_service import OmniCoreService


class TestOmniCoreServiceIntegration:
    """Test suite for OmniCore service integration."""

    def test_service_initialization(self):
        """Test that OmniCoreService initializes without errors."""
        service = OmniCoreService()
        assert service is not None
        assert hasattr(service, '_message_bus')
        assert hasattr(service, '_plugin_registry')
        assert hasattr(service, '_metrics_client')
        assert hasattr(service, '_audit_client')
        assert hasattr(service, '_omnicore_components_available')

    def test_component_availability_tracking(self):
        """Test that component availability is properly tracked."""
        service = OmniCoreService()
        
        # Check that availability dict exists
        assert isinstance(service._omnicore_components_available, dict)
        
        # Check expected keys
        expected_keys = ["message_bus", "plugin_registry", "metrics", "audit"]
        for key in expected_keys:
            assert key in service._omnicore_components_available
            assert isinstance(service._omnicore_components_available[key], bool)

    @pytest.mark.asyncio
    async def test_route_job_with_message_bus(self):
        """Test job routing when message bus is available."""
        service = OmniCoreService()
        
        # Create mock message bus
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=True)
        service._message_bus = mock_bus
        service._omnicore_components_available["message_bus"] = True
        
        # Test routing
        result = await service.route_job(
            job_id="test-123",
            source_module="api",
            target_module="sfe",
            payload={"action": "test"}
        )
        
        assert result["routed"] == True
        assert result["job_id"] == "test-123"
        assert result["transport"] == "message_bus"
        
        # Verify publish was called
        mock_bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_job_fallback(self):
        """Test job routing falls back when message bus unavailable."""
        service = OmniCoreService()
        
        # Ensure message bus is not available
        service._message_bus = None
        service._omnicore_components_available["message_bus"] = False
        
        # Test routing
        result = await service.route_job(
            job_id="test-123",
            source_module="api",
            target_module="sfe",
            payload={"action": "test"}
        )
        
        assert result["routed"] == True
        assert result["transport"] == "direct_dispatch_fallback"

    @pytest.mark.asyncio
    async def test_get_plugin_status_with_registry(self):
        """Test plugin status retrieval with actual registry."""
        service = OmniCoreService()
        
        # Mock plugin registry
        mock_registry = Mock()
        mock_registry._plugins = {
            "CORE_SERVICE": {
                "test_plugin": Mock(meta=Mock(version="1.0.0", safe=True))
            }
        }
        service._plugin_registry = mock_registry
        service._omnicore_components_available["plugin_registry"] = True
        
        # Get status
        status = await service.get_plugin_status()
        
        assert status["total_plugins"] == 1
        assert "test_plugin" in status["active_plugins"]
        assert status["source"] == "actual"

    @pytest.mark.asyncio
    async def test_get_plugin_status_fallback(self):
        """Test plugin status falls back when registry unavailable."""
        service = OmniCoreService()
        
        # Ensure registry is not available
        service._plugin_registry = None
        service._omnicore_components_available["plugin_registry"] = False
        
        # Get status
        status = await service.get_plugin_status()
        
        assert "total_plugins" in status
        assert status["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_get_system_health(self):
        """Test system health check with component status."""
        service = OmniCoreService()
        
        # Get health
        health = await service.get_system_health()
        
        assert "status" in health
        assert health["status"] in ["healthy", "degraded", "critical"]
        assert "components" in health
        assert "timestamp" in health
        
        # Check component statuses
        assert "message_bus" in health["components"]
        assert "plugin_registry" in health["components"]
        assert "metrics" in health["components"]
        assert "audit" in health["components"]

    @pytest.mark.asyncio
    async def test_publish_message_with_bus(self):
        """Test message publication when bus is available."""
        service = OmniCoreService()
        
        # Create mock message bus
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=True)
        service._message_bus = mock_bus
        service._omnicore_components_available["message_bus"] = True
        
        # Publish message
        result = await service.publish_message(
            topic="test.topic",
            payload={"data": "test"},
            priority=5
        )
        
        assert result["status"] == "published"
        assert result["topic"] == "test.topic"
        assert result["transport"] == "message_bus"
        
        # Verify publish was called
        mock_bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_message_fallback(self):
        """Test message publication falls back when bus unavailable."""
        service = OmniCoreService()
        
        # Ensure bus is not available
        service._message_bus = None
        service._omnicore_components_available["message_bus"] = False
        
        # Publish message
        result = await service.publish_message(
            topic="test.topic",
            payload={"data": "test"},
            priority=5
        )
        
        assert result["status"] == "published"
        assert result["transport"] == "fallback"

    @pytest.mark.asyncio
    async def test_get_audit_trail_fallback(self):
        """Test audit trail retrieval with fallback."""
        service = OmniCoreService()
        
        # Ensure audit client is not available
        service._audit_client = None
        service._omnicore_components_available["audit"] = False
        
        # Get audit trail
        trail = await service.get_audit_trail("test-job-123")
        
        assert isinstance(trail, list)
        assert len(trail) > 0
        assert trail[0]["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_get_job_metrics_fallback(self):
        """Test job metrics retrieval with fallback."""
        service = OmniCoreService()
        
        # Ensure metrics client is not available
        service._metrics_client = None
        service._omnicore_components_available["metrics"] = False
        
        # Get metrics
        metrics = await service.get_job_metrics("test-job-123")
        
        assert "job_id" in metrics
        assert metrics["job_id"] == "test-job-123"
        assert metrics["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_route_job_generator_uses_direct_dispatch(self):
        """Test that generator targets always use direct dispatch, even when message bus is available.
        
        This test validates the fix for the bug where generator jobs were published to
        the message bus with no subscriber, causing immediate failures.
        """
        service = OmniCoreService()
        
        # Create mock message bus to simulate production environment
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=True)
        service._message_bus = mock_bus
        service._omnicore_components_available["message_bus"] = True
        
        # Mock the _dispatch_generator_action to return test data
        async def mock_dispatch(job_id, action, payload):
            return {
                "status": "completed",
                "job_id": job_id,
                "action": action,
                "message": "Generator action completed successfully"
            }
        
        service._dispatch_generator_action = mock_dispatch
        
        # Test routing to generator
        result = await service.route_job(
            job_id="test-gen-123",
            source_module="api",
            target_module="generator",
            payload={"action": "run_full_pipeline", "readme_content": "test"}
        )
        
        # Verify that direct dispatch was used, not message bus
        assert result["routed"] is True
        assert result["job_id"] == "test-gen-123"
        assert result["transport"] == "direct_dispatch"
        assert result["target"] == "generator"
        
        # Verify that the data key is present with actual results
        assert "data" in result
        assert result["data"]["status"] == "completed"
        assert result["data"]["job_id"] == "test-gen-123"
        
        # Verify that message bus publish was NOT called for generator
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_job_non_generator_uses_message_bus(self):
        """Test that non-generator targets (e.g., sfe) still use message bus when available.
        
        This ensures the fix doesn't break message bus routing for other modules.
        """
        service = OmniCoreService()
        
        # Create mock message bus
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=True)
        service._message_bus = mock_bus
        service._omnicore_components_available["message_bus"] = True
        
        # Test routing to sfe (not generator)
        result = await service.route_job(
            job_id="test-sfe-123",
            source_module="api",
            target_module="sfe",
            payload={"action": "analyze_code"}
        )
        
        # Verify that message bus was used for non-generator target
        assert result["routed"] is True
        assert result["job_id"] == "test-sfe-123"
        assert result["transport"] == "message_bus"
        assert result["target"] == "sfe"
        
        # Verify that message bus publish WAS called for sfe
        mock_bus.publish.assert_called_once()
        
        # Verify that the result does NOT have a data key (fire-and-forget for message bus)
        assert "data" not in result

    @pytest.mark.asyncio
    async def test_route_job_audit_query_sfe_bypasses_message_bus(self):
        """Test that query_audit_logs for SFE targets uses direct dispatch, not the message bus.

        Fix 1 validation: read-only audit queries must always use direct dispatch
        because the message bus is fire-and-forget with no response channel; a
        published message returns no data, so the caller would always receive an
        empty result.
        """
        service = OmniCoreService()

        # Simulate a production environment where the message bus IS available
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=True)
        service._message_bus = mock_bus
        service._omnicore_components_available["message_bus"] = True

        sample_logs = [{"event_type": "bug_detection", "timestamp": "2026-01-01T00:00:00Z"}]

        async def mock_dispatch_sfe(job_id, action, payload):
            return {"logs": sample_logs}

        service._dispatch_sfe_action = mock_dispatch_sfe

        result = await service.route_job(
            job_id="audit-sfe-123",
            source_module="api",
            target_module="sfe",
            payload={"action": "query_audit_logs", "module": "arbiter", "limit": 10},
        )

        # Direct dispatch must have been used, not the message bus
        assert result["routed"] is True
        assert result["job_id"] == "audit-sfe-123"
        assert result["transport"] == "direct_dispatch"
        assert result["target"] == "sfe"

        # The data key must be present and contain the actual logs
        assert "data" in result
        assert result["data"]["logs"] == sample_logs

        # Message bus publish must NOT have been called
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_job_audit_query_handles_dispatch_error(self):
        """Test that a failing _dispatch_sfe_action returns routed=False with error info.

        Ensures the error-handling branch of the query_audit_logs intercept is
        exercised and returns the expected shape without raising.
        """
        service = OmniCoreService()
        service._message_bus = None
        service._omnicore_components_available["message_bus"] = False

        async def failing_dispatch(job_id, action, payload):
            raise RuntimeError("disk read failure")

        service._dispatch_sfe_action = failing_dispatch

        result = await service.route_job(
            job_id="audit-err-456",
            source_module="api",
            target_module="sfe",
            payload={"action": "query_audit_logs", "module": "testgen", "limit": 10},
        )

        assert result["routed"] is False
        assert result["job_id"] == "audit-err-456"
        assert result["transport"] == "direct_dispatch"
        assert "error" in result
        assert "disk read failure" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_sfe_action_audit_query_arbiter_uses_primary_path(self):
        """Fix 2 validation: arbiter audit query reads sfe_bug_manager_audit.log first.

        Ensures the primary write path for the Arbiter's AuditLogManager is
        listed before the legacy canonical paths so that the file reader finds
        actual log data rather than silently returning an empty list.
        """
        service = OmniCoreService()

        captured_paths = []

        async def mock_read(log_paths, payload):
            captured_paths.extend(log_paths)
            return {"logs": []}

        service._read_audit_logs_from_files = mock_read

        await service._dispatch_sfe_action(
            "job-arbiter-1",
            "query_audit_logs",
            {"module": "arbiter", "limit": 10},
        )

        assert captured_paths[0] == "sfe_bug_manager_audit.log", (
            f"Expected sfe_bug_manager_audit.log as first path, got {captured_paths[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_dispatch_sfe_action_audit_query_testgen_uses_primary_path(self):
        """Fix 2 validation: testgen audit query reads atco_artifacts/atco_audit.log first.

        Ensures the primary write path from _get_audit_log_file() is listed
        before the legacy canonical path so the reader finds actual log data.
        """
        service = OmniCoreService()

        captured_paths = []

        async def mock_read(log_paths, payload):
            captured_paths.extend(log_paths)
            return {"logs": []}

        service._read_audit_logs_from_files = mock_read

        await service._dispatch_sfe_action(
            "job-testgen-2",
            "query_audit_logs",
            {"module": "testgen", "limit": 10},
        )

        assert captured_paths[0] == "atco_artifacts/atco_audit.log", (
            f"Expected atco_artifacts/atco_audit.log as first path, got {captured_paths[0]!r}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
