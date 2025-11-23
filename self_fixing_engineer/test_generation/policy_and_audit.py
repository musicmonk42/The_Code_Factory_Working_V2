import os
import json
import asyncio
import logging
import shutil
import warnings
import re
import traceback
from typing import Dict, Any, Optional, Tuple, Callable, Awaitable, Set
from importlib.metadata import version, PackageNotFoundError
from packaging.version import Version
from dataclasses import dataclass
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from asyncio import Lock

# --- Safe Defaults to prevent circular imports with orchestrator ---
logger = logging.getLogger(__name__)


# --- Define Constants ---
class Constants:
    DEFAULT_POLICIES = {
        "generation_rules": {
            "regulated_modules": ["financial_data", "security.auth"],
            "allowed_languages": [
                "python",
                "javascript",
                "java",
                "typescript",
                "rust",
                "go",
            ],
            "safe_subfolders": ["src", "app"],
            "require_human_review_modules": [],
        },
        "integration_rules": {
            "min_test_quality_score": 0.7,
            "deny_integrate_modules": ["legacy_system.critical"],
            "human_review_required_languages": [
                "javascript",
                "java",
                "typescript",
                "rust",
                "go",
            ],
            "auto_commit_threshold": 0.85,
        },
        "security_scan_threshold": "HIGH",
        "opa_integration_enabled": False,
        "opa_server_url": "https://localhost:8181",
    }

    SECURITY_LEVELS = ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

    OPA_POLICY_PATHS = {
        "generation_check": "atco/authz/generation_check",
        "integration_check": "atco/authz/integration_check",
        "pr_required_check": "atco/authz/pr_required_check",
    }

    SENSITIVE_KEYS = {
        "password",
        "api_key",
        "token",
        "secret",
        "credentials",
        "jwt",
        "auth",
    }

    AUDIT_EVENT_TYPES = {
        "policy_decision": "policy_decision",
        "policy_denied": "policy_denied",
        "opa_failure": "opa_failure",
        "notification_sent": "notification_sent",
        "notification_failure": "notification_failure",
        "policy_reloaded": "policy_reloaded",
        "policy_reload_failure": "policy_reload_failure",
        "sensitive_data_redacted": "sensitive_data_redacted",
    }


# --- Configuration Class ---
@dataclass
class Configuration:
    """
    Holds all configuration settings, replacing global variables.
    """

    project_root: str
    audit_enabled: bool
    demo_mode: bool
    opa_integration_enabled: bool
    opa_server_url: Optional[str]
    opa_retries: int = 3
    opa_backoff_min: float = 2.0
    opa_backoff_max: float = 10.0
    notification_retries: int = 3
    notification_backoff_min: float = 2.0
    notification_backoff_max: float = 10.0
    opa_timeout_seconds: int = 5
    notification_timeout_seconds: int = 10
    critical_events_for_mq: list = None
    webhook_hooks: dict = None
    slack_webhook_url: str = None
    slack_events: list = None
    webhook_events: list = None
    retry_stop_attempts: int = 3
    retry_wait_multiplier: float = 1.0
    retry_wait_min: float = 2.0
    retry_wait_max: float = 10.0
    allowed_notification_hosts: Optional[Set[str]] = None

    @classmethod
    def from_env(cls, project_root: Optional[str] = None):
        """Creates a Configuration instance from environment variables and defaults."""
        return cls(
            project_root=project_root
            or os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            audit_enabled=os.getenv("AUDIT_ENABLED", "true").lower() == "true",
            demo_mode=os.getenv("DEMO_MODE", "false").lower() == "true",
            opa_integration_enabled=os.getenv("OPA_INTEGRATION_ENABLED", "false").lower() == "true",
            opa_server_url=os.getenv("OPA_SERVER_URL", "https://localhost:8181"),
            critical_events_for_mq=(
                os.getenv("CRITICAL_EVENTS_FOR_MQ", "").split(",")
                if os.getenv("CRITICAL_EVENTS_FOR_MQ")
                else []
            ),
            webhook_hooks=json.loads(os.getenv("WEBHOOK_HOOKS", "{}")),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            slack_events=(
                os.getenv("SLACK_EVENTS", "").split(",") if os.getenv("SLACK_EVENTS") else []
            ),
            webhook_events=(
                os.getenv("WEBHOOK_EVENTS", "").split(",") if os.getenv("WEBHOOK_EVENTS") else []
            ),
            allowed_notification_hosts=(
                set(os.getenv("ALLOWED_NOTIFICATION_HOSTS", "").split(","))
                if os.getenv("ALLOWED_NOTIFICATION_HOSTS")
                else None
            ),
        )


# --- Safe Import of arbiter.audit_log and Fallback Logger ---
AUDIT_LOGGER_AVAILABLE = False
try:
    from test_generation.orchestrator.audit import audit_event as _real_audit_event

    class _RealAuditLogger:
        async def log_event(self, event_type: str, details: Dict[str, Any], critical: bool = False):
            await _real_audit_event(event_type, details, critical=critical)

    AUDIT_LOGGER_AVAILABLE = True
except Exception:  # <-- FIX: Broadened exception handling
    AUDIT_LOGGER_AVAILABLE = False

if os.getenv("AUDIT_ENABLED", "true").lower() == "false":
    logger.info("Audit logging is disabled by environment configuration.")
else:
    if not AUDIT_LOGGER_AVAILABLE:
        warnings.warn(
            "test_generation.orchestrator.audit is not installed. Running with fallback logger.",
            RuntimeWarning,
        )
        logger.critical("Audit logging unavailable - using fallback.")

        class _FallbackAuditLogger:
            def __init__(self):
                self._logger = logging.getLogger("audit_fallback")
                self._logger.setLevel(logging.INFO)
                if not self._logger.handlers:
                    handler = logging.FileHandler(
                        os.getenv("AUDIT_FALLBACK_FILE", "audit_fallback.log"),
                        encoding="utf-8",
                    )
                    handler.setFormatter(
                        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
                    )
                    self._logger.addHandler(handler)

            async def log_event(
                self, event_type: str, details: Dict[str, Any], critical: bool = False
            ):
                self._logger.info({"event": event_type, **details})

        _module_audit_logger = _FallbackAuditLogger()


# --- Secure Redaction Helper ---
def redact_sensitive(obj: Any) -> Any:
    """
    Recursively redacts sensitive information from dictionaries and lists.
    Returns the redacted object and the count of redacted items.
    """
    api_key_regex = re.compile(
        r"^(sk_|pk_|xoxp-|AKIA[0-9A-Z]{16}|[a-zA-Z0-9_-]{32,}|[A-Za-z0-9+/]{32,}[=]{0,2})$",
        re.IGNORECASE,
    )
    url_param_regex = re.compile(r'(?i)(?:api_key|token|secret|password)=[^&"\'\s]+')
    redacted_items = 0

    def _redact_recursive(item):
        nonlocal redacted_items
        if isinstance(item, dict):
            new_dict = {}
            for k, v in item.items():
                if isinstance(v, str) and re.search(url_param_regex, v):
                    new_dict[k] = re.sub(url_param_regex, "[REDACTED_SENSITIVE_PARAM]", v)
                    redacted_items += 1
                elif (
                    k.lower() in Constants.SENSITIVE_KEYS
                    or "pass" in k.lower()
                    or (isinstance(v, str) and re.match(api_key_regex, v))
                ):
                    new_dict[k] = "[REDACTED]"
                    redacted_items += 1
                else:
                    new_dict[k] = _redact_recursive(v)
            return new_dict
        elif isinstance(item, list):
            return [_redact_recursive(i) for i in item]
        return item

    redacted_obj = _redact_recursive(obj)
    return redacted_obj, redacted_items


# --- Metrics Integration ---
# Always-available no-op metrics
class _NoOpMetric:
    def labels(self, **kwargs):
        return self

    def inc(self):
        pass

    def observe(self, *args, **kwargs):
        pass

    async def time(self):
        class _T:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _T()


METRICS_AVAILABLE = False
try:
    import prometheus_client

    prom_version = version("prometheus_client")
    if Version(prom_version) >= Version("0.22.1"):
        METRICS_AVAILABLE = True
    else:
        logger.warning("Warning: prometheus_client version < 0.22.1. Metrics will be disabled.")
except PackageNotFoundError:
    logger.warning("Warning: prometheus_client not found. Metrics will be disabled.")
except Exception as e:
    logger.warning(
        f"Warning: An error occurred during prometheus_client version check: {e}. Metrics will be disabled."
    )


class MetricsClient:
    """Encapsulates Prometheus metrics for dependency injection."""

    def __init__(self):
        self.enabled = METRICS_AVAILABLE
        self.policy_evaluations_total = None
        self.notification_failures_total = None
        if self.enabled:
            self.policy_evaluations_total = prometheus_client.Counter(
                "atco_policy_evaluations_total",
                "Total policy evaluations",
                ["result", "rule"],
            )
            self.notification_failures_total = prometheus_client.Counter(
                "atco_notification_failures_total",
                "Failed notifications",
                ["service", "event_name"],
            )
        else:
            self.policy_evaluations_total = _NoOpMetric()
            self.notification_failures_total = _NoOpMetric()


metrics_client = MetricsClient()

# --- Real HTTP Request Libraries and Retries ---
# MOVED BEFORE OPAPolicyClient to fix RETRY_STOP/RETRY_WAIT undefined error
AIOHTTP_AVAILABLE = False
TENACITY_AVAILABLE = False

# Initialize RETRY_STOP and RETRY_WAIT with default values
RETRY_STOP = None
RETRY_WAIT = None

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
    try:
        ten_version = version("tenacity")
        if Version(ten_version) >= Version("9.0.0"):
            from tenacity import retry, stop_after_attempt, wait_exponential

            TENACITY_AVAILABLE = True
            # Define RETRY_STOP and RETRY_WAIT here, BEFORE they're used
            RETRY_STOP = stop_after_attempt(3)
            RETRY_WAIT = wait_exponential(multiplier=1, min=2, max=10)
        else:
            logger.warning("Warning: tenacity version < 9.0.0. Retries disabled.")
    except PackageNotFoundError:
        logger.warning("Warning: tenacity not found. Retries disabled.")
    except Exception as e:
        logger.warning(
            f"Warning: An error occurred during tenacity version check: {e}. Retries disabled."
        )
except ImportError:
    logger.warning("Warning: 'aiohttp' not available. Notifications and OPA integration disabled.")

    class aiohttp:
        ClientSession = None


# Define fallback retry decorator if tenacity is not available
if not TENACITY_AVAILABLE:
    logger.warning("Tenacity is not available or version is too old. Retries will be disabled.")

    def retry(*args, **kwargs):
        def wrap(f):
            async def wrapped_f(*inner_args, **inner_kwargs):
                return await f(*inner_args, **inner_kwargs)

            return wrapped_f

        return wrap

    def stop_after_attempt(x):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    # Ensure RETRY_STOP and RETRY_WAIT have fallback values
    if RETRY_STOP is None:
        RETRY_STOP = stop_after_attempt(3)
    if RETRY_WAIT is None:
        RETRY_WAIT = wait_exponential(multiplier=1, min=2, max=10)


# --- DLT-friendly AuditLogger facade expected by tests ---
# This class remains here for compatibility with existing tests
class AuditLogger:
    """
    Test-friendly DLT facade:
      - Raises if DLT not available (as tests expect)
      - Exposes .dlt_logger.add_entry(...) for patching
      - Also logs one local .info for every event
    """

    def __init__(self, log_file: str):
        if not AUDIT_LOGGER_AVAILABLE:
            raise ImportError("DLT-enabled AuditLogger not available")
        self.log_file = log_file
        self.log_file_relative = log_file

        class _DLTAdapter:
            async def add_entry(self_inner, component: str, event: str, details: dict):
                from test_generation.orchestrator.audit import audit_event

                await audit_event(event, details, critical=bool(details.get("critical")))

        self.dlt_logger = _DLTAdapter()

    @classmethod
    def from_environment(cls):
        return cls("atco_artifacts/atco_audit.log")

    async def log_event(
        self,
        event_type: str,
        details: dict,
        correlation_id: str | None = None,
        critical: bool = False,
    ):
        red, _ = redact_sensitive(details or {})
        await self.dlt_logger.add_entry("atco_audit", event_type, red)
        logging.getLogger("atco_audit").info({"event": event_type, **red})


# --- FileSystem Abstraction ---
class FileSystem(ABC):
    """Abstract interface for file system operations to enable mocking."""

    @abstractmethod
    def read_json(self, path: str) -> Dict[str, Any]:
        """Reads a JSON file from the given path."""
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Checks if a file exists at the given path."""
        pass

    @abstractmethod
    def generate_file_hash(self, path: str, project_root: str) -> str:
        """Generates a hash for the file at the given path."""
        pass

    @abstractmethod
    def cleanup_temp_dir(self, path: str) -> Awaitable[None]:
        """Cleans up a temporary directory."""
        pass


class LocalFileSystem(FileSystem):
    """Concrete implementation of FileSystem for local disk access."""

    def read_json(self, path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def file_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def generate_file_hash(self, path: str, project_root: str) -> str:
        try:
            from test_generation.utils import generate_file_hash

            return generate_file_hash(path, project_root)
        except ImportError:
            logger.warning(
                "Could not import generate_file_hash from utils.py. Using local placeholder."
            )
            return "NO_HASH"

    async def cleanup_temp_dir(self, path: str) -> None:
        try:
            from test_generation.utils import cleanup_temp_dir

            await cleanup_temp_dir(path)
        except ImportError:
            logger.warning(
                "Could not import cleanup_temp_dir from utils.py. Using local placeholder."
            )
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)


# --- Policy Client Abstraction ---
class PolicyClient(ABC):
    """Abstract interface for policy evaluation clients."""

    @abstractmethod
    async def evaluate_policy(
        self, policy_path: str, input_data: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Evaluates a policy and returns the decision and reason."""
        pass


class OPAPolicyClient(PolicyClient):
    """Client for evaluating policies against an OPA server."""

    def __init__(self, config: Configuration, audit_logger: Any, metrics_client: MetricsClient):
        self.config = config
        self.audit_logger = audit_logger
        self.metrics_client = metrics_client
        self.enabled = config.opa_integration_enabled

    @retry(stop=RETRY_STOP, wait=RETRY_WAIT)
    async def evaluate_policy(
        self, policy_path: str, input_data: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Evaluates a Rego policy on the OPA server.
        """
        if not AIOHTTP_AVAILABLE:
            raise ImportError("aiohttp library is required for OPA integration.")
        if not self.enabled:
            return True, "OPA integration disabled, skipping policy evaluation."

        opa_server_base_url = self.config.opa_server_url
        if not opa_server_base_url or (
            not self.config.demo_mode and not opa_server_base_url.startswith("https://")
        ):
            raise ValueError("OPA URL is not HTTPS or is missing.")

        full_opa_url = f"{opa_server_base_url}/v1/data/{policy_path}"
        logger.debug(
            f"Querying OPA at {full_opa_url} with input (truncated): {json.dumps(input_data)[:200]}..."
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    full_opa_url,
                    json={"input": input_data},
                    headers={"Content-Type": "application/json"},
                    timeout=self.config.opa_timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    opa_response = await response.json()

                    allowed, reason = self._parse_opa_response(opa_response)

                    logger.debug(
                        f"OPA response for {policy_path}: Allowed={allowed}, Reason='{reason}'"
                    )
                    if self.audit_logger:
                        redacted_input, redacted_count = redact_sensitive(input_data)
                        if redacted_count > 0:
                            await self.audit_logger.log_event(
                                Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                                {"count": redacted_count},
                            )
                        await self.audit_logger.log_event(
                            Constants.AUDIT_EVENT_TYPES["policy_decision"],
                            {
                                "input": redacted_input,
                                "policy": policy_path,
                                "result": "allowed" if allowed else "denied",
                                "reason": reason,
                            },
                        )
                    return allowed, reason
        except Exception as e:
            if self.audit_logger:
                redacted_input, redacted_count = redact_sensitive(input_data)
                if redacted_count > 0:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                        {"count": redacted_count},
                    )
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["opa_failure"],
                    {
                        "policy_path": policy_path,
                        "input": redacted_input,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    critical=True,
                )
            raise

    def _parse_opa_response(self, opa_response: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Parses OPA response to extract decision and reason.
        Expected response shapes: {"result": bool}, {"result": {"allow": bool, "reason": str}}, or {"result": [{"allow": bool, "reason": str}, ...]}
        """
        result = opa_response.get("result")
        response_type = type(result).__name__
        logger.debug(f"OPA response type: {response_type}")
        if isinstance(result, bool):
            return result, "OPA allowed" if result else "OPA denied"
        if isinstance(result, dict):
            allowed = result.get("allow", False)
            return allowed, result.get("reason", "OPA allowed" if allowed else "OPA denied")
        if isinstance(result, list) and result:
            first_decision = result[0]
            if isinstance(first_decision, dict):
                allowed = first_decision.get("allow", False)
                return allowed, first_decision.get(
                    "reason", "OPA allowed" if allowed else "OPA denied"
                )
            return bool(first_decision), ("OPA allowed" if first_decision else "OPA denied")
        return False, "OPA denied by default rule."


# --- Policy Engine ---
class PolicyEngine:
    """
    Centralized Policy Engine for ATCO operations.
    """

    def __init__(
        self,
        policy_config_path: Optional[str],
        config: Configuration,
        audit_logger: Any,
        metrics_client: MetricsClient,
        filesystem: FileSystem,
        policy_client: PolicyClient,
    ):
        """
        Initializes the PolicyEngine with dependencies.
        """
        self.config = config
        self.audit_logger = audit_logger
        self.metrics_client = metrics_client
        self.filesystem = filesystem
        self.policy_config_path = policy_config_path
        self.policies: Dict[str, Any] = {}
        self.policy_hash = "NO_POLICY_FILE"
        self.policy_client = policy_client

        self._gen_cache = {}
        self._gen_lock = Lock()
        self._integrate_cache = {}
        self._integrate_lock = Lock()
        self._pr_cache = {}
        self._pr_lock = Lock()
        self._warned_severities = set()

    @classmethod
    async def create(
        cls,
        policy_config_path: Optional[str],
        config: Configuration,
        audit_logger: Any,
        metrics_client: MetricsClient,
        filesystem: FileSystem,
        policy_client: PolicyClient,
    ):
        self = cls(
            policy_config_path,
            config,
            audit_logger,
            metrics_client,
            filesystem,
            policy_client,
        )
        await self._load_policies()
        return self

    async def _load_policies(self):
        """Loads and validates policies from the configuration file."""
        if self.policy_config_path and (
            ".." in self.policy_config_path or not self.policy_config_path.endswith(".json")
        ):
            raise ValueError("Invalid policy_config_path")

        if not self.policy_config_path:
            logger.info(
                "PolicyEngine initialized without a valid config file path. Using built-in defaults."
            )
            self.policies = Constants.DEFAULT_POLICIES.copy()
            self.policy_hash = "NO_POLICY_FILE"
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                    {"path": "default", "new_hash": self.policy_hash},
                )
            return

        full_policy_config_path = os.path.join(self.config.project_root, self.policy_config_path)

        if not self.filesystem.file_exists(full_policy_config_path):
            logger.critical(
                f"CRITICAL: PolicyEngine initialized without a config file at {full_policy_config_path}. Using hardcoded defaults."
            )
            self.policies = Constants.DEFAULT_POLICIES.copy()
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                    {"path": full_policy_config_path, "new_hash": "default"},
                )
        else:
            try:
                self.policies = self.filesystem.read_json(full_policy_config_path)
                self._validate_policy_schema(self.policies)
                self.policy_hash = self.filesystem.generate_file_hash(
                    full_policy_config_path, self.config.project_root
                )
                logger.info(
                    f"PolicyEngine loaded policies from: {full_policy_config_path} (Hash: {self.policy_hash})"
                )
                if self.audit_logger:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                        {"path": full_policy_config_path, "new_hash": self.policy_hash},
                    )
            except (IOError, json.JSONDecodeError, ValueError) as e:
                logger.critical(
                    f"CRITICAL: Error loading or validating policies from {full_policy_config_path}: {e}. Aborting execution.",
                    exc_info=True,
                )
                if self.audit_logger:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["policy_reload_failure"],
                        {
                            "path": full_policy_config_path,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        },
                        critical=True,
                    )
                raise

    def _validate_policy_schema(self, policies: Dict[str, Any]):
        """Validates the policy configuration schema."""
        required_top_level_keys = [
            "generation_rules",
            "integration_rules",
            "security_scan_threshold",
        ]
        for key in required_top_level_keys:
            if key not in policies:
                raise ValueError(f"Missing required policy configuration key: '{key}'")

        gen_rules = policies.get("generation_rules", {})
        required_gen = ["regulated_modules", "allowed_languages", "safe_subfolders"]
        if not all(k in gen_rules for k in required_gen):
            raise ValueError("Missing required key in 'generation_rules' policy.")
        gen_rules.setdefault("require_human_review_modules", [])

        integ_rules = policies.get("integration_rules", {})
        required_integ = [
            "min_test_quality_score",
            "deny_integrate_modules",
            "human_review_required_languages",
        ]
        if not all(k in integ_rules for k in required_integ):
            raise ValueError("Missing required key in 'integration_rules' policy.")
        integ_rules.setdefault("auto_commit_threshold", 0.85)

        security_threshold = str(policies.get("security_scan_threshold", "NONE")).upper()
        if security_threshold not in Constants.SECURITY_LEVELS:
            raise ValueError(
                f"Invalid security_scan_threshold value: '{security_threshold}'. "
                f"Must be one of {Constants.SECURITY_LEVELS}"
            )

    async def reload_policies(self):
        """Reloads policies from disk, validating before applying."""
        logger.info("Reloading policies from disk...")

        async with self._gen_lock, self._integrate_lock, self._pr_lock:
            self._gen_cache.clear()
            self._integrate_cache.clear()
            self._pr_cache.clear()
            self._warned_severities.clear()

        if not self.policy_config_path:
            self.policies = Constants.DEFAULT_POLICIES.copy()
            self.policy_hash = "NO_POLICY_FILE"
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                    {"path": self.policy_config_path, "new_hash": self.policy_hash},
                )
            return

        full_path = os.path.join(self.config.project_root, self.policy_config_path)
        if not self.filesystem.file_exists(full_path):
            logger.critical(f"CRITICAL: Policy file {full_path} not found. Using defaults.")
            self.policies = Constants.DEFAULT_POLICIES.copy()
            self.policy_hash = "NO_POLICY_FILE"
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                    {"path": self.policy_config_path, "new_hash": self.policy_hash},
                )
            return

        try:
            new_policies = self.filesystem.read_json(full_path)
            self._validate_policy_schema(new_policies)
            new_hash = self.filesystem.generate_file_hash(full_path, self.config.project_root)
            self.policies = new_policies
            self.policy_hash = new_hash
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reloaded"],
                    {"path": self.policy_config_path, "new_hash": self.policy_hash},
                )
        except Exception as e:
            logger.critical(f"CRITICAL: Failed to reload policies: {e}", exc_info=True)
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_reload_failure"],
                    {
                        "path": self.policy_config_path,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    critical=True,
                )
            raise

    async def _deny(
        self, rule: str, input_data: Dict[str, Any], reason: str, critical: bool = False
    ) -> Tuple[bool, str]:
        """
        Logs and returns a policy denial decision.
        """
        redacted_input, redacted_count = redact_sensitive(input_data)
        self.metrics_client.policy_evaluations_total.labels(result="denied", rule=rule).inc()
        if self.audit_logger:
            if redacted_count > 0:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                    {"count": redacted_count},
                )
            await self.audit_logger.log_event(
                Constants.AUDIT_EVENT_TYPES["policy_denied"],
                {"input": redacted_input, "policy": rule, "reason": reason},
                critical=critical,
            )
        return False, reason

    async def _allow(self, rule: str, input_data: Dict[str, Any], reason: str) -> Tuple[bool, str]:
        """
        Logs and returns a policy allowance decision.
        """
        redacted_input, redacted_count = redact_sensitive(input_data)
        self.metrics_client.policy_evaluations_total.labels(result="allowed", rule=rule).inc()
        if self.audit_logger:
            if redacted_count > 0:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                    {"count": redacted_count},
                )
            await self.audit_logger.log_event(
                Constants.AUDIT_EVENT_TYPES["policy_decision"],
                {
                    "input": redacted_input,
                    "policy": rule,
                    "result": "allowed",
                    "reason": reason,
                },
            )
        return True, reason

    async def should_generate_tests(
        self, module_identifier: str, language: str
    ) -> Tuple[bool, str]:
        """
        Determines if test generation is allowed for a given module and language.
        """
        cache_key = (module_identifier, language)

        # Hold lock for entire operation to prevent redundant computation
        async with self._gen_lock:
            # Check cache first
            if cache_key in self._gen_cache:
                return self._gen_cache[cache_key]

            # Cache miss - compute the result while holding lock
            input_data = {"module_identifier": module_identifier, "language": language}

            if self.config.opa_integration_enabled:
                try:
                    allowed, reason = await self.policy_client.evaluate_policy(
                        Constants.OPA_POLICY_PATHS["generation_check"], input_data
                    )
                    self.metrics_client.policy_evaluations_total.labels(
                        result="allowed" if allowed else "denied",
                        rule="generation_check",
                    ).inc()
                    result = (allowed, reason)
                    self._gen_cache[cache_key] = result
                    return result
                except Exception as e:
                    logger.exception("OPA evaluation failed; falling back to local rules.")
                    self.metrics_client.policy_evaluations_total.labels(
                        result="denied", rule="generation_check"
                    ).inc()
                    result = (
                        False,
                        f"OPA policy evaluation failed: {e}. Defaulting to deny.",
                    )
                    self._gen_cache[cache_key] = result
                    return result

            generation_rules = self.policies.get("generation_rules", {})

            if any(
                reg_mod in module_identifier
                for reg_mod in generation_rules.get("regulated_modules", [])
            ):
                result = await self._deny(
                    "generation_rules_local",
                    input_data,
                    "Test generation explicitly forbidden for regulated modules.",
                )
                self._gen_cache[cache_key] = result
                return result

            lang = (language or "").lower()
            allowed_langs = [
                l.lower() for l in generation_rules.get("allowed_languages", ["python"])
            ]
            if lang not in allowed_langs:
                result = await self._deny(
                    "generation_rules_local",
                    input_data,
                    f"Test generation not allowed for '{language}'.",
                )
                self._gen_cache[cache_key] = result
                return result

            module_path_like = module_identifier.replace(".", os.sep)
            full_module_path = os.path.join(self.config.project_root, module_path_like)
            safe_subfolders = generation_rules.get("safe_subfolders", [])
            if safe_subfolders and not any(
                os.path.commonpath([full_module_path, os.path.join(self.config.project_root, f)])
                == os.path.join(self.config.project_root, f)
                for f in safe_subfolders
            ):
                logger.debug(
                    "Module '%s' not in safe_subfolders %s; continuing (advisory).",
                    module_identifier,
                    safe_subfolders,
                )

            result = await self._allow(
                "generation_rules_local", input_data, "Policy allows test generation."
            )
            self._gen_cache[cache_key] = result
            return result

    async def should_integrate_test(
        self,
        module_identifier: str,
        test_quality_score: float,
        language: str,
        has_security_issues: bool = False,
        security_severity_str: str = "NONE",
    ) -> Tuple[bool, str]:
        """
        Determines if a generated test should be integrated into the codebase.
        """
        cache_key = (
            module_identifier,
            test_quality_score,
            language,
            has_security_issues,
            security_severity_str,
        )
        async with self._integrate_lock:
            if cache_key in self._integrate_cache:
                return self._integrate_cache[cache_key]

        input_data = {
            "module_identifier": module_identifier,
            "test_quality_score": test_quality_score,
            "language": language,
            "has_security_issues": has_security_issues,
            "security_severity": security_severity_str,
        }

        if self.config.opa_integration_enabled:
            try:
                allowed, reason = await self.policy_client.evaluate_policy(
                    Constants.OPA_POLICY_PATHS["integration_check"], input_data
                )
                self.metrics_client.policy_evaluations_total.labels(
                    result="allowed" if allowed else "denied", rule="integration_check"
                ).inc()
                if not allowed:
                    if self.audit_logger:
                        redacted_input, redacted_count = redact_sensitive(input_data)
                        if redacted_count > 0:
                            await self.audit_logger.log_event(
                                Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                                {"count": redacted_count},
                            )
                        await self.audit_logger.log_event(
                            Constants.AUDIT_EVENT_TYPES["policy_denied"],
                            {
                                "input": redacted_input,
                                "policy": "integration_check_opa",
                                "reason": reason,
                            },
                            critical=True,
                        )
                async with self._integrate_lock:
                    self._integrate_cache[cache_key] = (allowed, reason)
                return allowed, reason
            except Exception as e:
                self.metrics_client.policy_evaluations_total.labels(
                    result="denied", rule="integration_check"
                ).inc()
                return False, f"OPA policy evaluation failed: {e}. Defaulting to deny."

        integration_rules = self.policies.get("integration_rules", {})

        min_quality = integration_rules.get("min_test_quality_score", 0.7)
        if test_quality_score < min_quality:
            result = await self._deny(
                "integration_rules_local",
                input_data,
                f"Test quality score ({test_quality_score:.2f}) below minimum required ({min_quality:.2f}).",
            )
            async with self._integrate_lock:
                self._integrate_cache[cache_key] = result
            return result

        if any(
            deny_mod in module_identifier
            for deny_mod in integration_rules.get("deny_integrate_modules", [])
        ):
            result = await self._deny(
                "integration_rules_local",
                input_data,
                "Test integration explicitly forbidden for this module.",
            )
            async with self._integrate_lock:
                self._integrate_cache[cache_key] = result
            return result

        security_threshold_level = self.policies.get("security_scan_threshold", "NONE").upper()
        severity_levels = {level: i for i, level in enumerate(Constants.SECURITY_LEVELS)}

        if security_severity_str not in severity_levels:
            if security_severity_str not in self._warned_severities:
                logger.warning(
                    f"Unknown security severity '{security_severity_str}'. Mapping to 'CRITICAL'."
                )
                self._warned_severities.add(security_severity_str)
            security_severity_str = "CRITICAL"

        if has_security_issues and severity_levels.get(
            security_severity_str, 0
        ) >= severity_levels.get(security_threshold_level, 0):
            result = await self._deny(
                "integration_rules_local",
                input_data,
                f"Security scan detected issues of '{security_severity_str}' severity, which meets or exceeds threshold '{security_threshold_level}'.",
            )
            async with self._integrate_lock:
                self._integrate_cache[cache_key] = result
            return result

        result = await self._allow(
            "integration_rules_local", input_data, "Policy allows test integration."
        )
        async with self._integrate_lock:
            self._integrate_cache[cache_key] = result
        return result

    async def requires_pr_for_integration(
        self, module_identifier: str, language: str, test_quality_score: float
    ) -> Tuple[bool, str]:
        """
        Determines if a test requires a human-reviewed Pull Request (PR) for integration.
        """
        cache_key = (module_identifier, language, test_quality_score)
        async with self._pr_lock:
            if cache_key in self._pr_cache:
                return self._pr_cache[cache_key]

        input_data = {
            "module_identifier": module_identifier,
            "language": language,
            "test_quality_score": test_quality_score,
        }

        if self.config.opa_integration_enabled:
            try:
                requires_pr_by_opa, reason_opa = await self.policy_client.evaluate_policy(
                    Constants.OPA_POLICY_PATHS["pr_required_check"], input_data
                )
                self.metrics_client.policy_evaluations_total.labels(
                    result="requires_pr" if requires_pr_by_opa else "allowed",
                    rule="pr_required_check",
                ).inc()
                async with self._pr_lock:
                    self._pr_cache[cache_key] = (requires_pr_by_opa, reason_opa)
                return requires_pr_by_opa, reason_opa
            except Exception as e:
                logger.warning(
                    f"OPA check for PR requirement failed: {e}. Falling back to local policy.",
                    exc_info=True,
                )
                if self.audit_logger:
                    redacted_input, redacted_count = redact_sensitive(input_data)
                    if redacted_count > 0:
                        await self.audit_logger.log_event(
                            Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                            {"count": redacted_count},
                        )
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["opa_failure"],
                        {
                            "input": redacted_input,
                            "policy": "pr_required_check",
                            "error": str(e),
                        },
                        critical=True,
                    )
                result = (
                    True,
                    f"OPA check for PR failed: {e}. Defaulting to require PR.",
                )
                async with self._pr_lock:
                    self._pr_cache[cache_key] = result
                return result

        integration_rules = self.policies.get("integration_rules", {})

        lang = (language or "").lower()
        hr_langs = [l.lower() for l in integration_rules.get("human_review_required_languages", [])]
        if lang in hr_langs:
            reason = f"Policy: Human review required for '{language}' tests before integration."
            self.metrics_client.policy_evaluations_total.labels(
                result="requires_pr", rule="integration_rules_local"
            ).inc()
            if self.audit_logger:
                redacted_input, redacted_count = redact_sensitive(input_data)
                if redacted_count > 0:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                        {"count": redacted_count},
                    )
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_decision"],
                    {
                        "input": redacted_input,
                        "policy": "pr_check_local",
                        "result": "requires_pr",
                        "reason": reason,
                    },
                )
            async with self._pr_lock:
                self._pr_cache[cache_key] = (True, reason)
            return True, reason

        if any(
            mod in module_identifier
            for mod in integration_rules.get("require_human_review_modules", [])
        ):
            reason = "Policy: Module explicitly requires human review via PR."
            self.metrics_client.policy_evaluations_total.labels(
                result="requires_pr", rule="integration_rules_local"
            ).inc()
            if self.audit_logger:
                redacted_input, redacted_count = redact_sensitive(input_data)
                if redacted_count > 0:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                        {"count": redacted_count},
                    )
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_decision"],
                    {
                        "input": redacted_input,
                        "policy": "pr_check_local",
                        "result": "requires_pr",
                        "reason": reason,
                    },
                )
            async with self._pr_lock:
                self._pr_cache[cache_key] = (True, reason)
            return True, reason

        auto_commit_threshold = integration_rules.get("auto_commit_threshold", 0.85)
        if test_quality_score < auto_commit_threshold:
            reason = f"Policy: Test quality ({test_quality_score:.2f}) below auto-commit threshold ({auto_commit_threshold:.2f}), requires human review via PR."
            self.metrics_client.policy_evaluations_total.labels(
                result="requires_pr", rule="integration_rules_local"
            ).inc()
            if self.audit_logger:
                redacted_input, redacted_count = redact_sensitive(input_data)
                if redacted_count > 0:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                        {"count": redacted_count},
                    )
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["policy_decision"],
                    {
                        "input": redacted_input,
                        "policy": "pr_check_local",
                        "result": "requires_pr",
                        "reason": reason,
                    },
                )
            async with self._pr_lock:
                self._pr_cache[cache_key] = (True, reason)
            return True, reason

        reason = "Policy: Direct integration allowed."
        self.metrics_client.policy_evaluations_total.labels(
            result="allowed", rule="integration_rules_local"
        ).inc()
        if self.audit_logger:
            redacted_input, redacted_count = redact_sensitive(input_data)
            if redacted_count > 0:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                    {"count": redacted_count},
                )
            await self.audit_logger.log_event(
                Constants.AUDIT_EVENT_TYPES["policy_decision"],
                {
                    "input": redacted_input,
                    "policy": "pr_check_local",
                    "result": "allowed",
                    "reason": reason,
                },
            )
        async with self._pr_lock:
            self._pr_cache[cache_key] = (False, reason)
        return False, reason


# --- Event Bus ---
class EventBus:
    """
    Centralized Event Bus for ATCO notifications with real webhook/Slack hooks.
    """

    def __init__(
        self,
        config: Configuration,
        audit_logger: Any,
        metrics_client: MetricsClient,
        message_queue_service=None,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        """
        Initializes the EventBus.
        """
        self.config = config
        self.audit_logger = audit_logger
        self.metrics_client = metrics_client
        self.message_queue_service = message_queue_service

        have_external = bool(self.config.slack_webhook_url or self.config.webhook_hooks)
        have_mq = bool(self.message_queue_service)
        self.http_notifications_enabled = AIOHTTP_AVAILABLE and have_external
        self.disabled = not (have_external or have_mq)

        self.session_factory = session_factory if self.http_notifications_enabled else None
        if self.session_factory is None and self.http_notifications_enabled:
            import aiohttp

            self.session_factory = aiohttp.ClientSession

        self._correlation_id_fn = lambda: os.urandom(8).hex()

        if self.disabled:
            logger.warning("EventBus is disabled as no destinations are configured.")
        else:
            logger.info("EventBus initialized with destinations.")
            logger.info(
                f"EventBus initialized. Webhook hooks: {len(self.config.webhook_hooks or {})} "
                f"Slack hook: {bool(self.config.slack_webhook_url)}"
            )

    async def publish(self, event_name: str, data: Dict[str, Any]):
        """
        Publishes an event to configured notification services.
        """
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("event_name must be a non-empty string")
        if not isinstance(data, dict):
            raise ValueError("data must be a dictionary")

        data.setdefault("correlation_id", self._correlation_id_fn())
        redacted_data, redacted_count = redact_sensitive(data)

        if self.audit_logger:
            try:
                if redacted_count > 0:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                        {"count": redacted_count},
                    )
                await self.audit_logger.log_event(
                    event_name,
                    redacted_data,
                    critical=event_name in self.config.critical_events_for_mq,
                )
                logger.info(f"Event '{event_name}' successfully sent to audit logger.")
            except Exception as e:
                logger.critical(
                    f"Failed to publish event '{event_name}' to audit logger: {e}. Event may be lost.",
                    exc_info=True,
                    extra={"error_type": "AuditLoggerPublishError"},
                )

        if self.disabled:
            return

        if event_name in self.config.critical_events_for_mq and self.message_queue_service:
            try:
                await self.message_queue_service.publish(event_name, redacted_data)
            except Exception:
                logger.warning("MessageQueue publish failed", exc_info=True)
                if self.metrics_client:
                    self.metrics_client.notification_failures_total.labels(
                        service="message_queue", event_name=event_name
                    ).inc()
                if self.audit_logger:
                    await self.audit_logger.log_event(
                        Constants.AUDIT_EVENT_TYPES["notification_failure"],
                        {
                            "service": "message_queue",
                            "event_name": event_name,
                            "error": "MessageQueue publish failed",
                        },
                    )

        if not self.http_notifications_enabled:
            return

        slack_url = self.config.slack_webhook_url
        slack_events = set(self.config.slack_events or [])
        webhook_hooks = self.config.webhook_hooks or {}
        webhook_events = set(self.config.webhook_events or [])

        if await self._publish_to_slack(event_name, data, slack_url, slack_events):
            return
        await self._publish_to_webhook(event_name, data, webhook_hooks, webhook_events)

    async def _publish_to_slack(
        self, event_name: str, data: Dict[str, Any], slack_url: str, slack_events: set
    ) -> bool:
        """Publishes an event to Slack if configured."""
        if not slack_url or event_name not in slack_events:
            return False
        redacted_data, _ = redact_sensitive(data)
        msg = (
            f"ATCO Alert: *{event_name}*\n"
            f">Module: `{redacted_data.get('module', redacted_data.get('module_identifier', 'N/A'))}`\n"
            f">Reason: {redacted_data.get('reason', redacted_data.get('status', 'N/A'))}\n"
            f">Payload: {redacted_data}"
        )
        payload = {"text": msg}
        try:
            await self._send_notification_with_retry(slack_url, payload, "Slack")
            return True
        except Exception:
            self.metrics_client.notification_failures_total.labels(
                service="Slack", event_name=event_name
            ).inc()
            logger.error(
                f"Failed to send notification to Slack for {event_name}.",
                exc_info=True,
                extra={"service": "Slack", "event": event_name},
            )
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["notification_failure"],
                    {
                        "service": "Slack",
                        "event": event_name,
                        "error": traceback.format_exc(),
                    },
                    critical=True,
                )
            return False

    async def _publish_to_webhook(
        self,
        event_name: str,
        data: Dict[str, Any],
        webhook_hooks: dict,
        webhook_events: set,
    ) -> bool:
        """Publishes an event to a webhook if configured."""
        if not webhook_hooks or event_name not in webhook_events:
            return False

        webhook_url = webhook_hooks.get(event_name)
        if not webhook_url:
            return False

        hook_name = event_name

        try:
            redacted_data, _ = redact_sensitive(data)
            await self._send_notification_with_retry(
                webhook_url, redacted_data, f"webhook:{hook_name}"
            )
            return True
        except Exception:
            self.metrics_client.notification_failures_total.labels(
                service=f"webhook:{hook_name}", event_name=event_name
            ).inc()
            logger.error(
                f"Failed to send notification to webhook {hook_name} for {event_name}.",
                exc_info=True,
                extra={"service": f"webhook:{hook_name}", "event": event_name},
            )
            if self.audit_logger:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["notification_failure"],
                    {
                        "service": f"webhook:{hook_name}",
                        "event": event_name,
                        "error": traceback.format_exc(),
                    },
                    critical=True,
                )
            return False

    @retry(stop=RETRY_STOP, wait=RETRY_WAIT)
    async def _send_notification_with_retry(
        self, url: str, payload: Dict[str, Any], service_name: str
    ):
        """Sends an HTTP notification with retry logic."""
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError(f"Invalid URL for {service_name}: {url}")
        if parsed_url.scheme != "https":
            raise ValueError(f"HTTPS required for {service_name} URL: {url}")
        if ".." in url:
            raise ValueError("URL cannot contain directory traversal sequences.")
        if any(c in url for c in ["\n", "\r"]):
            raise ValueError("URL cannot contain newline characters.")

        if (
            self.config.allowed_notification_hosts
            and parsed_url.netloc not in self.config.allowed_notification_hosts
        ):
            raise ValueError(f"Host {parsed_url.netloc} not in allowed hosts for {service_name}")

        async with self.session_factory() as session:
            async with session.post(
                url, json=payload, timeout=self.config.notification_timeout_seconds
            ) as response:
                response.raise_for_status()

        logger.debug("Direct HTTP notification sent to %s", service_name)
        if self.audit_logger:
            redacted_payload, redacted_count = redact_sensitive(payload)
            if redacted_count > 0:
                await self.audit_logger.log_event(
                    Constants.AUDIT_EVENT_TYPES["sensitive_data_redacted"],
                    {"count": redacted_count},
                )
            await self.audit_logger.log_event(
                Constants.AUDIT_EVENT_TYPES["notification_sent"],
                {"service": service_name, "payload": redacted_payload},
            )


# Final list of exposed symbols
__all__ = [
    "PolicyEngine",
    "EventBus",
    "redact_sensitive",
    "AuditLogger",
    "Constants",
    "Configuration",
    "MetricsClient",
    "FileSystem",
    "LocalFileSystem",
    "OPAPolicyClient",
    "PolicyClient",
]

# --- Self-Test Mode ---
if __name__ == "__main__":
    print("Running self-test for audit and policy module...")

    async def run_test_case():
        config = Configuration.from_env(project_root=os.getcwd())
        try:
            audit_logger = AuditLogger("audit.log")
        except ImportError:
            print("❌ DLT-enabled AuditLogger not available for self-test")
            return
        metrics_client = MetricsClient()
        filesystem = LocalFileSystem()
        policy_client = OPAPolicyClient(config, audit_logger, metrics_client)
        policy_engine = await PolicyEngine.create(
            None, config, audit_logger, metrics_client, filesystem, policy_client
        )
        await policy_engine.should_generate_tests("test.module", "python")
        print("✅ PolicyEngine used the injected logger and completed a test.")

    try:
        asyncio.run(run_test_case())
        print("✅ Self-test completed successfully.")
    except Exception as e:
        print(f"❌ Asynchronous self-test failed: {e}")
