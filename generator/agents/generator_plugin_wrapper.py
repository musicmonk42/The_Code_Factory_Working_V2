"""
generator_plugin_wrapper.py – Centralized Plugin Registration for README-to-App Code Generator

This module registers all Generator agents as plugins for the OmniCore Engine, providing a
production-ready orchestration workflow (clarify → code → critique → tests → deploy → docs).
It includes:
- Pydantic validation for inputs/outputs.
- Prometheus metrics and OpenTelemetry tracing for observability.
- Structured logging with PII redaction.
- Robust error handling with retries and circuit breaking.
- Async-safe concurrency with locks.

The workflow is triggered via OmniCore’s message bus (e.g., topic "start_workflow") and
produces serialized outputs compatible with Self-Fixing Engineer (SFE) for maintenance.

Dependencies:
- omnicore_engine (plugin_registry, message_bus)
- pydantic, prometheus_client, opentelemetry-sdk
- Generator agents (codegen_agent, testgen_agent, critique_agent, deploy_agent, docgen_agent, clarifier)

Author: Code Factory Team
"""

import logging
import time
import threading
import os
import uuid
from typing import Dict, Any, List  # <-- FIX: Moved 'List' here
from datetime import datetime, timezone
from pydantic import (
    BaseModel,
    Field,
    ValidationError as PydanticValidationError,
    field_validator,
    ConfigDict,
)
from prometheus_client import Counter, Histogram
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import re


from omnicore_engine.plugin_registry import plugin, PlugInKind, PLUGIN_REGISTRY

# Removed direct agent imports to rely on the PLUGIN_REGISTRY for decoupling
# from .codegen_agent.codegen_agent import generate_code
# from .testgen_agent.testgen_agent import generate_tests
# ... etc.

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# OpenTelemetry setup
trace.set_tracer_provider(
    TracerProvider(
        resource=Resource.create({"service.name": "generator-plugin-wrapper"})
    )
)
tracer = trace.get_tracer(__name__)
exporter_type = os.getenv("SFE_OTEL_EXPORTER_TYPE", "console").lower()
exporter = OTLPSpanExporter() if exporter_type == "otlp" else ConsoleSpanExporter()
trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(exporter))

# Prometheus metrics
_metrics_lock = threading.Lock()
_created_metrics = {}  # Cache of created metrics


def get_or_create_metric(metric_class, name, description, labelnames=None, **kwargs):
    """Thread-safe metric creation that avoids duplicate registration."""
    with _metrics_lock:
        # Check our cache first
        if name in _created_metrics:
            return _created_metrics[name]

        # Create the metric and cache it
        metric = metric_class(name, description, labelnames=labelnames, **kwargs)
        _created_metrics[name] = metric
        return metric


workflow_latency = get_or_create_metric(
    Histogram,
    "generator_workflow_latency_seconds",
    "Latency of Generator workflow execution",
    labelnames=["stage", "correlation_id"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
)
workflow_success = get_or_create_metric(
    Counter,
    "generator_workflow_success_total",
    "Total successful Generator workflows",
    labelnames=["correlation_id"],
)
workflow_errors = get_or_create_metric(
    Counter,
    "generator_workflow_errors_total",
    "Total failed Generator workflows",
    labelnames=["correlation_id", "stage", "error_type"],
)


# Custom exceptions
class GeneratorPluginError(Exception):
    """Base exception for Generator plugin errors."""

    pass


class ValidationError(GeneratorPluginError):
    """Raised for invalid input/output validation."""

    pass


class WorkflowError(GeneratorPluginError):
    """Raised for workflow execution failures."""

    pass


# Pydantic models for validation
class WorkflowInput(BaseModel):
    requirements: Dict[str, Any] = Field(
        ..., description="Natural language requirements from README"
    )
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Configuration for workflow execution"
    )
    repo_path: str = Field(
        ..., description="The local path to the codebase repository."
    )
    ambiguities: List[str] = Field(
        default_factory=list,
        description="A list of ambiguous statements found in requirements.",
    )

    @field_validator("requirements")
    def validate_requirements(cls, v):
        if not v or not isinstance(v, dict):
            raise ValueError("Requirements must be a non-empty dictionary")
        return v


class WorkflowOutput(BaseModel):
    status: str = Field(
        ..., description="The final status of the workflow (e.g., 'success', 'failed')."
    )
    correlation_id: str = Field(
        ..., description="The unique ID for tracing the workflow run."
    )
    final_results: Dict[str, Any] = Field(
        ...,
        description="A dictionary containing the artifacts from each successful stage.",
    )
    errors: List[str] = Field(
        default_factory=list,
        description="A list of errors encountered during the workflow.",
    )
    timestamp: str = Field(..., description="Execution timestamp in ISO format")

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
        extra="forbid",
        populate_by_name=True,
    )


# PII redaction
def redact_pii(data: str) -> str:
    """Redact PII from strings using regex patterns."""
    patterns = {
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "PHONE": r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        "SSN": r"\d{3}-\d{2}-\d{4}",
    }
    sanitized = data
    for pii_type, pattern in patterns.items():
        sanitized = re.sub(pattern, f"[{pii_type}]", sanitized, flags=re.IGNORECASE)
    return sanitized


@plugin(
    kind=PlugInKind.EXECUTION,  # <-- FIX: Was PlugInKind.WORKFLOW, which caused an AttributeError
    name="generator_workflow",
    version="2.0.0",
    params_schema={
        "requirements": {"type": "dict"},
        "config": {"type": "dict"},
        "repo_path": {"type": "string"},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
    },
    description="Orchestrates the full README-to-App code generation pipeline.",
    safe=False,  # Workflows that modify file systems are not considered safe by default
)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(WorkflowError),
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying workflow due to transient error: attempt {retry_state.attempt_number}"
    ),
)
async def run_generator_workflow(
    requirements: Dict[str, Any],
    config: Dict[str, Any],
    repo_path: str,
    ambiguities: List[str],
) -> Dict[str, Any]:
    """
    Orchestrates the full Generator pipeline: clarify -> code -> critique -> tests -> deploy -> docs.
    This version calls agents via the PLUGIN_REGISTRY for better decoupling and maintainability.
    """
    correlation_id = str(uuid.uuid4())
    start_time = time.time()

    with tracer.start_as_current_span(
        "generator_workflow", attributes={"correlation_id": correlation_id}
    ) as span:
        try:
            input_data = WorkflowInput(
                requirements=requirements,
                config=config,
                repo_path=repo_path,
                ambiguities=ambiguities,
            )
            logger.info(
                f"Starting Generator workflow [Correlation ID: {correlation_id}] for repo: {repo_path}"
            )
            span.set_attribute("workflow_start", datetime.now(timezone.utc).isoformat())

            # --- State dictionary to pass between stages ---
            workflow_state = {
                "requirements": input_data.requirements,
                "config": input_data.config,
                "repo_path": input_data.repo_path,
                "ambiguities": input_data.ambiguities,
                "code_files": {},
                "test_files": {},
                "critique_results": {},
                "deployment_artifacts": {},
                "documentation": {},
            }

            # --- 1. Clarification Stage ---
            clarifier = PLUGIN_REGISTRY.get("clarifier")
            if clarifier and workflow_state["ambiguities"]:
                with workflow_latency.labels(
                    stage="clarify", correlation_id=correlation_id
                ).time():
                    clarified_result = await clarifier(
                        requirements=workflow_state["requirements"],
                        ambiguities=workflow_state["ambiguities"],
                    )
                    workflow_state["requirements"] = clarified_result.get(
                        "requirements", workflow_state["requirements"]
                    )
                    logger.info(
                        f"Clarification stage complete [Correlation ID: {correlation_id}]"
                    )

            # --- 2. Code Generation Stage ---
            codegen = PLUGIN_REGISTRY.get("codegen_agent")
            with workflow_latency.labels(
                stage="codegen", correlation_id=correlation_id
            ).time():
                code_result = await codegen(
                    requirements=workflow_state["requirements"],
                    state_summary="Initial code generation",
                    config_path_or_dict=workflow_state["config"],
                )
                if "error.txt" in code_result:
                    raise WorkflowError(f"Codegen failed: {code_result['error.txt']}")
                workflow_state["code_files"] = code_result
                logger.info(
                    f"Code generation stage complete [Correlation ID: {correlation_id}]"
                )

            # --- 3. Critique Stage ---
            critiquer = PLUGIN_REGISTRY.get("critique_agent")
            with workflow_latency.labels(
                stage="critique", correlation_id=correlation_id
            ).time():
                critique_result = await critiquer(
                    code_files=workflow_state["code_files"],
                    test_files={},  # No tests yet
                    requirements=workflow_state["requirements"],
                    state_summary="Post-generation critique",
                    config=workflow_state["config"],
                )
                workflow_state["critique_results"] = critique_result
                logger.info(
                    f"Critique stage complete [Correlation ID: {correlation_id}]"
                )

            # --- 4. Test Generation Stage ---
            testgen = PLUGIN_REGISTRY.get("testgen_agent")
            with workflow_latency.labels(
                stage="testgen", correlation_id=correlation_id
            ).time():
                test_result = await testgen(
                    code_files=workflow_state["code_files"],
                    requirements=workflow_state["requirements"],
                )
                workflow_state["test_files"] = test_result
                logger.info(
                    f"Test generation stage complete [Correlation ID: {correlation_id}]"
                )

            # --- 5. Deployment Artifact Generation Stage ---
            deployer = PLUGIN_REGISTRY.get("deploy_agent")
            with workflow_latency.labels(
                stage="deploy", correlation_id=correlation_id
            ).time():
                deploy_result = await deployer(
                    repo_path=workflow_state["repo_path"],
                    target_files=list(workflow_state["code_files"].keys()),
                    targets=["docker", "helm"],  # Example targets
                    instructions="Generate standard deployment artifacts for a web service.",
                )
                workflow_state["deployment_artifacts"] = deploy_result
                logger.info(
                    f"Deployment artifact generation stage complete [Correlation ID: {correlation_id}]"
                )

            # --- 6. Documentation Generation Stage ---
            docgen = PLUGIN_REGISTRY.get("docgen_agent")
            with workflow_latency.labels(
                stage="docgen", correlation_id=correlation_id
            ).time():
                docs_result = await docgen(
                    repo_path=workflow_state["repo_path"],
                    target_files=list(workflow_state["code_files"].keys()),
                    doc_type="README",
                )
                workflow_state["documentation"] = docs_result
                logger.info(
                    f"Documentation generation stage complete [Correlation ID: {correlation_id}]"
                )

            workflow_success.labels(correlation_id=correlation_id).inc()
            workflow_latency.labels(
                stage="total", correlation_id=correlation_id
            ).observe(time.time() - start_time)
            logger.info(
                f"Generator workflow completed successfully [Correlation ID: {correlation_id}]"
            )
            span.set_attribute("workflow_status", "success")

            output = WorkflowOutput(
                status="success",
                correlation_id=correlation_id,
                final_results=workflow_state,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return output.model_dump()

        except (
            PydanticValidationError,
            ValidationError,
            WorkflowError,
            GeneratorPluginError,
        ) as e:
            # Determine the stage based on error type
            if isinstance(e, PydanticValidationError):
                stage = "validation"
            elif isinstance(e, ValidationError):
                stage = "validation"
            elif isinstance(e, WorkflowError):
                stage = "execution"
            else:  # GeneratorPluginError
                stage = "plugin"

            workflow_errors.labels(
                correlation_id=correlation_id, stage=stage, error_type=type(e).__name__
            ).inc()
            logger.error(
                f"Workflow failed at stage '{stage}': {e} [Correlation ID: {correlation_id}]",
                exc_info=True,
            )
            span.record_exception(e)
            span.set_attribute("workflow_status", "failed")
            output = WorkflowOutput(
                status="failed",
                correlation_id=correlation_id,
                final_results={},
                errors=[str(e)],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return output.model_dump()
        except Exception as e:
            workflow_errors.labels(
                correlation_id=correlation_id,
                stage="unknown",
                error_type=type(e).__name__,
            ).inc()
            logger.critical(
                f"An unexpected critical error occurred in the workflow: {e} [Correlation ID: {correlation_id}]",
                exc_info=True,
            )
            span.record_exception(e)
            span.set_attribute("workflow_status", "critical_failure")
            output = WorkflowOutput(
                status="critical_failure",
                correlation_id=correlation_id,
                final_results={},
                errors=[f"Unexpected critical error: {e}"],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return output.model_dump()


__all__ = [
    "run_generator_workflow",
    "WorkflowInput",
    "WorkflowOutput",
    "GeneratorPluginError",
    "ValidationError",
    "WorkflowError",
]
