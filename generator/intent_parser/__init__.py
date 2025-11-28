"""
Intent Parser package entry point.
"""

import sys

# --- Module Aliasing for Backwards Compatibility ---
# Set up 'intent_parser' as an alias to this module (generator.intent_parser)
if "intent_parser" not in sys.modules:
    sys.modules["intent_parser"] = sys.modules[__name__]
