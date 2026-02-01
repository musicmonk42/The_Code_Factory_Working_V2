# guardrails/compliance_mapper.py
import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml
from cerberus import Validator
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

# B. Add Compliance Metrics
try:
    from prometheus_client import Counter, Gauge

    # Use a specific, unique prefix to prevent conflicts in multi-service deployments
    METRIC_PREFIX = "self_healing_"

    # Use try-except to prevent duplicate metric registration errors
    try:
        self_healing_compliance_block_total = Counter(
            f"{METRIC_PREFIX}compliance_block_total",
            "Total number of actions blocked by compliance enforcement",
        )
        self_healing_compliance_gap_alerts_total = Counter(
            f"{METRIC_PREFIX}compliance_gap_alerts_total",
            "Total number of compliance gap alerts triggered",
        )
        self_healing_compliance_required_controls_not_enforced = Gauge(
            f"{METRIC_PREFIX}compliance_required_controls_not_enforced",
            "Number of required compliance controls not enforced",
            ["control_id"],
        )
        self_healing_config_load_failures = Counter(
            f"{METRIC_PREFIX}config_load_failures",
            "Total number of config load failures",
        )
    except ValueError:
        # Metrics already registered, retrieve them
        from prometheus_client import REGISTRY

        self_healing_compliance_block_total = REGISTRY._collector_to_names.get(
            f"{METRIC_PREFIX}compliance_block_total"
        )
        self_healing_compliance_gap_alerts_total = REGISTRY._collector_to_names.get(
            f"{METRIC_PREFIX}compliance_gap_alerts_total"
        )
        self_healing_compliance_required_controls_not_enforced = (
            REGISTRY._collector_to_names.get(
                f"{METRIC_PREFIX}compliance_required_controls_not_enforced"
            )
        )
        self_healing_config_load_failures = REGISTRY._collector_to_names.get(
            f"{METRIC_PREFIX}config_load_failures"
        )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    print("Prometheus client not installed. Metrics will not be exported.")
    PROMETHEUS_AVAILABLE = False


# A. Raise Custom Exception for Compliance Block
# P2: Monitoring/Observability - Placeholder for central audit system
try:
    from audit_log import audit_log_event_async

    AUDIT_LOG_AVAILABLE = True
except ImportError:
    AUDIT_LOG_AVAILABLE = False
    print("audit_log module not found. Centralized audit logging will be disabled.")

    async def audit_log_event_async(*args, **kwargs):
        pass


def sanitize_log(msg: str) -> str:
    """Strip potential PII/keys from log messages."""
    msg = re.sub(
        r"(?i)(api_key|password|secret|token|pass)=[^& ]+", r"\1=REDACTED", msg
    )
    msg = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "REDACTED_EMAIL", msg
    )
    return msg


async def _log_to_central_audit(event_name: str, details: Dict[str, Any]):
    """
    Sends structured events to the central audit system.
    In a real system, this would integrate with a service like Splunk, Datadog, or an internal Kafka topic.
    """
    if AUDIT_LOG_AVAILABLE:
        # P2: Integrate with audit_log.py
        await audit_log_event_async(
            event_type=f"compliance:{event_name}",
            message=f"Compliance event: {event_name}",
            data=details,
            agent_id="compliance_mapper",
        )
    else:
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "event_type": event_name,
            "details": details,
        }
        logger.critical(sanitize_log(f"CENTRAL_AUDIT_LOG: {json.dumps(log_entry)}"))


class ComplianceEnforcementError(Exception):
    """
    Custom exception raised when a requested action is blocked due to compliance enforcement.
    This exception carries details about the blocked action and the specific control that was violated.
    """

    def __init__(self, action_name: str, control_tag: str, message: str):
        """
        Initializes the ComplianceEnforcementError.
        """
        super().__init__(
            f"Compliance block on {action_name} (Control: {control_tag}): {message}"
        )
        self.action_name = action_name
        self.control_tag = control_tag
        self.message = message
        if PROMETHEUS_AVAILABLE:
            self_healing_compliance_block_total.inc()
        logger.error(
            sanitize_log(
                f"ACTION_BLOCKED_BY_COMPLIANCE: Action '{action_name}' blocked by control '{control_tag}': {message}"
            )
        )
        asyncio.create_task(
            _log_to_central_audit(
                "action_blocked",
                {
                    "action_name": action_name,
                    "control_tag": control_tag,
                    "message": message,
                },
            )
        )


# P2: Dependencies - Documenting required packages
# To run this script, ensure you have a requirements.txt with at least:
# pyyaml==6.0.1
# prometheus_client==0.20.0
# cerberus==1.3.4
#
# P1: Documenting ENV vars
# APP_ENV: 'development' or 'production'. Controls fail-closed behavior and error output.
# CREW_CONFIG_PATH: Path to the crew_config.yaml file.
#
# Get module logger - follows Python logging best practices.
# Do NOT call basicConfig() at module level to avoid duplicate logs.
logger = logging.getLogger(__name__)

DEFAULT_CREW_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "../../agent_orchestration/crew_config.yaml"
)
CONFIG_PATH = os.environ.get("CREW_CONFIG_PATH", DEFAULT_CREW_CONFIG_PATH)


def load_compliance_map(config_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Loads the compliance control definitions from the specified YAML configuration file.

    """
    schema = {
        "compliance_controls": {
            "type": "dict",
            "keysrules": {"regex": r"^[A-Z0-9-]+$", "required": True},
            "valuesrules": {
                "type": "dict",
                "schema": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {
                        "type": "string",
                        "allowed": [
                            "enforced",
                            "partially_enforced",
                            "logged",
                            "not_implemented",
                            "not_specified",
                        ],
                    },
                    "required": {"type": "boolean"},
                },
            },
        }
    }
    v = Validator(schema)

    try:
        if not os.path.exists(config_path):
            if os.environ.get("APP_ENV", "development").lower() == "production":
                logger.critical(
                    f"Production environment: crew_config.yaml missing at {config_path}. Failing closed."
                )
                if PROMETHEUS_AVAILABLE:
                    self_healing_config_load_failures.inc()
                raise ComplianceEnforcementError(
                    "startup",
                    "CONFIG",
                    f"crew_config.yaml missing at {config_path}. Service cannot start in production without compliance map.",
                )
            logger.error(
                f"Error: crew_config.yaml not found at {config_path}. Cannot load compliance map. Returning empty map.",
                exc_info=True,
            )
            if PROMETHEUS_AVAILABLE:
                self_healing_config_load_failures.inc()
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            crew_config = yaml.safe_load(f)

            # FIX: Handle None from empty YAML files
            if crew_config is None:
                logger.error(
                    f"Empty or invalid YAML file at {config_path}. Returning empty compliance map."
                )
                if PROMETHEUS_AVAILABLE:
                    self_healing_config_load_failures.inc()
                return {}

            # FIX: Validate that crew_config is a dictionary
            if not isinstance(crew_config, dict):
                logger.error(
                    f"Invalid YAML structure in {config_path}: expected dict, got {type(crew_config).__name__}. Returning empty map."
                )
                if PROMETHEUS_AVAILABLE:
                    self_healing_config_load_failures.inc()
                return {}

            # Now safe to call .get() on crew_config
            if not v.validate(
                {"compliance_controls": crew_config.get("compliance_controls", {})}
            ):
                logger.error(
                    f"Invalid YAML structure: {v.errors}",
                    extra={"validation_errors": v.errors},
                )
                if PROMETHEUS_AVAILABLE:
                    self_healing_config_load_failures.inc()
                return {}

            compliance_controls = crew_config.get("compliance_controls", {})
            if not compliance_controls:
                logger.warning(
                    f"No 'compliance_controls' found in {config_path}. Proceeding with no compliance enforcement."
                )
            else:
                logger.info(
                    f"Loaded {len(compliance_controls)} compliance controls from {config_path}."
                )
            return compliance_controls
    except FileNotFoundError:
        logger.error(
            sanitize_log(
                f"Error: crew_config.yaml not found at {config_path} during file open. Returning empty map."
            ),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            self_healing_config_load_failures.inc()
        return {}
    except PermissionError:
        logger.critical(
            sanitize_log(
                f"Permission denied when trying to open {config_path}. Check file permissions."
            ),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            self_healing_config_load_failures.inc()
        raise
    except yaml.YAMLError as e:
        logger.error(
            sanitize_log(f"Error parsing crew_config.yaml at {config_path}: {e}"),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            self_healing_config_load_failures.inc()
        return {}
    except Exception as e:
        logger.error(
            sanitize_log(
                f"An unexpected error occurred while loading compliance map from {config_path}: {e}"
            ),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            self_healing_config_load_failures.inc()
        return {}


def check_coverage(compliance_map: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Checks the coverage status of compliance controls based on their defined status
    and whether they are marked as 'required'.

    """
    coverage_gaps = {
        "not_enforced": [],
        "partially_enforced": [],
        "not_implemented": [],
        "required_but_not_enforced": [],
    }

    if PROMETHEUS_AVAILABLE:
        for control_id in compliance_map.keys():
            self_healing_compliance_required_controls_not_enforced.labels(
                control_id=control_id
            ).set(0)

    for control_id, control_info in compliance_map.items():
        status = control_info.get("status", "not_specified").lower()
        required = control_info.get("required", True)

        if required:
            if status == "enforced":
                pass
            elif status == "logged" or status == "partially_enforced":
                coverage_gaps["partially_enforced"].append(
                    f"{control_id} (Status: {status}, Required: True)"
                )
                coverage_gaps["required_but_not_enforced"].append(control_id)
                if PROMETHEUS_AVAILABLE:
                    self_healing_compliance_required_controls_not_enforced.labels(
                        control_id=control_id
                    ).set(1)
            elif status == "not_implemented":
                coverage_gaps["not_implemented"].append(
                    f"{control_id} (Status: {status}, Required: True)"
                )
                coverage_gaps["required_but_not_enforced"].append(control_id)
                if PROMETHEUS_AVAILABLE:
                    self_healing_compliance_required_controls_not_enforced.labels(
                        control_id=control_id
                    ).set(1)
            else:
                coverage_gaps["not_enforced"].append(
                    f"{control_id} (Status: {status}, Required: True)"
                )
                coverage_gaps["required_but_not_enforced"].append(control_id)
                if PROMETHEUS_AVAILABLE:
                    self_healing_compliance_required_controls_not_enforced.labels(
                        control_id=control_id
                    ).set(1)
        else:
            if status == "not_implemented":
                coverage_gaps["not_implemented"].append(
                    f"{control_id} (Status: {status}, Required: False)"
                )

    return coverage_gaps


def _audit_log_gap(message: str, details: Optional[Dict[str, Any]] = None):
    """
    Sends compliance gap information to a centralized audit/event streaming service.

    """
    log_entry = {
        "event_type": "compliance_gap_alert",
        "message": message,
        "details": details or {},
    }
    logger.warning(sanitize_log(f"AUDIT_LOG_COMPLIANCE_GAP: {json.dumps(log_entry)}"))
    if PROMETHEUS_AVAILABLE:
        self_healing_compliance_gap_alerts_total.inc()
    asyncio.create_task(_log_to_central_audit("compliance_gap_alert", log_entry))


def generate_report(config_path: str) -> Tuple[Dict[str, List[str]], bool]:
    """
    Generates and prints a compliance coverage report based on the loaded configuration.

    """
    print("\n--- Generating Compliance Coverage Report ---")
    compliance_map = load_compliance_map(config_path)

    if not compliance_map:
        print(
            "No compliance controls found or loaded. Report cannot be generated meaningfully."
        )
        return {
            "not_enforced": [],
            "partially_enforced": [],
            "not_implemented": [],
            "required_but_not_enforced": [],
        }, False

    coverage_gaps = check_coverage(compliance_map)

    all_enforced = True
    if coverage_gaps["required_but_not_enforced"]:
        all_enforced = False

    if all_enforced:
        print("\n✅ 100% GA: All required compliance controls are currently enforced.")
        logger.info("Compliance Report: All required controls enforced.")
    else:
        print("\n⚠️ WARNING: Compliance enforcement gaps detected!")
        logger.warning("Compliance Report: Gaps detected in required controls.")

        if coverage_gaps["required_but_not_enforced"]:
            print("\n🚨 Required controls NOT fully enforced:")
            for control_id in sorted(
                list(set(coverage_gaps["required_but_not_enforced"]))
            ):
                status = compliance_map.get(control_id, {}).get("status", "N/A")
                print(f"  - {control_id} (Current Status: {status})")
                _audit_log_gap(
                    f"Required control {control_id} not fully enforced.",
                    {
                        "control_id": control_id,
                        "current_status": status,
                        "required": True,
                    },
                )

        if coverage_gaps["partially_enforced"]:
            print("\n🟡 Controls with Partial Enforcement (may require attention):")
            for gap in sorted(list(set(coverage_gaps["partially_enforced"]))):
                print(f"  - {gap}")
                _audit_log_gap(
                    f"Control {gap.split(' ')[0]} has partial enforcement.",
                    {"control_details": gap, "required": True},
                )

        if coverage_gaps["not_implemented"]:
            print("\n⚪ Controls marked as 'not_implemented':")
            for gap in sorted(list(set(coverage_gaps["not_implemented"]))):
                control_id = gap.split(" ")[0]
                is_required = compliance_map.get(control_id, {}).get("required", True)
                status_str = "(Required: True)" if is_required else "(Required: False)"
                print(f"  - {gap} {status_str}")
                _audit_log_gap(
                    f"Control {control_id} is marked as not_implemented.",
                    {"control_details": gap, "required": is_required},
                )

        if coverage_gaps["not_enforced"]:
            print(
                "\n🔴 Controls with Unspecified/Non-Enforced Status (check configuration):"
            )
            for gap in sorted(list(set(coverage_gaps["not_enforced"]))):
                print(f"  - {gap}")
                _audit_log_gap(
                    f"Control {gap.split(' ')[0]} has unspecified or non-enforced status.",
                    {"control_details": gap, "required": True},
                )

    print("\n--- Report End ---")
    return coverage_gaps, all_enforced


def health_check() -> Dict[str, Any]:
    """Returns health status of integrations."""
    return {
        "prometheus_available": PROMETHEUS_AVAILABLE,
        "config_path_exists": os.path.exists(CONFIG_PATH),
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def write_dummy_config(path: str, content: str):
    """Writes dummy config with retries to handle transient file issues."""
    total, used, free = shutil.disk_usage(os.path.dirname(path))
    if free < 100 * 1024 * 1024:  # Less than 100MB
        logger.critical(
            f"Low disk space ({free/1024/1024:.2f}MB free) for {path}. Aborting dummy config creation."
        )
        sys.exit(1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main_cli():
    """
    Command-Line Interface (CLI) entry point for generating and checking
    compliance coverage reports.

    """
    parser = argparse.ArgumentParser(
        description="Generate and check compliance coverage report.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run health check and output status.",
    )
    # Parse actual command-line arguments
    args = parser.parse_args()

    config_path_to_use = os.environ.get("CREW_CONFIG_PATH", DEFAULT_CREW_CONFIG_PATH)

    app_env = os.environ.get("APP_ENV", "development").lower()

    if args.health_check:
        health = health_check()
        print(json.dumps(health, indent=2))
        sys.exit(0 if all(health.values()) else 1)

    if app_env == "production" and not PROMETHEUS_AVAILABLE:
        logger.critical(
            "Prometheus client not installed. Metrics required for production. Aborting."
        )
        sys.exit(1)

    try:
        coverage_gaps, all_enforced = generate_report(config_path_to_use)

        if app_env in ["development", "debug"]:
            print("--- Detailed Output for Development ---")
            print(
                json.dumps(
                    {"all_enforced": all_enforced, "coverage_gaps": coverage_gaps},
                    indent=2,
                )
            )

        # P1: CLI Hardening - Exit codes
        if not all_enforced:
            logger.error(
                "CLI: Exiting with non-zero status due to compliance gaps in required controls."
            )
            sys.exit(1)

    except PermissionError as e:
        logger.critical(f"CLI Critical Error (Permission): {e}")
        exit(2)
    except ComplianceEnforcementError as e:
        logger.critical(f"CLI Critical Error (Compliance Enforcement): {e}")
        exit(2)
    except Exception as e:
        logger.critical(
            f"CLI Unexpected Error: {e}", exc_info=(app_env not in ["production"])
        )
        exit(3)


if __name__ == "__main__":
    app_env = os.environ.get("APP_ENV", "development").lower()

    if app_env != "production":
        logger.info(
            f"Detected APP_ENV='{app_env}'. Creating dummy crew_config.yaml for testing/development."
        )
        test_config_dir = os.path.join(
            os.path.dirname(__file__), "../../agent_orchestration"
        )
        os.makedirs(test_config_dir, exist_ok=True)
        test_config_path = os.path.join(test_config_dir, "crew_config.yaml")

        dummy_crew_config_content = """
version: 10.0.0
id: self_fixing_engineer_crew
name: Self-Fixing Engineer: Universal Crew ∞
description: |
  The next-evolution, runtime-driven, infinitely extensible crew of AI, human, and plugin agents for self-healing, refactoring, testing, simulation, and governance.

compliance_controls:
  AC-1:
    name: Access Control Policy and Procedures
    description: Establishes policies and procedures for managing system and information access.
    status: enforced
    required: true
  AC-2:
    name: Account Management
    description: Manages information system accounts.
    status: enforced
    required: true
  AC-3:
    name: Access Enforcement
    description: Enforces approved authorizations.
    status: enforced
    required: true
  AC-6:
    name: Least Privilege
    description: Enforces the principle of least privilege.
    status: enforced
    required: true
  AU-2:
    name: Audit Events
    description: Determines audit events requiring logging.
    status: enforced
    required: true
  AU-6:
    name: Audit Review, Analysis, and Reporting
    description: Reviews and analyzes audit records.
    status: enforced
    required: true
  IR-4:
    name: Incident Handling
    description: Implements an incident handling capability.
    status: partially_enforced
    required: true
  CM-2:
    name: Baseline Configuration
    description: Establishes baseline configurations.
    status: not_implemented
    required: true
  SC-7:
    name: Boundary Protection
    description: Monitors and controls communications at boundaries.
    status: logged
    required: false
  RA-5:
    name: Vulnerability Scanning
    description: Scans for vulnerabilities.
    status: enforced
    required: true
  PL-2:
    name: System Security Plan
    description: Develops system security plan.
    status: enforced
    required: true
  IA-5:
    name: Authenticator Management
    description: Manages authenticators.
    status: enforced
    required: true
  UNMAPPED-CONTROL:
    name: A control not yet mapped to any agent or action.
    description: This control is defined but its enforcement is unknown.
    status: not_specified
    required: true

agents:
  - id: refactor
    name: Refactor Agent
    agent_type: ai
    compliance_controls:
      - id: AC-6
        status: enforced
      - id: CM-2
        status: not_implemented
"""
        try:
            write_dummy_config(test_config_path, dummy_crew_config_content)
            logger.info(f"Dummy crew_config.yaml created at: {test_config_path}")
            os.environ["CREW_CONFIG_PATH"] = test_config_path
            main_cli()
        except Exception as e:
            logger.critical(
                f"Failed to create and run dummy config: {e}", exc_info=True
            )
            sys.exit(1)
        finally:
            try:
                os.remove(test_config_path)
                if os.path.exists(test_config_dir) and not os.listdir(test_config_dir):
                    os.rmdir(test_config_dir)
                logger.info(
                    "Cleaned up dummy crew_config.yaml and directory (if empty)."
                )
            except Exception as e:
                logger.warning(
                    f"Failed to clean up dummy config files: {e}", exc_info=True
                )

    else:
        logger.info(
            "Running in production environment. Dummy file creation and detailed output skipped. `main_cli()` will be used as the entrypoint."
        )
        main_cli()
