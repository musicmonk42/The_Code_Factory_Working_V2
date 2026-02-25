# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
generator.clarifier._audit_compat
==================================

Single canonical definition of ``_wrap_log_audit_event`` used by both
:mod:`generator.clarifier.clarifier` and
:mod:`generator.clarifier.clarifier_prompt`.

Previously each of those modules defined its own nearly-identical copy of this
helper.  That copy is now removed and both modules import from here instead,
eliminating the duplication.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _wrap_log_audit_event(action: str, data=None, **kwargs) -> None:
    """
    Wrapper that converts legacy ``log_action`` calls to ``log_audit_event``
    format.

    The original ``log_action`` interface accepted ``(action_name, **kwargs)``.
    The new ``log_audit_event`` requires ``(action, data_dict)``.

    An optional ``data`` positional-style keyword argument is merged into
    ``kwargs`` before forwarding, matching the clarifier_prompt.py convention.
    """
    try:
        from runner.runner_audit import log_audit_event  # deferred to avoid circular imports

        if data is not None:
            if isinstance(data, dict):
                kwargs.update(data)
            else:
                kwargs["data"] = data
        await log_audit_event(action=action, data=kwargs)
    except ImportError:
        logger.debug("log_action: %s, %s", action, kwargs)
    except Exception as exc:
        logger.warning("log_action failed: %s", exc, extra={"action": action})
