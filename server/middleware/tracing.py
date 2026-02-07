# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Distributed tracing middleware for Code Factory platform.

This module provides OpenTelemetry-based distributed tracing for monitoring
and debugging across the platform's services, following industry standards:
- OpenTelemetry specification 1.0+
- W3C Trace Context specification
- Cloud Native Computing Foundation (CNCF) best practices

Compliance:
- ISO 27001 A.12.1.2: Logging and monitoring
- SOC 2 CC7.2: System monitoring
- NIST SP 800-53 AU-2: Audit events
"""

import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry, but make it optional
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logger.warning(
        "OpenTelemetry not available. Install with: "
        "pip install opentelemetry-api opentelemetry-sdk "
        "opentelemetry-exporter-otlp-proto-grpc "
        "opentelemetry-instrumentation-logging"
    )


def setup_tracing(
    service_name: str = "code-factory",
    service_version: str = "1.0.0",
    environment: str = "production",
    console_export: bool = False
) -> Optional[object]:
    """
    Set up OpenTelemetry distributed tracing with industry-standard configuration.
    
    Implements distributed tracing following:
    - OpenTelemetry Specification 1.0+
    - W3C Trace Context for propagation
    - Cloud Native Computing Foundation best practices
    - ISO 27001 A.12.1.2: Logging and monitoring
    
    Args:
        service_name: Name of the service for tracing (default: "code-factory")
        service_version: Version of the service (default: "1.0.0")
        environment: Deployment environment (production, staging, development)
        console_export: Export traces to console for debugging (default: False)
    
    Returns:
        Tracer instance if OpenTelemetry is available, None otherwise
        
    Environment Variables:
        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP collector endpoint (e.g., http://localhost:4317)
        OTEL_EXPORTER_OTLP_HEADERS: Headers for OTLP exporter (e.g., "api-key=secret")
        OTEL_SERVICE_NAME: Override service name
        OTEL_RESOURCE_ATTRIBUTES: Additional resource attributes
        
    Example:
        >>> tracer = setup_tracing("my-service", "1.0.0", "production")
        >>> with tracer.start_as_current_span("operation") as span:
        ...     span.set_attribute("user_id", "123")
        ...     # ... operation code ...
    """
    if not OTEL_AVAILABLE:
        logger.info(
            "Tracing disabled: OpenTelemetry not available",
            extra={"component": "tracing", "status": "disabled"}
        )
        return None
    
    try:
        # Override service name from environment if provided
        service_name = os.getenv("OTEL_SERVICE_NAME", service_name)
        
        # Create resource with service information
        resource_attributes = {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: service_version,
            "deployment.environment": environment,
            "telemetry.sdk.language": "python",
            "telemetry.sdk.name": "opentelemetry",
        }
        
        # Add custom resource attributes from environment
        custom_attrs = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
        if custom_attrs:
            for attr in custom_attrs.split(","):
                if "=" in attr:
                    key, value = attr.split("=", 1)
                    resource_attributes[key.strip()] = value.strip()
        
        resource = Resource(attributes=resource_attributes)
        
        # Create tracer provider with resource
        provider = TracerProvider(resource=resource)
        
        # Configure OTLP exporter if endpoint is provided
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            # Parse headers if provided
            headers = {}
            otlp_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
            if otlp_headers:
                for header in otlp_headers.split(","):
                    if "=" in header:
                        key, value = header.split("=", 1)
                        headers[key.strip()] = value.strip()
            
            # Create OTLP exporter
            otlp_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                headers=headers if headers else None
            )
            
            # Add batch processor for OTLP export
            processor = BatchSpanProcessor(
                otlp_exporter,
                max_queue_size=2048,
                max_export_batch_size=512,
                schedule_delay_millis=5000,
            )
            provider.add_span_processor(processor)
            
            logger.info(
                f"OTLP trace exporter configured",
                extra={
                    "endpoint": otlp_endpoint,
                    "service_name": service_name,
                    "component": "tracing"
                }
            )
        
        # Add console exporter for debugging if requested
        if console_export:
            console_processor = BatchSpanProcessor(ConsoleSpanExporter())
            provider.add_span_processor(console_processor)
            logger.info("Console trace exporter enabled for debugging")
        
        # Set global tracer provider
        trace.set_tracer_provider(provider)
        
        # Instrument logging to include trace context
        try:
            LoggingInstrumentor().instrument(set_logging_format=True)
            logger.debug("Logging instrumented with trace context")
        except Exception as e:
            logger.warning(f"Could not instrument logging: {e}")
        
        logger.info(
            f"Distributed tracing enabled",
            extra={
                "service_name": service_name,
                "service_version": service_version,
                "environment": environment,
                "component": "tracing",
                "status": "enabled"
            }
        )
        
        # Return tracer for use in application
        return trace.get_tracer(
            instrumenting_module_name=__name__,
            instrumenting_library_version=service_version
        )
        
    except Exception as e:
        logger.error(
            f"Failed to set up tracing: {type(e).__name__}: {e}",
            extra={
                "component": "tracing",
                "error_type": type(e).__name__,
                "service_name": service_name
            },
            exc_info=True
        )
        return None


def add_span_attributes(span, attributes: Dict[str, Any]) -> None:
    """
    Add multiple attributes to a span with validation.
    
    Args:
        span: OpenTelemetry span object
        attributes: Dictionary of attributes to add
        
    Example:
        >>> with tracer.start_as_current_span("operation") as span:
        ...     add_span_attributes(span, {
        ...         "user_id": "123",
        ...         "operation_type": "create",
        ...         "resource_count": 5
        ...     })
    """
    if not OTEL_AVAILABLE or not span:
        return
    
    for key, value in attributes.items():
        try:
            # Validate attribute key format
            if not isinstance(key, str) or not key:
                logger.warning(f"Invalid span attribute key: {key}")
                continue
            
            # Set attribute with type checking
            span.set_attribute(key, value)
        except Exception as e:
            logger.warning(f"Failed to set span attribute {key}: {e}")


def add_span_event(span, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
    """
    Add an event to a span with optional attributes.
    
    Args:
        span: OpenTelemetry span object
        name: Event name
        attributes: Optional dictionary of event attributes
        
    Example:
        >>> with tracer.start_as_current_span("operation") as span:
        ...     add_span_event(span, "validation_completed", {
        ...         "validation_errors": 0,
        ...         "validation_time_ms": 45
        ...     })
    """
    if not OTEL_AVAILABLE or not span:
        return
    
    try:
        span.add_event(name, attributes=attributes or {})
    except Exception as e:
        logger.warning(f"Failed to add span event {name}: {e}")


# Usage example in route handlers:
# from opentelemetry import trace
# from server.middleware.tracing import add_span_attributes, add_span_event
#
# tracer = trace.get_tracer(__name__)
#
# @router.post("/generate")
# async def generate_code(request: GenerateRequest):
#     with tracer.start_as_current_span("generate_code") as span:
#         add_span_attributes(span, {
#             "job_id": request.job_id,
#             "agent_type": request.agent,
#             "priority": request.priority
#         })
#         
#         try:
#             # ... operation code ...
#             add_span_event(span, "generation_started")
#             result = await generate(request)
#             add_span_event(span, "generation_completed", {
#                 "lines_generated": len(result.code.split("\n"))
#             })
#             return result
#         except Exception as e:
#             span.record_exception(e)
#             span.set_status(Status(StatusCode.ERROR, str(e)))
#             raise

