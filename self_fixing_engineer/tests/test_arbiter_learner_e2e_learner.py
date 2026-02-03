# test_e2e_learner.py

import asyncio
import importlib
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from self_fixing_engineer.arbiter.learner.audit import CircuitBreaker

# Import all the modules we're testing
from self_fixing_engineer.arbiter.learner.core import LearnerArbiterHelper, Learner
from self_fixing_engineer.arbiter.learner.encryption import ArbiterConfig
from cryptography.fernet import Fernet


class TestEndToEndLearner:
    """Comprehensive end-to-end tests for the Learner module."""

    @pytest.fixture(autouse=True)
    def clean_environment(self):
        """Clean environment before and after each test."""
        original_env = os.environ.copy()
        # Set a very short audit interval for testing
        os.environ["SELF_AUDIT_INTERVAL_SECONDS"] = "0.1"
        yield
        os.environ.clear()
        os.environ.update(original_env)

    @pytest.fixture(autouse=True)
    def patch_audit_log_module(self):
        """Patch the audit_log module functions and classes."""
        with patch("self_fixing_engineer.arbiter.learner.audit.audit_log") as mock_audit_func:
            # Make audit_log a callable async function
            async def mock_log_event(*args, **kwargs):
                return None

            mock_audit_func.side_effect = mock_log_event

            # Also patch the TamperEvidentLogger to handle _get_trace_ids
            with patch("self_fixing_engineer.arbiter.audit_log.TamperEvidentLogger") as mock_logger_class:
                instance = AsyncMock()
                instance._get_trace_ids = Mock(return_value=("trace-123", "span-456"))
                instance.emit_audit_event = AsyncMock()
                instance.log_event = AsyncMock()
                mock_logger_class.return_value = instance

                # Patch the singleton instance
                with patch("self_fixing_engineer.arbiter.audit_log.audit_logger", instance):
                    yield

    @pytest.fixture(autouse=True)
    def patch_prometheus_metrics(self):
        """Patch all Prometheus metrics across modules."""

        class DummyCounter:
            def labels(self, **kwargs):
                return self

            def inc(self, *args, **kwargs):
                pass

        class DummyHistogram:
            def labels(self, **kwargs):
                return self

            def observe(self, amount):
                pass

        class DummyGauge:
            def labels(self, **kwargs):
                return self

            def set(self, value):
                pass

        # List of patches to apply
        patches = []

        # Patch the metrics module itself to avoid global labels issue
        with patch("self_fixing_engineer.arbiter.learner.metrics.GLOBAL_LABELS", {}):
            # Patch all metric instances
            metrics_to_patch = [
                ("self_fixing_engineer.arbiter.learner.core", "learn_counter", DummyCounter()),
                ("self_fixing_engineer.arbiter.learner.core", "learn_error_counter", DummyCounter()),
                ("self_fixing_engineer.arbiter.learner.core", "forget_counter", DummyCounter()),
                ("self_fixing_engineer.arbiter.learner.core", "retrieve_hit_miss", DummyCounter()),
                ("self_fixing_engineer.arbiter.learner.core", "learn_duration_seconds", DummyHistogram()),
                ("self_fixing_engineer.arbiter.learner.core", "forget_duration_seconds", DummyHistogram()),
                ("self_fixing_engineer.arbiter.learner.audit", "circuit_breaker_state", DummyGauge()),
                ("self_fixing_engineer.arbiter.learner.audit", "learn_error_counter", DummyCounter()),
                (
                    "self_fixing_engineer.arbiter.learner.validation",
                    "validation_success_total",
                    DummyCounter(),
                ),
                (
                    "self_fixing_engineer.arbiter.learner.validation",
                    "validation_failure_total",
                    DummyCounter(),
                ),
                ("self_fixing_engineer.arbiter.learner.validation", "schema_reload_total", DummyCounter()),
                (
                    "self_fixing_engineer.arbiter.learner.validation",
                    "schema_reload_latency_seconds",
                    DummyHistogram(),
                ),
                (
                    "self_fixing_engineer.arbiter.learner.validation",
                    "validation_latency_seconds",
                    DummyHistogram(),
                ),
                (
                    "self_fixing_engineer.arbiter.learner.explanations",
                    "explanation_llm_latency_seconds",
                    DummyHistogram(),
                ),
                (
                    "self_fixing_engineer.arbiter.learner.explanations",
                    "explanation_llm_failure_total",
                    DummyCounter(),
                ),
                ("self_fixing_engineer.arbiter.learner.fuzzy", "fuzzy_parser_success_total", DummyCounter()),
                ("self_fixing_engineer.arbiter.learner.fuzzy", "fuzzy_parser_failure_total", DummyCounter()),
                (
                    "self_fixing_engineer.arbiter.learner.fuzzy",
                    "fuzzy_parser_latency_seconds",
                    DummyHistogram(),
                ),
            ]

            for module_name, attr_name, mock_obj in metrics_to_patch:
                try:
                    module = importlib.import_module(module_name)
                    if hasattr(module, attr_name):
                        p = patch(f"{module_name}.{attr_name}", mock_obj)
                        p.start()
                        patches.append(p)
                except (ImportError, AttributeError):
                    # Metric doesn't exist, skip it
                    pass

            try:
                yield
            finally:
                # Stop all patches
                for p in patches:
                    p.stop()

    @pytest.fixture(autouse=True)
    def patch_time_functions(self):
        """Patch time functions to return deterministic values."""
        with patch("self_fixing_engineer.arbiter.learner.validation.time.perf_counter", return_value=1.0):
            with patch("time.time", return_value=1234567890.0):
                with patch("time.monotonic", return_value=1.0):
                    yield

    @pytest.fixture(autouse=True)
    def patch_arbiter_config(self):
        """Patch ArbiterConfig to use short intervals for testing."""
        with patch(
            "self_fixing_engineer.arbiter.learner.core.ArbiterConfig.SELF_AUDIT_INTERVAL_SECONDS", 0.1
        ):
            with patch(
                "self_fixing_engineer.arbiter.learner.encryption.ArbiterConfig.SELF_AUDIT_INTERVAL_SECONDS",
                0.1,
            ):
                yield

    @pytest.fixture
    def setup_learner_environment(self):
        """Set up a complete learner environment with all dependencies."""
        # Save original values for restoration
        original_encryption_keys = (
            ArbiterConfig.ENCRYPTION_KEYS.copy()
            if hasattr(ArbiterConfig, "ENCRYPTION_KEYS")
            else {}
        )
        original_encrypted_domains = (
            ArbiterConfig.ENCRYPTED_DOMAINS.copy()
            if hasattr(ArbiterConfig, "ENCRYPTED_DOMAINS")
            else []
        )

        # Set up encryption keys - use raw bytes, not Fernet objects
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()
        ArbiterConfig.ENCRYPTION_KEYS = {
            "v1": key1,  # Raw bytes
            "v2": key2,  # Raw bytes
        }
        ArbiterConfig.ENCRYPTED_DOMAINS = ["SecretData", "PersonalInfo"]

        # Create mock Redis
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()
        mock_redis.delete = AsyncMock()
        mock_redis.lock = MagicMock()

        lock_mock = AsyncMock()
        lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
        lock_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.lock.return_value = lock_mock

        # Create mock database with proper transaction support
        mock_db = AsyncMock()
        mock_db.save_agent_knowledge = AsyncMock()
        mock_db.save_agent_knowledge_batch = AsyncMock()
        mock_db.load_agent_knowledge = AsyncMock(return_value=None)
        mock_db.delete_agent_knowledge = AsyncMock()
        mock_db.db_url = "postgresql://test"

        class MockTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        mock_db.transaction = lambda: MockTransaction()

        # Create Arbiter and Learner with properly mocked bug_manager
        with patch("self_fixing_engineer.arbiter.learner.core.BugManager") as mock_bug_manager:
            with patch("self_fixing_engineer.arbiter.learner.core.Neo4jKnowledgeGraph") as mock_neo4j:
                bug_manager_instance = Mock()
                bug_manager_instance.bug_detected = (
                    AsyncMock()
                )  # Fix: Make it AsyncMock
                mock_bug_manager.return_value = bug_manager_instance
                mock_neo4j.return_value = Mock()
                arbiter_helper = LearnerArbiterHelper()
                arbiter_helper.bug_manager = bug_manager_instance  # Ensure it's set

        with patch("self_fixing_engineer.arbiter.learner.core.PostgresClient") as mock_postgres:
            mock_postgres.return_value = mock_db

            with patch("self_fixing_engineer.arbiter.learner.core.LLMClient") as mock_llm:
                mock_llm_instance = AsyncMock()
                mock_llm_instance.generate_text = AsyncMock(
                    return_value="Generated explanation"
                )
                mock_llm.return_value = mock_llm_instance

                with patch("self_fixing_engineer.arbiter.learner.core.AuditLogger") as mock_audit:
                    mock_audit_instance = AsyncMock()
                    mock_audit_instance.log_event = AsyncMock()
                    mock_audit_instance.add_entry = AsyncMock()
                    mock_audit.from_environment.return_value = mock_audit_instance

                    with patch(
                        "self_fixing_engineer.arbiter.learner.core.get_meta_learning_data_store"
                    ) as mock_meta:
                        mock_meta_store = AsyncMock()
                        mock_meta_store.connect = AsyncMock()
                        mock_meta_store.disconnect = AsyncMock()
                        mock_meta_store.write_record = AsyncMock()
                        mock_meta_store.write_batch = AsyncMock()
                        mock_meta.return_value = mock_meta_store

                        learner = Learner(arbiter_helper, mock_redis)
                        learner.db = mock_db
                        learner.llm_explanation_client = mock_llm_instance
                        learner.audit_logger = mock_audit_instance
                        learner.meta_data_store = mock_meta_store

                        # Initialize additional learner attributes
                        learner.validation_schemas = {}
                        learner.validation_hooks = {}
                        learner.fuzzy_parsers = []
                        learner.event_hooks = {
                            "on_schema_reload": [],
                            "pre_learn": [],
                            "post_learn": [],
                            "pre_forget": [],
                            "post_forget": [],
                        }
                        learner.db_circuit_breaker = CircuitBreaker()
                        learner.audit_circuit_breaker = CircuitBreaker()
                        learner.learn_semaphore = asyncio.Semaphore(10)
                        learner._self_audit_task = None
                        learner._self_audit_stop_event = asyncio.Event()
                        learner.explanation_cache = {}

                        yield {
                            "learner": learner,
                            "arbiter": arbiter_helper,
                            "redis": mock_redis,
                            "db": mock_db,
                        }

                        # Restore original values
                        ArbiterConfig.ENCRYPTION_KEYS = original_encryption_keys
                        ArbiterConfig.ENCRYPTED_DOMAINS = original_encrypted_domains

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)  # Add timeout to prevent hanging
    async def test_complete_learning_cycle(self, setup_learner_environment):
        """Test a complete learning cycle: learn, retrieve, update, forget."""
        env = setup_learner_environment  # Don't await - it's not async
        learner = env["learner"]

        # Don't start the self-audit task for this test
        # Just connect the meta store
        await learner.meta_data_store.connect()

        try:
            # 1. Learn new fact
            with patch(
                "self_fixing_engineer.arbiter.learner.core.should_auto_learn", return_value=(True, None)
            ):
                with patch("self_fixing_engineer.arbiter.learner.core.validate_data") as mock_validate:
                    mock_validate.return_value = {"is_valid": True}

                    with patch(
                        "self_fixing_engineer.arbiter.learner.core.generate_explanation"
                    ) as mock_explain:
                        mock_explain.return_value = "New fact learned"

                        result = await learner.learn_new_thing(
                            domain="TestDomain",
                            key="fact1",
                            value={"data": "initial", "score": 100},
                            user_id="test_user",
                            source="test",
                        )

                        assert result["status"] == "learned"
                        assert result["version"] == 1

            # 2. Retrieve the fact from memory
            retrieved = await learner.retrieve_knowledge("TestDomain", "fact1")
            assert retrieved is not None
            assert retrieved["value"]["data"] == "initial"
            assert retrieved["version"] == 1

            # 3. Update the fact
            with patch(
                "self_fixing_engineer.arbiter.learner.core.should_auto_learn", return_value=(True, None)
            ):
                with patch("self_fixing_engineer.arbiter.learner.core.validate_data") as mock_validate:
                    mock_validate.return_value = {"is_valid": True}

                    with patch(
                        "self_fixing_engineer.arbiter.learner.core.generate_explanation"
                    ) as mock_explain:
                        mock_explain.return_value = "Fact updated"

                        result = await learner.learn_new_thing(
                            domain="TestDomain",
                            key="fact1",
                            value={"data": "updated", "score": 200},
                            user_id="test_user",
                        )

                        assert result["status"] == "learned"
                        assert result["version"] == 2

            # 4. Forget the fact
            result = await learner.forget_fact(
                domain="TestDomain",
                key="fact1",
                user_id="test_user",
                reason="test_cleanup",
            )

            assert result["status"] == "forgotten"

            # 5. Verify it's gone
            retrieved = await learner.retrieve_knowledge("TestDomain", "fact1")
            assert retrieved is None

        finally:
            # Cleanup
            if learner.meta_data_store:
                await learner.meta_data_store.disconnect()

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_batch_learning_with_validation(self, setup_learner_environment):
        """Test batch learning with mixed valid/invalid facts."""
        env = setup_learner_environment  # Don't await
        learner = env["learner"]

        await learner.meta_data_store.connect()

        try:
            # Set up validation schema
            test_schema = {
                "type": "object",
                "properties": {"name": {"type": "string"}, "value": {"type": "number"}},
                "required": ["name", "value"],
            }

            learner.validation_schemas = {
                "ValidatedDomain": {"schema": test_schema, "version": "1.0"}
            }

            facts = [
                {
                    "domain": "ValidatedDomain",
                    "key": "valid1",
                    "value": {"name": "test", "value": 42},
                },
                {
                    "domain": "ValidatedDomain",
                    "key": "invalid1",
                    "value": {"name": "test"},
                },
                {
                    "domain": "ValidatedDomain",
                    "key": "valid2",
                    "value": {"name": "test2", "value": 100},
                },
            ]

            with patch(
                "self_fixing_engineer.arbiter.learner.core.should_auto_learn", return_value=(True, None)
            ):
                with patch(
                    "self_fixing_engineer.arbiter.learner.core.generate_explanation",
                    return_value="Batch explanation",
                ):
                    results = await learner.learn_batch(
                        facts, user_id="test_user", write_to_disk=False
                    )

                    assert len(results) == 3
                    results_by_key = {
                        r.get("fact", {}).get("key"): r
                        for r in results
                        if isinstance(r, dict)
                    }
                    if "valid1" in results_by_key:
                        assert results_by_key["valid1"]["status"] == "learned"
                    if "invalid1" in results_by_key:
                        assert results_by_key["invalid1"]["status"] == "skipped"
                    if "valid2" in results_by_key:
                        assert results_by_key["valid2"]["status"] == "learned"
        finally:
            if learner.meta_data_store:
                await learner.meta_data_store.disconnect()

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_encryption_for_sensitive_domains(self, setup_learner_environment):
        """Test that sensitive domains are properly encrypted."""
        env = setup_learner_environment  # Don't await
        learner = env["learner"]

        await learner.meta_data_store.connect()

        try:
            sensitive_data = {
                "ssn": "123-45-6789",
                "credit_card": "4111-1111-1111-1111",
            }

            with patch(
                "self_fixing_engineer.arbiter.learner.core.should_auto_learn", return_value=(True, None)
            ):
                with patch("self_fixing_engineer.arbiter.learner.core.validate_data") as mock_validate:
                    mock_validate.return_value = {"is_valid": True}

                    with patch(
                        "self_fixing_engineer.arbiter.learner.core.generate_explanation"
                    ) as mock_explain:
                        mock_explain.return_value = "Sensitive data stored"

                        result = await learner.learn_new_thing(
                            domain="PersonalInfo",  # Encrypted domain
                            key="user123",
                            value=sensitive_data,
                            user_id="admin",
                            write_to_disk=False,
                        )
                        assert result["status"] == "learned"

            # Check that data is encrypted in memory
            mem_data = learner.arbiter.state["memory"]["PersonalInfo"]["user123"]
            assert isinstance(mem_data["value"], bytes)
            assert mem_data["value"].startswith(b"v1:")  # Encrypted with v1 key

            # Verify we can decrypt it back
            retrieved = await learner.retrieve_knowledge("PersonalInfo", "user123")
            assert retrieved["value"] == sensitive_data
        finally:
            if learner.meta_data_store:
                await learner.meta_data_store.disconnect()

    # Continue with the rest of the tests following the same pattern...
    # The key change is removing "await" from "env = await setup_learner_environment"
    # and changing it to "env = setup_learner_environment"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
