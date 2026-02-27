# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_backend package
===================

Public surface of the DLT Backend integration plugin.

CheckpointManager
-----------------
The ``CheckpointManager`` class (``dlt_backend.py``) is the Arbiter-registered
``PluginBase`` lifecycle plugin.  It manages checkpoint persistence via a
configurable DLT backend (Fabric / EVM) and an off-chain blob store (S3 /
GCS / Azure Blob).

Off-chain & DLT clients
-----------------------
``dlt_offchain_clients`` and ``dlt_fabric_clients`` are **canonical re-export
shims**.  All production client logic lives in the shared, platform-wide
library at:

    self_fixing_engineer.simulation.plugins.dlt_clients

Importing from this package is stable — the re-export paths will not change.
"""

from .dlt_backend import CheckpointManager

try:
    from .dlt_offchain_clients import (
        S3OffChainClient,
        InMemoryOffChainClient,
    )
except ImportError:
    pass  # simulation package not installed — dev stubs used automatically

try:
    from .dlt_fabric_clients import FabricClientWrapper
except ImportError:
    pass  # simulation package not installed — dev stubs used automatically

__all__ = [
    "CheckpointManager",
    "S3OffChainClient",
    "InMemoryOffChainClient",
    "FabricClientWrapper",
]
