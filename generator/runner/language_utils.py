# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Language utilities - re-exports for backwards compatibility.

This module provides language detection utilities that were previously
expected to be in runner.language_utils by some modules (e.g., clarifier_user_prompt.py).
It re-exports detect_language from runner_parsers for backwards compatibility.
"""

from .runner_parsers import detect_language

__all__ = ["detect_language"]
