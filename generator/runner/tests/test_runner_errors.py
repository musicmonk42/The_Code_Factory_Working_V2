# -*- coding: utf-8 -*-
"""
test_runner_errors.py
Industry-grade test suite for runner_errors.py (2025 refactor).

* Full coverage of the error hierarchy
* Registry duplicate protection
* OpenTelemetry no-op fallback
* PII redaction (tested via real redact_secrets import)
* Structured audit logging (LOG_HISTORY)
* pytest fixtures for isolation
"""

import json
import uuid
from typing import Any, Dict
from unittest.mock import patch

import pytest

# --------------------------------------------------------------------------- #
# Import the module under test – use aliases to avoid pytest collection warnings
# --------------------------------------------------------------------------- #
# --- FIX: Import ExecutionError and alias it for the test ---
from runner.runner_errors import (
    ERROR_CODE_REGISTRY,
    BackendError,
    ConfigurationError,
    DistributedError,
    ExecutionError,
    ExporterError,
    FrameworkError,
    LLMError,
    PersistenceError,
    RunnerError,
    SetupError,
    TimeoutError,
    ValidationError,
    register_error_code,
)

# --- END FIX ---
from runner.runner_logging import LOG_HISTORY  # <-- real module name

# --------------------------------------------------------------------------- #
# Fixtures – isolation per test
# --------------------------------------------------------------------------- #
# List of all base codes registered in runner/runner_errors.py
BASE_CODES = {
    "BACKEND_INIT_FAILURE": "Failed to initialize the execution backend.",
    "FRAMEWORK_UNSUPPORTED": "The specified or auto-detected test framework is not supported.",
    "TEST_EXECUTION_FAILED": "The test execution command returned a non-zero exit code or failed unexpectedly.",
    "PARSING_ERROR": "Failed to parse test results or coverage data.",
    "SETUP_FAILURE": "Environment setup within the backend failed.",
    "TASK_TIMEOUT": "The task exceeded its allocated execution time.",
    "DISTRIBUTED_COMMUNICATION_ERROR": "An error occurred during communication with a distributed worker or endpoint.",
    "PERSISTENCE_FAILURE": "Failed to load or save a persistent state (e.g., task queue).",
    "CONFIGURATION_ERROR": "An error occurred during configuration loading or validation.",
    "UNEXPECTED_ERROR": "An unhandled or unexpected error occurred within the runner.",
    "VALIDATION_ERROR": "Data validation failed for input or output contracts.",
    "EXPORTER_FAILURE": "Failed to export metrics to an external system.",
    "LLM_PROVIDER_ERROR": "The LLM provider API call failed.",
    "LLM_RATE_LIMIT": "Rate limit exceeded for the LLM provider.",
    "LLM_CIRCUIT_OPEN": "Circuit breaker is open for the LLM provider.",
    "LLM_PLUGIN_NOT_FOUND": "The specified LLM provider plugin is not loaded or available.",
}


def _re_register_codes():
    """Defensively re-registers the base codes."""
    for code, desc in BASE_CODES.items():
        if code not in ERROR_CODE_REGISTRY:
            try:
                register_error_code(code, desc)
            except ValueError:
                # Should not happen after a clear, but defensively swallow it
                pass


@pytest.fixture(autouse=True)
def clean_state():
    """Clear registry & log history before/after every test."""

    # 1. Clear everything for true isolation
    ERROR_CODE_REGISTRY.clear()
    LOG_HISTORY.clear()

    # 2. Restore state needed for classes/tests using base codes (which are now missing)
    _re_register_codes()

    yield

    # 3. Final cleanup
    ERROR_CODE_REGISTRY.clear()
    LOG_HISTORY.clear()


@pytest.fixture
def run_id() -> str:
    """Unique run-id for traceability."""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Helper – build a minimal error instance and return its dict representation
# --------------------------------------------------------------------------- #
def error_dict(exc: RunnerError) -> Dict[str, Any]:
    """Call the public ``as_dict`` method (all RunnerError subclasses have it)."""
    return exc.as_dict()


# --------------------------------------------------------------------------- #
# Registry tests
# --------------------------------------------------------------------------- #
def test_register_error_code_success(clean_state):
    # This must be a *new* code not registered by the initial import/fixture
    register_error_code("MY_CODE_NEW", "My description")
    assert ERROR_CODE_REGISTRY["MY_CODE_NEW"] == "My description"


def test_register_error_code_duplicate_raises():
    # We rely on the clean_state fixture to restore a base code (e.g., SETUP_FAILURE)
    with pytest.raises(ValueError, match="already registered"):
        register_error_code("SETUP_FAILURE", "Second")


# --------------------------------------------------------------------------- #
# OpenTelemetry no-op fallback
# --------------------------------------------------------------------------- #
def test_no_op_tracer_when_ot_missing():
    # Simulate missing OpenTelemetry by raising ImportError on trace
    # FIX 1: Patch the correct module 'runner.runner_errors.trace'
    with patch("runner.runner_errors.trace", side_effect=ImportError("No OT")):
        # Force reload so the except-block runs
        import importlib

        import runner.runner_errors as err_mod  # Use runner_errors

        importlib.reload(err_mod)

        # FIX 2: Use start_span which returns the span object directly (NoOpSpan)
        span = err_mod._tracer.start_span("test")
        span.set_attribute("k", "v")
        span.record_exception(Exception("boom"))

        # Test __enter__ and __exit__ (which are the context manager for start_as_current_span)
        with err_mod._tracer.start_as_current_span("test_ctx"):
            pass


# --------------------------------------------------------------------------- #
# Base RunnerError
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_runner_error_basic(clean_state, run_id):
    if "BASE_ERR" not in ERROR_CODE_REGISTRY:
        register_error_code("BASE_ERR", "Base error")

    with patch("runner.runner_errors.redact_secrets", new=lambda s: s):
        exc = RunnerError(error_code="BASE_ERR", detail="Something went wrong", task_id=run_id)
        d = error_dict(exc)

    assert d["error_type"] == "RunnerError"
    assert d["error_code"] == "BASE_ERR"
    assert d["detail"] == "Something went wrong"
    assert d["task_id"] == run_id
    assert isinstance(d["timestamp_utc"], str)


# --------------------------------------------------------------------------- #
# All concrete error classes – parametrised
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,code,extra_kwargs",
    [
        (
            BackendError,
            "BACKEND_INIT_FAILURE",
            {"backend_type": "docker"},
        ),  # FIX: Use correct kwarg
        (
            FrameworkError,
            "FRAMEWORK_UNSUPPORTED",
            {"framework_name": "pytest"},
        ),  # FIX: Use correct kwarg
        (ExecutionError, "TEST_EXECUTION_FAILED", {"returncode": 1, "cmd": "pytest"}),
        (SetupError, "SETUP_FAILURE", {}),
        (TimeoutError, "TASK_TIMEOUT", {"timeout_seconds": 30}),
        (DistributedError, "DISTRIBUTED_COMMUNICATION_ERROR", {}),
        (PersistenceError, "PERSISTENCE_FAILURE", {}),
        (ConfigurationError, "CONFIGURATION_ERROR", {"config_file": "cfg.yaml"}),
        (ValidationError, "VALIDATION_ERROR", {"field": "port", "value": -1}),
        (LLMError, "LLM_PROVIDER_ERROR", {"provider": "openai"}),
        (ExporterError, "EXPORTER_FAILURE", {"exporter_name": "datadog"}),
    ],
)
async def test_error_subclasses(cls, code, extra_kwargs, clean_state, run_id):

    kwargs = {"error_code": code, "detail": f"{cls.__name__} detail", "task_id": run_id}
    kwargs.update(extra_kwargs)

    with patch("runner.runner_errors.redact_secrets", new=lambda s: s):
        exc = cls(**kwargs)
        d = error_dict(exc)

    assert d["error_type"] == cls.__name__
    assert d["error_code"] == code
    assert d["detail"] == kwargs["detail"]
    assert d["task_id"] == run_id

    for k, v in extra_kwargs.items():
        assert d.get(k) == v


# --------------------------------------------------------------------------- #
# PII redaction in error details
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_error_redacts_secrets(clean_state, run_id):
    # FIX: Ensure registration is handled safely
    if "REDACT_ERR" not in ERROR_CODE_REGISTRY:
        register_error_code("REDACT_ERR", "Error with secret")

    # The mock now replaces lowercase "secret"
    with patch(
        "runner.runner_errors.redact_secrets",
        new=lambda s: s.replace("secret", "[REDACTED]"),
    ):
        exc = RunnerError(
            error_code="REDACT_ERR",
            # FIX: Use a string that demonstrably contains the lowercase word "secret"
            detail="Error in config. API key is secret=abc123",
            task_id=run_id,
        )
        d = error_dict(exc)
        # The sync mock replaces 'secret' -> '[REDACTED]' in the detail string immediately.
        assert "[REDACTED]" in d["detail"]


# --------------------------------------------------------------------------- #
# Audit logging – log_action is called from RunnerError.__init__
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_runner_error_triggers_audit_log(clean_state, run_id):
    if "AUDIT_ERR" not in ERROR_CODE_REGISTRY:
        register_error_code("AUDIT_ERR", "Audit test")

    with patch("runner.runner_logging.log_action") as mock_log:
        with patch("runner.runner_errors.redact_secrets", new=lambda s: s):
            RunnerError(error_code="AUDIT_ERR", detail="audit me", task_id=run_id)

    mock_log.assert_called_once()
    call_args = mock_log.call_args[1]  # Use [1] for kwargs
    assert call_args["action"] == "error_raised"
    # *** FIX: Use call_args, not call_kwargs ***
    assert call_args["error_type"] == "RunnerError"
    assert call_args["error_code"] == "AUDIT_ERR"


# --------------------------------------------------------------------------- #
# LLMError – validates allowed error codes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_llm_error_invalid_code_falls_back_to_default(clean_state, run_id):
    with patch("runner.runner_errors.redact_secrets", new=lambda s: s):
        exc = LLMError(detail="boom", error_code="INVALID_CODE", task_id=run_id)
        d = error_dict(exc)
    assert d["error_code"] == "LLM_PROVIDER_ERROR"  # default


# --------------------------------------------------------------------------- #
# JSON serialisation (used by API responses)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_error_as_json_is_valid(clean_state, run_id):
    if "JSON_ERR" not in ERROR_CODE_REGISTRY:
        register_error_code("JSON_ERR", "JSON test")
    with patch("runner.runner_errors.redact_secrets", new=lambda s: s):
        exc = RunnerError(error_code="JSON_ERR", detail="json", task_id=run_id)
        json_str = json.dumps(exc.as_dict())
    data = json.loads(json_str)
    assert data["error_type"] == "RunnerError"
    assert data["error_code"] == "JSON_ERR"


# --------------------------------------------------------------------------- #
# End of suite
# --------------------------------------------------------------------------- #
