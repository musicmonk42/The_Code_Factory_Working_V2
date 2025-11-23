import asyncio
import atexit
import datetime
import json
import logging
import os
import sys
from functools import wraps
from typing import Any, Callable, Dict, Optional

import boto3  # For centralized configuration management
import click  # For robust CLI
import yaml  # For loading YAML configs
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from pydantic import BaseModel, Field, ValidationError

# --- Global Constants ---
SERVICE_NAME = "Analyzer"
VERSION = "1.0.0"

# --- Python Version enforcement ---
MIN_PYTHON = (3, 9)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write(f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or higher is required.\n")
    sys.exit(1)

# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# --- Configure logging for the orchestrator ---
if PRODUCTION_MODE:

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_entry = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "service": SERVICE_NAME,
                "version": VERSION,
                "name": record.name,
                "message": record.getMessage(),
            }
            if hasattr(record, "extra") and record.extra:
                log_entry.update(record.extra)
            # Redact secrets if any field name contains "key", "token", or "secret"
            for k in log_entry:
                if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower():
                    log_entry[k] = "***REDACTED***"
            return json.dumps(log_entry, ensure_ascii=False)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)


# --- Custom Exceptions for graceful error handling ---
class NonCriticalError(Exception):
    """Custom exception for recoverable issues that should be logged but not halt execution."""

    pass


class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    This replaces `sys.exit(1)` and allows for centralized error handling.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        try:
            from .core_utils import alert_operator

            alert_operator(message, alert_level)
        except Exception:
            pass


# --- Import classes and functions from core files ---
try:
    from .core_ai import get_ai_patch, get_ai_suggestions
    from .core_graph import ImportGraphAnalyzer
    from .core_policy import PolicyManager, PolicyViolation
    from .core_report import ReportGenerator
    from .core_security import SecurityAnalyzer
    from .core_utils import alert_operator, scrub_secrets
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency: {e}. Aborting startup.")
    try:
        # Since alert_operator might not be imported, we need a fallback
        from .core_utils import alert_operator

        alert_operator(
            f"CRITICAL: Analyzer missing core dependency: {e}. Aborting.",
            level="CRITICAL",
        )
    except Exception:
        pass
    raise RuntimeError("DEPENDENCY_ERROR missing core dependency") from e


# --- Pydantic for comprehensive configuration validation ---
class AnalyzerConfig(BaseModel):
    project_root: str
    audit_logging_enabled: bool = False
    policy_rules_file: Optional[str] = None
    ai_config: Dict[str, Any] = Field(default_factory=dict)
    demo_mode_enabled: bool = False
    llm_endpoint: str = "https://api.openai.com"

    @classmethod
    def validate_paths(cls, config_data):
        root = os.path.abspath(config_data["project_root"])
        # Optional: only allow project roots inside a predefined allowlist (compliance)
        allowed_roots = [
            ar for ar in os.environ.get("ANALYZER_PROJECT_ALLOWLIST", "").split(":") if ar
        ]
        if allowed_roots and not any(root.startswith(os.path.abspath(ar)) for ar in allowed_roots):
            raise AnalyzerCriticalError(
                f"Project root {root} not in allowed project allowlist {allowed_roots}."
            )
        if ".." in root or os.path.islink(root) or not os.path.isdir(root):
            raise AnalyzerCriticalError(
                "Invalid project_root: must be a real directory, no symlinks/traversal."
            )
        if config_data.get("policy_rules_file"):
            pf = os.path.abspath(config_data["policy_rules_file"])
            if ".." in pf or os.path.islink(pf) or not os.path.isfile(pf):
                raise AnalyzerCriticalError(
                    "Invalid policy_rules_file: must be an existing file, no symlinks/traversal."
                )
        return config_data


def load_config(config_path: str) -> AnalyzerConfig:
    """
    Loads the application configuration from a specified path or a centralized service like AWS SSM.
    """
    from .core_audit import audit_logger

    config_data = {}
    try:
        if PRODUCTION_MODE:
            logger.info(f"Attempting to fetch config from AWS SSM parameter store: {config_path}")
            ssm = boto3.client("ssm")
            try:
                response = ssm.get_parameter(Name=config_path, WithDecryption=True)
                config_data = json.loads(response["Parameter"]["Value"])
            except (NoCredentialsError, EndpointConnectionError, ClientError) as e:
                logger.error(
                    f"CONFIG LOAD FAILURE: AWS SSM unavailable or misconfigured: {e}",
                    exc_info=True,
                )
                raise AnalyzerCriticalError(f"AWS SSM unavailable or misconfigured: {e}")
        else:
            if not os.path.exists(config_path):
                logger.error(f"CONFIG LOAD FAILURE: File not found: {config_path}", exc_info=True)
                raise AnalyzerCriticalError(
                    f"Configuration file not found at: {config_path}. Aborting startup."
                )
            with open(config_path, "r", encoding="utf-8") as f:
                if config_path.endswith(".json"):
                    config_data = json.load(f)
                elif config_path.endswith((".yaml", ".yml")):
                    config_data = yaml.safe_load(f)
                else:
                    logger.error(
                        f"CONFIG LOAD FAILURE: Unsupported format: {config_path}",
                        exc_info=True,
                    )
                    raise AnalyzerCriticalError(
                        f"Unsupported configuration file format: {config_path}. Must be .json or .yaml. Aborting startup."
                    )

        config_data = AnalyzerConfig.validate_paths(config_data)
        config = AnalyzerConfig(**config_data)
        logger.info("Configuration loaded successfully.")
        audit_logger.log_event(
            "config_loaded", path=config_path, config_summary=list(config_data.keys())
        )
        if PRODUCTION_MODE and "mock" in config.llm_endpoint:
            raise AnalyzerCriticalError(
                "Mock LLM endpoint detected in PRODUCTION_MODE. Aborting for security."
            )
        return config
    except (ClientError, json.JSONDecodeError, yaml.YAMLError, ValidationError) as e:
        logger.error(
            f"CONFIG LOAD FAILURE. Check file format, permissions, and AWS SSM configuration: {e}",
            exc_info=True,
        )
        raise AnalyzerCriticalError(
            f"Failed to load/validate configuration: {e}. Aborting startup."
        )
    except Exception as e:
        logger.error(f"Unexpected error loading configuration: {e}", exc_info=True)
        raise AnalyzerCriticalError(
            f"Unexpected error loading configuration: {e}. Aborting startup."
        )


def validate_output_dir(output_dir: str, project_root: str):
    abs_out = os.path.abspath(output_dir)
    abs_root = os.path.abspath(project_root)
    if not abs_out.startswith(abs_root):
        raise AnalyzerCriticalError(
            f"Output directory {abs_out} must be within project root {abs_root}."
        )
    if os.path.islink(abs_out) or ".." in abs_out:
        raise AnalyzerCriticalError(
            f"Output directory {abs_out} cannot be a symlink or contain traversal."
        )
    try:
        os.makedirs(abs_out, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create output directory {abs_out}: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Failed to create output directory {abs_out}: {e}")
    if not os.access(abs_out, os.W_OK):
        raise AnalyzerCriticalError(f"No write access to output directory {abs_out}")


def async_wrap(func):
    """Decorator to run a synchronous function in a separate thread or fallback if in event loop."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except RuntimeError:
            return func(*args, **kwargs)

    return wrapper


@async_wrap
def _handle_analyze(project_root, app_config, output_dir):
    from .core_audit import audit_logger

    try:
        audit_logger.log_event("action_analyze_start", project_root=project_root)
        logger.info(f"Building import graph for {project_root}...")
        try:
            graph_analyzer = ImportGraphAnalyzer(project_root)
            graph = graph_analyzer.build_graph()
            cycles = graph_analyzer.detect_cycles(graph)
            dead_nodes = graph_analyzer.detect_dead_nodes(graph)
        except NonCriticalError as e:
            logger.warning(f"Non-critical error in graph analysis: {e}")
            audit_logger.log_event("non_critical_error", reason=str(e), action="analyze")
            graph, cycles, dead_nodes = {}, [], []
        logger.info(
            f"Graph analysis complete. Cycles found: {len(cycles)}, Dead nodes: {len(dead_nodes)}"
        )
        audit_logger.log_event(
            "graph_analysis_complete",
            project_root=project_root,
            cycles_found=len(cycles),
            dead_nodes_found=len(dead_nodes),
        )
        report_generator = ReportGenerator(output_dir)
        report_path = report_generator.generate_report(
            report_name="graph_analysis_report",
            results={
                "cycles": cycles,
                "dead_nodes": dead_nodes,
                "graph_summary": "Graph built successfully.",
            },
        )
        logger.info(f"Graph analysis report generated at {report_path}")
    except Exception as e:
        logger.error(f"Error during analyze action: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Analyze action failed: {e}")


@async_wrap
def _handle_check_policy(project_root, app_config, output_dir):
    from .core_audit import audit_logger

    try:
        audit_logger.log_event(
            "action_check_policy_start",
            project_root=project_root,
            policy_file=app_config.policy_rules_file,
        )
        logger.info(f"Checking architectural policies for {project_root}...")
        if not app_config.policy_rules_file:
            raise AnalyzerCriticalError(
                "Policy rules file not specified in configuration for policy check."
            )
        policy_file = os.path.abspath(app_config.policy_rules_file)
        if not os.path.isfile(policy_file):
            raise AnalyzerCriticalError(f"Policy file {policy_file} does not exist.")
        policy_manager = PolicyManager(policy_file)
        temp_graph_analyzer = ImportGraphAnalyzer(project_root)
        temp_graph = temp_graph_analyzer.build_graph()
        temp_cycles = temp_graph_analyzer.detect_cycles(temp_graph)
        temp_dead_nodes = temp_graph_analyzer.detect_dead_nodes(temp_graph)
        policy_violations = policy_manager.check_architectural_policies(
            code_graph=temp_graph,
            module_paths=temp_graph_analyzer.module_paths,
            detected_cycles=temp_cycles,
            dead_nodes=temp_dead_nodes,
        )
        logger.info(f"Policy check complete. Violations found: {len(policy_violations)}")
        audit_logger.log_event(
            "policy_check_complete",
            project_root=project_root,
            violations_found=len(policy_violations),
            details=[v.model_dump() for v in policy_violations],
        )
        if policy_violations:
            critical_violations = [
                v for v in policy_violations if v.severity in ["high", "critical"]
            ]
            if critical_violations:
                alert_operator(
                    f"CRITICAL: Policy check FAILED for {project_root}. {len(critical_violations)} critical violations found.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("Critical policy violations detected.")
            else:
                logger.warning(
                    f"Policy check completed with {len(policy_violations)} non-critical violations for {project_root}."
                )
        report_generator = ReportGenerator(output_dir)
        report_path = report_generator.generate_report(
            report_name="policy_violations_report",
            results={"violations": [v.model_dump() for v in policy_violations]},
        )
        logger.info(f"Policy check report generated at {report_path}")
    except Exception as e:
        logger.error(f"Error during policy check action: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Policy check action failed: {e}")


@async_wrap
def _handle_security_scan(project_root, app_config, output_dir):
    from .core_audit import audit_logger

    try:
        audit_logger.log_event("action_security_scan_start", project_root=project_root)
        logger.info(f"Performing security scan for {project_root}...")
        security_analyzer = SecurityAnalyzer(project_root)
        scan_results = security_analyzer.perform_security_scan()
        logger.info(
            f"Security scan complete. Overall status: {scan_results.get('overall_status','UNKNOWN')}"
        )
        audit_logger.log_event(
            "security_scan_complete",
            project_root=project_root,
            status=scan_results.get("overall_status", "UNKNOWN"),
            summary=scan_results.get("summary"),
        )
        if scan_results.get("overall_status") == "FAIL":
            alert_operator(
                f"CRITICAL: Security scan FAILED for {project_root}. Issues detected.",
                level="CRITICAL",
            )
            raise AnalyzerCriticalError("Security scan failed with detected issues.")
        report_generator = ReportGenerator(output_dir)
        report_path = report_generator.generate_report(
            report_name="security_scan_report", results=scan_results
        )
        logger.info(f"Security scan report generated at {report_path}")
    except Exception as e:
        logger.error(f"Error during security scan action: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Security scan action failed: {e}")


@async_wrap
def _handle_suggest_patch(project_root, app_config, output_dir, dry_run):
    from .core_audit import audit_logger

    try:
        audit_logger.log_event(
            "action_suggest_patch_start", project_root=project_root, dry_run=dry_run
        )
        logger.info(f"Generating AI-driven suggestions and patches for {project_root}...")
        codebase_context = "Needs context from graph analysis or other sources."
        problem_description = "Describe the problem here."
        relevant_code = "print('Hello World')"
        ai_config = app_config.ai_config
        if PRODUCTION_MODE and ai_config.get("use_mock_ai_backend"):
            raise AnalyzerCriticalError("Mock AI backend detected in production. Aborting.")
        suggestions = get_ai_suggestions(codebase_context, config=ai_config)
        patches = get_ai_patch(problem_description, relevant_code, suggestions, config=ai_config)
        logger.info(
            f"AI suggestions and patches generated. Suggestions: {len(suggestions)}, Patches: {len(patches)}"
        )
        audit_logger.log_event(
            "ai_suggestions_patches_generated",
            project_root=project_root,
            suggestions_count=len(suggestions),
            patches_count=len(patches),
        )
        if not suggestions and not patches:
            alert_operator(
                f"WARNING: AI suggestions/patches generation failed for {project_root}. No output.",
                level="WARNING",
            )
        if not dry_run:
            logger.info("Patch application is not supported in production. Aborting apply.")
            raise AnalyzerCriticalError(
                "Patch application is not implemented or is disabled for safety in production."
            )
        report_generator = ReportGenerator(output_dir)
        report_path = report_generator.generate_report(
            report_name="ai_patch_report",
            results={"suggestions": suggestions, "patches": patches},
        )
        logger.info(f"AI suggestions and patches report generated at {report_path}")
    except Exception as e:
        logger.error(f"Error during suggest patch action: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Suggest patch action failed: {e}")


async def _handle_health_check(project_root, app_config):
    from .core_audit import audit_logger

    try:
        audit_logger.log_event("action_health_check_start")
        logger.info("Performing overall system health check...")
        health_statuses = {}

        async def check_components():
            tasks = [
                asyncio.to_thread(lambda: ImportGraphAnalyzer(project_root)),
                asyncio.to_thread(
                    lambda: (
                        PolicyManager(app_config.policy_rules_file)
                        if app_config.policy_rules_file
                        else True
                    )
                ),
                asyncio.to_thread(
                    lambda: SecurityAnalyzer(project_root).security_health_check(check_only=True)
                ),
                asyncio.to_thread(
                    lambda: get_ai_suggestions("health check", config=app_config.ai_config)
                ),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = await check_components()
        component_names = [
            "graph_analyzer",
            "policy_manager",
            "security_analyzer",
            "ai_manager",
        ]
        for name, result in zip(component_names, results):
            if isinstance(result, Exception):
                health_statuses[name] = False
                logger.error(f"{name} component unhealthy: {result}", exc_info=True)
                alert_operator(
                    f"CRITICAL: Analyzer {name.replace('_', ' ').title()} component unhealthy: {result}.",
                    level="CRITICAL",
                )
            else:
                health_statuses[name] = True if name != "security_analyzer" else result
                logger.info(f"{name} component is healthy.")
        overall_status = all(health_statuses.values())
        logger.info(f"Overall system health: {'HEALTHY' if overall_status else 'UNHEALTHY'}")
        audit_logger.log_event(
            "health_check_complete", overall_status=overall_status, **health_statuses
        )
        if not overall_status:
            alert_operator(
                "CRITICAL: Overall Analyzer health check FAILED. Some components are unhealthy.",
                level="CRITICAL",
            )
            raise AnalyzerCriticalError("Overall health check failed.")
    except Exception as e:
        logger.error(f"Error during health check action: {e}", exc_info=True)
        raise AnalyzerCriticalError(f"Health check action failed: {e}")


# --- Resource Management and Shutdown Hooks ---
AI_MANAGER = None  # Placeholder for your AI client instance


def _shutdown():
    """Graceful shutdown hook for closing resources."""
    try:
        if AI_MANAGER is not None and hasattr(AI_MANAGER, "aclose"):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, run the coroutine directly
                asyncio.run(AI_MANAGER.aclose())
            else:
                # Event loop is running, schedule the coroutine
                asyncio.run_coroutine_threadsafe(AI_MANAGER.aclose(), loop).result()
    except Exception:
        logging.exception("Error during analyzer shutdown")


atexit.register(_shutdown)


@click.command()
@click.argument(
    "action",
    type=click.Choice(
        ["analyze", "check-policy", "security-scan", "suggest-patch", "health-check"]
    ),
)
@click.option(
    "--path",
    default=".",
    help="Path to the codebase to analyze (default: current directory).",
)
@click.option(
    "--config",
    default="config.yaml",
    help="Path to the configuration file (default: config.yaml).",
)
@click.option(
    "--output-dir",
    default="reports",
    help="Output directory for reports (default: reports).",
)
@click.option("--verbose", is_flag=True, help="Enable verbose logging.")
@click.option("--production-mode", is_flag=True, help="Force production mode (overrides env var).")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Simulate execution without making changes or generating final reports.",
)
def main(action, path, config, output_dir, verbose, production_mode, dry_run):
    """
    Main entry point for the Analyzer CLI, using click for a more robust interface.
    """
    from .core_audit import audit_logger

    global PRODUCTION_MODE
    if production_mode:
        PRODUCTION_MODE = True
        logger.info("PRODUCTION_MODE forced ON via CLI flag.")

    if verbose:
        logger.setLevel(logging.DEBUG)

    start_time = datetime.datetime.utcnow()
    logger.info(f"Starting Analyzer with action: {action}")

    # Audit Logging: Log detailed metadata for each action.
    app_config = load_config(config)
    audit_logger.log_event(
        "action_metadata",
        action=action,
        project_root=path,
        config=scrub_secrets(app_config.model_dump()),
    )

    # Mandatory CLI Input Validation
    project_root = os.path.abspath(path)
    if not os.path.isdir(project_root):
        logger.critical(
            f"Project root path '{project_root}' is not a valid directory. Aborting startup."
        )
        logger.error(
            "For directory errors: ensure the path exists and permissions are correct, and not a symlink/traversal."
        )
        raise AnalyzerCriticalError("Invalid project root directory.")

    # Output directory validation and creation
    validate_output_dir(output_dir, project_root)

    # No Unprotected Demo/Dev Modes in Prod
    if PRODUCTION_MODE and app_config.demo_mode_enabled:
        logger.critical("Demo mode is enabled in PRODUCTION_MODE. Aborting for security.")
        raise AnalyzerCriticalError("Demo mode enabled in production.")
    if PRODUCTION_MODE and not app_config.audit_logging_enabled:
        logger.critical("Audit logging is DISABLED in PRODUCTION_MODE. Aborting for compliance.")
        raise AnalyzerCriticalError("Audit logging disabled in production.")

    actions: Dict[str, Callable] = {
        "analyze": _handle_analyze,
        "check-policy": _handle_check_policy,
        "security-scan": _handle_security_scan,
        "suggest-patch": _handle_suggest_patch,
        "health-check": _handle_health_check,
    }

    # Run the selected asynchronous action
    if action in actions:
        if action == "suggest-patch":
            asyncio.run(actions[action](project_root, app_config, output_dir, dry_run))
        else:
            asyncio.run(actions[action](project_root, app_config, output_dir))

    end_time = datetime.datetime.utcnow()
    duration = (end_time - start_time).total_seconds()
    logger.info("Analyzer execution finished.")
    audit_logger.log_event(
        "analyzer_end",
        action=action,
        status="success",
        duration_seconds=duration,
        end_time=end_time.isoformat() + "Z",
    )


if __name__ == "__main__":
    try:
        # Wrap the main function call in a try/except block to handle all exceptions.
        # This is the single point of failure for the entire process.
        main()
    except Exception as e:
        logger.critical(f"Fatal error in analyzer: {e}", exc_info=True)
        # We need to import audit_logger here for the final error logging
        from .core_audit import audit_logger

        audit_logger.log_event(
            "analyzer_execution_failure",
            action="startup",
            error=str(e),
            end_time=datetime.datetime.utcnow().isoformat() + "Z",
        )
        sys.exit(1)
