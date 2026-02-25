# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Centralised fallback stubs for ``log_audit_event`` on the Code Factory platform.

Problem
-------
Four files each defined their own ``log_audit_event`` no-op stub:

- ``generator/agents/codegen_agent/codegen_prompt.py``
- ``generator/agents/codegen_agent/codegen_response_handler.py``
- ``generator/agents/critique_agent/critique_agent.py``
- ``generator/agents/critique_agent/critique_prompt.py``

Every copy was subtly different (different log levels, different signatures),
leading to inconsistent audit trails when the real runner logging was
unavailable.

Solution
--------
This module provides a single, production-quality stub pair:

* :func:`log_audit_event` — async stub that logs a ``WARNING`` once per call.
* :func:`log_audit_event_sync` — synchronous counterpart.

Both accept ``*args, **kwargs`` so they are drop-in replacements for any
previous local definition without signature changes.

Architecture
------------
::

    Caller (async)                 Caller (sync)
         │                              │
         │ await log_audit_event(...)   │ log_audit_event_sync(...)
         ▼                              ▼
    logger.warning(...)          logger.warning(...)
    (no audit record written)    (no audit record written)

Usage
-----
::

    try:
        from generator.runner.runner_logging import log_audit_event
    except ImportError:
        from shared.stubs.audit_stubs import log_audit_event

Industry Standards Applied
--------------------------
* **PEP 484** — full type annotations on all public symbols.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def log_audit_event(*args: Any, **kwargs: Any) -> None:
    """No-op async fallback for ``log_audit_event``.

    Logs a single ``WARNING`` to indicate that the stub is in use, then
    returns immediately without writing any audit record.

    Parameters
    ----------
    *args : Any
        Positional arguments (ignored).
    **kwargs : Any
        Keyword arguments (ignored).

    Returns
    -------
    None

    Examples
    --------
    ::

        import asyncio
        from shared.stubs.audit_stubs import log_audit_event

        asyncio.run(log_audit_event("job_complete", job_id="abc123"))
    """
    logger.warning(
        "stub log_audit_event called — runner audit utility is unavailable; "
        "no audit record was written."
    )


def log_audit_event_sync(*args: Any, **kwargs: Any) -> None:
    """No-op synchronous fallback for ``log_audit_event``.

    Logs a single ``WARNING`` to indicate that the stub is in use, then
    returns immediately without writing any audit record.

    Parameters
    ----------
    *args : Any
        Positional arguments (ignored).
    **kwargs : Any
        Keyword arguments (ignored).

    Returns
    -------
    None

    Examples
    --------
    ::

        from shared.stubs.audit_stubs import log_audit_event_sync

        log_audit_event_sync("job_complete", job_id="abc123")
    """
    logger.warning(
        "stub log_audit_event_sync called — runner audit utility is unavailable; "
        "no audit record was written."
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "log_audit_event",
    "log_audit_event_sync",
]
