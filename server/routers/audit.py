# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified Audit Log Router
========================

Provides centralized access to audit logs from all platform modules via the
OmniCore central orchestrator.  All queries are routed through
OmniCoreService.route_job() so that no module internals are imported directly
and no raw log files are read by this router.

Architecture:
    Module Event → Module Local Audit → OmniCore Hub → Unified Storage
                                           ↑
                                  POST /audit/ingest
                                           ↓
                              Server Audit Tab ← OmniCore query_audit_records()
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from server.services.audit_query_service import get_audit_query_service, AuditQueryService
from server.services.omnicore_service import OmniCoreService, get_omnicore_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Audit Logs"])

# Maximum number of recent job IDs to query when no specific job_id is provided
_MAX_RECENT_JOBS = 10

# Mapping from logical module name to the OmniCore target_module used in route_job
_MODULE_TARGET: Dict[str, str] = {
    "generator": "generator",
    "arbiter": "sfe",
    "testgen": "sfe",
    "simulation": "sfe",
    "guardrails": "sfe",
    "omnicore": "omnicore",
}

# In-memory unified audit store for ingested events (module → list of entries).
# This is a lightweight fallback store; production deployments should wire this
# to a persistent backend via the existing audit backend infrastructure.
_ingest_store: Dict[str, List[Dict[str, Any]]] = {}
_MAX_INGEST_PER_MODULE = 10_000  # cap to prevent unbounded growth


class AuditIngestRequest(BaseModel):
    """Request body for ``POST /audit/ingest``.

    All fields beyond the required ones are accepted so that each module can
    embed its own additional metadata without schema changes.
    """

    module: str = Field(..., description="Source module name (e.g. 'generator', 'arbiter')")
    event_type: str = Field(..., description="Type of the audit event")
    timestamp: Optional[str] = Field(
        None,
        description="ISO 8601 timestamp; auto-set to UTC *now* if omitted",
    )
    job_id: Optional[str] = Field(None, description="Associated job ID")

    model_config = {"extra": "allow"}


async def _query_via_omnicore(
    omnicore_service: OmniCoreService,
    module: str,
    start_time: Optional[str],
    end_time: Optional[str],
    event_type: Optional[str],
    job_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Route an audit-log query for *module* through OmniCore.

    Follows the same pattern as GeneratorService.query_audit_logs():
    build a payload, call omnicore_service.route_job(), and extract the
    ``logs`` list from the returned ``data`` dict.

    Returns an empty list on any failure so that a single module error
    does not abort the whole aggregated query.
    """
    if not omnicore_service:
        logger.warning("OmniCore service unavailable; skipping %s audit query", module)
        return []

    target_module = _MODULE_TARGET.get(module, "sfe")
    payload = {
        "action": "query_audit_logs",
        "module": module,
        "start_time": start_time,
        "end_time": end_time,
        "event_type": event_type,
        "job_id": job_id,
        "limit": limit,
    }
    result = await omnicore_service.route_job(
        job_id=job_id or "audit_query",
        source_module="api",
        target_module=target_module,
        payload=payload,
    )
    data = result.get("data") or {}
    if isinstance(data, dict):
        logs = data.get("logs", [])
    else:
        logs = []

    # Supplement with any events that were pushed via POST /audit/ingest
    ingested = _ingest_store.get(module, [])
    if ingested:
        seen_ingested = list(ingested)
        if start_time:
            seen_ingested = [e for e in seen_ingested if (e.get("timestamp") or "") >= start_time]
        if end_time:
            seen_ingested = [e for e in seen_ingested if (e.get("timestamp") or "") <= end_time]
        if event_type:
            seen_ingested = [e for e in seen_ingested if event_type in (e.get("event_type") or "")]
        if job_id:
            seen_ingested = [e for e in seen_ingested if job_id in str(e.get("job_id") or "")]
        logs = logs + seen_ingested

    return logs[:limit]


@router.get("/logs/all")
async def query_all_audit_logs(
    module: Optional[str] = Query(None, description="Filter by module: generator, arbiter, testgen, simulation, omnicore, guardrails"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
    start_time: Optional[str] = Query(None, description="ISO 8601 start timestamp"),
    end_time: Optional[str] = Query(None, description="ISO 8601 end timestamp"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results per module; global cap is limit × number of modules queried"),
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
    audit_service: AuditQueryService = Depends(get_audit_query_service),
) -> Dict[str, Any]:
    """
    Query audit logs from all platform modules via the OmniCore orchestrator.

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

    if not omnicore_service:
        logger.error("OmniCore service unavailable; cannot serve audit logs")
        return {
            "aggregated_logs": [],
            "total_count": 0,
            "modules_queried": [],
            "errors": {"omnicore": "OmniCore service unavailable"},
            "metadata": {
                "query_timestamp": datetime.now(timezone.utc).isoformat(),
                "module_filter": module,
                "event_type_filter": event_type,
                "job_id_filter": job_id,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
        }

    aggregated_logs: List[Dict[str, Any]] = []
    modules_queried: List[str] = []
    errors: Dict[str, str] = {}

    # Define which modules to query
    modules_to_query = [module] if module else ["generator", "arbiter", "testgen", "simulation", "omnicore", "guardrails"]

    # Query each non-omnicore module through OmniCore route_job
    for mod in modules_to_query:
        if mod == "omnicore":
            continue
        try:
            modules_queried.append(mod)
            logs = await _query_via_omnicore(
                omnicore_service=omnicore_service,
                module=mod,
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            for log in logs:
                log["module"] = mod
                aggregated_logs.append(log)
        except Exception as e:
            logger.error(f"Error querying {mod} audit logs: {e}", exc_info=True)
            errors[mod] = str(e)

    # Query OmniCore's own audit trail via get_audit_trail()
    if "omnicore" in modules_to_query:
        try:
            modules_queried.append("omnicore")
            omnicore_trail_logs: List[Dict[str, Any]] = []

            # Primary path: use get_audit_trail() for each known job ID
            if job_id:
                job_ids = [job_id]
            else:
                try:
                    from server.storage import jobs_db
                    all_keys = list(jobs_db.keys())
                    job_ids = all_keys[-_MAX_RECENT_JOBS:] if all_keys else []
                except (ImportError, AttributeError):
                    job_ids = []
                if not job_ids:
                    job_ids = ["system"]

            for jid in job_ids:
                omnicore_logs = await audit_service.get_audit_trail(job_id=jid, limit=limit)
                for log in omnicore_logs:
                    log["module"] = "omnicore"
                    omnicore_trail_logs.append(log)

            # Fallback: read local OmniCore log files when primary path returns empty
            if not omnicore_trail_logs:
                _omnicore_log_candidates = [
                    "logs/omnicore_audit.jsonl",
                    "omnicore_engine/audit/audit_trail.jsonl",
                ]
                for _log_path_str in _omnicore_log_candidates:
                    _log_path = Path(_log_path_str)
                    if not _log_path.is_file():
                        continue
                    try:
                        async with aiofiles.open(_log_path, "r", encoding="utf-8") as _fh:
                            async for _raw in _fh:
                                _line = _raw.strip()
                                if not _line:
                                    continue
                                try:
                                    _entry: Dict[str, Any] = json.loads(_line)
                                except json.JSONDecodeError:
                                    continue
                                _ts = _entry.get("timestamp") or _entry.get("ts") or ""
                                if start_time and _ts and _ts < start_time:
                                    continue
                                if end_time and _ts and _ts > end_time:
                                    continue
                                if event_type:
                                    _et = _entry.get("event_type") or _entry.get("event") or ""
                                    if event_type not in _et:
                                        continue
                                if job_id:
                                    _jid = str(_entry.get("job_id") or "")
                                    if job_id not in _jid:
                                        continue
                                _entry["module"] = "omnicore"
                                omnicore_trail_logs.append(_entry)
                                if len(omnicore_trail_logs) >= limit:
                                    break
                    except OSError as _exc:
                        logger.debug("Could not read OmniCore log file %s: %s", _log_path, _exc)
                    if len(omnicore_trail_logs) >= limit:
                        break

            aggregated_logs.extend(omnicore_trail_logs)
        except Exception as e:
            logger.error(f"Error querying omnicore audit logs: {e}", exc_info=True)
            errors["omnicore"] = str(e)

    # Sort all logs by timestamp (newest first)
    aggregated_logs.sort(
        key=lambda x: x.get("timestamp") or "1970-01-01T00:00:00Z",
        reverse=True,
    )

    # Apply global limit: scale by the number of modules queried so per-module
    # results are not unexpectedly truncated when all modules are queried.
    global_limit = limit * max(len(modules_queried), 1)
    aggregated_logs = aggregated_logs[:global_limit]

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
        },
    }


@router.post("/ingest")
async def ingest_audit_event(
    event: AuditIngestRequest,
) -> Dict[str, Any]:
    """
    Ingest a single audit event from any platform module.

    Modules call this endpoint to push audit events to the unified store so
    that they become visible via ``GET /audit/logs/all``.

    **Required fields in the event body:**
    - ``module``: Source module name (e.g. ``generator``, ``arbiter``)
    - ``event_type``: Type of the audit event

    **Optional fields:**
    - ``timestamp``: ISO 8601 timestamp (auto-set to UTC *now* if omitted)
    - ``job_id``: Associated job ID
    - Any additional module-specific fields are preserved as-is.

    **Returns:**
    - ``ingested``: ``true`` when the event was stored successfully
    - ``module``: Module the event was attributed to
    - ``timestamp``: Timestamp recorded for the event
    """
    ts = event.timestamp or datetime.now(timezone.utc).isoformat()

    # Build an immutable snapshot of the entry; never mutate the Pydantic model.
    entry: Dict[str, Any] = {
        **event.model_dump(exclude_none=False),
        "timestamp": ts,
    }

    module_store = _ingest_store.setdefault(event.module, [])
    # Enforce per-module cap – drop the oldest entry when full
    if len(module_store) >= _MAX_INGEST_PER_MODULE:
        del module_store[0]
    module_store.append(entry)

    logger.debug(
        "Ingested audit event: module=%s event_type=%s",
        event.module,
        event.event_type,
    )
    return {"ingested": True, "module": event.module, "timestamp": ts}


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
