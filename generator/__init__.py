# generator/__init__.py
"""
Generator package entry point.
"""
import sys as _sys
import logging

logger = logging.getLogger(__name__)

# Ensure this generator package can be found as 'generator'
if "generator" not in _sys.modules:
    _sys.modules["generator"] = _sys.modules[__name__]

# Only expose successfully imported modules
__all__ = []

def _safe_import(module_name: str, package_attr: str):
    """Safely import submodule and add to __all__ if successful."""
    try:
        module = __import__(f".{module_name}", fromlist=[package_attr], level=0, package=__name__)
        globals()[package_attr] = module
        __all__.append(package_attr)
        return module
    except Exception as e:
        logger.debug(f"Submodule {module_name} not available: {e}")
        return None

# Import submodules
runner = _safe_import("runner", "runner")
clarifier = _safe_import("clarifier", "clarifier")
agents = _safe_import("agents", "agents")
audit_log = _safe_import("audit_log", "audit_log")
main = _safe_import("main", "main")
intent_parser = _safe_import("intent_parser", "intent_parser")

