
# test_clarifier_system.py
"""
Comprehensive enterprise-grade tests for the Clarifier system, covering clarifier.py,
clarifier_prompt.py, clarifier_updater.py, clarifier_llm_call.py, and clarifier_user_prompt.py.
Ensures functionality, security, and compliance for a highly regulated environment.
Created: September 1, 2025.

Requirements:
- pytest: For test execution (`pip install pytest`)
- pytest-asyncio: For async test support (`pip install pytest-asyncio`)
- pytest-mock: For mocking dependencies (`pip install pytest-mock`)
- googletrans==4.0.0-rc1: For translation (`pip install googletrans==4.0.0-rc1`)

Security & Compliance:
- Tests encryption using AWS KMS or fallback Fernet key.
- Verifies logging redaction for sensitive data.
- Ensures history file permissions are 0o600.
- Validates circuit breaker behavior for fault tolerance.
- Tests compliance question handling and user profile storage.
"""

import asyncio
import json
import os
import stat
import pytest
import sqlite3
import time
from typing import Dict, List, Any
from unittest.mock import AsyncMock, patch, MagicMock
from clarifier import Clarifier, get_config, get_fernet, get_logger, get_tracer, get_circuit_breaker, CLARIFIER_CYCLES, CLARIFIER_LATENCY, CLARIFIER_ERRORS, CLARIFIER_QUESTION_PROMPT_LATENCY, CLARIFIER_PRIORITIZATION_LATENCY, CLARIFIER_CONTEXT_RETRIEVAL_LATENCY
from clarifier_prompt import PromptClarifier
from clarifier_updater import RequirementsUpdater, update_requirements_with_answers
from clarifier_llm_call import call_llm_with_fallback
from clarifier_user_prompt import get_channel, UserPromptChannel, store_compliance_answer, load_profile

# pytest-asyncio configuration
pytestmark = pytest.mark.asyncio

# Test fixtures
@pytest.fixture(scope="function")
def clarifier():
    """Fixture to initialize a Clarifier instance."""
    return Clarifier()

@pytest.fixture(scope="function")
def prompt_clarifier():
    """Fixture to initialize a PromptClarifier instance."""
    return PromptClarifier()

@pytest.fixture(scope="function")
def requirements():
    """Sample requirements dictionary."""
    return {"features": ["test feature"], "version": "1.0"}

@pytest.fixture(scope="function")
def ambiguities():
    """Sample ambiguities list."""
    return ["ambiguous term"]

@pytest.fixture(scope="function")
def user_context():
    """Sample user context."""
    return {"user_id": "test_user", "user_email": "test@example.com"}

@pytest.fixture(scope="function")
def mock_channel():
    """Mock UserPromptChannel for testing prompting."""
    channel = AsyncMock(spec=UserPromptChannel)
    channel.prompt = AsyncMock(return_value=["answer"])
    channel.ask_compliance_questions = AsyncMock()
    return channel

@pytest.fixture(scope="function")
def mock_llm():
    """Mock LLM response for prioritization."""
    with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock()) as mock:
        mock.return_value = {
            "content": {
                "prioritized": [{"original": "ambiguous term", "score": 10, "question": "Clarify term?"}],
                "batch": [0]
            },
            "usage": {"prompt_tokens": 5, "completion_tokens": 5}
        }
        yield mock

@pytest.fixture(scope="function")
def mock_kms():
    """Mock AWS KMS client for encryption."""
    with patch('boto3.client') as mock_client:
        mock_kms = MagicMock()
        mock_kms.decrypt.return_value = {"Plaintext": b"dummy_key"}
        mock_client.return_value = mock_kms
        yield mock_kms

@pytest.fixture(scope="function")
def mock_sqlite():
    """Mock SQLiteContextManager for context storage."""
    with patch('clarifier.SQLiteContextManager') as mock:
        mock_instance = AsyncMock()
        mock_instance.query_context.return_value = ["context data"]
        mock_instance.add_to_context.return_value = None
        mock_instance.close.return_value = None
        mock.return_value = mock_instance
        yield mock_instance

@pytest.fixture(scope="function")
def mock_aiofiles():
    """Mock aiofiles for history file operations."""
    with patch('aiofiles.open', new_callable=AsyncMock) as mock:
        yield mock

# Test suite
class TestClarifierSystem:
    """Comprehensive tests for the Clarifier system."""

    async def test_clarifier_full_pipeline(self, clarifier, requirements, ambiguities, user_context, mock_channel, mock_llm, mock_sqlite, mock_aiofiles):
        """Test the full clarification pipeline in clarifier.py."""
        clarifier.interaction = mock_channel
        result = await clarifier.get_clarifications(ambiguities, requirements.copy())
        assert isinstance(result, dict)
        assert mock_channel.prompt.called
        assert mock_channel.prompt.call_args[0][0] == ["Clarify term?"]
        assert mock_channel.prompt.call_args[0][1] == {"user_id": "default"}
        assert mock_llm.called
        assert mock_sqlite.query_context.called
        assert mock_aiofiles.called
        assert CLARIFIER_CYCLES._metrics["clarifier_cycles_total"].labels(status="started")._value > 0
        assert CLARIFIER_LATENCY._metrics["clarifier_latency_seconds"].labels(status="success")._sum > 0

    async def test_prompt_clarifier_full_pipeline(self, prompt_clarifier, requirements, ambiguities, user_context, mock_channel, mock_llm, mock_sqlite, mock_aiofiles):
        """Test the full prompting pipeline in clarifier_prompt.py."""
        prompt_clarifier.interaction = mock_channel
        mock_channel.prompt.side_effect = [["Markdown, PDF"], ["answer"]]  # First for doc formats, then for ambiguities
        result = await prompt_clarifier.get_clarifications(ambiguities, requirements.copy(), user_context)
        assert isinstance(result, dict)
        assert "desired_doc_formats" in result
        assert result["desired_doc_formats"] == ["Markdown", "PDF"]
        assert prompt_clarifier.doc_formats_asked
        assert mock_channel.ask_compliance_questions.called
        assert mock_channel.prompt.call_count == 2  # Doc formats + ambiguities
        assert mock_llm.called
        assert mock_sqlite.query_context.called
        assert mock_aiofiles.called

    async def test_requirements_updater(self, requirements, ambiguities):
        """Test requirements updating in clarifier_updater.py."""
        updater = RequirementsUpdater()
        answers = ["clarified term"]
        with patch('clarifier_updater.redact_sensitive', return_value="clarified term"), \
             patch.object(updater.history_store, 'add_to_history', AsyncMock()):
            result = await updater.update(requirements.copy(), ambiguities, answers)
            assert isinstance(result, dict)
            assert updater.history_store.add_to_history.called
            assert result != requirements  # Ensure requirements were updated

    async def test_llm_call(self, ambiguities):
        """Test LLM call in clarifier_llm_call.py."""
        with patch('clarifier_llm_call.jinja2.Environment') as mock_jinja:
            mock_jinja.return_value.get_template.return_value.render.return_value = json.dumps({
                "prioritized": [{"original": ambiguities[0], "score": 10, "question": "Clarify term?"}],
                "batch": [0]
            })
            result = await call_llm_with_fallback('grok', {'ambiguities_list': ambiguities}, target_language='en')
            assert "content" in result
            assert result["content"]["prioritized"][0]["original"] == ambiguities[0]
            assert "batch" in result["content"]
            assert CLARIFIER_PRIORITIZATION_LATENCY._metrics["clarifier_prioritization_seconds"].labels(strategy="default")._sum > 0

    async def test_user_prompt_channel(self, user_context):
        """Test user prompting in clarifier_user_prompt.py."""
        channel = get_channel('cli', target_language='en')
        questions = ["Clarify term?"]
        with patch('builtins.input', side_effect=["answer"]):
            answers = await channel.prompt(questions, user_context, 'en')
            assert answers == ["answer"]
        with patch.object(channel, 'prompt', AsyncMock(return_value=["answer"])):
            await channel.ask_compliance_questions(user_context["user_id"], user_context)
            assert channel.prompt.called

    async def test_encryption(self, clarifier, mock_kms, mock_aiofiles):
        """Test history encryption in clarifier.py."""
        clarifier.history = [{"test": "data"}]
        await clarifier._save_history()
        assert mock_kms.decrypt.called
        assert mock_aiofiles.called
        assert os.path.exists(get_config().HISTORY_FILE)
        assert (os.stat(get_config().HISTORY_FILE).st_mode & 0o777) == 0o600

    async def test_logging_redaction(self, clarifier):
        """Test logging redaction in clarifier.py."""
        with patch('logging.Logger.info') as mock_log:
            clarifier.logger.info("Sensitive API_KEY data", extra={"user_input": "secret"})
            mock_log.assert_called_with("Sensitive ***REDACTED_API_KEY*** data", extra={"user_input": "***REDACTED***"})

    async def test_circuit_breaker(self, clarifier, mock_channel):
        """Test circuit breaker behavior in clarifier.py."""
        clarifier.circuit_breaker._tripped = True
        clarifier.circuit_breaker._trip_time = time.time()
        with pytest.raises(Exception, match="Operation aborted by circuit breaker"):
            await clarifier.get_clarifications(["term"], {"features": []})
        clarifier.circuit_breaker._tripped = False
        clarifier.circuit_breaker._error_count = 0
        clarifier.interaction = mock_channel
        await clarifier.get_clarifications(["term"], {"features": []})  # Should succeed
        assert clarifier.circuit_breaker._error_count == 0

    async def test_compliance_questions(self, user_context):
        """Test compliance question handling in clarifier_user_prompt.py."""
        with patch('clarifier_user_prompt.save_profile'), \
             patch('clarifier_user_prompt.load_profile', return_value=MagicMock(compliance_preferences={})):
            store_compliance_answer(user_context["user_id"], "gdpr_apply", True)
            profile = load_profile(user_context["user_id"])
            assert profile.compliance_preferences["gdpr_apply"] is True

    async def test_translation(self, prompt_clarifier):
        """Test translation in clarifier_prompt.py."""
        with patch('googletrans.Translator.translate', return_value=MagicMock(text="¿Clarificar término?")):
            result = prompt_clarifier._translate_text("Clarify term?", "es")
            assert result == "¿Clarificar término?"

    async def test_error_handling(self, clarifier, mock_channel):
        """Test error handling in clarifier.py."""
        mock_channel.prompt.side_effect = Exception("Prompt error")
        with pytest.raises(Exception, match="Prompt error"):
            await clarifier.get_clarifications(["term"], {"features": []})
        assert CLARIFIER_ERRORS._metrics["clarifier_errors_total"].labels(error_type="clarification_cycle_failed")._value > 0

    async def test_graceful_shutdown(self, clarifier, mock_sqlite, mock_aiofiles):
        """Test graceful shutdown in clarifier.py."""
        clarifier.history = [{"test": "data"}]
        with patch('asyncio.all_tasks', return_value=[MagicMock(cancel=MagicMock())]):
            await clarifier.graceful_shutdown("test")
            assert clarifier.shutdown_event.is_set()
            assert mock_sqlite.close.called
            assert mock_aiofiles.called

    async def test_jinja_templating(self, ambiguities):
        """Test Jinja2 templating in clarifier_llm_call.py."""
        with patch('clarifier_llm_call._jinja_env') as mock_jinja:
            mock_jinja.get_template.return_value.render.return_value = json.dumps({
                "prioritized": [{"original": ambiguities[0], "score": 10, "question": "Clarify term?"}],
                "batch": [0]
            })
            result = await call_llm_with_fallback('grok', {'ambiguities_list': ambiguities}, target_language='en')
            assert mock_jinja.get_template.called
            assert result["content"]["prioritized"][0]["original"] == ambiguities[0]

    async def test_configuration(self, clarifier, prompt_clarifier):
        """Test configuration consistency across modules."""
        config = get_config()
        assert clarifier.config == config
        assert prompt_clarifier.config == config
        assert config.LLM_PROVIDER == "grok"
        assert config.INTERACTION_MODE == "cli"

    async def test_file_permissions(self, clarifier, mock_aiofiles):
        """Test history file permissions in clarifier.py."""
        clarifier.history = [{"test": "data"}]
        with patch('os.chmod') as mock_chmod:
            await clarifier._save_history()
            mock_chmod.assert_called_with(get_config().HISTORY_FILE, stat.S_IREAD | stat.S_IWRITE)

if __name__ == '__main__':
    pytest.main(["-v", "--asyncio-mode=auto"])
