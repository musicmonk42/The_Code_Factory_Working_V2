# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Audit service functions."""

from typing import Any
import datetime

# In-memory store used as a stub; replace with a real database in production.
_AUDIT_LOG: list[dict] = []
_NEXT_ID: int = 1


def record_action(action: str, actor: str) -> dict:
    """Append an audit log entry and return it."""
    global _NEXT_ID
    entry = {
        "id": _NEXT_ID,
        "action": action,
        "actor": actor,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    _AUDIT_LOG.append(entry)
    _NEXT_ID += 1
    return entry


def get_audit_log() -> list[dict]:
    """Return all audit log entries."""
    return list(_AUDIT_LOG)
