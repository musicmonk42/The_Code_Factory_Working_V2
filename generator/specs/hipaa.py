# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/specs/hipaa.py
"""
HIPAA Compliance Specification Module — Healthcare Data Protection Engine

This module provides the HIPAA/healthcare compliance specification for the
Code Factory generator pipeline.  It exposes trigger-keyword detection,
clarifier question mappings, NIST control IDs, banned-function rules, a
generated-code scanner, and a ``CompliancePlugin`` for documentation audits.

Purpose
-------
Centralises **all** HIPAA-related compliance logic so that:

1. The spec router (``generator.specs.router``) can activate HIPAA rules
   automatically when keywords such as ``hipaa`` or ``phi`` appear in the
   user spec text.
2. The post-generation auditor can call :func:`check_generated_code` to flag
   PHI-handling patterns that require explicit compliance controls.
3. The gap-analysis tooling can call :func:`get_compliance_gaps` to surface
   NIST SP 800-53 control coverage deficiencies.

Architecture
------------
::

    ┌─────────────────────────────────┐
    │  spec_text / compliance_prefs   │  ← caller input
    └──────────────┬──────────────────┘
                   │
                   ▼
    ┌─────────────────────────────────┐
    │  TRIGGER_KEYWORDS match         │  ← frozenset lookup O(1)
    │  CLARIFIER_QUESTION_IDS match   │  ← clarifier answer lookup
    └──────────────┬──────────────────┘
                   │
          ┌────────┴─────────┐
          ▼                  ▼
    ┌──────────┐     ┌────────────────────┐
    │ DIRECTIVE│     │ CompliancePlugin   │
    │   _TEXT  │     │ check(docs)        │
    └──────────┘     └────────────────────┘
          │
          ▼
    ┌─────────────────────────────────┐
    │  check_generated_code(dir)      │  ← regex scan over *.py files
    │  get_compliance_gaps(cfg_path)  │  ← NIST coverage gap analysis
    └─────────────────────────────────┘

Observability
-------------
- **OpenTelemetry** — :func:`check_generated_code` emits a span
  ``"hipaa_spec.check_generated_code"`` with ``output_dir``,
  ``files_scanned``, and ``violations_found`` attributes.
- **Prometheus** — :func:`check_generated_code` increments
  ``spec_compliance_checks_total{spec="hipaa", status="passed"|"violations_found"}``.
- **Structured logging** — all functions emit ``DEBUG``/``WARNING`` messages
  with file-path and violation-count context.

Industry Standards Compliance
------------------------------
- **HIPAA Security Rule** (45 CFR § 164.312): encryption, audit controls,
  access controls, integrity controls.
- **NIST SP 800-53 Rev 5**: AC-3, AC-6, AU-2, AU-6, IA-5, SC-28.
- **HL7 FHIR R4**: data-model standards for EHR interoperability.
- **SOC 2 Type II** / **ISO 27001 A.12.4.1**: audit-ready structured logging.
- **PEP 484** / **PEP 526**: full static type-hint coverage.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

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
    from prometheus_client import Counter as _PCounter

    _spec_compliance_checks_total: Any = get_or_create_metric(
        _PCounter,
        "spec_compliance_checks_total",
        "Total compliance check invocations per spec and outcome",
        ["spec", "status"],
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

    _spec_compliance_checks_total: Any = _NoOpMetric()  # type: ignore[no-redef]

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

#: Keywords that trigger HIPAA compliance mode in the spec router.
TRIGGER_KEYWORDS: frozenset[str] = frozenset(
    {
        "hipaa",
        "phi",
        "protected health information",
        "ehr",
        "emr",
        "telehealth",
        "healthcare",
        "rs256",
    }
)

#: Clarifier question IDs whose affirmative answers activate this spec.
#: Maps to ``COMPLIANCE_QUESTIONS`` entries in
#: ``generator.clarifier.clarifier_user_prompt``.
CLARIFIER_QUESTION_IDS: frozenset[str] = frozenset({"phi_data"})

#: Compliance mode string accepted by ``validate_compliance()`` in
#: ``generator.audit_log.validate_config``.
COMPLIANCE_MODE: str = "hipaa"

#: NIST SP 800-53 Rev 5 control identifiers relevant to this spec.
#: AC-3 (Access Enforcement), AC-6 (Least Privilege), AU-2 (Event Logging),
#: AU-6 (Audit Review), IA-5 (Authenticator Management), SC-28 (Protection
#: of Information at Rest).
NIST_CONTROL_IDS: frozenset[str] = frozenset(
    {"AC-3", "AC-6", "AU-2", "AU-6", "IA-5", "SC-28"}
)

#: Banned functions and imports for HIPAA-compliant generated code.
#: ``pickle`` serialisation is disallowed because it can silently bypass
#: PHI encryption layers.
COMPLIANCE_RULES: Dict[str, Any] = {
    "banned_functions": ["pickle.loads", "pickle.load"],
    "banned_imports": ["pickle"],
}

#: Security prompt directives injected into the LLM system prompt when HIPAA
#: mode is active.  References NIST SP 800-53 control IDs inline so the LLM
#: produces controls-aligned code.
DIRECTIVE_TEXT: str = (
    "## HIPAA Security Rule Directives\n\n"
    "All generated code MUST comply with the HIPAA Security Rule "
    "(45 CFR § 164.312) and the following NIST SP 800-53 Rev 5 controls:\n\n"
    "### PHI Encryption (NIST SC-28 — Protection of Information at Rest)\n"
    "- Encrypt all Protected Health Information (PHI) at rest using AES-256.\n"
    "- Encrypt PHI in transit with TLS 1.2+ (prefer TLS 1.3).\n"
    "- Never serialise PHI with `pickle`; use JSON + encrypted field storage.\n\n"
    "### Authentication & Access Control (NIST AC-3, AC-6, IA-5)\n"
    "- Sign JWTs with RS256 (asymmetric); never use HS256 for PHI-bearing tokens.\n"
    "- Implement Role-Based Access Control (RBAC) restricting PHI to authorised "
    "roles only (principle of least privilege — AC-6).\n"
    "- Enforce short-lived tokens (≤ 15 min) with refresh-token rotation.\n\n"
    "### Audit Logging (NIST AU-2, AU-6)\n"
    "- Log every PHI access, modification, and deletion event with: timestamp "
    "(UTC), user ID, IP address, action, and resource identifier.\n"
    "- Audit logs must be immutable and retained for ≥ 6 years.\n"
    "- Implement real-time audit log review and alerting (AU-6).\n\n"
    "### Minimum Required Sections in Generated Docs\n"
    "- encryption, audit, rbac, rs256\n"
)

# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class ComplianceViolation(BaseModel):
    """Structured representation of a single HIPAA compliance violation.

    Attributes:
        standard: Compliance standard that was violated (e.g. ``"HIPAA"``).
        severity: Severity level — ``"critical"``, ``"high"``, ``"medium"``,
            or ``"low"``.
        type: Machine-readable violation category (e.g. ``"phi_handling"``).
        message: Human-readable description of the detected issue.
        file: Relative path of the offending file within the output directory.
        line: 1-based line number of the first match within the file.
        recommendation: Actionable remediation guidance.
        nist_control: Optional NIST SP 800-53 control ID associated with this
            violation (e.g. ``"SC-28"``).

    Examples:
        >>> v = ComplianceViolation(
        ...     standard="HIPAA",
        ...     severity="critical",
        ...     type="phi_handling",
        ...     message="Medical record handling detected",
        ...     file="app/patients.py",
        ...     line=42,
        ...     recommendation="Ensure HIPAA-compliant encryption and audit logging",
        ...     nist_control="SC-28",
        ... )
        >>> v.standard
        'HIPAA'
    """

    standard: str = Field(..., description="Compliance standard identifier")
    severity: str = Field(..., description="Violation severity level")
    type: str = Field(..., description="Machine-readable violation category")
    message: str = Field(..., description="Human-readable violation description")
    file: str = Field(..., description="Relative path of the offending file")
    line: int = Field(..., ge=1, description="1-based line number of the match")
    recommendation: str = Field(..., description="Actionable remediation guidance")
    nist_control: Optional[str] = Field(
        default=None, description="Associated NIST SP 800-53 control ID"
    )

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        if v not in allowed:
            raise ValueError(f"severity must be one of {allowed}, got {v!r}")
        return v


# =============================================================================
# INTERNAL STATE — thread-safe lazy loading
# =============================================================================

_plugin_lock: threading.Lock = threading.Lock()
_cached_plugin: Optional[Any] = None

# =============================================================================
# PUBLIC API
# =============================================================================


def make_compliance_plugin() -> Any:
    """Return a ``CompliancePlugin`` instance that audits generated docs for HIPAA sections.

    The plugin is created lazily and cached for the lifetime of the process.
    It checks for the presence of the four minimum HIPAA documentation sections:
    ``encryption``, ``audit``, ``rbac``, and ``rs256``.

    Returns:
        A :class:`CompliancePlugin` subclass instance named ``"HIPAACompliance"``.

    Examples:
        >>> plugin = make_compliance_plugin()
        >>> plugin.name
        'HIPAACompliance'
        >>> issues = plugin.check("## Encryption\\n## Audit\\n## RBAC\\n## RS256")
        >>> issues
        []
    """
    global _cached_plugin

    with _plugin_lock:
        if _cached_plugin is not None:
            return _cached_plugin

        from generator.agents.docgen_agent.docgen_agent import CompliancePlugin  # noqa: PLC0415

        _REQUIRED_SECTIONS = ("encryption", "audit", "rbac", "rs256")

        class HIPAACompliancePlugin(CompliancePlugin):
            """Verifies that generated documentation covers all HIPAA-required sections."""

            @property
            def name(self) -> str:  # type: ignore[override]
                return "HIPAACompliance"

            def check(self, docs_content: str) -> List[str]:
                issues: List[str] = []
                lower = docs_content.lower()
                for section in _REQUIRED_SECTIONS:
                    if section not in lower:
                        issue = (
                            f"HIPAA documentation is missing required section: '{section}'. "
                            f"Add a '{section.upper()}' section covering HIPAA Security Rule "
                            f"requirements."
                        )
                        logger.warning(
                            "HIPAA doc compliance issue",
                            extra={"missing_section": section, "plugin": "HIPAACompliance"},
                        )
                        issues.append(issue)
                return issues

        _cached_plugin = HIPAACompliancePlugin()
        logger.debug(
            "HIPAACompliance plugin created",
            extra={"plugin": "HIPAACompliance"},
        )
        return _cached_plugin


def check_generated_code(output_dir: str) -> List[Dict[str, Any]]:
    """Scan generated Python files in *output_dir* for unguarded PHI-handling patterns.

    Patterns are kept in sync with
    ``server.services.sfe_service.SFEService._check_hipaa_compliance``.

    PHI patterns detected:

    - ``patient``/``medical``/``health`` record access
    - ``diagnosis``/``prescription``/``treatment`` data
    - Medical Record Numbers (``mrn``, ``medical_record_number``)
    - Social Security Numbers (``ssn``, ``social security``)
    - Date of birth (``dob``, ``date_of_birth``, ``date-of-birth``)

    Emits an OpenTelemetry span ``"hipaa_spec.check_generated_code"`` with
    attributes ``output_dir``, ``files_scanned``, and ``violations_found``.

    Increments Prometheus counter ``spec_compliance_checks_total`` with
    ``spec="hipaa"`` and ``status="passed"`` or ``status="violations_found"``.

    Args:
        output_dir: Absolute or relative path to the directory containing the
            generated source files to be scanned.

    Returns:
        A list of :meth:`ComplianceViolation.model_dump` dicts — one entry per
        detected PHI-handling site.  Returns an empty list when no violations
        are found.

    Raises:
        OSError: If *output_dir* cannot be read (propagated to caller).

    Examples:
        >>> violations = check_generated_code("/tmp/generated_project")
        >>> isinstance(violations, list)
        True
    """
    _phi_patterns: List[tuple[str, str, str]] = [
        (
            r"\b(patient|medical|health).?record",
            "Medical record handling detected",
            "SC-28",
        ),
        (
            r"\b(diagnosis|prescription|treatment)\b",
            "PHI data handling detected",
            "SC-28",
        ),
        (
            r"\b(mrn|medical.?record.?number)\b",
            "Medical Record Number handling detected",
            "SC-28",
        ),
        (
            r"\b(ssn|social.?security)\b",
            "Social Security Number handling detected",
            "SC-28",
        ),
        (
            r"\b(dob|date.?of.?birth)\b",
            "Date of birth handling detected",
            "SC-28",
        ),
    ]

    with _tracer.start_as_current_span("hipaa_spec.check_generated_code") as span:
        span.set_attribute("output_dir", output_dir)

        violations: List[Dict[str, Any]] = []
        files_scanned = 0

        base = Path(output_dir)
        if not base.exists():
            logger.warning(
                "output_dir does not exist; skipping HIPAA scan",
                extra={"output_dir": output_dir},
            )
            span.set_attribute("files_scanned", 0)
            span.set_attribute("violations_found", 0)
            _spec_compliance_checks_total.labels(spec="hipaa", status="passed").inc()
            return violations

        for py_file in base.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning(
                    "Could not read file during HIPAA scan",
                    extra={"file": str(py_file), "error": str(exc)},
                )
                continue

            files_scanned += 1
            rel_path = str(py_file.relative_to(base))

            for pattern, message, nist_ctrl in _phi_patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[: match.start()].count("\n") + 1
                    violation = ComplianceViolation(
                        standard="HIPAA",
                        severity="critical",
                        type="phi_handling",
                        message=message,
                        file=rel_path,
                        line=line_num,
                        recommendation=(
                            "Ensure HIPAA-compliant encryption, access controls, "
                            "and audit logging for all PHI fields."
                        ),
                        nist_control=nist_ctrl,
                    )
                    violations.append(violation.model_dump())

        span.set_attribute("files_scanned", files_scanned)
        span.set_attribute("violations_found", len(violations))

        status = "violations_found" if violations else "passed"
        _spec_compliance_checks_total.labels(spec="hipaa", status=status).inc()

        logger.debug(
            "HIPAA check_generated_code complete",
            extra={
                "output_dir": output_dir,
                "files_scanned": files_scanned,
                "violations_found": len(violations),
            },
        )
        return violations


def get_compliance_gaps(config_path: str = "") -> Dict[str, List[str]]:
    """Return NIST SP 800-53 coverage gaps for the controls in :data:`NIST_CONTROL_IDS`.

    Delegates to
    ``self_fixing_engineer.guardrails.compliance_mapper.load_compliance_map``
    and ``check_coverage``, then filters the result to only the control IDs
    relevant to this HIPAA spec.

    Config path resolution order:

    1. The *config_path* argument (if non-empty).
    2. The ``CREW_CONFIG_PATH`` environment variable.
    3. Repo-root relative ``self_fixing_engineer/guardrails/crew_config.yaml``.

    Args:
        config_path: Optional explicit path to ``crew_config.yaml``.  Defaults
            to empty string, triggering automatic resolution.

    Returns:
        A dict mapping NIST control IDs (strings) to lists of gap description
        strings.  Controls with no gaps are omitted.  Returns an empty dict on
        error.

    Examples:
        >>> gaps = get_compliance_gaps()
        >>> isinstance(gaps, dict)
        True
    """
    from self_fixing_engineer.guardrails.compliance_mapper import (  # noqa: PLC0415
        load_compliance_map,
        check_coverage,
    )

    resolved_path = (
        config_path
        or os.environ.get("CREW_CONFIG_PATH", "")
        or str(
            Path(__file__).resolve().parent.parent.parent
            / "self_fixing_engineer"
            / "guardrails"
            / "crew_config.yaml"
        )
    )

    try:
        compliance_map = load_compliance_map(resolved_path)
        all_gaps: Dict[str, List[str]] = check_coverage(compliance_map)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "Failed to load compliance map for HIPAA gap analysis",
            extra={"config_path": resolved_path, "error": str(exc)},
            exc_info=True,
        )
        return {}

    filtered: Dict[str, List[str]] = {
        ctrl: gaps
        for ctrl, gaps in all_gaps.items()
        if ctrl in NIST_CONTROL_IDS and gaps
    }

    logger.debug(
        "HIPAA compliance gaps computed",
        extra={
            "config_path": resolved_path,
            "controls_with_gaps": list(filtered.keys()),
        },
    )
    return filtered
