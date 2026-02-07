# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified Audit Event Schema
===========================

Canonical Pydantic models for audit events across all platform modules.
Provides a single source of truth for audit event structure, ensuring 
consistency across Generator, Arbiter, Test Generation, Simulation, 
OmniCore, and Guardrails modules.

Usage:
    from self_fixing_engineer.arbiter.audit_schema import AuditEvent, AuditRouter
    
    # Create audit event
    event = AuditEvent(
        event_type="code_generation_started",
        module="generator",
        actor="user@example.com",
        resource_id="job-123",
        metadata={"language": "python"}
    )
    
    # Route to appropriate backend
    router = AuditRouter()
    await router.route_event(event)
"""

import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

try:
    from pydantic import BaseModel, Field, validator
    PYDANTIC_AVAILABLE = True
except ImportError:
    # Fallback for environments without Pydantic
    PYDANTIC_AVAILABLE = False
    BaseModel = object
    Field = lambda *args, **kwargs: None
    validator = lambda *args, **kwargs: lambda f: f

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Standard audit event types across all modules."""
    
    # Generator events
    CODE_GENERATION_STARTED = "code_generation_started"
    CODE_GENERATION_COMPLETED = "code_generation_completed"
    CODE_GENERATION_FAILED = "code_generation_failed"
    CRITIQUE_COMPLETED = "critique_completed"
    TEST_GENERATION_COMPLETED = "test_generation_completed"
    DEPLOYMENT_STARTED = "deployment_started"
    DEPLOYMENT_COMPLETED = "deployment_completed"
    
    # Arbiter events
    POLICY_CHECK = "policy_check"
    POLICY_VIOLATION = "policy_violation"
    CONSTITUTION_CHECK = "constitution_check"
    CONSTITUTION_VIOLATION = "constitution_violation"
    BUG_DETECTED = "bug_detected"
    BUG_FIXED = "bug_fixed"
    LEARNING_EVENT = "learning_event"
    EVOLUTION_CYCLE = "evolution_cycle"
    
    # Test generation events
    TEST_EXECUTION_STARTED = "test_execution_started"
    TEST_EXECUTION_COMPLETED = "test_execution_completed"
    TEST_FAILED = "test_failed"
    COVERAGE_UPDATED = "coverage_updated"
    
    # Simulation events
    SIMULATION_STARTED = "simulation_started"
    SIMULATION_COMPLETED = "simulation_completed"
    AGENT_ACTION = "agent_action"
    ANOMALY_DETECTED = "anomaly_detected"
    
    # OmniCore events
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_FAILED = "plugin_failed"
    
    # Guardrails events
    COMPLIANCE_CHECK = "compliance_check"
    COMPLIANCE_VIOLATION = "compliance_violation"
    SECURITY_SCAN = "security_scan"
    SECURITY_ALERT = "security_alert"
    
    # Human-in-loop events
    HITL_REQUEST = "hitl_request"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"
    
    # Generic events
    SYSTEM_ERROR = "system_error"
    ACCESS_DENIED = "access_denied"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditModule(str, Enum):
    """Platform modules that generate audit events."""
    GENERATOR = "generator"
    ARBITER = "arbiter"
    TEST_GENERATION = "test_generation"
    SIMULATION = "simulation"
    OMNICORE = "omnicore"
    GUARDRAILS = "guardrails"
    SERVER = "server"
    MESH = "mesh"


if PYDANTIC_AVAILABLE:
    class AuditEvent(BaseModel):
        """
        Canonical audit event model.
        
        All audit events across the platform should conform to this schema.
        """
        
        # Core fields
        event_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique event identifier")
        event_type: str = Field(..., description="Type of event (use AuditEventType enum values)")
        timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When the event occurred")
        
        # Context fields
        module: str = Field(..., description="Module that generated the event (use AuditModule enum values)")
        severity: str = Field(default="info", description="Event severity (use AuditSeverity enum values)")
        
        # Actor fields
        actor: Optional[str] = Field(None, description="User, service, or agent that triggered the event")
        actor_type: Optional[str] = Field(None, description="Type of actor: user, service, agent, system")
        
        # Resource fields
        resource_id: Optional[str] = Field(None, description="ID of the resource affected (job_id, arbiter_id, etc)")
        resource_type: Optional[str] = Field(None, description="Type of resource: job, workflow, policy, agent")
        
        # Result fields
        status: Optional[str] = Field(None, description="Event status: success, failure, pending, denied")
        message: Optional[str] = Field(None, description="Human-readable event description")
        
        # Additional context
        metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional event-specific data")
        
        # Tracing
        correlation_id: Optional[str] = Field(None, description="ID for correlating related events")
        parent_event_id: Optional[str] = Field(None, description="ID of parent event in causal chain")
        trace_id: Optional[str] = Field(None, description="OpenTelemetry trace ID")
        span_id: Optional[str] = Field(None, description="OpenTelemetry span ID")
        
        # Source tracking
        hostname: Optional[str] = Field(default_factory=lambda: os.getenv("HOSTNAME", "unknown"), description="Host that generated the event")
        process_id: Optional[int] = Field(default_factory=os.getpid, description="Process ID that generated the event")
        
        class Config:
            json_encoders = {
                datetime: lambda v: v.isoformat(),
            }
        
        @validator('timestamp', pre=True)
        def ensure_timezone(cls, v):
            """Ensure timestamp has timezone info."""
            if isinstance(v, datetime) and v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        
        def to_dict(self) -> Dict[str, Any]:
            """Convert to dictionary with ISO timestamps."""
            return self.dict()
        
        @classmethod
        def from_legacy_format(cls, legacy_dict: Dict[str, Any], module: str) -> 'AuditEvent':
            """
            Convert legacy audit log formats to unified schema.
            
            Args:
                legacy_dict: Legacy audit log entry
                module: Module that generated the legacy event
                
            Returns:
                AuditEvent instance
            """
            # Map common legacy field names to canonical names
            event_type = legacy_dict.get('event_type') or legacy_dict.get('type') or legacy_dict.get('event')
            timestamp = legacy_dict.get('timestamp') or legacy_dict.get('time') or legacy_dict.get('created_at')
            
            # Parse timestamp if string
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    timestamp = datetime.now(timezone.utc)
            elif timestamp is None:
                timestamp = datetime.now(timezone.utc)
            
            return cls(
                event_type=str(event_type),
                module=module,
                timestamp=timestamp,
                severity=legacy_dict.get('severity', 'info'),
                actor=legacy_dict.get('actor') or legacy_dict.get('user') or legacy_dict.get('service'),
                resource_id=legacy_dict.get('resource_id') or legacy_dict.get('job_id') or legacy_dict.get('id'),
                status=legacy_dict.get('status') or legacy_dict.get('result'),
                message=legacy_dict.get('message') or legacy_dict.get('description'),
                metadata=legacy_dict.get('metadata') or legacy_dict.get('data') or legacy_dict.get('details') or {},
                correlation_id=legacy_dict.get('correlation_id') or legacy_dict.get('request_id'),
                trace_id=legacy_dict.get('trace_id'),
                span_id=legacy_dict.get('span_id'),
            )

else:
    # Fallback class when Pydantic not available
    class AuditEvent:
        """Fallback audit event class without Pydantic validation."""
        
        def __init__(self, event_type: str, module: str, **kwargs):
            self.event_id = kwargs.get('event_id', str(uuid4()))
            self.event_type = event_type
            self.module = module
            self.timestamp = kwargs.get('timestamp', datetime.now(timezone.utc))
            self.severity = kwargs.get('severity', 'info')
            self.actor = kwargs.get('actor')
            self.resource_id = kwargs.get('resource_id')
            self.status = kwargs.get('status')
            self.message = kwargs.get('message')
            self.metadata = kwargs.get('metadata', {})
            self.correlation_id = kwargs.get('correlation_id')
            self.trace_id = kwargs.get('trace_id')
            self.span_id = kwargs.get('span_id')
            self.hostname = kwargs.get('hostname', os.getenv("HOSTNAME", "unknown"))
            self.process_id = kwargs.get('process_id', os.getpid())
        
        def to_dict(self) -> Dict[str, Any]:
            """Convert to dictionary."""
            return {
                'event_id': self.event_id,
                'event_type': self.event_type,
                'module': self.module,
                'timestamp': self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp,
                'severity': self.severity,
                'actor': self.actor,
                'resource_id': self.resource_id,
                'status': self.status,
                'message': self.message,
                'metadata': self.metadata,
                'correlation_id': self.correlation_id,
                'trace_id': self.trace_id,
                'span_id': self.span_id,
                'hostname': self.hostname,
                'process_id': self.process_id,
            }
        
        @classmethod
        def from_legacy_format(cls, legacy_dict: Dict[str, Any], module: str):
            """Convert legacy format to AuditEvent."""
            event_type = legacy_dict.get('event_type') or legacy_dict.get('type') or legacy_dict.get('event')
            return cls(
                event_type=str(event_type),
                module=module,
                timestamp=legacy_dict.get('timestamp'),
                severity=legacy_dict.get('severity', 'info'),
                actor=legacy_dict.get('actor'),
                resource_id=legacy_dict.get('resource_id'),
                status=legacy_dict.get('status'),
                message=legacy_dict.get('message'),
                metadata=legacy_dict.get('metadata', {}),
                correlation_id=legacy_dict.get('correlation_id'),
            )


class AuditRouter:
    """
    Routes audit events to appropriate backends.
    
    Accepts events in the unified AuditEvent schema and routes them to 
    the appropriate storage backend (database, file, message queue, etc).
    """
    
    def __init__(self):
        self.backends = []
        logger.info("AuditRouter initialized")
    
    def register_backend(self, backend: Any, name: str = None):
        """
        Register an audit backend.
        
        Args:
            backend: Object with write_event(event: AuditEvent) method
            name: Optional backend name for logging
        """
        backend_name = name or getattr(backend, '__class__.__name__', 'UnknownBackend')
        self.backends.append({'name': backend_name, 'backend': backend})
        logger.info(f"Registered audit backend: {backend_name}")
    
    async def route_event(self, event: AuditEvent) -> Dict[str, Any]:
        """
        Route an audit event to all registered backends.
        
        Args:
            event: AuditEvent to route
            
        Returns:
            Dict with routing results
        """
        results = {
            'event_id': event.event_id if hasattr(event, 'event_id') else 'unknown',
            'backends_attempted': len(self.backends),
            'backends_succeeded': 0,
            'backends_failed': 0,
            'errors': []
        }
        
        for backend_info in self.backends:
            backend_name = backend_info['name']
            backend = backend_info['backend']
            
            try:
                # Try async method first
                if hasattr(backend, 'write_event'):
                    if asyncio.iscoroutinefunction(backend.write_event):
                        await backend.write_event(event)
                    else:
                        backend.write_event(event)
                    results['backends_succeeded'] += 1
                    logger.debug(f"Routed event {event.event_id if hasattr(event, 'event_id') else 'unknown'} to {backend_name}")
                else:
                    logger.warning(f"Backend {backend_name} has no write_event method")
                    results['backends_failed'] += 1
                    results['errors'].append(f"{backend_name}: No write_event method")
                    
            except Exception as e:
                logger.error(f"Failed to route event to {backend_name}: {e}", exc_info=True)
                results['backends_failed'] += 1
                results['errors'].append(f"{backend_name}: {str(e)}")
        
        return results
    
    def route_event_sync(self, event: AuditEvent) -> Dict[str, Any]:
        """
        Synchronous version of route_event.
        
        Args:
            event: AuditEvent to route
            
        Returns:
            Dict with routing results
        """
        results = {
            'event_id': event.event_id if hasattr(event, 'event_id') else 'unknown',
            'backends_attempted': len(self.backends),
            'backends_succeeded': 0,
            'backends_failed': 0,
            'errors': []
        }
        
        for backend_info in self.backends:
            backend_name = backend_info['name']
            backend = backend_info['backend']
            
            try:
                if hasattr(backend, 'write_event'):
                    backend.write_event(event)
                    results['backends_succeeded'] += 1
                else:
                    results['backends_failed'] += 1
                    results['errors'].append(f"{backend_name}: No write_event method")
            except Exception as e:
                logger.error(f"Failed to route event to {backend_name}: {e}", exc_info=True)
                results['backends_failed'] += 1
                results['errors'].append(f"{backend_name}: {str(e)}")
        
        return results


# Convenience function for quick audit event creation
def create_audit_event(
    event_type: str,
    module: str,
    message: str = None,
    **kwargs
) -> AuditEvent:
    """
    Convenience function to create an audit event.
    
    Args:
        event_type: Type of event (use AuditEventType enum)
        module: Module generating event (use AuditModule enum)
        message: Human-readable message
        **kwargs: Additional fields (severity, actor, resource_id, etc)
        
    Returns:
        AuditEvent instance
    """
    return AuditEvent(
        event_type=event_type,
        module=module,
        message=message,
        **kwargs
    )


# Import asyncio at module level
import asyncio
