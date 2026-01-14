"""
Self-Fixing Engineer (SFE) package entry point.
Sets up module aliases for backwards compatibility and internal imports.
"""

import logging
import sys

_init_logger = logging.getLogger(__name__)

# --- Module Aliasing for Backwards Compatibility ---
# This must be done BEFORE any submodule imports to prevent duplicate module loading.
# Many internal modules use relative imports like 'from simulation.xyz import ...'
# or 'from arbiter.xyz import ...', which need to resolve to the full package path.

# Set up 'simulation' as an alias to self_fixing_engineer.simulation
try:
    from . import simulation as _simulation
    if "simulation" not in sys.modules:
        sys.modules["simulation"] = _simulation
except ImportError as e:
    _init_logger.debug("simulation module not available: %s", e)

# Set up 'arbiter' as an alias to self_fixing_engineer.arbiter
try:
    from . import arbiter as _arbiter
    if "arbiter" not in sys.modules:
        sys.modules["arbiter"] = _arbiter
except ImportError as e:
    _init_logger.debug("arbiter module not available: %s", e)

# Set up 'guardrails' as an alias to self_fixing_engineer.guardrails
try:
    from . import guardrails as _guardrails
    if "guardrails" not in sys.modules:
        sys.modules["guardrails"] = _guardrails
except ImportError as e:
    _init_logger.debug("guardrails module not available: %s", e)

# Set up 'test_generation' as an alias to self_fixing_engineer.test_generation
try:
    from . import test_generation as _test_generation
    if "test_generation" not in sys.modules:
        sys.modules["test_generation"] = _test_generation
except ImportError as e:
    _init_logger.debug("test_generation module not available: %s", e)

# Set up 'intent_capture' as an alias to self_fixing_engineer.intent_capture
try:
    from . import intent_capture as _intent_capture
    if "intent_capture" not in sys.modules:
        sys.modules["intent_capture"] = _intent_capture
except ImportError as e:
    _init_logger.debug("intent_capture module not available: %s", e)

# Version info
__version__ = "1.0.0"
