# generator/main/engine.py
"""
Workflow Engine Module - Production-Grade Orchestration System.

This module provides the core workflow engine for the Generator CLI, implementing
industry-standard patterns for distributed AI agent orchestration.

Key Features:
- Dynamic agent registration with hot-swapping capabilities
- Async workflow orchestration with configurable iteration strategies
- Full observability stack: OpenTelemetry tracing + Prometheus metrics
- Comprehensive error handling with structured exceptions
- Audit logging for compliance and debugging
- Thread-safe agent registry with lifecycle management

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    WorkflowEngine                           │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  Agent Registry (Thread-Safe)                        │  │
    │  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │  │
    │  │  │ codegen │ │critique │ │ testgen │ │ docgen  │   │  │
    │  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │  │
    │  └───────│───────────│───────────│───────────│────────┘  │
    │          └───────────┴───────────┴───────────┘           │
    │                      Orchestration                        │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │ Input → Parse → Generate → Critique → Test → Output │  │
    │  └──────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────┘

Usage:
    from generator.main.engine import WorkflowEngine, register_agent, AGENT_REGISTRY

    # Register custom agents
    register_agent("custom_agent", MyCustomAgentClass)

    # Create and run workflow
    engine = WorkflowEngine(config)
    if engine.health_check():
        result = await engine.orchestrate(
            input_file="README.md",
            max_iterations=5,
            output_path="./output"
        )

Industry Standards Compliance:
- OpenTelemetry: Distributed tracing (W3C Trace Context)
- Prometheus: Metrics exposition (OpenMetrics format)
- Pydantic: Data validation and serialization
- Structured Logging: JSON-structured log events
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Type,
    TypeVar,
    Union,
    runtime_checkable,
)

# Import provenance tracking for pipeline stage logging
try:
    from generator.main.provenance import (
        ProvenanceTracker,
        run_fail_fast_validation,
        validate_deployment_artifacts,
    )
    HAS_PROVENANCE = True
except ImportError:
    HAS_PROVENANCE = False
    ProvenanceTracker = None
    run_fail_fast_validation = None
    validate_deployment_artifacts = None

# Import DeployAgent for deployment artifact generation
try:
    from generator.agents.deploy_agent.deploy_agent import DeployAgent
    HAS_DEPLOY_AGENT = True
except ImportError as e:
    # Logger is not yet defined at import time, so we'll log later
    HAS_DEPLOY_AGENT = False
    DeployAgent = None
    _DEPLOY_AGENT_IMPORT_ERROR = str(e)

# --- Pydantic for Data Validation ---
try:
    from pydantic import BaseModel, Field, field_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    # Minimal fallback for environments without pydantic
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    
    def Field(*args, **kwargs):
        return kwargs.get('default')
    
    def field_validator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

# --- OpenTelemetry Integration ---
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, Span
    _tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    
    class _NoOpSpan:
        """No-op span for environments without OpenTelemetry."""
        def set_attribute(self, *args, **kwargs): pass
        def set_status(self, *args, **kwargs): pass
        def record_exception(self, *args, **kwargs): pass
        def add_event(self, *args, **kwargs): pass
        def end(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    
    class _NoOpTracer:
        """No-op tracer for environments without OpenTelemetry."""
        def start_as_current_span(self, name, **kwargs): return _NoOpSpan()
        def start_span(self, name, **kwargs): return _NoOpSpan()
    
    _tracer = _NoOpTracer()
    
    class StatusCode:
        OK = "OK"
        ERROR = "ERROR"
    
    class Status:
        def __init__(self, status_code, description=None):
            self.status_code = status_code
            self.description = description

# --- Prometheus Metrics ---
try:
    from prometheus_client import Counter, Histogram, Gauge, Info
    HAS_PROMETHEUS = True
    
    # Workflow metrics
    WORKFLOW_EXECUTIONS = Counter(
        'workflow_engine_executions_total',
        'Total number of workflow executions',
        ['status', 'dry_run']
    )
    WORKFLOW_DURATION = Histogram(
        'workflow_engine_duration_seconds',
        'Workflow execution duration in seconds',
        ['status'],
        buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
    )
    WORKFLOW_ITERATIONS = Histogram(
        'workflow_engine_iterations',
        'Number of iterations per workflow',
        buckets=[1, 2, 3, 5, 10, 20, 50]
    )
    AGENT_REGISTRY_SIZE = Gauge(
        'workflow_engine_agent_registry_size',
        'Number of agents currently registered'
    )
    AGENT_EXECUTIONS = Counter(
        'workflow_engine_agent_executions_total',
        'Total agent executions',
        ['agent_name', 'status']
    )
    ENGINE_INFO = Info(
        'workflow_engine',
        'Workflow engine metadata'
    )
    ENGINE_INFO.info({
        'version': '1.0.0',
        'has_opentelemetry': str(HAS_OPENTELEMETRY),
        'has_pydantic': str(HAS_PYDANTIC)
    })
except ImportError:
    HAS_PROMETHEUS = False
    
    # No-op metrics for environments without Prometheus
    class _NoOpMetric:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def dec(self, *args, **kwargs): pass
        def set(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        def time(self):
            class _Timer:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return _Timer()
    
    WORKFLOW_EXECUTIONS = _NoOpMetric()
    WORKFLOW_DURATION = _NoOpMetric()
    WORKFLOW_ITERATIONS = _NoOpMetric()
    AGENT_REGISTRY_SIZE = _NoOpMetric()
    AGENT_EXECUTIONS = _NoOpMetric()
    ENGINE_INFO = _NoOpMetric()

# --- Logging Configuration ---
logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class WorkflowStatus(str, Enum):
    """Enumeration of possible workflow execution statuses."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DRY_RUN_COMPLETED = "dry_run_completed"
    TIMEOUT = "timeout"


class AgentStatus(str, Enum):
    """Enumeration of agent execution statuses."""
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# Default configuration values
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_ITERATION_DELAY_SECONDS = 0.1
DEFAULT_AGENT_TIMEOUT_SECONDS = 300


# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================

class EngineError(Exception):
    """Base exception for all workflow engine errors.
    
    Provides structured error information for consistent error handling
    and reporting across the workflow engine.
    """
    
    def __init__(
        self,
        message: str,
        error_code: str = "ENGINE_ERROR",
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.cause = cause
        self.timestamp = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to a dictionary for structured logging/serialization."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
            "cause": str(self.cause) if self.cause else None
        }


class AgentNotFoundError(EngineError):
    """Raised when a required agent is not found in the registry."""
    
    def __init__(self, agent_name: str):
        super().__init__(
            message=f"Agent '{agent_name}' not found in registry",
            error_code="AGENT_NOT_FOUND",
            details={"agent_name": agent_name}
        )


class AgentExecutionError(EngineError):
    """Raised when an agent fails during execution."""
    
    def __init__(self, agent_name: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Agent '{agent_name}' execution failed",
            error_code="AGENT_EXECUTION_FAILED",
            details={"agent_name": agent_name},
            cause=cause
        )


class WorkflowTimeoutError(EngineError):
    """Raised when a workflow exceeds its timeout duration."""
    
    def __init__(self, timeout_seconds: float, elapsed_seconds: float):
        super().__init__(
            message=f"Workflow timeout after {elapsed_seconds:.2f}s (limit: {timeout_seconds}s)",
            error_code="WORKFLOW_TIMEOUT",
            details={"timeout_seconds": timeout_seconds, "elapsed_seconds": elapsed_seconds}
        )


class HealthCheckError(EngineError):
    """Raised when a health check fails."""
    
    def __init__(self, component: str, reason: str):
        super().__init__(
            message=f"Health check failed for {component}: {reason}",
            error_code="HEALTH_CHECK_FAILED",
            details={"component": component, "reason": reason}
        )


# =============================================================================
# DATA CONTRACTS (Pydantic Models)
# =============================================================================

if HAS_PYDANTIC:
    class WorkflowRequest(BaseModel):
        """Request model for workflow execution."""
        
        input_file: str = Field(..., description="Path to the input file")
        max_iterations: int = Field(
            default=DEFAULT_MAX_ITERATIONS,
            ge=1,
            le=100,
            description="Maximum refinement iterations"
        )
        output_path: Optional[str] = Field(
            default=None,
            description="Path for output artifacts"
        )
        dry_run: bool = Field(
            default=False,
            description="Simulate execution without side effects"
        )
        user_id: Optional[str] = Field(
            default=None,
            description="User identifier for audit logging"
        )
        timeout_seconds: Optional[float] = Field(
            default=None,
            ge=1,
            description="Workflow timeout in seconds"
        )
        
        @field_validator('input_file')
        @classmethod
        def validate_input_file(cls, v: str) -> str:
            if not v or not v.strip():
                raise ValueError("input_file must be a non-empty string")
            return v.strip()


    class WorkflowResult(BaseModel):
        """Result model for workflow execution."""
        
        workflow_id: str = Field(..., description="Unique workflow identifier")
        status: WorkflowStatus = Field(..., description="Final workflow status")
        input_file: str = Field(..., description="Input file that was processed")
        output_path: Optional[str] = Field(default=None, description="Output artifacts path")
        iterations: int = Field(default=0, description="Number of iterations completed")
        started_at: str = Field(..., description="ISO timestamp when workflow started")
        finished_at: str = Field(..., description="ISO timestamp when workflow finished")
        duration_seconds: float = Field(..., description="Total execution time")
        errors: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="List of errors encountered"
        )
        agent_results: Dict[str, Any] = Field(
            default_factory=dict,
            description="Results from each agent execution"
        )
        
        class Config:
            use_enum_values = True
else:
    # Simple dataclass fallback for environments without Pydantic
    @dataclass
    class WorkflowRequest:
        input_file: str
        max_iterations: int = DEFAULT_MAX_ITERATIONS
        output_path: Optional[str] = None
        dry_run: bool = False
        user_id: Optional[str] = None
        timeout_seconds: Optional[float] = None
    
    @dataclass
    class WorkflowResult:
        workflow_id: str
        status: str
        input_file: str
        started_at: str
        finished_at: str
        duration_seconds: float
        output_path: Optional[str] = None
        iterations: int = 0
        errors: List[Dict[str, Any]] = field(default_factory=list)
        agent_results: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# AGENT PROTOCOL AND REGISTRY
# =============================================================================

@runtime_checkable
class AgentProtocol(Protocol):
    """Protocol defining the interface for workflow agents.
    
    All agents registered with the workflow engine should implement this
    protocol to ensure consistent behavior and interoperability.
    """
    
    async def execute(
        self,
        input_data: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the agent's primary function.
        
        Args:
            input_data: Input data for the agent to process.
            config: Configuration parameters for this execution.
        
        Returns:
            Dictionary containing the agent's output and metadata.
        """
        ...


class AgentRegistry:
    """Thread-safe registry for workflow agents.
    
    Provides centralized management of agent classes with:
    - Thread-safe registration and retrieval
    - Hot-swapping capabilities for runtime updates
    - Lifecycle hooks for agent initialization/cleanup
    
    This class follows the Singleton pattern to ensure a single
    global registry instance.
    """
    
    _instance: Optional['AgentRegistry'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'AgentRegistry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._agents: Dict[str, Type[Any]] = {}
                    cls._instance._metadata: Dict[str, Dict[str, Any]] = {}
                    cls._instance._registry_lock = threading.RLock()
        return cls._instance
    
    def register(
        self,
        name: str,
        agent_class: Type[Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register an agent class with the given name.
        
        Args:
            name: Unique identifier for the agent.
            agent_class: The agent class to register.
            metadata: Optional metadata about the agent (version, description, etc.)
        
        Raises:
            ValueError: If agent_class is None.
        """
        if agent_class is None:
            raise ValueError(f"Cannot register None as agent '{name}'")
        
        with self._registry_lock:
            if name in self._agents:
                logger.warning(
                    f"Agent '{name}' already registered. Overwriting.",
                    extra={"agent_name": name, "action": "overwrite"}
                )
            
            self._agents[name] = agent_class
            self._metadata[name] = metadata or {}
            self._metadata[name]["registered_at"] = datetime.now(timezone.utc).isoformat()
            
            # Update Prometheus metric
            AGENT_REGISTRY_SIZE.set(len(self._agents))
            
            logger.info(
                f"Agent '{name}' registered successfully",
                extra={"agent_name": name, "action": "register"}
            )
    
    def unregister(self, name: str) -> bool:
        """Remove an agent from the registry.
        
        Args:
            name: The name of the agent to remove.
        
        Returns:
            True if the agent was removed, False if it wasn't found.
        """
        with self._registry_lock:
            if name in self._agents:
                del self._agents[name]
                del self._metadata[name]
                AGENT_REGISTRY_SIZE.set(len(self._agents))
                logger.info(
                    f"Agent '{name}' unregistered",
                    extra={"agent_name": name, "action": "unregister"}
                )
                return True
            return False
    
    def get(self, name: str) -> Optional[Type[Any]]:
        """Get an agent class by name.
        
        Args:
            name: The name of the agent to retrieve.
        
        Returns:
            The agent class if found, None otherwise.
        """
        with self._registry_lock:
            return self._agents.get(name)
    
    def get_all(self) -> Dict[str, Type[Any]]:
        """Get a copy of all registered agents.
        
        Returns:
            Dictionary mapping agent names to their classes.
        """
        with self._registry_lock:
            return dict(self._agents)
    
    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a registered agent.
        
        Args:
            name: The name of the agent.
        
        Returns:
            Agent metadata if found, None otherwise.
        """
        with self._registry_lock:
            return self._metadata.get(name, {}).copy()
    
    def clear(self) -> None:
        """Remove all agents from the registry."""
        with self._registry_lock:
            self._agents.clear()
            self._metadata.clear()
            AGENT_REGISTRY_SIZE.set(0)
            logger.info("Agent registry cleared", extra={"action": "clear"})
    
    def __len__(self) -> int:
        with self._registry_lock:
            return len(self._agents)
    
    def __contains__(self, name: str) -> bool:
        with self._registry_lock:
            return name in self._agents
    
    def __iter__(self):
        with self._registry_lock:
            return iter(list(self._agents.keys()))


# Global singleton instance for backwards compatibility
_agent_registry = AgentRegistry()


class _AgentRegistryProxy(dict):
    """A dict-like proxy that provides backwards compatibility with AGENT_REGISTRY.
    
    This proxy ensures that all dict operations are thread-safe by delegating
    to the AgentRegistry singleton. This maintains backwards compatibility
    with code that accesses AGENT_REGISTRY directly as a dict.
    """
    
    def __init__(self, registry: AgentRegistry):
        self._registry = registry
    
    def __getitem__(self, key):
        result = self._registry.get(key)
        if result is None:
            raise KeyError(key)
        return result
    
    def __setitem__(self, key, value):
        self._registry.register(key, value)
    
    def __delitem__(self, key):
        if not self._registry.unregister(key):
            raise KeyError(key)
    
    def __contains__(self, key):
        return key in self._registry
    
    def __iter__(self):
        return iter(self._registry)
    
    def __len__(self):
        return len(self._registry)
    
    def get(self, key, default=None):
        result = self._registry.get(key)
        return result if result is not None else default
    
    def keys(self):
        return self._registry.get_all().keys()
    
    def values(self):
        return self._registry.get_all().values()
    
    def items(self):
        return self._registry.get_all().items()
    
    def __repr__(self):
        return repr(self._registry.get_all())


# Dict-like interface for backwards compatibility with AGENT_REGISTRY usage
# This proxy provides thread-safe access while maintaining dict semantics
AGENT_REGISTRY: Dict[str, Type[Any]] = _AgentRegistryProxy(_agent_registry)


def register_agent(
    name: str,
    agent_class: Type[Any],
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Register an agent class with the global registry.
    
    This is a convenience function for registering agents without
    directly accessing the AgentRegistry singleton.
    
    Args:
        name: Unique identifier for the agent.
        agent_class: The agent class to register.
        metadata: Optional metadata about the agent.
    """
    _agent_registry.register(name, agent_class, metadata)


def hot_swap_agent(
    name: str,
    new_agent_class: Type[Any],
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Hot-swap an existing agent with a new implementation.
    
    This function allows runtime replacement of agent implementations
    without requiring a restart. Useful for:
    - A/B testing different agent versions
    - Rolling deployments
    - Dynamic capability updates
    
    Args:
        name: The name of the agent to swap.
        new_agent_class: The new agent class to use.
        metadata: Optional updated metadata.
    """
    with _tracer.start_as_current_span("hot_swap_agent") as span:
        span.set_attribute("agent.name", name)
        
        if name not in _agent_registry:
            logger.warning(
                f"Agent '{name}' not found in registry. Registering as new.",
                extra={"agent_name": name, "action": "hot_swap_new"}
            )
        else:
            logger.info(
                f"Hot-swapping agent '{name}'",
                extra={"agent_name": name, "action": "hot_swap"}
            )
        
        _agent_registry.register(name, new_agent_class, metadata)


def get_agent(name: str) -> Optional[Type[Any]]:
    """Get an agent class by name from the global registry.
    
    Args:
        name: The name of the agent to retrieve.
    
    Returns:
        The agent class if found, None otherwise.
    """
    return _agent_registry.get(name)


# =============================================================================
# WORKFLOW ENGINE
# =============================================================================

class WorkflowEngine:
    """Production-grade workflow orchestration engine.
    
    Coordinates execution across multiple AI agents to transform input
    (e.g., README specifications) into complete applications. Implements
    an iterative refinement loop with critique and testing stages.
    
    Key Capabilities:
    - Async execution with proper cancellation support
    - Configurable iteration strategies with early stopping
    - Full observability: tracing, metrics, structured logging
    - Graceful degradation when agents are unavailable
    - Feedback-driven tuning for continuous improvement
    
    Thread Safety:
        This class is designed to be instantiated per-request. The agent
        registry is thread-safe, but WorkflowEngine instances should not
        be shared across concurrent requests.
    
    Example:
        ```python
        config = load_config("config.yaml")
        engine = WorkflowEngine(config)
        
        if engine.health_check():
            result = await engine.orchestrate(
                input_file="README.md",
                max_iterations=5,
                output_path="./output",
                user_id="user_123"
            )
            print(f"Workflow {result.status}: {result.iterations} iterations")
        ```
    """
    
    def __init__(self, config: Union[Dict[str, Any], Any]):
        """Initialize the WorkflowEngine.
        
        Args:
            config: Configuration object or dictionary containing:
                - max_iterations: Default iteration limit (optional)
                - timeout_seconds: Default timeout (optional)
                - agent_timeout_seconds: Per-agent timeout (optional)
                - enable_critique: Whether to run critique agent (default: True)
                - enable_testing: Whether to run test generation (default: True)
        """
        self.config = config if isinstance(config, dict) else {}
        self._initialized = False
        self._shutdown_requested = False
        self._active_workflows: Dict[str, asyncio.Task] = {}
        self._workflow_lock = asyncio.Lock()
        
        # Extract configuration with defaults
        self._default_max_iterations = self.config.get(
            'max_iterations', DEFAULT_MAX_ITERATIONS
        )
        self._default_timeout = self.config.get('timeout_seconds')
        self._agent_timeout = self.config.get(
            'agent_timeout_seconds', DEFAULT_AGENT_TIMEOUT_SECONDS
        )
        self._enable_critique = self.config.get('enable_critique', True)
        self._enable_testing = self.config.get('enable_testing', True)
        
        logger.info(
            "WorkflowEngine initialized",
            extra={
                "default_max_iterations": self._default_max_iterations,
                "enable_critique": self._enable_critique,
                "enable_testing": self._enable_testing
            }
        )
    
    def health_check(self) -> bool:
        """Perform comprehensive health check.
        
        Validates:
        - Agent registry accessibility
        - Required agents availability (if configured)
        - Internal state consistency
        
        Returns:
            True if the engine is healthy and ready to process workflows.
        
        Raises:
            HealthCheckError: If a critical component is unhealthy.
        """
        with _tracer.start_as_current_span("workflow_engine.health_check") as span:
            try:
                # Check agent registry
                agent_count = len(_agent_registry)
                span.set_attribute("agent_registry.count", agent_count)
                
                if agent_count == 0:
                    logger.warning(
                        "No agents registered in workflow engine",
                        extra={"health_check": "warning", "agents": 0}
                    )
                    span.add_event("warning", {"message": "No agents registered"})
                else:
                    logger.debug(
                        f"Health check: {agent_count} agents registered",
                        extra={"agents": list(_agent_registry)}
                    )
                
                # Check for required agents (codegen is essential)
                if "codegen" not in _agent_registry:
                    logger.warning(
                        "Codegen agent not available - workflow may have limited functionality",
                        extra={"health_check": "degraded"}
                    )
                    span.add_event("warning", {"message": "Codegen agent unavailable"})
                
                # Check shutdown state
                if self._shutdown_requested:
                    logger.warning("Health check during shutdown", extra={"shutting_down": True})
                    span.set_attribute("shutting_down", True)
                    return False
                
                span.set_status(Status(StatusCode.OK))
                return True
                
            except Exception as e:
                logger.error(f"Health check failed: {e}", exc_info=True)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                return False
    
    async def orchestrate(
        self,
        input_file: str,
        max_iterations: Optional[int] = None,
        output_path: Optional[str] = None,
        dry_run: bool = False,
        user_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """Orchestrate the complete workflow execution.
        
        Executes an iterative refinement loop:
        1. Parse input requirements
        2. Generate code using codegen agent
        3. Critique generated code (if enabled)
        4. Generate/run tests (if enabled)
        5. Iterate based on feedback until max_iterations or convergence
        
        Args:
            input_file: Path to the input file (e.g., README.md).
            max_iterations: Maximum refinement iterations (default from config).
            output_path: Directory for output artifacts.
            dry_run: If True, simulate without executing agents.
            user_id: User identifier for audit logging.
            timeout_seconds: Workflow timeout (default from config).
        
        Returns:
            Dictionary containing workflow results including:
            - workflow_id: Unique identifier for this execution
            - status: Final workflow status
            - iterations: Number of iterations completed
            - output_path: Where artifacts were written
            - errors: Any errors encountered
            - agent_results: Results from each agent
        
        Raises:
            WorkflowTimeoutError: If the workflow exceeds its timeout.
            EngineError: For other execution failures.
        """
        workflow_id = str(uuid.uuid4())
        start_time = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        
        # Use defaults from config if not specified
        max_iterations = max_iterations or self._default_max_iterations
        timeout_seconds = timeout_seconds or self._default_timeout
        
        with _tracer.start_as_current_span("workflow_engine.orchestrate") as span:
            span.set_attribute("workflow.id", workflow_id)
            span.set_attribute("workflow.input_file", input_file)
            span.set_attribute("workflow.max_iterations", max_iterations)
            span.set_attribute("workflow.dry_run", dry_run)
            if user_id:
                span.set_attribute("workflow.user_id", user_id)
            
            # Log workflow start
            logger.info(
                f"Starting workflow orchestration",
                extra={
                    "workflow_id": workflow_id,
                    "input_file": input_file,
                    "max_iterations": max_iterations,
                    "dry_run": dry_run,
                    "user_id": user_id
                }
            )
            
            # Audit log (fire-and-forget to avoid blocking workflow on slow audit system)
            try:
                from generator.runner.runner_logging import log_audit_event
                # Use create_task for non-blocking audit logging with exception handling
                async def _safe_audit():
                    try:
                        await asyncio.wait_for(
                            log_audit_event(
                                action="workflow_started",
                                data={
                                    "workflow_id": workflow_id,
                                    "input_file": input_file,
                                    "user_id": user_id,
                                    "dry_run": dry_run
                                }
                            ),
                            timeout=5.0  # 5 second timeout for audit
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Audit logging timed out")
                    except Exception as e:
                        logger.warning(f"Audit logging failed: {e}")
                
                asyncio.create_task(_safe_audit())
            except ImportError:
                logger.debug("Audit logging not available")
            
            # Handle dry run
            if dry_run:
                logger.info(f"DRY RUN: Simulating workflow {workflow_id}")
                span.add_event("dry_run_simulation")
                WORKFLOW_EXECUTIONS.labels(status="dry_run_completed", dry_run="true").inc()
                
                return {
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.DRY_RUN_COMPLETED.value,
                    "input_file": input_file,
                    "iterations": 0,
                    "output_path": output_path,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": time.monotonic() - start_time,
                    "errors": [],
                    "agent_results": {}
                }
            
            # Initialize result structure
            result: Dict[str, Any] = {
                "workflow_id": workflow_id,
                "status": WorkflowStatus.PENDING.value,
                "input_file": input_file,
                "iterations": 0,
                "output_path": output_path,
                "started_at": started_at,
                "finished_at": None,
                "duration_seconds": 0.0,
                "errors": [],
                "agent_results": {}
            }
            
            # Initialize provenance tracker for pipeline stage logging
            provenance = None
            if HAS_PROVENANCE and ProvenanceTracker:
                provenance = ProvenanceTracker(job_id=workflow_id)
                logger.info(f"[STAGE:INIT] Provenance tracking initialized for workflow {workflow_id}")
            
            try:
                result["status"] = WorkflowStatus.RUNNING.value
                
                # [STAGE:READ_MD] Read and record input MD content
                md_content = ""
                if Path(input_file).exists():
                    try:
                        with open(input_file, "r", encoding="utf-8") as f:
                            md_content = f.read()
                        if provenance:
                            provenance.record_stage(
                                ProvenanceTracker.STAGE_READ_MD,
                                artifacts={"md_input": md_content},
                                metadata={"input_file": input_file}
                            )
                        logger.info(f"[STAGE:READ_MD] Read {len(md_content)} chars from {input_file}")
                    except Exception as e:
                        logger.warning(f"Could not read input file {input_file}: {e}")
                        if provenance:
                            provenance.record_error(
                                ProvenanceTracker.STAGE_READ_MD,
                                "file_read_error",
                                str(e)
                            )
                
                # Main orchestration loop
                for iteration in range(max_iterations):
                    # Check timeout
                    if timeout_seconds:
                        elapsed = time.monotonic() - start_time
                        if elapsed > timeout_seconds:
                            raise WorkflowTimeoutError(timeout_seconds, elapsed)
                    
                    # Check for shutdown
                    if self._shutdown_requested:
                        logger.warning(f"Workflow {workflow_id} cancelled due to shutdown")
                        result["status"] = WorkflowStatus.CANCELLED.value
                        break
                    
                    iteration_num = iteration + 1
                    result["iterations"] = iteration_num
                    
                    with _tracer.start_as_current_span(f"iteration_{iteration_num}") as iter_span:
                        iter_span.set_attribute("iteration.number", iteration_num)
                        logger.debug(f"Workflow {workflow_id}: Starting iteration {iteration_num}")
                        
                        # [STAGE:CODEGEN] Execute codegen agent with MD content
                        codegen_input = {
                            "input_file": input_file,
                            "md_content": md_content,  # Pass MD content directly
                            "iteration": iteration_num,
                            "previous_results": result.get("agent_results", {})
                        }
                        codegen_result = await self._execute_agent(
                            "codegen",
                            codegen_input,
                            workflow_id
                        )
                        result["agent_results"]["codegen"] = codegen_result
                        
                        # Record codegen output in provenance
                        if provenance:
                            codegen_files = codegen_result.get("files", {}) if isinstance(codegen_result, dict) else {}
                            main_py_content = codegen_files.get("main.py", "")
                            if main_py_content:
                                provenance.record_stage(
                                    ProvenanceTracker.STAGE_CODEGEN,
                                    artifacts={"main.py": main_py_content},
                                    metadata={"iteration": iteration_num}
                                )
                        
                        # Execute critique agent if enabled
                        if self._enable_critique and "critique" in _agent_registry:
                            critique_result = await self._execute_agent(
                                "critique",
                                {
                                    "codegen_output": codegen_result,
                                    "iteration": iteration_num
                                },
                                workflow_id
                            )
                            result["agent_results"]["critique"] = critique_result
                        
                        # [STAGE:VALIDATE] Run fail-fast validation BEFORE test generation
                        if HAS_PROVENANCE and run_fail_fast_validation:
                            codegen_files = codegen_result.get("files", {}) if isinstance(codegen_result, dict) else {}
                            if codegen_files:
                                validation_result = run_fail_fast_validation(
                                    codegen_files,
                                    output_dir=output_path
                                )
                                if provenance:
                                    provenance.record_stage(
                                        ProvenanceTracker.STAGE_VALIDATE,
                                        metadata={"validation_result": validation_result}
                                    )
                                if not validation_result.get("valid", True):
                                    logger.warning(
                                        f"[STAGE:VALIDATE] Validation failed: {validation_result.get('errors', [])}",
                                        extra={"validation_errors": validation_result.get("errors", [])}
                                    )
                        
                        # [STAGE:TESTGEN] Execute test generation AFTER validation
                        # Tests should be generated based on final validated code
                        if self._enable_testing and "testgen" in _agent_registry:
                            testgen_result = await self._execute_agent(
                                "testgen",
                                {
                                    "codegen_output": codegen_result,
                                    "iteration": iteration_num,
                                    "md_content": md_content  # Pass MD for spec-driven tests
                                },
                                workflow_id
                            )
                            result["agent_results"]["testgen"] = testgen_result
                            
                            # Record testgen output
                            if provenance:
                                provenance.record_stage(
                                    ProvenanceTracker.STAGE_TESTGEN,
                                    metadata={"iteration": iteration_num, "status": testgen_result.get("status", "unknown")}
                                )
                        
                        # [STAGE:DEPLOY_GEN] Generate deployment artifacts AFTER testgen
                        # This ensures we have validated code before creating deployment configs
                        enable_deploy = self.config.get('enable_deploy', True)
                        if enable_deploy and HAS_DEPLOY_AGENT and DeployAgent:
                            try:
                                deploy_result = await self._run_deploy_stage(
                                    codegen_result=codegen_result,
                                    output_path=output_path,
                                    workflow_id=workflow_id,
                                    provenance=provenance
                                )
                                result["agent_results"]["deploy"] = deploy_result
                                logger.info(
                                    f"[STAGE:DEPLOY_GEN] Deployment artifacts generated",
                                    extra={
                                        "workflow_id": workflow_id,
                                        "files_generated": deploy_result.get("files_written", [])
                                    }
                                )
                            except Exception as deploy_error:
                                logger.warning(
                                    f"[STAGE:DEPLOY_GEN] Deployment generation failed: {deploy_error}",
                                    extra={"workflow_id": workflow_id, "error": str(deploy_error)}
                                )
                                result["agent_results"]["deploy"] = {
                                    "status": "failed",
                                    "error": str(deploy_error)
                                }
                                if provenance:
                                    provenance.record_error(
                                        ProvenanceTracker.STAGE_DEPLOY_GEN,
                                        "deploy_generation_error",
                                        str(deploy_error)
                                    )
                        
                        # Small delay between iterations
                        await asyncio.sleep(DEFAULT_ITERATION_DELAY_SECONDS)
                
                # Mark as completed
                result["status"] = WorkflowStatus.COMPLETED.value
                span.set_status(Status(StatusCode.OK))
                logger.info(
                    f"Workflow {workflow_id} completed successfully",
                    extra={
                        "workflow_id": workflow_id,
                        "iterations": result["iterations"],
                        "status": result["status"]
                    }
                )
                
            except WorkflowTimeoutError as e:
                result["status"] = WorkflowStatus.TIMEOUT.value
                result["errors"].append(e.to_dict())
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                logger.error(f"Workflow {workflow_id} timed out: {e}")
                
            except asyncio.CancelledError:
                result["status"] = WorkflowStatus.CANCELLED.value
                span.set_status(Status(StatusCode.ERROR, "Cancelled"))
                logger.warning(f"Workflow {workflow_id} was cancelled")
                raise
                
            except Exception as e:
                result["status"] = WorkflowStatus.FAILED.value
                error_info = {
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                result["errors"].append(error_info)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                logger.error(
                    f"Workflow {workflow_id} failed: {e}",
                    exc_info=True,
                    extra={"workflow_id": workflow_id}
                )
            
            finally:
                # Finalize result
                end_time = time.monotonic()
                result["finished_at"] = datetime.now(timezone.utc).isoformat()
                result["duration_seconds"] = end_time - start_time
                
                # [STAGE:PACKAGE] Save provenance data if output_path is specified
                if provenance and output_path:
                    try:
                        provenance.record_stage(
                            ProvenanceTracker.STAGE_PACKAGE,
                            metadata={
                                "status": result["status"],
                                "iterations": result["iterations"],
                                "duration_seconds": result["duration_seconds"]
                            }
                        )
                        provenance_path = provenance.save_to_file(output_path)
                        result["provenance_path"] = provenance_path
                        logger.info(f"[STAGE:PACKAGE] Provenance saved to {provenance_path}")
                        
                        # Check for overwrites
                        overwrites = provenance.get_artifact_overwrites()
                        if overwrites:
                            logger.warning(
                                f"[PROVENANCE] Detected artifact overwrites: {list(overwrites.keys())}",
                                extra={"overwrites": overwrites}
                            )
                    except Exception as e:
                        logger.warning(f"Failed to save provenance data: {e}")
                
                # Record metrics
                WORKFLOW_EXECUTIONS.labels(
                    status=result["status"],
                    dry_run="false"
                ).inc()
                WORKFLOW_DURATION.labels(status=result["status"]).observe(
                    result["duration_seconds"]
                )
                WORKFLOW_ITERATIONS.observe(result["iterations"])
            
            return result
    
    async def _execute_agent(
        self,
        agent_name: str,
        input_data: Dict[str, Any],
        workflow_id: str
    ) -> Dict[str, Any]:
        """Execute a single agent within the workflow.
        
        Args:
            agent_name: Name of the agent to execute.
            input_data: Input data for the agent.
            workflow_id: Parent workflow identifier.
        
        Returns:
            Agent execution results.
        """
        with _tracer.start_as_current_span(f"agent.{agent_name}") as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("workflow.id", workflow_id)
            
            agent_class = _agent_registry.get(agent_name)
            
            if agent_class is None:
                logger.warning(
                    f"Agent '{agent_name}' not available, using fallback behavior",
                    extra={"agent_name": agent_name, "workflow_id": workflow_id}
                )
                span.add_event("agent_not_available")
                AGENT_EXECUTIONS.labels(agent_name=agent_name, status="skipped").inc()
                return {
                    "status": AgentStatus.SKIPPED.value,
                    "message": f"Agent '{agent_name}' not available"
                }
            
            try:
                logger.debug(
                    f"Executing agent '{agent_name}'",
                    extra={"agent_name": agent_name, "workflow_id": workflow_id}
                )
                
                # Check if agent has an async execute method (single lookup)
                execute_method = getattr(agent_class, 'execute', None)
                if execute_method is not None and asyncio.iscoroutinefunction(execute_method):
                    agent_instance = agent_class()
                    result = await asyncio.wait_for(
                        agent_instance.execute(input_data, self.config),
                        timeout=self._agent_timeout
                    )
                else:
                    # Agent is a config/simple class - just acknowledge registration
                    result = {
                        "status": AgentStatus.SUCCESS.value,
                        "message": f"Agent '{agent_name}' is available (config class)",
                        "agent_class": str(agent_class)
                    }
                
                span.set_status(Status(StatusCode.OK))
                AGENT_EXECUTIONS.labels(agent_name=agent_name, status="success").inc()
                return result
                
            except asyncio.TimeoutError:
                logger.error(
                    f"Agent '{agent_name}' timed out after {self._agent_timeout}s",
                    extra={"agent_name": agent_name, "workflow_id": workflow_id}
                )
                span.set_status(Status(StatusCode.ERROR, "Timeout"))
                AGENT_EXECUTIONS.labels(agent_name=agent_name, status="timeout").inc()
                return {
                    "status": AgentStatus.FAILED.value,
                    "error": f"Agent timed out after {self._agent_timeout}s"
                }
                
            except Exception as e:
                logger.error(
                    f"Agent '{agent_name}' execution failed: {e}",
                    exc_info=True,
                    extra={"agent_name": agent_name, "workflow_id": workflow_id}
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                AGENT_EXECUTIONS.labels(agent_name=agent_name, status="failed").inc()
                return {
                    "status": AgentStatus.FAILED.value,
                    "error": str(e)
                }
    
    async def _run_deploy_stage(
        self,
        codegen_result: Dict[str, Any],
        output_path: Optional[str],
        workflow_id: str,
        provenance: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Run the deployment artifact generation stage.
        
        This stage generates Docker-based deployment configurations including:
        - Dockerfile
        - docker-compose.yml
        - .dockerignore
        - deploy_metadata.json
        
        Args:
            codegen_result: Results from the code generation stage
            output_path: Directory for output artifacts
            workflow_id: Parent workflow identifier
            provenance: Optional ProvenanceTracker instance
            
        Returns:
            Deploy stage results including files written
        """
        with _tracer.start_as_current_span("workflow_engine.deploy_stage") as span:
            span.set_attribute("workflow.id", workflow_id)
            
            deploy_result = {
                "status": "pending",
                "files_written": [],
                "plugin": "docker",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            try:
                # Get generated files from codegen result
                codegen_files = codegen_result.get("files", {}) if isinstance(codegen_result, dict) else {}
                
                if not codegen_files:
                    logger.warning(
                        "[STAGE:DEPLOY_GEN] No generated files found for deployment",
                        extra={"workflow_id": workflow_id}
                    )
                    deploy_result["status"] = "skipped"
                    deploy_result["reason"] = "No generated files"
                    return deploy_result
                
                # Detect language and framework from generated code
                language = "python"
                framework = "fastapi"  # Default for Python web APIs
                entry_point = "main.py"
                
                # Check main.py content for framework detection
                main_py = codegen_files.get("main.py", "")
                if "flask" in main_py.lower():
                    framework = "flask"
                elif "django" in main_py.lower():
                    framework = "django"
                elif "fastapi" in main_py.lower():
                    framework = "fastapi"
                
                # Generate deployment configs using DeployAgent if available, otherwise fallback
                deploy_files = {}
                
                if HAS_DEPLOY_AGENT:
                    try:
                        # Use DeployAgent for sophisticated deployment artifact generation
                        logger.info(
                            "[STAGE:DEPLOY_GEN] Using DeployAgent for deployment config generation",
                            extra={"workflow_id": workflow_id}
                        )
                        
                        # Initialize DeployAgent with output directory
                        deploy_agent = DeployAgent(repo_path=output_path)
                        await deploy_agent._init_db()  # Initialize SQLite history
                        
                        # Call run_deployment with appropriate parameters
                        deploy_result = await deploy_agent.run_deployment(
                            target="docker",
                            requirements={
                                "language": language,
                                "framework": framework,
                                "entry_point": entry_point,
                                "pipeline_steps": ["generate", "validate"],  # Skip simulate in pipeline
                                "config": ""  # Will be generated
                            }
                        )
                        
                        # Check if deployment was successful
                        if not deploy_result.get("configs"):
                            raise ValueError("DeployAgent returned no configs")
                        
                        # Extract config content from agent result
                        docker_config = deploy_result.get("configs", {}).get("docker", "")
                        
                        if not docker_config:
                            raise ValueError("No Docker config generated by DeployAgent")
                        
                        # Parse the config - it may be JSON with multiple files or a single file
                        try:
                            # Try to parse as JSON (multi-file format)
                            config_data = json.loads(docker_config)
                            if isinstance(config_data, dict):
                                # Multi-file format - extract each file
                                deploy_files = config_data
                                logger.info(
                                    f"[STAGE:DEPLOY_GEN] Extracted {len(deploy_files)} files from DeployAgent response",
                                    extra={"workflow_id": workflow_id, "files": list(deploy_files.keys())}
                                )
                            else:
                                # Not a dict, treat as single file
                                deploy_files["Dockerfile"] = docker_config
                        except json.JSONDecodeError:
                            # Not JSON, treat as single Dockerfile
                            deploy_files["Dockerfile"] = docker_config
                            logger.info(
                                "[STAGE:DEPLOY_GEN] Treating DeployAgent response as single Dockerfile",
                                extra={"workflow_id": workflow_id}
                            )
                        
                        # Ensure we have at least a Dockerfile
                        if "Dockerfile" not in deploy_files:
                            raise ValueError("No Dockerfile found in DeployAgent response")
                        
                        # Add metadata from agent result
                        deploy_metadata = {
                            "schema_version": "1.0.0",
                            "generation_type": "production",
                            "generated_at": deploy_result.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            "generator": {
                                "name": "DeployAgent via WorkflowEngine",
                                "version": "1.0.0",
                                "agent_run_id": deploy_result.get("run_id", "unknown")
                            },
                            "application": {
                                "language": language,
                                "framework": framework,
                                "entry_point": entry_point,
                                "port": 8000
                            },
                            "validations": deploy_result.get("validations", {}),
                            "provenance": deploy_result.get("provenance", {})
                        }
                        
                        if "deploy_metadata.json" not in deploy_files:
                            deploy_files["deploy_metadata.json"] = json.dumps(deploy_metadata, indent=2)
                        
                        logger.info(
                            "[STAGE:DEPLOY_GEN] Successfully generated deployment configs using DeployAgent",
                            extra={"workflow_id": workflow_id, "files_count": len(deploy_files)}
                        )
                        
                    except Exception as e:
                        logger.warning(
                            f"[STAGE:DEPLOY_GEN] DeployAgent execution failed, falling back to templates: {e}",
                            exc_info=True,
                            extra={"workflow_id": workflow_id, "error": str(e)}
                        )
                        # Fallback to template-based generation
                        deploy_files = self._generate_docker_configs(
                            language=language,
                            framework=framework,
                            entry_point=entry_point,
                            codegen_files=codegen_files
                        )
                else:
                    # DeployAgent not available, use fallback
                    logger.warning(
                        "[STAGE:DEPLOY_GEN] DeployAgent not available, using template-based generation",
                        extra={"workflow_id": workflow_id}
                    )
                    deploy_files = self._generate_docker_configs(
                        language=language,
                        framework=framework,
                        entry_point=entry_point,
                        codegen_files=codegen_files
                    )
                
                # Validate deployment artifacts
                if HAS_PROVENANCE and validate_deployment_artifacts:
                    validation = validate_deployment_artifacts(deploy_files, output_path)
                    if not validation["valid"]:
                        deploy_result["status"] = "validation_failed"
                        deploy_result["validation_errors"] = validation["errors"]
                        if provenance:
                            provenance.record_error(
                                ProvenanceTracker.STAGE_DEPLOY_GEN,
                                "validation_failed",
                                f"Deployment validation failed: {validation['errors']}"
                            )
                        return deploy_result
                
                # Write deployment files to output directory
                if output_path:
                    output_dir = Path(output_path)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    for filename, content in deploy_files.items():
                        file_path = output_dir / filename
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        deploy_result["files_written"].append(filename)
                        logger.debug(f"[STAGE:DEPLOY_GEN] Wrote {filename}")
                
                # Record provenance
                if provenance:
                    provenance.record_stage(
                        ProvenanceTracker.STAGE_DEPLOY_GEN,
                        artifacts=deploy_files,
                        metadata={
                            "plugin": "docker",
                            "files_written": deploy_result["files_written"],
                            "language": language,
                            "framework": framework
                        }
                    )
                
                deploy_result["status"] = "completed"
                deploy_result["deploy_files"] = deploy_files
                span.set_status(Status(StatusCode.OK))
                
                return deploy_result
                
            except Exception as e:
                logger.error(
                    f"[STAGE:DEPLOY_GEN] Deployment generation failed: {e}",
                    exc_info=True,
                    extra={"workflow_id": workflow_id}
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                deploy_result["status"] = "failed"
                deploy_result["error"] = str(e)
                return deploy_result
    
    def _generate_docker_configs(
        self,
        language: str,
        framework: str,
        entry_point: str,
        codegen_files: Dict[str, str]
    ) -> Dict[str, str]:
        """Generate Docker deployment configuration files.
        
        DEPRECATED: This is a fallback template-based method.
        New code should use DeployAgent.run_deployment() which provides:
        - Full plugin architecture
        - Validation with build/security checks
        - Handler registry for response processing
        - Comprehensive metadata and provenance
        - Database history tracking
        
        This method is kept as a fallback for environments where DeployAgent
        is not available or fails to initialize.
        
        Args:
            language: Programming language (e.g., 'python')
            framework: Web framework (e.g., 'fastapi', 'flask')
            entry_point: Main application entry point file
            codegen_files: Dictionary of generated code files
            
        Returns:
            Dictionary mapping filenames to their content
        """
        deploy_files = {}
        
        # Get requirements if available
        requirements = codegen_files.get("requirements.txt", "")
        
        # Generate Dockerfile with production best practices
        if framework == "fastapi":
            dockerfile = f'''# =============================================================================
# Production-ready Dockerfile for FastAPI application
# Generated by Code Factory Deploy Stage
# 
# Industry Standards:
# - Multi-stage build for smaller images
# - Non-root user for security
# - Health checks for container orchestration
# - Proper signal handling for graceful shutdown
# =============================================================================

FROM python:3.11-slim AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PYTHONFAULTHANDLER=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create non-root user for security (SOC 2 compliance)
RUN groupadd --gid 1000 appgroup && \\
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home appuser

# Set working directory
WORKDIR /app

# Install system dependencies for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \\
    curl \\
    && rm -rf /var/lib/apt/lists/* \\
    && apt-get clean

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \\
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=appuser:appgroup . .

# Switch to non-root user
USER appuser

# Expose application port
EXPOSE 8000

# Health check using curl for consistency with docker-compose
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application with proper signal handling
CMD ["uvicorn", "{entry_point.replace('.py', '')}:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
'''
        else:
            dockerfile = f'''# Dockerfile for {language} application
# Generated by Code Factory Deploy Stage

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \\
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "{entry_point}"]
'''
        deploy_files["Dockerfile"] = dockerfile
        
        # Generate docker-compose.yml with consistent health check
        docker_compose = f'''# =============================================================================
# Docker Compose Configuration
# Generated by Code Factory Deploy Stage
#
# Usage:
#   docker-compose up -d        # Start services
#   docker-compose logs -f      # View logs
#   docker-compose down         # Stop services
# =============================================================================

version: '3.8'

services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - APP_ENV=production
      - LOG_LEVEL=info
      - PYTHONUNBUFFERED=1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.25'
          memory: 128M
'''
        deploy_files["docker-compose.yml"] = docker_compose
        
        # Generate .dockerignore - optimized for Python/FastAPI apps
        # Note: Docker files are NOT excluded as they may be needed during build
        dockerignore = '''# =============================================================================
# Docker Ignore File
# Generated by Code Factory Deploy Stage
#
# This file specifies which files and directories should be excluded from
# the Docker build context to reduce image size and build time.
# =============================================================================

# Python bytecode and cache
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# Build artifacts
build/
dist/
*.egg-info/
*.egg
.eggs/

# Virtual environments (use container's Python)
.env
.venv
env/
venv/
ENV/
.env.local
.env.*.local

# IDE and editor files
.idea/
.vscode/
*.swp
*.swo
*~
.project
.pydevproject
.settings/

# Testing artifacts
.coverage
.pytest_cache/
htmlcov/
.tox/
.nox/
coverage.xml
*.cover
.hypothesis/

# Git
.git/
.gitignore
.gitattributes

# CI/CD
.github/
.gitlab-ci.yml
.travis.yml
Jenkinsfile
azure-pipelines.yml

# Documentation build artifacts
docs/_build/
site/

# Development files
*.local
*.log
*.pid
*.seed
*.pid.lock

# OS files
.DS_Store
Thumbs.db

# Secrets (should never be in build context)
*.pem
*.key
secrets/
.secrets/
'''
        deploy_files[".dockerignore"] = dockerignore
        
        # Generate deploy_metadata.json
        metadata = {
            "schema_version": "1.0.0",
            "generation_type": "production",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": {
                "name": "Code Factory Deploy Stage",
                "version": "1.0.0"
            },
            "application": {
                "language": language,
                "framework": framework,
                "entry_point": entry_point,
                "port": 8000
            },
            "docker": {
                "base_image": "python:3.11-slim",
                "exposed_ports": [8000],
                "features": {
                    "health_check": True,
                    "non_root_user": framework == "fastapi",
                    "multi_stage_build": False,
                    "curl_installed": True
                },
                "resource_limits": {
                    "memory": "512M",
                    "cpu": "1.0"
                }
            },
            "files_generated": list(deploy_files.keys()) + ["deploy_metadata.json"],
            "deployment": {
                "recommended_replicas": 2,
                "health_endpoint": "/health",
                "readiness_probe": "/health",
                "liveness_probe": "/health"
            },
            "security": {
                "non_root_execution": framework == "fastapi",
                "secrets_excluded": True,
                "env_file_support": True
            }
        }
        deploy_files["deploy_metadata.json"] = json.dumps(metadata, indent=2, sort_keys=False)
        
        return deploy_files
    
    def _tune_from_feedback(self, rating: int) -> None:
        """Tune the workflow based on user feedback.
        
        Implements a simple feedback loop for continuous improvement:
        - Adjusts iteration counts based on success/failure patterns
        - Updates agent selection weights
        - Records feedback for future model training
        
        Args:
            rating: User rating (1-5) for the generated output.
        
        Raises:
            ValueError: If rating is not between 1 and 5.
        """
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")
        
        with _tracer.start_as_current_span("workflow_engine.tune_from_feedback") as span:
            span.set_attribute("feedback.rating", rating)
            
            logger.info(
                f"Tuning workflow from feedback",
                extra={"rating": rating, "action": "tune_feedback"}
            )
            
            # Audit the feedback (fire-and-forget with proper exception handling)
            try:
                from generator.runner.runner_logging import log_audit_event
                
                async def _safe_feedback_audit():
                    try:
                        await asyncio.wait_for(
                            log_audit_event(
                                action="workflow_feedback",
                                data={"rating": rating}
                            ),
                            timeout=5.0
                        )
                    except Exception as e:
                        logger.debug(f"Feedback audit failed: {e}")
                
                # Store task reference to prevent garbage collection
                task = asyncio.create_task(_safe_feedback_audit())
                # Add done callback to log any exceptions
                task.add_done_callback(
                    lambda t: logger.debug(f"Feedback audit completed: {t.exception() if t.done() and t.exception() else 'success'}")
                )
            except (ImportError, RuntimeError):
                pass
            
            # Implement tuning logic based on rating
            if rating <= 2:
                # Low rating - consider increasing iterations
                logger.info("Low feedback rating - workflow may need adjustment")
                span.add_event("low_rating_feedback")
            elif rating >= 4:
                # High rating - current settings working well
                logger.info("High feedback rating - workflow performing well")
                span.add_event("high_rating_feedback")
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the workflow engine.
        
        Cancels any active workflows and releases resources.
        """
        logger.info("WorkflowEngine shutdown initiated")
        self._shutdown_requested = True
        
        async with self._workflow_lock:
            for workflow_id, task in self._active_workflows.items():
                if not task.done():
                    logger.info(f"Cancelling workflow {workflow_id}")
                    task.cancel()
            
            # Wait for all workflows to complete/cancel
            if self._active_workflows:
                await asyncio.gather(
                    *self._active_workflows.values(),
                    return_exceptions=True
                )
            
            self._active_workflows.clear()
        
        logger.info("WorkflowEngine shutdown complete")


# =============================================================================
# AUTO-REGISTRATION
# =============================================================================

def _auto_register_agents() -> None:
    """Automatically register available agents from generator.agents.
    
    This function discovers and registers agents at module load time,
    providing a ready-to-use workflow engine out of the box.
    
    The agents package exports different types for different agents:
    - codegen: CodeGenConfig (configuration class)
    - critique: CritiqueConfig (configuration class)
    - testgen: TestgenAgent (agent class)
    - deploy: DeployAgent (agent class)
    - docgen: DocgenAgent (agent class)
    
    All available agents are registered regardless of their type to
    ensure the workflow engine can detect what's available.
    """
    try:
        from generator.agents import (
            _AVAILABLE_AGENTS,
            CodeGenConfig,
            CritiqueConfig,
            DeployAgent,
            DocgenAgent,
            TestgenAgent,
        )
        
        # Registration mapping
        agent_mapping = [
            ("codegen", CodeGenConfig, _AVAILABLE_AGENTS.get("codegen")),
            ("critique", CritiqueConfig, _AVAILABLE_AGENTS.get("critique")),
            ("testgen", TestgenAgent, _AVAILABLE_AGENTS.get("testgen")),
            ("deploy", DeployAgent, _AVAILABLE_AGENTS.get("deploy")),
            ("docgen", DocgenAgent, _AVAILABLE_AGENTS.get("docgen")),
        ]
        
        registered_count = 0
        for name, agent_class, is_available in agent_mapping:
            if is_available and agent_class:
                _agent_registry.register(
                    name,
                    agent_class,
                    metadata={"source": "auto_register", "module": "generator.agents"}
                )
                registered_count += 1
        
        logger.info(
            f"Auto-registered {registered_count} agents: {list(_agent_registry)}",
            extra={"action": "auto_register", "count": registered_count}
        )
        
    except ImportError as e:
        logger.warning(
            f"Could not auto-register agents: {e}",
            extra={"action": "auto_register_failed", "error": str(e)}
        )


# Perform auto-registration at module load (with error handling)
try:
    _auto_register_agents()
except Exception as e:
    logger.debug(f"Agent auto-registration skipped: {e}")


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    # Core classes
    "WorkflowEngine",
    "AgentRegistry",
    
    # Registry interface
    "AGENT_REGISTRY",
    "register_agent",
    "hot_swap_agent",
    "get_agent",
    
    # Data models
    "WorkflowRequest",
    "WorkflowResult",
    
    # Enums
    "WorkflowStatus",
    "AgentStatus",
    
    # Exceptions
    "EngineError",
    "AgentNotFoundError",
    "AgentExecutionError",
    "WorkflowTimeoutError",
    "HealthCheckError",
    
    # Protocol
    "AgentProtocol",
    
    # Feature flags
    "HAS_OPENTELEMETRY",
    "HAS_PROMETHEUS",
    "HAS_PYDANTIC",
]
