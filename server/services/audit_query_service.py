"""Audit log querying and periodic flush management.

Extracted from ``OmniCoreService`` during Phase 2 decomposition.  Covers
audit trail retrieval (database + file-based JSONL), and periodic audit
flush lifecycle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class AuditQueryService:
    """Service for querying and managing audit logs."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # -- Audit trail ---------------------------------------------------------

    async def get_audit_trail(
        self, job_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get audit trail for a job.

        Checks the database first, then the in-memory buffer, and finally
        falls back to a synthetic entry.
        """
        logger.debug(f"Fetching audit trail for job {job_id}")

        audit_client = self._ctx.audit_client
        if audit_client and self._ctx.omnicore_components_available.get("audit"):
            try:
                if hasattr(audit_client, "db") and audit_client.db:
                    try:
                        from sqlalchemy import select, desc
                        from omnicore_engine.database import AuditRecord

                        async with audit_client.db.async_session() as session:
                            stmt = (
                                select(AuditRecord)
                                .where(AuditRecord.name.like(f"%{job_id}%"))
                                .order_by(desc(AuditRecord.timestamp))
                                .limit(limit)
                            )
                            result = await session.execute(stmt)
                            records = result.scalars().all()

                            audit_entries = []
                            for record in records:
                                audit_entries.append({
                                    "timestamp": record.timestamp.isoformat() if hasattr(record.timestamp, "isoformat") else str(record.timestamp),
                                    "action": record.kind,
                                    "name": record.name,
                                    "job_id": job_id,
                                    "module": "omnicore_engine.audit",
                                    "detail": record.detail if hasattr(record, "detail") else {},
                                })

                            logger.info(f"Retrieved {len(audit_entries)} audit entries for job {job_id}")
                            if audit_entries:
                                return audit_entries

                    except ImportError as import_err:
                        logger.debug(f"Could not import audit database models: {import_err}")
                    except Exception as db_err:
                        logger.warning(f"Database query failed: {db_err}")

                # Fallback: in-memory buffer
                if hasattr(audit_client, "buffer") and audit_client.buffer:
                    matching = []
                    for entry in audit_client.buffer:
                        if isinstance(entry, dict) and job_id in entry.get("name", ""):
                            matching.append({
                                "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                                "action": entry.get("kind", "unknown"),
                                "name": entry.get("name", ""),
                                "job_id": job_id,
                                "module": "omnicore_engine.audit",
                                "detail": entry.get("detail", {}),
                            })
                    if matching:
                        logger.info(f"Retrieved {len(matching)} buffered audit entries for job {job_id}")
                        return matching[:limit]

            except Exception as e:
                logger.error(f"Error querying audit trail: {e}", exc_info=True)

        logger.debug(f"Using fallback audit trail for job {job_id}")
        return [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "job_created",
                "job_id": job_id,
                "module": "omnicore_engine",
                "source": "fallback",
            }
        ]

    # -- File-based JSONL log reader -----------------------------------------

    async def read_audit_logs_from_files(
        self,
        log_paths: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Read and filter JSONL audit log entries from file paths."""
        start_time: Optional[str] = payload.get("start_time")
        end_time: Optional[str] = payload.get("end_time")
        event_type: Optional[str] = payload.get("event_type")
        filter_job_id: Optional[str] = payload.get("job_id")
        filter_module: Optional[str] = payload.get("module")
        limit: int = int(payload.get("limit", 100))

        logs: List[Dict[str, Any]] = []

        for path_str in log_paths:
            path = Path(path_str)
            files_to_read: List[Path] = []
            if path.is_dir():
                files_to_read = sorted(path.glob("*.jsonl"))
            elif path.is_file():
                files_to_read = [path]

            for log_file in files_to_read:
                try:
                    async with aiofiles.open(log_file, "r", encoding="utf-8") as fh:
                        async for raw_line in fh:
                            line = raw_line.strip()
                            if not line:
                                continue
                            try:
                                entry: Dict[str, Any] = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            ts = entry.get("timestamp") or entry.get("ts") or ""
                            if start_time and ts and ts < start_time:
                                continue
                            if end_time and ts and ts > end_time:
                                continue
                            if event_type:
                                etype = entry.get("event_type") or entry.get("event") or ""
                                if event_type not in etype:
                                    continue
                            if filter_job_id:
                                ejid = str(entry.get("job_id") or "")
                                if filter_job_id not in ejid:
                                    continue
                            if filter_module:
                                emod = entry.get("module")
                                if emod is not None and filter_module not in emod:
                                    continue

                            logs.append(entry)
                            if len(logs) >= limit:
                                break
                    if len(logs) >= limit:
                        break
                except OSError as exc:
                    logger.debug("Could not read audit log file %s: %s", log_file, exc)

            if len(logs) >= limit:
                break

        return {"logs": logs[:limit]}

    # -- Periodic flush lifecycle --------------------------------------------

    async def start_periodic_audit_flush(self) -> bool:
        """Start periodic audit flush task from an async context."""
        audit_client = self._ctx.audit_client
        if audit_client and self._ctx.omnicore_components_available.get("audit"):
            try:
                await audit_client.start_periodic_flush()
                logger.info("Periodic audit flush initialized via AuditQueryService")
                return True
            except Exception as e:
                logger.warning(f"Failed to start periodic audit flush: {e}", exc_info=True)
                return False
        logger.debug("Audit client not available, skipping periodic flush initialization")
        return False


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_audit_query_service_instance: Optional[AuditQueryService] = None


def get_audit_query_service(ctx: Optional[ServiceContext] = None) -> AuditQueryService:
    """Return the singleton ``AuditQueryService``."""
    global _audit_query_service_instance
    if _audit_query_service_instance is None:
        if ctx is None:
            raise RuntimeError("AuditQueryService not initialised -- pass a ServiceContext on first call")
        _audit_query_service_instance = AuditQueryService(ctx)
    return _audit_query_service_instance
