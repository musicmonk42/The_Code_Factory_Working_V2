# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_offchain_clients.py  —  Canonical re-export shim
======================================================

The **single source of truth** for all off-chain blob-storage client
implementations is:

    self_fixing_engineer.simulation.plugins.dlt_clients.dlt_offchain_clients

That module provides:

* ``S3OffChainClient``         — AWS S3 via *aioboto3* (native async)
* ``GcsOffChainClient``        — Google Cloud Storage
* ``AzureBlobOffChainClient``  — Azure Blob Storage
* ``IPFSClient``               — InterPlanetary File System
* ``InMemoryOffChainClient``   — In-process ephemeral store (dev / tests)

All implementations share the platform-wide infrastructure defined in
``simulation.plugins.dlt_clients.dlt_base``:

* ``CircuitBreaker``     — shared circuit-breaker state machine
* ``async_retry``        — configurable exponential-backoff decorator
* ``AuditManager``       — HMAC-signed append-only audit trail (``AUDIT``)
* ``TRACER``             — OpenTelemetry tracer
* ``alert_operator``     — ops-alert helper
* ``SECRETS_MANAGER``    — platform secret resolution

This file is **intentionally thin**.  Adding off-chain client logic here
would duplicate code already maintained and tested in the simulation
package and would fragment the single source of truth.

Usage (unchanged from before, import paths are stable)
------------------------------------------------------
::

    from self_fixing_engineer.plugins.dlt_backend.dlt_offchain_clients import (
        S3OffChainClient,
        InMemoryOffChainClient,
    )

To add a new backend, extend the simulation package and update the
re-exports below — do **not** add a new class here.
"""

from __future__ import annotations

from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_offchain_clients import (
    AzureBlobOffChainClient,
    GcsOffChainClient,
    InMemoryOffChainClient,
    IPFSClient,
    S3OffChainClient,
)

# Re-export shared config models used by dlt_backend.py's initialize_dlt_backend()
try:
    from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_offchain_clients import (
        S3Config,
        GCSConfig,
        AzureBlobConfig,
        InMemoryConfig,
    )
except ImportError:
    # Older versions of the simulation package may not expose config models at this path.
    pass

__all__ = [
    "S3OffChainClient",
    "GcsOffChainClient",
    "AzureBlobOffChainClient",
    "IPFSClient",
    "InMemoryOffChainClient",
]
