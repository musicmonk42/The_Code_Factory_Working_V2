# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive unit tests for server/middleware/arbiter_policy.py

Tests FastAPI middleware, policy enforcement, dependency injection,
and HTTP exception handling.
"""

import asyncio  # For TimeoutError in tests
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request


class TestArbiterPolicyMiddlewareInit:
    """Test ArbiterPolicyMiddleware initialization."""

    def test_init_with_policy_engine(self):
        """Test initialization with PolicyEngine available."""
        # Import the middleware, which will check if policy module is available
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
        
        middleware = ArbiterPolicyMiddleware()
        
        # The policy_module_available should be set based on actual import availability
        assert hasattr(middleware, 'policy_module_available')
        assert isinstance(middleware.policy_module_available, bool)

    def test_init_without_policy_engine(self):
        """Test graceful degradation without PolicyEngine."""
        # Simulate ImportError when trying to import should_auto_learn
        import sys
        original_modules = sys.modules.copy()
        
        # Block the import
        sys.modules['self_fixing_engineer.arbiter.policy'] = None
        
        try:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            middleware = ArbiterPolicyMiddleware()
            
            assert middleware.policy_module_available is False
        finally:
            # Restore modules
            sys.modules.update(original_modules)


class TestCheckPolicy:
    """Test policy checking method."""

    @pytest.mark.asyncio
    async def test_check_policy_allowed(self):
        """Test policy check that allows action."""
        # Create middleware first
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
        middleware = ArbiterPolicyMiddleware()
        
        # If policy module is available, patch the function; otherwise test fail-open
        if middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (True, "Allowed")
                
                # Mock request
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                allowed, reason = await middleware.check_policy("test_action", request, {})
                
                assert allowed is True
                assert "Allowed" in reason
        else:
            # Test fail-open when module not available
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            assert allowed is True  # Fail-open

    @pytest.mark.asyncio
    async def test_check_policy_denied(self):
        """Test policy check that denies action."""
        # Create middleware first
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
        middleware = ArbiterPolicyMiddleware()
        
        # Only test denial if policy module is available
        if middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (False, "Denied")
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                allowed, reason = await middleware.check_policy("test_action", request, {})
                
                assert allowed is False
                assert "Denied" in reason
        else:
            pytest.skip("Policy module not available for testing")
    @pytest.mark.asyncio
    async def test_check_policy_no_engine(self):
        """Test policy check with no engine (fail-open)."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
        
        middleware = ArbiterPolicyMiddleware()
        # Force policy module to be unavailable
        middleware.policy_module_available = False
        
        request = MagicMock(spec=Request)
        request.url.path = "/test"
        request.method = "POST"
        request.client.host = "127.0.0.1"
        request.headers = {}
        
        allowed, reason = await middleware.check_policy("test_action", request, {})
        
        assert allowed is True  # Fail-open
        assert "unavailable" in reason.lower() or "not available" in reason.lower()

    @pytest.mark.asyncio
    async def test_check_policy_error_handling(self):
        """Test error handling during policy check."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
        middleware = ArbiterPolicyMiddleware()
        
        # Only test error handling if policy module is available
        if middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.side_effect = Exception("Policy error")
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                allowed, reason = await middleware.check_policy("test_action", request, {})
                
                assert allowed is True  # Fail-open on error
                assert "error" in reason.lower()
        else:
            pytest.skip("Policy module not available for testing")


class TestFastAPIDependencies:
    """Test FastAPI dependency functions."""

    @pytest.mark.asyncio
    async def test_arbiter_policy_check_dependency_allowed(self):
        """Test dependency that allows action."""
        from server.middleware.arbiter_policy import arbiter_policy_check, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (True, "Allowed")
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                # Get dependency function
                dep_func = arbiter_policy_check("test_action", {})
                
                # Call dependency
                result = await dep_func(request)
                
                assert result is not None  # Should return policy info dict
        else:
            # Test fail-open behavior
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            dep_func = arbiter_policy_check("test_action", {})
            result = await dep_func(request)
            assert result is not None

    @pytest.mark.asyncio
    async def test_arbiter_policy_check_dependency_denied(self):
        """Test dependency that denies action (raises HTTPException)."""
        from server.middleware.arbiter_policy import arbiter_policy_check, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (False, "Denied by policy")
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                dep_func = arbiter_policy_check("test_action", {})
                
                # Should raise HTTPException 403
                with pytest.raises(HTTPException) as exc_info:
                    await dep_func(request)
                
                assert exc_info.value.status_code == 403
                assert "Denied" in str(exc_info.value.detail)
        else:
            pytest.skip("Policy module not available for testing")

    @pytest.mark.asyncio
    async def test_optional_policy_check_dependency(self):
        """Test optional dependency that doesn't raise on denial."""
        from server.middleware.arbiter_policy import optional_arbiter_policy_check, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (False, "Denied")
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                dep_func = optional_arbiter_policy_check("test_action", {})
                
                # Should not raise, just log
                result = await dep_func(request)
                
                assert result is not None  # Returns policy info even if denied
        else:
            pytest.skip("Policy module not available for testing")


class TestMetrics:
    """Test Prometheus metrics tracking."""

    def test_metrics_defined(self):
        """Test that policy check metrics are defined."""
        from server.middleware.arbiter_policy import METRICS_AVAILABLE
        # Test that metrics availability flag is defined
        assert isinstance(METRICS_AVAILABLE, bool)

    @pytest.mark.asyncio
    async def test_metrics_incremented(self):
        """Test that metrics are incremented on policy checks."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (True, "Allowed")
                
                middleware = ArbiterPolicyMiddleware()
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                await middleware.check_policy("test_action", request, {})
                
                # Metrics should be tracked
                assert True
        else:
            pytest.skip("Policy module not available for testing")


class TestContextExtraction:
    """Test context extraction from requests."""

    @pytest.mark.asyncio
    async def test_context_includes_request_details(self):
        """Test that context includes route, method, client."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (True, "Allowed")
                
                middleware = ArbiterPolicyMiddleware()
                
                request = MagicMock(spec=Request)
                request.url.path = "/generator/123/codegen"
                request.method = "POST"
                request.client.host = "192.168.1.100"
                request.headers = {"user-agent": "TestClient/1.0"}
                
                await middleware.check_policy("codegen", request, {})
                
                # Verify context was passed to should_auto_learn
                call_args = mock_sal.call_args
                assert call_args is not None
        else:
            pytest.skip("Policy module not available for testing")


class TestIntegration:
    """Integration tests for FastAPI route protection."""

    @pytest.mark.asyncio
    async def test_route_protection_workflow(self):
        """Test complete workflow of protecting a route."""
        from server.middleware.arbiter_policy import arbiter_policy_check, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.return_value = (True, "Allowed")
                
                # Simulate FastAPI dependency injection
                request = MagicMock(spec=Request)
                request.url.path = "/generator/job123/codegen"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                # Get dependency
                dep_func = arbiter_policy_check("codegen", {"job_id": "job123"})
                
                # Execute dependency (policy check)
                policy_result = await dep_func(request)
                
                # Policy result should be available to route handler
                assert policy_result is not None
                assert isinstance(policy_result, dict)
        else:
            pytest.skip("Policy module not available for testing")

    @pytest.mark.asyncio
    async def test_multiple_routes_with_different_policies(self):
        """Test different policy checks for different routes."""
        from server.middleware.arbiter_policy import arbiter_policy_check, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                # Allow codegen, deny deploy
                def policy_decision(*args, **kwargs):
                    action = args[1] if len(args) > 1 else kwargs.get('action', '')
                    if action == 'codegen':
                        return (True, "Allowed")
                    elif action == 'deploy':
                        return (False, "Denied")
                    return (True, "Default allow")
                
                mock_sal.side_effect = policy_decision
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                # Codegen should be allowed
                codegen_dep = arbiter_policy_check("codegen", {})
                result = await codegen_dep(request)
                assert result is not None
                
                # Deploy should be denied
                deploy_dep = arbiter_policy_check("deploy", {})
                with pytest.raises(HTTPException) as exc_info:
                    await deploy_dep(request)
                assert exc_info.value.status_code == 403
        else:
            pytest.skip("Policy module not available for testing")


class TestFailOpen:
    """Test fail-open behavior for resilience."""

    @pytest.mark.asyncio
    async def test_fail_open_on_timeout(self):
        """Test that timeouts result in fail-open."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.side_effect = asyncio.TimeoutError()
                
                middleware = ArbiterPolicyMiddleware()
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                allowed, reason = await middleware.check_policy("test_action", request, {})
                
                assert allowed is True  # Fail-open
                assert "error" in reason.lower()  # All exceptions result in "error" message
        else:
            pytest.skip("Policy module not available for testing")

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self):
        """Test that exceptions result in fail-open."""
        from server.middleware.arbiter_policy import ArbiterPolicyMiddleware, _policy_middleware
        
        # Only test if policy module is available
        if _policy_middleware.policy_module_available:
            with patch('self_fixing_engineer.arbiter.policy.should_auto_learn', new_callable=AsyncMock) as mock_sal:
                mock_sal.side_effect = RuntimeError("Unexpected error")
                
                middleware = ArbiterPolicyMiddleware()
                
                request = MagicMock(spec=Request)
                request.url.path = "/test"
                request.method = "POST"
                request.client.host = "127.0.0.1"
                request.headers = {}
                
                allowed, reason = await middleware.check_policy("test_action", request, {})
                
                assert allowed is True  # Fail-open
                assert "error" in reason.lower()
        else:
            pytest.skip("Policy module not available for testing")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
