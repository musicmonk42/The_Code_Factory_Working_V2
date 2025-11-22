"""
audit_log.py - Audit logging adapter for simulation module

This module provides a compatibility layer for audit logging.
It wraps the actual audit logger from the guardrails module.
"""
import logging

logger = logging.getLogger(__name__)

try:
    from self_fixing_engineer.guardrails.audit_log import AuditLogger as _BaseAuditLogger
    
    # Re-export the AuditLogger for backward compatibility
    AuditLogger = _BaseAuditLogger
    
except ImportError:
    logger.warning("guardrails.audit_log not available, using fallback logger")
    
    # Fallback audit logger implementation
    class AuditLogger:
        """Fallback audit logger that uses standard logging."""
        
        def __init__(self, *args, **kwargs):
            self.logger = logging.getLogger("simulation.audit")
        
        def log(self, message: str, level: str = "INFO", **kwargs):
            """Log an audit message."""
            log_level = getattr(logging, level.upper(), logging.INFO)
            self.logger.log(log_level, f"AUDIT: {message}", extra=kwargs)
        
        def emit_audit_event(self, event_type: str, details: dict, severity: str = "INFO"):
            """Emit an audit event."""
            log_level = getattr(logging, severity.upper(), logging.INFO)
            self.logger.log(
                log_level,
                f"AUDIT_EVENT: {event_type}",
                extra={"details": details}
            )


__all__ = ["AuditLogger"]
