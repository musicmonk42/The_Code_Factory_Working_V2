# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/runner/providers/__init__.py

"""
Provider plugins package for runner.llm_client / LLMPluginManager.

Each *_provider.py module in this package should expose:
    - `get_provider()` -> LLMProvider instance
    - An LLMProvider subclass implementing:
        - async call(...)
        - async count_tokens(...)
        - async health_check()
"""

# This file intentionally left blank (or with just the docstring)
# to mark this directory as a Python package.
# The llm_plugin_manager will scan this directory for
# modules ending in '_provider.py' and load them.
