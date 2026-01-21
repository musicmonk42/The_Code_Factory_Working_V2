"""
Audit Log Package

This package provides secure, tamper-evident audit logging functionality.
"""

# Lazy import to avoid circular dependencies during module initialization
_log_action = None


def _get_log_action():
    """Lazy-load log_action to prevent circular imports at module load time."""
    global _log_action
    if _log_action is None:
        try:
            from .audit_log import log_action as _imported_log_action
            _log_action = _imported_log_action
        except ImportError:
            # Fallback dummy if circular dependency still occurs
            import logging
            logging.getLogger(__name__).debug(
                "log_action lazy import failed, using dummy"
            )

            async def _dummy_log_action(*args, **kwargs):
                pass

            _log_action = _dummy_log_action
    return _log_action


async def log_action(*args, **kwargs):
    """
    Wrapper for the actual log_action function.
    
    This wrapper enables lazy loading to prevent circular import issues
    when audit_crypto modules import log_action from the parent package.
    """
    actual_log_action = _get_log_action()
    return await actual_log_action(*args, **kwargs)


__all__ = ["log_action"]
