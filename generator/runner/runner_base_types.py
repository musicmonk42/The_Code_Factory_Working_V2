# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
runner_base_types — leaf module for the runner subsystem.

This module intentionally has **zero intra-package imports**.  It contains only
stdlib-dependent constants and type aliases that multiple runner modules need.
By importing shared values from here instead of from each other, every module
in the runner sub-system can avoid the circular import chains that previously
required scattered ``# FIX`` workarounds.

Dependency graph position
-------------------------
::

    stdlib
      └── runner_base_types        ← this file (leaf, no intra-package imports)
            ├── runner_security_utils
            ├── runner_errors
            ├── runner_config
            ├── runner_metrics
            └── runner_logging

Any runner module may import from ``runner_base_types`` without risk of a cycle.
No module in the runner package may be imported *by* ``runner_base_types``.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# TESTING sentinel
# ---------------------------------------------------------------------------
# Single, authoritative definition of the testing-mode flag.  Import this
# from here rather than computing it independently in each module so that the
# check is consistent and only evaluated once.

TESTING: bool = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)

# ---------------------------------------------------------------------------
# Sentinel values
# ---------------------------------------------------------------------------

#: Returned by operations that produce no meaningful result (e.g. a disabled
#: code path) so callers can distinguish "not run" from ``None``.
SENTINEL_NOT_RUN: object = object()

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: A JSON-serialisable dictionary — used in structured logging and API
#: response helpers throughout the runner subsystem.
JsonDict = Dict[str, Any]

#: Optional string — ubiquitous in runner configuration objects.
OptStr = Optional[str]
