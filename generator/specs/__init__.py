# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/specs/__init__.py
"""
generator.specs — Compliance Specification Package

This package provides the pluggable compliance specification system for the
Code Factory generator pipeline.  Each compliance regime (HIPAA, GDPR, …) is
implemented as a self-contained module that the :mod:`router` discovers and
activates automatically when relevant trigger keywords or clarifier answers
are detected in a user's spec text.

Purpose
-------
Centralise all compliance-driven behaviour — security directives, banned-code
rules, generated-code scanning, NIST coverage gap analysis, and documentation
plugins — behind a single, stable public API so the rest of the pipeline
never needs to import individual spec modules directly.

Module Contract
---------------
Every compliance spec module in this package **must** expose the following
eight attributes/callables, with the exact types shown, or the router will
drop it with a ``WARNING`` log and it will be silently excluded from all
routing decisions:

.. code-block:: python

    TRIGGER_KEYWORDS:        frozenset[str]          # activating keywords
    CLARIFIER_QUESTION_IDS:  frozenset[str]          # clarifier question IDs
    COMPLIANCE_MODE:         str                     # e.g. "hipaa" / "gdpr"
    NIST_CONTROL_IDS:        frozenset[str]          # e.g. {"AC-3", "SC-28"}
    COMPLIANCE_RULES:        Dict[str, Any]          # banned funcs/imports
    DIRECTIVE_TEXT:          str                     # LLM security directive block
    make_compliance_plugin() -> CompliancePlugin     # doc-audit plugin factory
    check_generated_code(output_dir: str) -> List[Dict[str, Any]]
    get_compliance_gaps(config_path: str = "") -> Dict[str, List[str]]

Adding a New Compliance Regime
------------------------------
1. Create ``generator/specs/<regime>.py`` implementing all nine attributes
   listed above.
2. Append the module's short name (e.g. ``"pci"`` for PCI-DSS) to
   ``generator.specs.router._SPEC_MODULES``.

That is the **only** change required — the router will automatically discover,
validate, and route to the new module.

Public API
----------
All public functions are re-exported from :mod:`generator.specs.router`:

- :func:`get_security_directives` — concatenated LLM directive text for active specs
- :func:`get_compliance_rules` — merged banned-function/import rules
- :func:`check_generated_output` — aggregate post-generation code scan
- :func:`get_compliance_gaps` — aggregated NIST coverage gap report
- :func:`as_compliance_plugins` — list of doc-audit ``CompliancePlugin`` instances
- :func:`list_registered_specs` — sorted names of all loaded spec modules
"""

from __future__ import annotations

from generator.specs.router import (
    as_compliance_plugins,
    check_generated_output,
    get_compliance_gaps,
    get_compliance_rules,
    get_security_directives,
    list_registered_specs,
)

__all__ = [
    "get_security_directives",
    "get_compliance_rules",
    "check_generated_output",
    "get_compliance_gaps",
    "as_compliance_plugins",
    "list_registered_specs",
]
