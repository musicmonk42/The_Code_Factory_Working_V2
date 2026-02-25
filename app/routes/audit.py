# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Audit log routes."""

from fastapi import APIRouter

from app.schemas import AuditLogEntry
from app.services import audit as audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/", response_model=list[AuditLogEntry])
async def get_audit_log() -> list[AuditLogEntry]:
    """Return the full audit log."""
    return [AuditLogEntry(**e) for e in audit_service.get_audit_log()]


@router.post("/", response_model=AuditLogEntry, status_code=201)
async def record_audit_action(action: str, actor: str) -> AuditLogEntry:
    """Append an entry to the audit log."""
    entry = audit_service.record_action(action=action, actor=actor)
    return AuditLogEntry(**entry)
