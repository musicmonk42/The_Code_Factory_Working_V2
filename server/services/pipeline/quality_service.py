# Copyright 2025 Novatrax Labs LLC. All Rights Reserved.

"""Quality assurance pipeline sub-service.

Extracts the quality-related pipeline methods from ``OmniCoreService``:

- ``_run_testgen`` -- generates unit tests for the produced code using
  the testgen agent.  ~240 lines in the original.
- ``_run_docgen`` -- generates documentation (API, README, developer
  guides) and optionally runs sphinx-build.  ~351 lines.
- ``_run_critique`` -- runs security/quality scanning with the critique
  agent and writes a JSON report.  ~241 lines.

.. note::

   Phase 3 extraction -- the combined original methods total ~832 lines.
   This file provides delegation stubs that forward to the original
   ``OmniCoreService`` methods.  Phase 5 will inline the real logic and
   convert the god-class methods into thin stubs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class QualityService:
    """Runs test generation, documentation, and code critique stages.

    Each method wraps the corresponding agent call with timeout handling,
    structured error responses, and metrics/tracing integration.

    Args:
        ctx: Shared service context.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_testgen(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate unit tests for the produced code.

        Delegation stub -- full implementation (~240 lines) lives in
        ``OmniCoreService._run_testgen``.

        Args:
            job_id: Unique job identifier.
            payload: Contains ``code_path``, optional ``coverage_target``,
                ``language``, ``package_name``.

        Returns:
            Dict with ``status``, ``generated_files``, ``tests_count``,
            and ``result`` from the testgen agent.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._run_testgen.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_testgen"):
            return await omnicore._run_testgen(job_id, payload)

        logger.error(
            "[QualityService] Cannot delegate _run_testgen",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "QualityService delegation failed: OmniCoreService not available",
            "job_id": job_id,
        }

    async def run_docgen(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate documentation for the produced code.

        Delegation stub -- full implementation (~351 lines) lives in
        ``OmniCoreService._run_docgen``.

        Supports multiple doc types (``api``, ``readme``, ``developer``),
        writes output to ``docs/`` subdirectory, and optionally triggers
        a sphinx-build for HTML documentation.

        Args:
            job_id: Unique job identifier.
            payload: Contains ``code_path``, ``doc_type``, ``format``,
                and optional ``instructions``.

        Returns:
            Dict with ``status``, ``generated_docs``, ``doc_type``,
            ``format``, and ``file_count``.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._run_docgen.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_docgen"):
            return await omnicore._run_docgen(job_id, payload)

        logger.error(
            "[QualityService] Cannot delegate _run_docgen",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "QualityService delegation failed: OmniCoreService not available",
            "job_id": job_id,
        }

    async def run_critique(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run code critique and security scanning.

        Delegation stub -- full implementation (~241 lines) lives in
        ``OmniCoreService._run_critique``.

        Gathers source and test files, invokes the critique agent with
        configurable scan types (``security``, ``quality``), writes
        fixed files back to disk if ``auto_fix`` is enabled, and
        produces a JSON report in ``reports/critique_report.json``.

        Args:
            job_id: Unique job identifier.
            payload: Contains ``code_path``, ``scan_types``,
                ``auto_fix``, and optional ``test_results`` /
                ``validation_results``.

        Returns:
            Dict with ``status``, ``issues_found``, ``issues_fixed``,
            ``scan_types``, ``report_path``, and ``file_count``.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._run_critique.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_critique"):
            return await omnicore._run_critique(job_id, payload)

        logger.error(
            "[QualityService] Cannot delegate _run_critique",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "QualityService delegation failed: OmniCoreService not available",
            "job_id": job_id,
        }

    # ------------------------------------------------------------------
    # Future: private helpers (Phase 5)
    # ------------------------------------------------------------------
    # async def _gather_code_files(self, repo_path, language) -> dict
    # async def _write_test_files(self, tests, repo_path, ...) -> list
    # async def _write_documentation(self, docs_output, ...) -> list
    # async def _write_critique_report(self, result, ...) -> str
