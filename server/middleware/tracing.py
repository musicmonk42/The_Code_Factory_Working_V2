"""
Distributed tracing middleware for Code Factory platform.

This module provides OpenTelemetry-based distributed tracing for monitoring
and debugging across the platform's services.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry, but make it optional
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logger.warning(
        "OpenTelemetry not available. Install with: "
        "pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc"
    )


def setup_tracing(service_name: str = "code-factory") -> Optional[object]:
    """
    Set up OpenTelemetry tracing.
    
    Args:
        service_name: Name of the service for tracing
    
    Returns:
        Tracer instance if OpenTelemetry is available, None otherwise
    """
    if not OTEL_AVAILABLE:
        logger.info("Tracing disabled: OpenTelemetry not available")
        return None
    
    try:
        provider = TracerProvider()
        processor = BatchSpanProcessor(OTLPSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        
        logger.info(f"Distributed tracing enabled for service: {service_name}")
        return trace.get_tracer(service_name)
    except Exception as e:
        logger.error(f"Failed to set up tracing: {e}")
        return None


# Usage example in route handlers:
# from opentelemetry import trace
#
# tracer = trace.get_tracer(__name__)
#
# @router.post("/generate")
# async def generate_code(request: GenerateRequest):
#     if tracer:
#         with tracer.start_as_current_span("generate_code") as span:
#             span.set_attribute("job_id", request.job_id)
#             # ... rest of handler
