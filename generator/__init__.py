# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/__init__.py
"""
Generator package entry point.
Imports subpackages which set up their own module aliases for backwards compatibility.
"""

import sys as _sys
import logging as _logging

_logger = _logging.getLogger(__name__)

# Ensure this package is reachable as both ``generator`` and its fully-qualified
# name.  Only alias when the slot is truly empty; never overwrite a pre-existing
# entry with a different identity (which would indicate a naming collision).
_existing = _sys.modules.get("generator")
if _existing is None:
    _sys.modules["generator"] = _sys.modules[__name__]
elif _existing is not _sys.modules[__name__]:
    _logger.debug(
        "generator/__init__: 'generator' already in sys.modules as %r — "
        "skipping self-alias to preserve existing registration.",
        _existing,
    )

# Import ALL subpackages - they will set up their own aliases
try:
    from . import runner  # noqa: F401
except ImportError:
    pass

try:
    from . import intent_parser  # noqa: F401
except ImportError:
    pass

try:
    from . import clarifier  # noqa: F401
except ImportError:
    pass

try:
    from . import agents  # noqa: F401
except ImportError:
    pass

try:
    from . import audit_log  # noqa: F401
except ImportError:
    pass

try:
    from . import main  # noqa: F401
except ImportError:
    pass

# Expose commonly accessed submodules as attributes
__all__ = ['runner', 'intent_parser', 'clarifier', 'agents', 'audit_log', 'main']

