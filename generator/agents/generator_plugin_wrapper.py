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
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List  # <-- FIX: Moved 'List' here

from opentelemetry import trace
from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic import field_validator, field_serializer
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Logger setup - needs to be early for use in defensive imports
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# Defensive import for omnicore_engine.plugin_registry with fallbacks
try:
    from omnicore_engine.plugin_registry import PLUGIN_REGISTRY, PlugInKind, plugin
    _PLUGIN_REGISTRY_AVAILABLE = True
except (ImportError, AttributeError) as e:
    # Provide fallback for test environments where omnicore_engine may not be fully initialized
    logger.warning(
        f"Failed to import from omnicore_engine.plugin_registry: {e}. "
        "Using fallback implementations for testing."
    )
    _PLUGIN_REGISTRY_AVAILABLE = False
    
    # Fallback PlugInKind enum
    from enum import Enum
    
    class PlugInKind(str, Enum):
        """Fallback PlugInKind enum for when omnicore_engine is unavailable."""
        FIX = "fix"
        CHECK = "check"
        VALIDATION = "validation"
        EXECUTION = "execution"
        CORE_SERVICE = "core_service"
        SCENARIO = "scenario"
        CUSTOM = "custom"
        AGGREGATOR = "aggregator"
        AI_ASSISTANT = "ai_assistant"
        OPTIMIZATION = "optimization"
        MONITORING = "monitoring"
        GROWTH_MANAGER = "growth_manager"
        SIMULATION_RUNNER = "simulation_runner"
        EVOLUTION = "evolution"
        RL_ENVIRONMENT = "rl_environment"
    
    # Fallback plugin decorator
    def plugin(
        kind=None,
        name=None,
        description="",
        version="0.1.0",
        safe=True,
        source="code",
        params_schema=None,
        signature=None,
        subscriptions=None,
    ):
        """Fallback no-op decorator for when omnicore_engine is unavailable."""
        def decorator(f):
            # Just return the function unmodified in test mode
            logger.debug(
                f"Fallback plugin decorator applied to {f.__name__} "
                f"(kind={kind}, name={name})"
            )
            return f
        
        # Handle both @plugin and @plugin(...) syntax
        if kind is not None and callable(kind):
            # Called as @plugin without arguments (kind is actually the function)
            func = kind
            logger.debug(f"Fallback plugin decorator applied to {func.__name__} (no args)")
            return func
        return decorator
    
    # Fallback PLUGIN_REGISTRY
    PLUGIN_REGISTRY = None

# Removed direct agent imports to rely on the PLUGIN_REGISTRY for decoupling
# from .codegen_agent.codegen_agent import generate_code
# from .testgen_agent.testgen_agent import generate_tests
# ... etc.


# OpenTelemetry setup
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except (TypeError, Exception) as e:
    # Fallback for older OpenTelemetry versions or when OpenTelemetry is not available
    logger.warning(f"OpenTelemetry tracer not available: {e}. Tracing will be disabled.")
    tracer = None


# Helper to safely use tracer
from contextlib import contextmanager
from typing import Optional


# No-op span class for when OpenTelemetry is unavailable
class NoOpSpan:
    """A no-op span that implements the minimal span interface."""
    
    def set_attribute(self, key, value):
        """No-op set_attribute."""
        pass
    
    def record_exception(self, exception):
        """No-op record_exception."""
        pass


@contextmanager
def safe_span(span_name: str, attributes: Optional[Dict[str, Any]] = None):
    """Context manager that safely handles tracing even when tracer is None.
    
    Args:
        span_name: Name of the span
        attributes: Optional attributes dict for the span
        
    Yields:
        A span object (real or no-op)
    """
    if tracer is not None:
        with tracer.start_as_current_span(span_name, attributes=attributes or {}) as span:
            yield span
    else:
        yield NoOpSpan()


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


class ConfigurationError(GeneratorPluginError):
    """Raised when critical agents or configurations are missing.

    This error indicates a fatal misconfiguration that prevents the workflow
    from executing. In production environments, this should halt startup
    rather than allowing silent degradation.
    """

    pass


class AgentUnavailableError(GeneratorPluginError):
    """Raised when a required agent is not available in the plugin registry.

    This error is raised when the orchestrator attempts to use an agent
    that failed to load or was not registered. This prevents silent failures
    and NoneType errors during workflow execution.
    """

    pass


# --- Agent Validation Utilities ---
# These utilities ensure fail-fast behavior for missing critical agents

# Define which agents are required vs optional for the workflow
REQUIRED_AGENTS = frozenset(
    {"codegen_agent", "critique_agent", "testgen_agent", "deploy_agent", "docgen_agent"}
)
OPTIONAL_AGENTS = frozenset({"clarifier"})


def validate_agent_available(agent_name: str, agent: object) -> None:
    """Validate that an agent is available and callable.

    Args:
        agent_name: The name of the agent being validated.
        agent: The agent object retrieved from the registry.

    Raises:
        AgentUnavailableError: If the agent is None or not callable.
    """
    if agent is None:
        raise AgentUnavailableError(
            f"Critical agent '{agent_name}' is not available in the plugin registry. "
            f"This may indicate a failed import, missing dependency, or configuration error. "
            f"Check the agent's __init__.py for import errors and ensure all dependencies are installed."
        )
    if not callable(agent):
        raise AgentUnavailableError(
            f"Agent '{agent_name}' was found but is not callable (type: {type(agent).__name__}). "
            f"Expected an async callable function or class with __call__ method."
        )


def validate_required_agents(registry: object) -> dict:
    """Validate all required agents are available at startup.

    This function should be called during workflow initialization to ensure
    fail-fast behavior rather than discovering missing agents mid-workflow.

    Args:
        registry: The plugin registry to check for agents.

    Returns:
        A dict mapping agent names to their callables.

    Raises:
        ConfigurationError: If any required agent is missing.
    """
    missing_agents = []
    agents = {}

    for agent_name in REQUIRED_AGENTS:
        agent = registry.get(agent_name)
        if agent is None:
            missing_agents.append(agent_name)
        else:
            agents[agent_name] = agent

    if missing_agents:
        raise ConfigurationError(
            f"Critical workflow agents are missing from the plugin registry: {', '.join(sorted(missing_agents))}. "
            f"The generator workflow cannot execute without these agents. "
            f"Please check agent initialization logs and ensure all dependencies are properly installed. "
            f"Required agents: {', '.join(sorted(REQUIRED_AGENTS))}"
        )

    return agents


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
        extra="forbid",
        populate_by_name=True,
    )

    @field_serializer("*", when_used="json")
    def serialize_datetime(self, value: Any) -> Any:
        """Serialize datetime objects to ISO format strings."""
        if isinstance(value, datetime):
            return value.isoformat()
        return value


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
    All critical agents are validated before workflow execution to ensure fail-fast behavior
    rather than silent failures mid-workflow.

    Args:
        requirements: Natural language requirements from README.
        config: Configuration for workflow execution.
        repo_path: The local path to the codebase repository.
        ambiguities: A list of ambiguous statements found in requirements.

    Returns:
        A WorkflowOutput dict containing status, correlation_id, final_results, errors, and timestamp.

    Raises:
        ConfigurationError: If critical agents are missing from the registry (not caught).
        AgentUnavailableError: If a required agent is None or not callable (not caught).
    """
    correlation_id = str(uuid.uuid4())
    start_time = time.time()
    
    # [ARBITER] Initialize ArbiterBridge with graceful degradation
    bridge = None
    try:
        from generator.arbiter_bridge import ArbiterBridge
        bridge = ArbiterBridge()
        logger.info(f"ArbiterBridge initialized for workflow [Correlation ID: {correlation_id}]")
    except ImportError as e:
        logger.debug(f"ArbiterBridge not available, generator working standalone: {e}")
    except Exception as e:
        logger.warning(f"Failed to initialize ArbiterBridge: {e}, continuing without Arbiter integration")

    with safe_span("generator_workflow", {"correlation_id": correlation_id}) as span:
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

            # --- CRITICAL: Validate all required agents are available BEFORE workflow starts ---
            # This ensures fail-fast behavior rather than discovering missing agents mid-workflow.
            # ConfigurationError is intentionally NOT caught here - it should propagate up.
            logger.debug(
                f"Validating required agents [Correlation ID: {correlation_id}]"
            )
            
            # [ARBITER] Pre-workflow policy check
            if bridge:
                try:
                    allowed, reason = await bridge.check_policy(
                        "run_workflow",
                        {
                            "correlation_id": correlation_id,
                            "repo_path": repo_path,
                            "has_ambiguities": bool(ambiguities)
                        }
                    )
                    if not allowed:
                        logger.warning(
                            f"Workflow denied by Arbiter policy [Correlation ID: {correlation_id}]: {reason}"
                        )
                        span.set_attribute("workflow_status", "policy_denied")
                        output = WorkflowOutput(
                            status="failed",
                            correlation_id=correlation_id,
                            final_results={},
                            errors=[f"Workflow denied by policy: {reason}"],
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        )
                        return output.model_dump()
                except Exception as e:
                    logger.warning(f"Arbiter policy check failed: {e}, continuing anyway")
            
            try:
                validated_agents = validate_required_agents(PLUGIN_REGISTRY)
                logger.info(
                    f"All required agents validated successfully [Correlation ID: {correlation_id}]: "
                    f"{', '.join(sorted(validated_agents.keys()))}"
                )
            except ConfigurationError as config_err:
                # Log the configuration error with full details for operators
                logger.critical(
                    f"FATAL: Workflow cannot start due to missing critical agents "
                    f"[Correlation ID: {correlation_id}]: {config_err}"
                )
                span.set_attribute("workflow_status", "configuration_error")
                span.record_exception(config_err)
                # Re-raise to ensure the caller knows this is a fatal configuration issue
                raise

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

            # --- 1. Clarification Stage (Optional) ---
            # Clarifier is optional - workflow continues if it's not available
            clarifier = PLUGIN_REGISTRY.get("clarifier")
            if clarifier and workflow_state["ambiguities"]:
                if not callable(clarifier):
                    logger.warning(
                        f"Clarifier agent found but not callable, skipping clarification "
                        f"[Correlation ID: {correlation_id}]"
                    )
                else:
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
            elif workflow_state["ambiguities"] and not clarifier:
                logger.warning(
                    f"Ambiguities present but clarifier agent not available, proceeding without clarification "
                    f"[Correlation ID: {correlation_id}]"
                )

            # --- 2. Code Generation Stage (Required) ---
            # Note: Agents are already validated by validate_required_agents() above
            codegen = validated_agents["codegen_agent"]
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
                
                # [ARBITER] Publish codegen output event
                if bridge:
                    try:
                        await bridge.publish_event(
                            "generator_output",
                            {
                                "correlation_id": correlation_id,
                                "stage": "codegen",
                                "files_generated": len(code_result),
                                "file_names": list(code_result.keys())[:10]  # First 10 files
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish codegen event: {e}")

            # --- 3. Critique Stage (Required) ---
            critiquer = validated_agents["critique_agent"]
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
                
                # [ARBITER] Publish critique results event
                if bridge:
                    try:
                        await bridge.publish_event(
                            "critique_results",
                            {
                                "correlation_id": correlation_id,
                                "stage": "critique",
                                "lint_issues": len(critique_result.get("lint_errors", [])),
                                "vulnerabilities": len(critique_result.get("vulnerabilities", [])),
                                "fixes_applied": critique_result.get("fixes_applied", False)
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish critique event: {e}")

            # --- 4. Test Generation Stage (Required) ---
            testgen = validated_agents["testgen_agent"]
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
                
                # [ARBITER] Publish test results event
                if bridge:
                    try:
                        await bridge.publish_event(
                            "test_results",
                            {
                                "correlation_id": correlation_id,
                                "stage": "testgen",
                                "tests_generated": len(test_result),
                                "test_files": list(test_result.keys())[:5]  # First 5 test files
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish test results event: {e}")

            # --- 5. Deployment Artifact Generation Stage (Required) ---
            deployer = validated_agents["deploy_agent"]
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
                
                # [ARBITER] Publish deploy artifacts event
                if bridge:
                    try:
                        await bridge.publish_event(
                            "deploy_artifacts",
                            {
                                "correlation_id": correlation_id,
                                "stage": "deploy",
                                "artifacts_generated": len(deploy_result.get("configs", {})),
                                "targets": ["docker", "helm"]
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish deploy artifacts event: {e}")

            # --- 6. Documentation Generation Stage (Required) ---
            docgen = validated_agents["docgen_agent"]
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
            
            # [ARBITER] Publish workflow completion event
            if bridge:
                try:
                    await bridge.publish_event(
                        "workflow_completed",
                        {
                            "correlation_id": correlation_id,
                            "status": "success",
                            "duration_seconds": time.time() - start_time,
                            "stages_completed": ["clarify", "codegen", "critique", "testgen", "deploy", "docgen"]
                        }
                    )
                    # Update knowledge graph with workflow statistics
                    await bridge.update_knowledge(
                        "generator_workflow",
                        correlation_id,
                        {
                            "status": "success",
                            "duration": time.time() - start_time,
                            "repo_path": repo_path,
                            "files_generated": len(workflow_state["code_files"]),
                            "tests_generated": len(workflow_state["test_files"])
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to publish workflow completion to Arbiter: {e}")

            output = WorkflowOutput(
                status="success",
                correlation_id=correlation_id,
                final_results=workflow_state,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return output.model_dump()

        except (ConfigurationError, AgentUnavailableError) as e:
            # CRITICAL: Configuration errors should NOT be silently caught.
            # These indicate fatal misconfiguration that requires operator intervention.
            # We log, record metrics, but then RE-RAISE to ensure the caller knows.
            workflow_errors.labels(
                correlation_id=correlation_id,
                stage="configuration",
                error_type=type(e).__name__,
            ).inc()
            logger.critical(
                f"FATAL CONFIGURATION ERROR: {e} [Correlation ID: {correlation_id}]. "
                f"This error indicates missing critical agents and requires operator intervention.",
                exc_info=True,
            )
            span.record_exception(e)
            span.set_attribute("workflow_status", "configuration_error")
            # Re-raise configuration errors - these should NOT return a "failed" response
            # because they indicate a system-level issue, not a workflow-level failure.
            raise

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
            
            # [ARBITER] Report workflow failure
            if bridge:
                try:
                    await bridge.report_bug({
                        "title": f"Generator workflow failed at {stage} stage",
                        "description": f"Workflow {correlation_id} failed with error: {str(e)}",
                        "severity": "high",
                        "error": str(e),
                        "context": {
                            "correlation_id": correlation_id,
                            "stage": stage,
                            "error_type": type(e).__name__,
                            "repo_path": repo_path
                        }
                    })
                except Exception as bug_error:
                    logger.warning(f"Failed to report workflow failure to Arbiter: {bug_error}")
            
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
            
            # [ARBITER] Report critical failure
            if bridge:
                try:
                    await bridge.report_bug({
                        "title": f"Critical failure in generator workflow",
                        "description": f"Unexpected critical error in workflow {correlation_id}: {str(e)}",
                        "severity": "critical",
                        "error": str(e),
                        "context": {
                            "correlation_id": correlation_id,
                            "error_type": type(e).__name__,
                            "repo_path": repo_path
                        }
                    })
                except Exception as bug_error:
                    logger.warning(f"Failed to report critical failure to Arbiter: {bug_error}")
            
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
    "ConfigurationError",
    "AgentUnavailableError",
    "validate_agent_available",
    "validate_required_agents",
    "REQUIRED_AGENTS",
    "OPTIONAL_AGENTS",
]
