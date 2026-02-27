# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_fabric_clients.py

Production Hyperledger Fabric DLT client.

Drop-in replacement for the dev file-backed stub used when this module cannot
be imported.  Requires the ``hfc`` package (``pip install hfc``) **and** a
valid Fabric connection profile JSON pointed to by the ``FABRIC_NETWORK_PROFILE``
environment variable (or passed via the *config* dict).

If ``hfc`` is not installed the class raises ``ImportError`` at construction
time so the fallback in ``dlt_backend.py`` takes over gracefully.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from hfc.fabric import Client as FabricClient
    from hfc.fabric.peer import Peer
    from hfc.fabric.user import create_user

    _HFC_AVAILABLE = True
except ImportError:
    _HFC_AVAILABLE = False


class FabricClientWrapper:
    """Production Hyperledger Fabric ledger client.

    Wraps the ``hfc`` Python SDK to write and read versioned checkpoint
    records on a Fabric channel / chaincode pair.  Off-chain payloads are
    stored via the companion *off_chain_client* (e.g. ``S3OffChainClient``)
    and only the content hash / reference is committed to the ledger.

    Configuration keys (passed as *config* dict):
        channel_name       (str, required)  Fabric channel name.
        chaincode_name     (str, required)  Chaincode / smart-contract name.
        org_name           (str, required)  MSP organisation name.
        user_name          (str, required)  Fabric user identity name.
        network_profile    (str, required)  Path to Fabric connection JSON.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        off_chain_client: Any,
    ) -> None:
        if not _HFC_AVAILABLE:
            raise ImportError(
                "hfc is required for production FabricClientWrapper. "
                "Install it with: pip install hfc"
            )

        self._channel_name: str = config["channel_name"]
        self._chaincode_name: str = config["chaincode_name"]
        self._org_name: str = config["org_name"]
        self._user_name: str = config["user_name"]
        self._network_profile: str = config["network_profile"]
        self.off_chain_client = off_chain_client

        # Fabric client is created lazily inside asyncio.to_thread to avoid
        # blocking the event loop during __init__.
        self._fabric_client: Optional[FabricClient] = None
        logger.info(
            "FabricClientWrapper initialised (channel=%r, cc=%r, org=%r).",
            self._channel_name,
            self._chaincode_name,
            self._org_name,
        )

    # ------------------------------------------------------------------
    # Private helpers (run in thread)
    # ------------------------------------------------------------------

    def _get_or_create_client(self) -> FabricClient:
        if self._fabric_client is None:
            self._fabric_client = FabricClient(
                net_profile=self._network_profile,
                channel_name=self._channel_name,
            )
        return self._fabric_client

    def _invoke_sync(self, fcn: str, args: list) -> str:
        """Submit a transaction proposal and commit it synchronously."""
        client = self._get_or_create_client()
        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(
                client.chaincode_invoke(
                    requestor=client.get_user(self._org_name, self._user_name),
                    channel_name=self._channel_name,
                    peers=[],
                    args=args,
                    cc_name=self._chaincode_name,
                    fcn=fcn,
                )
            )
        finally:
            loop.close()
        return response

    def _query_sync(self, fcn: str, args: list) -> str:
        """Execute a read-only chaincode query synchronously."""
        client = self._get_or_create_client()
        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(
                client.chaincode_query(
                    requestor=client.get_user(self._org_name, self._user_name),
                    channel_name=self._channel_name,
                    peers=[],
                    args=args,
                    cc_name=self._chaincode_name,
                    fcn=fcn,
                )
            )
        finally:
            loop.close()
        return response

    # ------------------------------------------------------------------
    # Public async API (mirrors dev stub interface)
    # ------------------------------------------------------------------

    async def write_checkpoint(
        self,
        checkpoint_name: str,
        hash: str,
        prev_hash: str,
        metadata: Dict[str, Any],
        payload_blob: Any,
        correlation_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Commit a checkpoint entry to the Fabric ledger.

        Returns ``(tx_id, off_chain_id, version)``.
        """
        off_chain_id = await self.off_chain_client.save_blob(
            checkpoint_name, payload_blob, correlation_id=correlation_id
        )
        tx_id = str(uuid.uuid4())
        args = [
            checkpoint_name,
            hash,
            prev_hash,
            json.dumps(metadata),
            off_chain_id,
            tx_id,
        ]
        try:
            await asyncio.to_thread(self._invoke_sync, "WriteCheckpoint", args)
        except Exception as exc:
            logger.error(
                "FabricClientWrapper.write_checkpoint failed (name=%s, cid=%s): %s",
                checkpoint_name,
                correlation_id,
                exc,
            )
            raise

        # The chaincode is expected to maintain its own version counter; we
        # derive the version from a subsequent query to stay in sync.
        version = await self._fetch_version(checkpoint_name)
        logger.debug(
            "Checkpoint written: name=%s tx=%s off_chain=%s v=%s",
            checkpoint_name,
            tx_id,
            off_chain_id,
            version,
        )
        return tx_id, off_chain_id, version

    async def _fetch_version(self, checkpoint_name: str) -> int:
        try:
            raw = await asyncio.to_thread(
                self._query_sync, "GetVersion", [checkpoint_name]
            )
            return int(raw.strip()) if raw else 0
        except Exception:
            return 0

    async def read_checkpoint(
        self,
        name: str,
        version: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read the latest (or a specific) checkpoint from the ledger."""
        args = [name] if version is None else [name, str(version)]
        try:
            raw = await asyncio.to_thread(self._query_sync, "ReadCheckpoint", args)
        except Exception as exc:
            logger.error(
                "FabricClientWrapper.read_checkpoint failed (name=%s, cid=%s): %s",
                name,
                correlation_id,
                exc,
            )
            raise
        entry = json.loads(raw)
        payload_blob = await self.off_chain_client.get_blob(
            entry["off_chain_ref"], correlation_id=correlation_id
        )
        return {
            "metadata": entry,
            "payload_blob": payload_blob,
            "tx_id": entry.get("tx_id"),
        }

    async def get_version_tx(
        self,
        name: str,
        version: int,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.read_checkpoint(name, version, correlation_id=correlation_id)

    async def rollback_checkpoint(
        self,
        name: str,
        rollback_hash: str,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rollback to the checkpoint matching *rollback_hash* on the ledger."""
        args = [name, rollback_hash]
        try:
            raw = await asyncio.to_thread(self._invoke_sync, "RollbackCheckpoint", args)
        except Exception as exc:
            logger.error(
                "FabricClientWrapper.rollback_checkpoint failed (name=%s, cid=%s): %s",
                name,
                correlation_id,
                exc,
            )
            raise
        return json.loads(raw)

    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a status dict; queries the Fabric peer for liveness."""
        try:
            # A lightweight query that the chaincode should support.
            await asyncio.to_thread(self._query_sync, "Ping", [])
            return {"status": True, "message": "Fabric peer is reachable."}
        except Exception as exc:
            return {"status": False, "message": f"Fabric health check failed: {exc}"}

    async def close(self) -> None:
        """Release the Fabric client if one was created."""
        self._fabric_client = None
