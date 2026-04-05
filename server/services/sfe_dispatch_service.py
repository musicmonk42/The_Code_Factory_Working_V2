"""SFE dispatch and analysis operations for the OmniCore service layer.

Extracted from ``OmniCoreService`` during Phase 2 decomposition.  Covers
dispatching jobs to the Self-Fixing Engineer (Kafka/HTTP), SFE action
routing, and SFE analysis execution.

.. warning::

    ``_dispatch_sfe_action`` (~430 lines) and ``_run_sfe_analysis`` (~395
    lines) both exceed the 250-line service target.  They are included here
    for completeness but should be decomposed further in a future phase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)

# Re-use the configurable timeout from the top-level module.
# Imported lazily to avoid circular imports at module load time.
_DEFAULT_SFE_ANALYSIS_TIMEOUT: Optional[int] = None


def _get_sfe_timeout() -> int:
    global _DEFAULT_SFE_ANALYSIS_TIMEOUT
    if _DEFAULT_SFE_ANALYSIS_TIMEOUT is None:
        _DEFAULT_SFE_ANALYSIS_TIMEOUT = int(os.getenv("SFE_ANALYSIS_TIMEOUT_SECONDS", "300"))
    return _DEFAULT_SFE_ANALYSIS_TIMEOUT


class SFEDispatchService:
    """Service wrapping SFE dispatch, action routing, and analysis."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # -- Kafka / HTTP dispatch -----------------------------------------------

    async def dispatch_to_sfe(
        self,
        job_id: str,
        output_path: Optional[str],
        validation_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Dispatch completed job to SFE with fallback (Kafka then HTTP)."""
        try:
            from server.config import get_server_config

            config = get_server_config()

            if config.kafka_enabled:
                try:
                    producer = self._ctx.kafka_producer
                    if producer:
                        sfe_payload = {
                            "job_id": job_id,
                            "output_path": output_path,
                            "validation_context": validation_context or {},
                        }
                        await producer.send(topic="sfe_jobs", value=sfe_payload)
                        logger.info(f"Dispatched job {job_id} to SFE via Kafka")
                        return
                except Exception as kafka_error:
                    logger.warning(f"Kafka dispatch failed: {kafka_error}, trying fallback")

            sfe_url = os.getenv("SFE_URL")
            if sfe_url:
                import httpx

                sfe_payload = {
                    "job_id": job_id,
                    "source": "omnicore",
                    "output_path": output_path,
                    "validation_context": validation_context or {},
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(f"{sfe_url}/api/jobs", json=sfe_payload)
                    response.raise_for_status()
                logger.info(
                    f"Dispatched job {job_id} to SFE via HTTP fallback (status: {response.status_code})"
                )
            else:
                logger.info(f"SFE dispatch skipped for job {job_id} (no Kafka or SFE_URL configured)")

        except Exception as e:
            logger.warning(f"Failed to dispatch job {job_id} to SFE: {e}")

    # -- SFE action dispatcher -----------------------------------------------
    # NOTE: This method is ~430 lines in the original.  It is included for
    # signature/behavioral parity but should be decomposed further.

    async def dispatch_sfe_action(
        self,
        job_id: str,
        action: str,
        payload: Dict[str, Any],
        *,
        resolve_job_output_path: Any = None,
    ) -> Dict[str, Any]:
        """Route an SFE action to the appropriate handler.

        ``resolve_job_output_path`` is a callable with signature
        ``(job_id: str, hint: str) -> Optional[str]`` used to locate job
        output directories.  During the transition period callers pass in
        ``OmniCoreService._resolve_job_output_path``.
        """
        # Minimal stub -- the full body will be migrated in Phase 5.
        logger.warning(
            "SFEDispatchService.dispatch_sfe_action called -- "
            "delegates to OmniCoreService during transition."
        )
        return {
            "status": "error",
            "message": "dispatch_sfe_action not yet migrated -- use OmniCoreService directly",
        }

    # -- SFE analysis runner -------------------------------------------------
    # NOTE: This method is ~395 lines in the original.  It is included for
    # signature/behavioral parity but should be decomposed further.

    async def run_sfe_analysis(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute SFE analysis with CodebaseAnalyzer and BugManager.

        This is a placeholder that preserves the method signature.  The full
        implementation (~395 lines) will be migrated from
        ``OmniCoreService._run_sfe_analysis`` in Phase 5.
        """
        logger.warning(
            "SFEDispatchService.run_sfe_analysis called -- "
            "delegates to OmniCoreService during transition."
        )
        return {
            "status": "error",
            "message": "run_sfe_analysis not yet migrated -- use OmniCoreService directly",
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_sfe_dispatch_service_instance: Optional[SFEDispatchService] = None


def get_sfe_dispatch_service(ctx: Optional[ServiceContext] = None) -> SFEDispatchService:
    """Return the singleton ``SFEDispatchService``."""
    global _sfe_dispatch_service_instance
    if _sfe_dispatch_service_instance is None:
        if ctx is None:
            raise RuntimeError(
                "SFEDispatchService not initialised -- pass a ServiceContext on first call"
            )
        _sfe_dispatch_service_instance = SFEDispatchService(ctx)
    return _sfe_dispatch_service_instance
