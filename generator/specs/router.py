# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/specs/router.py
"""
Compliance Spec Router — Multi-Regime Security Directive Aggregation Engine

This module is the single routing layer between the code-generation pipeline
and the family of compliance specification modules (``hipaa``, ``gdpr``, …).
Callers never import individual spec modules directly; they interact exclusively
through the functions in this module, which perform keyword and clarifier-answer
matching to determine which specs are active for a given request.

Purpose
-------
- **Keyword matching** — inspect raw spec text for trigger keywords defined
  in each spec module's ``TRIGGER_KEYWORDS`` frozenset.
- **Clarifier-answer matching** — check ``compliance_preferences`` dict for
  question IDs listed in each spec's ``CLARIFIER_QUESTION_IDS``.
- **Directive aggregation** — concatenate ``DIRECTIVE_TEXT`` strings from all
  active specs into a single block injected into the LLM system prompt.
- **Rule merging** — merge ``COMPLIANCE_RULES`` dicts from all active specs,
  union-combining list values and preferring non-empty overrides.
- **Output scanning** — delegate to each active spec's
  ``check_generated_code()`` and aggregate violation reports.
- **Gap analysis** — delegate to each active spec's
  ``get_compliance_gaps()`` and aggregate results keyed by
  ``COMPLIANCE_MODE``.

Architecture
------------
::

    ┌──────────────────────────────────────────┐
    │  Caller (engine / omnicore_service)      │
    └──────────────────┬───────────────────────┘
                       │  get_security_directives(spec_text, prefs)
                       ▼
    ┌──────────────────────────────────────────┐
    │  _get_loaded_specs()                     │  ← thread-safe lazy loader
    │   └── _load_specs()                      │     validates all attributes
    └──────────────────┬───────────────────────┘
                       │
                       ▼
    ┌──────────────────────────────────────────┐
    │  _match_specs(spec_text, prefs)          │  ← keyword + clarifier match
    └──────────────────┬───────────────────────┘
                       │  matched spec modules
                       ▼
    ┌──────────────────────────────────────────┐
    │  aggregate DIRECTIVE_TEXT / RULES /      │
    │  check_generated_code / gaps / plugins   │
    └──────────────────────────────────────────┘

Adding a New Compliance Regime
------------------------------
1. Create ``generator/specs/<regime>.py`` following the module contract
   (all 8 attributes/callables — see ``generator/specs/__init__.py``).
2. Append the module name string to :data:`_SPEC_MODULES`.

No other file needs to change.

Observability
-------------
- **OpenTelemetry** — all public functions emit spans with relevant attributes
  (matched spec names, violation counts, output directory, etc.).
- **Prometheus** — ``spec_routing_calls_total`` (Counter, labels: ``action``)
  and ``spec_routing_duration_seconds`` (Histogram) track call rate and latency.
- **Structured logging** — ``DEBUG``/``WARNING`` messages with match context.

Industry Standards Compliance
------------------------------
- **Single Responsibility Principle** — routing logic isolated from spec logic.
- **Open/Closed Principle** — extend by adding spec modules; never modify router.
- **Thread Safety** — double-checked locking protects lazy spec loading.
- **SOC 2 Type II** / **ISO 27001 A.12.4.1**: structured audit-ready logging.
- **PEP 484** / **PEP 526**: full static type-hint coverage.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from types import ModuleType
from typing import Any, Dict, List, Optional

from generator.agents.metrics_utils import get_or_create_metric

# =============================================================================
# OPTIONAL DEPENDENCY — PyYAML
# =============================================================================

try:
    import yaml as _pyyaml  # noqa: F401

    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _pyyaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

# =============================================================================
# OBSERVABILITY — OpenTelemetry (graceful degradation)
# =============================================================================

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    _tracer = trace.get_tracer(__name__)
    _HAS_OPENTELEMETRY = True
except ImportError:  # pragma: no cover
    _HAS_OPENTELEMETRY = False

    class _StatusCode:  # type: ignore[no-redef]
        OK = "OK"
        ERROR = "ERROR"

    class _Status:  # type: ignore[no-redef]
        def __init__(self, status_code: Any, description: Optional[str] = None) -> None:
            self.status_code = status_code
            self.description = description

    class _NoOpSpan:
        def set_attribute(self, *a: Any, **kw: Any) -> None: ...
        def set_status(self, *a: Any, **kw: Any) -> None: ...
        def record_exception(self, *a: Any, **kw: Any) -> None: ...
        def add_event(self, *a: Any, **kw: Any) -> None: ...

    class _NoOpContextManager:
        def __enter__(self) -> "_NoOpSpan": return _NoOpSpan()
        def __exit__(self, *a: Any) -> None: ...

    class _NoOpTracer:
        def start_as_current_span(self, *a: Any, **kw: Any) -> "_NoOpContextManager":
            return _NoOpContextManager()

    _tracer = _NoOpTracer()  # type: ignore[assignment]
    StatusCode = _StatusCode  # type: ignore[assignment,misc]
    Status = _Status  # type: ignore[assignment,misc]

# =============================================================================
# OBSERVABILITY — Prometheus metrics (graceful degradation)
# =============================================================================

try:
    from prometheus_client import Counter as _PCounter, Histogram as _Histogram

    spec_routing_calls_total: Any = get_or_create_metric(
        _PCounter,
        "spec_routing_calls_total",
        "Total spec router public function invocations",
        ["action"],
    )
    spec_routing_duration_seconds: Any = get_or_create_metric(
        _Histogram,
        "spec_routing_duration_seconds",
        "Wall-clock duration of spec router public function calls in seconds",
    )
    _HAS_PROMETHEUS = True

except ImportError:  # pragma: no cover
    _HAS_PROMETHEUS = False

    class _NoOpMetric:  # type: ignore[no-redef]
        """Lightweight no-op stub that silently accepts any Prometheus-style call."""

        def labels(self, *args: Any, **kwargs: Any) -> "_NoOpMetric":
            return self

        def inc(self, *args: Any, **kwargs: Any) -> None:
            pass

        def observe(self, *args: Any, **kwargs: Any) -> None:
            pass

    spec_routing_calls_total: Any = _NoOpMetric()  # type: ignore[no-redef]
    spec_routing_duration_seconds: Any = _NoOpMetric()  # type: ignore[no-redef]

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

#: Ordered list of spec module names.  Add new compliance regimes here —
#: no other file needs to change.
_SPEC_MODULES: List[str] = ["hipaa", "gdpr"]

#: Required attributes and their expected types for spec module validation.
_REQUIRED_ATTRS: Dict[str, type] = {
    "TRIGGER_KEYWORDS": frozenset,
    "CLARIFIER_QUESTION_IDS": frozenset,
    "COMPLIANCE_MODE": str,
    "NIST_CONTROL_IDS": frozenset,
    "COMPLIANCE_RULES": dict,
    "DIRECTIVE_TEXT": str,
}

# =============================================================================
# INTERNAL STATE — thread-safe lazy loading
# =============================================================================

_lock: threading.Lock = threading.Lock()
_loaded_specs: Optional[Dict[str, ModuleType]] = None

# =============================================================================
# INTERNAL HELPERS
# =============================================================================


def _load_specs() -> Dict[str, ModuleType]:
    """Load, import, and validate all registered spec modules.

    Iterates over :data:`_SPEC_MODULES`, dynamically imports each module from
    the ``generator.specs`` package, and validates that each module exposes
    all six attributes in :data:`_REQUIRED_ATTRS` with the correct types.

    Returns:
        A dict mapping spec-module short names (e.g. ``"hipaa"``) to the
        imported :class:`~types.ModuleType` objects.  Modules that fail
        validation are logged and omitted.

    Raises:
        ImportError: Only if a required module cannot be imported at all.
            Modules that fail attribute validation are silently dropped after
            a ``WARNING`` log.
    """
    specs: Dict[str, ModuleType] = {}

    for name in _SPEC_MODULES:
        module_path = f"generator.specs.{name}"
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            logger.warning(
                "Failed to import spec module",
                extra={"module": module_path},
                exc_info=True,
            )
            continue

        # Validate required attributes
        valid = True
        for attr, expected_type in _REQUIRED_ATTRS.items():
            if not hasattr(mod, attr):
                logger.warning(
                    "Spec module missing required attribute",
                    extra={"module": module_path, "missing_attr": attr},
                )
                valid = False
                break
            value = getattr(mod, attr)
            if not isinstance(value, expected_type):
                logger.warning(
                    "Spec module attribute has wrong type",
                    extra={
                        "module": module_path,
                        "attr": attr,
                        "expected": expected_type.__name__,
                        "actual": type(value).__name__,
                    },
                )
                valid = False
                break

        if valid:
            specs[name] = mod
            logger.debug(
                "Spec module loaded and validated",
                extra={"module": module_path},
            )

    return specs


def _get_loaded_specs() -> Dict[str, ModuleType]:
    """Return the lazily loaded spec module dict, initialising on first call.

    Thread-safe via double-checked locking around :data:`_lock`.

    Returns:
        Dict mapping spec names to validated :class:`~types.ModuleType` objects.
    """
    global _loaded_specs

    if _loaded_specs is not None:
        return _loaded_specs

    with _lock:
        if _loaded_specs is None:
            _loaded_specs = _load_specs()

    return _loaded_specs


def _match_specs(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]],
) -> List[ModuleType]:
    """Return spec modules that match the given spec text and/or clarifier answers.

    A spec is considered active when **either**:

    - At least one token in the spec module's ``TRIGGER_KEYWORDS`` frozenset
      appears (case-insensitively) in *spec_text*, **or**
    - The ``compliance_preferences`` dict contains a truthy value for any
      question ID listed in the spec's ``CLARIFIER_QUESTION_IDS`` frozenset.

    Args:
        spec_text: Raw user-supplied spec/prompt text used for keyword matching.
        compliance_preferences: Dict of clarifier question-ID → answer pairs
            (e.g. ``{"phi_data": True, "gdpr_apply": False}``).  May be
            ``None`` or empty.

    Returns:
        Ordered list of matching spec :class:`~types.ModuleType` objects.
        Preserves the order defined in :data:`_SPEC_MODULES`.
    """
    prefs = compliance_preferences or {}
    lower_text = spec_text.lower()
    matched: List[ModuleType] = []

    for name, mod in _get_loaded_specs().items():
        reason: Optional[str] = None

        # Keyword match
        for kw in mod.TRIGGER_KEYWORDS:
            if kw.lower() in lower_text:
                reason = f"keyword={kw!r}"
                break

        # Clarifier match (if no keyword match yet)
        if reason is None:
            for qid in mod.CLARIFIER_QUESTION_IDS:
                if prefs.get(qid):
                    reason = f"clarifier={qid!r}"
                    break

        if reason is not None:
            matched.append(mod)
            logger.debug(
                "Spec matched",
                extra={"spec": name, "reason": reason},
            )

    return matched


# =============================================================================
# PUBLIC API
# =============================================================================


def get_security_directives(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> str:
    """Return concatenated security directives for all specs matching the input.

    Inspects *spec_text* and *compliance_preferences* to determine which
    compliance specs are active, then concatenates their ``DIRECTIVE_TEXT``
    strings into a single block suitable for injection into an LLM system prompt.

    Emits an OpenTelemetry span ``"spec_router.get_security_directives"`` with
    ``matched_specs`` attribute.

    Increments Prometheus counter ``spec_routing_calls_total`` with
    ``action="get_security_directives"``.

    Args:
        spec_text: Raw spec/prompt text supplied by the caller.  Used for
            trigger-keyword matching.
        compliance_preferences: Optional dict of clarifier question-ID → answer
            pairs (e.g. ``{"phi_data": True}``).

    Returns:
        A single string containing the concatenated ``DIRECTIVE_TEXT`` values of
        all matched specs, separated by ``"\\n\\n"``.  Returns an empty string
        when no specs match.

    Examples:
        >>> directives = get_security_directives("Build a HIPAA-compliant EHR")
        >>> "HIPAA" in directives
        True
        >>> get_security_directives("simple todo app")
        ''
    """
    _t0 = time.monotonic()

    with _tracer.start_as_current_span("spec_router.get_security_directives") as span:
        matched = _match_specs(spec_text, compliance_preferences)
        names = [m.COMPLIANCE_MODE for m in matched]
        span.set_attribute("matched_specs", str(names))

        spec_routing_calls_total.labels(action="get_security_directives").inc()
        spec_routing_duration_seconds.observe(time.monotonic() - _t0)

        return "\n\n".join(m.DIRECTIVE_TEXT for m in matched)


def get_compliance_rules(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return merged compliance rules from all specs matching the input.

    Merges the ``COMPLIANCE_RULES`` dicts of all active specs.  List values
    (e.g. ``"banned_functions"``) are union-combined; duplicate entries are
    preserved in insertion order.

    Emits an OpenTelemetry span ``"spec_router.get_compliance_rules"`` with
    ``matched_specs`` attribute.

    Increments Prometheus counter ``spec_routing_calls_total`` with
    ``action="get_compliance_rules"``.

    Args:
        spec_text: Raw spec/prompt text for keyword matching.
        compliance_preferences: Optional clarifier-answer dict.

    Returns:
        A merged ``COMPLIANCE_RULES`` dict.  Returns an empty dict when no
        specs match.

    Examples:
        >>> rules = get_compliance_rules("HIPAA EHR application")
        >>> "banned_functions" in rules
        True
    """
    _t0 = time.monotonic()

    with _tracer.start_as_current_span("spec_router.get_compliance_rules") as span:
        matched = _match_specs(spec_text, compliance_preferences)
        names = [m.COMPLIANCE_MODE for m in matched]
        span.set_attribute("matched_specs", str(names))

        spec_routing_calls_total.labels(action="get_compliance_rules").inc()
        spec_routing_duration_seconds.observe(time.monotonic() - _t0)

        merged: Dict[str, Any] = {}
        for mod in matched:
            for key, value in mod.COMPLIANCE_RULES.items():
                if isinstance(value, list):
                    existing = merged.get(key, [])
                    merged[key] = existing + [v for v in value if v not in existing]
                else:
                    merged[key] = value

        return merged


def check_generated_output(
    output_dir: str,
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan *output_dir* with all matching spec checkers and aggregate violations.

    Delegates to each active spec's :func:`check_generated_code` function and
    returns the combined list of violation dicts.

    Emits an OpenTelemetry span ``"spec_router.check_generated_output"`` with
    attributes ``output_dir``, ``matched_specs``, and ``total_violations``.

    Increments Prometheus counter ``spec_routing_calls_total`` with
    ``action="check_generated_output"``.

    Args:
        output_dir: Path to the directory containing generated source files.
        spec_text: Raw spec/prompt text for keyword matching.
        compliance_preferences: Optional clarifier-answer dict.

    Returns:
        Combined list of :class:`ComplianceViolation`-shaped dicts from all
        active specs.  Returns an empty list when no specs match or no
        violations are detected.

    Examples:
        >>> violations = check_generated_output("/tmp/gen", "HIPAA EHR app")
        >>> isinstance(violations, list)
        True
    """
    _t0 = time.monotonic()

    with _tracer.start_as_current_span("spec_router.check_generated_output") as span:
        span.set_attribute("output_dir", output_dir)

        matched = _match_specs(spec_text, compliance_preferences)
        names = [m.COMPLIANCE_MODE for m in matched]
        span.set_attribute("matched_specs", str(names))

        spec_routing_calls_total.labels(action="check_generated_output").inc()

        all_violations: List[Dict[str, Any]] = []
        for mod in matched:
            try:
                violations = mod.check_generated_code(output_dir)
                all_violations.extend(violations)
            except Exception as exc:
                logger.warning(
                    "check_generated_code failed for spec",
                    extra={"spec": mod.COMPLIANCE_MODE, "error": str(exc)},
                    exc_info=True,
                )

        span.set_attribute("total_violations", len(all_violations))
        spec_routing_duration_seconds.observe(time.monotonic() - _t0)

        return all_violations


def get_compliance_gaps(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
    config_path: str = "",
) -> Dict[str, Dict[str, List[str]]]:
    """Return NIST coverage gaps aggregated from all matching specs.

    Delegates to each active spec's :func:`get_compliance_gaps` function and
    returns the results keyed by each spec's ``COMPLIANCE_MODE``.

    Emits an OpenTelemetry span ``"spec_router.get_compliance_gaps"`` with
    ``matched_specs`` attribute.

    Increments Prometheus counter ``spec_routing_calls_total`` with
    ``action="get_compliance_gaps"``.

    Args:
        spec_text: Raw spec/prompt text for keyword matching.
        compliance_preferences: Optional clarifier-answer dict.
        config_path: Optional explicit path to ``crew_config.yaml``.

    Returns:
        Dict mapping ``COMPLIANCE_MODE`` strings (e.g. ``"hipaa"``) to their
        respective gap dicts (control-ID → list of gap strings).  Returns an
        empty dict when no specs match.

    Examples:
        >>> gaps = get_compliance_gaps("HIPAA EHR application")
        >>> isinstance(gaps, dict)
        True
    """
    _t0 = time.monotonic()

    with _tracer.start_as_current_span("spec_router.get_compliance_gaps") as span:
        matched = _match_specs(spec_text, compliance_preferences)
        names = [m.COMPLIANCE_MODE for m in matched]
        span.set_attribute("matched_specs", str(names))

        spec_routing_calls_total.labels(action="get_compliance_gaps").inc()

        result: Dict[str, Dict[str, List[str]]] = {}
        for mod in matched:
            try:
                gaps = mod.get_compliance_gaps(config_path)
                result[mod.COMPLIANCE_MODE] = gaps
            except Exception as exc:
                logger.warning(
                    "get_compliance_gaps failed for spec",
                    extra={"spec": mod.COMPLIANCE_MODE, "error": str(exc)},
                    exc_info=True,
                )

        spec_routing_duration_seconds.observe(time.monotonic() - _t0)
        return result


def as_compliance_plugins(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Return ``CompliancePlugin`` instances from all specs matching the input.

    Delegates to each active spec's :func:`make_compliance_plugin` callable
    and returns the resulting plugin objects as an ordered list.

    Emits an OpenTelemetry span ``"spec_router.as_compliance_plugins"`` with
    ``matched_specs`` attribute.

    Increments Prometheus counter ``spec_routing_calls_total`` with
    ``action="as_compliance_plugins"``.

    Args:
        spec_text: Raw spec/prompt text for keyword matching.
        compliance_preferences: Optional clarifier-answer dict.

    Returns:
        List of :class:`~generator.agents.docgen_agent.docgen_agent.CompliancePlugin`
        instances from all active specs.  Returns an empty list when no specs
        match.

    Examples:
        >>> plugins = as_compliance_plugins("HIPAA EHR application")
        >>> all(hasattr(p, "name") for p in plugins)
        True
    """
    _t0 = time.monotonic()

    with _tracer.start_as_current_span("spec_router.as_compliance_plugins") as span:
        matched = _match_specs(spec_text, compliance_preferences)
        names = [m.COMPLIANCE_MODE for m in matched]
        span.set_attribute("matched_specs", str(names))

        spec_routing_calls_total.labels(action="as_compliance_plugins").inc()

        plugins: List[Any] = []
        for mod in matched:
            try:
                plugin = mod.make_compliance_plugin()
                plugins.append(plugin)
            except Exception as exc:
                logger.warning(
                    "make_compliance_plugin failed for spec",
                    extra={"spec": mod.COMPLIANCE_MODE, "error": str(exc)},
                    exc_info=True,
                )

        spec_routing_duration_seconds.observe(time.monotonic() - _t0)
        return plugins


def list_registered_specs() -> List[str]:
    """Return a sorted list of all registered compliance spec names.

    Reflects the names of modules that were successfully imported and
    validated by :func:`_load_specs`.  Failed/missing modules are absent.

    Returns:
        Sorted list of spec-module short names (e.g. ``["gdpr", "hipaa"]``).

    Examples:
        >>> specs = list_registered_specs()
        >>> isinstance(specs, list)
        True
        >>> all(isinstance(s, str) for s in specs)
        True
    """
    return sorted(_get_loaded_specs().keys())
