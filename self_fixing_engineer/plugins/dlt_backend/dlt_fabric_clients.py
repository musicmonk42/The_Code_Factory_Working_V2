# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_fabric_clients.py  —  Canonical re-export shim
====================================================

The **single source of truth** for the Hyperledger Fabric DLT client is:

    self_fixing_engineer.simulation.plugins.dlt_clients.dlt_fabric_clients

That module provides ``FabricClientWrapper``, a production-hardened async
Fabric client that:

* Connects to Fabric peers over **aiohttp** (native async HTTP/REST).
* Validates all configuration with **Pydantic** (``FabricConfig``).
* Uses the shared ``CircuitBreaker`` and ``async_retry`` from ``dlt_base``.
* Integrates with the platform ``AuditManager`` (HMAC-signed audit trail).
* Emits **OpenTelemetry** spans and **Prometheus** metrics via ``dlt_base``.
* Resolves credentials through the platform ``SECRETS_MANAGER``.

Public API (matches ``CheckpointManager``'s expectations)
----------------------------------------------------------
* ``write_checkpoint(name, hash, prev_hash, metadata, payload, cid)``
* ``read_checkpoint(name, version, cid)``
* ``get_version_tx(name, version, cid)``
* ``rollback_checkpoint(name, rollback_hash, cid)``
* ``health_check(cid)``
* ``close()``

This file is **intentionally thin**.  All production logic lives in the
simulation package to guarantee a single source of truth and prevent
divergence between the DLT backend plugin and the simulation runner.

Usage (unchanged from before, import paths are stable)
------------------------------------------------------
::

    from self_fixing_engineer.plugins.dlt_backend.dlt_fabric_clients import (
        FabricClientWrapper,
    )

To extend Fabric support, modify the simulation package directly.
"""

from __future__ import annotations

from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_fabric_clients import (
    FabricClientWrapper,
)

# Re-export the Pydantic config model so callers can type-check configs.
try:
    from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_fabric_clients import (
        FabricConfig,
    )
except ImportError:
    # Older versions of the simulation package may not expose FabricConfig here.
    pass

# Re-export the typed exception hierarchy from dlt_base for callers that
# need to catch Fabric-specific errors without importing dlt_base directly.
from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_base import (
    DLTClientAuthError,
    DLTClientCircuitBreakerError,
    DLTClientConfigurationError,
    DLTClientConnectivityError,
    DLTClientError,
    DLTClientQueryError,
    DLTClientTimeoutError,
    DLTClientTransactionError,
    DLTClientValidationError,
)

__all__ = [
    "FabricClientWrapper",
    # Exceptions
    "DLTClientError",
    "DLTClientConfigurationError",
    "DLTClientConnectivityError",
    "DLTClientAuthError",
    "DLTClientTransactionError",
    "DLTClientQueryError",
    "DLTClientTimeoutError",
    "DLTClientValidationError",
    "DLTClientCircuitBreakerError",
]
