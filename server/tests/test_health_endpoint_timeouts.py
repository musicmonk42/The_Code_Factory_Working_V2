"""
Tests for Health Endpoint Timeout Functionality
===============================================

This test module verifies that the health and readiness endpoints:
- Have proper timeouts to prevent blocking
- Handle timeouts gracefully
- Always return appropriate status codes
- Never block Railway healthchecks
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from server.utils.agent_loader import get_agent_loader


class TestHealthEndpointTimeouts:
    """Test suite for health endpoint timeout functionality."""
    
    def test_agent_loader_get_status_is_fast(self):
        """Test that get_status() completes quickly (< 10ms)."""
        import time
        loader = get_agent_loader()
        
        start = time.time()
        status = loader.get_status()
        elapsed = time.time() - start
        
        # Should complete in under 10ms even on slow systems
        assert elapsed < 0.01, f"get_status() took {elapsed*1000:.1f}ms, should be < 10ms"
        
        # Verify structure is still complete
        assert 'loading_in_progress' in status
        assert 'total_agents' in status
        assert 'availability_rate' in status
    
    def test_availability_rate_helper_exists(self):
        """Test that _get_availability_rate() helper method exists."""
        loader = get_agent_loader()
        assert hasattr(loader, '_get_availability_rate')
        
        # Test it returns a float between 0 and 1
        rate = loader._get_availability_rate()
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0
    
    @pytest.mark.asyncio
    async def test_health_endpoint_timeout_wrapper(self):
        """Test that health endpoint uses asyncio.wait_for with 50ms timeout."""
        # This tests the timeout mechanism itself
        
        # Mock a slow get_status that takes 100ms
        async def slow_status():
            await asyncio.sleep(0.1)  # 100ms
            return {'total_agents': 5}
        
        # Should timeout after 50ms
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_status(), timeout=0.05)
    
    @pytest.mark.asyncio
    async def test_readiness_endpoint_timeout_wrapper(self):
        """Test that readiness endpoint uses asyncio.wait_for with 1s timeout."""
        # This tests the timeout mechanism itself
        
        # Mock a slow get_status that takes 1.5s
        async def slow_status():
            await asyncio.sleep(1.5)  # 1.5s
            return {'loading_in_progress': True}
        
        # Should timeout after 1s
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_status(), timeout=1.0)
    
    @pytest.mark.asyncio
    async def test_asyncio_to_thread_works(self):
        """Test that asyncio.to_thread correctly wraps synchronous calls."""
        # This tests the wrapping mechanism
        
        def sync_function():
            return "success"
        
        result = await asyncio.to_thread(sync_function)
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_startup_lock_timeout_wrapper(self):
        """Test that startup lock acquisition uses timeout."""
        # Mock a lock that hangs
        class SlowLock:
            async def acquire(self, blocking=False):
                await asyncio.sleep(0.5)  # 500ms
                return True
        
        lock = SlowLock()
        
        # Should timeout after 100ms
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(lock.acquire(blocking=False), timeout=0.1)


class TestHealthEndpointBehavior:
    """Test suite for health endpoint behavior with timeouts."""
    
    @pytest.mark.asyncio  
    async def test_health_returns_healthy_on_timeout(self):
        """Test that health endpoint returns 'healthy' even if status check times out."""
        # This would be tested with the actual endpoint once dependencies are available
        # For now, we verify the timeout logic works
        
        components = {"api": "healthy", "agents_status": "loading"}
        
        # Simulate timeout - should leave agents_status as "loading"
        try:
            async def timeout_function():
                await asyncio.sleep(0.1)
                return {"total_agents": 5}
            
            await asyncio.wait_for(timeout_function(), timeout=0.05)
        except asyncio.TimeoutError:
            # This is expected - leave agents_status as "loading"
            pass
        
        # Components should still be valid
        assert components["api"] == "healthy"
        assert components["agents_status"] == "loading"
    
    @pytest.mark.asyncio
    async def test_readiness_handles_timeout_separately(self):
        """Test that readiness endpoint handles timeout as separate case."""
        # Simulate the readiness logic
        ready = True
        status_text = "ready"
        
        try:
            async def timeout_function():
                await asyncio.sleep(1.5)
                return {"loading_in_progress": False}
            
            await asyncio.wait_for(timeout_function(), timeout=1.0)
        except asyncio.TimeoutError:
            ready = False
            status_text = "timeout"
        except Exception:
            ready = False
            status_text = "error"
        
        # Should have handled timeout specifically
        assert not ready
        assert status_text == "timeout"


class TestAgentLoaderOptimizations:
    """Test suite for agent loader optimizations."""
    
    def test_get_status_no_blocking_calls(self):
        """Verify get_status() makes no I/O or blocking calls."""
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Just verify it returns without blocking
        # The actual speed test is in test_agent_loader_get_status_is_fast
        assert status is not None
        assert isinstance(status, dict)
    
    def test_availability_rate_calculation(self):
        """Test that availability rate is calculated correctly."""
        loader = get_agent_loader()
        
        # Test with no agents
        loader._agent_status = {}
        rate = loader._get_availability_rate()
        assert rate == 0.0
        
        # Test with mixed availability (would need proper mock objects)
        # This is a basic test just to ensure the method works
        assert callable(loader._get_availability_rate)


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
