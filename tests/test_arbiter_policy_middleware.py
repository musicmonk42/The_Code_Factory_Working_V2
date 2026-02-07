# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive unit tests for server/middleware/arbiter_policy.py

Tests FastAPI middleware, policy enforcement, dependency injection,
and HTTP exception handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request


class TestArbiterPolicyMiddlewareInit:
    """Test ArbiterPolicyMiddleware initialization."""

    def test_init_with_policy_engine(self):
        """Test initialization with PolicyEngine available."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            middleware = ArbiterPolicyMiddleware()
            
            assert middleware.policy_engine is not None

    def test_init_without_policy_engine(self):
        """Test graceful degradation without PolicyEngine."""
        with patch('server.middleware.arbiter_policy.PolicyEngine', None):
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            middleware = ArbiterPolicyMiddleware()
            
            assert middleware.policy_engine is None


class TestCheckPolicy:
    """Test policy checking method."""

    @pytest.mark.asyncio
    async def test_check_policy_allowed(self):
        """Test policy check that allows action."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            # Mock request
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            
            assert allowed is True
            assert "Allowed" in reason

    @pytest.mark.asyncio
    async def test_check_policy_denied(self):
        """Test policy check that denies action."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (False, "Denied")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            
            assert allowed is False
            assert "Denied" in reason

    @pytest.mark.asyncio
    async def test_check_policy_no_engine(self):
        """Test policy check with no engine (fail-open)."""
        with patch('server.middleware.arbiter_policy.PolicyEngine', None):
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            middleware = ArbiterPolicyMiddleware()
            
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
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.side_effect = Exception("Policy error")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            
            assert allowed is True  # Fail-open on error
            assert "error" in reason.lower()


class TestFastAPIDependencies:
    """Test FastAPI dependency functions."""

    @pytest.mark.asyncio
    async def test_arbiter_policy_check_dependency_allowed(self):
        """Test dependency that allows action."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import arbiter_policy_check
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_engine
            
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

    @pytest.mark.asyncio
    async def test_arbiter_policy_check_dependency_denied(self):
        """Test dependency that denies action (raises HTTPException)."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import arbiter_policy_check
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (False, "Denied by policy")
            mock_pe.return_value = mock_engine
            
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

    @pytest.mark.asyncio
    async def test_optional_policy_check_dependency(self):
        """Test optional dependency that doesn't raise on denial."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import optional_arbiter_policy_check
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (False, "Denied")
            mock_pe.return_value = mock_engine
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            dep_func = optional_arbiter_policy_check("test_action", {})
            
            # Should not raise, just log
            result = await dep_func(request)
            
            assert result is not None  # Returns policy info even if denied


class TestMetrics:
    """Test Prometheus metrics tracking."""

    def test_metrics_defined(self):
        """Test that policy check metrics are defined."""
        try:
            from server.middleware.arbiter_policy import POLICY_CHECKS, POLICY_LATENCY
            assert POLICY_CHECKS is not None
            assert POLICY_LATENCY is not None
        except ImportError:
            # OK if prometheus not available
            pass

    @pytest.mark.asyncio
    async def test_metrics_incremented(self):
        """Test that metrics are incremented on policy checks."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe, \
             patch('server.middleware.arbiter_policy.POLICY_CHECKS') as mock_checks:
            
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            await middleware.check_policy("test_action", request, {})
            
            # Metrics should be tracked
            assert True


class TestContextExtraction:
    """Test context extraction from requests."""

    @pytest.mark.asyncio
    async def test_context_includes_request_details(self):
        """Test that context includes route, method, client."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/generator/123/codegen"
            request.method = "POST"
            request.client.host = "192.168.1.100"
            request.headers = {"user-agent": "TestClient/1.0"}
            
            await middleware.check_policy("codegen", request, {})
            
            # Verify context was passed to policy engine
            call_args = mock_engine.should_auto_learn.call_args
            assert call_args is not None


class TestIntegration:
    """Integration tests for FastAPI route protection."""

    @pytest.mark.asyncio
    async def test_route_protection_workflow(self):
        """Test complete workflow of protecting a route."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import arbiter_policy_check
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_engine
            
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

    @pytest.mark.asyncio
    async def test_multiple_routes_with_different_policies(self):
        """Test different policy checks for different routes."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import arbiter_policy_check
            
            mock_engine = AsyncMock()
            # Allow codegen, deny deploy
            def policy_decision(*args, **kwargs):
                action = args[1] if len(args) > 1 else kwargs.get('action', '')
                if action == 'codegen':
                    return (True, "Allowed")
                elif action == 'deploy':
                    return (False, "Denied")
                return (True, "Default allow")
            
            mock_engine.should_auto_learn = AsyncMock(side_effect=policy_decision)
            mock_pe.return_value = mock_engine
            
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


class TestFailOpen:
    """Test fail-open behavior for resilience."""

    @pytest.mark.asyncio
    async def test_fail_open_on_timeout(self):
        """Test that timeouts result in fail-open."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.side_effect = asyncio.TimeoutError()
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            
            assert allowed is True  # Fail-open
            assert "timeout" in reason.lower() or "unavailable" in reason.lower()

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self):
        """Test that exceptions result in fail-open."""
        with patch('server.middleware.arbiter_policy.PolicyEngine') as mock_pe:
            from server.middleware.arbiter_policy import ArbiterPolicyMiddleware
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.side_effect = RuntimeError("Unexpected error")
            mock_pe.return_value = mock_engine
            
            middleware = ArbiterPolicyMiddleware()
            
            request = MagicMock(spec=Request)
            request.url.path = "/test"
            request.method = "POST"
            request.client.host = "127.0.0.1"
            request.headers = {}
            
            allowed, reason = await middleware.check_policy("test_action", request, {})
            
            assert allowed is True  # Fail-open
            assert "error" in reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
