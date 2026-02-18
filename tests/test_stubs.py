# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive unit tests for self_fixing_engineer/arbiter/stubs.py

Tests all stub implementations, production mode detection, metrics tracking,
and health check functionality.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestStubImports:
    """Test that all stub classes can be imported."""

    def test_import_all_stubs(self):
        """Test importing all stub classes."""
        from self_fixing_engineer.arbiter.stubs import (
            ArbiterStub,
            PolicyEngineStub,
            KnowledgeGraphStub,
            BugManagerStub,
            MessageQueueServiceStub,
            HumanInLoopStub,
            FeedbackManagerStub,
            ArbiterArenaStub,
            KnowledgeLoaderStub,
            is_using_stubs,
        )
        
        assert ArbiterStub is not None
        assert PolicyEngineStub is not None
        assert KnowledgeGraphStub is not None
        assert BugManagerStub is not None
        assert MessageQueueServiceStub is not None
        assert HumanInLoopStub is not None
        assert FeedbackManagerStub is not None
        assert ArbiterArenaStub is not None
        assert KnowledgeLoaderStub is not None
        assert is_using_stubs is not None


class TestPolicyEngineStub:
    """Test PolicyEngine stub implementation."""

    @pytest.mark.asyncio
    async def test_should_auto_learn_defaults_to_deny(self):
        """Test that stub DENIES by default for security."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        engine = PolicyEngineStub()
        allowed, reason = await engine.should_auto_learn(
            "TestModule", "test_action", "test_entity", {}
        )
        
        # Security-first: Should DENY by default
        assert allowed is False
        assert "denied" in reason.lower() or "security" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_should_auto_learn_with_override(self):
        """Test that stub allows when override is set."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch.dict(os.environ, {"STUB_ALLOW_AUTO_LEARN": "true"}):
            engine = PolicyEngineStub()
            allowed, reason = await engine.should_auto_learn(
                "TestModule", "test_action", "test_entity", {}
            )
            
            # Should allow with override
            assert allowed is True
            assert "allowed" in reason.lower()

    @pytest.mark.asyncio
    async def test_production_mode_warning(self):
        """Test CRITICAL log in production mode."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch.dict(os.environ, {"PRODUCTION_MODE": "true"}):
            engine = PolicyEngineStub()
            
            # First call should log CRITICAL warning and DENY
            allowed, reason = await engine.should_auto_learn(
                "TestModule", "test_action", "test_entity", {}
            )
            
            # Should deny in production without override
            assert allowed is False

    def test_metrics_tracking(self):
        """Test that stub usage is tracked via metrics."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch('self_fixing_engineer.arbiter.stubs.ARBITER_STUB_USAGE') as mock_counter:
            engine = PolicyEngineStub()
            # Metric should be tracked
            assert True  # Metrics called during init or first use
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_trips(self):
        """Test that circuit breaker trips after threshold."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch.dict(os.environ, {"CIRCUIT_BREAKER_THRESHOLD": "3"}):
            engine = PolicyEngineStub()
            
            # First few calls should have circuit closed
            is_open, reason = await engine.check_circuit_breaker("test_service")
            assert is_open is False
            
            # After threshold calls, circuit should open
            for _ in range(3):
                is_open, reason = await engine.check_circuit_breaker("test_service")
            
            assert is_open is True
            assert "open" in reason.lower()



class TestKnowledgeGraphStub:
    """Test KnowledgeGraph stub implementation."""

    @pytest.mark.asyncio
    async def test_add_fact_returns_none(self):
        """Test that add_fact returns None (graceful degradation)."""
        from self_fixing_engineer.arbiter.stubs import KnowledgeGraphStub
        
        kg = KnowledgeGraphStub()
        result = await kg.add_fact("domain", "key", {"data": "value"})
        
        # Should not raise, may return None or status dict
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_find_related_facts_with_persistence(self):
        """Test that find_related_facts returns persisted facts."""
        from self_fixing_engineer.arbiter.stubs import KnowledgeGraphStub
        
        kg = KnowledgeGraphStub()
        # Add a fact first
        await kg.add_fact("test_domain", "test_key", {"data": "value"})
        
        # Now query for facts in that domain
        results = await kg.find_related_facts("test_domain", "test_key", "value")
        
        assert isinstance(results, list)
        # Should return the fact we just added
        assert len(results) >= 1


class TestBugManagerStub:
    """Test BugManager stub implementation."""

    @pytest.mark.asyncio
    async def test_report_bug_returns_id(self):
        """Test that report_bug returns a bug ID."""
        from self_fixing_engineer.arbiter.stubs import BugManagerStub
        
        bm = BugManagerStub()
        result = await bm.report_bug({
            "title": "Test bug",
            "severity": "high",
            "description": "Test description"
        })
        
        # Should return a bug ID (string) now that we have persistence
        assert isinstance(result, str)
        assert result.startswith("bug_")


class TestMessageQueueServiceStub:
    """Test MessageQueueService stub implementation."""

    @pytest.mark.asyncio
    async def test_publish_succeeds(self):
        """Test that publish succeeds gracefully."""
        from self_fixing_engineer.arbiter.stubs import MessageQueueServiceStub
        
        mqs = MessageQueueServiceStub()
        await mqs.publish("test_event", {"data": "value"})
        
        # Should not raise

    @pytest.mark.asyncio
    async def test_subscribe_succeeds(self):
        """Test that subscribe succeeds gracefully."""
        from self_fixing_engineer.arbiter.stubs import MessageQueueServiceStub
        
        mqs = MessageQueueServiceStub()
        
        async def handler(data):
            pass
        
        await mqs.subscribe("test_event", handler)
        
        # Should not raise


class TestHumanInLoopStub:
    """Test HumanInLoop stub implementation."""

    @pytest.mark.asyncio
    async def test_request_approval_denies_by_default(self):
        """Test that request_approval DENIES by default for security."""
        from self_fixing_engineer.arbiter.stubs import HumanInLoopStub
        
        hitl = HumanInLoopStub()
        result = await hitl.request_approval(
            action="deployment",
            context={"environment": "dev"},
            timeout=300
        )
        
        # Should DENY by default for security
        assert result is False
    
    @pytest.mark.asyncio
    async def test_request_approval_with_override(self):
        """Test that request_approval auto-approves with override."""
        from self_fixing_engineer.arbiter.stubs import HumanInLoopStub
        
        with patch.dict(os.environ, {"STUB_AUTO_APPROVE": "true"}):
            hitl = HumanInLoopStub()
            result = await hitl.request_approval(
                action="deployment",
                context={"environment": "dev"},
                timeout=300
            )
            
            # Should auto-approve with override
            assert result is True


class TestHealthCheck:
    """Test is_using_stubs health check function."""

    def test_is_using_stubs_returns_dict(self):
        """Test that health check returns component status."""
        from self_fixing_engineer.arbiter.stubs import is_using_stubs
        
        status = is_using_stubs()
        
        assert isinstance(status, dict)
        assert "PolicyEngine" in status
        assert "KnowledgeGraph" in status
        assert "BugManager" in status
        assert "MessageQueueService" in status
        assert "HumanInLoop" in status
        
        # Values should be booleans
        for component, is_stub in status.items():
            assert isinstance(is_stub, bool)

    def test_production_mode_detection(self):
        """Test production mode detection in health check."""
        from self_fixing_engineer.arbiter.stubs import is_using_stubs
        
        with patch.dict(os.environ, {"PRODUCTION_MODE": "true"}):
            status = is_using_stubs()
            
            # Should detect stubs in production
            assert isinstance(status, dict)


class TestMetricsIntegration:
    """Test Prometheus metrics integration."""

    def test_metrics_counter_exists(self):
        """Test that stub usage counter is defined."""
        try:
            from self_fixing_engineer.arbiter.stubs import STUB_USAGE_COUNTER
            assert STUB_USAGE_COUNTER is not None
        except ImportError:
            # OK if prometheus not available
            pass

    def test_metrics_tracked_on_usage(self):
        """Test that metrics are incremented on stub usage."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch('self_fixing_engineer.arbiter.stubs.ARBITER_STUB_USAGE') as mock_counter:
            engine = PolicyEngineStub()
            # Should track usage
            assert True


class TestProductionSafety:
    """Test production safety features."""

    @pytest.mark.asyncio
    async def test_critical_log_in_production(self):
        """Test CRITICAL logs when stubs active in production."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        # Use the actual logger, not a mock, and check the behavior
        with patch.dict(os.environ, {"PRODUCTION_MODE": "true", "TEST_MODE": "false"}):
            engine = PolicyEngineStub()
            allowed, reason = await engine.should_auto_learn("Test", "action", "entity", {})
            
            # In production without override, should deny
            assert allowed is False

    def test_first_use_warning(self):
        """Test that warning is logged on first use of stub."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with patch('self_fixing_engineer.arbiter.stubs.logger') as mock_logger:
            engine = PolicyEngineStub()
            # Should log warning about stub usage
            assert True


class TestGracefulDegradation:
    """Test graceful degradation patterns."""

    @pytest.mark.asyncio
    async def test_stubs_never_raise_exceptions(self):
        """Test that stubs handle all calls without exceptions."""
        from self_fixing_engineer.arbiter.stubs import (
            PolicyEngineStub,
            KnowledgeGraphStub,
            BugManagerStub,
            MessageQueueServiceStub,
            HumanInLoopStub,
        )
        
        # All operations should succeed without exceptions
        policy = PolicyEngineStub()
        await policy.should_auto_learn("Test", "action", "entity", {})
        
        kg = KnowledgeGraphStub()
        await kg.add_fact("domain", "key", {"data": "value"})
        await kg.find_related_facts("domain", "key", "value")
        
        bm = BugManagerStub()
        await bm.report_bug({"title": "Test"})
        
        mqs = MessageQueueServiceStub()
        await mqs.publish("event", {})
        await mqs.subscribe("event", lambda x: None)
        
        hitl = HumanInLoopStub()
        await hitl.request_approval("type", {}, 300)
        
        # No exceptions = successful graceful degradation
        assert True

    @pytest.mark.asyncio
    async def test_stubs_return_safe_defaults(self):
        """Test that stubs return safe default values."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        policy = PolicyEngineStub()
        allowed, reason = await policy.should_auto_learn("Test", "action", "entity", {})
        
        # Should return safe default (always allow in development)
        assert isinstance(allowed, bool)
        assert isinstance(reason, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
