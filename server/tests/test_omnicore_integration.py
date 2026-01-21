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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
