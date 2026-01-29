"""
Test to verify the health and readiness endpoints are correctly implemented.

This validates that:
1. /health returns HTTP 200 immediately (liveness probe)
2. /ready returns appropriate status based on router and agent loading (readiness probe)
"""
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add server to path
sys.path.insert(0, str(Path(__file__).parent))

# Import server components after path setup
from server.main import health_check, readiness_check
from fastapi import Response


def test_health_endpoint_structure():
    """Verify /health endpoint returns correct structure and always returns 200."""
    
    # Health endpoint should ALWAYS return healthy - it's a pure liveness probe
    # It doesn't check agent status or any other dependencies
    
    # Run async function
    response = asyncio.run(health_check())
    
    # Verify response structure
    assert response.status == "healthy", \
        "/health should ALWAYS return 'healthy' status (liveness probe)"
    assert response.version is not None
    assert 'api' in response.components
    assert response.components['api'] == 'healthy'
    # Note: agents_status is no longer included in /health - use /ready instead
    
    print("✓ /health endpoint returns 'healthy' status immediately")
    print(f"  - Status: {response.status}")
    print(f"  - Components: {response.components}")



def test_readiness_endpoint_structure():
    """Verify /ready endpoint returns correct structure and status code."""
    
    # Test 1: Routers and agents still loading - should return 503
    # We need to patch both _routers_loaded and get_agent_loader
    
    mock_loader = AsyncMock()
    # Use a regular Mock for get_status since it's called synchronously via asyncio.to_thread
    from unittest.mock import Mock
    mock_loader.get_status = Mock(return_value={
        'loading_in_progress': True,
        'loading_error': None,
        'total_agents': 0,
        'availability_rate': 0.0,
        'available_agents': [],
        'unavailable_agents': []
    })
    
    with patch('server.main._routers_loaded', True), \
         patch('server.main._router_load_error', None), \
         patch('server.main.get_agent_loader', return_value=mock_loader):
        response_obj = Response()
        response = asyncio.run(readiness_check(response_obj))
        
        assert response.ready is False, \
            "/ready should return ready=False when agents are loading"
        assert response_obj.status_code == 503, \
            "/ready should return HTTP 503 when not ready"
        assert response.status == "loading"
        
        print("✓ /ready endpoint returns 503 when agents are loading")
        print(f"  - Ready: {response.ready}")
        print(f"  - Status code: {response_obj.status_code}")
    
    # Test 2: Routers and agents loaded - should return 200
    mock_loader = AsyncMock()
    mock_loader.get_status = Mock(return_value={
        'loading_in_progress': False,
        'loading_error': None,
        'total_agents': 5,
        'availability_rate': 1.0,
        'available_agents': ['runner', 'omnicore_engine', 'arbiter', 'codegen', 'testgen'],
        'unavailable_agents': []
    })
    
    with patch('server.main._routers_loaded', True), \
         patch('server.main._router_load_error', None), \
         patch('server.main.get_agent_loader', return_value=mock_loader):
        response_obj = Response()
        response = asyncio.run(readiness_check(response_obj))
        
        assert response.ready is True, \
            "/ready should return ready=True when agents are loaded"
        assert response_obj.status_code == 200, \
            "/ready should return HTTP 200 when ready"
        assert response.status == "ready"
        
        print("✓ /ready endpoint returns 200 when agents are loaded")
        print(f"  - Ready: {response.ready}")
        print(f"  - Status code: {response_obj.status_code}")



def test_health_vs_readiness_separation():
    """
    Verify that /health and /ready serve different purposes.
    
    /health (liveness): Always returns 200 if process is running
    /ready (readiness): Returns 503 if routers/agents not loaded, 200 when ready
    """
    from unittest.mock import Mock
    
    # Simulate agents still loading
    mock_loader = AsyncMock()
    mock_loader.get_status = Mock(return_value={
        'loading_in_progress': True,
        'loading_error': None,
        'total_agents': 0,
        'availability_rate': 0.0,
        'available_agents': [],
        'unavailable_agents': []
    })
    
    with patch('server.main._routers_loaded', True), \
         patch('server.main._router_load_error', None), \
         patch('server.main.get_agent_loader', return_value=mock_loader):
        # Health should be "healthy" (200)
        health_response = asyncio.run(health_check())
        assert health_response.status == "healthy"
        
        # Readiness should be not ready (503)
        response_obj = Response()
        ready_response = asyncio.run(readiness_check(response_obj))
        assert ready_response.ready is False
        assert response_obj.status_code == 503
        
        print("✓ /health and /ready are properly separated")
        print(f"  - /health returns: {health_response.status} (liveness)")
        print(f"  - /ready returns: {ready_response.status} with {response_obj.status_code} (readiness)")


if __name__ == "__main__":
    print("Testing health and readiness endpoints...")
    print()
    
    try:
        test_health_endpoint_structure()
        print()
        test_readiness_endpoint_structure()
        print()
        test_health_vs_readiness_separation()
        print()
        print("=" * 70)
        print("All health endpoint tests passed!")
        print("=" * 70)
        print()
        print("Summary:")
        print("  ✓ /health endpoint: Returns 200 immediately (liveness probe)")
        print("  ✓ /ready endpoint: Returns 503 during loading, 200 when ready (readiness probe)")
        print("  ✓ Endpoints are properly separated for K8s/Railway healthchecks")
        print()
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
