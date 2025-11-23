import logging
import re
from unittest.mock import MagicMock, patch

import core_utils
import pytest

# --- Fixtures and Mocks ---


class DummySecretsManager:
    def __init__(self, secrets=None):
        self._secrets = secrets or {}
        self.reload_called = False

    def get_secret(self, key, default=None):
        return self._secrets.get(key, default)

    def get_int(self, key, default=None):
        try:
            return int(self._secrets.get(key, default))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key, default=None):
        val = self._secrets.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes")
        return bool(val)

    def reload(self):
        self.reload_called = True


class DummyAuditLogger:
    def __init__(self, secrets_manager=None):
        self.events = []
        self.reload_called = False

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def reload(self):
        self.reload_called = True


@pytest.fixture
def secrets():
    return {
        "ALERT_QUEUE_MAX_SIZE": 10,
        "ALERT_LOG_LEVEL": "DEBUG",
        "SLACK_WEBHOOK_URL": "[REDACTED]",
        "ALERT_EMAIL_TO": "test@example.com",
        "ALERT_EMAIL_FROM": "noreply@example.com",
        "ALERT_SMTP_SERVER": "smtp.example.com",
        "ALERT_SMTP_USER": "smtpuser",
        "ALERT_SMTP_PASS": "smtppass",
        "ALERT_SMTP_PORT": 587,
        "ALERT_SMTP_SSL": False,
        "ALERT_SMTP_STARTTLS": True,
        "ALERT_MAX_MESSAGE_LEN": 100,
        "ALERT_EMAIL_SUBJECT_MAX": 50,
        "APP_NAME": "my_app",
        "ENVIRONMENT": "production",
        "ALERT_LOG_FILE": None,  # Use stdout
    }


@pytest.fixture
def alert_operator(monkeypatch, secrets):
    # Patch logging to avoid file/console IO.
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    monkeypatch.setattr(logging, "getLogger", lambda name=None: logging.getLogger("dummy_logger"))
    secrets_mgr = DummySecretsManager(secrets)
    audit_logger = DummyAuditLogger(secrets_mgr)
    op = core_utils.AlertOperator(secrets_mgr, audit_logger)
    op.logger.handlers = []
    op.logger.addHandler(logging.NullHandler())
    op.logger.setLevel(logging.DEBUG)
    yield op
    op._dispatcher.stop(timeout=1, drain=True)


# --- Tests for scrub and redaction ---


@pytest.mark.parametrize(
    "input_str,pattern",
    [
        ("password=secret123", r"password=\*\*\*REDACTED\*\*\*"),
        ("api_key:abcd1234", r"api_key=\*\*\*REDACTED\*\*\*"),
        ("Authorization: Bearer xyz", r"Authorization: Bearer \*\*\*REDACTED\*\*\*"),
        ("https://hooks.slack.com/services/foobar", r"\*\*\*REDACTED\*\*\*"),
        (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0",
            r"\*\*\*REDACTED\*\*\*",
        ),
        ("AKIAIOSFODNN7EXAMPLE", r"\*\*\*REDACTED\*\*\*"),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nfoo\n-----END RSA PRIVATE KEY-----",
            r"\*\*\*REDACTED\*\*\*",
        ),
    ],
)
def test_scrub_patterns(input_str, pattern):
    scrubbed = core_utils.scrub(input_str)
    assert re.search(pattern, scrubbed), f"Failed to redact pattern in: {input_str}"


def test_scrub_dict_and_large(monkeypatch):
    # Should scrub secret in dict and truncate large input
    obj = {"password": "secret123", "foo": "bar"}
    scrubbed = core_utils.scrub(obj)
    assert "***REDACTED***" in scrubbed and "secret123" not in scrubbed

    # Overly large input
    large_str = "A" * 300_000
    monkeypatch.setattr(core_utils, "_scrub_str", lambda s: s)
    result = core_utils.scrub(large_str)
    assert len(result) < 300_100


def test_safe_err_redacts():
    class DummyExc(Exception):
        def __str__(self):
            return "token=abc123"

    e = DummyExc()
    out = core_utils.safe_err(e)
    assert "***REDACTED***" in out


# --- Email header injection ---


@pytest.mark.parametrize("bad", ["foo\nbar", "evil\r\nbcc:attacker", "test\r", "x\ny"])
def test_reject_header_injection_raises(bad):
    with pytest.raises(ValueError):
        core_utils._reject_header_injection(bad)


def test_reject_header_injection_ok():
    # No newline, should not raise
    core_utils._reject_header_injection("foo", "bar", "baz")


# --- Truncate logic ---


@pytest.mark.parametrize(
    "s,max_len,expect",
    [
        ("abcdefgh", 2, "ab"),
        ("abcdefgh", 3, "abc"),
        ("abcdefgh", 4, "a..."),
        ("abc", 5, "abc"),
        ("abc", 0, ""),
    ],
)
def test_truncate_robust(s, max_len, expect):
    assert core_utils._truncate(s, max_len) == expect


# --- Dispatcher lifecycle, queue, and stop ---


def test_dispatcher_enqueue_and_stop(alert_operator):
    dispatcher = alert_operator._dispatcher
    # Patch internal dispatch methods to fast stub.
    dispatcher._dispatch_slack = MagicMock()
    dispatcher._dispatch_email = MagicMock()
    dispatcher.enqueue("slack", {"level": "ERROR", "message": "foo"})
    dispatcher.enqueue("email", {"level": "ERROR", "message": "bar"})
    dispatcher.stop(timeout=2, drain=True)
    # Should have processed both
    assert dispatcher._dispatch_slack.called or dispatcher._dispatch_email.called


def test_dispatcher_drops_when_full(alert_operator):
    dispatcher = alert_operator._dispatcher
    dispatcher._dispatch_slack = MagicMock()
    # Fill the queue
    for _ in range(dispatcher.queue.maxsize):
        dispatcher.enqueue("slack", {"level": "ERROR", "message": "foo"})
    # Next enqueue should be dropped
    with patch.object(alert_operator, "_log_rate_limited_alert") as mock_log:
        dispatcher.enqueue("slack", {"level": "ERROR", "message": "overflow"})
        assert mock_log.called


# --- Rate limiting logic ---


def test_rate_limiting(alert_operator):
    op = alert_operator
    sig = op._get_signature("test")
    # Allow up to max events in window
    for i in range(op.secrets_manager.get_int("ALERT_RL_MAX_EVENTS", 5)):
        assert op._allow_event(f"CRITICAL_{sig}") is True
    # Next should be False (rate-limited)
    assert op._allow_event(f"CRITICAL_{sig}") is False


# --- Slack and Email dispatch (mocked) ---


def test_dispatch_slack_success(alert_operator):
    dispatcher = alert_operator._dispatcher
    # Patch requests.Session.post
    with patch.object(
        dispatcher._session, "post", return_value=MagicMock(status_code=200)
    ) as mock_post:
        dispatcher._dispatch_slack({"level": "ERROR", "message": "test"})
        assert mock_post.called


def test_dispatch_slack_retries_on_failure(alert_operator):
    dispatcher = alert_operator._dispatcher
    # Always fail first, then succeed
    fail_resp = MagicMock(status_code=502)
    success_resp = MagicMock(status_code=200)
    with patch.object(
        dispatcher._session, "post", side_effect=[fail_resp, success_resp]
    ) as mock_post, patch("time.sleep", return_value=None):
        dispatcher._dispatch_slack({"level": "ERROR", "message": "test"})
        assert mock_post.call_count == 2


def test_dispatch_email_success(alert_operator):
    dispatcher = alert_operator._dispatcher
    # Patch smtplib.SMTP and send_message
    smtp_mock = MagicMock()
    smtp_mock.has_extn.return_value = True
    context_mgr = MagicMock()
    context_mgr.__enter__.return_value = smtp_mock
    with patch("smtplib.SMTP", return_value=context_mgr), patch(
        "ssl.create_default_context", return_value=MagicMock()
    ):
        dispatcher._dispatch_email(
            {
                "level": "ERROR",
                "message": "test",
                "app_name": "my_app",
                "environment": "prod",
            }
        )
        assert smtp_mock.send_message.called


def test_dispatch_email_missing_config(alert_operator):
    dispatcher = alert_operator._dispatcher
    dispatcher.operator.secrets_manager._secrets["ALERT_EMAIL_TO"] = ""
    # Should not raise or send
    dispatcher._dispatch_email({"level": "ERROR", "message": "test"})
    # No exception = pass


# --- AlertOperator API and audit ---


def test_alert_operator_alert_and_audit(alert_operator):
    op = alert_operator
    op.secrets_manager._secrets["SLACK_WEBHOOK_URL"] = None  # Only test audit/log
    op.secrets_manager._secrets["ALERT_EMAIL_TO"] = None
    op.alert("test message", level="ERROR")
    # Should have written audit event
    assert op.audit_logger.events[-1]["alert_level"] == "ERROR"


def test_alert_operator_reloads(alert_operator):
    op = alert_operator
    old_dispatcher = op._dispatcher
    op.reload()
    assert op.secrets_manager.reload_called
    assert op.audit_logger.reload_called
    assert op._dispatcher is not old_dispatcher
    op._dispatcher.stop(timeout=1, drain=True)


def test_alert_operator_context_update(alert_operator):
    op = alert_operator
    op.update_context(newkey="newval")
    assert op._context["newkey"] == "newval"


def test_alert_operator_alert_invalid_input(alert_operator):
    op = alert_operator
    with pytest.raises(ValueError):
        op.alert("", level="ERROR")
    with pytest.raises(ValueError):
        op.alert("msg", level=None)


def test_get_alert_operator_singleton(monkeypatch, secrets):
    monkeypatch.setattr(core_utils, "_alert_operator_singleton", None)
    op1 = core_utils.get_alert_operator()
    op2 = core_utils.get_alert_operator()
    assert op1 is op2


# --- Error/retry edge cases ---


def test_post_with_retry_attempts_zero(alert_operator):
    dispatcher = alert_operator._dispatcher
    with pytest.raises(Exception):
        dispatcher._post_with_retry("http://dummy", {}, 2, 0)


def test_post_with_retry_retry_after_header(alert_operator):
    dispatcher = alert_operator._dispatcher
    # Simulate 429 with Retry-After header
    resp = MagicMock(status_code=429, headers={"Retry-After": "2"})
    success = MagicMock(status_code=200)
    with patch.object(dispatcher._session, "post", side_effect=[resp, success]) as mock_post, patch(
        "time.sleep", return_value=None
    ):
        dispatcher._post_with_retry("http://dummy", {}, (1, 1), 2)
        assert mock_post.call_count == 2


def test_post_with_retry_raises_after_max_attempts(alert_operator):
    dispatcher = alert_operator._dispatcher
    fail_resp = MagicMock(status_code=502)
    with patch.object(dispatcher._session, "post", return_value=fail_resp), patch(
        "time.sleep", return_value=None
    ):
        with pytest.raises(Exception):
            dispatcher._post_with_retry("http://dummy", {}, 1, 2)


# --- Logging config edge-cases ---


def test_logger_configures_stdout(alert_operator, capsys):
    op = alert_operator
    op.secrets_manager._secrets["ALERT_LOG_FILE"] = None
    op._configure_logger()
    op.logger.info("hello world")
    # Should log JSON to stdout (noop in test env, but no error)


# --- END OF TEST FILE ---
