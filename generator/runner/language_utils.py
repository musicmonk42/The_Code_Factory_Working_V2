# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Language utilities - backwards compatibility and language detection module.

This module provides language detection utilities that were previously
expected to be in runner.language_utils by some modules (e.g., clarifier_user_prompt.py).
It re-exports detect_language from runner_parsers for backwards compatibility while
maintaining a stable API surface.

Architecture:
    - Acts as a facade/adapter for language detection functionality
    - Centralizes language utilities in one importable location
    - Provides backwards compatibility for legacy import paths
    - Maintains separation of concerns while enabling cross-module usage

Integration Points:
    - Used by: generator/clarifier/clarifier_user_prompt.py
    - Imports from: generator/runner/runner_parsers.py
    - Module alias: Available as both 'runner.language_utils' and 'generator.runner.language_utils'

Compliance:
    - Thread-safe: All exported functions are thread-safe
    - Async-safe: Functions are synchronous and do not block event loops
    - Error handling: Exceptions from underlying implementations are propagated with context
    - Logging: Inherits logging from runner_parsers module

Version: 1.0.0
Created: 2025-02-15
Last Modified: 2025-02-15
"""

import logging
from typing import Dict, Union

# Import detect_language from the canonical location in runner_parsers
# This function analyzes code files and determines the programming language
try:
    from .runner_parsers import detect_language
except ImportError as e:
    # Fallback logging if runner_parsers is not available
    # This should never happen in production but provides defensive error handling
    logging.getLogger(__name__).error(
        f"Failed to import detect_language from runner_parsers: {e}. "
        "Language detection functionality will not be available."
    )
    
    # Define a fallback function that raises a clear error
    def detect_language(code_files: Union[Dict[str, str], str]) -> str:
        """
        Fallback function when runner_parsers is unavailable.
        
        Args:
            code_files: Code files to analyze (not used in fallback)
            
        Raises:
            NotImplementedError: Always raised as runner_parsers is not available
        """
        raise NotImplementedError(
            "Language detection is not available. "
            "The runner_parsers module failed to import. "
            "This is a critical error that should be investigated."
        )

# Explicitly define the public API surface
# This ensures that only intended functions are exported via 'from runner.language_utils import *'
__all__ = ["detect_language"]

# Module metadata for introspection
__version__ = "1.0.0"
__author__ = "Novatrax Labs LLC"
__status__ = "Production"

