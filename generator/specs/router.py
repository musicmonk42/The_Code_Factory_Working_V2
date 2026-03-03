# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Security-Spec Router — routes compliance directives into code-generation
prompts and post-generation validation based on the project spec.

Integration with existing compliance infrastructure
----------------------------------------------------
This module is the single wiring point between ``generator/specs/`` and
the rest of the compliance stack:

* **Prompt injection** — :func:`get_security_directives` assembles ``DIRECTIVE_TEXT``
  from matching specs for appending to the LLM prompt.

* **SecurityUtils** — :func:`get_compliance_rules` merges each matching spec's
  ``COMPLIANCE_RULES`` dict so ``SecurityUtils.apply_compliance()`` in
  ``codegen_agent.py`` receives up-to-date rules without manual configuration.

* **Clarifier answers** — :func:`get_security_directives` and all other routing
  functions accept ``compliance_preferences`` (the ``UserProfile.compliance_preferences``
  dict populated by ``COMPLIANCE_QUESTIONS`` in ``clarifier_user_prompt.py``) to
  activate specs even when keyword detection misses them.

* **CompliancePlugin registry** — :func:`as_compliance_plugins` returns
  ``CompliancePlugin`` instances (``docgen_agent.CompliancePlugin`` ABC) for
  each matched spec so they can be registered with
  ``DocgenAgent.register_compliance_plugin()`` / ``PluginRegistry.register()``.

* **Post-generation validation** — :func:`check_generated_output` calls each
  matched spec's ``check_generated_code()`` which mirrors the scanning logic of
  ``server.services.sfe_service.SFEService._check_hipaa_compliance`` / ``_check_gdpr_compliance``.

* **NIST gap reporting** — :func:`get_compliance_gaps` aggregates gaps across
  all matched specs by delegating to ``compliance_mapper.check_coverage()``.

Adding a new compliance spec
-----------------------------
1. Create ``generator/specs/<name>.py`` exposing the full module contract
   (see ``generator/specs/hipaa.py`` for reference).
2. Add the module name to :data:`_SPEC_MODULES` below.
3. Done — no other file needs to change.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry — add new spec module names here only.
# ---------------------------------------------------------------------------

#: Ordered list of spec module names (relative to ``generator.specs``).
_SPEC_MODULES: List[str] = [
    "hipaa",
    "gdpr",
]

# ---------------------------------------------------------------------------
# Internal lazy cache
# ---------------------------------------------------------------------------

_loaded_specs: Optional[Dict[str, ModuleType]] = None


def _load_specs() -> Dict[str, ModuleType]:
    """Import every registered spec module, validate the contract, cache."""
    specs: Dict[str, ModuleType] = {}
    required_attrs = {
        "TRIGGER_KEYWORDS": frozenset,
        "CLARIFIER_QUESTION_IDS": frozenset,
        "COMPLIANCE_MODE": str,
        "COMPLIANCE_RULES": dict,
        "NIST_CONTROL_IDS": frozenset,
        "DIRECTIVE_TEXT": str,
    }
    for name in _SPEC_MODULES:
        full_name = f"generator.specs.{name}"
        try:
            mod = importlib.import_module(full_name)
        except ImportError as exc:
            logger.warning("Could not import security spec %r: %s", full_name, exc)
            continue

        # Validate contract attributes.
        valid = True
        for attr, expected_type in required_attrs.items():
            value = getattr(mod, attr, None)
            if not isinstance(value, expected_type):
                logger.warning(
                    "Spec module %r attribute %r must be %s, got %s — skipping",
                    full_name,
                    attr,
                    expected_type.__name__,
                    type(value).__name__,
                )
                valid = False
                break
        if not valid:
            continue

        specs[name] = mod
        logger.debug("Registered security spec: %s (mode=%s)", name, mod.COMPLIANCE_MODE)
    return specs


def _get_loaded_specs() -> Dict[str, ModuleType]:
    global _loaded_specs
    if _loaded_specs is None:
        _loaded_specs = _load_specs()
    return _loaded_specs


# ---------------------------------------------------------------------------
# Spec matching
# ---------------------------------------------------------------------------


def _match_specs(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> List[ModuleType]:
    """Return the ordered list of spec modules that match *spec_text*.

    A spec is considered active when **either**:
    - any of its ``TRIGGER_KEYWORDS`` appears in the lower-cased spec text, or
    - any of its ``CLARIFIER_QUESTION_IDS`` maps to a truthy value in
      *compliance_preferences* (the dict from
      ``clarifier_user_prompt.UserProfile.compliance_preferences``).

    Args:
        spec_text: Raw project specification text (README / MD content).
        compliance_preferences: Optional dict of clarifier answers, e.g.
            ``{"phi_data": True, "gdpr_apply": False}``.

    Returns:
        Ordered list of matching spec modules (preserves :data:`_SPEC_MODULES` order).
    """
    lower_text = spec_text.lower() if spec_text else ""
    prefs = compliance_preferences or {}
    matched: List[ModuleType] = []

    for mod in _get_loaded_specs().values():
        keyword_hit = any(kw in lower_text for kw in mod.TRIGGER_KEYWORDS)
        clarifier_hit = any(prefs.get(qid) for qid in mod.CLARIFIER_QUESTION_IDS)
        if keyword_hit or clarifier_hit:
            matched.append(mod)
            logger.debug(
                "Spec %r matched (keyword=%s, clarifier=%s)",
                mod.COMPLIANCE_MODE,
                keyword_hit,
                clarifier_hit,
            )
    return matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_security_directives(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> str:
    """Return concatenated directive sections for all specs that match.

    Intended to be appended to the LLM prompt in
    ``generator.agents.codegen_agent.codegen_prompt.build_code_generation_prompt``.

    Args:
        spec_text: Raw project specification (Markdown README).
        compliance_preferences: Clarifier answers from
            ``UserProfile.compliance_preferences`` (optional).

    Returns:
        String of one or more ``##``-headed sections, or ``""`` if none match.

    Examples:
        >>> "HIPAA" in get_security_directives("This app stores PHI data.")
        True
        >>> get_security_directives("A simple todo list app")
        ''
    """
    matched = _match_specs(spec_text, compliance_preferences)
    return "\n".join(mod.DIRECTIVE_TEXT for mod in matched)


def get_compliance_rules(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return merged ``COMPLIANCE_RULES`` for all matched specs.

    The returned dict can be merged into ``CodegenAgentConfig.compliance_rules``
    so that ``SecurityUtils.apply_compliance()`` enforces spec-specific rules
    (banned functions/imports) without manual configuration.

    Args:
        spec_text: Raw project specification.
        compliance_preferences: Clarifier answers (optional).

    Returns:
        Merged rule dict with keys ``"banned_functions"`` and
        ``"banned_imports"`` (both lists), ready to be merged with the
        agent's existing compliance rules.
    """
    merged: Dict[str, Any] = {"banned_functions": [], "banned_imports": []}
    for mod in _match_specs(spec_text, compliance_preferences):
        rules: Dict[str, Any] = mod.COMPLIANCE_RULES
        for key in ("banned_functions", "banned_imports"):
            for item in rules.get(key, []):
                if item not in merged[key]:
                    merged[key].append(item)
    return merged


def check_generated_output(
    output_dir: str,
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run post-generation compliance checks for all matched specs.

    Calls each matched spec's ``check_generated_code()`` which mirrors
    ``SFEService._check_hipaa_compliance`` / ``_check_gdpr_compliance``
    patterns.  Suitable for calling after code generation completes to
    surface compliance violations before the output is packaged.

    Args:
        output_dir: Root directory of the generated project.
        spec_text: Raw project specification.
        compliance_preferences: Clarifier answers (optional).

    Returns:
        Combined list of violation dicts from all matched specs.
    """
    violations: List[Dict[str, Any]] = []
    for mod in _match_specs(spec_text, compliance_preferences):
        checker = getattr(mod, "check_generated_code", None)
        if callable(checker):
            try:
                violations.extend(checker(output_dir))
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Spec %r check_generated_code raised: %s", mod.COMPLIANCE_MODE, exc
                )
    return violations


def get_compliance_gaps(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
    config_path: str = "",
) -> Dict[str, Dict[str, List[str]]]:
    """Return NIST control coverage gaps for all matched specs.

    Delegates to each spec's ``get_compliance_gaps()`` which calls
    ``compliance_mapper.load_compliance_map`` + ``check_coverage``.

    Args:
        spec_text: Raw project specification.
        compliance_preferences: Clarifier answers (optional).
        config_path: Path to ``crew_config.yaml`` (optional; see each spec
            module for default resolution logic).

    Returns:
        ``{compliance_mode: coverage_gap_dict}`` for each matched spec.
    """
    result: Dict[str, Dict[str, List[str]]] = {}
    for mod in _match_specs(spec_text, compliance_preferences):
        gap_fn = getattr(mod, "get_compliance_gaps", None)
        if callable(gap_fn):
            try:
                gaps = gap_fn(config_path) if config_path else gap_fn()
                if gaps:
                    result[mod.COMPLIANCE_MODE] = gaps
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Spec %r get_compliance_gaps raised: %s", mod.COMPLIANCE_MODE, exc
                )
    return result


def as_compliance_plugins(
    spec_text: str,
    compliance_preferences: Optional[Dict[str, Any]] = None,
) -> List[Any]:  # List[CompliancePlugin]
    """Return ``CompliancePlugin`` instances for all matched specs.

    Each returned object is a concrete subclass of
    ``generator.agents.docgen_agent.docgen_agent.CompliancePlugin`` and can
    be registered directly with ``DocgenAgent.register_compliance_plugin()``
    or ``PluginRegistry.register()``.

    Args:
        spec_text: Raw project specification.
        compliance_preferences: Clarifier answers (optional).

    Returns:
        List of ``CompliancePlugin`` instances (may be empty when no specs
        match or when ``docgen_agent`` is unavailable).
    """
    plugins: List[Any] = []
    for mod in _match_specs(spec_text, compliance_preferences):
        factory = getattr(mod, "make_compliance_plugin", None)
        if callable(factory):
            try:
                plugins.append(factory())
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Spec %r make_compliance_plugin raised: %s",
                    mod.COMPLIANCE_MODE,
                    exc,
                )
    return plugins


def list_registered_specs() -> List[str]:
    """Return names of all successfully loaded spec modules.

    Returns:
        Sorted list, e.g. ``["gdpr", "hipaa"]``.
    """
    return sorted(_get_loaded_specs().keys())
