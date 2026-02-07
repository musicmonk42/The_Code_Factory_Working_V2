# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/__init__.py
"""
Generator package entry point.
Imports subpackages which set up their own module aliases for backwards compatibility.
"""

import sys as _sys

# First, ensure this generator package can be found as 'generator'
if "generator" not in _sys.modules:
    _sys.modules["generator"] = _sys.modules[__name__]

# Import ALL subpackages - they will set up their own aliases
try:
    from . import runner  # noqa: F401
except ImportError:
    pass

try:
    from . import intent_parser  # noqa: F401
except ImportError:
    pass

# FIX: Add missing submodule imports that tests expect
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

