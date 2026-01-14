# agents/codegen_agent.py
import asyncio
import json
import logging
import logging.handlers
import os
import re
import shutil
import sqlite3
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Third-party libraries (MINIMAL SET RETAINED)
import aiohttp
import redis.asyncio as aioredis
import yaml
from fastapi import FastAPI, HTTPException
from jinja2 import TemplateNotFound

# Observability libraries
from opentelemetry import trace
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

try:
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
except ImportError:
    JaegerExporter = None

# Internal imports
from .codegen_prompt import build_code_generation_prompt
from .codegen_response_handler import add_traceability_comments, parse_llm_response

# --- REMOVED OBSOLETE IMPORT: from .codegen_llm_call import CacheManager ---

# --- RUNNER UTILITY IMPORTS (ENFORCED) ---
try:
    # --- FIX: Changed imports to be ABSOLUTE from the 'generator' root ---
    # CircuitBreaker is in llm_client, but if you need the class itself:
    from generator.runner.llm_client import (
        CircuitBreaker,
        call_ensemble_api,
        call_llm_api,
    )
    from generator.runner.runner_logging import log_audit_event
    from generator.runner.runner_metrics import (
        LLM_CIRCUIT_STATE,
        LLM_RATE_LIMIT_EXCEEDED,
    )
    from generator.runner.runner_security_utils import scan_for_vulnerabilities
except ImportError as e:
    # Hard fail: this agent is not allowed to run without the runner stack.
    raise ImportError(
        "codegen_agent requires the generator.runner package "
        "(llm_client, runner_logging, runner_security_utils, runner_metrics)."
    ) from e

# Internal component dummy/migration note
try:
    from omnicore_engine.plugin_registry import PlugInKind, plugin

    PLUGIN_AVAILABLE = True
except ImportError:
    PLUGIN_AVAILABLE = False

    # Create dummy decorator if plugin system not available
    def plugin(**kwargs):
        def decorator(func):
            return func

        return decorator

    class PlugInKind:
        FIX = "FIX"


# ==============================================================================
# --- Production-Grade Logging and Auditing (PLACEHOLDERS) ---
# --- REDUNDANT CLASS REMOVAL: SecretsManager removed ---
# --- All internal AuditLogger definitions replaced with centralized call ---
# ==============================================================================
class AuditLogger(ABC):
    """Placeholder for type compatibility."""

    @abstractmethod
    def log_action(self, action: str, details: Dict[str, Any]):
        pass


class JsonConsoleAuditLogger(AuditLogger):
    """
    JSON Console Audit Logger - outputs structured JSON audit logs to console/stdout.
    Delegates to the centralized log_audit_event for consistent audit logging.
    """

    def log_action(self, action: str, details: Dict[str, Any]):
        """Log an audit action as JSON to console via centralized audit system."""
        # Add metadata to indicate this is from JsonConsoleAuditLogger
        enriched_details = {
            **details,
            "audit_logger": "JsonConsoleAuditLogger",
            "output_target": "console",
        }
        log_audit_event(action, enriched_details)
        # Also output directly to console as JSON for immediate visibility
        import json
        import sys
        audit_record = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": enriched_details,
        }
        print(json.dumps(audit_record), file=sys.stdout, flush=True)


class FileAuditLogger(AuditLogger):
    """
    File Audit Logger - writes structured audit logs to a configured file.
    Delegates to the centralized log_audit_event and appends to file.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.log_file = config.get("audit_log_file", "audit.log")
        self.max_bytes = config.get("audit_log_max_bytes", 10 * 1024 * 1024)  # 10MB default
        self.backup_count = config.get("audit_log_backup_count", 5)
        
        # Create rotating file handler for audit logs
        from logging.handlers import RotatingFileHandler
        import os
        
        # Ensure directory exists
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        self.file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        self.file_handler.setFormatter(logging.Formatter('%(message)s'))

    def log_action(self, action: str, details: Dict[str, Any]):
        """Log an audit action to file via centralized audit system and direct file write."""
        # Add metadata to indicate this is from FileAuditLogger
        enriched_details = {
            **details,
            "audit_logger": "FileAuditLogger",
            "output_target": self.log_file,
        }
        log_audit_event(action, enriched_details)
        
        # Also write directly to the audit log file
        import json
        audit_record = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": enriched_details,
        }
        log_record = logging.LogRecord(
            name="audit",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=json.dumps(audit_record),
            args=(),
            exc_info=None
        )
        self.file_handler.emit(log_record)


# Standard application logging
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ==============================================================================
# --- Integrated Utilities & Security ---
# ==============================================================================
class SecurityUtils:
    """Utilities for enhancing security during code generation."""

    @staticmethod
    def mask_secrets(text: str) -> str:
        """
        Masks common secret patterns in text for safe logging.
        """
        # Pattern for key=value, key: value, "key": "value" formats
        masked_text = re.sub(
            r"""
            (['"]?
            (api_key|password|secret|token|auth_token|access_key)
            ['"]?\s*[:=]\s*['"]?
            )
            ([a-zA-Z0-9\-_.~+]{16,})
            (['"]?)
            """,
            r"\1REDACTED\5",
            text,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        # Pattern for Bearer tokens
        masked_text = re.sub(
            r"(Authorization\s*:\s*Bearer\s+)[a-zA-Z0-9\-_.~+/=]+",
            r"\1REDACTED",
            masked_text,
            flags=re.IGNORECASE,
        )
        return masked_text

    @staticmethod
    def apply_compliance(code: str, rules: Dict[str, Any]) -> List[str]:
        """Applies compliance checks based on configured rules."""
        violations = []
        for func in rules.get("banned_functions", []):
            if re.search(r"\b" + re.escape(func) + r"\b", code):
                violations.append(
                    f"Compliance violation: Use of banned function '{func}'."
                )
        for banned_import in rules.get("banned_imports", []):
            if re.search(
                r"\bimport\s+" + re.escape(banned_import) + r"\b", code
            ) or re.search(r"\bfrom\s+" + re.escape(banned_import) + r"\b", code):
                violations.append(
                    f"Compliance violation: Use of banned import '{banned_import}'."
                )
        required_header = rules.get("required_header")
        if required_header and not code.startswith(required_header):
            violations.append("Compliance violation: Missing required license header.")
        max_length = rules.get("max_line_length")
        if max_length:
            for i, line in enumerate(code.splitlines()):
                if len(line) > max_length:
                    violations.append(
                        f"Compliance violation: Line {i+1} exceeds max length of {max_length} characters."
                    )
        return violations


security_utils = SecurityUtils()

_tool_cache: Dict[str, bool] = {}


def _is_tool_available(tool: str) -> bool:
    """Checks if a command-line tool is available in the system's PATH and caches the result."""
    if tool not in _tool_cache:
        _tool_cache[tool] = shutil.which(tool) is not None
        if not _tool_cache[tool]:
            logger.warning(
                f"Tool '{tool}' not found in PATH. Dependent checks will be skipped."
            )
    return _tool_cache[tool]


# ==============================================================================
# --- Pluggable Feedback Store ---
# ==============================================================================
class FeedbackStore(ABC):
    """Abstract base class for storing and retrieving HITL feedback."""

    @abstractmethod
    async def setup(self):
        pass

    @abstractmethod
    async def get_feedback(self, req_hash: str) -> Optional[str]:
        pass

    @abstractmethod
    async def save_feedback(self, req_hash: str, feedback: str):
        pass


class SQLiteFeedbackStore(FeedbackStore):
    """An implementation of the feedback store using a local SQLite database."""

    def __init__(self, config: Dict[str, Any]):
        self.db_file = config.get("path", "feedback.db")

    async def setup(self):
        try:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hitl_feedback (req_hash TEXT PRIMARY KEY, feedback TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.close()
            # Cleanup job
            if os.getenv("CODEGEN_DISABLE_CLEANUP_TASKS", "").lower() not in {
                "1",
                "true",
                "yes",
            }:
                asyncio.create_task(self._cleanup_old_feedback())
        except sqlite3.Error as e:
            logger.error(f"SQLite setup failed: {e}")
            raise

    async def _cleanup_old_feedback(self):
        while True:
            await asyncio.sleep(24 * 60 * 60)  # Run daily
            try:
                conn = sqlite3.connect(self.db_file, check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM hitl_feedback WHERE timestamp <= date('now', '-30 days')"
                )
                conn.commit()
                conn.close()
                logger.info("Cleaned up old SQLite feedback entries.")
            except sqlite3.Error as e:
                logger.error(f"SQLite cleanup failed: {e}")

    async def get_feedback(self, req_hash: str) -> Optional[str]:
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            result = conn.execute(
                "SELECT feedback FROM hitl_feedback WHERE req_hash = ? ORDER BY timestamp DESC LIMIT 1",
                (req_hash,),
            ).fetchone()
            return result[0] if result else None

    async def save_feedback(self, req_hash: str, feedback: str):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hitl_feedback (req_hash, feedback) VALUES (?, ?)",
                (req_hash, feedback),
            )
            conn.commit()


class RedisFeedbackStore(FeedbackStore):
    """An implementation of the feedback store using Redis."""

    def __init__(self, config: Dict[str, Any]):
        self.redis_url = os.getenv("REDIS_URL", config.get("url", "redis://localhost"))
        self.ttl = config.get("ttl", 604800)  # 7 days in seconds
        self._redis = None

    async def setup(self):
        try:
            self._redis = await aioredis.from_url(self.redis_url)
            await self._redis.ping()
            logger.info("Redis feedback store connected successfully.")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    async def get_feedback(self, req_hash: str) -> Optional[str]:
        if not self._redis:
            raise RuntimeError("Redis client not initialized.")
        feedback = await self._redis.get(f"feedback:{req_hash}")
        return feedback.decode("utf-8") if feedback else None

    async def save_feedback(self, req_hash: str, feedback: str):
        if not self._redis:
            raise RuntimeError("Redis client not initialized.")
        await self._redis.set(f"feedback:{req_hash}", feedback, ex=self.ttl)
        logger.info(f"Saved feedback to Redis for hash {req_hash[:8]}...")


# ==============================================================================
# --- Agent Configuration & Setup ---
# ==============================================================================
# OpenTelemetry Setup
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None


# ==============================================================================
# --- Prometheus Metrics ---
# ==============================================================================
def get_or_create_counter(name: str, description: str, labelnames: List[str] = None):
    """Get existing counter or create new one to avoid duplicates."""
    labelnames = labelnames or []
    try:
        # Try to get existing metric from registry
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    # Create new counter if it doesn't exist
    return Counter(name, description, labelnames)


def get_or_create_histogram(name: str, description: str, labelnames: List[str] = None):
    """Get existing histogram or create new one to avoid duplicates."""
    labelnames = labelnames or []
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    return Histogram(name, description, labelnames)


def get_or_create_gauge(name: str, description: str, labelnames: List[str] = None):
    """Get existing gauge or create new one to avoid duplicates."""
    labelnames = labelnames or []
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    return Gauge(name, description, labelnames)


# Prometheus Metrics - Using safe creation functions
CODEGEN_REQUESTS = get_or_create_counter(
    "codegen_requests_total",
    "Total code generation requests",
    ["backend"],
)
# Backwards compatibility: some callers expect CODEGEN_COUNTER
CODEGEN_COUNTER = CODEGEN_REQUESTS

CODEGEN_LATENCY = get_or_create_histogram(
    "codegen_latency_seconds",
    "Latency of code generation requests",
    ["backend"],
)

CODEGEN_ERRORS = get_or_create_counter(
    "codegen_errors_total",
    "Total errors during code generation",
    ["error_type"],
)

HITL_APPROVAL_RATE = get_or_create_gauge(
    "hitl_approval_rate",
    "Ratio of approved to rejected HITL reviews",
)

HITL_TIMEOUT_RATE = get_or_create_counter(
    "hitl_timeout_total",
    "Total number of HITL review timeouts",
)

SECURITY_FINDINGS = get_or_create_counter(
    "security_findings_total",
    "Total security findings detected in generated code",
    ["scanner"],
)
# Backwards compatibility for older imports / tests
CODEGEN_SECURITY_FINDINGS = SECURITY_FINDINGS


ENSEMBLE_VOTES = get_or_create_counter(
    "ensemble_votes_total",
    "Total votes cast by ensemble models",
    ["model"],
)

CODEGEN_CACHE_HITS = get_or_create_counter(
    "codegen_cache_hits_total",
    "Total cache hits for code generation requests",
    ["backend"],
)

# NOTE: LLM_RATE_LIMIT_EXCEEDED and LLM_CIRCUIT_STATE are imported from runner.llm_client


# Custom Exception
class EnsembleGenerationError(RuntimeError):
    def __init__(self, message, underlying_exceptions):
        super().__init__(message)
        self.underlying_exceptions = underlying_exceptions


# circuit_breaker global is now the imported one.
circuit_breaker = CircuitBreaker()


class CodeGenConfig:
    def __init__(self, config: Dict[str, Any]):
        ### --- DEPLOYMENT NOTE ---
        # The internal model/key config has been removed, as the runner client handles this.
        self.backend = os.getenv(
            "CODEGEN_BACKEND", config.get("backend", "openai")
        ).lower()
        self.api_keys = config.get(
            "api_keys", {}
        )  # Retained for env key presence checks
        self.model = config.get("model", {})  # Retained for custom model mapping

        # VALIDATION: Ensure the environment key for the configured backend is present.
        # FIX: Skip API key validation in test mode
        testing_mode = (
            os.getenv("TESTING") == "1"
            or "pytest" in sys.modules
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )
        for b in ["grok", "openai", "gemini"]:
            self.api_keys[b] = os.getenv(f"{b.upper()}_API_KEY", self.api_keys.get(b))
            self.model[b] = os.getenv(f"{b.upper()}_MODEL", self.model.get(b))
            if self.backend == b and not self.api_keys.get(b) and not testing_mode:
                raise ValueError(f"API key for backend '{b}' is missing.")

        default_template_path = Path(__file__).parent / "templates"
        self.template_dir = config.get("template_dir", str(default_template_path))

        self.max_retries = int(config.get("max_retries", 2))
        self.enable_security_scan = bool(config.get("enable_security_scan", True))
        self.allow_interactive_hitl = bool(config.get("allow_interactive_hitl", False))
        self.ensemble_enabled = bool(
            config.get("ensemble_enabled", False)
        )  # ADDED ENSEMBLE FLAG
        self.compliance_rules = config.get(
            "compliance",
            {"banned_imports": [], "banned_functions": [], "max_line_length": 120},
        )
        self.feedback_store_config = config.get("feedback_store", {"type": "sqlite"})
        self.audit_logger_config = config.get("audit_logger", {"type": "console"})
        self.llm_backends = {}  # Kept as empty dict for code compatibility if needed.

    @classmethod
    def from_file(cls, filepath: str):
        with open(filepath, "r") as f:
            config_data = yaml.safe_load(f)
        return cls(config_data)


# ==============================================================================
# --- CORE COMPONENTS REMOVED/REPLACED ---
# NOTE: All internal LLMBackend, CircuitBreaker, and async_call_llm_api logic is GONE.
# ==============================================================================

# --- Obsolete Retry Wrapper REMOVED ---
# _call_llm_with_retry function has been deleted as requested.


async def perform_security_scans(code_files: dict) -> dict:
    """
    Run security scans on the generated code files.

    - Delegates to runner.runner_security_utils.scan_for_vulnerabilities
      (which may be sync or async; both are supported).
    - Increments SECURITY_FINDINGS when issues are detected.
    - Returns the original code_files unchanged (backwards-compatible).
    """
    try:
        result = scan_for_vulnerabilities(code_files)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        logger.warning(f"Security scan failed, continuing without blocking: {exc}")
        return code_files

    if not result:
        return code_files

    # Expected formats:
    # - {"issues": [ {...}, {...} ]}
    # - [ {...}, {...} ]
    issues = None
    if isinstance(result, dict) and "issues" in result:
        issues = result["issues"]
    elif isinstance(result, list):
        issues = result
    else:
        issues = None

    if issues:
        try:
            # Increment once per scan that finds at least one issue.
            SECURITY_FINDINGS.labels(scanner="default").inc()
        except Exception:
            # Under stubbed metrics, labels()/inc() may be a no-op; ignore failures.
            pass

    return code_files


async def hitl_review(
    code_files: Dict[str, str],
    feedback_store: FeedbackStore,
    req_hash: str,
    allow_interactive: bool,
    redis_client: aioredis.Redis,
    audit_logger: AuditLogger,
) -> Tuple[str, Optional[str]]:
    """API-based Human-in-the-Loop review."""
    if not allow_interactive:
        logger.warning("HITL running in non-interactive mode. Defaulting to rejection.")
        HITL_APPROVAL_RATE.set(0)
        return ("rejected", "Non-interactive HITL.")

    review_system_webhook = os.getenv("REVIEW_SYSTEM_WEBHOOK")
    if not review_system_webhook:
        logger.error("REVIEW_SYSTEM_WEBHOOK is not set. Defaulting to rejection.")
        return ("rejected", "Review system webhook not configured.")

    # Push review request to the external system via webhook
    review_request = {
        "req_hash": req_hash,
        "code_files": code_files,
        "review_url": os.getenv(
            "REVIEW_SYSTEM_URL", "https://review-system.example.com"
        )
        + f"/{req_hash}",
    }

    webhook_sent = False
    for i in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    review_system_webhook, json=review_request, timeout=10
                ) as resp:
                    resp.raise_for_status()
                    webhook_sent = True
                    # --- Audit/Logging Change: Use log_audit_event ---
                    log_audit_event(
                        "HITLWebhookSent", {"req_hash": req_hash, "attempt": i + 1}
                    )
                    # --- End Audit/Logging Change ---
                    break
        except Exception as e:
            # --- Audit/Logging Change: Use log_audit_event ---
            log_audit_event(
                "HITLWebhookFailed",
                {"req_hash": req_hash, "attempt": i + 1, "error": str(e)},
            )
            # --- End Audit/Logging Change ---
            logger.warning(f"Webhook to review system failed (attempt {i+1}): {e}")
            await asyncio.sleep(5)

    if not webhook_sent:
        logger.error(
            "Failed to send webhook to review system after 3 attempts. Defaulting to rejection."
        )
        return ("rejected", "Review system unreachable, defaulting to rejection.")

    # Wait for review submission via Pub/Sub
    # --- Audit/Logging Change: Use log_audit_event ---
    log_audit_event("HITLPubSubSubscribed", {"req_hash": req_hash})
    # --- End Audit/Logging Change ---
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"hitl:review_status:{req_hash}")

    try:
        message = await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True), timeout=60
        )
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        if message:
            review_status = json.loads(message["data"])
            status = review_status["status"]
            feedback = review_status.get("feedback")
            if status == "approved":
                HITL_APPROVAL_RATE.set(1)
            else:
                HITL_APPROVAL_RATE.set(0)
            return status, feedback
        else:
            return ("rejected", "HITL review timed out.")
    except asyncio.TimeoutError:
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        return ("rejected", "HITL review timed out.")
    except Exception as e:
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        logger.error(f"Error during HITL Pub/Sub wait: {e}")
        return ("rejected", f"Internal error during HITL review: {e}")


if PLUGIN_AVAILABLE:

    @plugin(
        kind=PlugInKind.FIX,
        name="codegen_agent",
        version="1.0.0",
        params_schema={
            "requirements": {
                "type": "dict",
                "description": "The requirements for the code to be generated.",
            },
            "state_summary": {
                "type": "string",
                "description": "A summary of the current system state.",
            },
            "config_path_or_dict": {
                "type": ["string", "dict"],
                "description": "Path to a YAML config file or a config dictionary.",
            },
        },
        description="Generates code based on requirements, incorporating security scans and human-in-the-loop review.",
        safe=True,
    )
    async def generate_code(
        requirements: Dict[str, Any],
        state_summary: str,
        config_path_or_dict: Union[str, Dict[str, Any]],
    ) -> Dict[str, str]:
        """Main async function for code generation with fully pluggable and implemented components."""
        config = (
            CodeGenConfig.from_file(config_path_or_dict)
            if isinstance(config_path_or_dict, str)
            else CodeGenConfig(config_path_or_dict)
        )

        request_id = str(uuid.uuid4())
        logger.info(f"Starting new code generation request. Request ID: {request_id}")

        # Initialize components based on config
        redis_client = None
        try:
            redis_client = await aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost")
            )
            await redis_client.ping()
        except Exception:
            logger.warning(
                "Redis not available. Distributed components will operate in-memory or be disabled."
            )

        feedback_store = None
        try:
            if config.feedback_store_config["type"] == "redis" and redis_client:
                feedback_store = RedisFeedbackStore(config.feedback_store_config)
                feedback_store._redis = redis_client
                await feedback_store.setup()
            else:
                feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
                await feedback_store.setup()
        except Exception:
            logger.warning("Configured feedback store failed. Falling back to SQLite.")
            feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
            await feedback_store.setup()

        # --- REMOVED OBSOLETE CACHE MANAGER INITIALIZATION ---
        # CacheManager initialization is removed as it's not needed by the new call_llm_api signature.
        # cache_manager = CacheManager(redis_client)

        req_hash = str(hash(json.dumps(requirements, sort_keys=True)))

        with tracer.start_as_current_span(
            "generate_code_request",
            attributes={"request.id": request_id, "backend": config.backend},
        ):
            try:
                with tracer.start_as_current_span("prepare_prompt"):
                    previous_feedback = await feedback_store.get_feedback(req_hash)
                    try:
                        prompt = await build_code_generation_prompt(
                            requirements=requirements,
                            state_summary=state_summary,
                            previous_feedback=previous_feedback,
                            target_language=requirements.get(
                                "target_language", "python"
                            ),
                            target_framework=None,
                            enable_meta_llm_critique=False,
                            multi_modal_inputs=None,
                            audit_logger=JsonConsoleAuditLogger(),  # Kept for prompt builder compatibility
                            redis_client=redis_client,
                        )
                    except TemplateNotFound as e:
                        logger.warning(
                            f"Template not found ({e}). Using minimal fallback prompt."
                        )
                        prompt = (
                            "Generate code strictly as JSON with a 'files' object mapping filenames to code strings. "
                            + f"Requirements: {json.dumps(requirements, sort_keys=True)}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Prompt build failed ({e}). Using minimal fallback prompt."
                        )
                        prompt = (
                            "Generate code strictly as JSON with a 'files' object mapping filenames to code strings. "
                            + f"Requirements: {json.dumps(requirements, sort_keys=True)}"
                        )

                # Generate Code
                with tracer.start_as_current_span("call_llm"):
                    # --- LLM Execution Change: Implement Ensemble/Single Call Logic ---
                    if config.ensemble_enabled:
                        # Ensemble call logic (as per sample update, assuming it exists)
                        models = [
                            {
                                "provider": "openai",
                                "model": config.model.get("openai", "gpt-4o"),
                            },
                            {
                                "provider": "gemini",
                                "model": config.model.get("gemini", "gemini-2.5-pro"),
                            },
                            {
                                "provider": "grok",
                                "model": config.model.get("grok", "grok-4"),
                            },
                        ]
                        response_dict = await call_ensemble_api(
                            prompt=prompt,
                            models=models,
                            voting_strategy="majority",
                            # Removed cache_manager argument
                        )
                        response = (
                            response_dict["content"]
                            if isinstance(response_dict, dict)
                            and "content" in response_dict
                            else str(response_dict)
                        )
                        backend_used = "ensemble"
                    else:
                        # Single call logic (using configured backend)
                        backend_used = config.backend
                        response = await call_llm_api(
                            prompt=prompt,
                            provider=config.backend,
                            model=config.model.get(config.backend),
                            # Removed cache_manager argument
                        )
                    # --- End LLM Execution Change ---

                with tracer.start_as_current_span("parse_response_and_scan"):
                    code_files = parse_llm_response(response)
                    code_files = add_traceability_comments(
                        code_files,
                        requirements,
                        requirements.get("target_language", "python"),
                    )

                    # Post-Processing and Scans
                    for code in code_files.values():
                        violations = security_utils.apply_compliance(
                            code, config.compliance_rules
                        )
                        if violations:
                            # --- Audit/Logging Change: Use log_audit_event ---
                            log_audit_event(
                                "Compliance Violation", {"violations": violations}
                            )
                            # --- End Audit/Logging Change ---

                    # --- Security Scans Change: Use unified scanning utility ---
                    code_files = await perform_security_scans(code_files)
                    # --- End Security Scans Change ---

                # HITL (only when enabled)
                if getattr(config, "allow_interactive_hitl", False):
                    with tracer.start_as_current_span("hitl_review"):
                        # We pass a dummy JsonConsoleAuditLogger to hitl_review for signature compatibility
                        status, feedback = await hitl_review(
                            code_files,
                            feedback_store,
                            req_hash,
                            True,
                            redis_client,
                            JsonConsoleAuditLogger(),
                        )
                    if status != "approved":
                        # --- Audit/Logging Change: Use log_audit_event ---
                        log_audit_event("HITL Rejection", {"feedback": feedback})
                        # --- End Audit/Logging Change ---
                        return {
                            "error.txt": f"Code rejected by human review. Feedback: {feedback}"
                        }
                else:
                    # Skip HITL entirely; treat as approved
                    status, feedback = ("approved", None)

                # --- Audit/Logging Change: Use log_audit_event ---
                log_audit_event(
                    "Code Generation Completed",
                    {"files": list(code_files.keys()), "model": backend_used},
                )
                # --- End Audit/Logging Change ---
                return code_files

            except Exception as e:
                logger.exception(f"Code generation failed: {e}")
                # --- Audit/Logging Change: Use log_audit_event ---
                log_audit_event(
                    "Code Generation Failed", {"error": str(e), "traceback": repr(e)}
                )
                # --- End Audit/Logging Change ---
                CODEGEN_ERRORS.labels(type(e).__name__).inc()
                return {
                    "error.txt": f"Error: Code generation failed. Details: {str(e)}"
                }

else:

    async def generate_code(
        requirements: Dict[str, Any],
        state_summary: str,
        config_path_or_dict: Union[str, Dict[str, Any]],
    ) -> Dict[str, str]:
        """Main async function for code generation with fully pluggable and implemented components."""
        config = (
            CodeGenConfig.from_file(config_path_or_dict)
            if isinstance(config_path_or_dict, str)
            else CodeGenConfig(config_path_or_dict)
        )

        request_id = str(uuid.uuid4())
        logger.info(f"Starting new code generation request. Request ID: {request_id}")

        # Initialize components based on config
        redis_client = None
        try:
            redis_client = await aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost")
            )
            await redis_client.ping()
        except Exception:
            logger.warning(
                "Redis not available. Distributed components will operate in-memory or be disabled."
            )

        feedback_store = None
        try:
            if config.feedback_store_config["type"] == "redis" and redis_client:
                feedback_store = RedisFeedbackStore(config.feedback_store_config)
                feedback_store._redis = redis_client
                await feedback_store.setup()
            else:
                feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
                await feedback_store.setup()
        except Exception:
            logger.warning("Configured feedback store failed. Falling back to SQLite.")
            feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
            await feedback_store.setup()

        # --- REMOVED OBSOLETE CACHE MANAGER INITIALIZATION ---
        # CacheManager initialization is removed as it's not needed by the new call_llm_api signature.
        # cache_manager = CacheManager(redis_client)

        req_hash = str(hash(json.dumps(requirements, sort_keys=True)))

        with tracer.start_as_current_span(
            "generate_code_request",
            attributes={"request.id": request_id, "backend": config.backend},
        ):
            try:
                with tracer.start_as_current_span("prepare_prompt"):
                    previous_feedback = await feedback_store.get_feedback(req_hash)
                    try:
                        prompt = await build_code_generation_prompt(
                            requirements=requirements,
                            state_summary=state_summary,
                            previous_feedback=previous_feedback,
                            target_language=requirements.get(
                                "target_language", "python"
                            ),
                            target_framework=None,
                            enable_meta_llm_critique=False,
                            multi_modal_inputs=None,
                            audit_logger=JsonConsoleAuditLogger(),  # Kept for prompt builder compatibility
                            redis_client=redis_client,
                        )
                    except TemplateNotFound as e:
                        logger.warning(
                            f"Template not found ({e}). Using minimal fallback prompt."
                        )
                        prompt = (
                            "Generate code strictly as JSON with a 'files' object mapping filenames to code strings. "
                            + f"Requirements: {json.dumps(requirements, sort_keys=True)}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Prompt build failed ({e}). Using minimal fallback prompt."
                        )
                        prompt = (
                            "Generate code strictly as JSON with a 'files' object mapping filenames to code strings. "
                            + f"Requirements: {json.dumps(requirements, sort_keys=True)}"
                        )

                # Generate Code
                with tracer.start_as_current_span("call_llm"):
                    # --- LLM Execution Change: Implement Ensemble/Single Call Logic ---
                    if config.ensemble_enabled:
                        # Ensemble call logic (as per sample update, assuming it exists)
                        models = [
                            {
                                "provider": "openai",
                                "model": config.model.get("openai", "gpt-4o"),
                            },
                            {
                                "provider": "gemini",
                                "model": config.model.get("gemini", "gemini-2.5-pro"),
                            },
                            {
                                "provider": "grok",
                                "model": config.model.get("grok", "grok-4"),
                            },
                        ]
                        response_dict = await call_ensemble_api(
                            prompt=prompt,
                            models=models,
                            voting_strategy="majority",
                            # Removed cache_manager argument
                        )
                        response = (
                            response_dict["content"]
                            if isinstance(response_dict, dict)
                            and "content" in response_dict
                            else str(response_dict)
                        )
                        backend_used = "ensemble"
                    else:
                        # Single call logic (using configured backend)
                        backend_used = config.backend
                        response = await call_llm_api(
                            prompt=prompt,
                            provider=config.backend,
                            model=config.model.get(config.backend),
                            # Removed cache_manager argument
                        )
                    # --- End LLM Execution Change ---

                with tracer.start_as_current_span("parse_response_and_scan"):
                    code_files = parse_llm_response(response)
                    code_files = add_traceability_comments(
                        code_files,
                        requirements,
                        requirements.get("target_language", "python"),
                    )

                    # Post-Processing and Scans
                    for code in code_files.values():
                        violations = security_utils.apply_compliance(
                            code, config.compliance_rules
                        )
                        if violations:
                            # --- Audit/Logging Change: Use log_audit_event ---
                            log_audit_event(
                                "Compliance Violation", {"violations": violations}
                            )
                            # --- End Audit/Logging Change ---

                    # --- Security Scans Change: Use unified scanning utility ---
                    code_files = await perform_security_scans(code_files)
                    # --- End Security Scans Change ---

                # HITL (only when enabled)
                if getattr(config, "allow_interactive_hitl", False):
                    with tracer.start_as_current_span("hitl_review"):
                        # We pass a dummy JsonConsoleAuditLogger to hitl_review for signature compatibility
                        status, feedback = await hitl_review(
                            code_files,
                            feedback_store,
                            req_hash,
                            True,
                            redis_client,
                            JsonConsoleAuditLogger(),
                        )
                    if status != "approved":
                        # --- Audit/Logging Change: Use log_audit_event ---
                        log_audit_event("HITL Rejection", {"feedback": feedback})
                        # --- End Audit/Logging Change ---
                        return {
                            "error.txt": f"Code rejected by human review. Feedback: {feedback}"
                        }
                else:
                    # Skip HITL entirely; treat as approved
                    status, feedback = ("approved", None)

                # --- Audit/Logging Change: Use log_audit_event ---
                log_audit_event(
                    "Code Generation Completed",
                    {"files": list(code_files.keys()), "model": backend_used},
                )
                # --- End Audit/Logging Change ---
                return code_files

            except Exception as e:
                logger.exception(f"Code generation failed: {e}")
                # --- Audit/Logging Change: Use log_audit_event ---
                log_audit_event(
                    "Code Generation Failed", {"error": str(e), "traceback": repr(e)}
                )
                # --- End Audit/Logging Change ---
                CODEGEN_ERRORS.labels(type(e).__name__).inc()
                return {
                    "error.txt": f"Error: Code generation failed. Details: {str(e)}"
                }


# ==============================================================================
# FastAPI application (importable for tests and deployment)
# ==============================================================================

app = FastAPI()


# ==============================================================================
# FastAPI routes
# ==============================================================================
@app.get("/health")
async def health_check():
    failed_backends = []
    status = "ok"
    details = {}

    audit_logger = JsonConsoleAuditLogger()

    # Redis health (best-effort)
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost")
        # Check connection (uses synchronous blocking call for simplicity in this health check)
        redis_client = await aioredis.from_url(redis_url)
        await redis_client.ping()
        await redis_client.close()
        details["redis"] = "ok"
    except Exception as e:
        status = "degraded"
        failed_backends.append("Redis")
        details["redis"] = f"failed: {e}"

    # LLM config presence (best-effort, uses CodeGenConfig)
    llm_config = CodeGenConfig(
        {
            "backend": "openai",
            "api_keys": {"openai": os.getenv("OPENAI_API_KEY")},
            "model": {"openai": "gpt-4o"},
        }
    )
    if not llm_config.api_keys.get("openai"):
        status = "degraded"
        failed_backends.append("openai")
        details["openai"] = "missing API key"
    else:
        details["openai"] = "ok"

    if not os.path.exists("templates"):
        status = "degraded"
        failed_backends.append("templates")
        details["templates"] = "directory missing"

    audit_logger.log_action("HealthCheck", {"status": status, "details": details})

    if failed_backends:
        return {
            "status": status,
            "details": f"Failed components: {', '.join(failed_backends)}",
        }

    return {"status": "ok", "details": details}


@app.get("/metrics")
async def metrics():
    # from prometheus_client import generate_latest  # Already imported
    data = generate_latest()
    return {
        "content_type": "text/plain; version=0.0.4",
        "metrics": data.decode("utf-8", errors="ignore"),
    }


@app.post("/review")
async def review_code(review_request: Dict[str, Any]):
    """
    Simple wrapper endpoint to trigger code generation and HITL review.
    This is intentionally thin; heavy lifting is in generate_code / hitl_review.
    """
    requirements = review_request.get("requirements", {})
    initial_state = review_request.get("initial_state", "")
    config_path = review_request.get("config_path", "prod_config.yaml")

    await generate_code(requirements, initial_state, config_path)
    req_hash = hash(json.dumps(requirements, sort_keys=True))
    review_url = f"/submit_review?req_hash={req_hash}"

    return {"status": "pending", "req_hash": req_hash, "review_url": review_url}


@app.post("/submit_review")
async def submit_review(review_submission: Dict[str, Any]):
    req_hash = review_submission.get("req_hash")
    status = review_submission.get("status")
    feedback = review_submission.get("feedback")

    if status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    if status == "rejected" and (not feedback or len(feedback) < 10):
        raise HTTPException(
            status_code=400,
            detail="Feedback must be at least 10 characters for rejected code.",
        )

    # Best-effort: in real deployment this would persist feedback
    # Here we just log it.
    log_audit_event(
        "HITL Review Submitted",
        {
            "req_hash": req_hash,
            "status": status,
            "feedback": feedback,
        },
    )

    if status == "approved":
        HITL_APPROVAL_RATE.set(1)
    else:
        HITL_APPROVAL_RATE.set(0)

    return {"status": status, "feedback": feedback}


# ==============================================================================
# Demo harness (optional)
# ==============================================================================
if __name__ == "__main__":
    # This is a self-contained demo harness, not used in production.
    import uvicorn

    # Setup for Demo Run
    config_data = {
        "backend": "openai",
        "api_keys": {"openai": os.getenv("OPENAI_API_KEY")},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": "prod_feedback.db"},
        "audit_logger": {"type": "console"},
        "compliance": {
            "banned_functions": ["pickle"],
            "max_line_length": 120,
            "banned_imports": ["os", "subprocess"],
        },
    }
    with open("prod_config.yaml", "w") as f:
        yaml.dump(config_data, f)
    if not os.path.exists("templates"):
        os.makedirs("templates")
    with open("templates/python.jinja2", "w") as f:
        f.write(
            "Generate a Python script. Requirements: {{ requirements.features }}. Respond ONLY with a valid JSON object with a 'files' key mapping filenames to code strings."
        )
    with open("templates/base.jinja2", "w") as f:
        f.write(
            "Generate a generic script. Requirements: {{ requirements.features }}. Respond ONLY with a valid JSON object with a 'files' key mapping filenames to code strings."
        )

    requirements_data = {
        "features": ["Implement a function to calculate the nth Fibonacci number."],
        "target_language": "python",
    }

    async def main():
        # 1. Start Prometheus metrics server in the background (using uvicorn in a real deployment)
        start_http_server(8000)

        # 2. Run a single code generation task
        print("Starting single code generation task...")
        # NOTE: This call will fail if OPENAI_API_KEY is not set.
        generated_code = await generate_code(
            requirements_data, "Initial state.", "prod_config.yaml"
        )
        print("\n--- Final Output ---")
        for filename, content in generated_code.items():
            print(f"File: {filename}\n{content}\n")

        # 3. Start the FastAPI server (blocking call if we were using it as the main entry)
        print("Starting FastAPI server (CTRL+C to stop)...")
        # NOTE: For demo simplicity, we use uvicorn.run for the server part, and just output the code above.
        # If you wanted both, you'd use multiprocessing or an ASGI server runner.
        uvicorn.run(app, host="0.0.0.0", port=8001)

    # Guarded so nothing runs during tests unless explicitly requested
    if os.getenv("CODEGEN_RUN_DEMO") == "1" and os.getenv("OPENAI_API_KEY"):
        asyncio.run(main())
    elif os.getenv("CODEGEN_RUN_DEMO") == "1" and not os.getenv("OPENAI_API_KEY"):
        print(
            "Skipping example run: OPENAI_API_KEY environment variable is not set. Cannot run LLM."
        )
    else:
        print(
            "Skipping demo harness. To run, set CODEGEN_RUN_DEMO=1 and OPENAI_API_KEY."
        )
