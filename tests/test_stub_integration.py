# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for the new stub implementations.

Tests that all new functionality (persistence, security, BasicFuzzyParser) 
is properly integrated and working together in the arbiter system.
"""

import os
import pytest
import tempfile
from pathlib import Path


class TestStubPersistence:
    """Test that stubs persist data correctly."""
    
    @pytest.mark.asyncio
    async def test_knowledge_graph_persistence(self):
        """Test KnowledgeGraph persists across instances."""
        from self_fixing_engineer.arbiter.stubs import KnowledgeGraphStub
        
        # Use a temp directory for this test
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("STUB_STORAGE_DIR", tmpdir)
                
                # Create first instance and add data
                kg1 = KnowledgeGraphStub()
                result = await kg1.add_fact("test_domain", "test_key", {"value": "test_data"})
                assert result["status"] == "success"
                
                # Create second instance - should load persisted data
                kg2 = KnowledgeGraphStub()
                facts = await kg2.find_related_facts("test_domain", "test_key", "test_data")
                
                # Should find the persisted fact
                assert len(facts) >= 1
                assert any(f.get("value") == "test_data" for f in facts)
    
    @pytest.mark.asyncio
    async def test_bug_manager_persistence(self):
        """Test BugManager persists bugs across instances."""
        from self_fixing_engineer.arbiter.stubs import BugManagerStub
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("STUB_STORAGE_DIR", tmpdir)
                
                # Create bug in first instance
                bm1 = BugManagerStub()
                bug_id = await bm1.report_bug({
                    "title": "Test Bug",
                    "severity": "high"
                })
                assert bug_id is not None
                assert bug_id.startswith("bug_")
                
                # Create second instance - should load persisted bugs
                bm2 = BugManagerStub()
                bug = await bm2.get_bug(bug_id)
                
                # Should find the persisted bug
                assert bug is not None
                assert bug["title"] == "Test Bug"
    
    @pytest.mark.asyncio
    async def test_feedback_manager_persistence(self):
        """Test FeedbackManager persists feedback across instances."""
        from self_fixing_engineer.arbiter.stubs import FeedbackManagerStub
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("STUB_STORAGE_DIR", tmpdir)
                
                # Record feedback in first instance
                fm1 = FeedbackManagerStub()
                result = await fm1.record_feedback(
                    "test_component",
                    "test_type",
                    {"rating": 5}
                )
                assert result is True
                
                # Verify file exists and contains data
                feedback_file = Path(fm1._feedback_file)
                assert feedback_file.exists()
                
                # Read and verify content
                with open(feedback_file) as f:
                    data = json.load(f)
                    assert len(data) >= 1


class TestStubSecurity:
    """Test security-first behavior of stubs."""
    
    @pytest.mark.asyncio
    async def test_policy_engine_denies_by_default(self):
        """Test PolicyEngine denies by default without override."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        engine = PolicyEngineStub()
        allowed, reason = await engine.should_auto_learn(
            "TestModule", "test_action", "test_entity", {}
        )
        
        assert allowed is False
        assert "denied" in reason.lower() or "security" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_human_in_loop_denies_by_default(self):
        """Test HumanInLoop denies by default without override."""
        from self_fixing_engineer.arbiter.stubs import HumanInLoopStub
        
        hitl = HumanInLoopStub()
        result = await hitl.request_approval(
            action="deployment",
            context={"environment": "production"},
            timeout=300
        )
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_functionality(self):
        """Test circuit breaker trips after threshold."""
        from self_fixing_engineer.arbiter.stubs import PolicyEngineStub
        
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("CIRCUIT_BREAKER_THRESHOLD", "5")
            
            engine = PolicyEngineStub()
            
            # Call multiple times
            for i in range(10):
                is_open, reason = await engine.check_circuit_breaker("test_service")
                
                # First call is 1, so after 4 calls (indices 0-3) circuit is closed
                # On 5th call (index 4), it reaches threshold and opens
                if i < 4:
                    assert is_open is False, f"Circuit should be closed at call {i+1}"
                else:
                    assert is_open is True, f"Circuit should be open at call {i+1}"


class TestMessageQueueIntegration:
    """Test MessageQueue in-memory implementation."""
    
    @pytest.mark.asyncio
    async def test_publish_subscribe_integration(self):
        """Test that publish delivers to subscribers."""
        from self_fixing_engineer.arbiter.stubs import MessageQueueServiceStub
        
        mqs = MessageQueueServiceStub()
        received_messages = []
        
        async def handler(message):
            received_messages.append(message)
        
        # Subscribe to topic
        await mqs.subscribe("test_topic", handler)
        
        # Publish message
        await mqs.publish("test_topic", {"data": "test_value"})
        
        # Should have received the message
        assert len(received_messages) == 1
        assert received_messages[0]["data"] == "test_value"
    
    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        """Test that multiple subscribers all receive messages."""
        from self_fixing_engineer.arbiter.stubs import MessageQueueServiceStub
        
        mqs = MessageQueueServiceStub()
        received1 = []
        received2 = []
        
        async def handler1(message):
            received1.append(message)
        
        async def handler2(message):
            received2.append(message)
        
        # Subscribe both handlers
        await mqs.subscribe("test_topic", handler1)
        await mqs.subscribe("test_topic", handler2)
        
        # Publish message
        await mqs.publish("test_topic", {"data": "broadcast"})
        
        # Both should have received
        assert len(received1) == 1
        assert len(received2) == 1


class TestBasicFuzzyParser:
    """Test BasicFuzzyParser implementation."""
    
    @pytest.mark.asyncio
    async def test_basic_parser_imports(self):
        """Test that BasicFuzzyParser is properly exported."""
        from self_fixing_engineer.arbiter.learner import BasicFuzzyParser
        
        parser = BasicFuzzyParser()
        assert parser is not None
    
    @pytest.mark.asyncio
    async def test_parse_dates(self):
        """Test parsing dates from text."""
        from self_fixing_engineer.arbiter.learner import BasicFuzzyParser
        
        parser = BasicFuzzyParser()
        text = "The issue was reported on 2025-01-15 and fixed on 2025-01-20."
        
        facts = await parser.parse(text, {})
        
        # Should extract dates
        date_facts = [f for f in facts if f["type"] == "date"]
        assert len(date_facts) >= 2
    
    @pytest.mark.asyncio
    async def test_parse_key_value_pairs(self):
        """Test parsing key-value pairs from text."""
        from self_fixing_engineer.arbiter.learner import BasicFuzzyParser
        
        parser = BasicFuzzyParser()
        text = "status: completed, priority: high, assignee: john_doe"
        
        facts = await parser.parse(text, {})
        
        # Should extract key-value pairs
        kv_facts = [f for f in facts if f["type"] == "key_value"]
        assert len(kv_facts) >= 3
        
        # Check specific values
        status_fact = next((f for f in kv_facts if f.get("key") == "status"), None)
        assert status_fact is not None
        assert "completed" in status_fact["value"]
    
    @pytest.mark.asyncio
    async def test_parse_numbers(self):
        """Test parsing numbers from text."""
        from self_fixing_engineer.arbiter.learner import BasicFuzzyParser
        
        parser = BasicFuzzyParser()
        text = "The system processed 1234 requests with 99.9 percent uptime."
        
        facts = await parser.parse(text, {})
        
        # Should extract numbers
        number_facts = [f for f in facts if f["type"] == "number"]
        assert len(number_facts) >= 2


class TestAuditLogPersistence:
    """Test DummyAuditLog file persistence."""
    
    @pytest.mark.asyncio
    async def test_audit_log_writes_to_file(self):
        """Test that audit events are persisted to file."""
        from self_fixing_engineer.arbiter.learner.core import DummyAuditLog
        
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("DUMMY_AUDIT_FILE", str(audit_file))
                
                # Create audit log and log event
                audit = DummyAuditLog()
                await audit.log_event(
                    "test_component",
                    "test_event",
                    {"action": "test_action"},
                    "test_user"
                )
                
                # Verify file was created and contains data
                assert audit_file.exists()
                content = audit_file.read_text()
                assert len(content) > 0
                assert "test_component" in content
                assert "test_event" in content


class TestStubImportIntegration:
    """Test that all stubs can be imported from main arbiter package."""
    
    def test_import_all_stubs_from_arbiter(self):
        """Test importing all stub classes from arbiter package."""
        from self_fixing_engineer.arbiter import (
            ArbiterStub,
            PolicyEngineStub,
            BugManagerStub,
            KnowledgeGraphStub,
            HumanInLoopStub,
            MessageQueueServiceStub,
            FeedbackManagerStub,
            ArbiterArenaStub,
            KnowledgeLoaderStub,
        )
        
        # All should be importable
        assert ArbiterStub is not None
        assert PolicyEngineStub is not None
        assert BugManagerStub is not None
        assert KnowledgeGraphStub is not None
        assert HumanInLoopStub is not None
        assert MessageQueueServiceStub is not None
        assert FeedbackManagerStub is not None
        assert ArbiterArenaStub is not None
        assert KnowledgeLoaderStub is not None
    
    def test_import_stubs_module(self):
        """Test importing the stubs module from arbiter."""
        from self_fixing_engineer.arbiter import stubs
        
        assert stubs is not None
        assert hasattr(stubs, "PolicyEngineStub")
        assert hasattr(stubs, "HumanInLoopStub")
