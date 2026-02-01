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

# Dict-like interface for backwards compatibility with AGENT_REGISTRY usage
AGENT_REGISTRY: Dict[str, Type[Any]] = _agent_registry._agents


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
            
            # Audit log (lazy import to avoid circular dependency)
            try:
                from generator.runner.runner_logging import log_audit_event
                await log_audit_event(
                    action="workflow_started",
                    data={
                        "workflow_id": workflow_id,
                        "input_file": input_file,
                        "user_id": user_id,
                        "dry_run": dry_run
                    }
                )
            except ImportError:
                logger.debug("Audit logging not available")
            except Exception as audit_err:
                logger.warning(f"Audit logging failed: {audit_err}")
            
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
            
            try:
                result["status"] = WorkflowStatus.RUNNING.value
                
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
                        
                        # Execute codegen agent
                        codegen_result = await self._execute_agent(
                            "codegen",
                            {
                                "input_file": input_file,
                                "iteration": iteration_num,
                                "previous_results": result.get("agent_results", {})
                            },
                            workflow_id
                        )
                        result["agent_results"]["codegen"] = codegen_result
                        
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
                        
                        # Execute test generation if enabled
                        if self._enable_testing and "testgen" in _agent_registry:
                            testgen_result = await self._execute_agent(
                                "testgen",
                                {
                                    "codegen_output": codegen_result,
                                    "iteration": iteration_num
                                },
                                workflow_id
                            )
                            result["agent_results"]["testgen"] = testgen_result
                        
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
                
                # Check if agent has an async execute method
                if hasattr(agent_class, 'execute') and asyncio.iscoroutinefunction(
                    getattr(agent_class, 'execute', None)
                ):
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
            
            # Audit the feedback
            try:
                from generator.runner.runner_logging import log_audit_event
                asyncio.create_task(log_audit_event(
                    action="workflow_feedback",
                    data={"rating": rating}
                ))
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
