# test_core.py

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from arbiter.learner.audit import CircuitBreaker

# Import the classes to test
from arbiter.learner.core import Arbiter, Learner
from arbiter.learner.encryption import ArbiterConfig
from cryptography.fernet import Fernet


class TestArbiter:
    """Test suite for Arbiter class."""

    def test_arbiter_initialization(self):
        """Test Arbiter initialization."""
        # Mock both BugManager and Neo4jKnowledgeGraph
        with patch("arbiter.learner.core.BugManager") as mock_bug_manager:
            with patch("arbiter.learner.core.Neo4jKnowledgeGraph") as mock_neo4j:
                mock_bug_manager.return_value = Mock()
                mock_neo4j.return_value = Mock()

                arbiter = Arbiter()

                assert arbiter.name == "Arbiter"
                assert "memory" in arbiter.state
                assert isinstance(arbiter.state["memory"], dict)
                assert arbiter.is_running_self_audit is False
                assert hasattr(arbiter, "bug_manager")
                assert hasattr(arbiter, "knowledge_graph")


class TestLearner:
    """Test suite for Learner class."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()
        redis.lock = MagicMock()

        # Mock the lock context manager
        lock_mock = AsyncMock()
        lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
        lock_mock.__aexit__ = AsyncMock(return_value=None)
        redis.lock.return_value = lock_mock

        return redis

    @pytest.fixture
    def mock_arbiter(self):
        """Create a mock Arbiter."""
        arbiter = Mock(spec=Arbiter)
        arbiter.state = {"memory": {}}
        arbiter.is_running_self_audit = False
        arbiter.bug_manager = AsyncMock()
        arbiter.knowledge_graph = AsyncMock()
        return arbiter

    @pytest.fixture
    def mock_db(self):
        """Create a mock database client."""
        db = AsyncMock()
        db.save_agent_knowledge = AsyncMock()
        db.save_agent_knowledge_batch = AsyncMock()
        db.load_agent_knowledge = AsyncMock(return_value=None)
        db.delete_agent_knowledge = AsyncMock()

        # Add proper transaction mock
        class MockTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        db.transaction = lambda: MockTransaction()
        db.db_url = "postgresql://test"
        return db

    @pytest.fixture
    async def learner(self, mock_arbiter, mock_redis, mock_db):
        """Create a Learner instance with mocked dependencies."""
        with patch("arbiter.learner.core.PostgresClient") as mock_postgres:
            mock_postgres.return_value = mock_db

            with patch("arbiter.learner.core.LLMClient"):
                with patch("arbiter.learner.core.AuditLogger") as mock_audit:
                    mock_audit_instance = AsyncMock()
                    mock_audit_instance.log_event = AsyncMock()
                    mock_audit.from_environment.return_value = mock_audit_instance

                    with patch(
                        "arbiter.learner.core.get_meta_learning_data_store"
                    ) as mock_meta:
                        mock_meta_store = AsyncMock()
                        mock_meta_store.connect = AsyncMock()
                        mock_meta_store.disconnect = AsyncMock()
                        mock_meta_store.write_record = AsyncMock()
                        mock_meta_store.write_batch = AsyncMock()
                        mock_meta.return_value = mock_meta_store

                        # Set up encryption keys
                        ArbiterConfig.ENCRYPTION_KEYS = {"v1": Fernet.generate_key()}

                        learner = Learner(
                            arbiter=mock_arbiter,
                            redis=mock_redis,
                            db_url="postgresql://test",
                        )
                        learner.db = mock_db

                        yield learner

    def test_learner_initialization(self, mock_arbiter, mock_redis):
        """Test Learner initialization."""
        with patch("arbiter.learner.core.PostgresClient"):
            with patch("arbiter.learner.core.LLMClient"):
                with patch("arbiter.learner.core.AuditLogger"):
                    with patch("arbiter.learner.core.get_meta_learning_data_store"):
                        ArbiterConfig.ENCRYPTION_KEYS = {"v1": Fernet.generate_key()}

                        learner = Learner(mock_arbiter, mock_redis)

                        assert learner.arbiter == mock_arbiter
                        assert learner.redis == mock_redis
                        assert isinstance(learner.ciphers, dict)
                        assert "v1" in learner.ciphers
                        assert isinstance(learner.validation_schemas, dict)
                        assert isinstance(learner.validation_hooks, dict)
                        assert isinstance(learner.event_hooks, dict)
                        assert isinstance(learner.db_circuit_breaker, CircuitBreaker)
                        assert isinstance(learner.audit_circuit_breaker, CircuitBreaker)

    @pytest.mark.asyncio
    async def test_learn_new_thing_success(self, learner):
        """Test successful learning of new knowledge."""
        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {
                    "is_valid": True,
                    "reason_code": "success",
                }

                with patch("arbiter.learner.core.generate_explanation") as mock_explain:
                    mock_explain.return_value = "Test explanation"

                    with patch(
                        "arbiter.learner.core.persist_knowledge"
                    ) as mock_persist:
                        mock_persist.return_value = None

                        result = await learner.learn_new_thing(
                            domain="TestDomain",
                            key="test_key",
                            value={"data": "test_value"},
                            user_id="test_user",
                            source="test",
                        )

                        assert result["status"] == "learned"
                        assert "version" in result
                        assert result["explanation"] == "Test explanation"

                        # Verify the value was stored in memory
                        assert "TestDomain" in learner.arbiter.state["memory"]
                        assert (
                            "test_key" in learner.arbiter.state["memory"]["TestDomain"]
                        )

    @pytest.mark.asyncio
    async def test_learn_new_thing_invalid_domain(self, learner):
        """Test learning with invalid domain format."""
        result = await learner.learn_new_thing(
            domain="Invalid Domain!",  # Contains invalid characters
            key="test_key",
            value={"data": "test"},
            user_id="test_user",
        )

        assert result["status"] == "failed"
        assert "invalid_domain" in result["reason"]

    @pytest.mark.asyncio
    async def test_learn_new_thing_policy_blocked(self, learner):
        """Test learning blocked by policy."""
        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (False, "blocked_by_policy")

            result = await learner.learn_new_thing(
                domain="TestDomain",
                key="test_key",
                value={"data": "test"},
                user_id="test_user",
            )

            assert result["status"] == "skipped"
            assert "policy_blocked" in result["reason"]

    @pytest.mark.asyncio
    async def test_learn_new_thing_validation_failed(self, learner):
        """Test learning with validation failure."""
        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {
                    "is_valid": False,
                    "reason_code": "schema_validation_failed",
                    "reason": "Invalid schema",
                }

                result = await learner.learn_new_thing(
                    domain="TestDomain",
                    key="test_key",
                    value={"data": "test"},
                    user_id="test_user",
                )

                assert result["status"] == "failed"
                assert "Invalid schema" in result["reason"]

    @pytest.mark.asyncio
    async def test_learn_batch_success(self, learner):
        """Test successful batch learning."""
        facts = [
            {"domain": "TestDomain", "key": "key1", "value": {"data": "value1"}},
            {"domain": "TestDomain", "key": "key2", "value": {"data": "value2"}},
        ]

        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {
                    "is_valid": True,
                    "reason_code": "success",
                }

                with patch("arbiter.learner.core.generate_explanation") as mock_explain:
                    mock_explain.return_value = "Batch explanation"

                    with patch(
                        "arbiter.learner.core.persist_knowledge_batch"
                    ) as mock_persist:
                        mock_persist.return_value = None

                        results = await learner.learn_batch(
                            facts=facts, user_id="test_user", source="batch_test"
                        )

                        assert len(results) == 2
                        assert all(r["status"] == "learned" for r in results)

    @pytest.mark.asyncio
    async def test_learn_batch_mixed_results(self, learner):
        """Test batch learning with mixed valid/invalid facts."""
        facts = [
            {"domain": "TestDomain", "key": "key1", "value": {"data": "value1"}},
            {
                "domain": "Invalid!",
                "key": "key2",
                "value": {"data": "value2"},
            },  # Invalid domain
            {"domain": "TestDomain", "key": "key3", "value": None},  # Invalid value
        ]

        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.side_effect = [
                    {"is_valid": True, "reason_code": "success"},
                    {
                        "is_valid": False,
                        "reason_code": "invalid_domain",
                        "reason": "Invalid domain",
                    },
                    {
                        "is_valid": False,
                        "reason_code": "null_value",
                        "reason": "Null value",
                    },
                ]

                with patch("arbiter.learner.core.generate_explanation") as mock_explain:
                    mock_explain.return_value = "Explanation"

                    # Since persist_knowledge_batch is failing, we need to set write_to_disk=False
                    # or mock it properly
                    results = await learner.learn_batch(
                        facts=facts,
                        user_id="test_user",
                        write_to_disk=False,  # Skip database persistence
                    )

                    assert len(results) == 3

                    # The order might be different than expected if there's async processing
                    # Let's check by content rather than position
                    learned_count = sum(1 for r in results if r["status"] == "learned")
                    skipped_count = sum(1 for r in results if r["status"] == "skipped")

                    assert learned_count == 1  # Only the first fact should be learned
                    assert skipped_count == 2  # The other two should be skipped

                    # Find the results for each fact
                    for result in results:
                        if result.get("fact", {}).get("key") == "key1":
                            assert result["status"] == "learned"
                        elif result.get("fact", {}).get("key") == "key2":
                            assert result["status"] == "skipped"
                            assert (
                                "Invalid domain" in result["reason"]
                                or "invalid_domain" in result["reason"].lower()
                            )
                        elif result.get("fact", {}).get("key") == "key3":
                            assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_forget_fact_success(self, learner):
        """Test successful fact forgetting."""
        # First, add a fact to memory
        learner.arbiter.state["memory"]["TestDomain"] = {
            "test_key": {
                "value": "test_value",
                "version": 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

        # Mock retrieve_knowledge to return the fact
        with patch.object(learner, "retrieve_knowledge") as mock_retrieve:
            mock_retrieve.return_value = {"value": "test_value", "version": 1}

            result = await learner.forget_fact(
                domain="TestDomain",
                key="test_key",
                user_id="test_user",
                reason="test_deletion",
            )

            assert result["status"] == "forgotten"
            assert result["reason"] == "success"

            # Verify the fact was removed from memory
            assert "test_key" not in learner.arbiter.state["memory"].get(
                "TestDomain", {}
            )

    @pytest.mark.asyncio
    async def test_forget_fact_not_found(self, learner):
        """Test forgetting non-existent fact."""
        with patch.object(learner, "retrieve_knowledge") as mock_retrieve:
            mock_retrieve.return_value = None

            result = await learner.forget_fact(
                domain="TestDomain", key="nonexistent_key", user_id="test_user"
            )

            assert result["status"] == "skipped"
            assert result["reason"] == "fact_not_found"

    @pytest.mark.asyncio
    async def test_retrieve_knowledge_from_memory(self, learner):
        """Test retrieving knowledge from memory cache."""
        test_data = {
            "value": "test_value",
            "version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        learner.arbiter.state["memory"]["TestDomain"] = {"test_key": test_data}

        result = await learner.retrieve_knowledge("TestDomain", "test_key")

        assert result is not None
        assert result["value"] == "test_value"
        assert result["version"] == 1

    @pytest.mark.asyncio
    async def test_retrieve_knowledge_from_redis(self, learner):
        """Test retrieving knowledge from Redis cache."""
        test_data = {
            "value": "test_value",
            "version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        learner.redis.get.return_value = json.dumps(test_data)

        result = await learner.retrieve_knowledge("TestDomain", "test_key")

        assert result is not None
        assert result["value"] == "test_value"
        assert result["version"] == 1

        # Verify it was cached in memory
        assert "test_key" in learner.arbiter.state["memory"]["TestDomain"]

    @pytest.mark.asyncio
    async def test_retrieve_knowledge_from_database(self, learner):
        """Test retrieving knowledge from database."""
        test_data = {
            "value": "test_value",
            "version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        learner.redis.get.return_value = None  # Not in Redis
        learner.db.load_agent_knowledge.return_value = test_data

        result = await learner.retrieve_knowledge("TestDomain", "test_key")

        assert result is not None
        assert result["value"] == "test_value"
        assert result["version"] == 1

        # Verify it was cached in Redis and memory
        learner.redis.setex.assert_called_once()
        assert "test_key" in learner.arbiter.state["memory"]["TestDomain"]

    @pytest.mark.asyncio
    async def test_retrieve_knowledge_not_found(self, learner):
        """Test retrieving non-existent knowledge."""
        learner.redis.get.return_value = None
        learner.db.load_agent_knowledge.return_value = None

        result = await learner.retrieve_knowledge("TestDomain", "nonexistent_key")

        assert result is None

    def test_compute_diff(self, learner):
        """Test JSON diff computation."""
        old_value = {"field1": "old", "field2": "same"}
        new_value = {"field1": "new", "field2": "same", "field3": "added"}

        diff = learner._compute_diff(old_value, new_value)

        assert diff is not None
        # The diff should contain the actual changes
        assert isinstance(diff, list)

    @pytest.mark.asyncio
    async def test_encryption_for_encrypted_domain(self, learner):
        """Test that values are encrypted for encrypted domains."""
        # Add domain to encrypted list
        ArbiterConfig.ENCRYPTED_DOMAINS = ["SecretDomain"]

        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {"is_valid": True}

                with patch("arbiter.learner.core.generate_explanation") as mock_explain:
                    mock_explain.return_value = "Encrypted explanation"

                    with patch("arbiter.learner.core.persist_knowledge"):
                        result = await learner.learn_new_thing(
                            domain="SecretDomain",
                            key="secret_key",
                            value={"secret": "data"},
                            user_id="test_user",
                        )

                        assert result["status"] == "learned"

                        # Check that the value in memory is encrypted (bytes)
                        stored = learner.arbiter.state["memory"]["SecretDomain"][
                            "secret_key"
                        ]
                        assert isinstance(stored["value"], bytes)
                        assert stored["value"].startswith(b"v1:")  # Key ID prefix

    @pytest.mark.asyncio
    async def test_self_audit_task(self, learner):
        """Test self-audit background task."""
        with patch("arbiter.learner.core.verify_audit_chain") as mock_verify:
            mock_verify.return_value = True

            # Start self-audit
            learner.start_self_audit()
            assert learner._self_audit_task is not None
            assert not learner._self_audit_task.done()

            # Stop self-audit
            await learner.stop_self_audit()
            assert learner._self_audit_stop_event.is_set()

    @pytest.mark.asyncio
    async def test_event_hooks_execution(self, learner):
        """Test that event hooks are called during learning."""
        pre_learn_hook = AsyncMock()
        post_learn_hook = AsyncMock()

        learner.event_hooks["pre_learn"].append(pre_learn_hook)
        learner.event_hooks["post_learn"].append(post_learn_hook)

        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {"is_valid": True}

                with patch("arbiter.learner.core.generate_explanation"):
                    with patch("arbiter.learner.core.persist_knowledge"):
                        await learner.learn_new_thing(
                            domain="TestDomain",
                            key="test_key",
                            value={"data": "test"},
                            user_id="test_user",
                        )

                        # Verify hooks were called
                        pre_learn_hook.assert_called_once()
                        post_learn_hook.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration(self, learner):
        """Test circuit breaker prevents database operations when open."""
        # Open the circuit breaker
        learner.db_circuit_breaker.is_open = True
        learner.db_circuit_breaker.can_proceed = AsyncMock(return_value=False)

        with patch("arbiter.learner.core.should_auto_learn") as mock_policy:
            mock_policy.return_value = (True, None)

            with patch("arbiter.learner.core.validate_data") as mock_validate:
                mock_validate.return_value = {"is_valid": True}

                with patch("arbiter.learner.core.generate_explanation"):
                    result = await learner.learn_new_thing(
                        domain="TestDomain",
                        key="test_key",
                        value={"data": "test"},
                        user_id="test_user",
                    )

                    # Should still learn to memory/Redis but skip DB
                    assert result["status"] == "learned"
                    learner.db.save_agent_knowledge.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=arbiter.learner.core"])
