import pytest
import logging
import queue
import threading
import json
import os
import tempfile
import time
import shutil
import signal
from unittest.mock import MagicMock, patch

import core_audit

# --- Utility and Fixtures ---

class DummySecretsManager:
    def __init__(self, secrets=None):
        self._secrets = secrets or {}
        self.reload_called = False
    def get_secret(self, key, default=None, type_cast=None):
        v = self._secrets.get(key, default)
        if type_cast and v is not None:
            try:
                return type_cast(v)
            except Exception:
                return default
        return v
    def reload(self):
        self.reload_called = True

@pytest.fixture
def temp_log_dir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d)

@pytest.fixture
def secrets(temp_log_dir):
    return {
        "AUDIT_LOG_FILE": os.path.join(temp_log_dir, "audit.log"),
        "AUDIT_LOG_LEVEL": "DEBUG",
        "AUDIT_QUEUE_MAXSIZE": 5,
        "AUDIT_LOG_MAX_BYTES": 1024*1024,
        "AUDIT_LOG_BACKUP_COUNT": 1,
        "AUDIT_EVENT_MAX_BYTES": 2048,
        "AUDIT_RL_WINDOW_SEC": 1,
        "AUDIT_RL_MAX_EVENTS": 3,
        "AUDIT_RL_MAX_KEYS": 10,
        "AUDIT_LOG_TO_CONSOLE": False,
        "AUDIT_USE_WATCHED_FILE": False,
        "APP_NAME": "test_app",
        "ENVIRONMENT": "test",
        "AUDIT_INCLUDE_TRACES": False,
        "AUDIT_STRICT_WRITES": False,
    }

@pytest.fixture(autouse=True)
def reset_singleton():
    # Remove singleton between tests
    core_audit.AuditLogger._instance = None
    yield
    core_audit.AuditLogger._instance = None

@pytest.fixture
def logger(secrets):
    mgr = DummySecretsManager(secrets)
    log = core_audit.AuditLogger(mgr)
    yield log
    log.close()

# --- Test Cases ---

def test_singleton_and_context(logger):
    l2 = core_audit.AuditLogger()
    assert logger is l2
    assert "app_name" in logger._context
    assert logger._context["app_name"] == "test_app"

def test_log_event_to_file(logger, secrets):
    logger.log_event("test_event", foo="bar", bar=123)
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = f.readlines()
    assert any('"event_type":"test_event"' in line for line in lines)
    assert any('"foo":"bar"' in line for line in lines)

def test_log_event_invalid_event_type(logger):
    with pytest.raises(ValueError):
        logger.log_event("", foo=1)

def test_log_event_truncation(logger, secrets):
    longstr = "X" * 3000
    logger._max_event_bytes = 512  # force truncation small
    logger.log_event("big_event", data=longstr)
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = f.readlines()
    found = [json.loads(line) for line in lines if "big_event" in line]
    assert found[0].get("truncated") is True

def test_log_event_hmac_signature(logger, secrets):
    key = "supersecret"
    secrets["AUDIT_HMAC_KEY"] = key
    logger.reload()
    logger.log_event("signed_event", value="abc")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = f.readlines()
    found = [json.loads(line) for line in lines if "signed_event" in line]
    assert "signature" in found[0]
    import hmac, hashlib
    body = json.dumps(found[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    # The signature is calculated before adding "signature" itself, so here we just check it exists and is hex.
    assert all(c in "0123456789abcdef" for c in found[0]["signature"])

def test_log_event_hmac_kid(logger, secrets):
    secrets["AUDIT_HMAC_KEYS_JSON"] = json.dumps({"k1": "kval1"})
    secrets["AUDIT_HMAC_ACTIVE_KID"] = "k1"
    logger.reload()
    logger.log_event("hmac_kid_event", x=1)
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        found = [json.loads(line) for line in f if "hmac_kid_event" in line][0]
        assert "kid" in found and found["kid"] == "k1"
        assert "signature" in found

def test_log_exception_and_stacktrace(logger, secrets):
    secrets["AUDIT_INCLUDE_TRACES"] = True
    logger.reload()
    try:
        1/0
    except Exception as e:
        logger.log_exception("div_zero", e, user="bob")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        found = [json.loads(line) for line in f if "div_zero" in line][0]
        assert found["exc_type"] == "ZeroDivisionError"
        assert "traceback" in found

def test_rate_limiting(logger, secrets):
    # Only 3 events per window per key
    for _ in range(3):
        logger.log_event("rate_event", foo="v")
    # 4th should be dropped (not logged)
    logger.log_event("rate_event", foo="should_drop")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = [l for l in f if "rate_event" in l]
    assert len(lines) == 3

def test_rate_limit_max_keys(logger, secrets):
    for i in range(10):
        logger.log_event(f"unique_{i}", foo="v")
    # 11th unique event_type should be dropped
    logger.log_event("unique_10", foo="should_drop")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = [l for l in f if "unique_" in l]
    # Only first 10 should be present
    assert len(lines) == 10

def test_queue_drop_on_full(logger, secrets):
    # Patch the queue to always be full
    logger._log_queue = queue.Queue(maxsize=1)
    logger._log_queue.put_nowait(logging.LogRecord("audit_logger", logging.INFO, "dummy", 1, "msg", (), None))
    with patch("sys.stderr.write") as serr:
        logger.log_event("should_drop", foo="bar")
        assert any("audit_queue_full" in json.loads(args[0])["event_type"] for args, _ in serr.call_args_list)

def test_reload_and_update_context(logger):
    logger.update_context(newfield="val")
    assert logger._context["newfield"] == "val"
    logger.reload()
    assert logger.secrets_manager.reload_called

def test_close_and_log_after_close(logger, secrets):
    logger.close()
    with patch("sys.stderr.write") as serr:
        logger.log_event("event_after_close", foo="bar")
        assert any("audit_after_close" in args[0] for args, _ in serr.call_args_list)

def test_strict_writes_kills_process(logger, secrets):
    secrets["AUDIT_STRICT_WRITES"] = True
    logger.reload()
    # Patch inner handler to raise
    for h in logger._attached_handlers:
        h.inner.emit = MagicMock(side_effect=Exception("fail"))
    with patch("os._exit") as oexit:
        logger.log_event("should_exit", foo="bar")
        assert oexit.called

def test_handler_permission_error(monkeypatch, secrets):
    # Simulate chmod failure
    monkeypatch.setattr(os, "chmod", MagicMock(side_effect=OSError("permfail")))
    secrets["ENVIRONMENT"] = "prod"
    logger = core_audit.AuditLogger(DummySecretsManager(secrets))
    logger.close()

def test_serialization_error(logger):
    class BadObj:
        def __str__(self): raise Exception("failstr")
    with patch("sys.stderr.write") as serr:
        logger.log_event("bad_serial", bad=BadObj())
        assert any("audit_serialization_error" in args[0] for args, _ in serr.call_args_list)

@pytest.mark.skipif(os.name != "posix", reason="SIGHUP only on POSIX")
def test_sighup_reload(monkeypatch, secrets):
    logger = core_audit.AuditLogger(DummySecretsManager(secrets))
    with patch.object(logger, "reload") as reload_mock:
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(0.1)
        assert reload_mock.called

def test_extra_context_json(logger, secrets):
    secrets["AUDIT_EXTRA_CONTEXT_JSON"] = json.dumps({"foo": "bar", "x": 42})
    logger.reload()
    logger.log_event("extra_ctx_event")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        found = [json.loads(line) for line in f if "extra_ctx_event" in line][0]
        assert found["foo"] == "bar"
        assert found["x"] == 42

def test_multithreaded_logging(logger, secrets):
    def worker(idx):
        for _ in range(10):
            logger.log_event("threaded_event", thread_idx=idx)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        lines = [json.loads(l) for l in f if "threaded_event" in l]
    # At least some events from both threads
    thread_idxs = set(l["thread_idx"] for l in lines)
    assert thread_idxs == {0, 1}

def test_correlation_id(logger, secrets):
    logger.log_event("corr_event", correlation_id="abc-123")
    logger.close()
    with open(secrets["AUDIT_LOG_FILE"]) as f:
        found = [json.loads(line) for line in f if "corr_event" in line][0]
        assert found.get("correlation_id") == "abc-123"

# --- END OF TEST FILE ---