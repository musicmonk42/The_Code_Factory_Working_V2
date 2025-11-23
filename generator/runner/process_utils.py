"""
runner.process_utils

Robust process and subprocess orchestration utilities used by the runner.

This module is intentionally self-contained and defensive so it can be
depended on by higher-level systems and by tests without pulling in heavy
side effects at import time.

It provides:

- CircuitBreaker / get_circuit_breaker
- subprocess_wrapper: single-command execution with metrics, provenance,
  redaction, and retry-on-timeout.
- parallel_subprocess: bounded-concurrency orchestration around
  subprocess_wrapper.
- distributed_subprocess: fan-out using configured runner_backends
  (if available), with clean failure modes when the distributed
  infrastructure is not configured.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import backoff
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric stubs
# ---------------------------------------------------------------------------


class _NullMetric:
    """
    Very small Prometheus-style metric stub.

    Provides labels(...).inc(), labels(...).observe(), labels(...).set()
    so production can plug in real metrics, while tests and local runs
    remain lightweight and deterministic.
    """

    class _Child:
        def inc(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            return self

        def set(self, *args, **kwargs):
            return self

    def labels(self, *args, **kwargs):
        return self._Child()


UTIL_LATENCY = _NullMetric()
UTIL_ERRORS = _NullMetric()
UTIL_SELF_HEAL = _NullMetric()

# ---------------------------------------------------------------------------
# External integration points: error codes, RunnerError, security, telemetry
# ---------------------------------------------------------------------------

# In the full project these should come from shared modules.
# Here we wire them defensively so this file is safe in isolation.

try:  # pragma: no cover
    from runner.errors import error_codes, RunnerError  # type: ignore
except Exception:  # pragma: no cover

    class RunnerError(RuntimeError):
        def __init__(self, code: str, message: str):
            super().__init__(message)
            self.error_code = code

    error_codes: Dict[str, str] = {
        "TEST_EXECUTION_FAILED": "TEST_EXECUTION_FAILED",
        "UNEXPECTED_ERROR": "UNEXPECTED_ERROR",
    }


def _noop_detect_anomaly(*args, **kwargs):
    return None


def _noop_collect_feedback(*args, **kwargs):
    return None


def _noop_redact_secrets(
    value: Union[str, bytes, Dict[str, Any]],
) -> Union[str, bytes, Dict[str, Any]]:
    return value


def _noop_add_provenance(data: Dict[str, Any], action: Optional[str] = None) -> Dict[str, Any]:
    if "provenance" not in data:
        data["provenance"] = {
            "source": "process_utils",
            "action": action or "unspecified",
        }
    return data


try:  # pragma: no cover
    from runner.observability import detect_anomaly, collect_feedback, add_provenance  # type: ignore
except Exception:  # pragma: no cover
    detect_anomaly = _noop_detect_anomaly
    collect_feedback = _noop_collect_feedback
    add_provenance = _noop_add_provenance

try:  # pragma: no cover
    from runner.security import redact_secrets, encrypt_data, decrypt_data  # type: ignore
except Exception:  # pragma: no cover
    redact_secrets = _noop_redact_secrets

    def encrypt_data(data: bytes, key: Any, algorithm: str = "fernet") -> bytes:
        return data

    def decrypt_data(data: bytes, key: Any, algorithm: str = "fernet") -> bytes:
        return data


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Simple async-aware circuit breaker.

    States:
      - CLOSED: calls flow normally. Failures increment counter.
      - OPEN: calls are blocked until recovery_timeout elapses.
      - HALF-OPEN: a single trial call is allowed; success -> CLOSED,
        failure -> OPEN.

    Failure semantics:
      - Until the threshold is hit, the original exception is propagated.
      - When the threshold is crossed, the breaker moves to OPEN and the
        triggering exception is propagated.
      - While OPEN (and before timeout), calls raise RunnerError with a
        structured error code.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "default",
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name

        self.state: str = "CLOSED"
        self.failures: int = 0
        self.last_failure_time: float = 0.0

    async def reset(self) -> None:
        self.state = "CLOSED"
        self.failures = 0
        self.last_failure_time = 0.0

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        # If OPEN, decide whether to allow a HALF-OPEN probe.
        if self.state == "OPEN":
            now = time.time()
            if now - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF-OPEN"
                logger.info(
                    "Circuit '%s' for %s is HALF-OPEN. Attempting recovery.",
                    self.name,
                    getattr(func, "__name__", repr(func)),
                )
            else:
                logger.warning(
                    "Circuit '%s' for %s is OPEN. Call blocked.",
                    self.name,
                    getattr(func, "__name__", repr(func)),
                )
                detect_anomaly(
                    f"circuit_breaker_open_{self.name}",
                    1,
                    0,
                    severity="high",
                    anomaly_type="circuit_open",
                )
                raise RunnerError(
                    error_codes["TEST_EXECUTION_FAILED"],
                    f"Circuit '{self.name}' is OPEN. Execution blocked.",
                )

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
        except Exception:
            # Record failure
            self.failures += 1
            self.last_failure_time = time.time()

            # Threshold reached -> OPEN
            if self.failures >= self.failure_threshold:
                if self.state != "OPEN":
                    logger.error(
                        "Circuit '%s' transitioning to OPEN after %d failures.",
                        self.name,
                        self.failures,
                    )
                self.state = "OPEN"
                detect_anomaly(
                    f"circuit_breaker_trip_{self.name}",
                    self.failures,
                    self.failure_threshold,
                    severity="high",
                    anomaly_type="circuit_trip",
                )

            # Propagate original exception to caller.
            raise

        # Successful call -> reset breaker.
        await self.reset()
        return result


_CIRCUIT_BREAKERS: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str) -> CircuitBreaker:
    if name not in _CIRCUIT_BREAKERS:
        _CIRCUIT_BREAKERS[name] = CircuitBreaker(name=name)
    return _CIRCUIT_BREAKERS[name]


# ---------------------------------------------------------------------------
# subprocess_wrapper
# ---------------------------------------------------------------------------


async def _run_subprocess_once(
    cmd: List[str],
    timeout: Optional[float] = None,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Internal helper to run a subprocess exactly once using asyncio.

    Returns a normalized result dict. Does not apply backoff or circuit-breaking.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    # Use asyncio subprocess instead of blocking subprocess.run
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
        env=full_env,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        returncode = process.returncode
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise subprocess.TimeoutExpired(cmd, timeout)

    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
    stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

    stdout = redact_secrets(stdout)
    stderr = redact_secrets(stderr)

    success = returncode == 0

    result: Dict[str, Any] = {
        "success": success,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }

    result = add_provenance(result, action="subprocess_wrapper")

    if not success:
        UTIL_ERRORS.labels("subprocess_wrapper", "NonZeroExit").inc()
        logger.error(
            "Subprocess failed: cmd=%r rc=%s stderr=%s",
            cmd,
            returncode,
            stderr,
        )

    return result


@backoff.on_exception(
    backoff.expo,
    subprocess.TimeoutExpired,
    max_tries=3,
    factor=0.5,
)
async def subprocess_wrapper(
    cmd: List[str],
    timeout: Optional[float] = None,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Dict[str, str]] = None,
    circuit_breaker_name: Optional[str] = None,  # <-- [FIX] ADD THIS ARGUMENT
) -> Dict[str, Any]:
    """
    Run a command with standardized behavior:

    - Per-function CircuitBreaker.
    - Retries on TimeoutExpired with exponential backoff.
    - Captures and redacts stdout/stderr.
    - Emits latency/error metrics.
    - Attaches provenance.
    """
    # [FIX] Use the argument, with a fallback to the original hardcoded name
    breaker_name = circuit_breaker_name or "subprocess_wrapper"
    breaker = get_circuit_breaker(breaker_name)

    async def _invoke():
        start = time.time()
        try:
            return await _run_subprocess_once(
                cmd,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
        finally:
            elapsed = max(time.time() - start, 0.0)
            UTIL_LATENCY.labels("subprocess_wrapper").observe(elapsed)

    return await breaker.call(_invoke)


# ---------------------------------------------------------------------------
# parallel_subprocess
# ---------------------------------------------------------------------------


async def parallel_subprocess(
    commands: List[List[str]],
    max_workers: int = 4,
    timeout: Optional[float] = None,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Run multiple commands concurrently via subprocess_wrapper.

    - Concurrency limited by max_workers.
    - Failures for individual commands are logged and counted but do not
      abort other commands.
    - Only entries with success=True are returned.
    """
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    sem = asyncio.Semaphore(max_workers)
    results: List[Optional[Dict[str, Any]]] = [None] * len(commands)

    async def worker(idx: int, cmd: List[str]) -> None:
        async with sem:
            try:
                res = await subprocess_wrapper(
                    cmd,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                )
                if res.get("success"):
                    results[idx] = res
                else:
                    UTIL_ERRORS.labels("parallel_subprocess", "CommandFailed").inc()
                    logger.error(
                        "parallel_subprocess: command failed idx=%d cmd=%r rc=%s",
                        idx,
                        cmd,
                        res.get("returncode"),
                    )
            except Exception as exc:
                UTIL_ERRORS.labels("parallel_subprocess", type(exc).__name__).inc()
                logger.exception(
                    "parallel_subprocess: exception for idx=%d cmd=%r: %s",
                    idx,
                    cmd,
                    exc,
                )

    await asyncio.gather(
        *(worker(i, c) for i, c in enumerate(commands)),
        return_exceptions=False,
    )

    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# distributed_subprocess
# ---------------------------------------------------------------------------

_HAS_RUNNER = False
BACKENDS: Dict[str, Any] = {}
config: Any = None

try:  # pragma: no cover
    from runner import runner_backends  # type: ignore
    from runner.runner_config import RunnerConfig  # type: ignore

    if hasattr(runner_backends, "BACKEND_REGISTRY"):
        BACKENDS = dict(runner_backends.BACKEND_REGISTRY)
        if hasattr(RunnerConfig, "from_env"):
            config = RunnerConfig.from_env()
        else:
            config = RunnerConfig()
        _HAS_RUNNER = True
except Exception:  # pragma: no cover
    runner_backends = None  # type: ignore
    BACKENDS = {}
    config = None
    _HAS_RUNNER = False


async def distributed_subprocess(
    commands: List[List[str]],
    backend: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Execute commands using a distributed backend.

    Contract:
    - If runner infrastructure is unavailable -> RuntimeError.
    - If backend is missing/unknown -> ValueError.
    - For a valid backend:
        - Constructs tasks and calls runner.parallel_runs().
        - Normalizes stdout/stderr to strings.
        - Attaches provenance.
        - Increments metrics on failure.
    """
    if not _HAS_RUNNER:
        logger.error("distributed_subprocess requested but runner infrastructure is not available.")
        raise RuntimeError("Distributed runner infrastructure is not available")

    selected_backend = backend or getattr(config, "backend", None)
    if not selected_backend:
        raise ValueError("No backend specified for distributed_subprocess")

    if selected_backend not in BACKENDS:
        raise ValueError(f"Unknown backend '{selected_backend}' for distributed_subprocess")

    if not hasattr(runner_backends, "BACKEND_REGISTRY"):
        raise RuntimeError("runner_backends.BACKEND_REGISTRY not available")

    backend_factory = runner_backends.BACKEND_REGISTRY.get(selected_backend)
    if backend_factory is None:
        raise ValueError(f"Backend '{selected_backend}' has no registered factory")

    runner = backend_factory(config)

    tasks: List[Dict[str, Any]] = []
    for idx, cmd in enumerate(commands):
        tasks.append(
            {
                "id": idx,
                "cmd": cmd,
            }
        )

    try:
        raw_results: List[Dict[str, Any]] = await runner.parallel_runs(tasks)
    except Exception as exc:
        logger.error(
            "Distributed subprocess execution failed with backend %s: %s",
            selected_backend,
            exc,
            exc_info=True,
        )
        UTIL_ERRORS.labels("distributed_subprocess", type(exc).__name__).inc()
        raise

    processed_results: List[Dict[str, Any]] = []
    for entry in raw_results:
        rc = int(entry.get("returncode", 0))
        out_raw = entry.get("stdout", b"")
        err_raw = entry.get("stderr", b"")

        if isinstance(out_raw, bytes):
            out = out_raw.decode("utf-8", errors="replace")
        else:
            out = str(out_raw)

        if isinstance(err_raw, bytes):
            err = err_raw.decode("utf-8", errors="replace")
        else:
            err = str(err_raw)

        out = redact_secrets(out)
        err = redact_secrets(err)

        res: Dict[str, Any] = {
            "success": rc == 0,
            "stdout": out,
            "stderr": err,
            "returncode": rc,
        }
        res = add_provenance(res, action="distributed_subprocess")

        if not res["success"]:
            UTIL_ERRORS.labels("distributed_subprocess", "NonZeroExit").inc()

        processed_results.append(res)

    return processed_results
