# Copyright 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pipeline orchestrator -- sequences the full generation pipeline.

Extracts ``_run_full_pipeline``, ``_dispatch_generator_action``,
``_finalize_successful_job``, ``_finalize_failed_job``, and
``_create_artifact_zip`` from ``OmniCoreService``.

``_run_full_pipeline`` is ~2 566 lines and requires a dedicated
decomposition pass (Phase 4+).  All methods are delegation stubs
until Phase 5 inlines the real implementations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.services.service_context import ServiceContext
from server.services.pipeline.codegen_service import CodegenService
from server.services.pipeline.deploy_service import DeployService
from server.services.pipeline.quality_service import QualityService

logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    """Composes sub-services and sequences the full generation pipeline.

    The orchestrator owns the lifecycle of a generation job from initial
    dispatch through codegen, testing, deployment, documentation,
    critique, and final artefact packaging.

    Args:
        ctx: Shared service context providing LLM config, agent
            references, and job output paths.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx
        self._codegen = CodegenService(ctx)
        self._deploy = DeployService(ctx)
        self._quality = QualityService(ctx)

    # ------------------------------------------------------------------
    # Sub-service accessors
    # ------------------------------------------------------------------

    @property
    def codegen(self) -> CodegenService:
        """Return the codegen sub-service."""
        return self._codegen

    @property
    def deploy(self) -> DeployService:
        """Return the deploy sub-service."""
        return self._deploy

    @property
    def quality(self) -> QualityService:
        """Return the quality sub-service."""
        return self._quality

    # ------------------------------------------------------------------
    # Pipeline dispatch
    # ------------------------------------------------------------------

    async def dispatch_generator_action(
        self, job_id: str, action: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Dispatch a generator action to the appropriate sub-service.

        Delegation stub for ``OmniCoreService._dispatch_generator_action``
        (~157 lines).  Implements circuit-breaker pattern and maps action
        strings (``run_codegen``, ``run_testgen``, etc.) to sub-services.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._dispatch_generator_action and wire to
        #   self._codegen / self._deploy / self._quality directly.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(
            omnicore, "_dispatch_generator_action"
        ):
            return await omnicore._dispatch_generator_action(
                job_id, action, payload
            )

        logger.error(
            "[PipelineOrchestrator] Cannot delegate dispatch_generator_action",
            extra={"job_id": job_id, "action": action},
        )
        return {
            "status": "error",
            "message": "PipelineOrchestrator delegation failed",
            "job_id": job_id,
            "action": action,
        }

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    async def run_full_pipeline(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the complete generation pipeline.

        Delegation stub for ``OmniCoreService._run_full_pipeline``
        (~2 566 lines).  Sequences: spec processing -> codegen -> SFE
        analysis -> testgen -> deploy_all -> deploy validation -> docgen
        -> critique -> final validation and artefact packaging.

        TODO(Phase 4/5): Decompose into per-stage methods and inline.
        """
        # TODO(Phase 4/5): Decompose _run_full_pipeline (~2 566 lines)
        #   into stage-specific methods and inline here.  Candidate
        #   breakdown:
        #     _stage_spec_processing(job_id, payload) -> dict
        #     _stage_codegen(job_id, payload) -> dict
        #     _stage_sfe_analysis(job_id, output_path) -> dict
        #     _stage_testgen(job_id, output_path, ...) -> dict
        #     _stage_deploy(job_id, output_path, ...) -> dict
        #     _stage_docgen(job_id, output_path, ...) -> dict
        #     _stage_critique(job_id, output_path, ...) -> dict
        #     _stage_final_validation(job_id, output_path) -> dict
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_full_pipeline"):
            return await omnicore._run_full_pipeline(job_id, payload)

        logger.error(
            "[PipelineOrchestrator] Cannot delegate run_full_pipeline",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "PipelineOrchestrator delegation failed",
            "job_id": job_id,
        }

    # ------------------------------------------------------------------
    # Job finalisation
    # ------------------------------------------------------------------

    async def finalize_successful_job(
        self,
        job_id: str,
        output_path: Optional[str],
        stages_completed: List[str],
    ) -> None:
        """Finalise a successfully completed job.

        Delegation stub -- full implementation (~140 lines) lives in
        ``OmniCoreService._finalize_successful_job``.

        Args:
            job_id: Unique job identifier.
            output_path: Path to the generated output directory.
            stages_completed: List of pipeline stages that succeeded.
        """
        # TODO(Phase 5): Inline implementation.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(
            omnicore, "_finalize_successful_job"
        ):
            await omnicore._finalize_successful_job(
                job_id, output_path, stages_completed
            )
            return

        logger.error(
            "[PipelineOrchestrator] Cannot delegate finalize_successful_job",
            extra={"job_id": job_id},
        )

    async def finalize_failed_job(
        self, job_id: str, error: str
    ) -> None:
        """Finalise a failed job.

        Delegation stub -- full implementation (~25 lines) lives in
        ``OmniCoreService._finalize_failed_job``.

        Args:
            job_id: Unique job identifier.
            error: Human-readable error description.
        """
        # TODO(Phase 5): Inline implementation.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_finalize_failed_job"):
            await omnicore._finalize_failed_job(job_id, error)
            return

        logger.error(
            "[PipelineOrchestrator] Cannot delegate finalize_failed_job",
            extra={"job_id": job_id},
        )

    async def create_artifact_zip(
        self,
        files: List[Path],
        zip_path: Path,
        base_dir: Path,
    ) -> None:
        """Bundle outputs into a deterministic ZIP archive.

        Delegation stub -- full implementation (~33 lines) lives in
        ``OmniCoreService._create_artifact_zip``.

        Args:
            files: Pre-scanned file list (used for logging only).
            zip_path: Destination path for the ZIP file.
            base_dir: Root directory whose contents are archived.
        """
        # TODO(Phase 5): Inline implementation.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_create_artifact_zip"):
            await omnicore._create_artifact_zip(files, zip_path, base_dir)
            return

        logger.error("[PipelineOrchestrator] Cannot delegate create_artifact_zip")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_orchestrator_instance: Optional[PipelineOrchestrator] = None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

def get_pipeline_orchestrator(
    ctx: Optional[ServiceContext] = None,
) -> Optional[PipelineOrchestrator]:
    """Return (or create) the module-level ``PipelineOrchestrator`` singleton.

    Args:
        ctx: If provided and no singleton exists yet, one is created
            with this context.  Subsequent calls ignore *ctx*.

    Returns:
        The singleton ``PipelineOrchestrator``, or ``None`` if *ctx*
        was never supplied.
    """
    global _orchestrator_instance
    if _orchestrator_instance is None and ctx is not None:
        _orchestrator_instance = PipelineOrchestrator(ctx)
        logger.info("PipelineOrchestrator singleton created")
    return _orchestrator_instance
