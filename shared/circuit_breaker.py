# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified circuit-breaker implementation for the Code Factory platform.

Problem
-------
Three independent ``CircuitBreaker`` implementations existed in the codebase,
each supporting a different call pattern:

- ``generator/runner/process_utils.py`` — wrap-style: ``await cb.call(func)``
- ``generator/runner/llm_client.py``    — provider-keyed: ``await cb.allow_request(provider)``
- ``generator/clarifier/clarifier.py`` — guard-style: ``cb.is_open()`` /
  ``cb.record_failure(exc)`` / ``cb.record_success()``

Each copy had different timeout semantics, different thread-safety guarantees,
and different Prometheus integration — creating drift that led to observable
bugs during provider failover.

Solution
--------
This module provides a single, production-quality implementation with:

* **Three usage patterns** in one class — Pattern A (wrap), B (guard),
  C (provider-keyed) — so existing call-sites require no structural change.
* **Separate threading locks** — one lock for instance-level state (Patterns
  A & B) and one for the provider-keyed dict (Pattern C).
* **Monotonic clock** — all timeout comparisons use ``time.monotonic()`` to
  avoid wall-clock skew and DST transitions.
* **Prometheus metrics** — emitted via :func:`shared.noop_metrics.safe_metric`
  with graceful degradation when ``prometheus_client`` is absent.
* **Optional gauge injection** — callers may pass ``state_metric_gauge``
  to reuse an already-registered ``Gauge`` (e.g.
  ``runner_metrics.LLM_CIRCUIT_STATE``).
* **Module-level registry** — :func:`get_circuit_breaker` vends named
  singletons, protected by a dedicated ``threading.Lock``.

Architecture
------------
::

    Caller (Pattern A)         Caller (Pattern B)         Caller (Pattern C)
         │                          │                          │
         │ await cb.call(fn)        │ cb.is_open()             │ await cb.allow_request(prov)
         │                          │ cb.record_failure(exc)   │ cb.record_failure(prov)
         ▼                          ▼                          ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                      CircuitBreaker                             │
    │                                                                 │
    │  ┌──────────────────────┐   ┌──────────────────────────────┐   │
    │  │  Instance State      │   │  Provider-keyed State        │   │
    │  │  (_inst_lock)        │   │  (_prov_lock)                │   │
    │  │                      │   │                              │   │
    │  │  state: CLOSED       │   │  _prov_state["openai"]:      │   │
    │  │  _error_count: int   │   │    CLOSED | OPEN | HALF-OPEN │   │
    │  │  _trip_time: float   │   │  _prov_failures["openai"]: 3 │   │
    │  └──────────────────────┘   └──────────────────────────────┘   │
    │                                                                 │
    │  Prometheus metrics (safe_metric — no-op when lib absent)       │
    │    circuit_breaker_state_info{breaker,provider}    Gauge        │
    │    circuit_breaker_failures_total{breaker,provider} Counter     │
    │    circuit_breaker_transitions_total{breaker,provider} Counter  │
    └─────────────────────────────────────────────────────────────────┘

Usage
-----
::

    from shared.circuit_breaker import CircuitBreaker, get_circuit_breaker

    # Pattern A — wrap style
    cb = get_circuit_breaker("subprocess")
    result = await cb.call(my_async_func, arg1, arg2)

    # Pattern B — guard style
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    if cb.is_open():
        raise RuntimeError("service unavailable")
    try:
        result = do_work()
        cb.record_success()
    except Exception as exc:
        cb.record_failure(exc)
        raise

    # Pattern C — provider-keyed style
    cb = CircuitBreaker()
    if await cb.allow_request("openai"):
        try:
            result = await call_openai()
            cb.record_success("openai")
        except Exception as exc:
            cb.record_failure("openai", exc)

Industry Standards Applied
--------------------------
* **Martin Fowler Circuit Breaker pattern** — three-state machine
  (CLOSED → OPEN → HALF-OPEN → CLOSED).
* **PEP 484** — full type annotations for public API.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
* **Twelve-Factor App** — no hard-coded assumptions about Prometheus presence.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Prometheus imports via safe_metric
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import]
except ImportError:
    _Counter = None  # type: ignore[assignment]
    _Gauge = None  # type: ignore[assignment]

from shared.noop_metrics import safe_metric

_CB_STATE = safe_metric(
    _Gauge,
    "circuit_breaker_state_info",
    "Current circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF-OPEN)",
    labelnames=["breaker", "provider"],
)
_CB_FAILURES = safe_metric(
    _Counter,
    "circuit_breaker_failures_total",
    "Total circuit breaker failures recorded",
    labelnames=["breaker", "provider"],
)
_CB_TRANSITIONS = safe_metric(
    _Counter,
    "circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    labelnames=["breaker", "provider"],
)

# Map state name → numeric gauge value
_STATE_VALUE: Dict[str, float] = {"CLOSED": 0.0, "OPEN": 1.0, "HALF-OPEN": 2.0}

# ---------------------------------------------------------------------------
# Module-level registry lock and dict
# ---------------------------------------------------------------------------

_registry_lock: threading.Lock = threading.Lock()
_CIRCUIT_BREAKERS: Dict[str, "CircuitBreaker"] = {}


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Unified async-aware circuit breaker supporting three usage patterns.

    **States** (standard Martin Fowler model):

    * ``CLOSED``    — calls flow normally; failures increment the counter.
    * ``OPEN``      — calls are blocked until ``recovery_timeout`` elapses.
    * ``HALF-OPEN`` — trial calls are allowed; on success → CLOSED, on
      failure → OPEN.

    **Usage patterns**

    Pattern A — wrap style (process_utils.py)::

        cb = CircuitBreaker(name="subprocess")
        result = await cb.call(my_async_func, arg1, arg2)

    Pattern B — guard style (clarifier.py)::

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        if cb.is_open():
            raise RuntimeError("unavailable")
        try:
            result = do_work()
            cb.record_success()
        except Exception as exc:
            cb.record_failure(exc)

    Pattern C — provider-keyed style (llm_client.py)::

        cb = CircuitBreaker()
        if await cb.allow_request("openai"):
            try:
                result = await call_openai()
                cb.record_success("openai")
            except Exception as exc:
                cb.record_failure("openai", exc)

    Thread safety
    -------------
    Two locks are used:

    * ``_inst_lock`` — serialises reads/writes to instance-level state
      (used by Patterns A and B).
    * ``_prov_lock`` — serialises reads/writes to the provider-keyed dict
      (used by Pattern C).

    Parameters
    ----------
    failure_threshold : int
        Number of consecutive failures before the breaker opens (default: 5).
        Also accepted as the legacy alias ``threshold``.
    recovery_timeout : float
        Seconds to wait in OPEN state before attempting recovery (default: 60.0).
        Also accepted as the legacy alias ``timeout``.
    recovery_threshold : int
        Number of consecutive successes in HALF-OPEN before closing (default: 1).
    name : str
        Human-readable name used in log messages and Prometheus labels.
    state_metric_gauge : Any
        Optional pre-existing Prometheus ``Gauge`` to emit state values to
        (e.g. ``runner_metrics.LLM_CIRCUIT_STATE``).  When provided it is
        used *in addition to* the module-level ``_CB_STATE`` metric.
    threshold : int | None
        Backwards-compat alias for ``failure_threshold``.
    timeout : float | None
        Backwards-compat alias for ``recovery_timeout``.

    Examples
    --------
    ::

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0, name="db")
        assert not cb.is_open()
        cb.record_failure(Exception("timeout"))
        assert cb.failure_count == 1
        cb.record_success()
        assert cb.failure_count == 0
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        recovery_threshold: int = 1,
        name: str = "default",
        state_metric_gauge: Any = None,
        # Backwards-compat aliases
        threshold: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> None:
        # Apply backwards-compat aliases
        if threshold is not None:
            failure_threshold = threshold
        if timeout is not None:
            recovery_timeout = float(timeout)

        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")
        if recovery_threshold <= 0:
            raise ValueError("recovery_threshold must be positive")

        self.failure_threshold: int = failure_threshold
        self.recovery_timeout: float = recovery_timeout
        self.recovery_threshold: int = recovery_threshold
        self.name: str = name
        self._state_metric_gauge: Any = state_metric_gauge

        # ── Instance-level lock (Patterns A & B) ────────────────────────────
        self._inst_lock: threading.Lock = threading.Lock()

        # ── Instance-level state (Patterns A & B) ───────────────────────────
        self.state: str = "CLOSED"
        self.failures: int = 0          # Pattern A failure counter
        self.last_failure_time: float = 0.0
        self._success_count: int = 0
        self._tripped: bool = False
        self._trip_time: float = 0.0
        self._error_count: int = 0      # Pattern B failure counter

        # ── Provider-keyed lock (Pattern C) ─────────────────────────────────
        self._prov_lock: threading.Lock = threading.Lock()

        # ── Provider-keyed state (Pattern C) ────────────────────────────────
        self._prov_state: Dict[str, str] = {}
        self._prov_failures: Dict[str, int] = {}
        self._prov_last_failure: Dict[str, float] = {}
        self._prov_success: Dict[str, int] = {}

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def failure_count(self) -> int:
        """Current Pattern-B failure count (int).

        Returns the internal ``_error_count`` so that test code such as::

            cb.record_failure(Exception("x"))
            assert cb.failure_count == 1

        works without any dict ``.get()`` call.
        """
        with self._inst_lock:
            return self._error_count

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _emit_state(self, provider: str, state: str) -> None:
        """Update Prometheus state gauge(s) for the given provider."""
        value = _STATE_VALUE.get(state, 0.0)
        try:
            _CB_STATE.labels(breaker=self.name, provider=provider).set(value)
            if self._state_metric_gauge is not None:
                self._state_metric_gauge.labels(
                    breaker=self.name, provider=provider
                ).set(value)
        except Exception:  # pragma: no cover
            pass

    def _emit_failure(self, provider: str) -> None:
        try:
            _CB_FAILURES.labels(breaker=self.name, provider=provider).inc()
        except Exception:  # pragma: no cover
            pass

    def _emit_transition(self, provider: str) -> None:
        try:
            _CB_TRANSITIONS.labels(breaker=self.name, provider=provider).inc()
        except Exception:  # pragma: no cover
            pass

    # ── Pattern A: wrap-style ────────────────────────────────────────────────

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Call *func* through the circuit breaker (Pattern A — wrap style).

        Raises the original exception on failure; raises :class:`RuntimeError`
        if the circuit is currently ``OPEN``.

        Parameters
        ----------
        func : Callable
            Sync or async callable to invoke.
        *args, **kwargs
            Forwarded verbatim to *func*.

        Returns
        -------
        Any
            Return value of *func*.

        Raises
        ------
        RuntimeError
            When the circuit is OPEN and the recovery timeout has not elapsed.
        Exception
            Any exception raised by *func* (re-raised after recording failure).
        """
        with self._inst_lock:
            if self.state == "OPEN":
                now = time.monotonic()
                if now - self.last_failure_time > self.recovery_timeout:
                    self.state = "HALF-OPEN"
                    self._emit_transition("_instance_")
                    self._emit_state("_instance_", "HALF-OPEN")
                    logger.info(
                        "Circuit '%s' is HALF-OPEN. Attempting recovery.", self.name
                    )
                else:
                    logger.warning(
                        "Circuit '%s' is OPEN. Call blocked.", self.name
                    )
                    raise RuntimeError(
                        f"Circuit '{self.name}' is OPEN. Execution blocked."
                    )

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
        except Exception:
            with self._inst_lock:
                self.failures += 1
                self.last_failure_time = time.monotonic()
                if self.failures >= self.failure_threshold:
                    if self.state != "OPEN":
                        self._emit_transition("_instance_")
                        logger.error(
                            "Circuit '%s' transitioning to OPEN after %d failures.",
                            self.name,
                            self.failures,
                        )
                    self.state = "OPEN"
                    self._emit_state("_instance_", "OPEN")
                self._emit_failure("_instance_")
            raise

        await self.reset()
        return result

    async def reset(self) -> None:
        """Reset the instance-level state to CLOSED (Pattern A).

        Returns
        -------
        None
        """
        with self._inst_lock:
            self.state = "CLOSED"
            self.failures = 0
            self.last_failure_time = 0.0
            self._success_count = 0
            self._tripped = False
            self._trip_time = 0.0
            self._error_count = 0
            self._emit_state("_instance_", "CLOSED")

    # ── Pattern B: guard-style ────────────────────────────────────────────────

    def is_open(self) -> bool:
        """Return ``True`` when the circuit is tripped and timeout has not elapsed.

        Automatically transitions to HALF-OPEN once the timeout elapses so the
        next :meth:`record_success` call can close the circuit.

        Returns
        -------
        bool
            ``True``  — calls should be rejected (circuit OPEN).
            ``False`` — calls may proceed (circuit CLOSED or HALF-OPEN).
        """
        with self._inst_lock:
            if self._tripped:
                if time.monotonic() - self._trip_time > self.recovery_timeout:
                    logger.info(
                        "Circuit breaker '%s' timeout reached; entering HALF-OPEN.",
                        self.name,
                    )
                    self._tripped = False
                    self._error_count = 0
                    self.state = "HALF-OPEN"
                    self._emit_transition("_instance_")
                    self._emit_state("_instance_", "HALF-OPEN")
                    return False
                logger.warning(
                    "Circuit breaker '%s' is OPEN. Preventing calls.", self.name
                )
                return True
            return False

    def record_failure(
        self, error_or_provider: Any = None, exc: Any = None
    ) -> None:
        """Record a failure for the instance (Pattern B) or a provider (Pattern C).

        Accepts both signatures transparently:

        * ``cb.record_failure(some_exception)``     — Pattern B (instance state)
        * ``cb.record_failure("openai", exc)``      — Pattern C (provider state)

        Parameters
        ----------
        error_or_provider : Exception | str | None
            When a ``str``, treated as a provider name (Pattern C).
            Otherwise treated as the exception that caused the failure (Pattern B).
        exc : Exception | None
            The causal exception when ``error_or_provider`` is a provider name.
        """
        if isinstance(error_or_provider, str):
            self._record_provider_failure(error_or_provider)
        else:
            self._record_instance_failure(error_or_provider)

    def _record_instance_failure(self, error: Any) -> None:
        """Pattern B — record a failure against the instance-level state."""
        with self._inst_lock:
            self._error_count += 1
            self._emit_failure("_instance_")
            if self._error_count >= self.failure_threshold:
                self._tripped = True
                self._trip_time = time.monotonic()
                self.state = "OPEN"
                self._emit_transition("_instance_")
                self._emit_state("_instance_", "OPEN")
                logger.error(
                    "Circuit breaker '%s' tripped to OPEN after %d failures. "
                    "Last error: %s",
                    self.name,
                    self._error_count,
                    error,
                )
            else:
                logger.warning(
                    "Circuit breaker '%s' error %d/%d: %s",
                    self.name,
                    self._error_count,
                    self.failure_threshold,
                    error,
                )

    def _record_provider_failure(self, provider: str) -> None:
        """Pattern C — record a failure against the provider-keyed state."""
        with self._prov_lock:
            self._prov_failures[provider] = self._prov_failures.get(provider, 0) + 1
            self._prov_last_failure[provider] = time.monotonic()
            self._prov_success[provider] = 0
            self._emit_failure(provider)
            if self._prov_failures[provider] >= self.failure_threshold:
                prev = self._prov_state.get(provider, "CLOSED")
                self._prov_state[provider] = "OPEN"
                if prev != "OPEN":
                    self._emit_transition(provider)
                    self._emit_state(provider, "OPEN")
                    logger.warning(
                        "CircuitBreaker '%s': provider '%s' tripped to OPEN "
                        "(%d/%d failures).",
                        self.name,
                        provider,
                        self._prov_failures[provider],
                        self.failure_threshold,
                    )

    def record_success(self, provider: Optional[str] = None) -> None:
        """Record a successful call for the instance (Pattern B) or provider (Pattern C).

        Parameters
        ----------
        provider : str | None
            When given, updates the provider-keyed state (Pattern C).
            When ``None``, updates the instance-level state (Pattern B).
        """
        if provider is not None:
            self._record_provider_success(provider)
        else:
            self._record_instance_success()

    def _record_instance_success(self) -> None:
        """Pattern B — reset the instance-level state."""
        with self._inst_lock:
            if self._tripped:
                logger.info(
                    "Circuit breaker '%s' closing after successful half-open call.",
                    self.name,
                )
            self._tripped = False
            self._error_count = 0
            self._trip_time = 0.0
            self.state = "CLOSED"
            self.failures = 0
            self.last_failure_time = 0.0
            self._emit_state("_instance_", "CLOSED")

    def _record_provider_success(self, provider: str) -> None:
        """Pattern C — update provider-keyed state on success."""
        with self._prov_lock:
            current = self._prov_state.get(provider, "CLOSED")
            if current == "HALF-OPEN":
                self._prov_success[provider] = (
                    self._prov_success.get(provider, 0) + 1
                )
                if self._prov_success[provider] >= self.recovery_threshold:
                    logger.info(
                        "CircuitBreaker '%s': provider '%s' recovered to CLOSED "
                        "(%d/%d successes).",
                        self.name,
                        provider,
                        self._prov_success[provider],
                        self.recovery_threshold,
                    )
                    self._prov_failures[provider] = 0
                    self._prov_success[provider] = 0
                    self._prov_state[provider] = "CLOSED"
                    self._emit_transition(provider)
                    self._emit_state(provider, "CLOSED")
            else:
                self._prov_failures[provider] = 0
                self._prov_success[provider] = 0
                self._prov_state[provider] = "CLOSED"
                self._emit_state(provider, "CLOSED")

    # ── Pattern C: provider-keyed allow_request ───────────────────────────────

    async def allow_request(self, provider: str) -> bool:
        """Return ``True`` when a request for *provider* should be allowed (Pattern C).

        Automatically transitions OPEN → HALF-OPEN once the recovery timeout
        elapses.

        Parameters
        ----------
        provider : str
            Provider name key (e.g. ``"openai"``, ``"anthropic"``).

        Returns
        -------
        bool
            ``True`` if the call should proceed; ``False`` if it should be
            blocked because the circuit is OPEN.
        """
        with self._prov_lock:
            state = self._prov_state.get(provider, "CLOSED")
            if state in ("CLOSED", "HALF-OPEN"):
                return True
            # OPEN — check whether timeout has elapsed
            elapsed = time.monotonic() - self._prov_last_failure.get(provider, 0.0)
            if elapsed > self.recovery_timeout:
                self._prov_state[provider] = "HALF-OPEN"
                self._prov_success[provider] = 0
                self._emit_transition(provider)
                self._emit_state(provider, "HALF-OPEN")
                logger.info(
                    "CircuitBreaker '%s': provider '%s' entering HALF-OPEN "
                    "(need %d successes to recover).",
                    self.name,
                    provider,
                    self.recovery_threshold,
                )
                return True
            return False

    def get_failure_count(self, provider: str) -> int:
        """Return the current failure count for *provider* (Pattern C).

        Parameters
        ----------
        provider : str
            Provider name key.

        Returns
        -------
        int
            Number of consecutive failures recorded for *provider*; ``0`` if
            the provider has never failed or has been reset.

        Examples
        --------
        ::

            cb = CircuitBreaker()
            cb.record_failure("openai")
            assert cb.get_failure_count("openai") == 1
        """
        with self._prov_lock:
            return self._prov_failures.get(provider, 0)

    def get_state(self, provider: str) -> str:
        """Return the current circuit state for *provider* (Pattern C).

        Parameters
        ----------
        provider : str
            Provider name key.

        Returns
        -------
        str
            One of ``"CLOSED"``, ``"OPEN"``, or ``"HALF-OPEN"``.
        """
        with self._prov_lock:
            return self._prov_state.get(provider, "CLOSED")

    def reset_provider(self, provider: str) -> None:
        """Manually reset the circuit breaker for *provider* to CLOSED.

        Parameters
        ----------
        provider : str
            Provider name key to reset.
        """
        with self._prov_lock:
            self._prov_failures[provider] = 0
            self._prov_success[provider] = 0
            self._prov_state[provider] = "CLOSED"
            self._emit_state(provider, "CLOSED")
            logger.info(
                "CircuitBreaker '%s': provider '%s' manually reset to CLOSED.",
                self.name,
                provider,
            )

    def __repr__(self) -> str:
        with self._inst_lock:
            inst_state = self.state
            err = self._error_count
        return (
            f"CircuitBreaker("
            f"name={self.name!r}, "
            f"state={inst_state!r}, "
            f"failure_threshold={self.failure_threshold}, "
            f"recovery_timeout={self.recovery_timeout}, "
            f"_error_count={err}"
            f")"
        )


# ---------------------------------------------------------------------------
# Module-level registry of named breakers
# ---------------------------------------------------------------------------


def get_circuit_breaker(
    name: str = "default",
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    recovery_threshold: int = 1,
    state_metric_gauge: Any = None,
    **kwargs: Any,
) -> CircuitBreaker:
    """Return (or create) a named :class:`CircuitBreaker` singleton.

    The registry is protected by a ``threading.Lock`` so that two threads
    racing to create the same breaker will not both succeed.

    Parameters
    ----------
    name : str
        Registry key and breaker name (default: ``"default"``).
    failure_threshold : int
        Forwarded to :class:`CircuitBreaker.__init__` on first creation.
    recovery_timeout : float
        Forwarded to :class:`CircuitBreaker.__init__` on first creation.
    recovery_threshold : int
        Forwarded to :class:`CircuitBreaker.__init__` on first creation.
    state_metric_gauge : Any
        Optional Prometheus Gauge injected into the new breaker.
    **kwargs
        Any additional keyword arguments forwarded to :class:`CircuitBreaker`.

    Returns
    -------
    CircuitBreaker
        The existing or newly created breaker for *name*.

    Examples
    --------
    ::

        cb = get_circuit_breaker("db", failure_threshold=3, recovery_timeout=30.0)
        result = await cb.call(db_query, sql)
    """
    with _registry_lock:
        if name not in _CIRCUIT_BREAKERS:
            _CIRCUIT_BREAKERS[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                recovery_threshold=recovery_threshold,
                state_metric_gauge=state_metric_gauge,
                **kwargs,
            )
        return _CIRCUIT_BREAKERS[name]


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "CircuitBreaker",
    "get_circuit_breaker",
]
