import pytest
import os
import json
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock

# Import objects that are safe to load at the module level
from test_generation.policy_and_audit import (
    redact_sensitive,
    PolicyEngine,
    AuditLogger,
    EventBus,
)


# Mark all tests as unit tests for selective running
pytestmark = pytest.mark.unit


@pytest.fixture
def temp_project_root():
    """Fixture for a temporary project root directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_config():
    """Fixture for a mock ATCO configuration."""
    return {
        "opa_retries": 3,
        "opa_backoff_min": 2,
        "opa_backoff_max": 10,
        "notification_retries": 3,
        "notification_backoff_min": 2,
        "notification_backoff_max": 10,
        "notification_timeout_seconds": 10,
        "slack_webhook_url": "http://mock-slack",
        "slack_events": ["test_event"],
        "webhook_hooks": {"test_hook": "http://mock-webhook"},
        "webhook_events": ["test_event"],
        "critical_events_for_mq": ["critical_event"],
    }


@pytest.fixture
def mock_policy_file(temp_project_root):
    """Fixture for a mock policy JSON file."""
    policy_path = os.path.join(temp_project_root, "atco_policies.json")
    policy_content = {
        "generation_rules": {
            "regulated_modules": ["financial_data"],
            "allowed_languages": ["python"],
            "safe_subfolders": ["src"],
        },
        "integration_rules": {
            "min_test_quality_score": 0.7,
            "deny_integrate_modules": ["legacy"],
            "human_review_required_languages": ["javascript"],
            "require_human_review_modules": [],
        },
        "security_scan_threshold": "HIGH",
        "opa_integration_enabled": False,
    }
    with open(policy_path, "w", encoding="utf-8") as f:
        json.dump(policy_content, f)
    yield policy_path


# --- Tests for redact_sensitive ---


@pytest.mark.parametrize(
    "input_data, expected_output",
    [
        ({"key": "value"}, {"key": "value"}),  # No sensitive keys
        ({"password": "secret123"}, {"password": "[REDACTED]"}),  # Sensitive key
        (
            {"api_key": "abc123", "nested": {"secret": "hidden"}},
            {"api_key": "[REDACTED]", "nested": {"secret": "[REDACTED]"}},
        ),  # Nested
        ([{"token": "tok123"}, "normal"], [{"token": "[REDACTED]"}, "normal"]),  # List
        ("non-dict", "non-dict"),  # Non-dict/list
    ],
)
def test_redact_sensitive(input_data, expected_output):
    """Test sensitive data redaction."""
    assert redact_sensitive(input_data) == expected_output


# --- Tests for PolicyEngine ---


def test_policy_engine_init_success(mock_policy_file, temp_project_root):
    """Test successful initialization of PolicyEngine with valid config."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    assert engine.policy_config_path == "atco_policies.json"
    assert engine.policy_hash != "NO_POLICY_FILE"
    assert "generation_rules" in engine.policies


def test_policy_engine_init_invalid_path(temp_project_root):
    """Test initialization fails with invalid policy path."""
    with pytest.raises(ValueError, match="Invalid policy_config_path"):
        PolicyEngine("../invalid.json", temp_project_root)


def test_policy_engine_init_missing_file(temp_project_root):
    """Test initialization with missing policy file uses defaults."""
    engine = PolicyEngine("missing.json", temp_project_root)
    assert engine.policies["generation_rules"]["allowed_languages"] == [
        "python",
        "javascript",
        "java",
        "typescript",
        "rust",
        "go",
    ]


@pytest.mark.asyncio
async def test_policy_engine_should_generate_tests_local_allowed(
    mock_policy_file, temp_project_root
):
    """Test local policy allows test generation."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    allowed, reason = await engine.should_generate_tests("src/module.py", "python")
    assert allowed
    assert "Policy allows" in reason


@pytest.mark.asyncio
async def test_policy_engine_should_generate_tests_local_denied_regulated(
    mock_policy_file, temp_project_root
):
    """Test local policy denies regulated module."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    allowed, reason = await engine.should_generate_tests("financial_data/module.py", "python")
    assert not allowed
    assert "regulated modules" in reason


@pytest.mark.asyncio
async def test_policy_engine_should_generate_tests_opa_enabled(
    mock_policy_file, temp_project_root, mock_config
):
    """Test OPA-enabled policy evaluation."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    engine.policies["opa_integration_enabled"] = True

    with patch("test_generation.policy_and_audit.aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"result": True, "reason": "OPA allowed"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value.__aenter__.return_value = mock_response

        allowed, reason = await engine.should_generate_tests("module.py", "python")
        assert allowed
        assert "OPA allowed" in reason


@pytest.mark.asyncio
async def test_policy_engine_should_generate_tests_opa_failure(mock_policy_file, temp_project_root):
    """Test OPA failure defaults to deny."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    engine.policies["opa_integration_enabled"] = True

    with patch(
        "test_generation.policy_and_audit.aiohttp.ClientSession.post",
        side_effect=Exception("OPA error"),
    ):
        allowed, reason = await engine.should_generate_tests("module.py", "python")
        assert not allowed
        assert "OPA policy evaluation failed" in reason


@pytest.mark.asyncio
async def test_policy_engine_should_integrate_test_local_allowed(
    mock_policy_file, temp_project_root
):
    """Test local policy allows integration."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    allowed, reason = await engine.should_integrate_test("module.py", 0.8, "python", False, "NONE")
    assert allowed
    assert "Policy allows" in reason


@pytest.mark.asyncio
async def test_policy_engine_should_integrate_test_local_denied_quality(
    mock_policy_file, temp_project_root
):
    """Test local policy denies low quality score."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    allowed, reason = await engine.should_integrate_test("module.py", 0.6, "python", False, "NONE")
    assert not allowed
    assert "below minimum required" in reason


@pytest.mark.asyncio
async def test_policy_engine_requires_pr_for_integration_local_required(
    mock_policy_file, temp_project_root
):
    """Test local policy requires PR for language."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    requires_pr, reason = await engine.requires_pr_for_integration("module.js", "javascript", 0.8)
    assert requires_pr
    assert "Human review required" in reason


@pytest.mark.asyncio
async def test_policy_engine_requires_pr_for_integration_local_not_required(
    mock_policy_file, temp_project_root
):
    """Test local policy does not require PR."""
    engine = PolicyEngine("atco_policies.json", temp_project_root)
    requires_pr, reason = await engine.requires_pr_for_integration("module.py", "python", 0.9)
    assert not requires_pr
    assert "Direct integration allowed" in reason


@pytest.mark.asyncio
async def test_policy_engine_metrics(mock_policy_file, temp_project_root):
    """Test Prometheus metrics for policy evaluations."""
    # Fix: Import metric inside the function to avoid circular dependency

    engine = PolicyEngine("atco_policies.json", temp_project_root)

    with patch("test_generation.policy_and_audit.policy_evaluations_total") as mock_counter:
        await engine.should_generate_tests("module.py", "python")
        mock_counter.labels.assert_called_with(result="allowed", rule="generation_rules_local")


# --- Tests for AuditLogger ---


def test_audit_logger_init_success(temp_project_root):
    """Test successful initialization of AuditLogger."""
    logger = AuditLogger("audit.log")
    assert logger.log_file_relative == "audit.log"


def test_audit_logger_init_no_dlt():
    """Test initialization fails without DLTLogger."""
    with patch("test_generation.policy_and_audit.AUDIT_LOGGER_AVAILABLE", False):
        with pytest.raises(ImportError, match="DLT-enabled AuditLogger not available"):
            AuditLogger("audit.log")


@pytest.mark.asyncio
async def test_audit_logger_log_event(temp_project_root):
    """Test logging an event with DLT and local logger."""
    logger = AuditLogger("audit.log")

    with patch.object(logger.dlt_logger, "add_entry", AsyncMock()) as mock_dlt_add, patch(
        "logging.getLogger"
    ) as mock_local_logger:
        mock_audit_logger = MagicMock()
        mock_local_logger.return_value = mock_audit_logger

        await logger.log_event("test_event", {"key": "value"}, "corr123")

        mock_dlt_add.assert_called_once()
        mock_audit_logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_audit_logger_log_event_redaction(temp_project_root):
    """Test sensitive data redaction in log event."""
    logger = AuditLogger("audit.log")

    with patch.object(logger.dlt_logger, "add_entry", AsyncMock()) as mock_dlt_add:
        await logger.log_event(
            "test_event", {"password": "secret", "nested": {"api_key": "key123"}}
        )

        call_args = mock_dlt_add.call_args[0]
        assert call_args[2] == {
            "password": "[REDACTED]",
            "nested": {"api_key": "[REDACTED]"},
        }


# --- Tests for EventBus ---


def test_event_bus_init_with_mq(mock_config):
    """Test initialization with MessageQueueService."""
    mock_mq = MagicMock()
    bus = EventBus(mock_config, mock_mq)
    assert bus.message_queue_service == mock_mq


def test_event_bus_init_no_mq(mock_config):
    """Test initialization without MessageQueueService."""
    bus = EventBus(mock_config)
    assert bus.message_queue_service is None


@pytest.mark.asyncio
async def test_event_bus_publish_critical_with_mq(mock_config):
    """Test publishing critical event with MQ."""
    mock_mq = AsyncMock()
    bus = EventBus(mock_config, mock_mq)
    mock_config["critical_events_for_mq"] = ["critical_event"]

    await bus.publish("critical_event", {"key": "value"})
    mock_mq.publish.assert_called_once()


@pytest.mark.asyncio
async def test_event_bus_publish_non_critical_no_aiohttp(mock_config, monkeypatch):
    """Test publishing non-critical event without AIOHTTP."""
    monkeypatch.setattr("test_generation.policy_and_audit.AIOHTTP_AVAILABLE", False)
    bus = EventBus(mock_config)
    await bus.publish("non_critical", {"key": "value"})
    # No exception, just warning logged


@pytest.mark.asyncio
async def test_event_bus_publish_webhook(mock_config):
    """Test publishing to webhook."""
    mock_config["webhook_events"] = ["test_event"]
    bus = EventBus(mock_config)

    with patch("test_generation.policy_and_audit.aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value.__aenter__.return_value = mock_response

        await bus.publish("test_event", {"key": "value"})
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_event_bus_publish_slack(mock_config):
    """Test publishing to Slack."""
    mock_config["slack_events"] = ["test_event"]
    bus = EventBus(mock_config)

    with patch("test_generation.policy_and_audit.aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value.__aenter__.return_value = mock_response

        await bus.publish("test_event", {"module": "test_module", "reason": "test_reason"})
        mock_post.assert_called_once()
        call_args = mock_post.call_args[1]["json"]
        assert "ATCO Alert: *test_event*" in call_args["text"]


@pytest.mark.asyncio
async def test_event_bus_publish_metrics_failure(mock_config):
    """Test metrics increment on notification failure."""
    # Fix: Import metric inside the function to avoid circular dependency

    mock_config["slack_events"] = ["test_event"]
    bus = EventBus(mock_config)

    with patch(
        "test_generation.policy_and_audit.aiohttp.ClientSession.post",
        side_effect=Exception("Network error"),
    ), patch("test_generation.policy_and_audit.notification_failures_total") as mock_counter:
        await bus.publish("test_event", {"key": "value"})

        mock_counter.labels.assert_called_with(service="Slack", event_name="test_event")
        mock_counter.labels.return_value.inc.assert_called_once()


@pytest.mark.asyncio
async def test_event_bus_publish_redaction(mock_config):
    """Test sensitive data redaction in published events."""
    mock_config["slack_events"] = ["test_event"]
    bus = EventBus(mock_config)

    with patch("test_generation.policy_and_audit.aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value.__aenter__.return_value = mock_response

        await bus.publish(
            "test_event",
            {"password": "secret", "module": "test_module", "reason": "test_reason"},
        )
        call_args = mock_post.call_args[1]["json"]
        assert "[REDACTED]" in call_args["text"]


def test_audit_import():
    """
    Verifies that `audit_event` is correctly exported and callable
    from `policy_and_audit`.
    """
    from test_generation.policy_and_audit import audit_event

    assert callable(audit_event)
