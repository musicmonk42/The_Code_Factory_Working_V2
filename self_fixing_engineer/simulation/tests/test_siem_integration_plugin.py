# tests/test_siem_integration_plugin.py

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

# Use absolute import path assuming tests are run from the project root
from simulation.plugins.siem_integration_plugin import (
    SIEM_EVENTS_SENT_TOTAL,
    SIEM_SEND_ERRORS_TOTAL,
    GenericSIEMIntegrationPlugin,
    PluginGlobalConfig,
    PolicyCondition,
    PolicyConfig,
    PolicyEnforcer,
    PolicyRule,
)

# A minimal Pydantic model for the config since the real one is now correctly mocked
# or we assume Pydantic is available in the test environment.
SIEM_CONFIG_MODEL = PluginGlobalConfig.parse_obj(
    {
        "default_siem_type": "splunk",
        "splunk": {"url": "http://mock-splunk.com", "token": "mock-token"},
        "elastic": {"url": "http://mock-elastic.com", "api_key": "mock-api-key"},
    }
)


# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks external libraries and environment variables for complete isolation.
    Uses absolute paths for patching.
    """
    # Mock SIEM client classes
    mock_splunk_client = MagicMock()
    mock_splunk_client.health_check = AsyncMock(return_value=(True, "OK"))
    mock_splunk_client.send_log = AsyncMock(return_value=(True, "Event sent"))
    mock_splunk_client.query_logs = AsyncMock(return_value=[{"result": "mock"}])
    mock_splunk_client.close = AsyncMock()

    mock_elastic_client = MagicMock()
    mock_elastic_client.health_check = AsyncMock(return_value=(True, "OK"))
    mock_elastic_client.send_log = AsyncMock(return_value=(True, "Event sent"))
    mock_elastic_client.close = AsyncMock()

    mock_registry = {"splunk": mock_splunk_client, "elastic": mock_elastic_client}

    # Use absolute paths for patching targets
    plugin_path = "simulation.plugins.siem_integration_plugin"

    with patch(f"{plugin_path}.SIEM_CLIENTS_AVAILABLE", True), patch(
        f"{plugin_path}.SIEM_CLIENT_REGISTRY", mock_registry
    ), patch(f"{plugin_path}.get_siem_client", side_effect=lambda t, c: mock_registry[t]), patch(
        f"{plugin_path}.redis"
    ) as mock_redis, patch(
        f"{plugin_path}.QUERY_PARSER_AVAILABLE", True
    ), patch(
        f"{plugin_path}.SiemQueryLanguageParser"
    ), patch(
        f"{plugin_path}._sfe_audit_logger.add_entry", new=AsyncMock()
    ) as mock_audit_add_entry, patch.dict(
        os.environ,
        {
            "SIEM_DEFAULT_TYPE": "splunk",
            "SIEM_SPLUNK_HEC_URL": "http://mock-splunk.com",
            "SIEM_SPLUNK_HEC_TOKEN": "mock-token",
            "SIEM_ELASTIC_APM_URL": "http://mock-elastic.com",
            "SIEM_ELASTIC_API_KEY": "mock-api-key",
        },
    ):

        # Mock Redis
        mock_redis_client = mock_redis.from_url.return_value
        mock_redis_client.rpush = AsyncMock()
        mock_redis_client.lpop = AsyncMock()
        mock_redis_client.llen = AsyncMock(return_value=0)
        mock_redis_client.delete = AsyncMock()

        # Use a fresh Prometheus registry for each test
        with patch(f"{plugin_path}.prometheus_available", True), patch(
            f"{plugin_path}.REGISTRY", new=CollectorRegistry(auto_describe=True)
        ):

            # This is crucial: we must also patch the config model loaded by the module
            # to ensure the plugin initializes with our mocked env vars during the test.
            test_config_model = PluginGlobalConfig.parse_obj(
                {
                    "default_siem_type": "splunk",
                    "splunk": {"url": "http://mock-splunk.com", "token": "mock-token"},
                    "elastic": {
                        "url": "http://mock-elastic.com",
                        "api_key": "mock-api-key",
                    },
                }
            )
            with patch(f"{plugin_path}.SIEM_CONFIG_MODEL", test_config_model):
                yield {
                    "mock_splunk_client": mock_splunk_client,
                    "mock_elastic_client": mock_elastic_client,
                    "mock_redis": mock_redis,
                    "mock_audit_add_entry": mock_audit_add_entry,
                }


# ==============================================================================
# Unit Tests for Pydantic Config and Validation
# ==============================================================================


def test_config_model_validation_success():
    """Test that a valid config is accepted by the Pydantic model."""
    config_data = {
        "default_siem_type": "splunk",
        "default_timeout_seconds": 15,
        "splunk": {"url": "https://test.com", "token": "test-token"},
        "policy": {
            "rules": [
                {
                    "conditions": [{"field": "event_type", "operator": "equals", "value": "alert"}],
                    "action": "block",
                }
            ]
        },
    }
    config = PluginGlobalConfig.parse_obj(config_data)
    assert config.default_siem_type == "splunk"
    assert config.splunk.url == "https://test.com"
    assert len(config.policy.rules) == 1


def test_config_model_validation_invalid_type():
    """Test that an invalid SIEM type raises a ValidationError."""
    with pytest.raises(ValidationError):
        PluginGlobalConfig.parse_obj({"default_siem_type": "invalid_siem"})


# ==============================================================================
# Unit Tests for `PolicyEnforcer`
# ==============================================================================


def test_policy_enforcer_mask_rule_enforcement():
    """Test that a 'mask' rule correctly redacts a field."""
    policy_config = PolicyConfig(
        rules=[
            PolicyRule(
                conditions=[
                    PolicyCondition(field="event_type", operator="equals", value="sensitive_event")
                ],
                action="mask",
                target_field="details.sensitive_info",
                mask_with="[REDACTED_BY_POLICY]",
            )
        ]
    )
    enforcer = PolicyEnforcer(policy_config)
    event = {
        "event_type": "sensitive_event",
        "details": {"sensitive_info": "secret123"},
    }

    is_allowed, reason, processed_event = enforcer.enforce(event)

    assert is_allowed is True
    assert processed_event["details"]["sensitive_info"] == "[REDACTED_BY_POLICY]"
    assert "Policy enforced successfully" in reason


def test_policy_enforcer_block_rule_enforcement():
    """Test that a 'block' rule correctly prevents an event from being sent."""
    policy_config = PolicyConfig(
        rules=[
            PolicyRule(
                conditions=[
                    PolicyCondition(field="event_type", operator="equals", value="blocked_event")
                ],
                action="block",
            )
        ]
    )
    enforcer = PolicyEnforcer(policy_config)
    event = {"event_type": "blocked_event", "details": {}}

    is_allowed, reason, processed_event = enforcer.enforce(event)

    assert is_allowed is False
    assert "blocked by policy rule" in reason
    assert processed_event == event  # Original event should not be modified


# ==============================================================================
# Integration Tests for `GenericSIEMIntegrationPlugin` workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_send_siem_event_success(mock_external_dependencies):
    """
    Test a successful end-to-end event send to the default SIEM.
    """
    # The plugin will now use the patched SIEM_CONFIG_MODEL for initialization
    plugin = GenericSIEMIntegrationPlugin(config=SIEM_CONFIG_MODEL)

    send_result = await plugin.send_siem_event(
        event_type="test_event", event_details={"message": "This is a test"}
    )

    assert send_result["success"] is True
    assert "Event sent" in send_result["reason"]
    assert mock_external_dependencies["mock_splunk_client"].send_log.call_count == 1
    # Re-import metrics locally as they are patched per-test

    assert SIEM_EVENTS_SENT_TOTAL.labels(siem_type="splunk", status="success")._value.get() == 1
    assert (
        SIEM_SEND_ERRORS_TOTAL.labels(
            siem_type="splunk", error_type="backend_not_found"
        )._value.get()
        == 0
    )


@pytest.mark.asyncio
async def test_send_siem_event_policy_blocked(mock_external_dependencies):
    """
    Test that an event is blocked by policy and returns a failure result.
    """
    plugin = GenericSIEMIntegrationPlugin(config=SIEM_CONFIG_MODEL)
    plugin.policy_enforcer = PolicyEnforcer(
        PolicyConfig(
            rules=[
                PolicyRule(
                    conditions=[
                        PolicyCondition(
                            field="event_type", operator="equals", value="blocked_event"
                        )
                    ],
                    action="block",
                )
            ]
        )
    )

    send_result = await plugin.send_siem_event(
        event_type="blocked_event",
        event_details={"message": "This event should not be sent"},
    )

    assert send_result["success"] is False
    assert "blocked by policy rule" in send_result["reason"]
    assert mock_external_dependencies["mock_splunk_client"].send_log.call_count == 0

    assert (
        SIEM_SEND_ERRORS_TOTAL.labels(siem_type="splunk", error_type="policy_blocked")._value.get()
        == 1
    )


@pytest.mark.asyncio
async def test_send_siem_event_backend_disabled(mock_external_dependencies):
    """
    Test that an event is enqueued for retry if the backend is disabled.
    """
    plugin = GenericSIEMIntegrationPlugin(config=SIEM_CONFIG_MODEL)

    # Manually disable the backend in the self-healing manager
    plugin.self_healing_manager.disabled_backends["splunk"] = {
        "consecutive_failures": 5,
        "disabled_until": time.monotonic() + 300,
    }

    with patch.object(
        plugin.self_healing_manager, "enqueue_for_retry", new=AsyncMock()
    ) as mock_enqueue:
        send_result = await plugin.send_siem_event(
            event_type="test_event", event_details={"message": "This should be queued"}
        )

        assert send_result["success"] is False
        assert "is temporarily disabled" in send_result["reason"]
        mock_enqueue.assert_called_once()
        from simulation.plugins.siem_integration_plugin import SIEM_SEND_ERRORS_TOTAL

        assert (
            SIEM_SEND_ERRORS_TOTAL.labels(
                siem_type="splunk", error_type="backend_disabled"
            )._value.get()
            == 1
        )


@pytest.mark.asyncio
async def test_query_siem_logs_success(mock_external_dependencies):
    """
    Test a successful query operation.
    """
    plugin = GenericSIEMIntegrationPlugin(config=SIEM_CONFIG_MODEL)

    query_result = await plugin.query_siem_logs(
        query_string="event_type='security_alert'", siem_type_override="splunk"
    )

    assert query_result["success"] is True
    assert len(query_result["results"]) == 1
    assert query_result["results"][0]["result"] == "mock"

    assert (
        SIEM_EVENTS_SENT_TOTAL.labels(siem_type="splunk", status="query_success")._value.get() == 1
    )
