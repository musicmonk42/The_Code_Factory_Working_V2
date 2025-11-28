# generator/__init__.py
"""
Generator package entry point.
Imports subpackages which set up their own module aliases for backwards compatibility.
"""

import sys as _sys

# First, ensure this generator package can be found as 'generator'
if "generator" not in _sys.modules:
    _sys.modules["generator"] = _sys.modules[__name__]

# Import subpackages - they will set up their own aliases
try:
    from . import runner  # noqa: F401
except ImportError:
    pass

try:
    from . import intent_parser  # noqa: F401
except ImportError:
    pass


