# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
generator.specs — Compliance / Security Spec Directive Registry.

Each sub-module represents one compliance regime and exposes the full
module contract (``TRIGGER_KEYWORDS``, ``CLARIFIER_QUESTION_IDS``,
``COMPLIANCE_MODE``, ``COMPLIANCE_RULES``, ``NIST_CONTROL_IDS``,
``DIRECTIVE_TEXT``, ``make_compliance_plugin()``,
``check_generated_code()``, ``get_compliance_gaps()``).

To add a new regime create ``generator/specs/<name>.py`` and register it
in :data:`generator.specs.router._SPEC_MODULES` — no other file changes.

Public API (re-exported from :mod:`generator.specs.router`)
-----------------------------------------------------------
:func:`get_security_directives`
    Inject compliance prompt sections based on spec text + clarifier answers.

:func:`get_compliance_rules`
    Merge spec ``COMPLIANCE_RULES`` into ``SecurityUtils.apply_compliance()``
    rule dict.

:func:`check_generated_output`
    Post-generation code scan (delegates to sfe_service patterns).

:func:`get_compliance_gaps`
    NIST control gap report (delegates to compliance_mapper).

:func:`as_compliance_plugins`
    ``CompliancePlugin`` instances for ``docgen_agent.PluginRegistry``.

:func:`list_registered_specs`
    Names of all loaded spec modules.
"""

from generator.specs.router import (
    as_compliance_plugins,
    check_generated_output,
    get_compliance_gaps,
    get_compliance_rules,
    get_security_directives,
    list_registered_specs,
)

__all__ = [
    "as_compliance_plugins",
    "check_generated_output",
    "get_compliance_gaps",
    "get_compliance_rules",
    "get_security_directives",
    "list_registered_specs",
]
