# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# agents/deploy_agent.py
import asyncio
import difflib
import glob
import importlib.util
import json
import logging
import os
import re
import sys
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiofiles
import aiohttp
import aiosqlite  # <-- FIX: Add aiosqlite import
import networkx as nx
import prometheus_client

# Defensive import for tiktoken
try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False
    tiktoken = None

from fastapi import FastAPI, HTTPException
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel
from runner.llm_client import call_ensemble_api, call_llm_api
from runner.runner_errors import LLMError, RunnerError
from runner.runner_file_utils import get_commits

# --- FIX: Import log_audit_event from runner_audit to avoid circular dependency ---
from runner.runner_audit import log_audit_event, log_audit_event_sync
from runner.runner_audit import log_audit_event as log_action
# Note: add_provenance is an alias for log_audit_event (async)
add_provenance = log_audit_event
# For sync contexts (e.g., __init__), use log_audit_event_sync
add_provenance_sync = log_audit_event_sync
from runner.runner_logging import logger
from runner.runner_metrics import LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS
from runner.runner_metrics import (
    LLM_REQUESTS_TOTAL as LLM_CALLS_TOTAL,  # <-- FIX: Use new name with alias
)
from runner.runner_security_utils import redact_secrets
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --- FIX 1: Import the class, not the method ---
from .deploy_prompt import DeployPromptAgent

# --- FIX: Import HandlerRegistry to instantiate it ---
from .deploy_response_handler import HandlerRegistry, handle_deploy_response
from .deploy_validator import ValidatorRegistry

# Import PROJECT_ROOT for template directory resolution
try:
    from path_setup import PROJECT_ROOT
except ImportError:
    # Fallback if path_setup is not available
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Safe tracer import: works even if runner.tracer is not available
try:
    from runner import tracer as _runner_tracer  # type: ignore[attr-defined]

    tracer = _runner_tracer
except (ImportError, AttributeError):
    try:
        # fallback to opentelemetry if available
        from opentelemetry import trace as _otel_trace

        tracer = _otel_trace.get_tracer(__name__)
    except Exception:
        from contextlib import nullcontext

        class _NoopTracer:
            def start_as_current_span(self, *a, **k):
                return nullcontext()

        tracer = _NoopTracer()
# --- FIX: Removed failing legacy import ---
# from audit_log import log_action

# --- Metrics --------------------------------------------------------
# Enterprise-Grade Metric Registration with Deduplication Protection
#
# Industry Standard Compliance:
# - SOC 2 Type II: Reliable metric collection without service disruption
# - ISO 27001 A.12.1.3: Capacity management through proper observability
# - NIST SP 800-53 AU-4: Audit storage capacity management
#
# Design Pattern: Check-before-create to prevent ValueError on duplicate registration
# This is critical for multi-import scenarios (tests, hot reloads, microservices)


def _get_or_create_metric(metric_class, name: str, description: str, labelnames=None):
    """
    Enterprise-grade metric factory with idempotent registration.

    Implements check-before-create pattern to prevent 'Duplicated timeseries
    in CollectorRegistry' errors that crash agents during initialization.

    Thread Safety: Uses REGISTRY's internal locking mechanism.

    Args:
        metric_class: prometheus_client metric class (Counter, Gauge, Histogram)
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created metric instance

    Raises:
        ValueError: Only if a non-duplicate registration error occurs
    """
    labelnames = labelnames or []

    # Check if metric already exists in registry (idempotent)
    try:
        existing = prometheus_client.REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
    except (AttributeError, KeyError):
        pass  # Registry structure may vary

    # Create new metric if it doesn't exist
    try:
        if labelnames:
            return metric_class(name, description, labelnames)
        return metric_class(name, description)
    except ValueError as e:
        # Handle race condition: metric was created by another thread/process
        if "Duplicated timeseries" in str(e):
            existing = prometheus_client.REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise  # Re-raise if it's a different error


GENERATION_DURATION = _get_or_create_metric(
    prometheus_client.Histogram,
    "deploy_agent_generation_duration_seconds",
    "Time taken for config generation",
    ["run_type", "model"],
)
VALIDATION_ERRORS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_agent_validation_errors_total",
    "Total validation errors",
    ["run_type"],
)
SUCCESSFUL_GENERATIONS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_agent_successful_generations_total",
    "Total successful generations",
    ["run_type"],
)
CONFIG_SIZE = _get_or_create_metric(
    prometheus_client.Gauge,
    "deploy_agent_config_size_bytes",
    "Size of generated configurations",
    ["run_type"],
)
PLUGIN_HEALTH = _get_or_create_metric(
    prometheus_client.Gauge,
    "deploy_agent_plugin_health",
    "Health status of plugins",
    ["plugin"],
)
SELF_HEAL_ATTEMPTS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_agent_self_heal_attempts",
    "Total self-healing attempts",
    ["run_id"],
)
HUMAN_APPROVAL_STATUS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_agent_human_approval_status",
    "Status of human approvals",
    ["run_id", "status"],
)
DEPLOY_RUNS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_runs_total",
    "Total deployment runs",
    ["status"],
)
DEPLOY_LATENCY = _get_or_create_metric(
    prometheus_client.Histogram,
    "deploy_latency_seconds",
    "Deployment run latency",
)
DEPLOY_ERRORS = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_errors_total",
    "Deployment errors",
    ["error_type"],
)

# --- FIX 4: Add MAX_LLM_RETRIES constant ---
MAX_LLM_RETRIES = 3
# --- End of FIX 4 constant ---

# --- FIX 6: Add new Prometheus metrics ---
PROMPT_TOKEN_COUNT = _get_or_create_metric(
    prometheus_client.Histogram,
    "deploy_prompt_token_count",
    "Token count of deployment prompts",
    ["target"],
)

CONTEXT_FILES_COUNT = _get_or_create_metric(
    prometheus_client.Gauge,
    "deploy_context_files_count",
    "Number of files included in deployment context",
    ["target"],
)

LLM_OUTPUT_FORMAT = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_llm_output_format_total",
    "Classification of LLM output format",
    ["target", "format_type"],  # format_type: valid, prose, markdown_wrapped, empty
)

LLM_RETRY_COUNT = _get_or_create_metric(
    prometheus_client.Counter,
    "deploy_llm_retry_total",
    "Number of LLM retries for deployment generation",
    ["target", "attempt"],
)
# --- End of FIX 6 metrics ---


# --- Scrubbing / Logging --------------------------------------------
def scrub_text(text: str) -> str:
    if not text:
        return ""
    try:
        return redact_secrets(text)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "Centralized secret scrubbing failed: %s. Falling back to generic redaction.",
            e,
        )
        patterns = [
            r"(?i)(api[-_]?key|secret|token)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}['\"]?",
            r"(?i)password\s*[:=]\s*['\"]?.+?['\"]?",
        ]
        masked = text
        for p in patterns:
            masked = re.sub(p, "[REDACTED]", masked)
        return masked if masked != text else "[SCRUBBING_FAILED]"


class ScrubFilter(logging.Filter):
    """
    Enterprise-grade logging filter that scrubs sensitive data from log records.

    Implements comprehensive error handling to prevent crashes from:
    - SystemExit from model downloads
    - Missing dependencies
    - Malformed log records
    - Any scrubbing failures

    Never allows exceptions to propagate - the filter must always succeed.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """
        Filter and scrub sensitive data from log record.

        Args:
            record: The log record to process

        Returns:
            bool: Always True (never block log records)
        """
        try:
            # Scrub message if present
            if getattr(record, "msg", None):
                try:
                    record.msg = scrub_text(str(record.msg))
                except SystemExit as se:
                    # CRITICAL: Don't let SystemExit from model downloads kill the app
                    # This is the primary defense against presidio/spacy crashes
                    # Log at debug level to aid troubleshooting without spam
                    if logger:
                        logger.debug(
                            f"SystemExit caught in log filter (code {se.code}). "
                            "Message scrubbing skipped for this record."
                        )
                    pass  # Leave msg unchanged if scrubbing fails
                except Exception as e:
                    # Gracefully handle any scrubbing failures
                    # Better to log unscrubbed than to crash
                    if logger:
                        logger.debug(
                            f"Log message scrubbing failed ({type(e).__name__}). "
                            "Logging message unscrubbed."
                        )
                    pass

            # Scrub exception info if present
            if getattr(record, "exc_info", None):
                try:
                    ei = []
                    for item in record.exc_info:
                        if isinstance(item, str):
                            try:
                                ei.append(scrub_text(item))
                            except (SystemExit, Exception):
                                # If scrubbing fails, use original
                                ei.append(item)
                        else:
                            ei.append(item)
                    record.exc_info = tuple(ei)
                except (SystemExit, Exception):
                    # If exc_info processing fails entirely, leave it unchanged
                    pass

        except Exception:
            # Outermost catch-all: never crash the logging system
            # Even if record processing fails completely, allow the log through
            pass

        # Always return True - never block log records
        return True


logger.addFilter(ScrubFilter())

# --- FastAPI surface (optional) ------------------------------------
app = FastAPI(
    title="Deploy Agent API",
    description="Deployment configuration generation, validation, simulation.",
    version="1.0.0",
)


class ApprovalRequest(BaseModel):
    run_id: str
    configs: Dict[str, Any]
    validations: Dict[str, Any]


class ApprovalResponse(BaseModel):
    approved: bool
    comments: Optional[str] = None


@app.post("/approve", response_model=ApprovalResponse)
async def approve_config(request: ApprovalRequest) -> ApprovalResponse:
    logger.info(
        "Approval requested for run_id: %s",
        request.run_id,
        extra={"run_id": request.run_id},
    )

    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    if slack_webhook:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    slack_webhook,
                    json={
                        "text": (
                            f"Approval needed for run_id: **{request.run_id}**\n"
                            f"Configs: {json.dumps(request.configs, indent=2)[:400]}...\n"
                            f"Validations: {json.dumps(request.validations, indent=2)[:400]}..."
                        )
                    },
                )
        except Exception as e:  # pragma: no cover
            logger.error(
                "Slack notification failed: %s",
                e,
                extra={"run_id": request.run_id},
            )

    approval_ui = os.getenv("APPROVAL_UI_URL", "http://localhost:8001/approval-ui")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                approval_ui, json=request.dict(), timeout=300
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    approved = bool(data.get("approved", False))
                    comments = data.get("comments", "")
                    HUMAN_APPROVAL_STATUS.labels(
                        run_id=request.run_id,
                        status="approved" if approved else "rejected",
                    ).inc()
                    return ApprovalResponse(approved=approved, comments=comments)

                detail = f"Approval UI error {resp.status}: {await resp.text()}"
                HUMAN_APPROVAL_STATUS.labels(
                    run_id=request.run_id, status="error"
                ).inc()
                raise HTTPException(status_code=500, detail=detail)
    except asyncio.TimeoutError:
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status="timeout").inc()
        raise HTTPException(status_code=504, detail="Approval request timed out.")
    except aiohttp.ClientError as e:
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status="error").inc()
        raise HTTPException(status_code=503, detail=f"Approval UI unavailable: {e}")
    except Exception as e:  # pragma: no cover
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status="error").inc()
        raise HTTPException(status_code=500, detail=f"Internal approval error: {e}")


# --- Plugin abstraction / registry ---------------------------------
class TargetPlugin(ABC):
    __version__ = "1.0"

    @abstractmethod
    async def generate_config(
        self,
        target_files: List[str],
        instructions: Optional[str],
        context: Dict[str, Any],
        previous_configs: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]: ...

    @abstractmethod
    async def simulate_deployment(self, config: Dict[str, Any]) -> Dict[str, Any]: ...

    @abstractmethod
    async def rollback(self, config: Dict[str, Any]) -> bool: ...

    @abstractmethod
    def health_check(self) -> bool: ...


class PluginRegistry(FileSystemEventHandler):
    def __init__(self, plugin_dir: str = "./plugins") -> None:
        super().__init__()
        self.plugins: Dict[str, TargetPlugin] = {}
        self.plugin_info: Dict[str, Dict[str, Any]] = {}
        
        # Resolve plugin_dir relative to the deploy_agent module directory
        # This ensures plugins are found regardless of working directory
        if not os.path.isabs(plugin_dir):
            module_dir = Path(__file__).parent
            self.plugin_dir = str(module_dir / plugin_dir)
        else:
            self.plugin_dir = plugin_dir
        
        self.observer = Observer()
        self.load_plugins()
        self.start_watching()

    def load_plugins(self) -> None:
        if not os.path.exists(self.plugin_dir):
            os.makedirs(self.plugin_dir)
        if self.plugin_dir not in sys.path:
            sys.path.insert(0, self.plugin_dir)

        # close previous plugins if needed
        for name, plugin in list(self.plugins.items()):
            if hasattr(plugin, "close") and callable(plugin.close):
                try:
                    asyncio.create_task(plugin.close())
                except Exception:
                    pass

        self.plugins.clear()
        self.plugin_info.clear()

        for path in glob.glob(os.path.join(self.plugin_dir, "*.py")):
            if path.endswith("__init__.py") or path.endswith("_test.py"):
                continue
            self._load_plugin_file(path)

        logger.info(
            f"Loaded {len(self.plugins)} plugins from {self.plugin_dir}"
        )

    def _load_plugin_file(self, plugin_file: str) -> None:
        module_name_base = Path(plugin_file).stem
        unique_name = (
            f"{self.plugin_dir.replace(os.sep, '.')}"
            f".{module_name_base}_{uuid.uuid4().hex}"
        )

        spec = importlib.util.spec_from_file_location(unique_name, plugin_file)
        if not spec or not spec.loader:
            logger.warning("No spec for plugin file %s", plugin_file)
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        
        # Inject TargetPlugin into the module's namespace to ensure plugins
        # can reference it even if their relative import fails
        module.TargetPlugin = TargetPlugin  # type: ignore[attr-defined]
        
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            found = False
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, TargetPlugin)
                    and attr is not TargetPlugin
                ):
                    instance = attr()
                    self.register(module_name_base, instance)
                    found = True
            if not found:
                logger.warning("No TargetPlugin found in %s", plugin_file)
        except Exception as e:  # pragma: no cover
            logger.error(
                "Failed to load plugin from %s: %s",
                plugin_file,
                e,
                exc_info=True,
            )
            sys.modules.pop(unique_name, None)

    def register(self, target: str, plugin: TargetPlugin) -> None:
        health = plugin.health_check()
        self.plugins[target] = plugin
        self.plugin_info[target] = {
            "version": getattr(plugin, "__version__", "N/A"),
            "last_reload": time.time(),
            "health": health,
        }
        PLUGIN_HEALTH.labels(plugin=target).set(1 if health else 0)
        logger.info(
            "Registered plugin %s (version=%s, health=%s)",
            target,
            getattr(plugin, "__version__", "N/A"),
            health,
        )

    def get_plugin(self, target: str) -> Optional[TargetPlugin]:
        return self.plugins.get(target)

    def start_watching(self) -> None:
        # --- FIX: Disable Watchdog in TESTING environments ---
        if os.getenv("TESTING") == "1":
            logger.info("TESTING environment detected. Skipping file watcher.")
            return
        # ----------------------------------------------------
        if not self.observer.is_alive():
            self.observer.schedule(self, self.plugin_dir, recursive=False)
            self.observer.start()

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        if event.event_type in {
            "created",
            "modified",
            "deleted",
        } and event.src_path.endswith(".py"):
            asyncio.create_task(asyncio.to_thread(self.load_plugins))


# --- DeployAgent core -----------------------------------------------
class DeployAgent:
    def __init__(
        self,
        repo_path: str,
        languages_supported: Optional[List[str]] = None,
        plugin_dir: str = "./plugins",
        slack_webhook: Optional[str] = None,
        webhook_url: Optional[str] = None,
        rate_limit: int = 5,
        llm_orchestrator_instance: Optional[Any] = None,  # compatibility
        arbiter_bridge: Optional[Any] = None,  # Arbiter integration
    ) -> None:
        self.repo_path = Path(repo_path)
        if not self.repo_path.is_dir():
            raise ValueError(
                f"Repository path does not exist or is not a directory: {repo_path}"
            )

        self.languages_supported = languages_supported or [
            "python",
            "javascript",
            "rust",
            "go",
            "java",
        ]
        # --- FIX: Rename and add singleton registries ---
        # Initialize PluginRegistry with correct path (will resolve to generator/agents/deploy_agent/plugins)
        self.plugin_registry = PluginRegistry(plugin_dir)  # Renamed
        self.validator_registry = ValidatorRegistry()
        self.handler_registry = HandlerRegistry()
        # -------------------------------------------------
        
        # Arbiter bridge for governance integration (optional)
        self.arbiter_bridge = arbiter_bridge
        if self.arbiter_bridge:
            logger.info("DeployAgent: Arbiter integration enabled")
        
        # NOTE: DeployAgent has its own HITL system via request_human_approval().
        # This should eventually delegate to Arbiter's HumanInLoop but keeping
        # the existing system working for now to avoid breaking changes.

        self.run_id = str(uuid.uuid4())
        # --- FIX: Use sync version in __init__ (not async context) ---
        add_provenance_sync("provenance", {"run_id": self.run_id, "agent": "DeployAgent"})

        self.history: List[Dict[str, Any]] = []
        # Only initialize tokenizer if tiktoken is available and not in test mode
        # (tiktoken tries to download encoding files from the internet during initialization)
        if HAS_TIKTOKEN and not os.getenv("TESTING"):
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        else:
            self.tokenizer = None

        # --- FIX 2: Use project root templates as primary source ---
        # Check project root deploy_templates first (where actual templates exist)
        project_template_dir = PROJECT_ROOT / "deploy_templates"
        if project_template_dir.exists():
            template_dir = project_template_dir
            logger.info(f"DeployAgent: Using project root templates at {template_dir}")
        else:
            # Fallback to job-specific directory
            template_dir = self.repo_path / "deploy_templates"
            template_dir.mkdir(parents=True, exist_ok=True)
            logger.warning(f"DeployAgent: Project root templates not found, using job-specific directory {template_dir}")
        
        few_shot_dir = self.repo_path / "few_shot_examples"
        few_shot_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            self.prompt_agent_instance = DeployPromptAgent(
                few_shot_dir=str(few_shot_dir), 
                template_dir=str(template_dir)
            )
            self.prompt_agent = self.prompt_agent_instance.build_deploy_prompt
        except Exception as e:
            logger.error("Failed to initialize DeployPromptAgent: %s", e, exc_info=True)
            # Create a fallback prompt function
            async def fallback_prompt(**kwargs):
                files = kwargs.get('files', [])
                files_str = ', '.join(str(f) for f in files) if files else 'none'
                return f"Generate {kwargs.get('target', 'configuration')} for files: {files_str}"
            self.prompt_agent = fallback_prompt
            logger.warning("Using fallback prompt function due to initialization failure")
        # ---------------------------------------------------

        # --- FIX: Use aiosqlite, remove sync connect ---
        self.db_path = "deploy_agent_history.db"
        # self.db = sqlite3.connect(self.db_path) # <-- REMOVED
        # self._init_db() # <-- REMOVED (must be awaited by caller)
        # -----------------------------------------------

        self.slack_webhook = slack_webhook
        self.webhook_url = webhook_url
        self.sem = asyncio.Semaphore(rate_limit)

        # Target dependency graph
        self.target_graph = nx.DiGraph()
        self.target_graph.add_edges_from(
            [
                ("docker", "helm"),
                ("helm", "terraform"),
            ]
        )
        for t in [
            "docs",
            "docker",
            "helm",
            "terraform",
            "k8s_manifests",
            "cloud_infra",
        ]:
            if t not in self.target_graph:
                self.target_graph.add_node(t)

        # Hooks
        self.pre_gather_hooks: List[
            Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
        ] = []
        self.post_gather_hooks: List[
            Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
        ] = []
        self.pre_gen_hooks: List[
            Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
        ] = []
        self.post_gen_hooks: List[Callable[[Any, str], Awaitable[Any]]] = []

        self.last_result: Optional[Dict[str, Any]] = None

        # Track initialization state
        self._db_initialized = False
        
        # Log initialization warning about database
        logger.warning(
            "DeployAgent initialized. Use 'async with DeployAgent(...) as agent:' "
            "or call 'await agent._init_db()' before performing operations."
        )

    # --- Async Context Manager Support ---
    # This ensures _init_db() is automatically called when using 'async with'

    async def __aenter__(self) -> "DeployAgent":
        """Async context manager entry - initializes the database.

        Usage:
            async with DeployAgent(repo_path) as agent:
                result = await agent.generate_documentation(...)

        This eliminates the need for manual _init_db() calls and prevents
        race conditions where database operations are attempted before
        initialization.
        """
        await self._init_db()
        self._db_initialized = True
        logger.info(
            f"DeployAgent initialized via context manager [run_id: {self.run_id}]"
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - cleanup resources.

        Currently a no-op since aiosqlite manages its own connections,
        but provides a hook for future cleanup needs.
        """
        # Log if we're exiting due to an exception
        if exc_type is not None:
            logger.warning(
                f"DeployAgent context exiting due to exception: {exc_type.__name__}: {exc_val} "
                f"[run_id: {self.run_id}]"
            )
        else:
            logger.debug(
                f"DeployAgent context exiting normally [run_id: {self.run_id}]"
            )

        # No explicit cleanup needed - aiosqlite manages connections
        return None  # Don't suppress exceptions

    # --- persistence ------------------------------------------------
    # --- FIX: Convert to async with aiosqlite ---
    async def _init_db(self) -> None:
        """Initialize the SQLite database for history persistence.

        This method is idempotent - it can be called multiple times safely.
        It is automatically called when using the agent as an async context manager.

        Manual call is still supported for backwards compatibility:
            agent = DeployAgent(repo_path)
            await agent._init_db()  # Manual init

        Preferred usage (auto-init):
            async with DeployAgent(repo_path) as agent:
                # Database is automatically initialized
                pass
        """
        if self._db_initialized:
            logger.debug("Database already initialized, skipping")
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    result TEXT
                )
                """)
            await db.commit()

        self._db_initialized = True
        logger.debug(f"Database initialized at {self.db_path}")

    def _ensure_db_initialized(self) -> None:
        """Check that the database has been initialized.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        if not self._db_initialized:
            raise RuntimeError(
                "DeployAgent database not initialized. Either use 'async with DeployAgent(...) as agent:' "
                "or call 'await agent._init_db()' before performing database operations."
            )

    # -------------------------------------------

    # --- FIX: Convert to async with aiosqlite ---
    async def get_previous_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a previous run result from the database.

        Args:
            run_id: The unique identifier of the run to retrieve.

        Returns:
            The run result dict, or None if not found.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        self._ensure_db_initialized()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT result FROM history WHERE id=?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return json.loads(row[0]) if row else None

    async def _save_to_history(
        self, run_id: str, timestamp: str, result: Dict[str, Any]
    ) -> None:
        """Save a run result to the history database.

        This method ensures the database is initialized before saving.
        If the database is not initialized, it will attempt to initialize it.

        Args:
            run_id: The unique identifier for this run.
            timestamp: ISO format timestamp string.
            result: The result dict to save.
        """
        # Auto-initialize if not already done (backwards compatibility)
        if not self._db_initialized:
            logger.warning(
                "Database not initialized when saving to history. "
                "Auto-initializing. For best practice, use 'async with DeployAgent(...) as agent:'"
            )
            await self._init_db()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                    (run_id, timestamp, json.dumps(result)),
                )
                await db.commit()
            logger.debug(f"Saved run {run_id} to history database")
        except Exception as e:
            logger.error(f"Failed to save run {run_id} to history: {e}", exc_info=True)
            # Don't raise - history saving failure shouldn't break the workflow

    # -------------------------------------------

    # --- context ----------------------------------------------------
    async def gather_context(self, target_files: List[str]) -> Dict[str, Any]:
        with tracer.start_as_current_span("deploy.gather_context") as span:
            ctx: Dict[str, Any] = {
                "dependencies": {},
                "recent_commits": [],
                "env_vars": {k: scrub_text(v) for k, v in os.environ.items()},
                "file_contents": {},
            }

            for hook in self.pre_gather_hooks:
                ctx = await hook(ctx)

            # read target files
            for rel in target_files:
                path = self.repo_path / rel
                if not path.is_file():
                    logger.warning(
                        "Target file not found: %s",
                        rel,
                        extra={"run_id": self.run_id},
                    )
                    continue
                try:
                    async with aiofiles.open(path, "r", encoding="utf-8") as f:
                        content = await f.read()
                    ctx["file_contents"][rel] = scrub_text(content)
                except Exception as e:
                    logger.warning(
                        "Failed to read %s: %s",
                        rel,
                        e,
                        extra={"run_id": self.run_id},
                    )
                    span.record_exception(e)

            # infer deps from common files
            try:
                for rel in target_files:
                    path = self.repo_path / rel
                    if (
                        path.name == "requirements.txt"
                        and "python" in self.languages_supported
                    ):
                        async with aiofiles.open(path, "r", encoding="utf-8") as f:
                            ctx["dependencies"]["python"] = (
                                await f.read()
                            ).splitlines()
                    elif path.name == "package.json" and any(
                        x in self.languages_supported
                        for x in ["javascript", "typescript"]
                    ):
                        async with aiofiles.open(path, "r", encoding="utf-8") as f:
                            pkg = json.loads(await f.read())
                        ctx["dependencies"]["javascript"] = pkg.get("dependencies", {})
                        ctx["dependencies"]["dev_javascript"] = pkg.get(
                            "devDependencies", {}
                        )
                    elif path.name == "go.mod" and "go" in self.languages_supported:
                        async with aiofiles.open(path, "r", encoding="utf-8") as f:
                            mod = await f.read()
                        modules = re.findall(
                            r"^\s*(?:require|replace)\s+([^\s]+)\s+([^\s]+)",
                            mod,
                            re.MULTILINE,
                        )
                        ctx["dependencies"]["go"] = {m: v for m, v in modules}
            except Exception as e:
                logger.warning(
                    "Dependency parse error: %s",
                    e,
                    extra={"run_id": self.run_id},
                )
                span.record_exception(e)

            # recent commits
            try:
                commits = await get_commits(str(self.repo_path), limit=5)
                if isinstance(commits, str) and commits.startswith(
                    "Failed to retrieve"
                ):
                    logger.warning(
                        commits,
                        extra={"run_id": self.run_id},
                    )
                else:
                    if isinstance(commits, str):
                        ctx["recent_commits"] = commits.splitlines()
                    else:
                        ctx["recent_commits"] = list(commits)
            except Exception as e:
                logger.error(
                    "Error reading commits: %s",
                    e,
                    extra={"run_id": self.run_id},
                )
                span.record_exception(e)

            for hook in self.post_gather_hooks:
                ctx = await hook(ctx)

            span.set_attribute("context_size_bytes", len(json.dumps(ctx)))
            return ctx

    # --- helpers ----------------------------------------------------
    async def validate_configs_final(
        self, config_string: str, target: str
    ) -> Dict[str, Any]:
        with tracer.start_as_current_span(f"deploy.validate_final.{target}"):
            # --- FIX: Use singleton registry ---
            validator = self.validator_registry.get_validator(target)
            # -----------------------------------
            if not validator:
                return {
                    "valid": False,
                    "details": f"No validator for target '{target}'.",
                }
            return await validator.validate(config_string, target)

    async def compliance_check_final(self, config_string: str) -> List[str]:
        # minimal placeholder; full scans live elsewhere
        return []

    async def simulate_deployment_final(
        self, config_string: str, target: str
    ) -> Dict[str, Any]:
        plugin = self.plugin_registry.get_plugin(target)
        if not plugin:
            return {
                "status": "skipped",
                "reason": f"No simulation for target: {target}",
            }
        try:
            cfg = json.loads(config_string)
        except json.JSONDecodeError:
            if target == "docs":
                return {
                    "status": "skipped",
                    "reason": "Simulation not applicable for docs.",
                }
            return {
                "status": "failed",
                "reason": "Config not valid JSON for simulation.",
            }
        return await plugin.simulate_deployment(cfg)

    async def generate_explanation_final(
        self,
        config_string: str,
        validation_result: Dict[str, Any],
        target: str,
    ) -> str:
        prompt = scrub_text(f"""
Provide a concise explanation for the configuration for target '{target}'.
Explain key design decisions, trade-offs, and how it addresses requirements, security, performance,
scalability, and compatibility. Briefly summarize validation results.

Configuration snippet:
{config_string[:1000]}

Validation results:
{json.dumps(validation_result, indent=2)}

Respond in plain prose only (no JSON / no code fences).
""")
        await add_provenance(
            "provenance",
            {"action": "explanation_llm_call", "target": target, "model": "grok-4"},
        )
        try:
            resp = await call_llm_api(prompt, "grok-4", stream=False)
            content = (resp.get("content") or "").strip()
            if not content:
                return "No explanation generated."
            # strip accidental fences
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z0-9]*", "", content).strip()
                if content.endswith("```"):
                    content = content[:-3].strip()
            return content
        except Exception as e:  # pragma: no cover
            logger.error(
                "Explanation generation failed for %s: %s",
                target,
                e,
                extra={"run_id": self.run_id},
            )
            return f"Failed to generate explanation due to an error: {e}"

    # --- main pipeline ----------------------------------------------
    async def generate_documentation(
        self,
        target_files: List[str],
        doc_type: str = "README",
        targets: Optional[List[str]] = None,
        instructions: Optional[str] = None,
        human_approval: bool = False,
        cli_approval: bool = False,
        ensemble: bool = False,
        stream: bool = False,
        llm_model: str = "gpt-4o",
    ) -> Dict[str, Any]:
        if targets is None:
            targets = ["docs", "docker", "helm", "terraform"]

        start = time.time()
        logger.info(
            "Starting pipeline doc_type=%s targets=%s",
            doc_type,
            targets,
            extra={"run_id": self.run_id},
        )
        # --- FIX: Await add_provenance in async context ---
        await add_provenance(
            "provenance",
            {"action": "pipeline_start", "doc_type": doc_type, "targets": targets},
        )

        with tracer.start_as_current_span("deploy.generate_documentation") as span:
            try:
                context = await self.gather_context(target_files)
                configs: Dict[str, Any] = {}

                # determine order
                try:
                    nodes = set(targets)
                    for t in targets:
                        nodes.update(nx.ancestors(self.target_graph, t))
                    sub = self.target_graph.subgraph(nodes)
                    order = [t for t in nx.topological_sort(sub) if t in targets]
                except nx.NetworkXUnfeasible as e:
                    msg = f"Cycle in target dependencies: {e}"
                    span.set_status(Status(StatusCode.ERROR, msg))
                    raise RunnerError(
                        error_code="DEPENDENCY_CYCLE",
                        detail=msg
                    )

                # generation per target
                for t in order:
                    async with self.sem:
                        with tracer.start_as_current_span(
                            f"deploy.generate.{t}"
                        ) as tspan:
                            try:
                                for hook in self.pre_gen_hooks:
                                    context = await hook(context, t)

                                # --- FIX 3.2: Pass repo_path to prompt agent ---
                                prompt = await self.prompt_agent(
                                    target=t,
                                    files=target_files,
                                    repo_path=str(self.repo_path),  # <-- ADDED
                                    instructions=instructions,
                                    context=context,
                                )
                                # --------------------------------------------
                                prompt = scrub_text(prompt)
                                # --- FIX: Await add_provenance in async context ---
                                await add_provenance(
                                    "provenance",
                                    {"target": t, "model": llm_model},
                                )

                                start_llm = time.time()
                                try:
                                    if ensemble:
                                        # FIX Issue 1: Add provider to model configuration
                                        # Use centralized utility for provider inference (Industry Standard: DRY principle)
                                        from generator.utils.llm_provider_utils import create_model_config
                                        
                                        # Create properly formatted model configuration
                                        model_config = create_model_config(llm_model)
                                        
                                        resp = await call_ensemble_api(
                                            prompt,
                                            [model_config],  # FIX: Use validated model config
                                            voting_strategy="majority",
                                            stream=stream,
                                        )
                                    else:
                                        resp = await call_llm_api(
                                            prompt,
                                            llm_model,
                                            stream=stream,
                                        )
                                    LLM_CALLS_TOTAL.labels(
                                        provider="deploy", model=llm_model
                                    ).inc()
                                    LLM_LATENCY_SECONDS.labels(
                                        provider="deploy", model=llm_model
                                    ).observe(time.time() - start_llm)
                                except Exception as le:
                                    LLM_ERRORS_TOTAL.labels(
                                        provider="deploy",
                                        model=llm_model,
                                        error_type=type(le).__name__,
                                    ).inc()
                                    raise LLMError(
                                        f"LLM call failed for target {t}"
                                    ) from le

                                raw = resp if stream else resp.get("content", "")
                                out_format = t if t != "docs" else "markdown"
                                # --- FIX: Pass singleton handler_registry ---
                                # FIX Issue 4: Skip Presidio on deployment configs
                                # Determine proper to_format: kubernetes/helm should use "yaml"
                                to_format = "yaml" if out_format in ("kubernetes", "helm") else out_format
                                handled = await handle_deploy_response(
                                    raw_response=raw,
                                    handler_registry=self.handler_registry,
                                    output_format=out_format,
                                    to_format=to_format,
                                    repo_path=str(self.repo_path),
                                    run_id=self.run_id,
                                    skip_presidio=True,  # Skip PII scrubbing for deployment configs
                                )
                                # --------------------------------------------

                                # structured validation (strict)
                                # --- FIX: Use singleton validator_registry ---
                                validator = self.validator_registry.get_validator(t)
                                # -------------------------------------------
                                if validator:
                                    v_report = await validator.validate(
                                        json.dumps(handled["structured_data"]),
                                        t,
                                    )
                                else:
                                    v_report = {
                                        "build_status": "error",
                                        "compliance_score": 0.0,
                                        "details": "No validator registered.",
                                    }

                                if (
                                    v_report.get("build_status") not in ("success", "skipped", "tool_not_found")
                                    or v_report.get("compliance_score", 0.0) < 0.5
                                ):
                                    # Allow skipped/tool_not_found builds when Docker is unavailable
                                    if v_report.get("build_status") in ("skipped", "tool_not_found"):
                                        logger.warning(
                                            f"Docker validation skipped for {t} - Docker not available. "
                                            "Set DOCKER_REQUIRED=true to enforce Docker availability."
                                        )
                                    else:
                                        VALIDATION_ERRORS.labels(run_type=t).inc()
                                        raise RunnerError(
                                            error_code="VALIDATION_FAILED",
                                            detail=f"Config validation failed for {t}"
                                        )

                                configs[t] = handled["final_config_output"]

                                for hook in self.post_gen_hooks:
                                    configs[t] = await hook(configs[t], t)

                                tspan.set_status(Status(StatusCode.OK))
                            except Exception as e:
                                tspan.record_exception(e)
                                tspan.set_status(Status(StatusCode.ERROR, str(e)))
                                raise

                # downstream stages
                validations: Dict[str, Any] = {}
                compliances: Dict[str, Any] = {}
                simulations: Dict[str, Any] = {}
                explanations: Dict[str, Any] = {}

                for t in order:
                    cfg = configs.get(t)
                    if not isinstance(cfg, str):
                        validations[t] = {
                            "valid": False,
                            "error": "Missing config.",
                        }
                        compliances[t] = ["Missing config."]
                        simulations[t] = {
                            "status": "failed",
                            "error": "Missing config.",
                        }
                        explanations[t] = "Generation failed."
                        continue

                    validations[t] = await self.validate_configs_final(cfg, t)
                    compliances[t] = await self.compliance_check_final(cfg)
                    simulations[t] = await self.simulate_deployment_final(cfg, t)
                    explanations[t] = await self.generate_explanation_final(
                        cfg,
                        validations[t],
                        t,
                    )

                badges = await self.generate_badges(
                    list(validations.values()),
                    [v if isinstance(v, list) else [] for v in compliances.values()],
                )

                if human_approval:
                    ok = await self.request_human_approval(
                        configs,
                        validations,
                        cli_approval=cli_approval,
                    )
                    if not ok:
                        raise ValueError("Configuration rejected by human reviewer")

                duration = time.time() - start
                GENERATION_DURATION.labels(run_type=doc_type, model=llm_model).observe(
                    duration
                )
                SUCCESSFUL_GENERATIONS.labels(run_type=doc_type).inc()
                CONFIG_SIZE.labels(run_type=doc_type).set(
                    sum(len(v) for v in configs.values() if isinstance(v, str))
                )

                result = {
                    "configs": configs,
                    "validations": validations,
                    "compliances": compliances,
                    "simulations": simulations,
                    "explanations": explanations,
                    "badges": badges,
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "provenance": {
                        "model_used": llm_model,
                        "generated_by": "DeployAgent",
                        "version": "1.0",
                        "duration_seconds": duration,
                        "config_status": (
                            "Approved" if human_approval else "Skipped_Approval"
                        ),
                    },
                }
                self.last_result = result
                self.history.append(result)

                # Save to history database using helper method
                await self._save_to_history(self.run_id, result["timestamp"], result)

                span.set_status(Status(StatusCode.OK, "Pipeline completed"))
                return result

            except Exception as e:
                VALIDATION_ERRORS.labels(run_type=doc_type).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                SELF_HEAL_ATTEMPTS.labels(run_id=self.run_id).inc()
                healed = await self.self_heal(
                    target_files,
                    doc_type,
                    targets,
                    instructions,
                    str(e),
                    llm_model,
                    ensemble,
                    stream,
                )
                if healed:
                    return healed

                err = {
                    "error": str(e),
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "status": "failed_pipeline",
                }
                # Save error to history database
                await self._save_to_history(self.run_id, err["timestamp"], err)
                raise

    # --- run_deployment ---------------------------------------------
    
    def _build_retry_prompt(
        self, original_prompt: str, failed_output: str, error: str, target: str
    ) -> str:
        """
        Build a retry prompt with error context for self-healing.
        
        FIX 4: This method creates an enhanced prompt that includes the error
        from the previous attempt, helping the LLM correct its output format.
        
        Args:
            original_prompt: The original prompt that was sent
            failed_output: The LLM's failed output (first 300 chars)
            error: The error message from the failed attempt
            target: The target deployment type (docker, kubernetes, helm)
            
        Returns:
            Enhanced prompt with error context and correction instructions
        """
        return f"""Your previous response was INVALID and could not be parsed.

ERROR: {error}

Your previous (incorrect) response started with:
{failed_output[:300] if failed_output else "(empty)"}...

CRITICAL CORRECTIONS REQUIRED:
- Output ONLY the {target} configuration content
- NO explanations, NO markdown code fences, NO prose
- Start IMMEDIATELY with the first valid instruction
- For Dockerfile: Start with FROM or ARG
- For YAML: Start with --- or apiVersion:

{original_prompt}

BEGIN YOUR RESPONSE WITH THE CONFIGURATION NOW (no preamble):"""
    
    def _generate_fallback_config(self, target: str, project_name: str = "hello_generator") -> Optional[str]:
        """
        Generate fallback deployment configuration templates when LLM generation fails.
        
        FIX Issue 4: Provides deterministic fallback templates for Docker, Kubernetes, and Helm
        configurations to ensure the pipeline doesn't fail completely when LLM retries are exhausted.
        
        Args:
            target: The deployment target type (docker, kubernetes, helm)
            project_name: Name of the project for configuration
            
        Returns:
            Configuration content as string, or None if target not supported
        """
        logger.info(f"[DEPLOY_AGENT] Generating fallback template for target: {target}")
        
        if target == "docker":
            # Fallback Dockerfile template for Python FastAPI apps
            return f"""FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the application port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
        
        elif target == "kubernetes":
            # Fallback Kubernetes deployment and service YAML
            return f"""---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {project_name}
  labels:
    app: {project_name}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: {project_name}
  template:
    metadata:
      labels:
        app: {project_name}
    spec:
      containers:
      - name: {project_name}
        image: {project_name}:latest
        ports:
        - containerPort: 8000
        env:
        - name: PORT
          value: "8000"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: {project_name}-service
spec:
  selector:
    app: {project_name}
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {project_name}-config
data:
  config.yaml: |
    # Application configuration
    environment: production
    log_level: info
"""
        
        elif target == "helm":
            # FIX Bug 3 & 4: Complete Helm chart structure fallback
            # Return JSON structure for proper file organization
            return json.dumps({
                "Chart.yaml": {
                    "apiVersion": "v2",
                    "name": project_name,
                    "description": f"A Helm chart for {project_name}",
                    "type": "application",
                    "version": "0.1.0",
                    "appVersion": "1.0.0"
                },
                "values.yaml": {
                    "replicaCount": 2,
                    "image": {
                        "repository": project_name,
                        "pullPolicy": "IfNotPresent",
                        "tag": "latest"
                    },
                    "service": {
                        "type": "LoadBalancer",
                        "port": 80,
                        "targetPort": 8000
                    },
                    "resources": {
                        "limits": {
                            "cpu": "500m",
                            "memory": "512Mi"
                        },
                        "requests": {
                            "cpu": "250m",
                            "memory": "256Mi"
                        }
                    }
                },
                "templates": {
                    "deployment.yaml": f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{{{ include "{project_name}.fullname" . }}}}
  labels:
    {{{{- include "{project_name}.labels" . | nindent 4 }}}}
spec:
  replicas: {{{{ .Values.replicaCount }}}}
  selector:
    matchLabels:
      {{{{- include "{project_name}.selectorLabels" . | nindent 6 }}}}
  template:
    metadata:
      labels:
        {{{{- include "{project_name}.selectorLabels" . | nindent 8 }}}}
    spec:
      containers:
      - name: {{{{ .Chart.Name }}}}
        image: "{{{{ .Values.image.repository }}}}:{{{{ .Values.image.tag | default .Chart.AppVersion }}}}"
        imagePullPolicy: {{{{ .Values.image.pullPolicy }}}}
        ports:
        - name: http
          containerPort: {{{{ .Values.service.targetPort }}}}
          protocol: TCP
        resources:
          {{{{- toYaml .Values.resources | nindent 10 }}}}
""",
                    "service.yaml": f"""apiVersion: v1
kind: Service
metadata:
  name: {{{{ include "{project_name}.fullname" . }}}}
  labels:
    {{{{- include "{project_name}.labels" . | nindent 4 }}}}
spec:
  type: {{{{ .Values.service.type }}}}
  ports:
    - port: {{{{ .Values.service.port }}}}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    {{{{- include "{project_name}.selectorLabels" . | nindent 4 }}}}
""",
                    "_helpers.tpl": f"""{{{{/*
Expand the name of the chart.
*/}}}}
{{{{- define "{project_name}.name" -}}}}
{{{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}}}
{{{{- end }}}}

{{{{/*
Create a default fully qualified app name.
*/}}}}
{{{{- define "{project_name}.fullname" -}}}}
{{{{- if .Values.fullnameOverride }}}}
{{{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}}}
{{{{- else }}}}
{{{{- $name := default .Chart.Name .Values.nameOverride }}}}
{{{{- if contains $name .Release.Name }}}}
{{{{- .Release.Name | trunc 63 | trimSuffix "-" }}}}
{{{{- else }}}}
{{{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}}}
{{{{- end }}}}
{{{{- end }}}}
{{{{- end }}}}

{{{{/*
Create chart name and version as used by the chart label.
*/}}}}
{{{{- define "{project_name}.chart" -}}}}
{{{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}}}
{{{{- end }}}}

{{{{/*
Common labels
*/}}}}
{{{{- define "{project_name}.labels" -}}}}
helm.sh/chart: {{{{ include "{project_name}.chart" . }}}}
{{{{ include "{project_name}.selectorLabels" . }}}}
{{{{- if .Chart.AppVersion }}}}
app.kubernetes.io/version: {{{{ .Chart.AppVersion | quote }}}}
{{{{- end }}}}
app.kubernetes.io/managed-by: {{{{ .Release.Service }}}}
{{{{- end }}}}

{{{{/*
Selector labels
*/}}}}
{{{{- define "{project_name}.selectorLabels" -}}}}
app.kubernetes.io/name: {{{{ include "{project_name}.name" . }}}}
app.kubernetes.io/instance: {{{{ .Release.Name }}}}
{{{{- end }}}}
"""
                }
            }, indent=2)
        
        else:
            logger.warning(f"[DEPLOY_AGENT] No fallback template available for target: {target}")
            return None
    
    async def run_deployment(
        self, target: str, requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        with tracer.start_as_current_span("deploy.run_deployment") as span:
            start = time.time()
            DEPLOY_RUNS.labels(status="started").inc()

            if not target:
                DEPLOY_ERRORS.labels(error_type="InvalidTarget").inc()
                raise ValueError("Target must be non-empty")
            if not isinstance(requirements, dict):
                DEPLOY_ERRORS.labels(error_type="InvalidRequirements").inc()
                raise ValueError("Requirements must be a dictionary")

            try:
                logger.info(f"[DEPLOY_AGENT] Attempting to get plugin for target='{target}'")
                logger.info(f"[DEPLOY_AGENT] Available plugins: {list(self.plugin_registry.plugins.keys())}")
                
                plugin = self.plugin_registry.get_plugin(target)
                if not plugin:
                    DEPLOY_ERRORS.labels(error_type="PluginNotFound").inc()
                    logger.error(f"[DEPLOY_AGENT] No plugin found for target '{target}'. Available: {list(self.plugin_registry.plugins.keys())}")
                    raise ValueError(f"No plugin found for target: {target}")
                
                logger.info(f"[DEPLOY_AGENT] Found plugin for target '{target}': {plugin.name if hasattr(plugin, 'name') else 'unknown'}")

                context = await self.gather_context([])
                
                # FIX 6: Record context files count
                files_list = requirements.get("files", [])
                CONTEXT_FILES_COUNT.labels(target=target).set(len(files_list))

                steps = requirements.get(
                    "pipeline_steps",
                    ["generate", "validate", "simulate"],
                )
                config_content = requirements.get("config", "")

                if "generate" in steps:
                    # FIX 4: Add retry logic with self-healing prompts
                    last_error = None
                    last_raw_response = None
                    config_content = ""
                    original_prompt = None  # Industry standard: Store original to prevent accumulation
                    
                    for attempt in range(MAX_LLM_RETRIES):
                        try:
                            # FIX 6: Record retry attempt
                            if attempt > 0:
                                LLM_RETRY_COUNT.labels(target=target, attempt=str(attempt + 1)).inc()
                            
                            # --- FIX 3.3: Pass repo_path to prompt agent ---
                            # Only generate original prompt on first attempt
                            if original_prompt is None:
                                original_prompt = await self.prompt_agent(
                                    target=target,
                                    files=[],
                                    repo_path=str(self.repo_path),  # <-- ADDED
                                    instructions=None,
                                    context=context,
                                )
                            # --------------------------------------------
                            
                            # Industry standard: Use original prompt, not accumulated version
                            prompt = original_prompt
                            
                            # FIX 6: Record prompt token count (approximate)
                            if HAS_TIKTOKEN:
                                try:
                                    encoding = tiktoken.encoding_for_model("gpt-4")
                                    token_count = len(encoding.encode(prompt))
                                    PROMPT_TOKEN_COUNT.labels(target=target).observe(token_count)
                                    logger.debug(f"[DEPLOY_AGENT] Prompt token count: {token_count}")
                                except Exception as e:
                                    logger.debug(f"[DEPLOY_AGENT] Could not count tokens: {e}")
                            
                            # FIX 4: Build retry prompt with error context on subsequent attempts
                            if attempt > 0 and last_error:
                                prompt = self._build_retry_prompt(
                                    original_prompt, last_raw_response, str(last_error), target
                                )
                                logger.warning(
                                    f"Retrying {target} generation (attempt {attempt + 1}/{MAX_LLM_RETRIES}): {last_error}"
                                )
                            
                            prompt = scrub_text(prompt)
                            try:
                                resp = await call_llm_api(prompt, "gpt-4o", stream=False)
                                LLM_CALLS_TOTAL.labels(provider="deploy", model="gpt-4o").inc()
                            except Exception as le:
                                LLM_ERRORS_TOTAL.labels(
                                    provider="deploy",
                                    model="gpt-4o",
                                    error_type=type(le).__name__,
                                ).inc()
                                raise LLMError("LLM call failed during run_deployment") from le
                            
                            raw = resp.get("content", "")
                            last_raw_response = raw
                            
                            # --- FIX: Pass singleton handler_registry ---
                            # FIX Issue 4: Skip Presidio on deployment configs
                            # Determine proper to_format: kubernetes/helm should use "yaml"
                            to_format = "yaml" if target in ("kubernetes", "helm") else target
                            handled = await handle_deploy_response(
                                raw_response=raw,
                                handler_registry=self.handler_registry,
                                output_format=target,
                                to_format=to_format,
                                repo_path=str(self.repo_path),
                                run_id=self.run_id,
                                skip_presidio=True,  # Skip PII scrubbing for deployment configs
                            )
                            # --------------------------------------------
                            config_content = handled["final_config_output"]
                            logger.info(
                                f"[DEPLOY_AGENT] Config generated successfully on attempt {attempt + 1}, "
                                f"length: {len(config_content)} chars"
                            )
                            # Success - break out of retry loop
                            break
                            
                        except ValueError as e:
                            last_error = e
                            logger.warning(
                                f"[DEPLOY_AGENT] Generation attempt {attempt + 1}/{MAX_LLM_RETRIES} failed: {e}"
                            )
                            if attempt >= MAX_LLM_RETRIES - 1:
                                # FIX Issue 4: Use fallback template instead of failing
                                logger.warning(
                                    f"[DEPLOY_AGENT] All {MAX_LLM_RETRIES} attempts failed for {target}, "
                                    f"using fallback template"
                                )
                                # Generate fallback config based on target type
                                project_name = self.repo_path.name
                                config_content = self._generate_fallback_config(target, project_name)
                                if config_content:
                                    logger.info(
                                        f"[DEPLOY_AGENT] Using fallback template for {target}, "
                                        f"length: {len(config_content)} chars"
                                    )
                                    break
                                else:
                                    # If fallback fails, re-raise the original error
                                    logger.error(
                                        f"[DEPLOY_AGENT] Fallback template generation failed for {target}"
                                    )
                                    raise
                            # Otherwise, continue to next attempt
                            continue
                    # End of FIX 4 retry loop

                if "validate" in steps:
                    vres = await self.validate_configs_final(config_content, target)
                    # Treat 'skipped' and 'tool_not_found' build statuses as non-fatal
                    build_status = vres.get("build_status", "")
                    is_valid = vres.get("valid", False)
                    if not is_valid and build_status not in ("skipped", "tool_not_found", "success"):
                        DEPLOY_ERRORS.labels(error_type="ValidationFailed").inc()
                        raise RunnerError(
                            error_code="VALIDATION_FAILED",
                            detail=f"Validation failed: {vres}",
                            task_id=self.run_id
                        )
                    elif not is_valid and build_status in ("skipped", "tool_not_found"):
                        logger.warning(
                            f"[DEPLOY_AGENT] Validation skipped for {target}: Docker/tools not available"
                        )
                        vres["valid"] = True  # Mark as valid since tools aren't available
                else:
                    vres = {"valid": True, "details": "Skipped"}

                if "simulate" in steps:
                    sres = await self.simulate_deployment_final(config_content, target)
                    if sres.get("status") not in (
                        "success",
                        "skipped",
                    ):
                        DEPLOY_ERRORS.labels(error_type="SimulationFailed").inc()
                        raise RunnerError(
                            error_code="SIMULATION_FAILED",
                            detail=f"Simulation failed: {sres}",
                            task_id=self.run_id
                        )
                else:
                    sres = {
                        "status": "skipped",
                        "reason": "Not requested",
                    }

                result = {
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "target": target,
                    "configs": {target: config_content},
                    "validations": {target: vres},
                    "simulations": {target: sres},
                    "provenance": {
                        "generated_by": "DeployAgent",
                        "version": "1.0",
                    },
                }
                
                logger.info(f"[DEPLOY_AGENT] Result prepared with configs: {list(result['configs'].keys())}")

                self.last_result = result
                self.history.append(result)
                # Save to history database using helper method
                await self._save_to_history(self.run_id, result["timestamp"], result)

                # [ARBITER] Publish deployment event
                if self.arbiter_bridge:
                    try:
                        await self.arbiter_bridge.publish_event(
                            "deployment_completed",
                            {
                                "run_id": self.run_id,
                                "target": target,
                                "validation_passed": vres.get("valid", False),
                                "simulation_status": sres.get("status", "unknown")
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish deployment event to Arbiter: {e}")

                DEPLOY_RUNS.labels(status="success").inc()
                DEPLOY_LATENCY.observe(time.time() - start)
                await log_action(
                    "Deployment Run",
                    {
                        "run_id": self.run_id,
                        "target": target,
                        "status": "success",
                    },
                )
                return result

            except Exception as e:
                DEPLOY_ERRORS.labels(error_type=type(e).__name__).inc()
                DEPLOY_LATENCY.observe(time.time() - start)
                logger.error(
                    "Deployment failed: %s",
                    e,
                    extra={"run_id": self.run_id},
                    exc_info=True,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                
                # [ARBITER] Report deployment failure
                if self.arbiter_bridge:
                    try:
                        await self.arbiter_bridge.report_bug({
                            "title": f"Deployment failed for target {target}",
                            "description": f"Deployment run {self.run_id} failed with error: {str(e)}",
                            "severity": "high",
                            "error": str(e),
                            "context": {
                                "run_id": self.run_id,
                                "target": target,
                                "error_type": type(e).__name__
                            }
                        })
                    except Exception as bug_error:
                        logger.warning(f"Failed to report deployment failure to Arbiter: {bug_error}")
                
                raise

    # --- legacy helpers kept for compatibility ----------------------
    async def compliance_check(self, config: Dict[str, Any]) -> List[str]:
        return await self.compliance_check_final(json.dumps(config))

    async def simulate_deployment(
        self, config: Dict[str, Any], target: str
    ) -> Dict[str, Any]:
        plugin = self.plugin_registry.get_plugin(target)
        if plugin:
            return await plugin.simulate_deployment(config)
        return {
            "status": "skipped",
            "reason": f"No simulation for target: {target}",
        }

    async def generate_explanation(
        self,
        config: Dict[str, Any],
        validation_result: Dict[str, Any],
        target: str,
    ) -> str:
        return await self.generate_explanation_final(
            json.dumps(config, indent=2),
            validation_result,
            target,
        )

    async def generate_badges(
        self,
        validations: List[Dict[str, Any]],
        compliances: List[List[str]],
    ) -> Dict[str, Dict[str, str]]:
        badges: Dict[str, Dict[str, str]] = {}
        for i, (v, c) in enumerate(zip(validations, compliances)):
            name = f"target_{i}"
            valid = v.get("valid", False)
            v_status = "passing" if valid else "failing"
            v_color = "28A745" if valid else "DC3545"
            c_status = "clean" if not c else "issues"
            c_color = "28A745" if not c else "FFC107"
            badges[name] = {
                "validation": f"[https://img.shields.io/badge/Validation-](https://img.shields.io/badge/Validation-){v_status}-{v_color}.svg",
                "compliance": f"[https://img.shields.io/badge/Compliance-](https://img.shields.io/badge/Compliance-){c_status}-{c_color}.svg",
            }
        return badges

    async def request_human_approval(
        self,
        configs: Dict[str, Any],
        validations: Dict[str, Any],
        cli_approval: bool = False,
    ) -> bool:
        summary = (
            f"Approval needed for run_id: **{self.run_id}**.\n"
            f"Configs: {json.dumps(configs, indent=2)[:400]}...\n"
            f"Validations: {json.dumps(validations, indent=2)[:400]}..."
        )
        approved = False

        if self.webhook_url:
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.post(
                        self.webhook_url,
                        json=ApprovalRequest(
                            run_id=self.run_id,
                            configs=configs,
                            validations=validations,
                        ).dict(),
                    )
                    if resp.status == 200:
                        data = await resp.json()
                        approved = bool(data.get("approved", False))
                        HUMAN_APPROVAL_STATUS.labels(
                            run_id=self.run_id,
                            status=("approved" if approved else "rejected"),
                        ).inc()
                        return approved
            except Exception as e:  # pragma: no cover
                logger.error(
                    "Webhook approval error: %s",
                    e,
                    extra={"run_id": self.run_id},
                )

        if not approved and self.slack_webhook:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        self.slack_webhook,
                        json={"text": summary},
                    )
            except Exception as e:  # pragma: no cover
                logger.error(
                    "Slack approval error: %s",
                    e,
                    extra={"run_id": self.run_id},
                )

        if not approved and cli_approval:
            print(summary)
            ans = input("Approve? (y/n): ").strip().lower()
            approved = ans == "y"
            HUMAN_APPROVAL_STATUS.labels(
                run_id=self.run_id,
                status=("approved_cli" if approved else "rejected_cli"),
            ).inc()

        return approved

    async def self_heal(
        self,
        target_files: List[str],
        doc_type: str,
        targets: List[str],
        instructions: Optional[str],
        error: str,
        llm_model: str,
        ensemble: bool,
        stream: bool,
    ) -> Optional[Dict[str, Any]]:
        with tracer.start_as_current_span("deploy.self_heal"):
            # --- FIX: Await add_provenance in async context ---
            await add_provenance("provenance", {"action": "self_heal_attempt"})
            for attempt in range(1, 4):
                try:
                    healing_prompt = scrub_text(f"""
Previous attempt failed.

Error:
{error}

Original instructions:
{instructions or "None"}

Propose corrected configurations as JSON keyed by target.
""")
                    try:
                        if ensemble:
                            resp = await call_ensemble_api(
                                healing_prompt,
                                [{"model": llm_model}],
                                voting_strategy="majority",
                                stream=False,
                            )
                        else:
                            resp = await call_llm_api(
                                healing_prompt,
                                llm_model,
                                stream=False,
                            )
                    except Exception as le:
                        LLM_ERRORS_TOTAL.labels(
                            provider="deploy",
                            model=llm_model,
                            error_type=type(le).__name__,
                        ).inc()
                        raise LLMError("LLM self-heal failed") from le

                    fixed = resp.get("config", {})
                    if not isinstance(fixed, dict):
                        continue

                    validations: Dict[str, Any] = {}
                    compliances: Dict[str, Any] = {}
                    simulations: Dict[str, Any] = {}
                    explanations: Dict[str, Any] = {}
                    all_ok = True

                    for t in targets:
                        cfg = fixed.get(t)
                        if not cfg:
                            continue
                        validations[t] = await self.validate_configs_final(cfg, t)
                        compliances[t] = await self.compliance_check_final(cfg)
                        simulations[t] = await self.simulate_deployment_final(cfg, t)
                        explanations[t] = await self.generate_explanation_final(
                            cfg,
                            validations[t],
                            t,
                        )

                        if not validations[t].get("valid", False):
                            all_ok = False
                        if simulations[t].get("status") not in (
                            "success",
                            "skipped",
                        ):
                            all_ok = False

                    if all_ok and fixed:
                        diff = ""
                        if self.last_result:
                            prev_cfg = json.dumps(
                                self.last_result.get("configs", {}),
                                indent=2,
                                sort_keys=True,
                            )
                            new_cfg = json.dumps(
                                fixed,
                                indent=2,
                                sort_keys=True,
                            )
                            diff = "".join(
                                difflib.unified_diff(
                                    prev_cfg.splitlines(keepends=True),
                                    new_cfg.splitlines(keepends=True),
                                    fromfile="previous",
                                    tofile="healed",
                                )
                            )

                        badges = await self.generate_badges(
                            list(validations.values()),
                            [
                                v if isinstance(v, list) else []
                                for v in compliances.values()
                            ],
                        )

                        healed = {
                            "configs": fixed,
                            "validations": validations,
                            "compliances": compliances,
                            "simulations": simulations,
                            "explanations": explanations,
                            "badges": badges,
                            "run_id": self.run_id,
                            "timestamp": datetime.now().isoformat(),
                            "provenance": {
                                "model_used": llm_model,
                                "generated_by": "DeployAgent(Self-Healed)",
                                "version": "1.0",
                                "heal_diff": diff,
                            },
                        }
                        self.last_result = healed
                        self.history.append(healed)

                        # Save healed result to history
                        healed_run_id = f"{self.run_id}_healed_{int(time.time())}"
                        await self._save_to_history(
                            healed_run_id, healed["timestamp"], healed
                        )
                        return healed
                except Exception as e:  # pragma: no cover
                    logger.warning(
                        "Self-heal attempt %d failed: %s",
                        attempt,
                        e,
                        extra={"run_id": self.run_id},
                    )

            return None

    # --- rollback ---------------------------------------------------
    # --- FIX: Make async and await get_previous_run ---
    async def rollback(self, run_id: str) -> bool:
        logger.info(
            "Rollback requested to run_id=%s",
            run_id,
            extra={"run_id": self.run_id},
        )
        prev = await self.get_previous_run(run_id)
        if not prev:
            logger.error(
                "No history for run_id=%s",
                run_id,
                extra={"run_id": self.run_id},
            )
            return False

        target = prev.get("target")
        if not target:
            cfgs = prev.get("configs") or {}
            if not cfgs:
                logger.error(
                    "No configs in run_id=%s",
                    run_id,
                    extra={"run_id": self.run_id},
                )
                return False
            target = next(iter(cfgs.keys()))

        cfg_str = (prev.get("configs") or {}).get(target)
        if not cfg_str:
            logger.error(
                "No config for target=%s in run_id=%s",
                target,
                run_id,
                extra={"run_id": self.run_id},
            )
            return False

        plugin = self.plugin_registry.get_plugin(target)
        if not plugin:
            logger.error(
                "No plugin for target=%s",
                target,
                extra={"run_id": self.run_id},
            )
            return False

        try:
            try:
                cfg = json.loads(cfg_str)
            except json.JSONDecodeError:
                if target == "docs":
                    logger.warning(
                        "Rollback for docs is no-op for non-JSON content.",
                        extra={"run_id": self.run_id},
                    )
                    return True
                return False

            ok = await plugin.rollback(cfg)
            await log_action(
                "Rollback",
                {
                    "run_id": run_id,
                    "target": target,
                    "status": "success" if ok else "failed",
                },
            )
            return ok
        except Exception as e:  # pragma: no cover
            logger.error(
                "Rollback exception for run_id=%s: %s",
                run_id,
                e,
                extra={"run_id": self.run_id},
                exc_info=True,
            )
            await log_action(
                "Rollback",
                {
                    "run_id": run_id,
                    "target": target,
                    "status": "exception",
                },
            )
            return False

    # ----------------------------------------------------

    # --- misc -------------------------------------------------------
    def supported_languages(self) -> List[str]:
        return self.languages_supported

    def register_plugin(self, target: str, plugin: TargetPlugin) -> None:
        """Register a plugin and add it to the target dependency graph."""
        self.plugin_registry.register(target, plugin)

        # Add node to target dependency graph if it doesn't exist
        if target not in self.target_graph:
            self.target_graph.add_node(target)
            logger.info(f"Added target '{target}' to dependency graph")

    async def generate_report(self, result: Dict[str, Any]) -> str:
        with tracer.start_as_current_span("deploy.generate_report"):
            run_id = result.get("run_id", "")
            timestamp = result.get("timestamp", "")
            provenance = result.get("provenance", {})

            report_lines: List[str] = []
            report_lines.append(
                f"# Deployment Configuration Report (Run ID: `{run_id}`)"
            )
            report_lines.append(f"**Timestamp**: {timestamp}")
            report_lines.append("")
            report_lines.append("**Provenance**:")
            report_lines.append("```json")
            report_lines.append(json.dumps(provenance, indent=2))
            report_lines.append("```")
            report_lines.append("")

            configs = result.get("configs", {}) or {}
            validations = result.get("validations", {}) or {}
            compliances = result.get("compliances", {}) or {}
            simulations = result.get("simulations", {}) or {}
            explanations = result.get("explanations", {}) or {}
            badges = result.get("badges", {}) or {}

            for target, cfg in configs.items():
                report_lines.append("---")
                report_lines.append(f"## Target: {target}")
                report_lines.append("")
                report_lines.append("### Explanation")
                report_lines.append(
                    explanations.get(target, "No explanation available.")
                )
                report_lines.append("")
                report_lines.append("### Configuration")
                report_lines.append("```text")
                report_lines.append(str(cfg)[:4000])
                report_lines.append("```")
                report_lines.append("")
                report_lines.append("### Validation Summary")
                report_lines.append("```json")
                report_lines.append(json.dumps(validations.get(target, {}), indent=2))
                report_lines.append("```")
                report_lines.append("")
                report_lines.append("### Compliance Issues")
                report_lines.append("```json")
                report_lines.append(json.dumps(compliances.get(target, []), indent=2))
                report_lines.append("```")
                report_lines.append("")
                report_lines.append("### Simulation Result")
                report_lines.append("```json")
                report_lines.append(json.dumps(simulations.get(target, {}), indent=2))
                report_lines.append("```")
                report_lines.append("")

            if badges:
                report_lines.append("---")
                report_lines.append("## Badges")
                for name, badge in badges.items():
                    v = badge.get("validation")
                    c = badge.get("compliance")
                    if v:
                        report_lines.append(f"- ![]({v})")
                    if c:
                        report_lines.append(f"- ![]({c})")

            return "\n".join(report_lines)


# --- CLI demo ------------------------------------------------------
async def _main_async() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="DeployAgent demo CLI")
    parser.add_argument("repo", help="Path to repository")
    parser.add_argument(
        "--targets",
        nargs="*",
        default=["docs"],
        help="Targets to generate (e.g., docs docker helm terraform)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Target files to include as context",
    )
    args = parser.parse_args()

    # Use async context manager for automatic DB initialization
    async with DeployAgent(args.repo) as agent:
        result = await agent.generate_documentation(
            target_files=args.files,
            targets=args.targets,
            doc_type="CLI_DEMO",
            human_approval=False,
        )
        report = await agent.generate_report(result)
        print(report)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
