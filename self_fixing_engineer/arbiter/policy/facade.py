# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# POL-FACADE

"""
Unified Policy Facade

Central routing facade that delegates ``should_auto_learn`` policy checks to
domain-specific :class:`PolicyEngine` instances, with a fallback chain through
the arbiter engine and a final fail-closed default.

Public API
----------
- :class:`UnifiedPolicyFacade`
    - :meth:`register_engine` – register a domain-specific PolicyEngine
    - :meth:`should_auto_learn` – route a policy check with full OTel + metrics
    - :meth:`health_check` – async per-domain engine availability map
- :func:`get_unified_policy_facade` – thread-safe global singleton accessor

Metrics
-------
- ``facade_policy_checks_total``          Counter  [domain, routed_to, allowed]
- ``facade_policy_check_latency_seconds`` Histogram [domain]
- ``facade_engine_registrations_total``   Counter  [domain]
- ``facade_routing_errors_total``         Counter  [error_type]
- ``facade_no_engine_total``              Counter  [domain]

Architecture
------------
A single global :class:`UnifiedPolicyFacade` is lazily created and protected by
a :class:`threading.Lock`.  Engine registration is also lock-protected so that
concurrent service-start paths cannot corrupt the engine registry.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional OpenTelemetry – graceful no-op fallback                [POL-FACADE]
# ---------------------------------------------------------------------------
try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer_safe
except Exception:  # pragma: no cover

    class _NoOpSpan:  # minimal no-op span
        def __enter__(self) -> "_NoOpSpan":
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def set_attribute(self, *_: Any, **__: Any) -> None:  # noqa: D401
            return None

        def record_exception(self, *_: Any, **__: Any) -> None:
            return None

    class _NoOpTracer:
        def start_as_current_span(self, *_: Any, **__: Any) -> "_NoOpSpan":
            return _NoOpSpan()

    def get_tracer_safe(name: str, version: Optional[str] = None) -> "_NoOpTracer":  # type: ignore[misc]
        """Fallback tracer when otel_config is unavailable."""
        return _NoOpTracer()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Optional Prometheus metrics                                      [POL-FACADE]
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter, Histogram
    from omnicore_engine.metrics_utils import get_or_create_metric

    _FACADE_POLICY_CHECKS_TOTAL = get_or_create_metric(
        Counter,
        "facade_policy_checks_total",
        "Total policy checks routed by UnifiedPolicyFacade",
        labelnames=("domain", "routed_to", "allowed"),
    )
    _FACADE_POLICY_CHECK_LATENCY = get_or_create_metric(
        Histogram,
        "facade_policy_check_latency_seconds",
        "Latency of policy checks routed by UnifiedPolicyFacade",
        labelnames=("domain",),
        buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5),
    )
    _FACADE_ENGINE_REGISTRATIONS_TOTAL = get_or_create_metric(
        Counter,
        "facade_engine_registrations_total",
        "Total engine registrations in UnifiedPolicyFacade",
        labelnames=("domain",),
    )
    _FACADE_ROUTING_ERRORS_TOTAL = get_or_create_metric(
        Counter,
        "facade_routing_errors_total",
        "Total routing errors in UnifiedPolicyFacade",
        labelnames=("error_type",),
    )
    _FACADE_NO_ENGINE_TOTAL = get_or_create_metric(
        Counter,
        "facade_no_engine_total",
        "Total policy checks with no engine available in UnifiedPolicyFacade",
        labelnames=("domain",),
    )
    _METRICS_AVAILABLE = True
except Exception:  # pragma: no cover
    _METRICS_AVAILABLE = False

    class _NullMetric:
        """Stub metric that silently ignores all operations."""

        def labels(self, **_kw: Any) -> "_NullMetric":
            return self

        def inc(self, _amount: float = 1) -> None:
            return None

        def observe(self, _amount: float) -> None:
            return None

    _FACADE_POLICY_CHECKS_TOTAL = _NullMetric()  # type: ignore[assignment]
    _FACADE_POLICY_CHECK_LATENCY = _NullMetric()  # type: ignore[assignment]
    _FACADE_ENGINE_REGISTRATIONS_TOTAL = _NullMetric()  # type: ignore[assignment]
    _FACADE_ROUTING_ERRORS_TOTAL = _NullMetric()  # type: ignore[assignment]
    _FACADE_NO_ENGINE_TOTAL = _NullMetric()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Optional audit logging                                           [POL-FACADE]
# ---------------------------------------------------------------------------
try:
    from self_fixing_engineer.guardrails.audit_log import audit_log_event_async as _audit_log_event_async
    _AUDIT_AVAILABLE = True
except Exception:  # pragma: no cover
    _AUDIT_AVAILABLE = False

    async def _audit_log_event_async(*_: Any, **__: Any) -> None:  # type: ignore[misc]
        """No-op audit stub when audit_log module is unavailable."""
        return None


tracer = get_tracer_safe(__name__)
logger = logging.getLogger(__name__)

__all__ = ["UnifiedPolicyFacade", "get_unified_policy_facade"]


# ---------------------------------------------------------------------------
# Facade class                                                     [POL-FACADE]
# ---------------------------------------------------------------------------

class UnifiedPolicyFacade:
    """Central policy routing facade that delegates to domain-specific PolicyEngines.

    Public API
    ----------
    register_engine(domain, engine)
        Register a :class:`PolicyEngine` for ``domain``.
    should_auto_learn(domain, key, user_id, value) → Tuple[bool, str]
        Route a policy check through the domain engine → arbiter engine → fail-closed chain.
    health_check() → Dict[str, Any]
        Return per-domain engine availability information.

    Metrics
    -------
    All operations increment dedicated Prometheus counters/histograms via
    ``get_or_create_metric`` to prevent registration conflicts on hot-reload.

    Notes
    -----
    - ``_lock`` (``threading.Lock``) guards all engine-registry mutations.
    - Fail-closed: when no engine is found, returns ``(False, "No policy engine configured (fail-closed)")``.
    """

    # POL-FACADE: known domain → attribute name mapping
    _DOMAIN_ATTR_MAP: Dict[str, str] = {
        "arbiter": "_arbiter_engine",
        "test_generation": "_test_gen_engine",
        "simulation": "_simulation_engine",
        "mesh": "_mesh_engine",
    }

    def __init__(self) -> None:
        self._arbiter_engine: Optional[Any] = None
        self._test_gen_engine: Optional[Any] = None
        self._simulation_engine: Optional[Any] = None
        self._mesh_engine: Optional[Any] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Engine registration
    # ------------------------------------------------------------------

    def register_engine(self, domain: str, engine: Any) -> None:
        """Register a domain-specific policy engine.

        Parameters
        ----------
        domain:
            One of ``"arbiter"``, ``"test_generation"``, ``"simulation"``, ``"mesh"``.
        engine:
            A :class:`PolicyEngine` instance (or compatible duck-typed object).
        """
        with tracer.start_as_current_span(
            "facade_register_engine",
            attributes={"domain": str(domain)},
        ) as span:
            # Input validation
            if not isinstance(domain, str) or not domain:
                reason = f"register_engine: domain must be a non-empty str, got {domain!r}"
                span.record_exception(ValueError(reason))
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type="invalid_domain").inc()
                logger.error(f'{{"event": "register_engine_invalid_domain", "domain": {domain!r}}}')
                return
            if engine is None:
                reason = f"register_engine: engine must not be None for domain {domain!r}"
                span.record_exception(ValueError(reason))
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type="null_engine").inc()
                logger.error(f'{{"event": "register_engine_null_engine", "domain": {domain!r}}}')
                return

            attr = self._DOMAIN_ATTR_MAP.get(domain)
            if attr is None:
                span.record_exception(ValueError(f"Unknown domain: {domain!r}"))
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type="unknown_domain").inc()
                logger.warning(
                    f'{{"event": "register_engine_unknown_domain", "domain": {domain!r}}}'
                )
                return

            with self._lock:
                setattr(self, attr, engine)

            span.set_attribute("engine_type", type(engine).__name__)
            _FACADE_ENGINE_REGISTRATIONS_TOTAL.labels(domain=domain).inc()
            logger.info(
                f'{{"event": "engine_registered", "domain": {domain!r}, "engine": "{type(engine).__name__}"}}'
            )

    # ------------------------------------------------------------------
    # Core policy check
    # ------------------------------------------------------------------

    async def should_auto_learn(
        self,
        domain: str,
        key: str,
        user_id: Optional[str],
        value: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """Route a policy check to the appropriate domain engine.

        Falls back to the arbiter engine when no domain-specific engine is
        registered, then to a fail-closed default.

        Parameters
        ----------
        domain:
            Policy domain identifier (non-empty string).
        key:
            Policy key to evaluate (non-empty string).
        user_id:
            Caller identity; ``None`` is treated as anonymous.
        value:
            Optional value payload for size/content checks.

        Returns
        -------
        Tuple[bool, str]
            ``(allowed, reason)``
        """
        with tracer.start_as_current_span(
            "facade_should_auto_learn",
            attributes={"domain": str(domain), "key": str(key)},
        ) as span:
            _t0 = time.monotonic()

            # --- Input validation ---
            if not isinstance(domain, str) or not domain:
                reason = f"Invalid domain: {domain!r} — must be a non-empty string."
                span.record_exception(ValueError(reason))
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type="invalid_domain").inc()
                logger.error(
                    f'{{"event": "facade_invalid_domain", "domain": {domain!r}}}'
                )
                return False, reason

            if not isinstance(key, str) or not key:
                reason = f"Invalid key: {key!r} — must be a non-empty string."
                span.record_exception(ValueError(reason))
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type="invalid_key").inc()
                logger.error(
                    f'{{"event": "facade_invalid_key", "domain": {domain!r}, "key": {key!r}}}'
                )
                return False, reason

            # --- Routing ---
            engine = self._get_engine_for_domain(domain)
            routed_to = "none"

            try:
                if engine is not None and hasattr(engine, "should_auto_learn"):
                    routed_to = type(engine).__name__
                    allowed, reason = await engine.should_auto_learn(
                        domain, key, user_id, value
                    )
                elif self._arbiter_engine is not None:
                    routed_to = "arbiter_engine"
                    allowed, reason = await self._arbiter_engine.should_auto_learn(
                        domain, key, user_id, value
                    )
                else:
                    routed_to = "fail_closed"
                    _FACADE_NO_ENGINE_TOTAL.labels(domain=domain).inc()
                    logger.warning(
                        f'{{"event": "facade_no_engine", "domain": {domain!r}}}'
                    )
                    allowed, reason = False, "No policy engine configured (fail-closed)"

            except Exception as exc:
                span.record_exception(exc)
                _FACADE_ROUTING_ERRORS_TOTAL.labels(error_type=type(exc).__name__).inc()
                logger.error(
                    f'{{"event": "facade_routing_error", "domain": {domain!r}, "routed_to": "{routed_to}", "error": "{exc}"}}',
                    exc_info=True,
                )
                return False, f"Policy routing error (fail-closed): {exc}"

            finally:
                elapsed = time.monotonic() - _t0
                _FACADE_POLICY_CHECK_LATENCY.labels(domain=domain).observe(elapsed)

            span.set_attribute("routed_to", routed_to)
            span.set_attribute("allowed", str(allowed))
            _FACADE_POLICY_CHECKS_TOTAL.labels(
                domain=domain,
                routed_to=routed_to,
                allowed=str(allowed).lower(),
            ).inc()
            logger.debug(
                f'{{"event": "facade_policy_check", "domain": {domain!r}, "key": {key!r}, '
                f'"routed_to": "{routed_to}", "allowed": {allowed}, "reason": {reason!r}}}'
            )
            return allowed, reason

    # ------------------------------------------------------------------
    # Pure domain-routing helper (no side effects)
    # ------------------------------------------------------------------

    def _get_engine_for_domain(self, domain: str) -> Optional[Any]:
        """Return the registered engine for *domain* without side effects."""
        if domain.startswith("test_gen") or domain == "TestGeneration":
            return self._test_gen_engine
        if domain.startswith("simulation") or domain == "Simulation":
            return self._simulation_engine
        if domain.startswith("mesh") or domain == "Mesh":
            return self._mesh_engine
        # Explicit arbiter match – also serves as default
        if domain == "arbiter":
            return self._arbiter_engine
        return None  # caller will fall back to arbiter engine

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Return per-domain engine availability as a status dict.

        Returns
        -------
        Dict[str, Any]
            ``{"domains": {"arbiter": bool, ...}, "metrics_available": bool}``
        """
        with self._lock:
            domains = {
                "arbiter": self._arbiter_engine is not None,
                "test_generation": self._test_gen_engine is not None,
                "simulation": self._simulation_engine is not None,
                "mesh": self._mesh_engine is not None,
            }
        return {
            "domains": domains,
            "metrics_available": _METRICS_AVAILABLE,
        }


# ---------------------------------------------------------------------------
# Thread-safe global singleton                                     [POL-FACADE]
# ---------------------------------------------------------------------------

_unified_facade: Optional[UnifiedPolicyFacade] = None
_facade_lock = threading.Lock()


def get_unified_policy_facade() -> UnifiedPolicyFacade:
    """Return the process-wide :class:`UnifiedPolicyFacade` singleton.

    Thread-safe: uses a module-level :class:`threading.Lock` so that
    concurrent first-callers cannot create duplicate instances.
    """
    global _unified_facade
    if _unified_facade is None:
        with _facade_lock:
            if _unified_facade is None:
                _unified_facade = UnifiedPolicyFacade()
    return _unified_facade
