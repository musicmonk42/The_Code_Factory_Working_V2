# -*- coding: utf-8 -*-
"""
DLT E2E (mocks-only) – production-minded and offline-safe.

Goals:
  • Exercise end-to-end checkpoint semantics without real chains:
      - write_checkpoint(name, hash, prevHash, metadata, offChainRef) -> version
      - read_checkpoint(name) -> {hash, prevHash, metadata, offChainRef, version}
      - rollback_checkpoint(name, targetHash) -> checkpoint dict
  • Verify audit logging (JSONL) with optional HMAC signing via env DLT_AUDIT_HMAC_KEY.
  • Avoid importing heavy/broken client modules (web3/hfc/corda), so this always collects.

This does NOT test vendor SDK wiring (covered by client-specific tests).
It guarantees the contract & invariants your real clients must satisfy.

Usage: included in full suite; no external deps; runs on Windows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import pytest

# -----------------------------
# Minimal mock "DLT client"
# -----------------------------


@dataclass
class Checkpoint:
    hash: str
    prevHash: Optional[str]
    metadata: str
    offChainRef: str
    version: int


class MockDLTClient:
    """
    A deterministic, in-memory DLT that enforces simple checkpoint rules:
      - First write must have prevHash=None (or ""), or it must match the current tip's hash.
      - Version increments by 1 per write.
      - Rollback trims the chain to the target hash (inclusive).
    Audit events are written to JSONL; if DLT_AUDIT_HMAC_KEY is set, events are HMAC-signed.
    """

    def __init__(self, network_label: str, audit_path: Optional[str] = None) -> None:
        self.network_label = network_label
        self._chains: Dict[str, List[Checkpoint]] = {}
        self._audit_path = audit_path

    # ---- audit helpers ----
    def _audit(self, event: Dict) -> None:
        if not self._audit_path:
            return
        payload = {
            "ts": int(time.time() * 1000),
            "network": self.network_label,
            **event,
        }
        key = os.getenv("DLT_AUDIT_HMAC_KEY")
        if key:
            mac = hmac.new(
                key.encode("utf-8"),
                json.dumps(payload, sort_keys=True).encode("utf-8"),
                hashlib.sha256,
            )
            payload["hmac_sha256"] = mac.hexdigest()

        with open(self._audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")

    # ---- API under test ----
    def write_checkpoint(
        self,
        name: str,
        hash: str,
        prevHash: Optional[str],
        metadata: str,
        offChainRef: str,
    ) -> int:
        chain = self._chains.setdefault(name, [])
        if chain:
            tip = chain[-1]
            # Accept explicit matching prevHash or allow "" / None as "append to tip"
            if prevHash not in (None, "", tip.hash):
                raise ValueError(
                    f"prevHash mismatch: expected {tip.hash!r}, got {prevHash!r}"
                )
            version = tip.version + 1
            new_prev = tip.hash
        else:
            # genesis
            if prevHash not in (None, ""):
                raise ValueError(f"genesis prevHash must be None/''; got {prevHash!r}")
            version = 1
            new_prev = None

        cp = Checkpoint(
            hash=hash,
            prevHash=new_prev,
            metadata=metadata,
            offChainRef=offChainRef,
            version=version,
        )
        chain.append(cp)
        self._audit(
            {
                "op": "write",
                "name": name,
                "hash": hash,
                "prevHash": new_prev,
                "version": version,
            }
        )
        return version

    def read_checkpoint(self, name: str) -> Dict:
        chain = self._chains.get(name, [])
        if not chain:
            raise KeyError(f"no checkpoint for {name!r}")
        tip = chain[-1]
        out = {
            "hash": tip.hash,
            "prevHash": tip.prevHash,
            "metadata": tip.metadata,
            "offChainRef": tip.offChainRef,
            "version": tip.version,
        }
        self._audit(
            {"op": "read", "name": name, "hash": tip.hash, "version": tip.version}
        )
        return out

    def rollback_checkpoint(self, name: str, targetHash: str) -> Dict:
        chain = self._chains.get(name, [])
        if not chain:
            raise KeyError(f"no checkpoint for {name!r}")
        # find target from the end for speed
        idx = next(
            (i for i in range(len(chain) - 1, -1, -1) if chain[i].hash == targetHash),
            None,
        )
        if idx is None:
            raise ValueError(
                f"target hash {targetHash!r} not found in chain for {name!r}"
            )
        # trim to target
        chain[:] = chain[: idx + 1]
        tip = chain[-1]
        self._audit(
            {"op": "rollback", "name": name, "hash": tip.hash, "version": tip.version}
        )
        return {
            "hash": tip.hash,
            "prevHash": tip.prevHash,
            "metadata": tip.metadata,
            "offChainRef": tip.offChainRef,
            "version": tip.version,
        }


# -----------------------------
# Fixtures
# -----------------------------


@pytest.fixture(params=["evm", "quorum", "fabric", "corda"])
def dlt_client(tmp_path, request):
    """
    Provide a per-test client instance with independent audit logs.
    Parametrized over representative network labels to emulate multiple backends.
    """
    audit_path = tmp_path / f"audit_{request.param}.jsonl"
    client = MockDLTClient(network_label=request.param, audit_path=str(audit_path))
    return client


# -----------------------------
# Tests
# -----------------------------


def test_write_read_rollback_roundtrip(dlt_client: MockDLTClient):
    # genesis
    v1 = dlt_client.write_checkpoint(
        name="orders",
        hash="h1",
        prevHash=None,
        metadata="m1",
        offChainRef="s3://bucket/1",
    )
    assert v1 == 1

    latest = dlt_client.read_checkpoint("orders")
    assert latest["hash"] == "h1"
    assert latest["prevHash"] is None
    assert latest["version"] == 1

    # append (prevHash may be omitted/None/"", treated as tip)
    v2 = dlt_client.write_checkpoint(
        name="orders",
        hash="h2",
        prevHash="h1",
        metadata="m2",
        offChainRef="s3://bucket/2",
    )
    assert v2 == 2
    assert dlt_client.read_checkpoint("orders")["hash"] == "h2"

    # rollback to v1
    rolled = dlt_client.rollback_checkpoint("orders", targetHash="h1")
    assert rolled["hash"] == "h1"
    assert rolled["version"] == 1
    assert dlt_client.read_checkpoint("orders")["hash"] == "h1"


def test_isolation_between_streams(dlt_client: MockDLTClient):
    # Stream A
    dlt_client.write_checkpoint("A", "a1", None, "meta", "off")
    dlt_client.write_checkpoint("A", "a2", "a1", "meta", "off")
    # Stream B
    dlt_client.write_checkpoint("B", "b1", None, "meta", "off")

    assert dlt_client.read_checkpoint("A")["hash"] == "a2"
    assert dlt_client.read_checkpoint("B")["hash"] == "b1"

    # Rollback B; A must remain intact
    dlt_client.rollback_checkpoint("B", "b1")
    assert dlt_client.read_checkpoint("A")["hash"] == "a2"
    assert dlt_client.read_checkpoint("B")["hash"] == "b1"


def test_prevhash_mismatch_rejected(dlt_client: MockDLTClient):
    dlt_client.write_checkpoint("X", "x1", None, "m", "off")
    # Wrong prevHash vs tip
    with pytest.raises(ValueError):
        dlt_client.write_checkpoint("X", "x2", "WRONG", "m", "off")


def test_genesis_prevhash_rules(dlt_client: MockDLTClient):
    # genesis must be None or ""
    with pytest.raises(ValueError):
        dlt_client.write_checkpoint("gen", "g1", "not-none", "m", "off")
    # valid genesis
    dlt_client.write_checkpoint("gen", "g1", None, "m", "off")
    assert dlt_client.read_checkpoint("gen")["version"] == 1


def test_rollback_unknown_hash_errors(dlt_client: MockDLTClient):
    dlt_client.write_checkpoint("Y", "y1", None, "m", "off")
    with pytest.raises(ValueError):
        dlt_client.rollback_checkpoint("Y", "nope")


def test_audit_log_file_and_hmac(monkeypatch, tmp_path):
    # Force HMAC
    monkeypatch.setenv("DLT_AUDIT_HMAC_KEY", "testkey123")
    audit_path = tmp_path / "audit.jsonl"
    client = MockDLTClient("evm", audit_path=str(audit_path))

    client.write_checkpoint("Z", "z1", None, "m", "off")
    client.read_checkpoint("Z")
    client.write_checkpoint("Z", "z2", "z1", "m", "off")
    client.rollback_checkpoint("Z", "z1")

    assert audit_path.exists()
    lines = [
        json.loads(x)
        for x in audit_path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert {e["op"] for e in lines} == {"write", "read", "rollback"}
    # presence and basic shape of HMAC
    assert all("hmac_sha256" in e and len(e["hmac_sha256"]) == 64 for e in lines)
