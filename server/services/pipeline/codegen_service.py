# Copyright 2025 Novatrax Labs LLC. All Rights Reserved.

"""Code generation pipeline sub-service.

Extracts the ``_run_codegen`` and ``_execute_codegen`` methods from
``OmniCoreService`` into a standalone service that can be composed by the
:class:`PipelineOrchestrator`.

The public entry point is :meth:`CodegenService.run_codegen`, which
validates agent/LLM availability, sets up tracing, and delegates to the
inner ``_execute_codegen`` closure for the actual generation + file
materialisation logic.

.. note::

   Phase 3 extraction -- the original methods (lines 2040-3261 of
   ``omnicore_service.py``, ~1 220 lines) are too large to inline here
   within the 250-line budget.  This file therefore provides a thin
   delegation wrapper that calls the *original* methods on the
   ``OmniCoreService`` instance held in the ``ServiceContext``.  Phase 5
   will move the implementation here and leave stubs on the god-class.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class CodegenService:
    """Orchestrates LLM-driven code generation.

    Responsibilities:
        - Validate that the codegen agent is loaded and an LLM provider
          is configured.
        - Invoke the codegen agent with structured requirements.
        - Post-process results (JSON unwrapping, import auto-fix,
          compliance scanning, Pydantic V2 migration).
        - Materialise the generated file map to disk.

    Args:
        ctx: Shared service context providing LLM config, agent
            references, and job output paths.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_codegen(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute code generation for *job_id*.

        This is a **delegation stub**.  The full implementation currently
        lives in ``OmniCoreService._run_codegen`` (~1 220 lines including
        the nested ``_execute_codegen`` closure).  Moving that logic here
        is deferred to Phase 5 of the decomposition to avoid touching the
        god-class in Phase 3.

        Args:
            job_id: Unique job identifier.
            payload: Action-specific parameters including ``requirements``,
                ``language``, ``framework``, ``output_dir``, etc.

        Returns:
            A dict with at minimum ``status`` (``"completed"`` or
            ``"error"``) and, on success, ``generated_files``,
            ``output_path``, ``files_count``, and ``total_bytes_written``.
        """
        # TODO(Phase 5): Inline the full implementation from
        #   OmniCoreService._run_codegen / _execute_codegen here and
        #   replace the original methods with thin stubs that delegate
        #   to this service.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_codegen"):
            return await omnicore._run_codegen(job_id, payload)

        logger.error(
            "[CodegenService] Cannot delegate -- OmniCoreService reference "
            "not available in ServiceContext.omnicore_engine",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": (
                "CodegenService delegation failed: OmniCoreService not "
                "available in ServiceContext"
            ),
            "job_id": job_id,
        }

    # ------------------------------------------------------------------
    # Future: private helpers extracted from _execute_codegen
    # ------------------------------------------------------------------
    # The following methods will be populated in Phase 5:
    #
    # async def _validate_agent_availability(self, job_id) -> Optional[dict]
    # async def _resolve_llm_provider(self, job_id) -> Optional[str]
    # async def _build_requirements_dict(self, payload, job_id) -> dict
    # async def _invoke_codegen_agent(self, requirements_dict, ...) -> dict
    # async def _post_process_result(self, result, ...) -> dict
    # async def _materialise_files(self, result, output_path, ...) -> dict
