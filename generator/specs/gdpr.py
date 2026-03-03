# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
GDPR / EU-Privacy Security Spec.

Follows the full module contract defined in ``generator/specs/hipaa.py``:
``TRIGGER_KEYWORDS``, ``CLARIFIER_QUESTION_IDS``, ``COMPLIANCE_MODE``,
``COMPLIANCE_RULES``, ``NIST_CONTROL_IDS``, ``DIRECTIVE_TEXT``,
``make_compliance_plugin()``, ``check_generated_code()``,
``get_compliance_gaps()``.

Integration
-----------
* :func:`check_generated_code` mirrors
  ``server.services.sfe_service.SFEService._check_gdpr_compliance``.
* :func:`get_compliance_gaps` delegates to
  ``self_fixing_engineer.guardrails.compliance_mapper``.
* :func:`make_compliance_plugin` returns a ``CompliancePlugin`` for
  ``docgen_agent.PluginRegistry``.
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

TRIGGER_KEYWORDS: frozenset[str] = frozenset(
    {
        "gdpr",
        "ccpa",
        "pipeda",
        "personal data",
        "data subject",
        "right to erasure",
        "right to be forgotten",
        "data controller",
        "data processor",
        "consent management",
    }
)

#: Clarifier question IDs that activate this spec.
#: ``gdpr_apply`` is the question "Does this project need to comply with GDPR?".
CLARIFIER_QUESTION_IDS: frozenset[str] = frozenset({"gdpr_apply"})

#: Matches the ``COMPLIANCE_MODE`` accepted by ``validate_compliance()``.
COMPLIANCE_MODE: str = "gdpr"

#: NIST SP 800-53 control IDs enforced by this spec.
NIST_CONTROL_IDS: frozenset[str] = frozenset(
    {
        "AC-3",   # Access Enforcement — data-subject scoped access
        "AU-2",   # Audit Events — personal data access log
        "SC-28",  # Protection of Information at Rest — PII encryption
    }
)

#: Additional rules contributed to ``SecurityUtils.apply_compliance()``.
COMPLIANCE_RULES: Dict[str, Any] = {
    "banned_functions": [],
    "banned_imports": [],
}

# ---------------------------------------------------------------------------
# PII detection patterns
# Mirrored from server.services.sfe_service.SFEService._check_gdpr_compliance
# ---------------------------------------------------------------------------

_PII_PATTERNS: List[tuple[str, str]] = [
    (r"\b(email|e-mail|mail)\b.*=.*input", "Email collection without consent mechanism"),
    (r"\b(ssn|social.?security)\b", "Social Security Number handling detected"),
    (r"\b(credit.?card|card.?number|cvv)\b", "Credit card data handling detected"),
    (r"\b(password|passwd)\b.*=.*input", "Password handling without encryption"),
    (r"\b(dob|date.?of.?birth|birthday)\b", "Date of birth collection detected"),
]

# ---------------------------------------------------------------------------
# Prompt directive
# ---------------------------------------------------------------------------

DIRECTIVE_TEXT: str = (
    "\n\n## GDPR / PRIVACY SECURITY REQUIREMENTS (MANDATORY)\n\n"
    "This spec involves personal data subject to GDPR or equivalent privacy "
    "regulation (NIST controls: {nist_ids}).  "
    "ALL of the following controls MUST be fully implemented.\n\n"
    "1. **Consent Tracking** — store a `consents` table with lawful basis, "
    "`granted_at`, and `revoked_at` timestamps for each data-processing activity.\n\n"
    "2. **Right to Erasure** (`DELETE /users/{{id}}/data`) — anonymise or remove "
    "all personal data for the data subject while preserving non-personal records.\n\n"
    "3. **Data Minimisation** — response schemas MUST exclude passwords, internal "
    "IDs, and raw audit payloads from all public endpoints.  (NIST AC-3)\n\n"
    "4. **Personal Data Audit Log** — log every access to personal data with "
    "accessor identity, timestamp, and purpose in a `gdpr_audit_log` table.  "
    "(NIST AU-2)\n\n"
    "5. **Encryption at Rest** — personal data fields (email, phone, address) "
    "MUST be encrypted using `cryptography.fernet.Fernet` (AES-256-CBC).  "
    "(NIST SC-28)\n\n"
    "Add to `requirements.txt`:\n```\ncryptography>=41.0.0\n```"
).format(nist_ids=", ".join(sorted(NIST_CONTROL_IDS)))


# ---------------------------------------------------------------------------
# CompliancePlugin implementation
# ---------------------------------------------------------------------------


def make_compliance_plugin():  # -> CompliancePlugin
    """Return a ``CompliancePlugin`` for use in ``docgen_agent.PluginRegistry``.

    Checks that generated documentation mentions the required GDPR sections.
    Pass the returned object to ``DocgenAgent.register_compliance_plugin()``.
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
        ("consent", "Documentation must describe the consent management approach"),
        ("erasure", "Documentation must describe the right-to-erasure endpoint"),
        ("encryption", "Documentation must describe PII encryption strategy"),
        ("audit", "Documentation must describe personal data audit logging"),
    ]

    class _GDPRCompliancePlugin(CompliancePlugin):
        @property
        def name(self) -> str:
            return "GDPRCompliance"

        def check(self, docs_content: str) -> List[str]:
            issues = []
            lower = docs_content.lower()
            for keyword, message in _required_doc_sections:
                if keyword not in lower:
                    issues.append(message)
                    logger.warning(
                        "GDPR doc compliance issue: missing section keyword=%r — %s",
                        keyword,
                        message,
                    )
            return issues

    return _GDPRCompliancePlugin()


# ---------------------------------------------------------------------------
# Post-generation code checker
# ---------------------------------------------------------------------------


def check_generated_code(output_dir: str) -> List[Dict[str, Any]]:
    """Scan *output_dir* for unprotected PII-handling patterns.

    Mirrors ``server.services.sfe_service.SFEService._check_gdpr_compliance``.

    Args:
        output_dir: Root directory of the generated project.

    Returns:
        List of violation dicts (may be empty).
    """
    violations: List[Dict[str, Any]] = []
    root = Path(output_dir)
    if not root.exists():
        return violations

    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            rel_path = str(py_file.relative_to(root))
            for pattern, message in _PII_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[: match.start()].count("\n") + 1
                    violations.append(
                        {
                            "standard": "GDPR",
                            "severity": "high",
                            "type": "pii_handling",
                            "message": message,
                            "file": rel_path,
                            "line": line_num,
                            "recommendation": (
                                "Ensure proper consent, encryption (SC-28), "
                                "and data protection measures (AC-3)"
                            ),
                        }
                    )
        except OSError as exc:
            logger.warning("GDPR spec checker could not read %s: %s", py_file, exc)

    return violations


# ---------------------------------------------------------------------------
# NIST control gap reporter
# ---------------------------------------------------------------------------


def get_compliance_gaps(config_path: str = "") -> Dict[str, List[str]]:
    """Return NIST control coverage gaps for the GDPR control set.

    Delegates to :mod:`self_fixing_engineer.guardrails.compliance_mapper`.
    See :func:`generator.specs.hipaa.get_compliance_gaps` for full docs.
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
        gdpr_map = {k: v for k, v in full_map.items() if k in NIST_CONTROL_IDS}
        if not gdpr_map:
            return {}
        return check_coverage(gdpr_map)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "GDPR spec: could not load compliance gaps from %s: %s", config_path, exc
        )
        return {}
