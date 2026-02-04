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
from .omnicore_service import OmniCoreService, get_omnicore_service
from .sfe_service import SFEService

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
    "OmniCoreService",
    "get_omnicore_service",
    "SFEService",
    
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
