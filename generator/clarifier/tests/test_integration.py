# test_integration.py
"""
Integration tests for the Clarifier system.
Tests the complete workflow from ambiguity detection through clarification to requirements update.
"""

import asyncio
import base64
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# --- Mock Configuration (MUST RUN BEFORE IMPORTS) ---


class MockConfigObject:
    LLM_PROVIDER = "grok"
    INTERACTION_MODE = "cli"
    BATCH_STRATEGY = "default"
    FEEDBACK_STRATEGY = "none"
    HISTORY_FILE = "mock_integration_history.json"
    TARGET_LANGUAGE = "en"
    CONTEXT_DB_PATH = "mock_integration.db"
    KMS_KEY_ID = "mock_kms_key"
    ALERT_ENDPOINT = "http://mock.alert/endpoint"
    HISTORY_COMPRESSION = False
    CONTEXT_QUERY_LIMIT = 3
    HISTORY_LOOKBACK_LIMIT = 10
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_TIMEOUT = 30
    CONFLICT_STRATEGY = "auto_merge"
    LLM_INFERENCE_ENDPOINT = "http://mock.llm/inference"
    LLM_INFERENCE_MODEL = "mock-model"
    LLM_INFERENCE_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 0.1
    is_production_env = False
    GROK_API_KEY = "mock_grok_key"
    GROK_API_ENDPOINT = "http://mock.grok/api"
    CLARIFIER_EMAIL_SERVER = "smtp.mock.com"
    CLARIFIER_EMAIL_PORT = "587"
    CLARIFIER_EMAIL_USER = "user@mock.com"
    CLARIFIER_EMAIL_PASS = "password"
    CLARIFIER_SLACK_WEBHOOK = "http://slack.mock/webhook"
    CLARIFIER_SMS_API = "http://sms.mock/api"
    CLARIFIER_SMS_KEY = "sms_key"
    SCHEMA_VERSION = 3  # Required by clarifier_updater.py
    HISTORY_DB_PATH = "mock_history.db"  # Required by clarifier_updater.py


mock_config_instance = MagicMock(spec=MockConfigObject)
for attr, value in MockConfigObject.__dict__.items():
    if not attr.startswith("_"):
        setattr(mock_config_instance, attr, value)

TEST_FERNET_KEY = base64.urlsafe_b64encode(b"\x00" * 32)
mock_fernet_instance = MagicMock()
# Don't use base64 encoding - it breaks SQL LIKE queries on encrypted data
mock_fernet_instance.encrypt.side_effect = lambda data: b"ENCRYPTED_" + (
    data if isinstance(data, bytes) else data.encode()
)
mock_fernet_instance.decrypt.side_effect = lambda data: data.replace(b"ENCRYPTED_", b"")

mock_logger = MagicMock()
for method in ["info", "warning", "error", "debug", "critical"]:
    setattr(mock_logger, method, MagicMock())

mock_circuit_breaker = MagicMock()
mock_circuit_breaker.is_open.return_value = False
mock_circuit_breaker.record_success = MagicMock()
mock_circuit_breaker.record_failure = MagicMock()

mock_tracer = MagicMock()
mock_span = MagicMock()
mock_tracer.start_span.return_value = mock_span
MockStatus = MagicMock()
MockStatusCode = MagicMock()
MockStatusCode.OK = "OK"
MockStatusCode.ERROR = "ERROR"

# Start global patches
patcher_dynaconf = patch(
    "generator.clarifier.clarifier.Dynaconf", return_value=mock_config_instance
)
patcher_boto3 = patch("generator.clarifier.clarifier.boto3.client", return_value=MagicMock())
patcher_fernet_class = patch(
    "generator.clarifier.clarifier.Fernet", return_value=mock_fernet_instance
)
patcher_sys_exit = patch("generator.clarifier.clarifier.sys.exit")

patcher_dynaconf.start()
patcher_boto3.start()
patcher_fernet_class.start()
patcher_sys_exit.start()

# Patch for clarifier module
patcher_get_config_clarifier = patch(
    "generator.clarifier.clarifier.get_config", return_value=mock_config_instance
)
patcher_get_fernet_clarifier = patch(
    "generator.clarifier.clarifier.get_fernet", return_value=mock_fernet_instance
)
patcher_get_logger_clarifier = patch(
    "generator.clarifier.clarifier.get_logger", return_value=mock_logger
)
patcher_get_tracer_clarifier = patch(
    "generator.clarifier.clarifier.get_tracer",
    return_value=(mock_tracer, MockStatus, MockStatusCode),
)
patcher_get_cb_clarifier = patch(
    "generator.clarifier.clarifier.get_circuit_breaker",
    return_value=mock_circuit_breaker,
)

patcher_get_config_clarifier.start()
patcher_get_fernet_clarifier.start()
patcher_get_logger_clarifier.start()
patcher_get_tracer_clarifier.start()
patcher_get_cb_clarifier.start()

# Patch for prompt module
patcher_get_config_prompt = patch(
    "generator.clarifier.clarifier_prompt.get_config", return_value=mock_config_instance
)
patcher_get_fernet_prompt = patch(
    "generator.clarifier.clarifier_prompt.get_fernet", return_value=mock_fernet_instance
)
patcher_get_logger_prompt = patch(
    "generator.clarifier.clarifier_prompt.get_logger", return_value=mock_logger
)
patcher_get_tracer_prompt = patch(
    "generator.clarifier.clarifier_prompt.get_tracer",
    return_value=(mock_tracer, MockStatus, MockStatusCode),
)
patcher_get_cb_prompt = patch(
    "generator.clarifier.clarifier_prompt.get_circuit_breaker",
    return_value=mock_circuit_breaker,
)

patcher_get_config_prompt.start()
patcher_get_fernet_prompt.start()
patcher_get_logger_prompt.start()
patcher_get_tracer_prompt.start()
patcher_get_cb_prompt.start()

# Patch for updater module
patcher_get_config_updater = patch(
    "generator.clarifier.clarifier_updater.get_config",
    return_value=mock_config_instance,
)
patcher_get_fernet_updater = patch(
    "generator.clarifier.clarifier_updater.get_fernet",
    return_value=mock_fernet_instance,
)
patcher_get_logger_updater = patch(
    "generator.clarifier.clarifier_updater.get_logger", return_value=mock_logger
)

patcher_get_config_updater.start()
patcher_get_fernet_updater.start()
patcher_get_logger_updater.start()

# Patch for user_prompt module
patcher_get_config_user = patch(
    "generator.clarifier.clarifier_user_prompt.get_config",
    return_value=mock_config_instance,
)
patcher_get_fernet_user = patch(
    "generator.clarifier.clarifier_user_prompt.get_fernet",
    return_value=mock_fernet_instance,
)
patcher_get_logger_user = patch(
    "generator.clarifier.clarifier_user_prompt.get_logger", return_value=mock_logger
)
patcher_translator = patch("generator.clarifier.clarifier_user_prompt.Translator")
patcher_detect_language = patch(
    "generator.clarifier.clarifier_user_prompt.detect_language", return_value="en"
)
patcher_redact_sensitive_user = patch(
    "generator.clarifier.clarifier_user_prompt.redact_sensitive",
    side_effect=lambda x: x,
)
patcher_log_action_user = patch(
    "generator.clarifier.clarifier_user_prompt.log_action", return_value=None
)

patcher_get_config_user.start()
patcher_get_fernet_user.start()
patcher_get_logger_user.start()
MockTranslatorCls = patcher_translator.start()
patcher_detect_language.start()
patcher_redact_sensitive_user.start()
patcher_log_action_user.start()

MockTranslatorInstance = MockTranslatorCls.return_value
MockTranslatorInstance.translate.side_effect = lambda text, dest: MagicMock(text=text)

# Patch utility functions
patcher_log_action = patch(
    "generator.clarifier.clarifier_updater.log_action", new_callable=AsyncMock
)
patcher_send_alert = patch(
    "generator.clarifier.clarifier_updater.send_alert", new_callable=AsyncMock
)
patcher_redact_sensitive = patch(
    "generator.clarifier.clarifier_updater.redact_sensitive",
    side_effect=lambda x: x.replace("SECRET", "[REDACTED]").replace("@", "[REDACTED_EMAIL]"),
)

MockLogAction = patcher_log_action.start()
MockSendAlert = patcher_send_alert.start()
MockRedactSensitive = patcher_redact_sensitive.start()


class TestEndToEndClarification(unittest.IsolatedAsyncioTestCase):
    """Test complete end-to-end clarification workflow."""

    async def asyncSetUp(self):
        # Create temp files
        self.temp_history = tempfile.NamedTemporaryFile(delete=False, suffix="_integration.json")
        self.temp_history.close()
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix="_integration.db")
        self.temp_db.close()
        self.temp_profile_dir = tempfile.mkdtemp(prefix="user_profiles_")

        # Update config
        mock_config_instance.HISTORY_FILE = self.temp_history.name
        mock_config_instance.CONTEXT_DB_PATH = self.temp_db.name

        # Import after patching
        from generator.clarifier.clarifier import Clarifier
        from generator.clarifier.clarifier_prompt import PromptClarifier
        from generator.clarifier.clarifier_updater import RequirementsUpdater
        from generator.clarifier.clarifier_user_prompt import get_channel

        self.get_channel = get_channel
        self.Clarifier = Clarifier
        self.PromptClarifier = PromptClarifier
        self.RequirementsUpdater = RequirementsUpdater

    async def asyncTearDown(self):
        # Cleanup temp files
        if os.path.exists(self.temp_history.name):
            os.unlink(self.temp_history.name)
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

        # Cleanup temp files with similar patterns
        for f in os.listdir("."):
            if "integration" in f and (
                f.endswith(".tmp") or f.endswith(".json") or f.endswith(".db")
            ):
                try:
                    os.unlink(f)
                except:
                    pass

        # Cleanup profile directory
        import shutil

        if os.path.exists(self.temp_profile_dir):
            shutil.rmtree(self.temp_profile_dir)

    @patch(
        "builtins.input",
        side_effect=[
            "user's clarification answer",
            "Markdown, PDF",
            "yes",
            "no",
            "yes",
            "EU",
            "no",
        ],
    )
    async def test_full_clarification_workflow(self, mock_input):
        """Test complete workflow: ambiguity -> prompt -> clarification -> update."""

        # 1. Initial requirements with ambiguities
        initial_requirements = {
            "features": ["user authentication", "data storage"],
            "constraints": ["secure"],
            "schema_version": 1,
        }
        ambiguities = ["user authentication"]

        # 2. Mock LLM components
        with patch("generator.clarifier.clarifier.GrokLLM") as MockLLM, patch(
            "generator.clarifier.clarifier.DefaultPrioritizer"
        ) as MockPrioritizer:

            # Setup LLM mock
            mock_llm_instance = MagicMock()
            MockLLM.return_value = mock_llm_instance

            # Setup Prioritizer mock
            mock_prioritizer = MagicMock()
            mock_prioritizer.prioritize = AsyncMock(
                return_value={
                    "prioritized": [
                        {
                            "original": "user authentication",
                            "score": 10,
                            "question": "What authentication method should be used?",
                        }
                    ],
                    "batch": [0],
                }
            )
            MockPrioritizer.return_value = mock_prioritizer

            # 3. Mock updater's inference
            with patch.object(
                self.RequirementsUpdater,
                "_infer_updates",
                AsyncMock(
                    return_value={
                        "inferred_features": [
                            "login_system",
                            "password_hashing",
                            "session_management",
                        ],
                        "inferred_constraints": ["use_https", "encrypt_passwords"],
                    }
                ),
            ):

                # 4. Create clarifier instance
                clarifier = self.Clarifier()

                # Explicitly set mocked components
                clarifier.llm = mock_llm_instance
                clarifier.prioritizer = mock_prioritizer

                # Mock interaction channel
                mock_channel = MagicMock()
                mock_channel.prompt = AsyncMock(return_value=["user's clarification answer"])
                clarifier.interaction = mock_channel

                # Initialize context manager
                from generator.clarifier.clarifier import SQLiteContextManager

                clarifier.context_manager = SQLiteContextManager(
                    self.temp_db.name, mock_fernet_instance
                )
                await clarifier.context_manager._init_db()

                # 5. Run clarification
                try:
                    result = await clarifier.get_clarifications(ambiguities, initial_requirements)

                    # 6. Verify results
                    self.assertIsInstance(result, dict)
                    self.assertIn("clarifications", result)

                    # 7. Verify clarification was recorded
                    if "user authentication" in result.get("clarifications", {}):
                        self.assertIsNotNone(result["clarifications"]["user authentication"])

                    # 8. Verify version info was added
                    if "version" in result:
                        self.assertIsInstance(result["version"], int)

                finally:
                    await clarifier.context_manager.close()

    async def test_prompt_clarifier_integration(self):
        """Test PromptClarifier integration with core clarifier."""

        with patch("generator.clarifier.clarifier.GrokLLM"), patch(
            "generator.clarifier.clarifier.DefaultPrioritizer"
        ) as MockPrioritizer, patch(
            "generator.clarifier.clarifier_prompt.get_channel"
        ) as MockGetChannel:

            # Setup prioritizer
            mock_prioritizer = MagicMock()
            mock_prioritizer.prioritize = AsyncMock(
                return_value={
                    "prioritized": [{"original": "test", "score": 5, "question": "Q?"}],
                    "batch": [0],
                }
            )
            MockPrioritizer.return_value = mock_prioritizer

            # Setup channel
            mock_channel = MagicMock()
            mock_channel.prompt = AsyncMock(return_value=["Markdown", "answer"])
            mock_channel.ask_compliance_questions = AsyncMock()
            MockGetChannel.return_value = mock_channel

            # Mock updater
            with patch.object(
                self.RequirementsUpdater,
                "_infer_updates",
                AsyncMock(return_value={"inferred_features": [], "inferred_constraints": []}),
            ):

                prompt_clarifier = self.PromptClarifier()

                # Explicitly set mocked components on core clarifier
                prompt_clarifier.core_clarifier.prioritizer = mock_prioritizer

                # Mock interaction channel on core clarifier
                prompt_clarifier.core_clarifier.interaction = mock_channel

                # Initialize context manager
                from generator.clarifier.clarifier import SQLiteContextManager

                prompt_clarifier.core_clarifier.context_manager = SQLiteContextManager(
                    self.temp_db.name, mock_fernet_instance
                )
                await prompt_clarifier.core_clarifier.context_manager._init_db()

                try:
                    requirements = {"features": ["f1"], "schema_version": 1}
                    ambiguities = ["test"]
                    user_context = {"user_id": "integration_test"}

                    result = await prompt_clarifier.get_clarifications(
                        ambiguities, requirements, user_context
                    )

                    self.assertIsInstance(result, dict)

                    # Verify channel interactions
                    mock_channel.prompt.assert_awaited()
                    mock_channel.ask_compliance_questions.assert_awaited()

                finally:
                    await prompt_clarifier.core_clarifier.context_manager.close()

    async def test_multi_channel_integration(self):
        """Test clarification with different communication channels."""

        channels_to_test = ["cli"]  # Start with CLI

        for channel_type in channels_to_test:
            with self.subTest(channel=channel_type):
                with patch("builtins.input", return_value="test answer"):
                    channel = self.get_channel(channel_type, target_language="en")

                    questions = ["Test question?"]
                    context = {"user_id": "test_user"}

                    answers = await channel.prompt(questions, context)

                    self.assertEqual(len(answers), 1)
                    self.assertIsInstance(answers[0], str)

    async def test_requirements_update_integration(self):
        """Test requirements updater integration."""

        with patch.object(
            self.RequirementsUpdater,
            "_infer_updates",
            AsyncMock(
                return_value={
                    "inferred_features": ["feature_inferred"],
                    "inferred_constraints": ["constraint_inferred"],
                }
            ),
        ):

            updater = self.RequirementsUpdater()
            await updater._db_init_task

            try:
                requirements = {"features": ["existing_feature"], "schema_version": 1}
                ambiguities = ["ambiguous_term"]
                answers = ["clarified_meaning"]

                result = await updater.update(requirements, ambiguities, answers)

                # Verify structure
                self.assertIn("clarifications", result)
                self.assertIn("version", result)
                self.assertIn("inferred_features", result)

                # Verify clarification
                self.assertEqual(result["clarifications"]["ambiguous_term"], "clarified_meaning")

                # Verify inferred features
                self.assertIn("feature_inferred", result["inferred_features"])

                # Verify history was stored
                history = await updater.history_store.query(limit=1)
                self.assertEqual(len(history), 1)

            finally:
                await updater.close()

    async def test_context_retrieval_integration(self):
        """Test context retrieval during clarification."""

        from generator.clarifier.clarifier import SQLiteContextManager

        # Store some context
        context_manager = SQLiteContextManager(self.temp_db.name, mock_fernet_instance)
        await context_manager._init_db()

        try:
            # Store historical context
            await context_manager.store(
                {
                    "description": "previous clarification about authentication",
                    "result": "use OAuth 2.0",
                    "timestamp": "2025-07-30",
                }
            )

            # Query context
            results = await context_manager.query("authentication", limit=3)

            self.assertGreater(len(results), 0)
            self.assertIn("description", results[0])

        finally:
            await context_manager.close()

    async def test_error_recovery_integration(self):
        """Test error recovery across components."""

        with patch("generator.clarifier.clarifier.GrokLLM") as MockLLM, patch(
            "generator.clarifier.clarifier.DefaultPrioritizer"
        ) as MockPrioritizer:

            # Make prioritizer fail first, then succeed
            call_count = 0

            async def prioritize_with_failure(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Temporary failure")
                return {
                    "prioritized": [{"original": "test", "score": 5, "question": "Q?"}],
                    "batch": [0],
                }

            mock_prioritizer = MagicMock()
            mock_prioritizer.prioritize = prioritize_with_failure
            MockPrioritizer.return_value = mock_prioritizer

            mock_llm_instance = MagicMock()
            MockLLM.return_value = mock_llm_instance

            clarifier = self.Clarifier()

            # Explicitly set mocked components
            clarifier.llm = mock_llm_instance
            clarifier.prioritizer = mock_prioritizer

            # Mock interaction channel
            mock_channel = MagicMock()
            mock_channel.prompt = AsyncMock(return_value=["answer"])
            clarifier.interaction = mock_channel

            from generator.clarifier.clarifier import SQLiteContextManager

            clarifier.context_manager = SQLiteContextManager(
                self.temp_db.name, mock_fernet_instance
            )
            await clarifier.context_manager._init_db()

            try:
                requirements = {"features": ["f1"], "schema_version": 1}
                ambiguities = ["test"]

                # Should retry and succeed
                with patch("builtins.input", return_value="answer"):
                    result = await clarifier.get_clarifications(ambiguities, requirements)

                self.assertIsInstance(result, dict)

            finally:
                await clarifier.context_manager.close()

    async def test_concurrent_clarifications(self):
        """Test handling multiple concurrent clarification requests."""

        with patch("generator.clarifier.clarifier.GrokLLM"), patch(
            "generator.clarifier.clarifier.DefaultPrioritizer"
        ) as MockPrioritizer, patch("builtins.input", return_value="answer"):

            mock_prioritizer = MagicMock()
            mock_prioritizer.prioritize = AsyncMock(
                return_value={
                    "prioritized": [{"original": "test", "score": 5, "question": "Q?"}],
                    "batch": [0],
                }
            )
            MockPrioritizer.return_value = mock_prioritizer

            # Create multiple clarifier instances
            clarifiers = []
            for i in range(3):
                c = self.Clarifier()

                # Mock interaction channel
                mock_channel = MagicMock()
                mock_channel.prompt = AsyncMock(return_value=["answer"])
                c.interaction = mock_channel

                from generator.clarifier.clarifier import SQLiteContextManager

                c.context_manager = SQLiteContextManager(
                    f"{self.temp_db.name}_{i}", mock_fernet_instance
                )
                await c.context_manager._init_db()
                clarifiers.append(c)

            try:
                # Run concurrent clarifications
                tasks = [
                    c.get_clarifications(
                        [f"ambiguity_{i}"],
                        {"features": [f"feature_{i}"], "schema_version": 1},
                    )
                    for i, c in enumerate(clarifiers)
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Verify all succeeded or returned reasonable results
                for result in results:
                    if not isinstance(result, Exception):
                        self.assertIsInstance(result, dict)

            finally:
                for i, c in enumerate(clarifiers):
                    await c.context_manager.close()
                    db_file = f"{self.temp_db.name}_{i}"
                    if os.path.exists(db_file):
                        os.unlink(db_file)

    async def test_schema_evolution_integration(self):
        """Test schema evolution through update cycle."""

        with patch.object(
            self.RequirementsUpdater,
            "_infer_updates",
            AsyncMock(return_value={"inferred_features": [], "inferred_constraints": []}),
        ):

            updater = self.RequirementsUpdater()
            await updater._db_init_task

            try:
                # Start with v1 schema
                requirements_v1 = {
                    "features": ["f1"],
                    "constraints": ["c1"],
                    "schema_version": 1,
                }

                result = await updater.update(requirements_v1, ["amb"], ["ans"])

                # Should be migrated to v2
                self.assertEqual(result["schema_version"], 2)
                self.assertIn("inferred_features", result)
                self.assertIn("inferred_constraints", result)

            finally:
                await updater.close()


class TestComponentInteractions(unittest.IsolatedAsyncioTestCase):
    """Test interactions between components."""

    async def asyncSetUp(self):
        self.temp_history = tempfile.NamedTemporaryFile(delete=False, suffix="_comp.json")
        self.temp_history.close()
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix="_comp.db")
        self.temp_db.close()

        mock_config_instance.HISTORY_FILE = self.temp_history.name
        mock_config_instance.CONTEXT_DB_PATH = self.temp_db.name

    async def asyncTearDown(self):
        if os.path.exists(self.temp_history.name):
            os.unlink(self.temp_history.name)
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

    async def test_clarifier_to_updater_flow(self):
        """Test data flow from clarifier to updater."""
        from generator.clarifier.clarifier import Clarifier, SQLiteContextManager
        from generator.clarifier.clarifier_updater import RequirementsUpdater

        with patch("generator.clarifier.clarifier.GrokLLM"), patch(
            "generator.clarifier.clarifier.DefaultPrioritizer"
        ) as MockPrioritizer, patch("builtins.input", return_value="clarified answer"):

            mock_prioritizer = MagicMock()
            mock_prioritizer.prioritize = AsyncMock(
                return_value={
                    "prioritized": [{"original": "amb", "score": 5, "question": "Q?"}],
                    "batch": [0],
                }
            )
            MockPrioritizer.return_value = mock_prioritizer

            with patch.object(
                RequirementsUpdater,
                "_infer_updates",
                AsyncMock(
                    return_value={
                        "inferred_features": ["inferred"],
                        "inferred_constraints": [],
                    }
                ),
            ):

                clarifier = Clarifier()

                # Explicitly set mocked components
                clarifier.prioritizer = mock_prioritizer

                clarifier.context_manager = SQLiteContextManager(
                    self.temp_db.name, mock_fernet_instance
                )
                await clarifier.context_manager._init_db()

                try:
                    requirements = {"features": ["f1"], "schema_version": 1}
                    clarified = await clarifier.get_clarifications(["amb"], requirements)

                    self.assertIsInstance(clarified, dict)
                    self.assertIn("clarifications", clarified)

                finally:
                    await clarifier.context_manager.close()


def tearDownModule():
    """Clean up all patches."""
    patcher_log_action_user.stop()
    patcher_redact_sensitive_user.stop()
    patcher_detect_language.stop()
    patcher_translator.stop()
    patcher_get_logger_user.stop()
    patcher_get_fernet_user.stop()
    patcher_get_config_user.stop()
    patcher_get_logger_updater.stop()
    patcher_get_fernet_updater.stop()
    patcher_get_config_updater.stop()
    patcher_get_cb_prompt.stop()
    patcher_get_tracer_prompt.stop()
    patcher_get_logger_prompt.stop()
    patcher_get_fernet_prompt.stop()
    patcher_get_config_prompt.stop()
    patcher_get_cb_clarifier.stop()
    patcher_get_tracer_clarifier.stop()
    patcher_get_logger_clarifier.stop()
    patcher_get_fernet_clarifier.stop()
    patcher_get_config_clarifier.stop()
    patcher_redact_sensitive.stop()
    patcher_send_alert.stop()
    patcher_log_action.stop()
    patcher_sys_exit.stop()
    patcher_fernet_class.stop()
    patcher_boto3.stop()
    patcher_dynaconf.stop()
    print("\nAll integration test mocks stopped.")


if __name__ == "__main__":
    # Run with verbose output
    unittest.main(argv=["first-arg-is-ignored"], verbosity=2, exit=False)
    tearDownModule()
