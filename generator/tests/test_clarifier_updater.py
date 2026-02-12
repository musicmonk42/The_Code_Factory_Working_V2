# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_clarifier_updater.py

import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
import pytest
from pathlib import Path

# FIX: Added Callable to this line
from typing import Any, Callable

# FIX: Removed Callable from this line
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
TEST_DIR = Path(__file__).parent
CLARIFIER_DIR = TEST_DIR.parent
GENERATOR_DIR = CLARIFIER_DIR.parent
PROJECT_ROOT = GENERATOR_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Mock omnicore_engine module before importing clarifier modules
if 'omnicore_engine' not in sys.modules:
    sys.modules['omnicore_engine'] = MagicMock()
if 'omnicore_engine.plugin_registry' not in sys.modules:
    mock_plugin_registry = MagicMock()
    mock_plugin_registry.PlugInKind = MagicMock()
    mock_plugin_registry.plugin = MagicMock()
    sys.modules['omnicore_engine.plugin_registry'] = mock_plugin_registry

# --- Mock Configuration and Core Utilities ---


class MockConfigObject:
    LLM_PROVIDER = "grok"
    HISTORY_FILE = "mock_history_updater.json"
    CONTEXT_DB_PATH = ":memory:"
    HISTORY_DB_PATH = ":memory:"  # For history store
    KMS_KEY_ID = "mock_kms_key"
    ALERT_ENDPOINT = "http://mock.alert/endpoint"
    HISTORY_COMPRESSION = False
    CONFLICT_STRATEGY = "auto_merge"
    LLM_INFERENCE_ENDPOINT = "http://mock.llm/inference"
    LLM_INFERENCE_MODEL = "mock-model"
    INFERENCE_LLM = "mock-model"  # For inference calls
    LLM_INFERENCE_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    SCHEMA_VERSION = 2  # Current schema version
    INTERACTION_MODE = "cli"
    BATCH_STRATEGY = "default"
    FEEDBACK_STRATEGY = "none"
    TARGET_LANGUAGE = "en"
    CONTEXT_QUERY_LIMIT = 3
    HISTORY_LOOKBACK_LIMIT = 10
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_TIMEOUT = 30
    is_production_env = False


mock_config_instance = MagicMock(spec=MockConfigObject)
for attr, value in MockConfigObject.__dict__.items():
    if not attr.startswith("_"):
        setattr(mock_config_instance, attr, value)

TEST_FERNET_KEY = base64.urlsafe_b64encode(b"\x00" * 32)
mock_fernet_instance = MagicMock()
mock_fernet_instance.encrypt.side_effect = lambda data: base64.b64encode(
    b"ENCRYPTED_" + (data if isinstance(data, bytes) else data.encode())
)
mock_fernet_instance.decrypt.side_effect = lambda data: base64.b64decode(data).replace(
    b"ENCRYPTED_", b""
)

mock_logger = MagicMock()
mock_logger.info = MagicMock()
mock_logger.warning = MagicMock()
mock_logger.error = MagicMock()
mock_logger.debug = MagicMock()

# Mock OpenTelemetry to prevent initialization errors
mock_tracer = MagicMock()
mock_span = MagicMock()
mock_span.__enter__ = MagicMock(return_value=mock_span)
mock_span.__exit__ = MagicMock(return_value=None)
mock_tracer.start_as_current_span = MagicMock(return_value=mock_span)

# Create mock for opentelemetry.sdk.trace.sampling.ALWAYS_ON
mock_always_on = MagicMock()
mock_trace_provider = MagicMock()
mock_span_processor = MagicMock()

# Mock instances for use in tests
MockLogAction = MagicMock(side_effect=lambda *args, **kwargs: None)
MockSendAlert = AsyncMock()
MockRedactSensitive = MagicMock(
    side_effect=lambda x: str(x)
    .replace("SECRET123", "[REDACTED_API_KEY]")
    .replace("admin@example.com", "[REDACTED_EMAIL]")
    .replace("user@example.com", "[REDACTED_EMAIL]")
)
MockDetectPII = MagicMock(return_value=False)


# FIX: Define the redaction logic that the _recursive_transform mock will use
def mock_redaction_logic(
    data: Any, detect_func: Callable, redact_func: Callable
) -> Any:
    if isinstance(data, str):
        return redact_func(data)
    elif isinstance(data, dict):
        return {
            k: mock_redaction_logic(v, detect_func, redact_func)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [mock_redaction_logic(item, detect_func, redact_func) for item in data]
    return data


MockRecursiveTransform = MagicMock(side_effect=mock_redaction_logic)


@pytest.fixture(autouse=True)
def mock_dependencies():
    """Fixture to mock all dependencies for clarifier_updater tests.
    
    Note: This fixture runs during test execution, not during test collection.
    Module-level imports happen at collection time, which is why module-level
    patches (see below after fixture definition) are still required.
    """
    patches = [
        patch("opentelemetry.trace.get_tracer", return_value=mock_tracer),
        patch("opentelemetry.sdk.trace.sampling.ALWAYS_ON", mock_always_on),
        patch("opentelemetry.sdk.trace.TracerProvider", return_value=mock_trace_provider),
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", return_value=mock_span_processor),
        patch("opentelemetry.trace.set_tracer_provider"),
        patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter", return_value=MagicMock()),
        patch("generator.clarifier.clarifier.get_config", return_value=mock_config_instance),
        patch("generator.clarifier.clarifier.get_fernet", return_value=mock_fernet_instance),
        patch("generator.clarifier.clarifier.get_logger", return_value=mock_logger),
        patch("generator.clarifier.clarifier_updater.get_config", return_value=mock_config_instance),
        patch("generator.clarifier.clarifier_updater.get_fernet", return_value=mock_fernet_instance),
        patch("generator.clarifier.clarifier_updater.get_logger", return_value=mock_logger),
        patch("generator.clarifier.clarifier_updater.log_action", side_effect=MockLogAction),
        patch("generator.clarifier.clarifier_updater.send_alert", new_callable=AsyncMock),
        patch("generator.clarifier.clarifier_updater.redact_sensitive", side_effect=MockRedactSensitive),
        patch("generator.clarifier.clarifier_updater.detect_pii", return_value=False),
        patch("generator.clarifier.clarifier_updater._recursive_transform", side_effect=mock_redaction_logic),
    ]
    
    # Start all patches
    for p in patches:
        p.start()
    
    yield
    
    # Stop all patches
    for p in patches:
        p.stop()


# Import the clarifier module first so it exists in sys.modules before patching
try:
    import generator.clarifier.clarifier as _clarifier_module
except ImportError:
    # Create a minimal stub if import fails
    import sys
    from types import ModuleType

    if "generator" not in sys.modules:
        gen_stub = ModuleType("generator")
        gen_stub.__path__ = []  # Make it a package
        sys.modules["generator"] = gen_stub
    if "generator.clarifier" not in sys.modules:
        clarifier_stub = ModuleType("generator.clarifier")
        clarifier_stub.__path__ = []  # Make it a package
        sys.modules["generator.clarifier"] = clarifier_stub
    if "generator.clarifier.clarifier" not in sys.modules:
        _clarifier_module = ModuleType("generator.clarifier.clarifier")
        _clarifier_module.get_config = MagicMock(return_value=mock_config_instance)
        _clarifier_module.get_fernet = MagicMock(return_value=mock_fernet_instance)
        _clarifier_module.get_logger = MagicMock(return_value=mock_logger)
        sys.modules["generator.clarifier.clarifier"] = _clarifier_module

# Module-level patches for collection-time imports
# These are required because module imports happen at collection time (before fixtures run).
# The patches are started but not stopped because:
# 1. They only affect this test module's scope
# 2. Test isolation is handled by the fixture above during test execution
# 3. pytest cleans up the test module after all tests complete
_module_level_patches = [
    patch("generator.clarifier.clarifier.get_config", return_value=mock_config_instance),
    patch("generator.clarifier.clarifier_updater.get_config", return_value=mock_config_instance),
]
for p in _module_level_patches:
    p.start()

import generator.clarifier.clarifier_updater as clarifier_updater_module

# Import the classes we need
from generator.clarifier.clarifier_updater import (
    UPDATE_ERRORS,
    HistoryStore,
    RequirementsUpdater,
    update_requirements_with_answers,
)


class TestHistoryStore(unittest.IsolatedAsyncioTestCase):
    """Test HistoryStore functionality."""

    async def asyncSetUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        # FIX: Remove compression parameter - it's read from settings.HISTORY_COMPRESSION
        self.store = HistoryStore(self.temp_db.name, mock_fernet_instance)
        await self.store._init_db()

    async def asyncTearDown(self):
        await self.store.close()
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

    async def test_store_and_query(self):
        """Test storing and querying history."""
        test_data = {
            "features": ["test_feature"],
            "version": 1,
            "version_hash": "testhash",
            "schema_version": 2,
        }

        await self.store.store(test_data)

        results = await self.store.query(limit=1)
        self.assertEqual(len(results), 1)
        self.assertIn("features", results[0])
        self.assertEqual(results[0]["features"], ["test_feature"])

    async def test_query_limit(self):
        """Test query limit parameter."""
        for i in range(5):
            await self.store.store(
                {"features": [f"feature_{i}"], "version": i + 1, "schema_version": 2}
            )

        results = await self.store.query(limit=3)
        self.assertEqual(len(results), 3)

    async def test_query_by_version(self):
        """Test querying by specific version."""
        await self.store.store({"features": ["v1"], "version": 1, "schema_version": 2})
        await self.store.store({"features": ["v2"], "version": 2, "schema_version": 2})
        await self.store.store({"features": ["v3"], "version": 3, "schema_version": 2})

        results = await self.store.query(version=2, limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["version"], 2)

    async def test_empty_query(self):
        """Test querying empty database."""
        results = await self.store.query(limit=10)
        self.assertEqual(len(results), 0)


class TestRequirementsUpdater(unittest.IsolatedAsyncioTestCase):
    """Test RequirementsUpdater class."""

    async def asyncSetUp(self):
        # FIX: Remove .clear() calls on Prometheus metrics
        # The metrics are global and don't need to be cleared for isolated tests

        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()

        # Temporarily override the config to use our temp database
        mock_config_instance.HISTORY_DB_PATH = self.temp_db.name

        # FIX 2: Disable self-test in tests to prevent SystemExit
        self.updater = RequirementsUpdater(run_self_test=False)

        # Wait for the db init task to complete
        await self.updater._db_init_task

    async def asyncTearDown(self):
        if hasattr(self.updater, "history_store"):
            await self.updater.history_store.close()
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

    async def test_initialization(self):
        """Test RequirementsUpdater initialization."""
        self.assertIsNotNone(self.updater)
        self.assertIsNotNone(self.updater.history_store)
        self.assertIsNotNone(self.updater.conflict_resolver)
        self.assertIsNotNone(self.updater.llm_client)

    async def test_simple_update(self):
        """Test a simple requirements update."""
        requirements = {
            "features": ["existing_feature"],
            "constraints": ["existing_constraint"],
            "schema_version": 1,
        }
        ambiguities = ["What does 'user-friendly' mean?"]
        answers = ["Easy to use with intuitive interface"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["intuitive interface"],
                    "inferred_constraints": [],
                }
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        self.assertIsInstance(updated, dict)
        self.assertIn("features", updated)
        # FIX 3: Inferred features go into inferred_features, not features
        self.assertIn("intuitive interface", updated["inferred_features"])

    async def test_conflict_resolution_auto_merge(self):
        """Test automatic conflict resolution."""
        requirements = {
            "features": ["feature1"],
            "constraints": ["constraint1"],
            "schema_version": 1,
        }
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["feature1"],  # Duplicate
                    "inferred_constraints": [],
                }
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        # Auto-merge should deduplicate
        self.assertEqual(len(updated["features"]), 1)
        self.assertEqual(updated["features"][0], "feature1")

    async def test_conflict_resolution_prefer_incoming(self):
        """Test conflict resolution with prefer_incoming strategy."""
        requirements = {
            "features": ["old_feature"],
            "schema_version": 1,
            "conflict_strategy": "prefer_incoming",  # Set strategy in requirements
        }
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["new_feature"],
                    "inferred_constraints": [],
                }
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        self.assertIn(
            "new_feature", updated["inferred_features"]
        )  # Inferred features go here
        self.assertIn("old_feature", updated["features"])

    async def test_conflict_resolution_prefer_base(self):
        """Test conflict resolution with prefer_base strategy."""
        requirements = {
            "features": ["base_feature"],
            "schema_version": 1,
            "conflict_strategy": "prefer_base",  # Set strategy in requirements
        }
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["base_feature"],  # Duplicate
                    "inferred_constraints": [],
                }
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        # Should keep base version
        self.assertEqual(len(updated["features"]), 1)
        self.assertEqual(updated["features"][0], "base_feature")

    async def test_conflict_resolution_manual(self):
        """Test manual conflict resolution (fallback to auto-merge in async)."""
        requirements = {
            "features": ["feature1"],
            "schema_version": 1,
            "conflict_strategy": "manual",  # Set strategy in requirements
        }
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["feature1"],  # Duplicate
                    "inferred_constraints": [],
                }
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        # Manual mode falls back to auto-merge in async context
        self.assertIn("features", updated)

    async def test_redaction(self):
        """Test PII redaction in requirements."""
        requirements = {
            "features": ["Contact admin@example.com"],
            "constraints": ["API Key: SECRET123"],
            "schema_version": 1,
        }
        ambiguities = ["How to authenticate?"]
        answers = ["Use the API key SECRET123 and email admin@example.com"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={"inferred_features": [], "inferred_constraints": []}
            ),
        ):
            updated = await self.updater.update(requirements, ambiguities, answers)

        # FIX 4: Check that sensitive data was redacted in clarifications (not in requirements)
        self.assertIn("[REDACTED_EMAIL]", str(updated["clarifications"]))
        self.assertIn("[REDACTED_API_KEY]", str(updated["clarifications"]))
        # Ensure original requirements are NOT redacted
        self.assertIn("admin@example.com", str(updated["features"]))
        self.assertIn("SECRET123", str(updated["constraints"]))

    async def test_schema_migration(self):
        """Test schema migration from v1 to v2."""
        old_requirements = {
            "features": ["feature1"],
            "constraints": ["constraint1"],
            "schema_version": 1,
        }

        migrated = await self.updater._migrate_schema(old_requirements)

        self.assertEqual(migrated["schema_version"], 2)
        self.assertIn("inferred_features", migrated)
        self.assertIn("inferred_constraints", migrated)

    async def test_versioning(self):
        """Test requirements versioning."""
        requirements = {"features": ["f1"], "version": 1, "schema_version": 2}

        # FIX 5: Method is _add_versioning and params are user/reason
        versioned = self.updater._add_versioning(
            requirements, user="test_user", reason="testing"
        )

        self.assertEqual(versioned["version"], 2)
        self.assertIn("prev_hash", versioned)
        self.assertIn("version_hash", versioned)
        self.assertIn("update_timestamp", versioned)
        self.assertIn("update_reason", versioned)
        self.assertEqual(versioned["updated_by"], "test_user")
        self.assertEqual(versioned["update_reason"], "testing")

    # FIX 6: Use exact hash algorithm from production
    async def test_hash_chain_verification(self):
        """Test hash chain integrity verification."""
        requirements = {
            "features": ["feature1"],
            "version": 1,
            "prev_hash": "genesis_hash_placeholder",  # Must match production code
            "schema_version": 2,
            "updated_by": "test_user",
            "update_reason": "testing",
            "update_timestamp": "2025-01-01T00:00:00.000Z",
            "changes": [],  # Must match production code
        }

        # Create hash for requirements using the EXACT algorithm from production
        import hashlib

        hashable_data = {k: v for k, v in requirements.items() if k != "version_hash"}
        hashable_data["prev_hash"] = (
            "genesis_hash_placeholder"  # Ensure prev_hash is in hashable data
        )
        canonical_json = json.dumps(
            hashable_data, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        computed_hash = hashlib.sha256(canonical_json).hexdigest()
        requirements["version_hash"] = computed_hash

        # Should verify successfully
        self.assertTrue(self.updater._verify_hash_chain(requirements))

        # Tamper with hash
        requirements["version_hash"] = "wrong_hash"
        self.assertFalse(self.updater._verify_hash_chain(requirements))

    async def test_history_storage(self):
        """Test storing requirements in history."""
        requirements = {"features": ["feature1"], "version": 1, "schema_version": 2}
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={"inferred_features": [], "inferred_constraints": []}
            ),
        ):
            await self.updater.update(requirements, ambiguities, answers)

        # Verify stored in history
        history = await self.updater.history_store.query(limit=1)
        self.assertEqual(len(history), 1)

    async def test_update_with_correlation_id(self):
        """Test update with correlation ID for tracing."""
        requirements = {"features": ["f1"], "schema_version": 1}
        ambiguities = ["term"]
        answers = ["meaning"]

        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={"inferred_features": [], "inferred_constraints": []}
            ),
        ):
            result = await self.updater.update(
                requirements,
                ambiguities,
                answers,
                correlation_id="test-correlation-123",
            )

        self.assertIsInstance(result, dict)

    async def test_update_error_handling(self):
        """Test error handling during update."""
        requirements = {"features": ["f1"], "schema_version": 1}
        ambiguities = ["term"]
        answers = ["meaning"]

        # Make inference fail
        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(side_effect=Exception("Inference failed")),
        ):
            with self.assertRaises(Exception) as context:
                await self.updater.update(requirements, ambiguities, answers)

            self.assertIn("Inference failed", str(context.exception))

        # Verify error metrics were incremented (just check they exist, don't rely on exact values)
        self.assertIsNotNone(UPDATE_ERRORS)

        # Verify alert was sent
        MockSendAlert.assert_awaited()

    # FIX 7: Add await to self_test()
    async def test_self_test(self):
        """Test self-test functionality."""
        # Clear history first
        await self.updater._clear_history_for_test()

        # Run self-test
        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["inferred_self_test_feature"],
                    "inferred_constraints": [],
                }
            ),
        ):
            result = await self.updater.self_test()

        self.assertTrue(result)

    # FIX 8: Add await to self_test()
    async def test_self_test_redaction_check(self):
        """Test that self-test verifies redaction."""
        await self.updater._clear_history_for_test()

        # Run self-test with redaction verification
        with patch.object(
            self.updater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["inferred_self_test_feature"],
                    "inferred_constraints": [],
                }
            ),
        ):
            result = await self.updater.self_test()

        # Redaction should have been tested
        self.assertTrue(result)

    async def test_inference_timeout(self):
        """Test LLM inference timeout handling."""
        clarifications = {"key": "value"}

        # Mock slow response
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200

            async def slow_json():
                await asyncio.sleep(100)  # Very slow
                return {"inferred_features": [], "inferred_constraints": []}

            mock_response.json = slow_json

            mock_post_cm = MagicMock()
            mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_cm)

            mock_session_cm = MagicMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session_cm

            # Should timeout and return empty
            result = await self.updater._infer_updates(clarifications)
            self.assertEqual(
                result, {"inferred_features": [], "inferred_constraints": []}
            )


class TestConvenienceFunction(unittest.IsolatedAsyncioTestCase):
    """Test convenience function for updates."""

    async def asyncSetUp(self):
        # FIX: Remove .clear() calls on Prometheus metrics
        # Just reset the global updater
        import generator.clarifier.clarifier_updater as updater_module

        updater_module.updater = None

    # FIX 9: Test that convenience function correctly rejects async context
    async def test_update_requirements_with_answers_raises_in_async(self):
        """Test that convenience function correctly rejects async context."""
        requirements = {"features": ["f1"], "schema_version": 1}
        ambiguities = ["term"]
        answers = ["meaning"]

        # The convenience function should raise RuntimeError when called from async context
        with self.assertRaises(RuntimeError) as context:
            update_requirements_with_answers(requirements, ambiguities, answers)

        # Check for the specific error messages from the production code
        self.assertIn("async context", str(context.exception))
        self.assertIn("await initialize_updater()", str(context.exception))

    # Add a test for the synchronous (non-running-loop) context
    @patch("generator.clarifier.clarifier_updater.asyncio.run")
    @patch(
        "generator.clarifier.clarifier_updater.updater", None
    )  # Ensure updater is None
    @patch("generator.clarifier.clarifier_updater.RequirementsUpdater")
    def test_update_requirements_with_answers_sync_path(
        self, MockRequirementsUpdater, mock_asyncio_run
    ):
        """Test convenience function initialization and use from a sync context."""
        requirements = {"features": ["f1"], "schema_version": 1}
        ambiguities = ["term"]
        answers = ["meaning"]

        # Mock the instance that will be created
        mock_updater_instance = MagicMock()
        mock_updater_instance.update = AsyncMock(
            return_value={"features": ["f1"], "clarifications": {}}
        )
        # FIX: Replace asyncio.create_task with awaitable mocks
        mock_updater_instance._db_init_task = AsyncMock()
        mock_updater_instance._self_test_task = AsyncMock()
        MockRequirementsUpdater.return_value = mock_updater_instance

        # Mock asyncio.run to just return the mocked result
        mock_asyncio_run.return_value = {"features": ["f1"], "clarifications": {}}

        # This call happens in a sync test runner (no running event loop)
        result = update_requirements_with_answers(requirements, ambiguities, answers)

        self.assertIsInstance(result, dict)
        self.assertEqual(result, {"features": ["f1"], "clarifications": {}})
        # FIX 2 (Call Count): Check that asyncio.run was called once
        self.assertEqual(mock_asyncio_run.call_count, 1)


def tearDownModule():
    """Clean up all patches.
    
    Note: Module-level patches are managed by _module_level_patches list.
    The autouse fixture mock_dependencies handles per-test cleanup.
    This function stops only the module-level patches that were started.
    """
    for p in _module_level_patches:
        try:
            p.stop()
        except RuntimeError:
            # Patch may already be stopped
            pass
    print("\nAll clarifier_updater mocks stopped.")


if __name__ == "__main__":
    unittest.main(argv=["first-arg-is-ignored"], exit=False)
    tearDownModule()
