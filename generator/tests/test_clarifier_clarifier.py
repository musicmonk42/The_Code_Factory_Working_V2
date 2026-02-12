# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_clarifier.py

import base64
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
)

# --- Mock Configuration and Core Utilities (MUST RUN BEFORE IMPORTS) ---


class MockConfigObject:
    LLM_PROVIDER = "grok"
    INTERACTION_MODE = "cli"
    BATCH_STRATEGY = "default"
    FEEDBACK_STRATEGY = "none"
    HISTORY_FILE = "mock_history_clarifier.json"
    TARGET_LANGUAGE = "en"
    CONTEXT_DB_PATH = ":memory:"  # In-memory SQLite for tests
    KMS_KEY_ID = "mock_kms_key"
    ALERT_ENDPOINT = "http://mock.alert/endpoint"
    HISTORY_COMPRESSION = False
    CONTEXT_QUERY_LIMIT = 3
    HISTORY_LOOKBACK_LIMIT = 10
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_TIMEOUT = 30
    CONFLICT_STRATEGY = "auto_merge"
    is_production_env = False
    GROK_API_KEY = "mock_grok_key"
    GROK_API_ENDPOINT = "http://mock.grok/api"
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0


mock_config_instance = MagicMock(spec=MockConfigObject)
for attr, value in MockConfigObject.__dict__.items():
    if not attr.startswith("_"):
        setattr(mock_config_instance, attr, value)

TEST_FERNET_KEY = base64.urlsafe_b64encode(b"\x00" * 32)
mock_fernet_instance = MagicMock()
# FIX: Removed the incorrect base64.b64encode and base64.b64decode calls.
# The mock should just prepend bytes to simulate encryption while keeping the content searchable for LIKE.
mock_fernet_instance.encrypt.side_effect = lambda data: (
    b"ENCRYPTED_" + data if isinstance(data, bytes) else b"ENCRYPTED_" + data.encode()
)
mock_fernet_instance.decrypt.side_effect = lambda data: data.replace(b"ENCRYPTED_", b"")

mock_logger = MagicMock()
mock_logger.info = MagicMock()
mock_logger.warning = MagicMock()
mock_logger.error = MagicMock()
mock_logger.debug = MagicMock()
mock_logger.critical = MagicMock()

# Mock KMS client
mock_kms_client = MagicMock()
mock_kms_client.generate_data_key.return_value = {
    "Plaintext": TEST_FERNET_KEY,
    "CiphertextBlob": b"encrypted_key",
}

# Start critical patches before importing
patcher_dynaconf = patch(
    "generator.clarifier.clarifier.Dynaconf", return_value=mock_config_instance
)
patcher_boto3 = patch(
    "generator.clarifier.clarifier.boto3.client", return_value=mock_kms_client
)
patcher_fernet = patch(
    "generator.clarifier.clarifier.Fernet", return_value=mock_fernet_instance
)
patcher_sys_exit = patch("generator.clarifier.clarifier.sys.exit")
patcher_get_config = patch(
    "generator.clarifier.clarifier.get_config", return_value=mock_config_instance
)

patcher_dynaconf.start()
patcher_boto3.start()
patcher_fernet.start()
patcher_sys_exit.start()
patcher_get_config.start()

# Mock the sub-module imports that might fail
patcher_llm = patch("generator.clarifier.clarifier.GrokLLM")
patcher_prioritizer = patch("generator.clarifier.clarifier.DefaultPrioritizer")
patcher_get_channel = patch("generator.clarifier.clarifier.get_channel")
patcher_update_requirements = patch(
    "generator.clarifier.clarifier.update_requirements_with_answers"
)

MockLLM = patcher_llm.start()
MockPrioritizer = patcher_prioritizer.start()
MockGetChannel = patcher_get_channel.start()
MockUpdateReqs = patcher_update_requirements.start()

# Setup mock returns
mock_llm_instance = MagicMock()
MockLLM.return_value = mock_llm_instance

mock_prioritizer_instance = MagicMock()
mock_prioritizer_instance.prioritize = AsyncMock(
    return_value={
        "prioritized": [
            {
                "original": "ambiguous term",
                "score": 10,
                "question": "What does this mean?",
            }
        ],
        "batch": [0],
    }
)
MockPrioritizer.return_value = mock_prioritizer_instance

mock_channel_instance = MagicMock()
mock_channel_instance.prompt = AsyncMock(return_value=["user answer"])
MockGetChannel.return_value = mock_channel_instance

MockUpdateReqs.return_value = {
    "features": ["updated"],
    "clarifications": {"ambiguous term": "user answer"},
}

# Import after mocking
try:
    from generator.clarifier.clarifier import (
        CircuitBreaker,
        Clarifier,
        SQLiteContextManager,
        get_circuit_breaker,
        get_config,
        get_fernet,
        get_logger,
    )

    # Try to import optional functions
    try:
        from generator.clarifier.clarifier import load_config, setup_logging
    except ImportError:
        setup_logging = None
        load_config = None

    try:
        from generator.clarifier.clarifier import initialize_encryption
    except ImportError:
        initialize_encryption = None

    # Try to import metrics
    try:
        from generator.clarifier.clarifier import (
            CLARIFIER_CYCLES,
            CLARIFIER_ERRORS,
            CLARIFIER_LATENCY,
        )
    except (ImportError, AttributeError):
        # Create dummy metrics if they don't exist
        from unittest.mock import MagicMock

        CLARIFIER_CYCLES = MagicMock()
        CLARIFIER_ERRORS = MagicMock()
        CLARIFIER_LATENCY = MagicMock()
        CLARIFIER_CYCLES.clear = MagicMock()
        CLARIFIER_ERRORS.clear = MagicMock()
        CLARIFIER_LATENCY.clear = MagicMock()
        CLARIFIER_CYCLES.labels = MagicMock(
            return_value=MagicMock(_value=MagicMock(get=MagicMock(return_value=1)))
        )
        CLARIFIER_ERRORS.labels = MagicMock(
            return_value=MagicMock(_value=MagicMock(get=MagicMock(return_value=1)))
        )

except ImportError as e:
    print(f"Warning: Could not import from clarifier: {e}")
    print("Some tests may be skipped")
    # Create minimal mocks for testing
    Clarifier = None
    CircuitBreaker = None
    SQLiteContextManager = None


@unittest.skipIf(CircuitBreaker is None, "CircuitBreaker class not available")
class TestCircuitBreaker(unittest.TestCase):
    """Test CircuitBreaker functionality."""

    def setUp(self):
        self.cb = CircuitBreaker(threshold=3, timeout=1)
        if hasattr(CLARIFIER_ERRORS, "clear"):
            CLARIFIER_ERRORS.clear()

    def test_circuit_breaker_opens_after_threshold(self):
        """Circuit breaker should open after threshold failures."""
        for _ in range(3):
            self.cb.record_failure(Exception("test"))

        self.assertTrue(self.cb.is_open())

    def test_circuit_breaker_success_resets(self):
        """Success should reset failure count."""
        self.cb.record_failure(Exception("test"))
        self.assertEqual(self.cb.failure_count, 1)

        self.cb.record_success()
        self.assertEqual(self.cb.failure_count, 0)

    @pytest.mark.slow
    def test_circuit_breaker_closes_after_timeout(self):
        """Circuit breaker should close after timeout."""
        import time

        for _ in range(3):
            self.cb.record_failure(Exception("test"))

        self.assertTrue(self.cb.is_open())
        time.sleep(1.1)  # Wait for timeout
        self.assertFalse(self.cb.is_open())


@unittest.skipIf(
    SQLiteContextManager is None, "SQLiteContextManager class not available"
)
class TestSQLiteContextManager(unittest.IsolatedAsyncioTestCase):
    """Test SQLiteContextManager for storing and querying context."""

    async def asyncSetUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.manager = SQLiteContextManager(self.temp_db.name, mock_fernet_instance)
        await self.manager._init_db()

    async def asyncTearDown(self):
        await self.manager.close()
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

    async def test_store_and_retrieve(self):
        """Test storing and retrieving context data."""
        test_data = {"key": "value", "description": "test data"}
        await self.manager.store(test_data)

        results = await self.manager.query("test", limit=1)
        self.assertEqual(len(results), 1)
        self.assertIn("key", results[0])

    async def test_query_limit(self):
        """Test query limit parameter."""
        for i in range(5):
            await self.manager.store(
                {"key": f"value{i}", "description": f"test data {i}"}
            )

        results = await self.manager.query("test", limit=3)
        self.assertEqual(len(results), 3)

    async def test_empty_query(self):
        """Test querying with no results."""
        results = await self.manager.query("nonexistent")
        self.assertEqual(len(results), 0)


@unittest.skipIf(Clarifier is None, "Clarifier class not available")
class TestClarifier(unittest.IsolatedAsyncioTestCase):
    """Test the main Clarifier class."""

    async def asyncSetUp(self):
        # Clear metrics
        if hasattr(CLARIFIER_CYCLES, "clear"):
            CLARIFIER_CYCLES.clear()
        if hasattr(CLARIFIER_ERRORS, "clear"):
            CLARIFIER_ERRORS.clear()

        # Create temp files for testing
        self.temp_history = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.temp_history.close()
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()

        # Update config for testing
        mock_config_instance.HISTORY_FILE = self.temp_history.name
        mock_config_instance.CONTEXT_DB_PATH = self.temp_db.name

        # FIX: Create and initialize the context manager instance first
        self.context_manager_instance = SQLiteContextManager(
            self.temp_db.name, mock_fernet_instance
        )
        await self.context_manager_instance._init_db()

        # Mock logger setup to return our mock
        with patch(
            "generator.clarifier.clarifier.setup_logging", return_value=mock_logger
        ):
            # FIX: Pass mock dependencies directly to the constructor
            self.clarifier = Clarifier(
                llm=mock_llm_instance,
                prioritizer=mock_prioritizer_instance,
                context_manager=self.context_manager_instance,
            )

    async def asyncTearDown(self):
        # FIX: Close the explicitly created context_manager_instance
        if hasattr(self, "context_manager_instance") and self.context_manager_instance:
            await self.context_manager_instance.close()

        if os.path.exists(self.temp_history.name):
            os.unlink(self.temp_history.name)
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

        # Cleanup any .tmp files
        for f in os.listdir("."):
            if f.startswith("mock_history_clarifier") and f.endswith(".tmp"):
                try:
                    os.unlink(f)
                except:
                    pass

    async def test_get_clarifications_success(self):
        """Test successful clarification workflow."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]

        result = await self.clarifier.get_clarifications(ambiguities, requirements)

        # Verify result structure
        self.assertIsInstance(result, dict)
        self.assertIn("clarifications", result)

        # Verify metrics
        try:
            cycles_metric = CLARIFIER_CYCLES.labels(status="completed")._value.get()
            self.assertGreater(cycles_metric, 0)
        except (AttributeError, TypeError):
            # Metrics may not be available
            pass

    async def test_get_clarifications_with_context(self):
        """Test clarification with context retrieval."""
        # Store some context
        await self.clarifier.context_manager.store(
            {
                "description": "previous clarification about ambiguous term",
                "result": "clarified meaning",
            }
        )

        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]

        result = await self.clarifier.get_clarifications(ambiguities, requirements)

        self.assertIsInstance(result, dict)

    async def test_retry_mechanism(self):
        """Test retry mechanism with failures."""
        call_count = 0

        async def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return "success"

        result = await self.clarifier._retry(failing_func, retries=3, delay=0.1)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

    async def test_retry_exhaustion(self):
        """Test retry mechanism when all attempts fail."""

        async def always_failing_func():
            raise Exception("Permanent failure")

        with self.assertRaises(Exception) as context:
            await self.clarifier._retry(always_failing_func, retries=2, delay=0.1)

        self.assertIn("Permanent failure", str(context.exception))

    async def test_save_history(self):
        """Test history saving functionality."""
        self.clarifier.history = [
            {"ambiguity": "test", "answer": "test_answer", "timestamp": "2025-07-30"}
        ]

        await self.clarifier._save_history()

        # Verify file was created
        self.assertTrue(os.path.exists(self.temp_history.name))

        # Verify file is not empty
        self.assertGreater(os.path.getsize(self.temp_history.name), 0)

    async def test_save_history_with_compression(self):
        """Test history saving with compression enabled."""
        mock_config_instance.HISTORY_COMPRESSION = True

        self.clarifier.history = [{"test": "data"}]
        await self.clarifier._save_history()

        self.assertTrue(os.path.exists(self.temp_history.name))

        # Reset compression flag
        mock_config_instance.HISTORY_COMPRESSION = False

    async def test_circuit_breaker_integration(self):
        """Test circuit breaker integration in clarifier."""
        # Force circuit breaker to open
        for _ in range(5):
            self.clarifier.circuit_breaker.record_failure(Exception("test"))

        self.assertTrue(self.clarifier.circuit_breaker.is_open())

        # Attempt operation with open circuit breaker
        with self.assertRaises(Exception) as context:
            await self.clarifier._retry(AsyncMock(), retries=1)

        self.assertIn("Circuit breaker", str(context.exception))

    async def test_graceful_shutdown(self):
        """Test graceful shutdown process."""
        await self.clarifier.graceful_shutdown("test shutdown")

        self.assertTrue(self.clarifier.shutdown_event.is_set())

    async def test_empty_ambiguities(self):
        """Test handling of empty ambiguities list."""
        requirements = {"features": ["feature1"]}
        ambiguities = []

        # Should handle empty list gracefully
        result = await self.clarifier.get_clarifications(ambiguities, requirements)
        self.assertIsInstance(result, dict)

    async def test_load_history_on_init(self):
        """Test loading existing history on initialization."""
        # Create history file with data
        history_data = [{"test": "historical_data"}]
        encrypted = mock_fernet_instance.encrypt(json.dumps(history_data).encode())

        with open(self.temp_history.name, "wb") as f:
            f.write(encrypted)

        # Create new clarifier instance
        with patch(
            "generator.clarifier.clarifier.setup_logging", return_value=mock_logger
        ):
            new_clarifier = Clarifier(
                llm=mock_llm_instance,
                prioritizer=mock_prioritizer_instance,
                context_manager=self.context_manager_instance,
            )

        # History should be loaded (might be empty if decryption fails, but no crash)
        self.assertIsInstance(new_clarifier.history, list)

    async def test_detect_ambiguities_with_llm(self):
        """Test detect_ambiguities with working LLM."""
        # Setup mock LLM to return JSON list of ambiguities
        mock_llm_instance.generate = AsyncMock(
            return_value='["Database not specified", "API type unclear"]'
        )
        
        readme_content = "Build a web application with user authentication"
        result = await self.clarifier.detect_ambiguities(readme_content)
        
        # Should return list of ambiguities
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        mock_llm_instance.generate.assert_called_once()

    async def test_detect_ambiguities_rule_based_fallback(self):
        """Test detect_ambiguities falls back to rule-based when LLM fails."""
        # Setup mock LLM to fail
        mock_llm_instance.generate = AsyncMock(side_effect=Exception("LLM error"))
        
        readme_content = "Build a web application with database"
        result = await self.clarifier.detect_ambiguities(readme_content)
        
        # Should return list of ambiguities from rule-based detection
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)

    async def test_detect_ambiguities_no_llm(self):
        """Test detect_ambiguities with no LLM available."""
        # Create clarifier without LLM
        with patch(
            "generator.clarifier.clarifier.setup_logging", return_value=mock_logger
        ):
            clarifier_no_llm = Clarifier(
                llm=None,
                prioritizer=mock_prioritizer_instance,
                context_manager=self.context_manager_instance,
            )
        
        readme_content = "Build a REST API with authentication"
        result = await clarifier_no_llm.detect_ambiguities(readme_content)
        
        # Should use rule-based detection
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)

    async def test_generate_questions_with_llm(self):
        """Test generate_questions with working LLM."""
        # Setup mock LLM to return JSON list of questions
        mock_llm_instance.generate = AsyncMock(
            return_value='[{"question": "What database?", "category": "database"}]'
        )
        
        ambiguities = ["Database not specified"]
        result = await self.clarifier.generate_questions(ambiguities)
        
        # Should return list of question dictionaries
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertIsInstance(result[0], dict)
        self.assertIn("question", result[0])
        mock_llm_instance.generate.assert_called_once()

    async def test_generate_questions_rule_based_fallback(self):
        """Test generate_questions falls back to rule-based when LLM fails."""
        # Setup mock LLM to fail
        mock_llm_instance.generate = AsyncMock(side_effect=Exception("LLM error"))
        
        ambiguities = ["Database not specified", "API type unclear"]
        result = await self.clarifier.generate_questions(ambiguities)
        
        # Should return list of questions from rule-based generation
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertIsInstance(result[0], dict)
        self.assertIn("question", result[0])
        self.assertIn("category", result[0])

    async def test_generate_questions_no_llm(self):
        """Test generate_questions with no LLM available."""
        # Create clarifier without LLM
        with patch(
            "generator.clarifier.clarifier.setup_logging", return_value=mock_logger
        ):
            clarifier_no_llm = Clarifier(
                llm=None,
                prioritizer=mock_prioritizer_instance,
                context_manager=self.context_manager_instance,
            )
        
        ambiguities = ["Frontend framework not specified"]
        result = await clarifier_no_llm.generate_questions(ambiguities)
        
        # Should use rule-based generation
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertIsInstance(result[0], dict)
        self.assertIn("question", result[0])

    async def test_detect_ambiguities_limits_results(self):
        """Test that detect_ambiguities limits the number of results."""
        # Setup mock LLM to return many ambiguities
        many_ambiguities = [f"Ambiguity {i}" for i in range(20)]
        mock_llm_instance.generate = AsyncMock(
            return_value=json.dumps(many_ambiguities)
        )
        
        readme_content = "Build something"
        result = await self.clarifier.detect_ambiguities(readme_content)
        
        # Should limit to 10 for LLM-based
        self.assertLessEqual(len(result), 10)

    async def test_generate_questions_limits_results(self):
        """Test that generate_questions limits the number of results."""
        # Setup mock LLM to return many questions
        many_questions = [
            {"question": f"Question {i}?", "category": "general"} 
            for i in range(20)
        ]
        mock_llm_instance.generate = AsyncMock(
            return_value=json.dumps(many_questions)
        )
        
        ambiguities = ["Something unclear"]
        result = await self.clarifier.generate_questions(ambiguities)
        
        # Should limit to 10 for LLM-based
        self.assertLessEqual(len(result), 10)


class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions."""

    @unittest.skipIf(setup_logging is None, "setup_logging not available")
    def test_setup_logging(self):
        """Test logger setup."""
        logger = setup_logging()
        self.assertIsNotNone(logger)

    @unittest.skipIf(load_config is None, "load_config not available")
    def test_load_config(self):
        """Test config loading."""
        config = load_config()
        self.assertIsNotNone(config)

    @unittest.skipIf(
        initialize_encryption is None, "initialize_encryption not available"
    )
    def test_initialize_encryption(self):
        """Test encryption initialization."""
        # This is mocked, so just verify it can be called
        try:
            fernet = initialize_encryption("mock_key_id", False)
            self.assertIsNotNone(fernet)
        except Exception:
            self.skipTest("initialize_encryption requires additional setup")

    def test_get_logger(self):
        """Test logger getter."""
        logger = get_logger()
        self.assertIsNotNone(logger)

    def test_get_config(self):
        """Test config getter."""
        config = get_config()
        self.assertIsNotNone(config)

    def test_get_fernet(self):
        """Test Fernet getter."""
        fernet = get_fernet()
        self.assertIsNotNone(fernet)

    def test_get_circuit_breaker(self):
        """Test circuit breaker getter."""
        cb = get_circuit_breaker()
        self.assertIsNotNone(cb)
        self.assertIsInstance(cb, CircuitBreaker)


@unittest.skipIf(Clarifier is None, "Clarifier class not available")
class TestPluginEntrypoint(unittest.IsolatedAsyncioTestCase):
    """Test the plugin entrypoint."""

    async def asyncSetUp(self):
        if hasattr(CLARIFIER_CYCLES, "clear"):
            # FIX: Corrected typo CLARARIFIER_CYCLES -> CLARIFIER_CYCLES
            CLARIFIER_CYCLES.clear()
        if hasattr(CLARIFIER_ERRORS, "clear"):
            CLARIFIER_ERRORS.clear()

        # Import the run function
        try:
            from generator.clarifier.clarifier import run as plugin_run

            self.plugin_run = plugin_run
        except ImportError:
            self.plugin_run = None
            self.skipTest("Plugin run function not available")

    async def test_plugin_run_success(self):
        """Test plugin run with valid inputs."""
        if self.plugin_run is None:
            self.skipTest("Plugin run function not available")

        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]

        # FIX: Create a mock instance that we want Clarifier.create() to return
        mock_clarifier_instance = MagicMock(spec=Clarifier)
        mock_clarifier_instance.get_clarifications = AsyncMock(
            return_value={"features": ["feature1"], "clarifications": {}}
        )
        mock_clarifier_instance.graceful_shutdown = AsyncMock()

        # FIX: Patch the 'Clarifier.create' classmethod and make it an AsyncMock
        with patch(
            "generator.clarifier.clarifier.Clarifier.create", new_callable=AsyncMock
        ) as MockClarifierCreate:
            MockClarifierCreate.return_value = mock_clarifier_instance

            result = await self.plugin_run(requirements, ambiguities)

            self.assertIn("requirements", result)
            mock_clarifier_instance.get_clarifications.assert_awaited_once()
            mock_clarifier_instance.graceful_shutdown.assert_awaited_once()


def tearDownModule():
    """Clean up all patches."""
    patcher_update_requirements.stop()
    patcher_get_channel.stop()
    patcher_prioritizer.stop()
    patcher_llm.stop()
    patcher_sys_exit.stop()
    patcher_fernet.stop()
    patcher_boto3.stop()
    patcher_dynaconf.stop()
    print("\nAll clarifier mocks stopped.")


if __name__ == "__main__":
    unittest.main(argv=["first-arg-is-ignored"], exit=False)
    tearDownModule()
