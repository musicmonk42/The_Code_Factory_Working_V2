# Copyright 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pipeline sub-services for the OmniCore generator pipeline.

This package decomposes the monolithic pipeline methods from
``OmniCoreService`` into focused, single-responsibility services:

- **CodegenService** -- LLM-driven code generation orchestration.
- **DeployService** -- deployment artifact generation (Docker, K8s, Helm).
- **QualityService** -- test generation, documentation, and code critique.
- **PipelineOrchestrator** -- end-to-end pipeline sequencing that composes
  the three sub-services above.

All services accept a :class:`~server.services.service_context.ServiceContext`
and follow the same ``__init__(ctx)`` / ``async method(...)`` pattern.
"""

from server.services.pipeline.codegen_service import CodegenService
from server.services.pipeline.deploy_service import DeployService
from server.services.pipeline.quality_service import QualityService
from server.services.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
    get_pipeline_orchestrator,
)

__all__ = [
    "CodegenService",
    "DeployService",
    "QualityService",
    "PipelineOrchestrator",
    "get_pipeline_orchestrator",
]
