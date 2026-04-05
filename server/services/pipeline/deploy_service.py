# Copyright 2025 Novatrax Labs LLC. All Rights Reserved.

"""Deployment artifact generation pipeline sub-service.

Extracts the deployment-related methods from ``OmniCoreService``:

- ``_run_deploy`` -- single-target deployment generation (Docker, K8s,
  Helm, Terraform, docker-compose).  ~736 lines in the original.
- ``_run_deploy_all`` -- multi-target orchestration that calls
  ``_run_deploy`` for docker, kubernetes, and helm sequentially.
- ``_execute_deploy_all_targets`` -- inner implementation with
  per-target error handling and metrics.
- ``_validate_deployment_completeness`` -- post-generation validation
  using ``DeploymentCompletenessValidator``.

.. note::

   Phase 3 extraction -- ``_run_deploy`` alone is ~736 lines, far
   exceeding the 250-line file budget.  This file provides delegation
   stubs that call the original methods on ``OmniCoreService``.  The
   full implementation will be inlined in Phase 5.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class DeployService:
    """Generates deployment artifacts for generated code.

    Supports Docker (Dockerfile, docker-compose, .dockerignore),
    Kubernetes (multi-document YAML split into k8s/ directory), and
    Helm (Chart.yaml, values.yaml, templates/).

    Args:
        ctx: Shared service context.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_deploy(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate deployment config for a single target platform.

        Delegation stub -- full implementation (~736 lines) lives in
        ``OmniCoreService._run_deploy``.

        Args:
            job_id: Unique job identifier.
            payload: Must contain ``code_path`` and ``platform``
                (``"docker"``, ``"kubernetes"``, ``"helm"``, etc.).

        Returns:
            Dict with ``status``, ``generated_files``, ``platform``,
            ``run_id``, and optional ``validations``.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._run_deploy.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_deploy"):
            return await omnicore._run_deploy(job_id, payload)

        logger.error(
            "[DeployService] Cannot delegate -- OmniCoreService reference "
            "not available in ServiceContext.omnicore_engine",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "DeployService delegation failed: OmniCoreService not available",
            "job_id": job_id,
        }

    async def run_deploy_all(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run deployment for ALL targets (docker, kubernetes, helm).

        Delegation stub -- full implementation (~252 lines) lives in
        ``OmniCoreService._run_deploy_all`` and
        ``_execute_deploy_all_targets``.

        Args:
            job_id: Unique job identifier.
            payload: Contains ``code_path`` and ``include_ci_cd`` flag.

        Returns:
            Dict with ``status``, per-target ``results``,
            ``generated_files``, ``failed_targets``, and
            ``duration_seconds``.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._run_deploy_all / _execute_deploy_all_targets.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(omnicore, "_run_deploy_all"):
            return await omnicore._run_deploy_all(job_id, payload)

        logger.error(
            "[DeployService] Cannot delegate _run_deploy_all",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "message": "DeployService delegation failed: OmniCoreService not available",
            "job_id": job_id,
        }

    async def validate_deployment_completeness(
        self, job_id: str, code_path: str
    ) -> Dict[str, Any]:
        """Validate that all required deployment files exist and are valid.

        Delegation stub -- full implementation (~177 lines) lives in
        ``OmniCoreService._validate_deployment_completeness``.

        Uses ``DeploymentCompletenessValidator`` to check file existence,
        placeholder substitution, YAML syntax, and Dockerfile instructions.

        Args:
            job_id: Unique job identifier.
            code_path: Path to the generated code directory.

        Returns:
            Dict with ``status`` (``"passed"``/``"failed"``/``"error"``),
            ``errors``, ``warnings``, ``missing_files``, and
            ``invalid_files``.
        """
        # TODO(Phase 5): Inline implementation from
        #   OmniCoreService._validate_deployment_completeness.
        omnicore = self._ctx.omnicore_engine
        if omnicore is not None and hasattr(
            omnicore, "_validate_deployment_completeness"
        ):
            return await omnicore._validate_deployment_completeness(
                job_id, code_path
            )

        logger.error(
            "[DeployService] Cannot delegate _validate_deployment_completeness",
            extra={"job_id": job_id},
        )
        return {
            "status": "error",
            "errors": [
                "DeployService delegation failed: OmniCoreService not available"
            ],
        }

    # ------------------------------------------------------------------
    # Future: private helpers (Phase 5)
    # ------------------------------------------------------------------
    # async def _write_kubernetes_configs(self, ...) -> list[str]
    # async def _write_helm_chart(self, ...) -> list[str]
    # async def _write_docker_configs(self, ...) -> list[str]
    # async def _generate_fallback_dockerfile(self, ...) -> list[str]
