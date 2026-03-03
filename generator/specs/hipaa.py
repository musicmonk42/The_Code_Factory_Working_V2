# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
HIPAA / Healthcare Security Spec.

Module Contract
---------------
Every ``generator/specs/<name>.py`` module must expose:

``TRIGGER_KEYWORDS : frozenset[str]``
    Lower-cased substrings; any match in spec text auto-activates this spec.

``CLARIFIER_QUESTION_IDS : frozenset[str]``
    IDs from ``generator.clarifier.clarifier_user_prompt.COMPLIANCE_QUESTIONS``
    that, when answered ``True``, also activate this spec (e.g. ``"phi_data"``).

``COMPLIANCE_MODE : str``
    The ``COMPLIANCE_MODE`` value accepted by
    ``generator.audit_log.validate_config.ConfigValidator.validate_compliance``
    (one of ``"soc2"``, ``"hipaa"``, ``"pci-dss"``, ``"gdpr"``, ``"standard"``).

``COMPLIANCE_RULES : dict``
    Additional rules merged into ``SecurityUtils.apply_compliance()`` rule dict
    (keys: ``"banned_functions"``, ``"banned_imports"``, ``"required_header"``,
    ``"max_line_length"``).

``NIST_CONTROL_IDS : frozenset[str]``
    NIST SP 800-53 control IDs (from ``compliance_mapper`` YAML) enforced here.

``DIRECTIVE_TEXT : str``
    Prompt section (starting with ``##``) injected when the spec is active.

``make_compliance_plugin() -> CompliancePlugin``
    Factory that returns a ``CompliancePlugin`` instance (from
    ``generator.agents.docgen_agent.docgen_agent``) so the spec can be
    registered in ``docgen_agent.PluginRegistry`` via
    ``DocgenAgent.register_compliance_plugin()``.

``check_generated_code(output_dir: str) -> List[Dict[str, Any]]``
    Scans generated Python files for PHI-handling violations.
    Mirrors ``server.services.sfe_service.SFEService._check_hipaa_compliance``.

``get_compliance_gaps(config_path: str = "") -> Dict[str, List[str]]``
    Delegates to ``compliance_mapper.load_compliance_map`` + ``check_coverage``
    filtered to :data:`NIST_CONTROL_IDS`.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spec identity
# ---------------------------------------------------------------------------

#: Auto-detection keywords (MD spec text, case-insensitive).
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

#: Clarifier question IDs (``COMPLIANCE_QUESTIONS``) that activate this spec.
#: ``phi_data`` is the question "Will this project process PHI?".
CLARIFIER_QUESTION_IDS: frozenset[str] = frozenset({"phi_data"})

#: Matches the ``COMPLIANCE_MODE`` accepted by ``validate_compliance()``.
COMPLIANCE_MODE: str = "hipaa"

#: NIST SP 800-53 control IDs enforced by this spec (must exist in crew_config.yaml).
NIST_CONTROL_IDS: frozenset[str] = frozenset(
    {
        "AC-3",   # Access Enforcement — RBAC on PHI routes
        "AC-6",   # Least Privilege — role-scoped JWT claims
        "AU-2",   # Audit Events — tamper-evident audit log
        "AU-6",   # Audit Review — audit log querying endpoint
        "IA-5",   # Authenticator Management — RS256 JWT signing
        "SC-28",  # Protection of Information at Rest — PHI field encryption
    }
)

#: Additional rules contributed to ``SecurityUtils.apply_compliance()``.
COMPLIANCE_RULES: Dict[str, Any] = {
    "banned_functions": ["pickle.loads", "pickle.load"],
    "banned_imports": ["pickle"],
}

# ---------------------------------------------------------------------------
# PHI detection patterns
# Mirrored from server.services.sfe_service.SFEService._check_hipaa_compliance
# ---------------------------------------------------------------------------

_PHI_PATTERNS: List[tuple[str, str]] = [
    (r"\b(patient|medical|health).?record", "Medical record handling detected"),
    (r"\b(diagnosis|prescription|treatment)\b", "PHI data handling detected"),
    (r"\b(mrn|medical.?record.?number)\b", "Medical Record Number handling detected"),
    (r"\b(ssn|social.?security)\b", "SSN / social security number handling detected"),
    (r"\b(date.?of.?birth|dob)\b", "Date-of-birth handling detected"),
]

# ---------------------------------------------------------------------------
# Prompt directive
# ---------------------------------------------------------------------------

DIRECTIVE_TEXT: str = (
    "\n\n## HIPAA / HEALTHCARE SECURITY REQUIREMENTS (MANDATORY)\n\n"
    "This spec involves Protected Health Information (PHI). "
    "ALL of the following security controls MUST be fully implemented — stubs "
    "or placeholder comments will cause a HIPAA compliance check failure "
    "(NIST controls: {nist_ids}).\n\n"
    "1. **Field-level PHI Encryption** (`app/security/phi_encryption.py`)  "
    "(NIST SC-28)\n"
    "   Use `cryptography.fernet.Fernet` (AES-256-CBC) to encrypt PHI fields "
    "before writing to the database.  Load the key from env var "
    "`PHI_ENCRYPTION_KEY`.\n\n"
    "2. **Tamper-Evident Audit Logging** (`app/services/audit_service.py`)  "
    "(NIST AU-2, AU-6)\n"
    "   Every PHI create/update/delete MUST emit a record with a SHA-256 hash "
    "chain `entry_hash = SHA256(prev_hash + payload)` in an `audit_logs` table.\n\n"
    "3. **JWT RS256 Authentication** (`app/security/auth.py`)  (NIST IA-5)\n"
    "   Replace HS256 with RS256 asymmetric signing.  Load RSA keys from env "
    "vars `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`.  Use `python-jose[cryptography]`.\n\n"
    "4. **RBAC Middleware** (`app/middleware/rbac.py`)  (NIST AC-3, AC-6)\n"
    "   All PHI routes MUST verify the JWT `role` claim via a `require_role()` "
    "FastAPI dependency before granting access.\n\n"
    "5. **TLS-only** — add an operator comment in `app/main.py` that the app "
    "must always run behind HTTPS in production.\n\n"
    "Add to `requirements.txt`:\n"
    "```\ncryptography>=41.0.0\npython-jose[cryptography]>=3.3.0\n```"
).format(nist_ids=", ".join(sorted(NIST_CONTROL_IDS)))


# ---------------------------------------------------------------------------
# CompliancePlugin implementation
# ---------------------------------------------------------------------------


def make_compliance_plugin():  # -> CompliancePlugin
    """Return a ``CompliancePlugin`` instance for this spec.

    The returned object can be passed directly to
    ``DocgenAgent.register_compliance_plugin()`` so that documentation
    generated for HIPAA specs is also checked for missing security sections.

    Returns:
        A :class:`~generator.agents.docgen_agent.docgen_agent.CompliancePlugin`
        instance that checks documentation content for required HIPAA sections.
    """
    try:
        from generator.agents.docgen_agent.docgen_agent import (  # noqa: PLC0415
            CompliancePlugin,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "generator.agents.docgen_agent is required to create a CompliancePlugin. "
            f"Original error: {exc}"
        ) from exc

    _required_doc_sections = [
        ("encryption", "Documentation must describe PHI encryption strategy"),
        ("audit", "Documentation must describe audit logging approach"),
        ("rbac", "Documentation must describe role-based access controls"),
        ("rs256", "Documentation must describe JWT RS256 authentication"),
    ]

    class _HIPAACompliancePlugin(CompliancePlugin):
        @property
        def name(self) -> str:
            return "HIPAACompliance"

        def check(self, docs_content: str) -> List[str]:
            issues = []
            lower = docs_content.lower()
            for keyword, message in _required_doc_sections:
                if keyword not in lower:
                    issues.append(message)
                    logger.warning(
                        "HIPAA doc compliance issue: missing section keyword=%r — %s",
                        keyword,
                        message,
                    )
            return issues

    return _HIPAACompliancePlugin()


# ---------------------------------------------------------------------------
# Post-generation code checker
# ---------------------------------------------------------------------------


def check_generated_code(output_dir: str) -> List[Dict[str, Any]]:
    """Scan *output_dir* for unprotected PHI-handling patterns.

    Mirrors ``server.services.sfe_service.SFEService._check_hipaa_compliance``
    so that the same pattern set drives both prompt directives and
    post-generation validation.

    Args:
        output_dir: Root directory of the generated project.

    Returns:
        List of violation dicts (may be empty).  Each dict matches the schema
        returned by ``SFEService._check_hipaa_compliance``:
        ``standard``, ``severity``, ``type``, ``message``, ``file``,
        ``line``, ``recommendation``.
    """
    violations: List[Dict[str, Any]] = []
    root = Path(output_dir)
    if not root.exists():
        return violations

    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            rel_path = str(py_file.relative_to(root))
            for pattern, message in _PHI_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[: match.start()].count("\n") + 1
                    violations.append(
                        {
                            "standard": "HIPAA",
                            "severity": "critical",
                            "type": "phi_handling",
                            "message": message,
                            "file": rel_path,
                            "line": line_num,
                            "recommendation": (
                                "Ensure HIPAA-compliant encryption (SC-28), "
                                "access controls (AC-3/AC-6), and audit logging (AU-2)"
                            ),
                        }
                    )
        except OSError as exc:
            logger.warning("HIPAA spec checker could not read %s: %s", py_file, exc)

    return violations


# ---------------------------------------------------------------------------
# NIST control gap reporter
# ---------------------------------------------------------------------------


def get_compliance_gaps(config_path: str = "") -> Dict[str, List[str]]:
    """Return NIST control coverage gaps for the HIPAA control set.

    Delegates to
    :func:`self_fixing_engineer.guardrails.compliance_mapper.load_compliance_map`
    and :func:`~compliance_mapper.check_coverage`, filtered to
    :data:`NIST_CONTROL_IDS`.

    Args:
        config_path: Path to ``crew_config.yaml``.  Defaults to the
            ``CREW_CONFIG_PATH`` env var, then the repo-root default path.

    Returns:
        Coverage-gap dict (keys ``"required_but_not_enforced"``,
        ``"partially_enforced"``, ``"not_implemented"``, ``"not_enforced"``)
        containing only :data:`NIST_CONTROL_IDS` entries.  Empty dict when
        the compliance mapper is unavailable.
    """
    if not config_path:
        config_path = os.environ.get(
            "CREW_CONFIG_PATH",
            str(
                Path(__file__).resolve().parent.parent.parent
                / "self_fixing_engineer"
                / "guardrails"
                / "crew_config.yaml"
            ),
        )
    try:
        from self_fixing_engineer.guardrails.compliance_mapper import (  # noqa: PLC0415
            check_coverage,
            load_compliance_map,
        )

        full_map = load_compliance_map(config_path)
        hipaa_map = {k: v for k, v in full_map.items() if k in NIST_CONTROL_IDS}
        if not hipaa_map:
            logger.debug(
                "HIPAA spec: none of %s found in compliance map at %s",
                NIST_CONTROL_IDS,
                config_path,
            )
            return {}
        return check_coverage(hipaa_map)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "HIPAA spec: could not load compliance gaps from %s: %s", config_path, exc
        )
        return {}
