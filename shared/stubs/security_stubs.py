# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Centralised fallback stub for ``redact_secrets`` on the Code Factory platform.

Problem
-------
Multiple files each defined their own ``redact_secrets`` no-op stub:

- ``generator/agents/codegen_agent/codegen_prompt.py``
- ``generator/agents/codegen_agent/codegen_response_handler.py``
- ``generator/agents/critique_agent/critique_prompt.py``

Each copy returned the input text unchanged but with slightly different
logging behaviour, making it hard to audit where secrets might have leaked.

Solution
--------
This module provides a single, production-quality stub:

* :func:`redact_secrets` — returns the input text unchanged, emitting a
  ``WARNING`` once per call so that log aggregation can detect stub usage.

Architecture
------------
::

    Caller
       │
       │ redact_secrets(text)
       ▼
    logger.warning(...)
    return text   (unchanged — stub only)

Usage
-----
::

    try:
        from generator.runner.runner_security_utils import redact_secrets
    except ImportError:
        from shared.stubs.security_stubs import redact_secrets

Industry Standards Applied
--------------------------
* **PEP 484** — full type annotations on all public symbols.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def redact_secrets(text: str) -> str:
    """No-op fallback for ``redact_secrets`` — returns *text* unchanged.

    Logs a single ``WARNING`` so that log aggregation can detect when the
    stub is active (meaning secrets are **not** being redacted).

    Parameters
    ----------
    text : str
        Input text that would normally have secrets redacted.

    Returns
    -------
    str
        The original *text*, unmodified.

    Examples
    --------
    ::

        from shared.stubs.security_stubs import redact_secrets

        out = redact_secrets("token=abc123")
        assert out == "token=abc123"  # stub: no redaction performed
    """
    logger.warning(
        "stub redact_secrets called — runner security utility is unavailable; "
        "secrets have NOT been redacted."
    )
    return text


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "redact_secrets",
]
