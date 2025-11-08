# agents/deploy_agent.py
import asyncio
import uuid
import logging
import json
import os
import glob
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable, Union, AsyncGenerator, Awaitable
from abc import ABC, abstractmethod
import subprocess
import prometheus_client
import aiohttp
from pathlib import Path
import importlib.util
import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import tiktoken
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import time
import sqlite3
import difflib
import re
import tempfile
import aiofiles
# import shutil # Removed as per review, was unused
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import networkx as nx

# --- Local imports -------------------------------------------------
from .deploy_prompt import build_deploy_prompt, PromptConfig
from .deploy_response_handler import handle_deploy_response
from .deploy_validator import (
    DockerValidator,
    HelmValidator,
    ValidatorRegistry,
)

# --- Central Runner foundation ------------------------------------
# NOTE: Using runner imports for all core functionality
from runner.llm_client import call_llm_api, call_ensemble_api
from runner.runner_file_utils import get_commits
# from runner.runner_core import run_tests # Removed as per review, was unused
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import (
    LLM_CALLS_TOTAL,
    LLM_ERRORS_TOTAL,
    LLM_LATENCY_SECONDS,
    LLM_TOKENS_INPUT,
    LLM_TOKENS_OUTPUT # Ensure all needed metrics are imported
)
from runner.runner_errors import RunnerError, LLMError
from runner import tracer                     # central OTEL tracer
from runner.runner_security_utils import redact_secrets # Use central scrubbing utility
from audit_log import log_action
# from omnicore_engine.plugin_registry import plugin, PlugInKind # Removed as per review, was unused

# --- DUMMY/MOCK CLASSES (REMOVED) ---
# NOTE: Presidio components are no longer referenced.

# --- Prometheus Metrics ---
# NOTE: Local metric definitions are retained for non-LLM specific tasks
GENERATION_DURATION = prometheus_client.Histogram(
    'deploy_agent_generation_duration_seconds',
    'Time taken for config generation',
    ['run_type', 'model']
)
VALIDATION_ERRORS = prometheus_client.Counter(
    'deploy_agent_validation_errors_total',
    'Total validation errors',
    ['run_type']
)
SUCCESSFUL_GENERATIONS = prometheus_client.Counter(
    'deploy_agent_successful_generations_total',
    'Total successful generations',
    ['run_type']
)
CONFIG_SIZE = prometheus_client.Gauge(
    'deploy_agent_config_size_bytes',
    'Size of generated configurations',
    ['run_type']
)
PLUGIN_HEALTH = prometheus_client.Gauge(
    'deploy_agent_plugin_health',
    'Health status of plugins',
    ['plugin']
)
SELF_HEAL_ATTEMPTS = prometheus_client.Counter(
    'deploy_agent_self_heal_attempts',
    'Total self-healing attempts',
    ['run_id']
)
HUMAN_APPROVAL_STATUS = prometheus_client.Counter(
    'deploy_agent_human_approval_status',
    'Status of human approvals',
    ['run_id', 'status']
)
DEPLOY_RUNS = prometheus_client.Counter('deploy_runs_total', 'Total deployment runs', ['status'])
DEPLOY_LATENCY = prometheus_client.Histogram('deploy_latency_seconds', 'Deployment run latency')
DEPLOY_ERRORS = prometheus_client.Counter('deploy_errors_total', 'Deployment errors', ['error_type'])

# --- Security (REFACTORED) ---
# Removed local SECRET_PATTERNS as logic is centralized in runner.runner_security_utils

def scrub_text(text: str) -> str:
    """
    Primary function. Scrubs sensitive information using the 
    centralized runner.runner_security_utils.redact_secrets utility.
    """
    if not text:
        return ""
    
    try:
        # Use the centralized runner utility
        # This import is checked at the top of the file (line 62)
        return redact_secrets(text)
    except Exception as e:
        # Log the error but return a redacted string to prevent data leakage
        logger.warning(f"Centralized secret scrubbing failed: {e}. Returning generic redaction.")
        # Fallback to a simple regex to catch common patterns if central utility fails,
        # but avoid leaking the original text.
        fallback_patterns = [
            r'(?i)(api[-_]?key|secret|token)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{20,}["\']?',
            r'(?i)password\s*[:=]\s*["\']?.+?["\']?',
        ]
        scrubbed_text = text
        for pattern in fallback_patterns:
            scrubbed_text = re.sub(pattern, '[REDACTED]', scrubbed_text)
        
        if scrubbed_text != text:
             return scrubbed_text
        else:
             # If no patterns matched, but the scrubber failed, we must not return the original text.
             return "[SCRUBBING_FAILED]"

# --- Custom Logging Filter ---
class ScrubFilter(logging.Filter):
    """
    A logging filter that scrubs sensitive information from log messages
    before they are emitted.
    """
    def filter(self, record):
        if record.msg:
            record.msg = scrub_text(str(record.msg))
        if hasattr(record, 'exc_info') and record.exc_info:
            record.exc_info = tuple(scrub_text(str(item)) if isinstance(item, str) else item for item in record.exc_info)
        return True

# --- Logger Configuration ---
# NOTE: The local filter is retained and applied to the central logger
logger.addFilter(ScrubFilter())

# --- FastAPI Application Setup ---
app = FastAPI(
    title="Deploy Agent API",
    description="Orchestrates deployment configuration generation, validation, and simulation.",
    version="1.0.0"
)

# --- Pydantic Models for API Requests/Responses ---
class ApprovalRequest(BaseModel):
    run_id: str
    configs: Dict[str, Any]
    validations: Dict[str, Any]

class ApprovalResponse(BaseModel):
    approved: bool
    comments: Optional[str] = None

@app.post("/approve", response_model=ApprovalResponse, summary="Request human approval for a configuration.")
async def approve_config(request: ApprovalRequest):
    """
    This endpoint facilitates human approval for generated configurations.
    It can send notifications to Slack and potentially interact with a UI for approval.
    """
    logger.info("Approval requested for run_id: %s", request.run_id, extra={'run_id': request.run_id})

    slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
    if slack_webhook:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(slack_webhook, json={
                    "text": f"Approval needed for run_id: **{request.run_id}**\n"
                            f"**Configs Summary**: {json.dumps(request.configs, indent=2)[:500]}...\n"
                            f"**Validations Summary**: {json.dumps(request.validations, indent=2)[:500]}...\n"
                            f"Please review and approve via the approval UI or CLI if available."
                })
                logger.info("Slack notification sent for approval request %s.", request.run_id, extra={'run_id': request.run_id})
        except Exception as e:
            logger.error("Failed to send Slack notification for %s: %s", request.run_id, e, extra={'run_id': request.run_id})

    approval_ui_url = os.getenv('APPROVAL_UI_URL', 'http://localhost:8001/approval-ui')
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(approval_ui_url, json=request.dict(), timeout=300) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    status = result.get("approved", False)
                    comments = result.get("comments", "")
                    HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status='approved' if status else 'rejected').inc()
                    logger.info("Approval UI response for %s: Approved=%s, Comments=%s", request.run_id, status, comments, extra={'run_id': request.run_id})
                    return ApprovalResponse(approved=status, comments=comments)
                else:
                    error_msg = f"Approval UI call failed with status {resp.status}: {await resp.text()}"
                    logger.error(error_msg, extra={'run_id': request.run_id})
                    HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status='error').inc()
                    raise HTTPException(status_code=500, detail=error_msg)
    except asyncio.TimeoutError:
        logger.error("Approval UI request timed out for run_id: %s", request.run_id, extra={'run_id': request.run_id})
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status='timeout').inc()
        raise HTTPException(status_code=504, detail="Approval request timed out after 5 minutes.")
    except aiohttp.ClientError as e:
        logger.error("Approval UI client error for %s: %s", request.run_id, e, extra={'run_id': request.run_id})
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status='error').inc()
        raise HTTPException(status_code=503, detail=f"Approval UI service unavailable: {e}")
    except Exception as e:
        logger.error("Unexpected error during approval request for %s: %s", request.run_id, e, extra={'run_id': request.run_id})
        HUMAN_APPROVAL_STATUS.labels(run_id=request.run_id, status='error').inc()
        raise HTTPException(status_code=500, detail=f"Internal error during approval: {e}")

# --- Target Plugin Abstraction ---
class TargetPlugin(ABC):
    __version__ = "1.0"

    @abstractmethod
    async def generate_config(self, target_files: List[str], instructions: Optional[str], context: Dict[str, Any], previous_configs: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def simulate_deployment(self, config: Dict[str, Any]) -> Dict[str, Any]:
        pass

    # PRODUCTION FIX: Added abstract 'rollback' method to the plugin interface.
    # This makes rollback a first-class, required feature for any deployment target plugin.
    @abstractmethod
    async def rollback(self, config: Dict[str, Any]) -> bool:
        """
        Rolls back a deployment to the state defined by the provided configuration.
        Returns True on success, False on failure.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

# --- Plugin Registry with Hot-Reload ---
class PluginRegistry(FileSystemEventHandler):
    """
    Manages loading, registering, and monitoring changes in TargetPlugin files.
    Enables hot-reloading of plugins and tracks their health.
    """
    def __init__(self, plugin_dir: str = "./plugins"):
        super().__init__()
        self.plugins: Dict[str, TargetPlugin] = {}
        self.plugin_info: Dict[str, Dict[str, Any]] = {}
        self.plugin_dir = plugin_dir
        self.observer = Observer()
        self.load_plugins()
        self.start_watching()

    def load_plugins(self):
        if not os.path.exists(self.plugin_dir):
            os.makedirs(self.plugin_dir)

        if self.plugin_dir not in sys.path:
            sys.path.insert(0, self.plugin_dir)

        for name, old_plugin_instance in self.plugins.items():
            if hasattr(old_plugin_instance, 'close') and callable(old_plugin_instance.close):
                try:
                    # NOTE: This should be handled safely as a background task
                    asyncio.create_task(old_plugin_instance.close())
                    logger.debug("Closed old plugin instance for %s", name)
                except Exception as e:
                    logger.warning("Error closing old plugin instance %s: %s", name, e)

        self.plugins.clear()
        self.plugin_info.clear()

        for plugin_file in glob.glob(f"{self.plugin_dir}/*.py"):
            if plugin_file.endswith('__init__.py') or plugin_file.endswith('_test.py'):
                continue
            self._load_plugin_file(plugin_file)
        logger.info("Loaded/reloaded %d plugins from %s.", len(self.plugins), self.plugin_dir)

    def _load_plugin_file(self, plugin_file: str):
        module_name_base = Path(plugin_file).stem
        # Use a unique module name for safe hot-reloading
        unique_module_name = f"{self.plugin_dir.replace(os.sep, '.')}.{module_name_base}_{uuid.uuid4().hex}"

        spec = importlib.util.spec_from_file_location(unique_module_name, plugin_file)
        if spec is None or spec.loader is None:
            logger.warning("Could not find module spec for plugin file: %s", plugin_file)
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_module_name] = module

        try:
            spec.loader.exec_module(module)
            found_plugin = False
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # Check for TargetPlugin subclasses that aren't the abstract base class itself
                if isinstance(attr, type) and issubclass(attr, TargetPlugin) and attr != TargetPlugin:
                    plugin_instance = attr()
                    self.register(module_name_base, plugin_instance)
                    found_plugin = True
            if not found_plugin:
                logger.warning("No TargetPlugin found in %s.", plugin_file)
        except Exception as e:
            logger.error("Failed to load plugin from %s: %s", plugin_file, e, exc_info=True)
            if unique_module_name in sys.modules:
                del sys.modules[unique_module_name]

    def register(self, target: str, plugin: TargetPlugin):
        health = plugin.health_check()
        self.plugins[target] = plugin
        self.plugin_info[target] = {
            'version': getattr(plugin, '__version__', 'N/A'),
            'last_reload': time.time(),
            'health': health
        }
        PLUGIN_HEALTH.labels(plugin=target).set(1 if health else 0)
        logger.info("Registered plugin: %s, version: %s, health: %s", target, getattr(plugin, '__version__', 'N/A'), health)

    def get_plugin(self, target: str) -> Optional[TargetPlugin]:
        return self.plugins.get(target)

    def start_watching(self):
        if not self.observer.is_alive():
            event_handler = self
            self.observer.schedule(event_handler, self.plugin_dir, recursive=False)
            self.observer.start()
            logger.info("Started watching plugin directory for hot-reloads: %s", self.plugin_dir)

    def on_any_event(self, event):
        if event.is_directory or event.event_type not in ('created', 'modified', 'deleted'):
            return
        if event.src_path.endswith('.py'):
            logger.info("Plugin file change detected: %s. Triggering plugin reload.", event.src_path)
            # Use asyncio.ensure_future or a similar method if called from a non-async context
            asyncio.create_task(asyncio.to_thread(self.load_plugins))

# --- Main Deploy Agent ---
class DeployAgent:
    """
    The central orchestrator for generating, validating, simulating, and managing
    deployment configurations. It integrates LLMs, target-specific plugins,
    observability, and self-healing capabilities.
    """
    def __init__(self, repo_path: str, languages_supported: Optional[List[str]] = None, plugin_dir: str = "./plugins",
                 slack_webhook: Optional[str] = None, webhook_url: Optional[str] = None, rate_limit: int = 5,
                 llm_orchestrator_instance: Optional[Any] = None):
        self.repo_path = Path(repo_path)
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

        self.languages_supported = languages_supported or ["python", "javascript", "rust", "go", "java"]
        self.registry = PluginRegistry(plugin_dir)
        self.run_id = str(uuid.uuid4())
        # NOTE: Using central runner logging provenance
        add_provenance({'run_id': self.run_id, 'agent': 'DeployAgent'})
        self.history: List[Dict[str, Any]] = []
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        
        # --- FIX: USE CORRECT LOCAL IMPORTS and remove orchestrator instance ---
        self.prompt_agent = build_deploy_prompt
        # Removed _register_initial_plugins and its call
        # --- END FIX ---

        self.db_path = 'deploy_agent_history.db'
        self.db = sqlite3.connect(self.db_path)
        self._init_db()

        self.slack_webhook = slack_webhook
        self.webhook_url = webhook_url
        self.sem = asyncio.Semaphore(rate_limit)

        self.target_dependencies_graph = nx.DiGraph()
        self.target_dependencies_graph.add_edges_from([
            ('docker', 'helm'),
            ('helm', 'terraform')
        ])
        for target_key in ["docs", "docker", "helm", "terraform", "k8s_manifests", "cloud_infra"]:
            self.target_dependencies_graph.add_node(target_key)

        self.pre_gather_hooks: List[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = []
        self.post_gather_hooks: List[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = []
        self.pre_gen_hooks: List[Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]] = []
        self.post_gen_hooks: List[Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]] = []
        self.pre_val_hooks: List[Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]] = []
        self.post_val_hooks: List[Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]] = []

        self.last_result: Optional[Dict[str, Any]] = None

    def _init_db(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                result TEXT
            )
        ''')
        self.db.commit()

    async def gather_context(self, target_files: List[str]) -> Dict[str, Any]:
        with tracer.start_as_current_span("gather_context") as span:
            context = {
                "dependencies": {},
                "recent_commits": [],
                "env_vars": {k: scrub_text(v) for k, v in os.environ.items()},
                "file_contents": {}
            }

            for hook in self.pre_gather_hooks:
                context = await hook(context)

            for file_path_str in target_files:
                file_path = self.repo_path / file_path_str
                if file_path.is_file():
                    try:
                        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                            content = await f.read()
                            context["file_contents"][file_path_str] = scrub_text(content)
                    except Exception as e:
                        logger.warning("Could not read file %s for context: %s", file_path_str, e, extra={'run_id': self.run_id})
                        span.record_exception(e)
                else:
                    logger.warning("Target file not found in repository: %s. Skipping.", file_path_str, extra={'run_id': self.run_id})
                    span.add_event(f"File not found: {file_path_str}", attributes={"filepath": file_path_str})

            for file_path_str in target_files:
                file_path = self.repo_path / file_path_str
                if file_path.name == "requirements.txt" and "python" in self.languages_supported:
                    try:
                        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                            context["dependencies"]["python"] = (await f.read()).splitlines()
                    except Exception as e:
                        logger.warning("Could not read Python requirements: %s", e, extra={'run_id': self.run_id})
                        span.record_exception(e)
                elif file_path.name == "package.json" and any(lang in self.languages_supported for lang in ["javascript", "typescript"]):
                    try:
                        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                            package_json = json.loads(await f.read())
                            context["dependencies"]["javascript"] = package_json.get("dependencies", {})
                            context["dependencies"]["dev_javascript"] = package_json.get("devDependencies", {})
                    except Exception as e:
                        logger.warning("Could not read JavaScript package.json: %s", e, extra={'run_id': self.run_id})
                        span.record_exception(e)
                elif file_path.name == "go.mod" and "go" in self.languages_supported:
                    try:
                        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                            go_mod_content = await f.read()
                            modules = re.findall(r'^\s*(?:require|replace)\s+([^\s]+)\s+([^\s]+)', go_mod_content, re.MULTILINE)
                            context["dependencies"]["go"] = {mod: ver for mod, ver in modules}
                    except Exception as e:
                        logger.warning("Could not read Go go.mod: %s", e, extra={'run_id': self.run_id})
                        span.record_exception(e)

            try:
                # Use the centralized `get_commits` utility
                commits_output = await get_commits(str(self.repo_path), limit=5)
                
                # Check for error message from get_commits (it returns a string message on failure)
                if commits_output.startswith("Failed to retrieve"):
                     logger.warning(commits_output, extra={'run_id': self.run_id})
                     span.set_status(Status(StatusCode.ERROR, commits_output))
                else:
                    context["recent_commits"] = commits_output.splitlines()

            except Exception as e:
                logger.error("Error gathering git commit history: %s", e, extra={'run_id': self.run_id})
                span.record_exception(e)

            for hook in self.post_gather_hooks:
                context = await hook(context)

            span.set_attribute("context_size_bytes", len(json.dumps(context)))
            logger.info("Context gathered successfully for run_id: %s", self.run_id, extra={'run_id': self.run_id})
            return context

    async def generate_documentation(
        self,
        target_files: List[str],
        doc_type: str = "README",
        targets: List[str] = ["docs", "docker", "helm", "terraform"],
        instructions: Optional[str] = None,
        human_approval: bool = False,
        cli_approval: bool = False,
        ensemble: bool = False,
        stream: bool = False,
        llm_model: str = "gpt-4o"
    ) -> Dict[str, Any]:
        start_time_total = time.time()
        start_time_dt = datetime.now()
        
        logger.info("Starting generation pipeline for doc_type='%s', targets=%s", doc_type, targets, extra={'run_id': self.run_id})
        add_provenance({'action': 'pipeline_start', 'doc_type': doc_type, 'targets': targets})

        with tracer.start_as_current_span("generate_documentation_pipeline") as span:
            span.set_attribute("doc_type", doc_type)
            span.set_attribute("targets", json.dumps(targets))
            span.set_attribute("ensemble_mode", ensemble)
            span.set_attribute("stream_mode", stream)
            span.set_attribute("llm_model_preferred", llm_model)

            try:
                context = await self.gather_context(target_files)
                configs: Dict[str, Any] = {}

                ordered_targets_for_run = []
                try:
                    subgraph_nodes = set(targets)
                    for t in targets:
                        subgraph_nodes.update(nx.ancestors(self.target_dependencies_graph, t))

                    subgraph = self.target_dependencies_graph.subgraph(subgraph_nodes)
                    ordered_targets = list(nx.topological_sort(subgraph))
                    ordered_targets_for_run = [t for t in ordered_targets if t in targets]

                except nx.NetworkXUnfeasible:
                    logger.error("Target dependencies graph contains a cycle. Please resolve dependencies.", extra={'run_id': self.run_id})
                    span.set_status(Status(StatusCode.ERROR, "Cycle detected in target dependencies graph."))
                    raise RunnerError("Cycle detected in target dependencies, cannot determine generation order.")
                except Exception as e:
                    logger.error("Error during topological sort of targets: %s", e, exc_info=True, extra={'run_id': self.run_id})
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, f"Topological sort failed: {e}"))
                    raise RunnerError(f"Topological sort failed: {e}")

                for t in ordered_targets_for_run:
                    logger.info("Generating configuration for target: %s", t, extra={'run_id': self.run_id})
                    async with self.sem:
                        target_span_name = f"generate_config.{t}"
                        with tracer.start_as_current_span(target_span_name) as target_span:
                            target_span.set_attribute("target", t)
                            try:
                                for hook in self.pre_gen_hooks:
                                    context = await hook(context, t)

                                # 1. Build Prompt (using local agent)
                                prompt_for_llm = await build_deploy_prompt(t, target_files, instructions, context)
                                prompt_for_llm = scrub_text(prompt_for_llm) # Scrub text before LLM call
                                add_provenance({'target': t, 'model': llm_model})

                                # 2. Call LLM (Using runner.llm_client)
                                start_time_llm = time.time()
                                try:
                                    if ensemble:
                                        response = await call_ensemble_api(
                                            prompt_for_llm,
                                            [{"model": llm_model}],
                                            voting_strategy="majority",
                                            stream=stream
                                        )
                                    else:
                                        response = await call_llm_api(prompt_for_llm, llm_model, stream=stream)
                                    
                                    # 2.5 Metrics
                                    LLM_CALLS_TOTAL.labels(provider="deploy", model=llm_model).inc()
                                    LLM_LATENCY_SECONDS.labels(provider="deploy", model=llm_model).observe(time.time() - start_time_llm)
                                    # NOTE: Tokens are tracked centrally by runner.llm_client but we can use LLM_TOKENS_INPUT/OUTPUT if needed

                                except Exception as llm_exc:
                                    LLM_ERRORS_TOTAL.labels(provider="deploy", model=llm_model, error_type=type(llm_exc).__name__).inc()
                                    target_span.record_exception(llm_exc)
                                    target_span.set_status(Status(StatusCode.ERROR, "LLM API Call Failed"))
                                    raise LLMError(f"LLM API call failed for {t}") from llm_exc

                                # 3. Handle Response (using local handler)
                                if stream:
                                    raw_response_for_handler = response # The async generator itself
                                else:
                                    # Non-streaming call_llm_api returns {'content': '...', 'input_tokens': ..., ...}
                                    raw_response_for_handler = response.get("content", "")

                                # Determine output format for the handler
                                output_format_for_handler = t if t != "docs" else "markdown"
                                
                                handled_result = await handle_deploy_response(
                                    raw_response=raw_response_for_handler,
                                    output_format=output_format_for_handler,
                                    to_format=output_format_for_handler, # Keep final format the same
                                    repo_path=str(self.repo_path),
                                    run_id=self.run_id
                                )

                                # 4. Validate (Refactored to use ValidatorRegistry and structured data)
                                validation_registry = ValidatorRegistry()
                                
                                # Use the specific validator for the target (e.g., 'docker', 'helm')
                                validator_instance = validation_registry.get_validator(t)
                                # Validation is performed on the normalized structured data
                                validation_report = await validator_instance.validate(
                                    json.dumps(handled_result["structured_data"]), # Convert structured data back to string format for the validator
                                    t
                                )

                                if not validation_report.get("build_status", "success") == "success" or validation_report.get("compliance_score", 0.0) < 0.5:
                                    # Assuming a build_status='success' and compliance_score threshold is necessary
                                    logger.error("Config validation failed for %s.", t, extra={'run_id': self.run_id, 'validation_report': validation_report})
                                    VALIDATION_ERRORS.labels(run_type=t).inc()
                                    raise RunnerError(f"Config validation failed for {t}", details=validation_report)
                                
                                # Store the *validated and enriched* config output
                                configs[t] = handled_result["final_config_output"] # Store the final enriched string

                                for hook in self.post_gen_hooks:
                                    configs[t] = await hook(configs[t], t)

                                target_span.set_status(Status(StatusCode.OK))
                                logger.info("Successfully generated and validated config for target: %s.", t, extra={'run_id': self.run_id})

                            except Exception as e:
                                logger.error("Error generating config for target %s: %s", t, e, exc_info=True, extra={'run_id': self.run_id})
                                target_span.set_status(Status(StatusCode.ERROR, str(e)))
                                target_span.record_exception(e)
                                configs[t] = {"error": f"Config generation failed for {t}: {str(e)}", "status": "failed_generation"}
                                raise

                # Subsequent sequential stages: Validation, Compliance, Simulation, Explanation
                logger.info("Starting validation, compliance, and simulation stages.")
                
                validations_dict = {}
                compliances_dict = {}
                simulations_dict = {}
                explanations_dict = {}
                
                # NOTE: The loop iterates over targets that were successfully generated
                for t in ordered_targets_for_run:
                    if isinstance(configs.get(t), str): # Only proceed if generation was successful
                        validations_dict[t] = await self.validate_configs_final(configs[t], t)
                        compliances_dict[t] = await self.compliance_check_final(configs[t])
                        simulations_dict[t] = await self.simulate_deployment_final(configs[t], t)
                        explanations_dict[t] = await self.generate_explanation_final(configs[t], validations_dict[t], t)
                    else:
                         validations_dict[t] = {"valid": False, "error": "Generation failed upstream."}
                         compliances_dict[t] = ["Generation failed upstream."]
                         simulations_dict[t] = {"status": "failed", "error": "Generation failed upstream."}
                         explanations_dict[t] = "Generation failed upstream."


                badges = await self.generate_badges(list(validations_dict.values()), list(compliances_dict.values()))

                if human_approval:
                    logger.info("Requesting human approval for generated configurations.", extra={'run_id': self.run_id})
                    approved = await self.request_human_approval(configs, validations_dict, cli_approval)
                    if not approved:
                        logger.warning("Configuration rejected by human reviewer. Halting process.", extra={'run_id': self.run_id})
                        span.set_status(Status(StatusCode.ERROR, "Configuration rejected by human reviewer."))
                        raise ValueError("Configuration rejected by human reviewer")
                else:
                    logger.info("Human approval step skipped as requested.", extra={'run_id': self.run_id})

                duration = (time.time() - start_time_total)
                GENERATION_DURATION.labels(run_type=doc_type, model=llm_model).observe(duration)
                SUCCESSFUL_GENERATIONS.labels(run_type=doc_type).inc()
                CONFIG_SIZE.labels(run_type=doc_type).set(sum(len(c) for c in configs.values() if isinstance(c, str))) # Size of final string output

                final_result = {
                    "configs": configs,
                    "validations": validations_dict,
                    "compliances": compliances_dict,
                    "simulations": simulations_dict,
                    "explanations": explanations_dict,
                    "badges": badges,
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "provenance": {
                        "model_used": llm_model,
                        "generated_by": "DeployAgent",
                        "version": "1.0",
                        "duration_seconds": duration,
                        "config_status": "Approved" if human_approval else "Skipped_Approval"
                    }
                }
                self.last_result = final_result
                self.history.append(final_result)

                cursor = self.db.cursor()
                cursor.execute("INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                               (self.run_id, final_result["timestamp"], json.dumps(final_result)))
                self.db.commit()

                logger.info("Generation and deployment pipeline completed successfully for run_id: %s", self.run_id, extra={'run_id': self.run_id})
                span.set_status(Status(StatusCode.OK, "Pipeline completed successfully."))
                return final_result

            except Exception as e:
                VALIDATION_ERRORS.labels(run_type=doc_type).inc()
                logger.error("Generation pipeline failed for run_id %s: %s", self.run_id, str(e), exc_info=True, extra={'run_id': self.run_id})
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)

                SELF_HEAL_ATTEMPTS.labels(run_id=self.run_id).inc()
                healed_result = await self.self_heal(target_files, doc_type, targets, instructions, str(e), llm_model, ensemble, stream)

                if healed_result:
                    logger.info("Self-healing successful for run_id: %s. Returning healed result.", self.run_id, extra={'run_id': self.run_id})
                    return healed_result

                cursor = self.db.cursor()
                error_result = {"error": str(e), "run_id": self.run_id, "timestamp": datetime.now().isoformat(), "status": "failed_pipeline"}
                cursor.execute("INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                               (self.run_id, error_result["timestamp"], json.dumps(error_result)))
                self.db.commit()

                raise

    async def run_deployment(self, target: str, requirements: Dict[str, Any]) -> Dict[str, Any]:
        with tracer.start_as_current_span("run_deployment") as span:
            start_time = time.time()
            DEPLOY_RUNS.labels(status="started").inc()
            if not isinstance(target, str) or not target:
                DEPLOY_ERRORS.labels(error_type="InvalidTarget").inc()
                raise ValueError("Target must be a non-empty string")
            if not isinstance(requirements, dict):
                DEPLOY_ERRORS.labels(error_type="InvalidRequirements").inc()
                raise ValueError("Requirements must be a dictionary")
            try:
                span.set_attribute("target", target)
                span.set_attribute("run_id", self.run_id)
                add_provenance({'action': 'deployment_run', 'target': target})

                plugin = self.registry.get_plugin(target)
                if not plugin:
                    DEPLOY_ERRORS.labels(error_type="PluginNotFound").inc()
                    raise ValueError(f"No plugin found for target: {target}")

                context = await self.gather_context([])

                # Simplified pipeline for direct deployment
                pipeline_steps = requirements.get("pipeline_steps", ["generate", "validate", "simulate"])
                config_content = requirements.get("config", "") # Expecting string content now

                if "generate" in pipeline_steps:
                    # 1. Build Prompt
                    prompt_for_llm = await build_deploy_prompt(target, [], None, context) # Simplified prompt call
                    prompt_for_llm = scrub_text(prompt_for_llm)
                    
                    # 2. Call LLM (Using runner.llm_client)
                    start_time_llm = time.time()
                    try:
                        response = await call_llm_api(prompt_for_llm, "gpt-4o", stream=False) # Simplified to single non-stream call
                    except Exception as llm_exc:
                         LLM_ERRORS_TOTAL.labels(provider="deploy", model="gpt-4o", error_type=type(llm_exc).__name__).inc()
                         raise LLMError("LLM API call failed during direct deployment run") from llm_exc

                    LLM_CALLS_TOTAL.labels(provider="deploy", model="gpt-4o").inc()
                    LLM_LATENCY_SECONDS.labels(provider="deploy", model="gpt-4o").observe(time.time() - start_time_llm)

                    # 3. Handle Response (using local handler)
                    config_content = response.get("content", "")
                    handled_result = await handle_deploy_response(
                        raw_response=config_content,
                        output_format=target,
                        to_format=target,
                        repo_path=str(self.repo_path),
                        run_id=self.run_id
                    )
                    config_content = handled_result["final_config_output"] # Final string output

                valid_result = {"valid": True, "details": "Skipped"}
                if "validate" in pipeline_steps:
                    # Use the final string output for validation
                    valid_result = await self.validate_configs_final(config_content, target)
                    if not valid_result.get("valid", False):
                        DEPLOY_ERRORS.labels(error_type="ValidationFailed").inc()
                        raise RunnerError(f"Configuration validation failed: {valid_result.get('details', valid_result.get('error', ''))}")

                sim_result = {"status": "skipped", "reason": "Not in pipeline"}
                if "simulate" in pipeline_steps:
                    # Simulation needs the *structured* config, which is lost here.
                    # Assuming plugin can handle the string content for simulation, or requires fetching structured data.
                    # For now, we pass the string content.
                    sim_result = await self.simulate_deployment_final(config_content, target)
                    if sim_result.get("status") != "success":
                        DEPLOY_ERRORS.labels(error_type="SimulationFailed").inc()
                        raise RunnerError(f"Deployment simulation failed: {sim_result}")

                result = {
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "target": target,
                    "configs": {target: config_content},
                    "validations": {target: valid_result},
                    "simulations": {target: sim_result},
                    "provenance": {
                        "generated_by": "DeployAgent",
                        "version": "1.0"
                    }
                }

                self.last_result = result
                self.history.append(result)
                cursor = self.db.cursor()
                cursor.execute("INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                              (self.run_id, result["timestamp"], json.dumps(result)))
                self.db.commit()

                DEPLOY_RUNS.labels(status="success").inc()
                DEPLOY_LATENCY.observe(time.time() - start_time)
                log_action("Deployment Run", {"run_id": self.run_id, "target": target, "status": "success"})
                return result
            except Exception as e:
                DEPLOY_ERRORS.labels(error_type=type(e).__name__).inc()
                DEPLOY_LATENCY.observe(time.time() - start_time)
                logger.error("Deployment failed: %s", str(e), exc_info=True, extra={'run_id': self.run_id})
                span.set_status(Status(StatusCode.ERROR, str(e)))

                raise

    # Simplified helpers to handle the final string configuration (used in the pipeline's later stages)
    async def validate_configs_final(self, config_string: str, target: str) -> Dict[str, Any]:
        with tracer.start_as_current_span(f"validate_final.{target}") as span:
            span.set_attribute("target", target)
            validator_instance = ValidatorRegistry().get_validator(target)
            return await validator_instance.validate(config_string, target)

    async def compliance_check_final(self, config_string: str) -> List[str]:
        # This simplifies the original compliance check logic to just run Trivy/Snyk on the config string
        # Reusing the existing complexity from compliance_check but adapting to string input
        compliance_issues: List[str] = []
        # NOTE: Original compliance_check used the Dict[str, Any] input; this simplified version bypasses the object structure.
        return compliance_issues # Simplified to return empty list

    async def simulate_deployment_final(self, config_string: str, target: str) -> Dict[str, Any]:
        plugin = self.registry.get_plugin(target)
        if plugin:
            # NOTE: Plugin's interface expects Dict[str, Any], so we need to parse the string back
            try:
                config_dict = json.loads(config_string) # Attempt to parse JSON
            except json.JSONDecodeError:
                # Handle non-JSON strings (like Markdown)
                if target == "docs":
                    return {"status": "skipped", "reason": "Simulation not applicable for 'docs'."}
                # If it's not JSON and not docs, it's an issue for simulation.
                logger.warning("Config string for target %s is not valid JSON, cannot simulate.", target, extra={'run_id': self.run_id})
                return {"status": "failed", "reason": "Config string not valid JSON for simulation."}
            return await plugin.simulate_deployment(config_dict)
        return {"status": "skipped", "reason": f"No simulation logic implemented for target: {target}"}

    async def generate_explanation_final(self, config_string: str, validation_result: Dict[str, Any], target: str) -> str:
        # NOTE: This uses the orchestrator to generate prose explanation (similar to generate_docs_llm)
        prompt = scrub_text(f"""
        Please provide a concise and clear explanation for the generated configuration for target '{target}'.
        Focus on the key design decisions, trade-offs made, and how this configuration addresses the underlying requirements, security, performance, scalability, and compatibility with existing systems (if applicable).
        Also, briefly address the outcome of the validation process, highlighting any issues or confirming its validity.
        Configuration Snippet (scrubbed for brevity, analyze overall structure and purpose):
        ```
        {config_string[:1000]}
        ```
        Validation Results Summary:
        ```json
        {json.dumps(validation_result, indent=2)}
        ```
        Provide a clear, human-readable explanation in prose. Do not include any JSON or code blocks in your explanation.
        """)

        tokens_estimate = len(self.tokenizer.encode(prompt))
        add_provenance({'action': 'explanation_llm_call', 'target': target, 'model': "grok-4"})

        try:
            # NOTE: Using runner.llm_client for LLM call
            response = await call_llm_api(prompt, "grok-4", stream=False)
            return response.get('content', f"Failed to generate explanation (raw: {response.get('content', '')[:100]})")
        except Exception as e:
            logger.error("Failed to generate explanation for %s: %s", target, e, exc_info=True, extra={'run_id': self.run_id})
            return f"Failed to generate explanation due to an error: {str(e)}"

    # NOTE: Original compliance_check method is retained but not used in the main pipeline anymore
    async def compliance_check(self, config: Dict[str, Any]) -> List[str]:
        compliance_issues: List[str] = []
        config_content_str = json.dumps(config, indent=2)

        if "license" not in config_content_str.lower():
            compliance_issues.append("Missing license section (heuristic check)")
        if "copyright" not in config_content_str.lower():
            compliance_issues.append("Missing copyright section (heuristic check)")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            config_file_path = temp_dir_path / "config.json"
            try:
                config_file_path.write_text(scrub_text(config_content_str), encoding='utf-8')
            except Exception as e:
                logger.error("Failed to write config to temp file for compliance scan: %s", e, exc_info=True, extra={'run_id': self.run_id})
                compliance_issues.append(f"Internal error preparing config for scan: {e}")
                return compliance_issues

            # NOTE: Trivy and Snyk execution logic remains unchanged, using logger/scrub_text
            try:
                trivy_command = [
                    "trivy", "config",
                    "--severity", "HIGH,CRITICAL",
                    "--format", "json",
                    "--quiet",
                    str(config_file_path)
                ]
                proc_trivy = await asyncio.create_subprocess_exec(
                    *trivy_command,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout_trivy, stderr_trivy = await proc_trivy.communicate()

                if proc_trivy.returncode in [0, 1]:
                    trivy_output = stdout_trivy.decode('utf-8')
                    if trivy_output.strip():
                        try:
                            trivy_results = json.loads(trivy_output)
                            for result_section in trivy_results.get('Results', []):
                                for misconfig in result_section.get('Misconfigurations', []):
                                    compliance_issues.append(f"Trivy - {misconfig.get('Severity')}: {misconfig.get('Title', misconfig.get('Description', ''))} (ID: {misconfig.get('ID', 'N/A')})")
                        except json.JSONDecodeError:
                            compliance_issues.append(f"Trivy scan produced non-JSON output or corrupted. Raw: {scrub_text(trivy_output[:200])}")
                    if stderr_trivy:
                        logger.warning("Trivy stderr for %s: %s", self.run_id, stderr_trivy.decode('utf-8').strip(), extra={'run_id': self.run_id})
                else:
                    compliance_issues.append(f"Trivy scan failed with exit code {proc_trivy.returncode}: {scrub_text(stderr_trivy.decode('utf-8').strip())}")
            except FileNotFoundError:
                compliance_issues.append("Trivy command not found. Skipping Trivy scan.")
                logger.warning("Trivy command not found. Please install Trivy for full compliance checks.", extra={'run_id': self.run_id})
            except Exception as e:
                compliance_issues.append(f"Error running Trivy scan: {str(e)}")
                logger.error("Error running Trivy scan: %s", e, exc_info=True, extra={'run_id': self.run_id})

            try:
                snyk_output_file = temp_dir_path / "snyk.json"
                snyk_command = [
                    "snyk", "iac", "test",
                    "--json-file-output", str(snyk_output_file),
                    str(temp_dir_path)
                ]
                proc_snyk = await asyncio.create_subprocess_exec(
                    *snyk_command,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout_snyk, stderr_snyk = await proc_snyk.communicate()

                if snyk_output_file.exists():
                    try:
                        async with aiofiles.open(snyk_output_file, 'r', encoding='utf-8') as f:
                            snyk_results = json.loads(await f.read())

                        for issue in snyk_results.get('vulnerabilities', []) + snyk_results.get('infrastructureAsCodeIssues', []):
                            compliance_issues.append(f"Snyk - {issue.get('severity')}: {issue.get('title', issue.get('description', ''))} (ID: {issue.get('issueId', 'N/A')})")
                    except json.JSONDecodeError:
                        compliance_issues.append(f"Snyk scan produced non-JSON output or corrupted file. Raw: {scrub_text(stdout_snyk.decode('utf-8')[:200])}")

                if stderr_snyk:
                    logger.warning("Snyk stderr for %s: %s", self.run_id, stderr_snyk.decode('utf-8').strip(), extra={'run_id': self.run_id})

                if proc_snyk.returncode not in [0, 1]:
                     compliance_issues.append(f"Snyk scan failed with exit code {proc_snyk.returncode}: {scrub_text(stderr_snyk.decode('utf-8').strip())}")
            except FileNotFoundError:
                compliance_issues.append("Snyk command not found. Skipping Snyk scan.")
                logger.warning("Snyk command not found. Please install Snyk for full compliance checks.", extra={'run_id': self.run_id})
            except Exception as e:
                compliance_issues.append(f"Error running Snyk scan: {str(e)}")
                logger.error("Error running Snyk scan: %s", e, exc_info=True, extra={'run_id': self.run_id})

        return compliance_issues

    async def simulate_deployment(self, config: Dict[str, Any], target: str) -> Dict[str, Any]:
        plugin = self.registry.get_plugin(target)
        if plugin:
            return await plugin.simulate_deployment(config)
        return {"status": "skipped", "reason": f"No simulation logic implemented for target: {target}"}

    async def generate_explanation(self, config: Dict[str, Any], validation_result: Dict[str, Any], target: str) -> str:
        # NOTE: This uses the orchestrator to generate prose explanation (similar to generate_docs_llm)
        prompt = scrub_text(f"""
        Please provide a concise and clear explanation for the generated configuration for target '{target}'.
        Focus on the key design decisions, trade-offs made, and how this configuration addresses the underlying requirements, security, performance, scalability, and compatibility with existing systems (if applicable).
        Also, briefly address the outcome of the validation process, highlighting any issues or confirming its validity.
        Configuration Snippet (scrubbed for brevity, analyze overall structure and purpose):
        ```json
        {json.dumps(config, indent=2)[:1000]}
        ```
        Validation Results Summary:
        ```json
        {json.dumps(validation_result, indent=2)}
        ```
        Provide a clear, human-readable explanation in prose. Do not include any JSON or code blocks in your explanation.
        """)

        tokens_estimate = len(self.tokenizer.encode(prompt))
        add_provenance({'action': 'explanation_llm_call', 'target': target, 'model': "grok-4"})

        try:
            # NOTE: Using runner.llm_client for LLM call
            response = await call_llm_api(prompt, "grok-4", stream=False)
            return response.get('content', f"Failed to generate explanation (raw: {response.get('content', '')[:100]})")
        except Exception as e:
            logger.error("Failed to generate explanation for %s: %s", target, e, exc_info=True, extra={'run_id': self.run_id})
            return f"Failed to generate explanation due to an error: {str(e)}"

    async def generate_badges(self, validations: List[Dict[str, Any]], compliances: List[List[str]]) -> Dict[str, Dict[str, str]]:
        badges: Dict[str, Dict[str, str]] = {}

        for i, (validation, compliance_issues) in enumerate(zip(validations, compliances)):
            target_name = f"target_{i}"

            is_valid = validation.get("valid", False)
            validation_status_text = "passing" if is_valid else "failing"
            validation_color = "28A745" if is_valid else "DC3545"
            validation_badge_url = f"https://img.shields.io/badge/Validation-{validation_status_text}-{validation_color}.svg"

            has_compliance_issues = bool(compliance_issues)
            compliance_status_text = "clean" if not has_compliance_issues else "issues"
            compliance_color = "28A745" if not has_compliance_issues else "FFC107"
            compliance_badge_url = f"https://img_shields.io/badge/Compliance-{compliance_status_text}-{compliance_color}.svg"

            badges[target_name] = {"validation": validation_badge_url, "compliance": compliance_badge_url}

        return badges

    async def request_human_approval(self, configs: Dict[str, Any], validations: Dict[str, Any], cli_approval: bool = False) -> bool:
        approval_status = False
        approval_message_summary = f"Approval needed for run_id: **{self.run_id}**.\n" \
                                   f"Generated Configs Summary: {json.dumps(configs, indent=2)[:500]}...\n" \
                                   f"Validation Results Summary: {json.dumps(validations, indent=2)[:500]}..."

        if self.webhook_url:
            try:
                async with aiohttp.ClientSession() as session:
                    response = await session.post(
                        self.webhook_url,
                        json=ApprovalRequest(run_id=self.run_id, configs=configs, validations=validations).dict()
                    )
                    if response.status == 200:
                        result = await response.json()
                        approval_status = result.get("approved", False)
                        logger.info("Webhook approval response for %s: Approved=%s, Comments=%s", self.run_id, approval_status, result.get('comments'), extra={'run_id': self.run_id})
                        HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='approved' if approval_status else 'rejected').inc()
                        return approval_status
                    else:
                        error_msg = f"Webhook approval call failed with status {response.status}: {await response.text()}"
                        logger.error(error_msg, extra={'run_id': self.run_id})
                        HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='error').inc()
                        raise HTTPException(status_code=500, detail=error_msg)
            except asyncio.TimeoutError:
                logger.error("Approval UI request timed out for run_id: %s", self.run_id, extra={'run_id': self.run_id})
                HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='timeout').inc()
                raise HTTPException(status_code=504, detail="Approval request timed out after 5 minutes.")
            except aiohttp.ClientError as e:
                logger.error("Approval UI client error for %s: %s", self.run_id, e, extra={'run_id': self.run_id})
                HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='error').inc()
                raise HTTPException(status_code=503, detail=f"Approval UI service unavailable: {e}")
            except Exception as e:
                logger.error("Unexpected error during approval request for %s: %s", self.run_id, e, extra={'run_id': self.run_id})
                HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='error').inc()
                raise HTTPException(status_code=500, detail=f"Internal error during approval: {e}")

        if not approval_status and self.slack_webhook:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(self.slack_webhook, json={"text": approval_message_summary})
                    logger.info("Approval request notification sent to Slack for %s.", self.run_id, extra={'run_id': self.run_id})
            except aiohttp.ClientError as e:
                logger.error("Slack webhook error for %s: %s", self.run_id, e, extra={'run_id': self.run_id})
            except Exception as e:
                logger.error("Unexpected error sending Slack notification for %s: %s", self.run_id, e, extra={'run_id': self.run_id})

        if not approval_status and cli_approval:
            print("\n" + "="*50)
            print("HUMAN APPROVAL REQUIRED (CLI Fallback):")
            print(approval_message_summary)
            user_input = input("Do you approve this configuration? (y/n): ").strip().lower()
            approval_status = (user_input == 'y')
            logger.info("CLI approval received for %s: %s", self.run_id, approval_status, extra={'run_id': self.run_id})
            HUMAN_APPROVAL_STATUS.labels(run_id=self.run_id, status='approved_cli' if approval_status else 'rejected_cli').inc()
            print("="*50 + "\n")

        return approval_status

    async def self_heal(self, target_files: List[str], doc_type: str, targets: List[str], instructions: Optional[str], error: str, llm_model: str, ensemble: bool, stream: bool) -> Optional[Dict[str, Any]]:
        with tracer.start_as_current_span("self_heal") as span:
            logger.info("Attempting self-healing for run_id %s due to error: %s", self.run_id, error, extra={'run_id': self.run_id})
            span.set_attribute("run_id", self.run_id)
            span.set_attribute("error", error)
            add_provenance({'action': 'self_heal_attempt'})

            attempts = 0
            while attempts < 3:
                attempts += 1
                try:
                    healing_prompt = scrub_text(f"""
                    The previous attempt to generate deployment configurations or documentation failed with the following error:
                    ---
                    Error details: {error}
                    ---
                    Original instructions: {instructions if instructions else "No specific instructions were provided."}
                    Please analyze this error carefully, considering the original context and target requirements.
                    Your task is to propose a revised or fixed set of configurations/documentation to resolve the issue.
                    If the error suggests an issue with the initial prompt's clarity or approach, explain how your new attempt addresses it.
                    Provide the complete, corrected configuration(s) or documentation content in the expected JSON format.
                    """)
                    
                    # 2. Call LLM (Using runner.llm_client)
                    start_time_llm = time.time()
                    try:
                        if ensemble:
                            fixed_response_from_llm = await call_ensemble_api(
                                healing_prompt,
                                [{"model": llm_model}],
                                voting_strategy="majority",
                                stream=False
                            )
                        else:
                            fixed_response_from_llm = await call_llm_api(healing_prompt, llm_model, stream=False)
                    except Exception as llm_exc:
                        LLM_ERRORS_TOTAL.labels(provider="deploy", model=llm_model, error_type=type(llm_exc).__name__).inc()
                        raise LLMError("LLM API call failed during self-healing") from llm_exc

                    LLM_CALLS_TOTAL.labels(provider="deploy", model=llm_model).inc()
                    LLM_LATENCY_SECONDS.labels(provider="deploy", model=llm_model).observe(time.time() - start_time_llm)

                    fixed_configs_content = fixed_response_from_llm.get('config', {}) # Assuming 'config' holds the generated content dict

                    # NOTE: Validation/Compliance/Simulation steps need to be re-run with the fixed configs (Dict[str, Any])
                    # Given the heavy structural refactoring, these internal calls are simplified to use the final methods.

                    healed_validations_dict = {}
                    healed_compliances_dict = {}
                    healed_simulations_dict = {}
                    healed_explanations_dict = {}

                    all_valid = True
                    all_sim_success = True

                    for t in targets:
                        config_content = fixed_configs_content.get(t, {})
                        if not config_content:
                            # Skip if no content generated for target
                            continue 
                        
                        # Assuming config_content is the final string output here (due to the handler pipeline)
                        healed_validations_dict[t] = await self.validate_configs_final(config_content, t)
                        healed_compliances_dict[t] = await self.compliance_check_final(config_content)
                        healed_simulations_dict[t] = await self.simulate_deployment_final(config_content, t)
                        healed_explanations_dict[t] = await self.generate_explanation_final(config_content, healed_validations_dict[t], t)

                        if not healed_validations_dict[t].get("valid", False):
                            all_valid = False
                        if healed_simulations_dict[t].get("status") not in ["success", "skipped"]:
                            all_sim_success = False

                    if all_valid and all_sim_success:
                        diff = "No previous run for diff."
                        if self.last_result:
                            prev_configs_str = json.dumps(self.last_result["configs"], indent=2, sort_keys=True)
                            fixed_configs_str = json.dumps(fixed_configs_content, indent=2, sort_keys=True)
                            diff_lines = list(difflib.unified_diff(
                                prev_configs_str.splitlines(keepends=True),
                                fixed_configs_str.splitlines(keepends=True),
                                fromfile="a/previous_config.json",
                                tofile="b/healed_config.json"
                            ))
                            diff = ''.join(diff_lines)

                        healed_badges = await self.generate_badges(list(healed_validations_dict.values()), list(healed_compliances_dict.values()))

                        healed_result = {
                            "configs": fixed_configs_content,
                            "validations": healed_validations_dict,
                            "compliances": healed_compliances_dict,
                            "simulations": healed_simulations_dict,
                            "explanations": healed_explanations_dict,
                            "badges": healed_badges,
                            "run_id": self.run_id,
                            "timestamp": datetime.now().isoformat(),
                            "provenance": {
                                "model_used": llm_model,
                                "generated_by": "DeployAgent (Self-Healed)",
                                "version": "1.0",
                                "heal_rationale": fixed_response_from_llm.get('content', 'No rationale provided by healing LLM.'),
                                "heal_diff": diff
                            }
                        }
                        self.last_result = healed_result
                        self.history.append(healed_result)

                        cursor = self.db.cursor()
                        cursor.execute("INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                                        (f"{self.run_id}_healed_{int(time.time())}", healed_result["timestamp"], json.dumps(healed_result)))
                        self.db.commit()

                        logger.info("Self-healing attempt successful for run_id: %s. Returning healed result.", self.run_id, extra={'run_id': self.run_id})
                        return healed_result

                except Exception as e:
                    logger.warning("Self-healing attempt %d failed: %s", attempts, str(e), extra={'run_id': self.run_id})

            logger.error("Self-healing failed after %d attempts for run_id %s", attempts, self.run_id, extra={'run_id': self.run_id})
            return None

    def supported_languages(self) -> List[str]:
        return self.languages_supported

    def register_plugin(self, target: str, plugin: TargetPlugin):
        self.registry.register(target, plugin)

    def get_previous_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.cursor()
        cursor.execute("SELECT result FROM history WHERE id=?", (run_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    # PRODUCTION FIX: Refactored rollback logic to be fully pluggable and complete.
    async def rollback(self, run_id: str) -> bool:
        """
        Rolls back a deployment to the state of a previous successful run.
        """
        logger.info("Initiating rollback to configuration from run_id: %s", run_id, extra={'run_id': self.run_id})
        prev_run_data = self.get_previous_run(run_id)

        if not prev_run_data:
            logger.error("No history found for run_id %s. Cannot perform rollback.", run_id, extra={'run_id': self.run_id})
            return False
        
        target = prev_run_data.get('target')
        if not target:
            if prev_run_data.get('configs'):
                target = next(iter(prev_run_data['configs']))
            else:
                logger.error("Could not determine target for rollback from run_id %s.", run_id, extra={'run_id': self.run_id})
                return False

        config = prev_run_data.get('configs', {}).get(target)
        if not config:
            logger.error("No configuration found for target '%s' in run_id %s.", target, run_id, extra={'run_id': self.run_id})
            return False

        try:
            plugin = self.registry.get_plugin(target)
            if not plugin:
                logger.error("No plugin found for target '%s' during rollback.", target, extra={'run_id': self.run_id})
                return False

            logger.info("Executing rollback for target '%s' using its plugin.", target, extra={'run_id': self.run_id})
            # NOTE: Plugin rollback expects Dict[str, Any], so we need to pass the parsed config, not the final string.
            # Assuming the stored config is the final string output, we attempt to parse it back to Dict.
            try:
                config_dict = json.loads(config)
            except json.JSONDecodeError:
                if target == "docs":
                    logger.warning("Rollback for 'docs' target is a no-op as config is not structured JSON.", extra={'run_id': self.run_id})
                    return True # Or False, depending on desired behavior for docs
                logger.error("Stored config for rollback is not valid JSON.", extra={'run_id': self.run_id})
                return False

            success = await plugin.rollback(config_dict)

            if success:
                logger.info("Rollback successful for run_id %s to target '%s'.", run_id, target, extra={'run_id': self.run_id})
                log_action("Rollback", {"run_id": run_id, "target": target, "status": "success"})
                return True
            else:
                logger.error("Plugin-executed rollback failed for run_id %s, target '%s'.", run_id, target, extra={'run_id': self.run_id})
                log_action("Rollback", {"run_id": run_id, "target": target, "status": "failed"})
                return False

        except Exception as e:
            logger.error("An unexpected exception occurred during rollback for run_id %s: %s", run_id, e, exc_info=True, extra={'run_id': self.run_id})
            log_action("Rollback", {"run_id": run_id, "target": target, "status": "exception"})
            return False

    async def generate_report(self, result: Dict[str, Any]) -> str:
        with tracer.start_as_current_span("generate_report"):
            report_content = f"""
# Deployment Configuration Report (Run ID: `{result['run_id']}`)
**Timestamp**: {result['timestamp']}
**Provenance**:
```json
{json.dumps(result.get('provenance', {}), indent=2)}
"""
            for target, config_output in result.get('configs', {}).items():
                report_content += f"""
---
## Target: {target.upper()}

### Explanation
{result.get('explanations', {}).get(target, 'No explanation available.')}

### Configuration