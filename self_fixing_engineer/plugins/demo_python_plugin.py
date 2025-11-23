import os
import sys
import json
import logging
import datetime
import importlib.util  # For dynamic dependency checks
from typing import Any
from omnicore_engine.plugin_registry import plugin, PlugInKind

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# --- Logging Setup ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# --- Custom Exception for recoverable issues ---
class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


# --- Centralized Utilities (replacing placeholders) ---
try:
    from core_utils import alert_operator, scrub_secrets
    from core_audit import audit_logger
except ImportError:
    logger.warning("core_utils or core_audit not found. Plugin functionality will be limited.")

    def alert_operator(message, level="CRITICAL"):
        logger.critical(f"[OPS ALERT - {level}] {message}")

    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            logger.info(f"[AUDIT_LOG_DISABLED] {event_type}: {kwargs}")

    audit_logger = DummyAuditLogger()

# BLOCKER: Never deploy or enable demo/test plugins in production.
# This check is now an explicit part of the main orchestrator's PluginManager.
# However, this local check is a defense-in-depth measure.
if PRODUCTION_MODE:
    logger.critical(
        "CRITICAL: Attempting to load 'demo_python_plugin.py' in PRODUCTION_MODE. This plugin is for demo/test purposes only and is forbidden in production builds. Aborting startup."
    )
    alert_operator(
        "CRITICAL: Demo plugin 'demo_python_plugin.py' detected in PRODUCTION_MODE. Aborting.",
        level="CRITICAL",
    )
    sys.exit(1)


# Manifest Controls: All prod plugins must have real, operator-reviewed manifest fields.
# For a demo plugin, we'll ensure its manifest explicitly marks it as a demo.
PLUGIN_MANIFEST = {
    "name": "demo_python_plugin",
    "version": "0.0.1",
    "description": "A demo python plugin generated for safe mode. NOT FOR PRODUCTION USE.",  # Explicitly mark as NOT FOR PROD
    "entrypoint": "demo_python_plugin.py",
    "type": "python",
    "author": "Omnisapient Wizard",
    "capabilities": ["demo_capability", "test_feature_a"],
    "permissions": [  # Declare actual permissions for health check
        "none",  # For demo, this is fine. Real plugins should declare minimum required.
        "read_filesystem",  # Example: if it needed to read a file
        "network_access_limited",  # Example: if it needed limited network access
    ],
    "dependencies": [  # Declare actual dependencies for health check
        "requests",  # Example dependency
        "numpy",  # Another example
    ],
    "min_core_version": "1.1.0",
    "max_core_version": "2.0.0",
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://example.com/demo_plugin",  # Real homepage
    "tags": [
        "demo",
        "onboarding",
        "testing_only",  # Explicitly mark for testing
        "do_not_deploy_to_prod",  # Explicit warning tag
    ],
    "generated_with": {
        "wizard_version": "1.0.0",
        "python_version": sys.version,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    },
    "is_demo_plugin": True,  # Security Enhancement: Explicitly mark as demo
    "signature": "PLACEHOLDER_FOR_HMAC_SIGNATURE",  # Security Enhancement: Placeholder for manifest signature
}


# Health Check: Health function (plugin_health) must actually validate plugin dependencies and runtime.
def plugin_health():
    """
    Performs a health check for the demo plugin.
    Checks for declared dependencies and simulates a runtime check.
    """
    health_status = {
        "status": "ok",
        "message": "Demo Python plugin is healthy!",
        "details": {},
    }

    # Check declared dependencies
    missing_deps = []
    for dep in PLUGIN_MANIFEST.get("dependencies", []):
        try:
            # Check for module availability
            if importlib.util.find_spec(dep) is None:
                missing_deps.append(dep)
        except Exception:  # Catch any error during spec finding
            missing_deps.append(dep)

    if missing_deps:
        health_status["status"] = "degraded"
        health_status["message"] = f"Missing optional dependencies: {', '.join(missing_deps)}"
        health_status["details"]["missing_dependencies"] = missing_deps
        logger.warning(f"Plugin health check: {health_status['message']}", extra=health_status)
        audit_logger.log_event(
            "plugin_health_degraded",
            plugin=PLUGIN_MANIFEST["name"],
            reason="missing_dependencies",
            details=scrub_secrets(missing_deps),
        )

    # Simulate a runtime check (e.g., connectivity to a dummy external service)
    try:
        # This would be a real check in a non-demo plugin
        # For demo, simulate success/failure
        if os.getenv("DEMO_PLUGIN_HEALTH_FAIL", "false").lower() == "true":
            raise NonCriticalError("Simulated runtime failure for demo plugin.")
        health_status["details"]["runtime_check"] = "passed"
    except NonCriticalError as e:
        health_status["status"] = "unhealthy"
        health_status["message"] = f"Runtime check failed: {e}"
        health_status["details"]["runtime_check"] = "failed"
        logger.error(
            f"Plugin health check: {health_status['message']}",
            exc_info=True,
            extra=health_status,
        )
        audit_logger.log_event(
            "plugin_health_unhealthy",
            plugin=PLUGIN_MANIFEST["name"],
            reason="runtime_failure",
            error=scrub_secrets(str(e)),
        )
        alert_operator(
            f"WARNING: Demo plugin '{PLUGIN_MANIFEST['name']}' health check failed: {e}",
            level="WARNING",
        )
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["message"] = f"Runtime check failed: {e}"
        health_status["details"]["runtime_check"] = "failed"
        logger.error(
            f"Plugin health check: {health_status['message']}",
            exc_info=True,
            extra=health_status,
        )
        audit_logger.log_event(
            "plugin_health_unhealthy",
            plugin=PLUGIN_MANIFEST["name"],
            reason="runtime_failure",
            error=scrub_secrets(str(e)),
        )
        alert_operator(
            f"WARNING: Demo plugin '{PLUGIN_MANIFEST['name']}' health check failed: {e}",
            level="WARNING",
        )

    audit_logger.log_event(
        "plugin_health_check",
        plugin=PLUGIN_MANIFEST["name"],
        status=health_status["status"],
        details=scrub_secrets(health_status),
    )
    return health_status


@plugin(
    kind=PlugInKind.EXECUTION,
    name="demo_python_plugin",
    description="A simple demo Python plugin.",
    version="0.0.1",
    safe=True,
)
class PLUGIN_API:
    def hello(self):
        # Audit: Any execution, even in test mode, must be audit-logged in prod environments (if run for QA/testing).
        # This check is for if the main orchestrator is in a "test/QA" production mode.
        if os.getenv("RUN_QA_TESTS", "false").lower() == "true":
            audit_logger.log_event(
                "demo_plugin_hello_executed_in_qa", plugin=PLUGIN_MANIFEST["name"]
            )
            logger.info("Demo plugin 'hello' method executed in QA mode.")

        return "Hello from the safe mode demo Python plugin!"


if __name__ == "__main__":
    # This block is for direct execution of the plugin file, typically for testing/development.
    # In production, plugins are loaded by the main orchestrator.
    # The PRODUCTION_MODE check at the top handles blocking this file in prod builds.

    print(json.dumps(PLUGIN_MANIFEST, indent=4))

    # Run health check
    health_result = plugin_health()
    print(f"Plugin Health: {health_result}")

    # Instantiate API and call method
    api_instance = PLUGIN_API()
    print(api_instance.hello())

    # Example of how to trigger a simulated health failure for testing
    # os.environ["DEMO_PLUGIN_HEALTH_FAIL"] = "true"
    # print("\n--- Testing simulated health failure ---")
    # health_result_fail = plugin_health()
    # print(f"Plugin Health (simulated fail): {health_result_fail}")
    # del os.environ["DEMO_PLUGIN_HEALTH_FAIL"] # Clean up
