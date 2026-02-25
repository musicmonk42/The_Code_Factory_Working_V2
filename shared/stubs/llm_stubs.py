# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Centralised fallback stubs for LLM-related utilities on the Code Factory platform.

Problem
-------
Multiple files each defined their own ``count_tokens`` and ``call_llm_api``
no-op stubs:

- ``generator/agents/codegen_agent/codegen_prompt.py``
- ``generator/agents/codegen_agent/codegen_response_handler.py``
- ``generator/agents/critique_agent/critique_prompt.py``

Each copy had slightly different heuristics or error messages, making it
impossible to diagnose stub usage from log data alone.

Solution
--------
This module provides a single, production-quality stub pair:

* :func:`count_tokens` — char/4 heuristic, returns at least 1 to avoid
  downstream division-by-zero, logs a ``WARNING`` on every call.
* :func:`call_llm_api` — async stub that always raises
  :class:`NotImplementedError` with a descriptive message.

Architecture
------------
::

    Caller
       │
       ├── count_tokens(prompt, model_name)
       │        │
       │        ├── logger.warning(...)
       │        └── return max(1, len(prompt) // 4)
       │
       └── await call_llm_api(*args, **kwargs)
                │
                └── raise NotImplementedError(...)

Usage
-----
::

    try:
        from generator.runner.llm_client import count_tokens
    except ImportError:
        from shared.stubs.llm_stubs import count_tokens

Industry Standards Applied
--------------------------
* **PEP 484** — full type annotations on all public symbols.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def count_tokens(prompt: str, model_name: str = "default") -> int:
    """Estimate token count using a simple char/4 heuristic.

    Returns at least ``1`` to prevent downstream division-by-zero in callers
    that divide a budget by the token count.

    Parameters
    ----------
    prompt : str
        The prompt text whose token count is needed.
    model_name : str
        Model name (accepted for API compatibility; not used in the estimate).

    Returns
    -------
    int
        Estimated token count: ``max(1, len(prompt) // 4)``.

    Examples
    --------
    ::

        from shared.stubs.llm_stubs import count_tokens

        assert count_tokens("") == 1          # floor of 1
        assert count_tokens("hello world") >= 1
    """
    logger.warning(
        "stub count_tokens called (model=%r) — LLM client is unavailable; "
        "returning char/4 estimate.",
        model_name,
    )
    return max(1, len(prompt) // 4)


async def call_llm_api(*args: Any, **kwargs: Any) -> Any:
    """No-op async stub for ``call_llm_api`` — always raises :class:`NotImplementedError`.

    Parameters
    ----------
    *args : Any
        Positional arguments (ignored).
    **kwargs : Any
        Keyword arguments (ignored).

    Raises
    ------
    NotImplementedError
        Always — the LLM API is unavailable in this context.

    Examples
    --------
    ::

        import asyncio
        from shared.stubs.llm_stubs import call_llm_api

        try:
            asyncio.run(call_llm_api(prompt="Hello"))
        except NotImplementedError:
            pass  # expected
    """
    raise NotImplementedError(
        "LLM API unavailable: runner LLM client could not be imported. "
        "Ensure generator.runner.llm_client is installed and accessible."
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "count_tokens",
    "call_llm_api",
]
