# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Services package for module interactions.

This package provides enterprise-grade services for:
- Generator operations (code generation, clarification)
- OmniCore coordination (inter-module communication)
- Self-Fixing Engineer operations (analysis, fixing)
- Job finalization (atomic state management, artifact persistence)
- Event dispatch (Kafka, webhooks, with circuit breaker pattern)

All services follow industry best practices:
- Dependency injection for testability
- Graceful degradation on failures
- Comprehensive observability (metrics, tracing, logging)
- Security by design (input validation, path traversal prevention)
"""

from .generator_service import GeneratorService, get_generator_service
from .sfe_service import SFEService

# New domain services (decoupled from OmniCoreService)
from .service_context import ServiceContext, create_service_context
from .job_router import route_job
from .admin_service import AdminService, get_admin_service
from .audit_query_service import AuditQueryService, get_audit_query_service
from .diagnostics_service import DiagnosticsService, get_diagnostics_service
from .message_bus_service import MessageBusService, get_message_bus_service

# Backward compat — lazy-loaded to avoid eagerly importing the 11K-line god-module
def __getattr__(name):
    if name in ("OmniCoreService", "get_omnicore_service", "get_omnicore_service_async"):
        from .omnicore_service import OmniCoreService, get_omnicore_service, get_omnicore_service_async
        _compat = {"OmniCoreService": OmniCoreService, "get_omnicore_service": get_omnicore_service, "get_omnicore_service_async": get_omnicore_service_async}
        return _compat[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Import SFE utilities
from .sfe_utils import (
    transform_pipeline_issues_to_frontend_errors,
    transform_pipeline_issues_to_bugs,
    MAX_ISSUES_PER_BATCH,
    ERROR_ID_PREFIX,
    DEFAULT_SEVERITY,
)

# Import job finalization service functions
from .job_finalization import (
    finalize_job_success,
    finalize_job_failure,
    reset_finalization_state,
)

# Import dispatch service functions
from .dispatch_service import (
    dispatch_job_completion,
    get_kafka_health_status,
    kafka_available,
    mark_kafka_failure,
    mark_kafka_success,
)

__all__ = [
    # Core services
    "GeneratorService",
    "get_generator_service",
    "SFEService",

    # Domain services
    "ServiceContext",
    "create_service_context",
    "route_job",
    "AdminService",
    "get_admin_service",
    "AuditQueryService",
    "get_audit_query_service",
    "DiagnosticsService",
    "get_diagnostics_service",
    "MessageBusService",
    "get_message_bus_service",

    # Backward compat
    "OmniCoreService",
    "get_omnicore_service",
    "get_omnicore_service_async",
    
    # SFE utilities
    "transform_pipeline_issues_to_frontend_errors",
    "transform_pipeline_issues_to_bugs",
    "MAX_ISSUES_PER_BATCH",
    "ERROR_ID_PREFIX",
    "DEFAULT_SEVERITY",
    
    # Job finalization
    "finalize_job_success",
    "finalize_job_failure",
    "reset_finalization_state",
    
    # Event dispatch
    "dispatch_job_completion",
    "get_kafka_health_status",
    "kafka_available",
    "mark_kafka_failure",
    "mark_kafka_success",
]
