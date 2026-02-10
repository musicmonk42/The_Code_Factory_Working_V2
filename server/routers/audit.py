# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified Audit Log Router
========================

Provides centralized access to audit logs from all platform modules:
- Generator (generator/audit_log/)
- Arbiter (self_fixing_engineer/arbiter/audit_log.py)
- Test Generation (self_fixing_engineer/test_generation/orchestrator/audit.py)
- Simulation (self_fixing_engineer/simulation/agentic.py)
- OmniCore (omnicore_engine/audit.py)
- Guardrails (self_fixing_engineer/guardrails/audit_log.py)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from server.services.generator_service import GeneratorService, get_generator_service
from server.services.omnicore_service import OmniCoreService, get_omnicore_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get("/logs/all")
async def query_all_audit_logs(
    module: Optional[str] = Query(None, description="Filter by module: generator, arbiter, testgen, simulation, omnicore, guardrails"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
    start_time: Optional[str] = Query(None, description="ISO 8601 start timestamp"),
    end_time: Optional[str] = Query(None, description="ISO 8601 end timestamp"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results per module"),
    generator_service: GeneratorService = Depends(get_generator_service),
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
) -> Dict[str, Any]:
    """
    Query audit logs from all platform modules.
    
    Returns a unified audit trail aggregated from:
    - Generator module (code generation, tests, docs, deployment)
    - Arbiter module (bug detection, fixes, learning, policy)
    - Test Generation module (test creation, execution, coverage)
    - Simulation module (agent decisions, simulations, anomalies)
    - OmniCore module (workflow orchestration, plugins, errors)
    - Guardrails module (compliance checks, policy violations)
    
    **Query Parameters:**
    - module: Filter by specific module (optional)
    - event_type: Filter by event type (optional)
    - job_id: Filter by job ID (optional)
    - start_time: Filter by start timestamp (optional)
    - end_time: Filter by end timestamp (optional)
    - limit: Max results per module (default: 100, max: 1000)
    
    **Returns:**
    - aggregated_logs: Combined list of all audit entries
    - total_count: Total number of entries returned
    - modules_queried: List of modules that were queried
    - metadata: Query parameters and timestamps
    """
    logger.info(f"Querying unified audit logs: module={module}, event_type={event_type}, job_id={job_id}")
    
    aggregated_logs = []
    modules_queried = []
    errors = {}
    
    # Define which modules to query
    modules_to_query = []
    if module:
        modules_to_query = [module]
    else:
        modules_to_query = ["generator", "arbiter", "testgen", "simulation", "omnicore", "guardrails"]
    
    # 1. Query Generator Audit Logs
    if "generator" in modules_to_query:
        try:
            modules_queried.append("generator")
            generator_logs = await _query_generator_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in generator_logs:
                log["module"] = "generator"
                aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying generator audit logs: {e}", exc_info=True)
            errors["generator"] = str(e)
    
    # 2. Query Arbiter Audit Logs
    if "arbiter" in modules_to_query:
        try:
            modules_queried.append("arbiter")
            arbiter_logs = await _query_arbiter_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in arbiter_logs:
                log["module"] = "arbiter"
                aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying arbiter audit logs: {e}", exc_info=True)
            errors["arbiter"] = str(e)
    
    # 3. Query Test Generation Audit Logs
    if "testgen" in modules_to_query:
        try:
            modules_queried.append("testgen")
            testgen_logs = await _query_testgen_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in testgen_logs:
                log["module"] = "testgen"
                aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying testgen audit logs: {e}", exc_info=True)
            errors["testgen"] = str(e)
    
    # 4. Query Simulation Audit Logs
    if "simulation" in modules_to_query:
        try:
            modules_queried.append("simulation")
            simulation_logs = await _query_simulation_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in simulation_logs:
                log["module"] = "simulation"
                aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying simulation audit logs: {e}", exc_info=True)
            errors["simulation"] = str(e)
    
    # 5. Query OmniCore Audit Logs
    if "omnicore" in modules_to_query:
        try:
            modules_queried.append("omnicore")
            
            # Get all jobs if no specific job_id
            if job_id:
                job_ids = [job_id]
            else:
                # Query recent jobs (limit to 10 for performance)
                job_ids = ["system"]  # Placeholder - in production, query actual job IDs
            
            for jid in job_ids:
                omnicore_logs = await omnicore_service.get_audit_trail(job_id=jid, limit=limit)
                for log in omnicore_logs:
                    log["module"] = "omnicore"
                    aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying omnicore audit logs: {e}", exc_info=True)
            errors["omnicore"] = str(e)
    
    # 6. Query Guardrails Audit Logs
    if "guardrails" in modules_to_query:
        try:
            modules_queried.append("guardrails")
            guardrails_logs = await _query_guardrails_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in guardrails_logs:
                log["module"] = "guardrails"
                aggregated_logs.append(log)
                
        except Exception as e:
            logger.error(f"Error querying guardrails audit logs: {e}", exc_info=True)
            errors["guardrails"] = str(e)
    
    # Sort all logs by timestamp (newest first)
    # Use "or" to handle None values in addition to missing keys
    aggregated_logs.sort(
        key=lambda x: x.get("timestamp") or "1970-01-01T00:00:00Z",
        reverse=True
    )
    
    # Apply global limit
    aggregated_logs = aggregated_logs[:limit]
    
    return {
        "aggregated_logs": aggregated_logs,
        "total_count": len(aggregated_logs),
        "modules_queried": modules_queried,
        "errors": errors if errors else None,
        "metadata": {
            "query_timestamp": datetime.now(timezone.utc).isoformat(),
            "module_filter": module,
            "event_type_filter": event_type,
            "job_id_filter": job_id,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        }
    }


async def _query_generator_audit_logs(
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query Generator audit logs from the audit_log module."""
    try:
        # Import generator's AUDIT_LOG singleton
        from generator.audit_log.audit_log import AUDIT_LOG
        
        # Try to get the log file path from the backend
        if hasattr(AUDIT_LOG, 'backend') and hasattr(AUDIT_LOG.backend, 'log_file'):
            log_file = Path(AUDIT_LOG.backend.log_file)
        else:
            # Fallback: try common default paths
            default_paths = [
                Path("logs/generator_audit.jsonl"),
                Path("generator/logs/audit_log.jsonl"),
                Path("audit_log.jsonl"),
            ]
            log_file = None
            for path in default_paths:
                if path.exists():
                    log_file = path
                    break
            
            if not log_file:
                logger.warning("Generator audit log file not found")
                return []
        
        if not log_file.exists():
            logger.warning(f"Generator audit log file not found: {log_file}")
            return []
        
        logs = []
        with open(log_file, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    
                    # Apply filters
                    # Check event_type (could be in 'action' or 'event_type' field)
                    entry_event_type = entry.get("event_type") or entry.get("action")
                    if event_type and entry_event_type != event_type:
                        continue
                    
                    # Check job_id (could be in details or top-level)
                    entry_job_id = entry.get("job_id") or entry.get("details", {}).get("job_id")
                    if job_id and str(entry_job_id) != job_id:
                        continue
                    
                    # Parse timestamp for range filtering
                    entry_time = entry.get("timestamp")
                    if start_time and entry_time and entry_time < start_time:
                        continue
                    if end_time and entry_time and entry_time > end_time:
                        continue
                    
                    # Normalize to standardized format
                    logs.append({
                        "timestamp": entry.get("timestamp"),
                        "event_type": entry_event_type,
                        "job_id": entry_job_id,
                        "action": entry.get("action") if "action" in entry else entry_event_type,
                        "user": entry.get("user", entry.get("user_id", "system")),
                        "status": entry.get("status", "success"),
                        "details": entry.get("details", {}),
                    })
                    
                    if len(logs) >= limit:
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return logs
        
    except ImportError:
        logger.warning("Generator audit logger not available")
        return []
    except Exception as e:
        logger.error(f"Error reading generator audit logs: {e}", exc_info=True)
        return []


async def _query_arbiter_audit_logs(
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query Arbiter audit logs from file-based storage."""
    try:
        # Import Arbiter audit logger
        from self_fixing_engineer.arbiter.audit_log import TamperEvidentLogger, AuditLoggerConfig
        
        # Get singleton instance (will create with default config if needed)
        audit_logger = TamperEvidentLogger.get_instance()
        
        # Get log path from config (default: ./logs/audit_log.jsonl)
        if hasattr(audit_logger, 'config') and hasattr(audit_logger.config, 'log_path'):
            log_path = audit_logger.config.log_path
        elif hasattr(audit_logger, 'log_path'):
            log_path = audit_logger.log_path
        else:
            # Use default from AuditLoggerConfig
            default_config = AuditLoggerConfig()
            log_path = default_config.log_path
        
        if not Path(log_path).exists():
            logger.warning(f"Arbiter audit log file not found: {log_path}")
            return []
        
        logs = []
        with open(log_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    
                    # Apply filters
                    if event_type and entry.get("event_type") != event_type:
                        continue
                    if job_id and job_id not in entry.get("details", {}).get("job_id", ""):
                        continue
                    
                    # Parse timestamp for range filtering
                    entry_time = entry.get("timestamp")
                    if start_time and entry_time and entry_time < start_time:
                        continue
                    if end_time and entry_time and entry_time > end_time:
                        continue
                    
                    logs.append({
                        "timestamp": entry.get("timestamp"),
                        "event_type": entry.get("event_type"),
                        "job_id": entry.get("details", {}).get("job_id"),
                        "action": entry.get("event_type"),
                        "user": entry.get("user_id", "system"),
                        "status": "success",  # Arbiter doesn't track status explicitly
                        "details": entry.get("details", {}),
                        "hash": entry.get("current_hash"),
                        "signature": entry.get("signature"),
                    })
                    
                    if len(logs) >= limit:
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return logs
        
    except ImportError:
        logger.warning("Arbiter audit logger not available")
        return []
    except Exception as e:
        logger.error(f"Error reading arbiter audit logs: {e}", exc_info=True)
        return []


async def _query_testgen_audit_logs(
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query Test Generation audit logs."""
    try:
        # Try to get log path from test generation config
        try:
            from self_fixing_engineer.test_generation.orchestrator.audit import _get_audit_log_file
            log_path_str = _get_audit_log_file()
            if log_path_str:
                log_path = Path(log_path_str)
            else:
                # Fallback to default
                log_path = Path("atco_artifacts/atco_audit.log")
        except (ImportError, AttributeError):
            # Fallback to default location
            log_path = Path("atco_artifacts/atco_audit.log")
        
        if not log_path.exists():
            logger.warning(f"Test generation audit log not found: {log_path}")
            return []
        
        logs = []
        with open(log_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    
                    # Apply filters
                    if event_type and entry.get("event") != event_type:
                        continue
                    if job_id and job_id not in str(entry.get("run_id", "")):
                        continue
                    
                    # Parse timestamp
                    entry_time = entry.get("timestamp_iso", entry.get("timestamp"))
                    if start_time and entry_time and entry_time < start_time:
                        continue
                    if end_time and entry_time and entry_time > end_time:
                        continue
                    
                    logs.append({
                        "timestamp": entry_time,
                        "event_type": entry.get("event"),
                        "job_id": entry.get("run_id"),
                        "action": entry.get("event"),
                        "user": "testgen_system",
                        "status": entry.get("status", "success"),
                        "details": {k: v for k, v in entry.items() if k not in ["event", "timestamp", "timestamp_iso", "run_id"]},
                    })
                    
                    if len(logs) >= limit:
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return logs
        
    except Exception as e:
        logger.error(f"Error reading testgen audit logs: {e}", exc_info=True)
        return []


async def _query_simulation_audit_logs(
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query Simulation/Agentic audit logs."""
    try:
        # Simulation uses guardrails audit logger, try both locations
        log_paths = [
            Path("agentic_audit.log"),  # Old/direct location
            Path("simulation/results/audit_trail.log"),  # From guardrails default
        ]
        
        log_path = None
        for path in log_paths:
            if path.exists():
                log_path = path
                break
        
        if not log_path:
            logger.warning(f"Simulation audit log not found in: {[str(p) for p in log_paths]}")
            return []
        
        logs = []
        with open(log_path, 'r') as f:
            for line in f:
                try:
                    signed_entry = json.loads(line.strip())
                    entry = signed_entry.get("event", {})
                    
                    # Apply filters
                    if event_type and entry.get("event_type") != event_type:
                        continue
                    if job_id and job_id not in str(entry.get("payload", {})):
                        continue
                    
                    entry_time = entry.get("timestamp")
                    if start_time and entry_time and entry_time < start_time:
                        continue
                    if end_time and entry_time and entry_time > end_time:
                        continue
                    
                    logs.append({
                        "timestamp": entry_time,
                        "event_type": entry.get("event_type"),
                        "job_id": entry.get("event_id"),
                        "action": entry.get("event_type"),
                        "user": "simulation_system",
                        "status": "success",
                        "details": entry.get("payload", {}),
                        "signature": signed_entry.get("signature"),
                    })
                    
                    if len(logs) >= limit:
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return logs
        
    except Exception as e:
        logger.error(f"Error reading simulation audit logs: {e}", exc_info=True)
        return []


async def _query_guardrails_audit_logs(
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query Guardrails compliance audit logs."""
    try:
        from self_fixing_engineer.guardrails.audit_log import AuditLogger
        
        # Get audit logger instance  
        audit_logger = AuditLogger.from_environment()
        
        # Get log path (default: simulation/results/audit_trail.log)
        if hasattr(audit_logger, 'log_path'):
            log_path = audit_logger.log_path
        else:
            # Fallback to default from MockConfig
            import os
            log_path = os.environ.get("AUDIT_LOG_PATH", "simulation/results/audit_trail.log")
        
        if not Path(log_path).exists():
            logger.warning(f"Guardrails audit log not found: {log_path}")
            return []
        
        logs = []
        with open(log_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    
                    # Apply filters
                    if event_type and entry.get("event_type") != event_type:
                        continue
                    
                    entry_time = entry.get("timestamp")
                    if start_time and entry_time and entry_time < start_time:
                        continue
                    if end_time and entry_time and entry_time > end_time:
                        continue
                    
                    logs.append({
                        "timestamp": entry_time,
                        "event_type": entry.get("event_type"),
                        "job_id": entry.get("correlation_id"),
                        "action": entry.get("name", entry.get("event_type")),
                        "user": entry.get("agent_id", "compliance_system"),
                        "status": entry.get("detail", {}).get("status", "success"),
                        "details": entry.get("detail", {}),
                        "hash": entry.get("hash"),
                    })
                    
                    if len(logs) >= limit:
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return logs
        
    except ImportError:
        logger.warning("Guardrails audit logger not available")
        return []
    except Exception as e:
        logger.error(f"Error reading guardrails audit logs: {e}", exc_info=True)
        return []


@router.get("/logs/event-types")
async def get_all_event_types() -> Dict[str, Any]:
    """
    Get a list of all available audit event types across all modules.
    
    **Returns:**
    - event_types_by_module: Dictionary mapping module names to their event types
    - total_event_types: Total count of unique event types
    - all_event_types_sorted: Sorted list of all unique event types
    """
    event_types = {
        "generator": [
            "code_generated",
            "test_generated",
            "doc_generated",
            "deployment",
            "error",
            "crypto_operation",
            "plugin_invocation",
            "rbac_denial",
            "tamper_detection",
        ],
        "arbiter": [
            "codebase_scan",
            "bug_detection",
            "fix_proposal",
            "fix_application",
            "learning_update",
            "policy_enforcement",
            "key_management",
            "self_audit",
            "explore_and_fix",
            "auto_optimize",
        ],
        "testgen": [
            "test_generation_start",
            "test_execution_complete",
            "test_failure",
            "coverage_analysis",
            "spec_parsing",
            "feedback_received",
        ],
        "simulation": [
            "agent_decision",
            "policy_decision",
            "simulation_start",
            "simulation_complete",
            "anomaly_detected",
        ],
        "omnicore": [
            "code_factory_workflow_start",
            "plugin_installed",
            "fix_applied",
            "error_logged",
            "metric_collected",
        ],
        "guardrails": [
            "compliance_check",
            "gap_detected",
            "enforcement_failure",
            "policy_violation",
            "key_rotation",
        ],
    }
    
    all_types = set()
    for types_list in event_types.values():
        all_types.update(types_list)
    
    return {
        "event_types_by_module": event_types,
        "total_event_types": len(all_types),
        "all_event_types_sorted": sorted(list(all_types)),
    }


@router.get("/config/status")
async def get_audit_config_status() -> Dict[str, Any]:
    """
    Get audit log configuration status and information.
    
    Returns detailed information about the audit logging configuration including:
    - Configuration source (YAML file or environment variables)
    - Active configuration values (non-sensitive)
    - Security status (encryption, RBAC, immutability)
    - Backend configuration
    - Compliance mode
    - Configuration validation status
    
    **Returns:**
    - config_source: Where configuration is loaded from
    - backend: Backend configuration details
    - security: Security feature status
    - compliance: Compliance mode and settings
    - performance: Performance-related settings
    - validation: Configuration validation results
    """
    import os
    import yaml
    from pathlib import Path
    
    logger.info("Fetching audit configuration status")
    
    config_info = {
        "config_source": "environment_variables",
        "config_file": None,
        "backend": {},
        "security": {},
        "compliance": {},
        "performance": {},
        "validation": {},
        "features": {},
    }
    
    # Check if YAML config file exists
    possible_config_paths = [
        "generator/audit_config.yaml",
        "generator/audit_config.production.yaml",
        "generator/audit_config.development.yaml",
    ]
    
    config_file_path = None
    for config_path in possible_config_paths:
        full_path = Path(config_path)
        if full_path.exists():
            config_file_path = config_path
            config_info["config_source"] = "yaml_file"
            config_info["config_file"] = config_path
            break
    
    # Load configuration from file if it exists
    config_data = {}
    if config_file_path:
        try:
            with open(config_file_path, 'r') as f:
                config_data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Error loading config file {config_file_path}: {e}")
            config_info["config_file_error"] = str(e)
    
    # Get configuration values (environment variables take precedence)
    def get_config(key: str, default=None):
        """Get config value with environment variable precedence"""
        # Map config keys to environment variable names
        env_mappings = {
            "BACKEND_TYPE": "AUDIT_LOG_BACKEND_TYPE",
            "PROVIDER_TYPE": "AUDIT_CRYPTO_PROVIDER_TYPE",
            "DEFAULT_ALGO": "AUDIT_CRYPTO_DEFAULT_ALGO",
            "COMPRESSION_ALGO": "AUDIT_COMPRESSION_ALGO",
            "BATCH_FLUSH_INTERVAL": "AUDIT_BATCH_FLUSH_INTERVAL",
            "IMMUTABLE": "AUDIT_LOG_IMMUTABLE",
            "ENCRYPTION_ENABLED": "AUDIT_ENCRYPTION_ENABLED",
            "TAMPER_DETECTION_ENABLED": "AUDIT_TAMPER_DETECTION_ENABLED",
            "RBAC_ENABLED": "AUDIT_RBAC_ENABLED",
            "COMPLIANCE_MODE": "AUDIT_COMPLIANCE_MODE",
            "DEV_MODE": "AUDIT_LOG_DEV_MODE",
            "CRYPTO_MODE": "AUDIT_CRYPTO_MODE",
        }
        
        env_var = env_mappings.get(key, f"AUDIT_{key}")
        env_value = os.getenv(env_var)
        
        if env_value is not None:
            # Convert string booleans
            if env_value.lower() in ["true", "false"]:
                return env_value.lower() == "true"
            # Convert numbers
            try:
                return int(env_value)
            except ValueError:
                try:
                    return float(env_value)
                except ValueError:
                    return env_value
        
        return config_data.get(key, default)
    
    # Backend configuration
    config_info["backend"] = {
        "type": get_config("BACKEND_TYPE", "file"),
        "compression_enabled": get_config("COMPRESSION_ALGO", "none") != "none",
        "compression_algorithm": get_config("COMPRESSION_ALGO", "none"),
        "batch_flush_interval": get_config("BATCH_FLUSH_INTERVAL", 10),
        "batch_max_size": get_config("BATCH_MAX_SIZE", 100),
    }
    
    # Security configuration
    encryption_key_set = bool(os.getenv("AUDIT_LOG_ENCRYPTION_KEY"))
    
    config_info["security"] = {
        "encryption_enabled": get_config("ENCRYPTION_ENABLED", True),
        "encryption_key_configured": encryption_key_set,
        "immutable": get_config("IMMUTABLE", True),
        "tamper_detection_enabled": get_config("TAMPER_DETECTION_ENABLED", True),
        "rbac_enabled": get_config("RBAC_ENABLED", True),
        "dev_mode": get_config("DEV_MODE", False),
        "crypto_provider": get_config("PROVIDER_TYPE", "software"),
        "signing_algorithm": get_config("DEFAULT_ALGO", "ed25519"),
        "crypto_mode": get_config("CRYPTO_MODE", "full"),
    }
    
    # Compliance configuration
    config_info["compliance"] = {
        "mode": get_config("COMPLIANCE_MODE", "standard"),
        "data_retention_days": get_config("DATA_RETENTION_DAYS", 365),
        "pii_redaction_enabled": get_config("PII_REDACTION_ENABLED", True),
    }
    
    # Performance configuration
    config_info["performance"] = {
        "retry_max_attempts": get_config("RETRY_MAX_ATTEMPTS", 3),
        "retry_backoff_factor": get_config("RETRY_BACKOFF_FACTOR", 0.5),
        "health_check_interval": get_config("HEALTH_CHECK_INTERVAL", 30),
    }
    
    # Feature status
    config_info["features"] = {
        "tracing_enabled": get_config("TRACING_ENABLED", True),
        "metrics_enabled": True,  # Always enabled
        "api_enabled": True,  # Always enabled if this endpoint is accessible
        "grpc_enabled": get_config("GRPC_PORT") is not None,
    }
    
    # Validation status
    validation_warnings = []
    validation_errors = []
    
    # Check for common configuration issues
    if config_info["security"]["dev_mode"]:
        validation_warnings.append("DEV_MODE is enabled - not suitable for production")
    
    if not config_info["security"]["encryption_key_configured"]:
        if not config_info["security"]["dev_mode"]:
            validation_errors.append("Encryption key not configured (AUDIT_LOG_ENCRYPTION_KEY not set)")
        else:
            validation_warnings.append("Encryption key not configured (acceptable in dev mode)")
    
    if not config_info["security"]["immutable"]:
        validation_warnings.append("Immutability disabled - logs can be modified")
    
    if not config_info["security"]["tamper_detection_enabled"]:
        validation_warnings.append("Tamper detection disabled - security risk")
    
    if config_info["security"]["crypto_provider"] == "software":
        validation_warnings.append("Using software crypto provider - consider HSM for production")
    
    if config_info["backend"]["type"] == "file":
        validation_warnings.append("Using file backend - consider cloud storage for production")
    
    if config_info["backend"]["type"] == "memory":
        validation_errors.append("Using memory backend - data will not persist!")
    
    config_info["validation"] = {
        "status": "ok" if not validation_errors else "error",
        "warnings": validation_warnings,
        "errors": validation_errors,
        "warnings_count": len(validation_warnings),
        "errors_count": len(validation_errors),
    }
    
    # Documentation links
    config_info["documentation"] = {
        "configuration_guide": "/docs/AUDIT_CONFIGURATION.md",
        "quick_start": "/generator/AUDIT_CONFIG_README.md",
        "validation_script": "python generator/audit_log/validate_config.py",
    }
    
    # Configuration files available
    config_info["available_templates"] = {
        "production": "generator/audit_config.production.yaml",
        "development": "generator/audit_config.development.yaml",
        "enhanced": "generator/audit_config.enhanced.yaml",
    }
    
    return config_info


@router.get("/config/documentation")
async def get_audit_config_documentation() -> Dict[str, Any]:
    """
    Get audit configuration documentation and help.
    
    Returns information about:
    - Available configuration options
    - Environment variables
    - Configuration templates
    - Validation commands
    - Quick start guides
    
    **Returns:**
    - configuration_options: List of available configuration categories
    - environment_variables: Key environment variables
    - templates: Available configuration templates
    - validation: Validation commands
    - documentation_links: Links to comprehensive documentation
    """
    return {
        "configuration_options": {
            "cryptographic_provider": {
                "description": "Configure cryptographic signing and key management",
                "key_settings": [
                    "PROVIDER_TYPE (software/hsm)",
                    "DEFAULT_ALGO (rsa/ecdsa/ed25519/hmac)",
                    "KEY_ROTATION_INTERVAL_SECONDS",
                ],
            },
            "backend_storage": {
                "description": "Configure where audit logs are stored",
                "key_settings": [
                    "BACKEND_TYPE (file/sqlite/s3/gcs/azure/kafka/splunk)",
                    "BACKEND_PARAMS (JSON configuration)",
                    "COMPRESSION_ALGO (none/gzip/zstd)",
                ],
            },
            "security": {
                "description": "Security features and encryption",
                "key_settings": [
                    "ENCRYPTION_ENABLED (true/false)",
                    "IMMUTABLE (true/false)",
                    "TAMPER_DETECTION_ENABLED (true/false)",
                    "RBAC_ENABLED (true/false)",
                ],
            },
            "performance": {
                "description": "Batch processing and retry configuration",
                "key_settings": [
                    "BATCH_FLUSH_INTERVAL (seconds)",
                    "BATCH_MAX_SIZE (entries)",
                    "RETRY_MAX_ATTEMPTS",
                    "COMPRESSION_LEVEL",
                ],
            },
            "compliance": {
                "description": "Compliance framework settings",
                "key_settings": [
                    "COMPLIANCE_MODE (soc2/hipaa/pci-dss/gdpr/standard)",
                    "DATA_RETENTION_DAYS",
                    "PII_REDACTION_ENABLED",
                ],
            },
        },
        "environment_variables": {
            "critical": {
                "AUDIT_LOG_ENCRYPTION_KEY": "Base64-encoded Fernet key (REQUIRED in production)",
                "AUDIT_CRYPTO_MODE": "Crypto mode: full/dev/disabled",
                "AUDIT_LOG_DEV_MODE": "Enable development mode (NEVER in production)",
            },
            "core": {
                "AUDIT_LOG_BACKEND_TYPE": "Storage backend type",
                "AUDIT_LOG_BACKEND_PARAMS": "Backend-specific parameters (JSON)",
                "AUDIT_LOG_IMMUTABLE": "Prevent log deletion/modification",
                "AUDIT_CRYPTO_PROVIDER_TYPE": "Crypto provider (software/hsm)",
            },
            "performance": {
                "AUDIT_COMPRESSION_ALGO": "Compression algorithm",
                "AUDIT_BATCH_FLUSH_INTERVAL": "Batch flush interval (seconds)",
                "AUDIT_BATCH_MAX_SIZE": "Maximum batch size",
                "AUDIT_RETRY_MAX_ATTEMPTS": "Retry attempts",
            },
        },
        "templates": {
            "production": {
                "file": "generator/audit_config.production.yaml",
                "description": "Production-hardened configuration with security-first defaults",
                "command": "make audit-config-setup-prod",
            },
            "development": {
                "file": "generator/audit_config.development.yaml",
                "description": "Developer-friendly configuration for local testing",
                "command": "make audit-config-setup-dev",
            },
            "enhanced": {
                "file": "generator/audit_config.enhanced.yaml",
                "description": "Complete reference with all options documented",
                "use_case": "Reference documentation for all available settings",
            },
        },
        "validation": {
            "validate_current": {
                "command": "make audit-config-validate",
                "description": "Validate current audit_config.yaml",
            },
            "validate_production": {
                "command": "make audit-config-validate-prod",
                "description": "Validate production template",
            },
            "validate_strict": {
                "command": "make audit-config-validate-strict",
                "description": "Strict validation (warnings = errors)",
            },
            "validate_env": {
                "command": "make audit-config-validate-env",
                "description": "Validate environment variables",
            },
            "manual": {
                "command": "python generator/audit_log/validate_config.py --config <file>",
                "description": "Manually validate any configuration file",
            },
        },
        "documentation_links": {
            "complete_reference": "docs/AUDIT_CONFIGURATION.md",
            "quick_start": "generator/AUDIT_CONFIG_README.md",
            "module_readme": "generator/audit_log/README.md",
            "implementation_summary": "AUDIT_CONFIG_IMPLEMENTATION_SUMMARY.md",
        },
        "quick_start": {
            "development": [
                "1. Run: make audit-config-setup-dev",
                "2. Set: export AUDIT_LOG_DEV_MODE=true",
                "3. Validate: make audit-config-validate",
                "4. Start: python server/main.py",
            ],
            "production": [
                "1. Run: make audit-config-setup-prod",
                "2. Edit: vim generator/audit_config.yaml",
                "3. Generate key: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
                "4. Set: export AUDIT_LOG_ENCRYPTION_KEY=<generated-key>",
                "5. Validate: make audit-config-validate-strict",
                "6. Deploy your application",
            ],
        },
        "help": {
            "message": "For complete documentation, see docs/AUDIT_CONFIGURATION.md",
            "validation_script": "Run 'python generator/audit_log/validate_config.py --help' for validation options",
            "makefile_help": "Run 'make help' to see all available audit configuration commands",
        },
    }
