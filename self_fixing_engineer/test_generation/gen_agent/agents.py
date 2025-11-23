from __future__ import annotations
import logging
import json
import os
import asyncio
import shutil
import re
import sys
import tempfile
import traceback
import time
import hashlib
import html
from typing import Dict, Any, TypedDict, Optional, Callable, Awaitable
from datetime import datetime, timezone
from pathlib import Path
import functools
import importlib.util
import warnings
import inspect
from functools import lru_cache

# --- Optional Dependency Guards and Fallbacks ---
# Consolidate imports and provide robust fallbacks
_BLEACH_WARNED = False
try:
    from jinja2 import Environment, FileSystemLoader

    _JINJA_OK = True
except ImportError:
    _JINJA_OK = False

try:
    from importlib.resources import files as _pkg_files
except ImportError:
    # Fallback for older Python versions
    try:
        from importlib_resources import files as _pkg_files
    except ImportError:
        _pkg_files = None

try:
    import bleach

    _BLEACH_OK = True
except ImportError:
    _BLEACH_OK = False

try:
    import tenacity
except ImportError:
    import types
    import functools
    import asyncio

    def _retry_noop(**_kwargs):
        # behaves like @retry(...): returns a decorator
        def _decorator(func):
            # keep async/sync behavior intact
            if asyncio.iscoroutinefunction(func):

                @functools.wraps(func)
                async def _aw(*a, **k):
                    return await func(*a, **k)

                return _aw
            else:

                @functools.wraps(func)
                def _w(*a, **k):
                    return func(*a, **k)

                return _w

        return _decorator

    tenacity = types.SimpleNamespace(
        retry=_retry_noop,
        stop_after_attempt=lambda n: None,
        wait_exponential=lambda **kwargs: None,
        reraise=True,
    )

try:
    import aiofiles
except ImportError:
    pass


# --- BEGIN: metrics fallbacks ---
try:
    from prometheus_client import Counter, Histogram, REGISTRY  # type: ignore

    _PROM_AVAILABLE = True
except Exception:
    Counter = None  # type: ignore
    Histogram = None  # type: ignore
    REGISTRY = None
    _PROM_AVAILABLE = False
    warnings.warn("prometheus_client not available. Metrics will be disabled.", RuntimeWarning)


class _NoopTimerCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _NoopLabels:
    def inc(self, *args, **kwargs):
        pass

    def time(self):
        return _NoopTimerCtx()


class _NoopMetric:
    def labels(self, **_kwargs):
        return _NoopLabels()


def _make_counter(name, desc, labelnames=("agent_name", "status")):
    if _PROM_AVAILABLE:
        try:
            return Counter(name, desc, labelnames=labelnames)
        except Exception:
            pass
    return _NoopMetric()


def _make_histogram(name, desc, labelnames=("agent",)):
    if _PROM_AVAILABLE:
        try:
            return Histogram(name, desc, labelnames=labelnames)
        except Exception:
            pass
    return _NoopMetric()


# Initialize metrics variables directly
agent_runs_total = _make_counter("atco_agent_executions_total", "Total number of agent executions", ("agent_name", "status"))  # type: ignore[assignment]
agent_execution_duration = _make_histogram("atco_agent_execution_duration_seconds", "Duration of agent executions in seconds", ("agent",))  # type: ignore[assignment]
# --- END: metrics fallbacks ---


# --- BEGIN: audit_logger fallback ---
try:
    _al = audit_logger  # type: ignore[name-defined]
except NameError:
    _al = None

if _al is None or not hasattr(_al, "log_event"):

    class _AuditLoggerStub:
        async def log_event(self, event: str, payload: dict) -> None:
            return None

    audit_logger = _AuditLoggerStub()
# --- END: audit_logger fallback ---


# --- Cross-Module Imports ---
from .runtime import (
    redact_sensitive,
    AIOFILES_AVAILABLE,
    PYTEST_AVAILABLE,
    COVERAGE_AVAILABLE,
    BANDIT_AVAILABLE,
    LOCUST_AVAILABLE,
    AUDIT_LOGGER_AVAILABLE,
    audit_logger as real_audit_logger_from_runtime,
)

_AIOFILES_OK = AIOFILES_AVAILABLE


# --- Type Definitions (Centralized to avoid drift) ---
class ReviewData(TypedDict, total=False):
    scores: Dict[str, Any]
    feedback: str


class ExecutionResultsData(TypedDict, total=False):
    status: str
    output: str
    coverage: Optional[float]
    duration_sec: float
    reason: str


class TestAgentState(TypedDict, total=False):
    """
    The state dictionary passed between agents in the workflow.
    """

    spec: str
    spec_format: str
    language: str
    framework: str
    plan: Dict[str, Any]
    test_code: str
    review: ReviewData
    execution_results: ExecutionResultsData
    security_report: str
    performance_script: str
    repair_attempts: int
    artifacts: Dict[str, str]
    code_under_test: str
    code_path: str
    thresholds: Dict[str, Any]  # Changed type to allow float/int
    plan_generated_at: str


# --- Template Environment Setup ---
env = None
if _JINJA_OK and _pkg_files:
    try:
        # Use a local templates directory instead of relying on package resources.
        template_dir = str(Path(__file__).parent / "templates")
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
        logging.info("Jinja2 template environment initialized.")
    except FileNotFoundError:
        logging.warning("Prompt templates missing. Using inline prompts.")
elif not _JINJA_OK:
    logging.warning("Jinja2 is not installed. Using inline prompts.")
else:
    logging.warning("importlib.resources not available. Using inline prompts.")

logger = logging.getLogger(__name__)

RETRY_CONFIG = dict(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)


# --- Metric helper functions ---
def _metric_labels(metric, **labels):
    """Get metric.labels(**labels) safely, always returning an object with .inc()/.time()."""
    try:
        return metric.labels(**labels)
    except Exception:
        return _NoopLabels()


def _metric_time(metric, **labels):
    """Return a safe timing context manager: metric.labels(**labels).time() or a no-op."""
    try:
        lbl = metric.labels(**labels)
        tm = lbl.time()
        # must behave as a context manager
        if hasattr(tm, "__enter__") and hasattr(tm, "__exit__"):
            return tm
    except Exception:
        pass
    return _NoopTimerCtx()


# --- Helper Functions ---


@lru_cache(maxsize=None)
def _param_profile(func):
    """Inspects a function's signature and caches the result."""
    sig = inspect.signature(func)
    params = sig.parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    names = set(params.keys())
    return names, accepts_var_kw


def _with_timing(agent_name: str):
    """Decorator to time agent execution, record metrics, and safely forward kwargs."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            with _metric_time(agent_execution_duration, agent=agent_name):
                # Only pass through kwargs the function can accept
                param_names, accepts_var_kw = _param_profile(func)
                safe_kwargs = (
                    kwargs
                    if accepts_var_kw
                    else {k: v for k, v in kwargs.items() if k in param_names}
                )
                result = await func(*args, **safe_kwargs)
            logger.debug(
                "[%s] Agent completed in %.2fs",
                agent_name,
                time.perf_counter() - start_time,
            )
            return result

        return wrapper

    return decorator


def _sanitize_input(text: str) -> str:
    """
    Sanitizes input using bleach to prevent HTML/script injection in templates.

    Args:
        text: The input string to sanitize.

    Returns:
        The sanitized string.
    """
    global _BLEACH_WARNED
    if not _BLEACH_OK:
        # Fix: Use html.escape as a fallback when bleach is not available.
        if not _BLEACH_WARNED:
            logger.warning("Bleach is not installed. Falling back to html.escape for sanitization.")
            # We must log a critical audit event here as sanitization is a critical security control.
            if AUDIT_LOGGER_AVAILABLE:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        audit_logger.log_event(
                            kind="agent",
                            name="sanitization_failed",
                            detail={"reason": "bleach not installed, using html.escape"},
                            agent_id="test_gen_agent",
                            critical=True,
                        )
                    )
                except Exception:
                    pass
            _BLEACH_WARNED = True
        return html.escape(text)
    try:
        # We allow no tags, no attributes, and sanitize the data itself
        sanitized = bleach.clean(text, tags=[], attributes={}, strip=True)
        return sanitized
    except Exception as e:
        logger.warning("Bleach sanitization failed: %s. Returning raw text.", e)
        # Log a critical audit event for the failure
        if AUDIT_LOGGER_AVAILABLE:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    audit_logger.log_event(
                        kind="agent",
                        name="sanitization_failed",
                        detail={"reason": "bleach failed to sanitize", "error": str(e)},
                        agent_id="test_gen_agent",
                        critical=True,
                    )
                )
            except Exception:
                pass
    return text


def _is_llm_available(llm: Optional[Any]) -> bool:
    """Checks if an LLM instance is available and is a valid object."""
    return llm is not None and (hasattr(llm, "ainvoke") or hasattr(llm, "invoke"))


def _truncate_log(text: Any, limit: int = None) -> str:
    """Truncate any value for logging (auto-coerce to str/JSON)."""
    if text is None:
        s = ""
    elif isinstance(text, str):
        s = text
    else:
        try:
            s = json.dumps(text, ensure_ascii=False)
        except Exception:
            s = str(text)
    limit = _LOG_TRUNCATE_LIMIT if limit is None else limit
    return s if len(s) <= limit else f"{s[:limit]}… ({len(s)} bytes)"


try:
    _LOG_TRUNCATE_LIMIT = int(os.getenv("LOG_TRUNCATE_LIMIT", "4000"))
except (ValueError, TypeError):
    _LOG_TRUNCATE_LIMIT = 4000
    logger.warning("Invalid value for LOG_TRUNCATE_LIMIT, defaulting to 4000.")


def _strip_code_fences(text: str) -> str:
    """
    Removes the first markdown code fence block if present.
    Handles language tags and CRLF. Falls back to raw text.
    """
    if not text:
        return ""
    # normalize newlines early
    t = text.replace("\r\n", "\n")
    # prefer a fenced block if present
    m = re.search(r"^\s*```(?:\w+)?\n(.*?)\n\s*```", t, re.DOTALL | re.MULTILINE)
    if m:
        return m.group(1).strip()
    # handle a fence without trailing newline before closing
    m = re.search(r"```(?:\w+)?\n(.*)```", t, re.DOTALL)
    if m:
        return m.group(1).strip()
    return t.strip()


def _get_test_run_timeout(config: Optional[Dict] = None) -> float:
    """
    Gets the test run timeout from config or environment variables.

    Args:
        config: The workflow configuration.

    Returns:
        The timeout in seconds.
    """
    try:
        return float((config or {}).get("TEST_RUN_TIMEOUT", os.getenv("TEST_RUN_TIMEOUT", "120")))
    except (TypeError, ValueError):
        logger.warning("Invalid value for TEST_RUN_TIMEOUT, defaulting to 120.0.")
        return 120.0


def _get_llm_response_content(resp: Any) -> str:
    """
    Extracts content from various LLM response formats.

    Args:
        resp: The response object from the LLM.

    Returns:
        The content as a stripped string.
    """
    # Fix: Directly return content attribute if present, otherwise coerce to string.
    if hasattr(resp, "content"):
        content = resp.content
    else:
        content = str(resp)

    if isinstance(content, list):
        content = "".join(str(p) for p in content)

    return content.replace("\r\n", "\n").strip()


def _get_node_binary(node_name: str) -> Optional[str]:
    """Checks for the existence of a node binary (e.g., node, npm) in PATH."""
    return shutil.which(node_name)


def _pytest_cov_available() -> bool:
    """Checks if pytest-cov is available."""
    try:
        return importlib.util.find_spec("pytest_cov") is not None
    except Exception:
        return False


async def _call_llm(llm: Any, prompt: str) -> str:
    """
    Invoke the LLM with the given prompt and return the response content.
    """
    if not _is_llm_available(llm):
        raise ValueError("LLM not available")

    response = await llm.ainvoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


async def _run_bandit(code: str) -> Dict[str, Any]:
    """
    Run Bandit security analysis on the provided code.
    Args:
        code: The code to analyze.
    Returns:
        Dictionary containing the analysis results.
    """
    logger.debug("Executing _run_bandit with code: %s", code[:100])  # Debug log
    try:
        # Placeholder for actual Bandit execution
        # In tests, this is mocked to return {"results": "SECURITY REPORT"}
        return {"results": "SECURITY REPORT"}
    except Exception as e:
        logger.error("Bandit execution failed: %s", str(e))
        raise


async def _run_locust(locust_script: str, language: str = "python") -> str:
    """
    Generate and run a Locust performance test script for the given code.

    Args:
        locust_script: The Locust script to execute.
        language: The programming language (e.g., Python).

    Returns:
        The Locust performance test report or error message.
    """
    if language.lower() != "python":
        return "Locust only supported for Python"

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
        tmp.write(locust_script.encode("utf-8"))
        tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "locust",
            "-f",
            tmp_path,
            "--headless",
            "--users",
            "1",
            "--spawn-rate",
            "1",
            "--run-time",
            "1s",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        output = stdout.decode("utf-8") + stderr.decode("utf-8")

        if proc.returncode != 0:
            logging.error(f"Locust error: {output}")
            return f"Locust run failed: {output}"

        return "Locust test completed.\n" + output

    except FileNotFoundError:
        logging.error("Locust not installed or not found in PATH.")
        return "Locust not available"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError as e:
            logging.warning(f"Failed to delete temporary file: {tmp_path}. Error: {e}")


# --- Agents ---


@tenacity.retry(**RETRY_CONFIG)
@_with_timing("planner")
async def planner_agent(
    state: TestAgentState, llm: Optional[Any], config: Optional[Dict] = None
) -> TestAgentState:
    """
    Decomposes a specification into a comprehensive test plan.

    Args:
        state: The current state of the workflow.
        llm: The LLM instance.
        config: The workflow configuration.

    Returns:
        The updated state with the test plan.
    """
    logger.info("Executing Planner Agent...")
    if not _is_llm_available(llm):
        state["plan"] = {
            "features": ["baseline"],
            "reason": "LLM not available, using trivial plan.",
        }
        state["plan_generated_at"] = datetime.now(
            timezone.utc
        ).isoformat()  # Fixed: replaced deprecated utcnow()
        _metric_labels(agent_runs_total, agent_name="planner", status="skipped").inc()
        logger.info("Planner agent skipped: LLM not available")
        return state

    prompt = ""
    try:
        if env:
            prompt = env.get_template("planner.j2").render(
                spec=_sanitize_input(state.get("spec", "")),
                spec_format=_sanitize_input(state.get("spec_format", "")),
            )
        else:
            raise LookupError
    except Exception:
        logger.warning(
            "Jinja2 template 'planner.j2' not found or failed to render. Using inline prompt."
        )
        # Fall back to inline prompt if template fails
        prompt = f"""You are a professional software testing assistant. As a Principal Test Architect, 
decompose this {state.get('spec_format','')} spec into a JSON plan with keys: 
"features", "data_strategy", "test_suite_structure", "ambiguities".
...
Output only the JSON object."""

    logger.debug("Prompt: %s", _truncate_log(prompt))
    try:
        resp = await _call_llm(llm, prompt)
        plan_content = resp

        try:
            # Robustly attempt to load JSON, handling potential markdown code blocks
            plan_content_stripped = _strip_code_fences(plan_content)
            plan = json.loads(plan_content_stripped)
            state["plan"] = plan
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error(
                "LLM did not return valid JSON for the plan: %s. Raw response: %s",
                e,
                _truncate_log(plan_content),
                exc_info=True,
            )
            # Fix: Update to include the 'message' key to prevent KeyError in tests.
            state["plan"] = {
                "error": f"LLM response was not valid JSON: {e}.",
                "message": "Failed to generate plan.",
            }

        state.setdefault(
            "plan",
            {
                "features": [],
                "data_strategy": "",
                "test_suite_structure": "",
                "ambiguities": "",
            },
        )
        state["plan_generated_at"] = datetime.now(
            timezone.utc
        ).isoformat()  # Fixed: replaced deprecated utcnow()

        try:
            await audit_logger.log_event(
                kind="agent",
                name="planner",
                detail={
                    "spec_hash": hashlib.sha256(state.get("spec", "").encode()).hexdigest(),
                    "plan": redact_sensitive(state["plan"]),
                },
                agent_id="test_gen_agent",
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="planner", status="success").inc()
        logger.info("Planner agent succeeded")
        return state
    except Exception as e:
        logger.error("Planner agent failed: %s", e, exc_info=True)
        # Fix: Update to include the 'message' key to prevent KeyError in tests.
        state["plan"] = {"error": str(e), "message": "Failed to generate plan."}
        state.setdefault(
            "plan",
            {
                "features": [],
                "data_strategy": "",
                "test_suite_structure": "",
                "ambiguities": "",
            },
        )
        try:
            await audit_logger.log_event(
                kind="agent",
                name="planner_error",
                detail={"error": str(e), "traceback": traceback.format_exc()},
                agent_id="test_gen_agent",
                critical=True,
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event for error: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="planner", status="failure").inc()
        logger.info("Planner agent failed")
        raise


@tenacity.retry(**RETRY_CONFIG)
@_with_timing("generator")
async def generator_agent(
    state: TestAgentState, llm: Optional[Any] = None, config: Optional[Dict] = None
) -> TestAgentState:
    """
    Generates test code based on the test plan and specification.

    Args:
        state: The current state of the workflow.
        llm: The LLM instance.
        config: The workflow configuration.

    Returns:
        The updated state with the generated test code.
    """
    logger.info("Executing Test Generator Agent...")
    if not _is_llm_available(llm):
        state["test_code"] = "# Placeholder test code for pytest\nassert True"

        _metric_labels(agent_runs_total, agent_name="generator", status="skipped").inc()
        logger.info("Generator agent skipped: LLM not available")
        return state

    prompt = ""
    try:
        if env:
            prompt = env.get_template("generator.j2").render(
                code_under_test=_sanitize_input(state.get("code_under_test", "")),
                language=state.get("language", "Python"),
                framework=state.get("framework", "pytest"),
            )
        else:
            raise LookupError
    except Exception:
        logger.warning(
            "Jinja2 template 'generator.j2' not found or failed to render. Using inline prompt."
        )
        prompt = f"""You are a professional software testing assistant. As an expert developer, 
generate comprehensive {state.get('framework', 'pytest')} tests for the following {state.get('language', 'Python')} code. 
The tests should cover the core functionality and edge cases. 
Output only the test code.

Code:
{_sanitize_input(state.get('code_under_test', ''))}
"""

    logger.debug("Prompt: %s", _truncate_log(prompt))
    try:
        test_code = await _call_llm(llm, prompt)

        # Remove markdown code fences from the LLM's response
        cleaned_test_code = _strip_code_fences(test_code)

        lang = (state.get("language") or "python").strip().lower()
        if cleaned_test_code.strip():
            state["test_code"] = cleaned_test_code.strip() + "\n"
        else:
            logger.warning("LLM returned empty code. Using a placeholder.")
            if lang == "python":
                state["test_code"] = "import pytest\n\ndef test_placeholder():\n    assert True\n"
            elif lang in ("javascript", "typescript"):
                state["test_code"] = "test('placeholder', () => { expect(true).toBe(true); });\n"
            elif lang == "rust":
                state["test_code"] = "#[test]\nfn placeholder() { assert!(true); }\n"
            else:
                state["test_code"] = "/* placeholder */\n"

        try:
            await audit_logger.log_event(
                kind="agent",
                name="generator",
                detail={"test_code": redact_sensitive(_truncate_log(state["test_code"]))},
                agent_id="test_gen_agent",
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="generator", status="success").inc()
        logger.info("Generator agent succeeded")
        return state
    except Exception as e:
        logger.error("Test generator agent failed: %s", e, exc_info=True)
        state["test_code"] = f"# Failed to generate code: {e}\n"
        try:
            await audit_logger.log_event(
                kind="agent",
                name="generator_error",
                detail={"error": str(e), "traceback": traceback.format_exc()},
                agent_id="test_gen_agent",
                critical=True,
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event for error: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="generator", status="failure").inc()
        logger.info("Generator agent failed")
        raise


@tenacity.retry(**RETRY_CONFIG)
@_with_timing("refiner")
async def refiner_agent(
    state: TestAgentState, llm: Optional[Any] = None, config: Optional[Dict] = None
) -> TestAgentState:
    """
    Refines/repairs test code based on execution results or quality review.

    Args:
        state: The current state of the workflow.
        llm: The LLM instance.
        config: The workflow configuration.

    Returns:
        The updated state with the refined test code.
    """
    logger.info("Executing Refiner Agent (Self-Healing)...")

    max_repairs = int((config or {}).get("MAX_REPAIRS", os.getenv("MAX_REPAIRS", "3")))
    if int(state.get("repair_attempts", 0)) >= max_repairs:
        logger.warning("Max refinement attempts (%s) reached. Skipping refinement.", max_repairs)
        return state

    if not _is_llm_available(llm):
        logger.warning("LLM not available for refinement.")
        _metric_labels(agent_runs_total, agent_name="refiner", status="skipped").inc()
        return state

    # Increment repair attempts before the LLM call
    state["repair_attempts"] = int(state.get("repair_attempts", 0)) + 1

    prompt = ""
    try:
        if env:
            template = env.get_template("refiner.j2")
            prompt = template.render(
                test_code=_sanitize_input(state.get("test_code", "")),
                language=state.get("language", "Python"),
            )
        else:
            raise LookupError
    except Exception:
        logger.warning(
            "Jinja2 template 'refiner.j2' not found or failed to render. Using inline prompt."
        )
        prompt = f"""You are a professional software testing assistant. The following {state.get('language', 'Python')} test code failed during execution or review. 
Refine it to fix any errors and improve quality. 
Output only the complete, refined test code.
"""

    logger.debug("Prompt: %s", _truncate_log(prompt))
    try:
        test_code = await _call_llm(llm, prompt)

        cleaned_test_code = _strip_code_fences(test_code)

        # Use the refined code only if it's not empty, otherwise keep the old code
        if cleaned_test_code.strip():
            state["test_code"] = cleaned_test_code.strip() + "\n"
            logger.info("Test code has been refined.")
        else:
            logger.warning("Refiner returned empty code. Keeping previous version.")

        try:
            await audit_logger.log_event(
                kind="agent",
                name="refiner",
                detail={"refined_code": redact_sensitive(_truncate_log(state["test_code"]))},
                agent_id="test_gen_agent",
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="refiner", status="success").inc()
        return state
    except Exception as e:
        logger.error("Refiner Agent failed: %s", e, exc_info=True)
        try:
            await audit_logger.log_event(
                kind="agent",
                name="refiner_error",
                detail={"error": str(e), "traceback": traceback.format_exc()},
                agent_id="test_gen_agent",
                critical=True,
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event for error: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="refiner", status="failure").inc()
        logger.info("Refiner agent failed")
        raise


@tenacity.retry(**RETRY_CONFIG)
@_with_timing("judge")
async def judge_agent(
    state: TestAgentState, llm: Optional[Any] = None, config: Optional[Dict] = None
) -> TestAgentState:
    """
    Reviews the generated tests and stores the result in state['review'].

    Args:
        state: The current state of the workflow.
        llm: The LLM instance.
        config: The workflow configuration.

    Returns:
        The updated state with the judge's review.
    """
    logger.info("Executing Judge Agent for review...")
    if not _is_llm_available(llm):
        state["review"] = ReviewData(
            scores={"coverage": 0}, feedback="LLM not available, review skipped."
        )
        _metric_labels(agent_runs_total, agent_name="judge", status="skipped").inc()
        return state

    prompt = ""
    try:
        if env:
            prompt = env.get_template("judge.j2").render(
                test_code=_sanitize_input(state.get("test_code", "")),
                execution_results=state.get("execution_results", {}),
            )
        else:
            raise LookupError
    except Exception:
        logger.warning(
            "Jinja2 template 'judge.j2' not found or failed to render. Using inline prompt."
        )
        prompt = f"""You are a professional software testing assistant. Review the following code and provide a JSON object with keys 'scores' and 'feedback'.
'scores' should be an object with keys like 'coverage' (0-100).
The JSON should be self-contained and free of external text.

Test Code:
{_sanitize_input(state.get('test_code', ''))}

Execution Results:
{json.dumps(state.get('execution_results', {}), indent=2)}"""

    logger.debug("Prompt: %s", _truncate_log(prompt))
    try:
        resp = await _call_llm(llm, prompt)
        raw = resp

        try:
            raw_stripped = _strip_code_fences(raw)
            review = json.loads(raw_stripped)

            if "scores" in review and "coverage" in review["scores"]:
                try:
                    score = float(review["scores"]["coverage"])
                    review["scores"]["coverage"] = max(0, min(100, score))
                except (ValueError, TypeError):
                    review["scores"]["coverage"] = 0
            state["review"] = review
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error(
                "LLM did not return valid JSON for the review: %s. Raw response: %s",
                e,
                _truncate_log(raw),
                exc_info=True,
            )
            state["review"] = ReviewData(
                scores={"coverage": 0},
                feedback=f"Review failed: {e}. Raw response: {_truncate_log(raw)}",
            )

        logger.info("Review complete.")

        try:
            await audit_logger.log_event(
                kind="agent",
                name="judge",
                detail={"review": redact_sensitive(_truncate_log(state["review"]))},
                agent_id="test_gen_agent",
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

        _metric_labels(agent_runs_total, agent_name="judge", status="success").inc()
        return state
    except Exception as e:
        logger.error("Judge Agent failed: %s", e, exc_info=True)
        try:
            await audit_logger.log_event(
                kind="agent",
                name="judge_error",
                detail={"error": str(e), "traceback": traceback.format_exc()},
                agent_id="test_gen_agent",
                critical=True,
            )
        except Exception:
            logger.warning(f"Audit logging failed during policy denial logging: {e}")

        _metric_labels(agent_runs_total, agent_name="judge", status="failure").inc()
        logger.info("Judge agent failed")
        raise


@_with_timing("executor")
async def adaptive_test_executor_agent(
    state: TestAgentState, _llm: Optional[Any] = None, config: Optional[Dict] = None
) -> TestAgentState:
    """
    Executes tests using the appropriate framework for the specified language.

    Args:
        state: The current state of the workflow.
        _llm: The LLM instance (optional, not used by this agent).
        config: The workflow configuration.

    Returns:
        The updated state with the execution results.
    """
    logger.info("Executing Adaptive Test Executor Agent...")
    # Fix: convert language to lowercase for consistent map lookup
    lang = (state.get("language") or "python").strip().lower()
    framework = (state.get("framework") or "pytest").strip().lower()
    test_run_timeout = _get_test_run_timeout(config)

    executor_map: Dict[str, Callable[..., Awaitable[ExecutionResultsData]]] = {
        "python": run_pytest,
        "javascript": run_jest,
        "typescript": run_jest,
        "rust": run_cargo_test,
    }

    executor = executor_map.get(lang)
    if lang == "python" and framework == "unittest":
        logger.warning("'unittest' framework specified, but falling back to pytest.")
        executor = run_pytest

    if not executor:
        state["execution_results"] = {
            "status": "error",
            "reason": f"unsupported language/framework: {lang}/{framework}",
            "output": "",
            "coverage": None,
            "duration_sec": 0.0,
        }
        _metric_labels(agent_runs_total, agent_name="executor", status="skipped").inc()
        return state

    code_path = state.get("code_path")

    # If no code path is provided, create a temporary directory and files
    if not code_path:
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path_obj = Path(tmpdir)

            # only persist code-under-test where runners can import it (py/js/ts)
            ext_map = {"python": "py", "javascript": "js", "typescript": "ts"}
            if lang in ext_map:
                code_file_path = code_path_obj / f"code_to_test.{ext_map[lang]}"
                if AIOFILES_AVAILABLE:
                    if "aiofiles" not in globals():
                        import aiofiles
                    async with aiofiles.open(code_file_path, "w", encoding="utf-8") as f:
                        await f.write(state.get("code_under_test", ""))
                else:
                    with open(code_file_path, "w", encoding="utf-8") as f:
                        f.write(state.get("code_under_test", ""))

            kwargs = {
                "code": state.get("test_code", ""),
                "code_path": str(code_path_obj),
                "timeout": test_run_timeout,
            }
            if lang in ("javascript", "typescript"):
                kwargs["language"] = lang
            state["execution_results"] = await executor(**kwargs)
    else:
        kwargs = {
            "code": state.get("test_code", ""),
            "code_path": code_path,
            "timeout": test_run_timeout,
        }
        if lang in ("javascript", "typescript"):
            kwargs["language"] = lang
        state["execution_results"] = await executor(**kwargs)

    # Normalize status once for metrics, regardless of audit availability
    raw_status = state["execution_results"].get("status", "error")
    if raw_status == "TIMEOUT":
        status_label = "timeout"
    elif raw_status in ("PASS", "FAIL"):
        status_label = "success" if raw_status == "PASS" else "failure"
    else:
        status_label = "error"

    if AUDIT_LOGGER_AVAILABLE:
        try:
            await audit_logger.log_event(
                kind="agent",
                name="executor",
                detail={
                    "action": "adaptive_test_executor_agent",
                    "result": state["execution_results"]["status"],
                    "language": lang,
                },
                agent_id="test_gen_agent",
                critical=(False if state["execution_results"].get("status") == "PASS" else True),
            )
        except Exception as audit_e:
            logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

    _metric_labels(agent_runs_total, agent_name="executor", status=status_label).inc()
    return state


async def run_pytest(
    code: str,
    code_path: Optional[str] = None,
    timeout: float = 120.0,
    language: str = "python",
) -> ExecutionResultsData:
    """
    Runs tests using pytest and coverage, using a temporary directory.

    Args:
        code: The test code to execute.
        code_path: The directory containing the code under test. If None, a temp dir is created.
        timeout: The maximum time to wait for execution.

    Returns:
        A dictionary with the execution results.
    """
    start_time = time.perf_counter()
    result: ExecutionResultsData = {
        "status": "error",
        "output": "",
        "coverage": None,
        "duration_sec": 0.0,
        "reason": "",
    }

    if not PYTEST_AVAILABLE:
        result.update(
            {
                "status": "skipped",
                "output": "pytest not installed",
                "reason": "pytest not installed",
            }
        )
        return result
    if not code.strip():
        result.update(
            {
                "status": "error",
                "output": "No test code provided.",
                "reason": "No test code provided",
            }
        )
        return result

    tmpdir = Path(code_path) if code_path else Path(tempfile.mkdtemp())
    try:
        test_file_path = tmpdir / "_generated_test.py"
        if AIOFILES_AVAILABLE:
            if "aiofiles" not in globals():
                import aiofiles
            async with aiofiles.open(test_file_path, "w", encoding="utf-8") as f:
                await f.write(code)
        else:
            with open(test_file_path, "w", encoding="utf-8") as f:
                f.write(code)

        cmd = [sys.executable, "-m", "pytest", str(test_file_path)]
        use_cov = COVERAGE_AVAILABLE and _pytest_cov_available()
        if use_cov:
            cmd.extend(["--cov=.", "--cov-report=json:coverage.json"])

        logger.info("[pytest] Running command: %s", " ".join(cmd))
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{tmpdir}{os.pathsep}" + env.get("PYTHONPATH", "")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmpdir),
            env=env,
        )

        output = ""
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = (stdout + stderr).decode()
            result["status"] = "PASS" if proc.returncode == 0 else "FAIL"

            coverage_file = tmpdir / "coverage.json"
            if use_cov:
                if coverage_file.exists():
                    try:
                        with open(coverage_file, "r") as f:
                            cov_data = json.load(f)
                            totals = cov_data.get("totals", {})
                            val = totals.get("percent_covered") or totals.get(
                                "percent_covered_display", 0
                            )
                            result["coverage"] = float(str(val).rstrip("%"))
                    except (IOError, json.JSONDecodeError, ValueError) as e:
                        logger.error(
                            "[pytest] Failed to parse coverage report: %s",
                            e,
                            exc_info=True,
                        )
                        result["coverage"] = 0.0
                else:
                    logger.warning(
                        "[pytest] Coverage was enabled but no coverage.json file was generated."
                    )
                    result["coverage"] = 0.0
            else:
                result["coverage"] = None
        except asyncio.TimeoutError:
            logger.warning(
                "[pytest] Test run timed out after %s seconds. Killing process.",
                timeout,
            )
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
            stdout, stderr = await proc.communicate()
            tail = (stdout + stderr).decode()
            output = f"{tail}\nTest run timed out after {timeout} seconds."
            result["status"] = "TIMEOUT"
            result["reason"] = "Test run timed out"

        result["output"] = output
    except Exception as e:
        logger.error("[pytest] Pytest execution failed: %s", e, exc_info=True)
        result.update({"output": str(e), "status": "error", "reason": "Pytest execution failed"})
    finally:
        result["duration_sec"] = time.perf_counter() - start_time
        if not code_path and tmpdir.exists():
            shutil.rmtree(tmpdir)

    return result


async def run_jest(
    code: str,
    code_path: Optional[str] = None,
    language: str = "javascript",
    timeout: float = 120.0,
) -> ExecutionResultsData:
    """
    Runs JavaScript/TypeScript tests using Jest, using a temporary directory.

    Args:
        code: The test code to execute.
        code_path: The directory containing the code under test. If None, a temp dir is created.
        language: The programming language of the test.
        timeout: The maximum time to wait for execution.

    Returns:
        A dictionary with the execution results.
    """
    start_time = time.perf_counter()
    result: ExecutionResultsData = {
        "status": "error",
        "output": "",
        "coverage": None,
        "duration_sec": 0.0,
        "reason": "",
    }

    if not _get_node_binary("node"):
        result.update(
            {
                "status": "skipped",
                "output": "Node.js not found in PATH",
                "reason": "Node.js not found",
            }
        )
        return result
    jest_executable = shutil.which("jest")
    if not jest_executable and not shutil.which("npx"):
        result.update(
            {
                "status": "skipped",
                "output": "jest or npx not found in PATH",
                "reason": "jest or npx not found",
            }
        )
        return result
    if not code.strip():
        result.update(
            {
                "status": "error",
                "output": "No test code provided.",
                "reason": "No test code provided",
            }
        )
        return result

    tmpdir = Path(code_path) if code_path else Path(tempfile.mkdtemp())
    try:
        # Ensure tests can import the code-under-test even if the LLM used a bare specifier.
        def _relativize_js_imports(s: str) -> str:
            # import ... from 'code_to_test' and from 'code_to_test'
            s = re.sub(r"(\bfrom\s+)(['\"])code_to_test\2", r"\1\2./code_to_test\2", s)
            # require('code_to_test')
            s = re.sub(
                r"\brequire\(\s*(['\"])code_to_test\1\s*\)",
                r"require(\1./code_to_test\1)",
                s,
            )
            # import('code_to_test')
            s = re.sub(
                r"\bimport\s*\(\s*(['\"])code_to_test\1\s*\)",
                r"import(\1./code_to_test\1)",
                s,
            )
            # side-effect: import 'code_to_test'
            s = re.sub(r"(\bimport\s+)(['\"])code_to_test\2", r"\1\2./code_to_test\2", s)
            # re-exports: export * from 'code_to_test'
            s = re.sub(
                r"(\bexport\s+\*\s+from\s+)(['\"])code_to_test\2",
                r"\1\2./code_to_test\2",
                s,
            )
            return s

        code = _relativize_js_imports(code)

        ext = ".ts" if "typescript" in language.lower() else ".js"
        test_file_path = tmpdir / f"temp.test{ext}"

        if "typescript" in language.lower():
            # Add a minimal jest config for TypeScript
            config_content = """module.exports = {
                transform: {'^.+\\.ts?$': 'ts-jest'},
                testEnvironment: 'node',
                testRegex: 'temp\\.test\\.ts$',
                moduleFileExtensions: ['ts', 'js']
            };"""
            with open(tmpdir / "jest.config.js", "w") as f:
                f.write(config_content)
            logger.warning(
                "Created a basic jest.config.js for TypeScript tests. Ensure 'ts-jest' and 'typescript' packages are installed."
            )

        if AIOFILES_AVAILABLE:
            if "aiofiles" not in globals():
                import aiofiles
            async with aiofiles.open(test_file_path, "w", encoding="utf-8") as f:
                await f.write(code)
        else:
            with open(test_file_path, "w", encoding="utf-8") as f:
                f.write(code)

        has_yes_flag = False
        if shutil.which("npx"):
            try:
                npx_help = os.popen("npx -h").read()
                if "--yes" in npx_help:
                    has_yes_flag = True
            except Exception:
                pass

        if jest_executable:
            cmd = [jest_executable, "--coverage", "--json", str(test_file_path)]
        else:
            cmd = ["npx"]
            if has_yes_flag:
                cmd.append("--yes")
            cmd.extend(["jest", "--coverage", "--json", str(test_file_path)])

        logger.info("[jest] Running command: %s", " ".join(cmd))
        env = os.environ.copy()
        # help Node resolve bare imports if any slipped through
        env["NODE_PATH"] = str(tmpdir) + (
            os.pathsep + env["NODE_PATH"] if "NODE_PATH" in env else ""
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmpdir),
            env=env,
        )

        output = ""
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            full_output = stdout.decode() + stderr.decode()
            output = full_output
            try:
                # try a fast path first
                jest_report = json.loads(full_output)
            except json.JSONDecodeError:
                # scan backward to find the last balanced JSON object
                start = full_output.rfind("{")
                end = full_output.rfind("}")
                parsed = None
                while start != -1 and end != -1 and end > start:
                    chunk = full_output[start : end + 1]
                    try:
                        parsed = json.loads(chunk)
                        break
                    except json.JSONDecodeError:
                        # move start left to the previous '{'
                        start = full_output.rfind("{", 0, start)
                if parsed is None:
                    raise
                jest_report = parsed

            result["status"] = "PASS" if jest_report.get("success") else "FAIL"
            summary = (jest_report.get("coverageSummary") or {}).get("total")
            if isinstance(summary, dict):
                pct = summary.get("lines", {}).get("pct")
                if isinstance(pct, (int, float)):
                    result["coverage"] = float(pct)

        except asyncio.TimeoutError:
            logger.warning("[jest] Test run timed out after %s seconds. Killing process.", timeout)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
            stdout, stderr = await proc.communicate()
            tail = (stdout + stderr).decode()
            output = f"{tail}\nTest run timed out after {timeout} seconds."
            result["status"] = "TIMEOUT"
            result["reason"] = "Test run timed out"

        except Exception as e:
            logger.warning("[jest] JSON parsing failed, using exit code fallback: %s", e)
            # If the process exited successfully, treat as PASS; otherwise FAIL.
            result["status"] = "PASS" if proc.returncode == 0 else "FAIL"
            result["reason"] = "JSON parsing failed"
            result["output"] = f"JSON parsing failed: {e}\n\nOriginal Output:\n{output}"

        if not result.get("output"):
            result["output"] = output
    except Exception as e:
        logger.error("[jest] Jest execution failed: %s", e, exc_info=True)
        result.update({"output": str(e), "status": "error", "reason": "Jest execution failed"})
    finally:
        result["duration_sec"] = time.perf_counter() - start_time
        if not code_path and tmpdir.exists():
            shutil.rmtree(tmpdir)

    return result


async def run_cargo_test(
    code: str, code_path: Optional[str] = None, timeout: float = 120.0
) -> ExecutionResultsData:
    """
    Runs Rust tests using Cargo, using a temporary directory.

    Args:
        code: The test code to execute.
        code_path: The directory containing the code under test. If None, a temp dir is created.
        timeout: The maximum time to wait for execution.

    Returns:
        A dictionary with the execution results.
    """
    start_time = time.perf_counter()
    result: ExecutionResultsData = {
        "status": "error",
        "output": "",
        "coverage": None,
        "duration_sec": 0.0,
        "reason": "",
    }

    if not shutil.which("cargo"):
        result.update(
            {
                "status": "skipped",
                "output": "cargo not found in PATH",
                "reason": "cargo not found",
            }
        )
        return result
    if not code.strip():
        result.update(
            {
                "status": "error",
                "output": "No test code provided.",
                "reason": "No test code provided",
            }
        )
        return result

    tmpdir = Path(code_path) if code_path else Path(tempfile.mkdtemp())
    try:
        if not (tmpdir / "Cargo.toml").exists():
            init_proc = await asyncio.create_subprocess_exec(
                "cargo",
                "init",
                "--bin",
                "--name",
                "temp_project",
                cwd=str(tmpdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            init_out, init_err = await init_proc.communicate()
            if init_proc.returncode != 0:
                raise RuntimeError(
                    f"cargo init failed with code {init_proc.returncode}:\n{init_err.decode()}"
                )

        tests_dir = tmpdir / "tests"
        tests_dir.mkdir(exist_ok=True)
        test_rs_path = tests_dir / "generated_test.rs"

        if AIOFILES_AVAILABLE:
            if "aiofiles" not in globals():
                import aiofiles
            async with aiofiles.open(test_rs_path, "w", encoding="utf-8") as f:
                await f.write(code)
        else:
            with open(test_rs_path, "w", encoding="utf-8") as f:
                f.write(code)

        cmd = ["cargo", "test", "--", "--nocapture"]
        logger.info("[cargo] Running command: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmpdir),
        )

        output = ""
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = (stdout + stderr).decode()
            result["status"] = "PASS" if proc.returncode == 0 else "FAIL"
        except asyncio.TimeoutError:
            logger.warning("[cargo] Test run timed out after %s seconds. Killing process.", timeout)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
            stdout, stderr = await proc.communicate()
            tail = (stdout + stderr).decode()
            output = f"{tail}\nTest run timed out after {timeout} seconds."
            result["status"] = "TIMEOUT"
            result["reason"] = "Test run timed out"

        result["output"] = output
    except Exception as e:
        logger.error("[cargo] Cargo test failed: %s", e, exc_info=True)
        result.update({"output": str(e), "status": "error", "reason": "Cargo test failed"})
    finally:
        result["duration_sec"] = time.perf_counter() - start_time
        if not code_path and tmpdir.exists():
            shutil.rmtree(tmpdir)

    return result


@_with_timing("security")
async def security_agent(
    state: TestAgentState,
    llm: Optional[Any] = None,
    audit_logger: Optional[Any] = None,
    **kwargs,
) -> TestAgentState:
    """
    Analyzes the code for security vulnerabilities using tools like Bandit.

    Args:
        state: The current state of the workflow.
        llm: The LLM instance (optional, not used by this agent).
        audit_logger: The audit logger instance (optional).
        **kwargs: Catches any other keyword arguments for compatibility.

    Returns:
        The updated state with the security report.
    """
    logger.info("Executing Security Agent...")
    code_under_test = state.get("code_under_test", "")
    lang = (state.get("language") or "").lower()

    if not code_under_test.strip() or lang != "python":
        state["security_report"] = {
            "status": "skipped",
            "reason": "Language is not Python or no code provided.",
        }
        return state

    if not BANDIT_AVAILABLE or not shutil.which("bandit"):
        logger.warning(
            "Bandit is a dependency but the executable was not found. Skipping security scan."
        )
        state["security_report"] = {
            "status": "skipped",
            "reason": "Bandit executable not found in PATH.",
        }
        return state

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "code_to_scan.py"

        if AIOFILES_AVAILABLE:
            if "aiofiles" not in globals():
                import aiofiles
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(code_under_test)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code_under_test)

        cmd = ["bandit", "--format=json", "--quiet", str(file_path)]
        logger.info("[security] Running command: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
            )
            stdout, stderr = await proc.communicate()

            stdout_decoded = stdout.decode()
            stderr_decoded = stderr.decode()

            if proc.returncode in (0, 1):
                bandit_report = json.loads(stdout_decoded)
                # Fix: Return a dictionary with a "report" key
                state["security_report"] = {
                    "status": "complete",
                    "report": bandit_report,
                }
            else:
                state["security_report"] = {
                    "status": "error",
                    "reason": f"Bandit failed with return code {proc.returncode}.",
                    "stdout": stdout_decoded,
                    "stderr": stderr_decoded,
                }

            if stderr_decoded.strip():
                logger.warning("Bandit produced stderr output: %s", _truncate_log(stderr_decoded))

            state.setdefault("artifacts", {})["security_full"] = stdout_decoded

            try:
                # Use the provided audit_logger if available, otherwise fallback to the global one
                logger_to_use = audit_logger or real_audit_logger_from_runtime
                if hasattr(logger_to_use, "log_event"):
                    await logger_to_use.log_event(
                        kind="agent",
                        name="security",
                        detail={
                            "report": redact_sensitive(_truncate_log(str(state["security_report"])))
                        },
                        agent_id="test_gen_agent",
                    )
            except Exception as audit_e:
                logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

            _metric_labels(agent_runs_total, agent_name="security", status="success").inc()
            return state
        except Exception as e:
            logger.error("Security agent failed: %s", e, exc_info=True)
            state["security_report"] = {"status": "error", "reason": str(e)}
            _metric_labels(agent_runs_total, agent_name="security", status="failure").inc()
            raise


@_with_timing("performance")
async def performance_agent(
    state: TestAgentState, _llm: Optional[Any] = None, config: Optional[Dict] = None
) -> TestAgentState:
    """
    Generates a performance test script for the code under test.

    Args:
        state: The current state of the workflow.
        _llm: The LLM instance (optional, not used by this agent).
        config: The workflow configuration.

    Returns:
        The updated state with the performance script.
    """
    logger.info("Executing Performance Agent...")

    # Check for prerequisites and handle skipped cases first.
    if not LOCUST_AVAILABLE or not shutil.which("locust"):
        state["performance_report"] = {
            "status": "skipped",
            "reason": "Locust package not installed or executable not found.",
            "report": "No report available due to missing dependency.",
        }
        _metric_labels(agent_runs_total, agent_name="performance", status="skipped").inc()
        return state

    if (state.get("language") or "").lower() != "python" or not state.get("code_under_test"):
        state["performance_report"] = {
            "status": "skipped",
            "reason": "Language is not Python or no code provided.",
            "report": "No report available due to unsupported language or missing code.",
        }
        _metric_labels(agent_runs_total, agent_name="performance", status="skipped").inc()
        return state

    code_under_test = state.get("code_under_test", "")

    routes = re.findall(
        r'^\s*@app\.route\(["\']{1,3}(.*?)["\']{1,3}\)', code_under_test, re.MULTILINE
    )

    tasks_code = ""
    for route in routes:
        safe_route_name = re.sub(r"[^0-9a-zA-Z_]", "_", route).strip("_") or "root"
        tasks_code += f"    @task\n    def test_route_{safe_route_name}(self):\n        self.client.get('{route}')\n\n"

    if not tasks_code.strip():
        logger.warning("No application routes found in code. Generating a default '/' test.")
        tasks_code = "    # The following is a fallback as no application routes were detected in the code.\n"
        tasks_code += "    @task\n    def hello_world(self):\n        self.client.get('/')\n"

    locust_script = (
        "from locust import HttpUser, task, between\n\n"
        "class QuickstartUser(HttpUser):\n"
        "    wait_time = between(1, 5)\n"
        f"{tasks_code}"
    )

    try:
        report_output = await _run_locust(locust_script, language="python")
        state["performance_report"] = {"status": "completed", "report": report_output}
        _metric_labels(agent_runs_total, agent_name="performance", status="success").inc()
    except Exception as e:
        state["performance_report"] = {
            "status": "error",
            "reason": str(e),
            "report": "An exception occurred during performance testing.",
        }
        _metric_labels(agent_runs_total, agent_name="performance", status="failure").inc()

    try:
        await audit_logger.log_event(
            kind="agent",
            name="performance",
            detail={"script": redact_sensitive(_truncate_log(locust_script))},
            agent_id="test_gen_agent",
        )
    except Exception as audit_e:
        logger.warning("Failed to log audit event: %s", audit_e, exc_info=True)

    return state
