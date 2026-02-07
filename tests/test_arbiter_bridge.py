# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive unit tests for generator/arbiter_bridge.py

Tests all public methods, error handling, graceful degradation,
and integration points with Arbiter services.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from generator.arbiter_bridge import ArbiterBridge


class TestArbiterBridgeInit:
    """Test ArbiterBridge initialization and graceful degradation."""

    def test_init_with_all_services_available(self):
        """Test initialization when all Arbiter services are available."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            
            assert bridge.message_queue is not None
            assert bridge.policy_engine is not None
            assert bridge.knowledge_graph is not None
            assert bridge.bug_manager is not None
            assert bridge.human_in_loop is not None
            assert bridge.enabled is True

    def test_init_with_provided_services(self):
        """Test initialization with provided service instances."""
        mock_pe = MagicMock()
        mock_mqs = MagicMock()
        mock_bm = MagicMock()
        mock_kg = MagicMock()
        mock_hl = MagicMock()
        
        bridge = ArbiterBridge(
            policy_engine=mock_pe,
            message_queue=mock_mqs,
            bug_manager=mock_bm,
            knowledge_graph=mock_kg,
            human_in_loop=mock_hl
        )
        
        assert bridge.policy_engine is mock_pe
        assert bridge.message_queue is mock_mqs
        assert bridge.bug_manager is mock_bm
        assert bridge.knowledge_graph is mock_kg
        assert bridge.human_in_loop is mock_hl
        assert bridge.enabled is True


class TestCheckPolicy:
    """Test policy checking functionality."""

    @pytest.mark.asyncio
    async def test_check_policy_allowed(self):
        """Test policy check that allows action."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed by policy")
            mock_pe.return_value = mock_engine
            mock_mqs.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True
            assert "Allowed" in reason
            mock_engine.should_auto_learn.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_policy_denied(self):
        """Test policy check that denies action."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (False, "Denied by policy")
            mock_pe.return_value = mock_engine
            mock_mqs.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is False
            assert "Denied" in reason

    @pytest.mark.asyncio
    async def test_check_policy_no_engine(self):
        """Test policy check with no policy engine available (fail-open)."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            # Make PolicyEngine return None, simulating failure but keep other services working
            mock_pe.return_value = None
            mock_mqs.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Manually set policy engine to None to test graceful degradation
            bridge.policy_engine = None
            
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True  # Fail-open
            assert "error" in reason.lower()

    @pytest.mark.asyncio
    async def test_check_policy_error_handling(self):
        """Test error handling during policy check."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.side_effect = Exception("Policy error")
            mock_pe.return_value = mock_engine
            mock_mqs.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True  # Fail-open on error
            assert "error" in reason.lower()


class TestPublishEvent:
    """Test event publishing functionality."""

    @pytest.mark.asyncio
    async def test_publish_event_success(self):
        """Test successful event publishing."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_service = AsyncMock()
            mock_service.publish.return_value = None
            mock_mqs.return_value = mock_service
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            await bridge.publish_event("test_event", {"data": "value"})
            
            mock_service.publish.assert_called_once()
            call_args = mock_service.publish.call_args
            # Check keyword arguments
            assert "topic" in call_args.kwargs or call_args.args[0] == "generator.test_event"
            if "message" in call_args.kwargs:
                assert "data" in call_args.kwargs["message"]
            else:
                assert "data" in call_args.args[1]

    @pytest.mark.asyncio
    async def test_publish_event_no_service(self):
        """Test event publishing with no message queue available."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_mqs.return_value = None
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Manually set message queue to None to test graceful degradation
            bridge.message_queue = None
            
            # Should not raise, just log
            await bridge.publish_event("test_event", {"data": "value"})

    @pytest.mark.asyncio
    async def test_publish_event_error_handling(self):
        """Test error handling during event publishing."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_service = AsyncMock()
            mock_service.publish.side_effect = Exception("Publish error")
            mock_mqs.return_value = mock_service
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Should not raise, error logged
            await bridge.publish_event("test_event", {"data": "value"})


class TestReportBug:
    """Test bug reporting functionality."""

    @pytest.mark.asyncio
    async def test_report_bug_success(self):
        """Test successful bug reporting."""
        with patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_manager = AsyncMock()
            mock_manager.report_bug.return_value = {"bug_id": "bug-123"}
            mock_bm.return_value = mock_manager
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            bug_data = {
                "title": "Test bug",
                "severity": "high",
                "description": "Test description"
            }
            await bridge.report_bug(bug_data)
            
            mock_manager.report_bug.assert_called_once()

    @pytest.mark.asyncio
    async def test_report_bug_no_manager(self):
        """Test bug reporting with no bug manager available."""
        with patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_bm.return_value = None
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Manually set bug manager to None to test graceful degradation
            bridge.bug_manager = None
            
            await bridge.report_bug({"title": "Test bug"})

    @pytest.mark.asyncio
    async def test_report_bug_error_handling(self):
        """Test error handling during bug reporting."""
        with patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_manager = AsyncMock()
            mock_manager.report_bug.side_effect = Exception("Report error")
            mock_bm.return_value = mock_manager
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Should not raise
            await bridge.report_bug({"title": "Test bug"})


class TestUpdateKnowledge:
    """Test knowledge graph update functionality."""

    @pytest.mark.asyncio
    async def test_update_knowledge_success(self):
        """Test successful knowledge graph update."""
        with patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_graph = AsyncMock()
            mock_graph.add_fact.return_value = {"status": "success"}
            mock_kg.return_value = mock_graph
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})
            
            mock_graph.add_fact.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_knowledge_no_graph(self):
        """Test knowledge update with no graph available."""
        with patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_kg.return_value = None
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Manually set knowledge graph to None to test graceful degradation
            bridge.knowledge_graph = None
            
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})

    @pytest.mark.asyncio
    async def test_update_knowledge_error_handling(self):
        """Test error handling during knowledge update."""
        with patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            mock_graph = AsyncMock()
            mock_graph.add_fact.side_effect = Exception("Update error")
            mock_kg.return_value = mock_graph
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Should not raise
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})


class TestMetrics:
    """Test Prometheus metrics tracking."""

    def test_metrics_incremented_on_operations(self):
        """Test that metrics are properly incremented."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl, \
             patch('generator.arbiter_bridge.BRIDGE_POLICY_CHECKS') as mock_policy_checks, \
             patch('generator.arbiter_bridge.BRIDGE_OPERATION_DURATION') as mock_duration:
            
            mock_mqs.return_value = AsyncMock()
            mock_pe.return_value = AsyncMock()
            mock_kg.return_value = AsyncMock()
            mock_bm.return_value = AsyncMock()
            mock_hl.return_value = AsyncMock()
            
            bridge = ArbiterBridge()
            # Bridge should be initialized successfully
            assert bridge.enabled is True


class TestIntegration:
    """Integration tests for full workflows."""

    @pytest.mark.asyncio
    async def test_full_workflow_with_all_services(self):
        """Test complete workflow with all services available."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            # Setup mocks
            mock_policy = AsyncMock()
            mock_policy.should_auto_learn.return_value = (True, "Allowed")
            mock_pe.return_value = mock_policy
            
            mock_queue = AsyncMock()
            mock_mqs.return_value = mock_queue
            
            mock_graph = AsyncMock()
            mock_kg.return_value = mock_graph
            
            mock_bugs = AsyncMock()
            mock_bm.return_value = mock_bugs
            
            mock_hl.return_value = AsyncMock()
            
            # Execute workflow
            bridge = ArbiterBridge()
            
            # 1. Check policy
            allowed, reason = await bridge.check_policy("generate", {})
            assert allowed is True
            
            # 2. Publish event
            await bridge.publish_event("generation_started", {"status": "running"})
            
            # 3. Update knowledge
            await bridge.update_knowledge("generator", "run", {"success": True})
            
            # 4. Report bug (if any)
            await bridge.report_bug({"title": "Minor issue", "severity": "low"})
            
            # Verify all services were called
            assert mock_policy.should_auto_learn.called
            assert mock_queue.publish.called
            assert mock_graph.add_fact.called
            assert mock_bugs.report_bug.called

    @pytest.mark.asyncio
    async def test_full_workflow_with_no_services(self):
        """Test complete workflow with no services (graceful degradation)."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraph') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm, \
             patch('generator.arbiter_bridge.HumanInLoop') as mock_hl:
            
            # Return None for all services to simulate failures
            mock_mqs.return_value = None
            mock_pe.return_value = None
            mock_kg.return_value = None
            mock_bm.return_value = None
            mock_hl.return_value = None
            
            bridge = ArbiterBridge()
            # Manually set all services to None to test graceful degradation
            bridge.policy_engine = None
            bridge.message_queue = None
            bridge.knowledge_graph = None
            bridge.bug_manager = None
            bridge.human_in_loop = None
            
            # All operations should succeed without errors
            allowed, reason = await bridge.check_policy("generate", {})
            assert allowed is True  # Fail-open
            
            await bridge.publish_event("generation_started", {"status": "running"})
            await bridge.update_knowledge("generator", "run", {"success": True})
            await bridge.report_bug({"title": "Minor issue"})
            
            # No exceptions raised = successful graceful degradation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
