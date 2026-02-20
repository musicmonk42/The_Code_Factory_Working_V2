# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Compatibility shim for legacy ``arbiter`` imports."""

import importlib
import sys

_arbiter_pkg = importlib.import_module("self_fixing_engineer.arbiter")
sys.modules[__name__] = _arbiter_pkg
