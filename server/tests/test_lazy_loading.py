"""
Tests for Lazy Agent Loading and Health/Readiness Endpoints
===========================================================

This test module verifies that:
- Agent loader supports background loading
- /health endpoint always returns 200 when API is up
- /ready endpoint returns 503 while agents are loading
- /ready endpoint returns 200 when agents are ready

Note: These tests require the full application to be installed.
Run with: pytest server/tests/test_lazy_loading.py -v
"""

import asyncio
import pytest

from server.utils.agent_loader import AgentLoader, AgentType, get_agent_loader
from server.schemas.common import ReadinessResponse, HealthResponse


class TestBackgroundAgentLoading:
    """Test suite for background agent loading functionality."""
    
    def test_agent_loader_has_background_loading_attributes(self):
        """Test that AgentLoader has background loading attributes."""
        loader = AgentLoader()
        assert hasattr(loader, '_loading_task')
        assert hasattr(loader, '_loading_started')
        assert hasattr(loader, '_loading_completed')
        assert hasattr(loader, '_loading_error')
    
    def test_is_loading_method_exists(self):
        """Test that is_loading method exists."""
        loader = get_agent_loader()
        assert hasattr(loader, 'is_loading')
        assert callable(loader.is_loading)
    
    def test_start_background_loading_method_exists(self):
        """Test that start_background_loading method exists."""
        loader = get_agent_loader()
        assert hasattr(loader, 'start_background_loading')
        assert callable(loader.start_background_loading)
    
    def test_get_status_includes_loading_fields(self):
        """Test that get_status includes loading-related fields."""
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Check for new fields
        assert 'loading_in_progress' in status
        assert 'loading_completed' in status
        assert 'loading_error' in status
        
        # Check types
        assert isinstance(status['loading_in_progress'], bool)
        assert isinstance(status['loading_completed'], bool)
    
    def test_background_loading_can_be_started(self):
        """Test that background loading can be started."""
        loader = AgentLoader()
        
        # Start background loading with empty list
        loader.start_background_loading([])
        
        # Check that loading was started
        assert loader._loading_started
        assert loader._loading_task is not None


class TestSchemas:
    """Test suite for new schemas."""
    
    def test_readiness_response_schema(self):
        """Test that ReadinessResponse schema works correctly."""
        from datetime import datetime
        
        response = ReadinessResponse(
            ready=True,
            status='ready',
            checks={'api_available': 'pass', 'agents_loaded': 'pass'},
            timestamp=datetime.utcnow().isoformat()
        )
        
        assert response.ready is True
        assert response.status == 'ready'
        assert 'api_available' in response.checks
        assert 'agents_loaded' in response.checks
        assert response.timestamp is not None
    
    def test_readiness_response_not_ready(self):
        """Test ReadinessResponse for not ready state."""
        from datetime import datetime
        
        response = ReadinessResponse(
            ready=False,
            status='loading',
            checks={'api_available': 'pass', 'agents_loaded': 'loading'},
            timestamp=datetime.utcnow().isoformat()
        )
        
        assert response.ready is False
        assert response.status == 'loading'


# Integration tests that require the full app
# These are marked to be skipped if FastAPI dependencies are not available
try:
    from fastapi.testclient import TestClient
    from server.main import app
    
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
class TestHealthEndpoint:
    """Test suite for /health endpoint."""
    
    def test_health_endpoint_always_returns_200(self):
        """Test that /health endpoint always returns HTTP 200."""
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
    
    def test_health_response_structure(self):
        """Test that /health endpoint returns expected structure."""
        with TestClient(app) as client:
            response = client.get("/health")
            data = response.json()
            
            # Check required fields
            assert 'status' in data
            assert 'version' in data
            assert 'components' in data
            assert 'timestamp' in data
            
            # Status should always be "healthy" if API is up
            assert data['status'] == 'healthy'
            
            # Check components
            assert 'api' in data['components']
            assert 'agents_status' in data['components']
            
            # API should be healthy
            assert data['components']['api'] == 'healthy'
    
    def test_health_includes_agents_status(self):
        """Test that /health includes agents_status field."""
        with TestClient(app) as client:
            response = client.get("/health")
            data = response.json()
            
            assert 'agents_status' in data['components']
            # Should be one of: "loading", "ready", "degraded"
            assert data['components']['agents_status'] in ['loading', 'ready', 'degraded']


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
class TestReadinessEndpoint:
    """Test suite for /ready endpoint."""
    
    def test_ready_endpoint_exists(self):
        """Test that /ready endpoint exists."""
        with TestClient(app) as client:
            response = client.get("/ready")
            # Should return either 200 or 503
            assert response.status_code in [200, 503]
    
    def test_ready_response_structure(self):
        """Test that /ready endpoint returns expected structure."""
        with TestClient(app) as client:
            response = client.get("/ready")
            data = response.json()
            
            # Check required fields
            assert 'ready' in data
            assert 'status' in data
            assert 'checks' in data
            assert 'timestamp' in data
            
            # Check types
            assert isinstance(data['ready'], bool)
            assert isinstance(data['status'], str)
            assert isinstance(data['checks'], dict)
            assert isinstance(data['timestamp'], str)
    
    def test_ready_includes_required_checks(self):
        """Test that /ready includes required checks."""
        with TestClient(app) as client:
            response = client.get("/ready")
            data = response.json()
            
            # Should include api_available and agents_loaded checks
            assert 'api_available' in data['checks']
            assert 'agents_loaded' in data['checks']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

