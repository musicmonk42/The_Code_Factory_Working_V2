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
            generator_logs = await generator_service.query_audit_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type,
                job_id=job_id,
                limit=limit,
            )
            
            for log in generator_logs.get("logs", []):
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
    aggregated_logs.sort(
        key=lambda x: x.get("timestamp", "1970-01-01T00:00:00Z"),
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
        from self_fixing_engineer.arbiter.audit_log import TamperEvidentLogger
        
        # Get singleton instance
        audit_logger = TamperEvidentLogger.get_instance()
        
        # Read audit log file
        log_path = audit_logger.log_path if hasattr(audit_logger, 'log_path') else "arbiter_audit.log"
        
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
                    if start_time and entry_time < start_time:
                        continue
                    if end_time and entry_time > end_time:
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
        # Default log path
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
        # Default log path from agentic.py
        log_path = Path("agentic_audit.log")
        
        if not log_path.exists():
            logger.warning(f"Simulation audit log not found: {log_path}")
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
                    if start_time and entry_time < start_time:
                        continue
                    if end_time and entry_time > end_time:
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
        log_path = audit_logger.log_path if hasattr(audit_logger, 'log_path') else "guardrails_audit.log"
        
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
                    if start_time and entry_time < start_time:
                        continue
                    if end_time and entry_time > end_time:
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
async def get_all_event_types() -> Dict[str, List[str]]:
    """
    Get a list of all available audit event types across all modules.
    
    **Returns:**
    - event_types_by_module: Dictionary mapping module names to their event types
    - total_event_types: Total count of unique event types
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
