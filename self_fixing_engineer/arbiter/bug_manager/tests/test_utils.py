# test_utils.py
# Comprehensive production-grade tests for utils.py
# Requires: pytest, unittest.mock
# Run with: pytest test_utils.py -v --cov=utils

from unittest.mock import MagicMock, patch

import pytest

# Fixed imports to use absolute paths from the project root
from arbiter.bug_manager.utils import (
    BugManagerError,
    NotificationError,
    RemediationError,
    SecretStr,
    Severity,
    apply_settings_validation,
    parse_bool_env,
    redact_pii,
    validate_input_details,
)

# --- Fixtures ---


@pytest.fixture
def mock_logger():
    """Fixture to patch the logger in the utils module."""
    with patch("arbiter.bug_manager.utils.logger") as mock_log:
        yield mock_log


@pytest.fixture
def valid_settings():
    """Provides a valid mock settings object."""
    settings = MagicMock()
    settings.DEBUG_MODE = True
    settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/FAKE/URL"
    settings.EMAIL_RECIPIENTS = ["test@example.com"]
    settings.EMAIL_ENABLED = True
    settings.EMAIL_SENDER = "sender@example.com"
    settings.EMAIL_SMTP_SERVER = "smtp.example.com"
    settings.EMAIL_SMTP_PORT = 587
    settings.EMAIL_USE_STARTTLS = True
    settings.EMAIL_SMTP_USERNAME = "user"
    settings.EMAIL_SMTP_PASSWORD = SecretStr("password")
    settings.PAGERDUTY_ENABLED = True
    settings.PAGERDUTY_ROUTING_KEY = SecretStr("key")
    settings.ENABLED_NOTIFICATION_CHANNELS = ("slack",)
    settings.AUDIT_LOG_FILE_PATH = "/var/log/audit.log"
    settings.AUDIT_DEAD_LETTER_FILE_PATH = "/var/log/dead_letter.log"
    settings.AUTO_FIX_ENABLED = False
    settings.NOTIFICATION_FAILURE_THRESHOLD = 5
    settings.NOTIFICATION_FAILURE_WINDOW_SECONDS = 300
    settings.RATE_LIMIT_ENABLED = True
    settings.RATE_LIMIT_WINDOW_SECONDS = 60
    settings.RATE_LIMIT_MAX_REPORTS = 10
    settings.AUDIT_LOG_ENABLED = True
    settings.AUDIT_LOG_FLUSH_INTERVAL_SECONDS = 10.0
    settings.AUDIT_LOG_BUFFER_SIZE = 100
    settings.AUDIT_LOG_MAX_FILE_SIZE_MB = 10
    settings.AUDIT_LOG_BACKUP_COUNT = 5
    settings.REMOTE_AUDIT_SERVICE_ENABLED = False
    settings.REMOTE_AUDIT_SERVICE_URL = None
    settings.REMOTE_AUDIT_SERVICE_TIMEOUT = 5.0
    settings.REMOTE_AUDIT_DEAD_LETTER_ENABLED = True
    settings.SLACK_API_TIMEOUT_SECONDS = 5.0
    settings.EMAIL_API_TIMEOUT_SECONDS = 10.0
    settings.PAGERDUTY_API_TIMEOUT_SECONDS = 5.0
    settings.SLACK_FAILURE_RATE = 0.0
    settings.EMAIL_FAILURE_RATE = 0.0
    settings.PAGERDUTY_FAILURE_RATE = 0.0
    settings.ML_REMEDIATION_ENABLED = False
    settings.ML_MODEL_ENDPOINT = "http://localhost:8000/predict"
    return settings


# --- Test Cases ---


class TestSecretStr:
    def test_creation_and_retrieval(self):
        secret = "my-super-secret-password"
        s = SecretStr(secret)
        assert s.get_secret_value() == secret

    def test_redaction_on_str_and_repr(self):
        s = SecretStr("my-secret")
        # Pydantic v2 uses a different length, so we adapt the test
        assert str(s) == "**********"
        assert repr(s) == "SecretStr('**********')"

    def test_coerces_non_string_input(self):
        s = SecretStr(12345)
        assert s.get_secret_value() == str(12345)  # Ensure str conversion


class TestParseBoolEnv:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
    def test_parses_true_values(self, monkeypatch, value):
        monkeypatch.setenv("TEST_VAR", value)
        assert parse_bool_env("TEST_VAR", False) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", " FALSE ", "other"])
    def test_parses_false_values(self, monkeypatch, value):
        monkeypatch.setenv("TEST_VAR", value)
        assert parse_bool_env("TEST_VAR", True) is False

    def test_uses_default_when_var_not_set(self):
        assert parse_bool_env("UNSET_VAR", True) is True
        assert parse_bool_env("UNSET_VAR", False) is False


class TestRedactPII:
    def test_redacts_by_sensitive_keyword(self):
        data = {"user": "test", "api_key": "abc-123", "token": "xyz-456"}
        redacted = redact_pii(data)
        assert redacted["user"] == "test"
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["token"] == "[REDACTED]"

    def test_redacts_by_pattern(self):
        data = {
            "contact_email": "test.user@example.com",
            "last_login_ip": "192.168.1.1",
            "connection_string": "user:pass;token=supersecret123",
        }
        redacted = redact_pii(data)
        # The keyword "email" in the key takes precedence and redacts the whole value.
        assert redacted["contact_email"] == "[REDACTED]"
        assert redacted["last_login_ip"] == "[REDACTED_IP]"
        assert redacted["connection_string"] == "user:pass;[REDACTED_SECRET]"

    def test_recursive_redaction(self):
        data = {
            "user_id": 123,
            "config": {"credentials": {"password": "my-password-123"}},
            "history": [{"action": "login", "ip_address": "10.0.0.1"}, "logout"],
        }
        redacted = redact_pii(data)
        assert redacted["config"]["credentials"]["password"] == "[REDACTED]"
        assert redacted["history"][0]["ip_address"] == "[REDACTED]"
        assert (
            redacted["history"][1] == "logout"
        )  # Non-sensitive strings in lists are untouched

    def test_ignores_non_sensitive_data(self):
        data = {
            "user_id": 123,
            "is_active": True,
            "score": 99.5,
            "notes": "A regular note",
        }
        redacted = redact_pii(data)
        assert redacted == data


class TestSettingsValidation:
    def test_apply_validation_succeeds_on_valid_settings(self, valid_settings):
        try:
            apply_settings_validation(valid_settings)
        except ValueError:
            pytest.fail(
                "apply_settings_validation raised ValueError unexpectedly on valid settings."
            )

    def test_apply_validation_fails_on_missing_field(self, valid_settings):
        del valid_settings.DEBUG_MODE
        with pytest.raises(ValueError, match="Missing required setting: 'DEBUG_MODE'"):
            apply_settings_validation(valid_settings)

    def test_apply_validation_fails_on_incorrect_type(self, valid_settings):
        valid_settings.DEBUG_MODE = "not-a-bool"
        with pytest.raises(
            ValueError,
            match="Setting 'DEBUG_MODE' has incorrect type. Expected <class 'bool'>, got <class 'str'>.",
        ):
            apply_settings_validation(valid_settings)

    def test_apply_validation_handles_optional_fields(self, valid_settings):
        # Should be valid with a string
        valid_settings.SLACK_WEBHOOK_URL = "http://example.com"
        apply_settings_validation(valid_settings)

        # Should be valid with None
        valid_settings.SLACK_WEBHOOK_URL = None
        apply_settings_validation(valid_settings)

        # Should fail with an incorrect type
        valid_settings.SLACK_WEBHOOK_URL = 123
        with pytest.raises(
            ValueError, match="Setting 'SLACK_WEBHOOK_URL' has incorrect type."
        ):
            apply_settings_validation(valid_settings)


class TestValidateInputDetails:
    def test_valid_dict_is_sanitized(self):
        details = {"user_email": "test@example.com", "user_id": 123}
        sanitized = validate_input_details(details)
        # The keyword "email" in the key takes precedence
        assert sanitized["user_email"] == "[REDACTED]"
        assert sanitized["user_id"] == 123

    def test_none_returns_empty_dict(self):
        assert validate_input_details(None) == {}

    def test_invalid_type_raises_error(self, mock_logger):
        with pytest.raises(ValueError, match="custom_details must be a dictionary"):
            validate_input_details("this is a string")
        mock_logger.error.assert_called_once()

    def test_max_depth_exceeded_raises_error(self):
        deep_dict = {"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}}
        with pytest.raises(ValueError, match="exceeds maximum nesting depth"):
            validate_input_details(deep_dict)


class TestErrorClasses:
    def test_bug_manager_error_properties(self):
        error = BugManagerError("Test message")
        assert error.message == "Test message"
        assert len(error.error_id) == 8
        assert isinstance(error.timestamp, str)
        assert "Test message" in str(error)

    def test_subclass_properties(self):
        notif_error = NotificationError(
            "Failed", channel="slack", error_code="API_ERROR"
        )
        assert notif_error.channel == "slack"
        assert notif_error.error_code == "API_ERROR"

        remed_error = RemediationError(
            "Step failed", step_name="restart", playbook_name="svc_restart"
        )
        assert remed_error.step_name == "restart"
        assert remed_error.playbook_name == "svc_restart"


class TestSeverityEnum:
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            ("CRITICAL", Severity.CRITICAL),
            ("high", Severity.HIGH),
            ("Medium", Severity.MEDIUM),
            ("lOw", Severity.LOW),
        ],
    )
    def test_from_string_valid(self, input_str, expected):
        assert Severity.from_string(input_str) == expected

    def test_from_string_invalid_defaults_and_warns(self, mock_logger):
        result = Severity.from_string("INVALID_SEVERITY")
        assert result == Severity.MEDIUM
        mock_logger.warning.assert_called_once_with(
            "Invalid severity string 'INVALID_SEVERITY', defaulting to MEDIUM."
        )
