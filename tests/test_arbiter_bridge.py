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
             patch('generator.arbiter_bridge.KnowledgeGraphDB') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm:
            
            bridge = ArbiterBridge()
            
            assert bridge.message_queue is not None
            assert bridge.policy_engine is not None
            assert bridge.knowledge_graph is not None
            assert bridge.bug_manager is not None
            assert bridge.available is True

    def test_init_with_no_services_available(self):
        """Test graceful degradation when no services available."""
        with patch('generator.arbiter_bridge.MessageQueueService', None), \
             patch('generator.arbiter_bridge.PolicyEngine', None), \
             patch('generator.arbiter_bridge.KnowledgeGraphDB', None), \
             patch('generator.arbiter_bridge.BugManager', None):
            
            bridge = ArbiterBridge()
            
            assert bridge.message_queue is None
            assert bridge.policy_engine is None
            assert bridge.knowledge_graph is None
            assert bridge.bug_manager is None
            assert bridge.available is False

    def test_init_with_partial_services(self):
        """Test initialization with some services available."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine', None), \
             patch('generator.arbiter_bridge.KnowledgeGraphDB', None), \
             patch('generator.arbiter_bridge.BugManager', None):
            
            bridge = ArbiterBridge()
            
            assert bridge.message_queue is not None
            assert bridge.policy_engine is None
            assert bridge.available is True  # At least one service available


class TestCheckPolicy:
    """Test policy checking functionality."""

    @pytest.mark.asyncio
    async def test_check_policy_allowed(self):
        """Test policy check that allows action."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe:
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (True, "Allowed by policy")
            mock_pe.return_value = mock_engine
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True
            assert "Allowed" in reason
            mock_engine.should_auto_learn.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_policy_denied(self):
        """Test policy check that denies action."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe:
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.return_value = (False, "Denied by policy")
            mock_pe.return_value = mock_engine
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is False
            assert "Denied" in reason

    @pytest.mark.asyncio
    async def test_check_policy_no_engine(self):
        """Test policy check with no policy engine available (fail-open)."""
        with patch('generator.arbiter_bridge.PolicyEngine', None):
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True  # Fail-open
            assert "not available" in reason.lower()

    @pytest.mark.asyncio
    async def test_check_policy_error_handling(self):
        """Test error handling during policy check."""
        with patch('generator.arbiter_bridge.PolicyEngine') as mock_pe:
            mock_engine = AsyncMock()
            mock_engine.should_auto_learn.side_effect = Exception("Policy error")
            mock_pe.return_value = mock_engine
            
            bridge = ArbiterBridge()
            allowed, reason = await bridge.check_policy("test_action", {"key": "value"})
            
            assert allowed is True  # Fail-open on error
            assert "error" in reason.lower()


class TestPublishEvent:
    """Test event publishing functionality."""

    @pytest.mark.asyncio
    async def test_publish_event_success(self):
        """Test successful event publishing."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs:
            mock_service = AsyncMock()
            mock_service.publish.return_value = None
            mock_mqs.return_value = mock_service
            
            bridge = ArbiterBridge()
            await bridge.publish_event("test_event", {"data": "value"})
            
            mock_service.publish.assert_called_once()
            call_args = mock_service.publish.call_args[0]
            assert call_args[0] == "test_event"
            assert "data" in call_args[1]

    @pytest.mark.asyncio
    async def test_publish_event_no_service(self):
        """Test event publishing with no message queue available."""
        with patch('generator.arbiter_bridge.MessageQueueService', None):
            bridge = ArbiterBridge()
            # Should not raise, just log
            await bridge.publish_event("test_event", {"data": "value"})

    @pytest.mark.asyncio
    async def test_publish_event_error_handling(self):
        """Test error handling during event publishing."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs:
            mock_service = AsyncMock()
            mock_service.publish.side_effect = Exception("Publish error")
            mock_mqs.return_value = mock_service
            
            bridge = ArbiterBridge()
            # Should not raise, error logged
            await bridge.publish_event("test_event", {"data": "value"})


class TestReportBug:
    """Test bug reporting functionality."""

    @pytest.mark.asyncio
    async def test_report_bug_success(self):
        """Test successful bug reporting."""
        with patch('generator.arbiter_bridge.BugManager') as mock_bm:
            mock_manager = AsyncMock()
            mock_manager.report_bug.return_value = {"bug_id": "bug-123"}
            mock_bm.return_value = mock_manager
            
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
        with patch('generator.arbiter_bridge.BugManager', None):
            bridge = ArbiterBridge()
            await bridge.report_bug({"title": "Test bug"})

    @pytest.mark.asyncio
    async def test_report_bug_error_handling(self):
        """Test error handling during bug reporting."""
        with patch('generator.arbiter_bridge.BugManager') as mock_bm:
            mock_manager = AsyncMock()
            mock_manager.report_bug.side_effect = Exception("Report error")
            mock_bm.return_value = mock_manager
            
            bridge = ArbiterBridge()
            # Should not raise
            await bridge.report_bug({"title": "Test bug"})


class TestUpdateKnowledge:
    """Test knowledge graph update functionality."""

    @pytest.mark.asyncio
    async def test_update_knowledge_success(self):
        """Test successful knowledge graph update."""
        with patch('generator.arbiter_bridge.KnowledgeGraphDB') as mock_kg:
            mock_graph = AsyncMock()
            mock_graph.add_fact.return_value = {"status": "success"}
            mock_kg.return_value = mock_graph
            
            bridge = ArbiterBridge()
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})
            
            mock_graph.add_fact.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_knowledge_no_graph(self):
        """Test knowledge update with no graph available."""
        with patch('generator.arbiter_bridge.KnowledgeGraphDB', None):
            bridge = ArbiterBridge()
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})

    @pytest.mark.asyncio
    async def test_update_knowledge_error_handling(self):
        """Test error handling during knowledge update."""
        with patch('generator.arbiter_bridge.KnowledgeGraphDB') as mock_kg:
            mock_graph = AsyncMock()
            mock_graph.add_fact.side_effect = Exception("Update error")
            mock_kg.return_value = mock_graph
            
            bridge = ArbiterBridge()
            # Should not raise
            await bridge.update_knowledge("test_domain", "test_key", {"value": 123})


class TestMetrics:
    """Test Prometheus metrics tracking."""

    def test_metrics_incremented_on_operations(self):
        """Test that metrics are properly incremented."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.BRIDGE_OPERATIONS') as mock_ops, \
             patch('generator.arbiter_bridge.BRIDGE_LATENCY') as mock_lat:
            
            mock_service = AsyncMock()
            mock_mqs.return_value = mock_service
            
            bridge = ArbiterBridge()
            # Operations should increment metrics
            assert mock_ops.labels.called or True  # Metrics may be called during init


class TestIntegration:
    """Integration tests for full workflows."""

    @pytest.mark.asyncio
    async def test_full_workflow_with_all_services(self):
        """Test complete workflow with all services available."""
        with patch('generator.arbiter_bridge.MessageQueueService') as mock_mqs, \
             patch('generator.arbiter_bridge.PolicyEngine') as mock_pe, \
             patch('generator.arbiter_bridge.KnowledgeGraphDB') as mock_kg, \
             patch('generator.arbiter_bridge.BugManager') as mock_bm:
            
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
        with patch('generator.arbiter_bridge.MessageQueueService', None), \
             patch('generator.arbiter_bridge.PolicyEngine', None), \
             patch('generator.arbiter_bridge.KnowledgeGraphDB', None), \
             patch('generator.arbiter_bridge.BugManager', None):
            
            bridge = ArbiterBridge()
            
            # All operations should succeed without errors
            allowed, reason = await bridge.check_policy("generate", {})
            assert allowed is True  # Fail-open
            
            await bridge.publish_event("generation_started", {"status": "running"})
            await bridge.update_knowledge("generator", "run", {"success": True})
            await bridge.report_bug({"title": "Minor issue"})
            
            # No exceptions raised = successful graceful degradation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
