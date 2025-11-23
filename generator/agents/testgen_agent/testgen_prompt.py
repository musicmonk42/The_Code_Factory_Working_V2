"""
testgen_prompt.py: The intelligent core for the agentic testing system.

REFACTORED: This module is now fully compliant with the central runner foundation.
All V0/V1 dependencies (testgen_llm_call, audit_log, utils) have been removed
and replaced with runner.llm_client, runner.runner_logging, and runner.runner_metrics.

Features:
- Multi-RAG with ChromaDB for codebase, tests, docs, dependencies, and historical failures.
- Advanced template versioning and rollback for robust prompt management.
- Dynamic chain adaptation based on output quality.
- Strict sanitization and auditing for security and compliance.
- Hot-reloading of templates for dynamic updates.
- Health endpoints for Kubernetes compatibility (port 8080).
- Plugin architecture for custom prompt builders.
"""

import abc
import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

# Gold Standard Imports
import chromadb
import tiktoken  # Imported for token counting
from aiohttp import web
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape

# --- CENTRAL RUNNER FOUNDATION ---
from runner.llm_client import call_llm_api
from runner.runner_errors import LLMError
from runner.runner_logging import add_provenance, logger
from runner.runner_metrics import LLM_ERRORS_TOTAL
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# -----------------------------------

# --- External dependencies (REFACTORED) ---
# REMOVED: from ...audit_log import log_action
# REMOVED: from ...utils import token_count_estimate, summarize_text
# REMOVED: from testgen_llm_call import call_llm_api, batch_generate_docs_llm, scrub_prompt, TokenizerService

load_dotenv()
# REFACTORED: Removed local logger = logging.getLogger(__name__)

# Configuration
MAX_PROMPT_TOKENS = int(os.getenv("TESTGEN_MAX_PROMPT_TOKENS", 16000))
COMPLIANCE_MODE = os.getenv("COMPLIANCE_MODE", "false").lower() == "true"
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "testgen_templates")
TEMPLATE_VERSION_DIR = os.path.join(TEMPLATE_DIR, "versions")
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(TEMPLATE_VERSION_DIR, exist_ok=True)

# Advanced Sanitization Patterns
SANITIZATION_PATTERNS = {
    "[REDACTED_CREDENTIAL]": r'(?i)(api_key|password|secret|token|auth|bearer)\s*[:=]\s*["\']?[^"\']+["\']?(?=\s|$)',
    "[REDACTED_EMAIL]": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "[REDACTED_PHONE]": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    "[REDACTED_IP]": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "[REDACTED_SSN]": r"\b\d{3}-\d{2}-\d{4}\b",
    "[REDACTED_CC]": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}


def _local_regex_sanitize(text: str) -> str:
    """
    Internal helper to scrub text using this file's local patterns.
    This replaces the dependency on the V0 `scrub_prompt` function.
    """
    for replacement, pattern in SANITIZATION_PATTERNS.items():
        text = re.sub(pattern, replacement, text)
    return text


# Supported Languages and Frameworks
SUPPORTED_LANGUAGES = ["python", "javascript", "java", "rust", "go"]
SUPPORTED_FRAMEWORKS = {
    "python": ["pytest", "unittest"],
    "javascript": ["jest", "mocha"],
    "java": ["junit", "testng"],
}

# Plugin registry for custom prompt builders
PROMPT_BUILDER_REGISTRY: Dict[str, type] = {}


# Health Endpoints for Kubernetes
async def healthz(request):
    """Kubernetes liveness/readiness probe on port 8080."""
    return web.Response(text="OK", status=200)


async def start_health_server():
    """Starts an aiohttp server for health endpoints on port 8080."""
    app = web.Application()
    app.add_routes([web.get("/healthz", healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Health endpoint server started on port 8080.")


# 1. Multi-RAG: Multiple Vector DBs for Diverse Contexts
class MultiVectorDBManager:
    """
    Manages multiple collections in the vector database for Multi-RAG.
    REFACTORED: Uses central runner logging.
    """

    def __init__(self, path: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=path)
        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
        self.collections = {
            "codebase": self.client.get_or_create_collection(
                name="codebase", embedding_function=self.embedding_function
            ),
            "tests": self.client.get_or_create_collection(
                name="tests", embedding_function=self.embedding_function
            ),
            "docs": self.client.get_or_create_collection(
                name="docs", embedding_function=self.embedding_function
            ),
            "dependencies": self.client.get_or_create_collection(
                name="dependencies", embedding_function=self.embedding_function
            ),
            "historical_failures": self.client.get_or_create_collection(
                name="historical_failures", embedding_function=self.embedding_function
            ),
        }

    async def add_files(self, collection_name: str, files: Dict[str, str]):
        """
        Adds or updates files in a specific collection asynchronously.
        """
        if collection_name not in self.collections:
            logger.error(f"Unknown collection: {collection_name}")
            raise ValueError(f"Unknown collection: {collection_name}")
        if not files:
            logger.debug(f"No files provided for {collection_name} collection")
            return
        try:
            ids = [
                hashlib.sha256(content.encode()).hexdigest()
                for content in files.values()
            ]
            documents = list(files.values())
            metadatas = [{"filename": filename} for filename in files.keys()]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.collections[collection_name].add(
                    documents=documents, metadatas=metadatas, ids=ids
                ),
            )
            logger.info(
                f"Added/updated {len(files)} files in {collection_name} collection."
            )

            # REFACTORED: Use add_provenance
            add_provenance(
                {
                    "action": "VectorDBUpdate",
                    "collection": collection_name,
                    "file_count": len(files),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger": "add_files",
                }
            )
        except Exception as e:
            logger.error(
                f"Failed to add files to {collection_name} collection: {e}",
                exc_info=True,
            )
            raise

    async def query_relevant_context(
        self, query_text: str, collections: List[str] = None, n_results: int = 3
    ) -> Dict[str, str]:
        """
        Queries multiple collections for relevant contexts asynchronously.
        """
        if collections is None:
            collections = list(self.collections.keys())
        contexts = {}
        for col_name in collections:
            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.collections[col_name].query(
                        query_texts=[query_text], n_results=n_results
                    ),
                )
                context_str = ""
                for i, doc in enumerate(results["documents"][0]):
                    filename = results["metadatas"][0][i]["filename"]
                    context_str += (
                        f"--- Relevant from {col_name}: {filename} ---\n{doc}\n\n"
                    )
                contexts[col_name] = context_str
                logger.debug(f"Queried {n_results} results from {col_name} collection")
            except Exception as e:
                logger.error(
                    f"Failed to query {col_name} collection: {e}", exc_info=True
                )
                contexts[col_name] = f"Could not retrieve relevant context: {str(e)}"

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "VectorDBQuery",
                "collections": collections,
                "query_length": len(query_text),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "query_relevant_context",
            }
        )
        return contexts

    async def close(self):
        """Closes any resources held by the vector DB manager."""
        self.collections.clear()
        logger.info("MultiVectorDBManager resources cleared.")
        add_provenance(
            {
                "action": "VectorDBClosed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


# 2. Prompt Analytics & Template Versioning/Rollback
class AdvancedTemplateTracker:
    """
    Tracks performance, versions templates, and enables auto-evolution/rollback.
    REFACTORED: Uses central runner logging and LLM client.
    """

    def __init__(self, db_path: str = "template_performance.json"):
        self.db_path = db_path
        # REFACTORED: Removed llm_api_url
        self.data = self._load()
        self.versions = {}  # {template_name: {version: hash}}
        self.observer = None
        self._setup_hot_reload()

    def _load(self) -> Dict:
        """Loads performance and version data from the JSON file."""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Failed to decode template performance JSON {self.db_path}: {e}. Initializing new store."
                )
                return {"performance": {}, "versions": {}, "version_hashes": {}}
        return {"performance": {}, "versions": {}, "version_hashes": {}}

    def _save(self):
        """Saves performance and version data to the JSON file atomically."""
        try:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".json"
            )
            json.dump(self.data, temp_file, indent=2)
            temp_file.close()
            os.replace(temp_file.name, self.db_path)
            logger.debug(f"Saved template performance data to {self.db_path}")
        except Exception as e:
            logger.error(
                f"Failed to save template performance data: {e}", exc_info=True
            )

    def log_performance(self, template_hash: str, scores: Dict[str, float]):
        """
        Logs performance metrics for a template with history.
        """
        if template_hash not in self.data["performance"]:
            self.data["performance"][template_hash] = {
                "runs": 0,
                "total_scores": {},
                "history": [],
            }
        self.data["performance"][template_hash]["runs"] += 1
        for key, value in scores.items():
            current_total = self.data["performance"][template_hash]["total_scores"].get(
                key, 0.0
            )
            self.data["performance"][template_hash]["total_scores"][key] = (
                current_total + value
            )
        self.data["performance"][template_hash]["history"].append(
            {"scores": scores, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.data["performance"][template_hash]["history"]) > 100:
            self.data["performance"][template_hash]["history"].pop(0)
        self._save()

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "TemplatePerformanceLogged",
                "template_hash": template_hash,
                "scores": scores,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "log_performance",
            }
        )

    def get_avg_score(self, template_hash: str, metric: str) -> float:
        """
        Calculates the average score for a specific metric.
        """
        data = self.data["performance"].get(template_hash, {})
        if data.get("runs", 0) > 0:
            return data["total_scores"].get(metric, 0.0) / data["runs"]
        logger.debug(f"No runs for template_hash {template_hash}")
        return -1

    def version_template(self, template_name: str, content: str) -> str:
        """
        Versions a template by saving it with a unique ID.
        """
        version = str(uuid.uuid4())[:8]
        version_path = os.path.join(
            TEMPLATE_VERSION_DIR, f"{template_name}_{version}.jinja"
        )
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        with open(version_path, "w") as f:
            f.write(content)
        self.data["versions"].setdefault(template_name, []).append(version)
        self.data["version_hashes"].setdefault(template_name, {})[
            version
        ] = content_hash
        self._save()
        logger.info(
            f"Versioned template {template_name} as {version} with hash {content_hash}."
        )

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "TemplateVersioned",
                "template_name": template_name,
                "version": version,
                "content_hash": content_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "version_template",
            }
        )
        return version

    def rollback_template(self, template_name: str, to_version: str):
        """
        Rolls back a template to a specified version.
        """
        version_path = os.path.join(
            TEMPLATE_VERSION_DIR, f"{template_name}_{to_version}.jinja"
        )
        if os.path.exists(version_path):
            with open(version_path, "r") as f:
                content = f.read()
            active_path = os.path.join(TEMPLATE_DIR, f"{template_name}.jinja")
            with open(active_path, "w") as f:
                f.write(content)
            content_hash = (
                self.data["version_hashes"]
                .get(template_name, {})
                .get(to_version, "unknown")
            )
            logger.info(
                f"Rolled back {template_name} to version {to_version} with hash {content_hash}."
            )

            # REFACTORED: Use add_provenance
            add_provenance(
                {
                    "action": "TemplateRollback",
                    "template_name": template_name,
                    "version": to_version,
                    "content_hash": content_hash,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger": "rollback_template",
                }
            )
        else:
            logger.error(
                f"Version {to_version} not found for {template_name}. Directory contents: {os.listdir(TEMPLATE_VERSION_DIR)}"
            )
            raise FileNotFoundError(
                f"Version {to_version} not found for {template_name}."
            )

    async def auto_evolve_template(
        self, template_name: str, current_content: str, performance_data: Dict
    ) -> str:
        """
        Uses an LLM to refine a template based on performance metrics.
        REFACTORED: Uses runner.llm_client and local sanitizer.
        """
        prompt = f"Refine this prompt template based on performance: {json.dumps(performance_data)}\nTemplate:\n{current_content}\nImprove for better {performance_data['primary_metric']}."
        pre_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        # REFACTORED: Use internal regex sanitizer
        scrubbed_prompt = _local_regex_sanitize(prompt)

        post_hash = hashlib.sha256(scrubbed_prompt.encode("utf-8")).hexdigest()

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "TemplateEvolutionPromptSanitized",
                "pre_hash": pre_hash,
                "post_hash": post_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "auto_evolve_template",
            }
        )

        if COMPLIANCE_MODE:
            add_provenance(
                {
                    "action": "ComplianceSensitiveData",
                    "scrubbed_fields": {
                        "prompt": {"pre_hash": pre_hash, "post_hash": post_hash}
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        model = "gpt-4o"  # Use a strong model for refinement
        try:
            # REFACTORED: Use runner.llm_client.call_llm_api
            time.time()
            response = await call_llm_api(prompt=scrubbed_prompt, model=model)

            # REMOVED: Manual LLM Metrics (Handled by llm_client)
            # LLM_CALLS_TOTAL.labels(provider="testgen_prompt", model=model, task="auto_evolve_template").inc()
            # LLM_LATENCY_SECONDS.labels(provider="testgen_prompt", model=model, task="auto_evolve_template").observe(time.time() - start_time)

            new_content = response.get("content", current_content)

            if not new_content or new_content == current_content:
                logger.warning(
                    f"Auto-evolve returned no new content for {template_name}."
                )
                LLM_ERRORS_TOTAL.labels(
                    provider="testgen_prompt",
                    model=model,
                    error_type="EmptyLLMResponse",
                ).inc()
                return current_content

            version = self.version_template(template_name, new_content)
            logger.info(f"Auto-evolved template {template_name} to version {version}.")
            return new_content
        except Exception as e:
            logger.error(f"Failed to auto-evolve template: {e}", exc_info=True)
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(
                    provider="testgen_prompt", model=model, error_type=type(e).__name__
                ).inc()
            return current_content

    def check_for_regression(
        self, template_hash: str, new_scores: Dict, primary_metric: str
    ):
        """
        Checks for performance regression and initiates rollback if detected.
        """
        avg_score = self.get_avg_score(template_hash, primary_metric)
        if new_scores.get(primary_metric, 0) < avg_score * 0.9:  # 10% regression
            logger.warning(
                f"Regression detected for {template_hash}. Initiating rollback."
            )
            template_name = next(
                (
                    name
                    for name, versions in self.data["version_hashes"].items()
                    if template_hash in versions.values()
                ),
                None,
            )
            if template_name and self.data["versions"].get(template_name):
                best_version = max(
                    self.data["versions"][template_name],
                    key=lambda v: self.get_avg_score(
                        self.data["version_hashes"][template_name][v], primary_metric
                    ),
                )
                self.rollback_template(template_name, best_version)

                # REFACTORED: Use add_provenance
                add_provenance(
                    {
                        "action": "RegressionDetected",
                        "template_hash": template_hash,
                        "primary_metric": primary_metric,
                        "new_score": new_scores.get(primary_metric, 0),
                        "avg_score": avg_score,
                        "rollback_version": best_version,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "trigger": "check_for_regression",
                    }
                )

    def _get_template_content(self, template_name: str) -> str:
        """Helper to load template content."""
        path = os.path.join(TEMPLATE_DIR, template_name)
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read()
        logger.debug(f"No template found at {path}")
        return ""

    def _setup_hot_reload(self):
        """Sets up Watchdog to monitor template directory for changes."""

        class TemplateReloadHandler(FileSystemEventHandler):
            def __init__(self, tracker: "AdvancedTemplateTracker"):
                self.tracker = tracker

            def dispatch(self, event):
                if (
                    not event.is_directory
                    and event.src_path.endswith(".jinja")
                    and event.event_type in ("created", "modified", "deleted")
                ):
                    logger.info(
                        f"Template file changed: {event.src_path} (Event: {event.event_type}). Triggering reload."
                    )
                    task = asyncio.create_task(self.tracker._reload_templates())
                    task.add_done_callback(
                        lambda fut: (
                            logger.error(
                                f"Error in template reload: {fut.exception()}",
                                exc_info=True,
                            )
                            if fut.exception()
                            else None
                        )
                    )

        self.observer = Observer()
        self.observer.schedule(
            TemplateReloadHandler(self), TEMPLATE_DIR, recursive=True
        )
        self.observer.start()
        logger.info(f"Started hot-reload observer for templates in: {TEMPLATE_DIR}")

    async def _reload_templates(self):
        """Reloads templates and updates version tracking."""
        try:
            self.data = self._load()
            logger.info("Templates reloaded successfully.")
            add_provenance(
                {
                    "action": "TemplateReload",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger": "hot_reload",
                }
            )
        except Exception as e:
            logger.error(f"Failed to reload templates: {e}", exc_info=True)

    async def close(self):
        """Closes any resources held by the tracker."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self._save()
        logger.info("AdvancedTemplateTracker resources saved and closed.")
        add_provenance(
            {
                "action": "TemplateTrackerClosed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


# 3. Self-Adaptive Chains & Rich Context Injection
class AdaptivePromptDirector:
    """
    Manages state, adaptive chains, and rich context for agentic prompts.
    REFACTORED: Uses central runner logging.
    """

    def __init__(
        self, multi_vdb: MultiVectorDBManager, tracker: AdvancedTemplateTracker
    ):
        self.conversation_history = []
        self.multi_vdb = multi_vdb
        self.tracker = tracker
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html", "xml", "htm", "j2", "jinja2"]),
        )  # Enable selective autoescape for XSS protection
        self.human_review_callback: Optional[
            Callable[[str], Union[bool, Awaitable[bool]]]
        ] = None

    def set_human_review_callback(
        self, callback: Callable[[str], Union[bool, Awaitable[bool]]]
    ):
        """Sets a callback for human-in-the-loop review (sync or async)."""
        self.human_review_callback = callback
        logger.info("Human review callback set.")
        add_provenance(
            {
                "action": "HumanReviewCallbackSet",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _get_template_content(self, template_name: str) -> str:
        """
        Loads the content of a Jinja2 template.
        """
        try:
            source = self.env.loader.get_source(self.env, template_name)[0]
            return source
        except Exception as e:
            logger.error(
                f"Failed to load template {template_name}: {e}. Directory contents: {os.listdir(TEMPLATE_DIR)}"
            )
            template_file_name = (
                f"{template_name}.jinja"
                if not template_name.endswith(".jinja")
                else template_name
            )
            try:
                path = os.path.join(TEMPLATE_DIR, template_file_name)
                with open(path, "r") as f:
                    return f.read()
            except Exception as fe:
                logger.error(f"Fallback direct file load failed: {fe}")
                return ""

    def _select_template(self, candidate_names: List[str], primary_metric: str) -> str:
        """
        Selects the best template using an epsilon-greedy strategy.
        """
        candidate_hashes = {
            hashlib.sha256(self._get_template_content(name).encode()).hexdigest(): name
            for name in candidate_names
            if self._get_template_content(name)
        }
        if not candidate_hashes:
            logger.error(
                f"No valid templates found for candidates: {candidate_names}. Directory: {os.listdir(TEMPLATE_DIR)}"
            )
            raise FileNotFoundError("No valid templates found.")

        if random.random() < 0.10:  # Epsilon-greedy exploration (10% chance)
            selected_hash = random.choice(list(candidate_hashes.keys()))
        else:
            best_hash = max(
                candidate_hashes,
                key=lambda h: self.tracker.get_avg_score(h, primary_metric),
            )
            selected_hash = best_hash

        logger.debug(
            f"Selected template {candidate_hashes[selected_hash]} with hash {selected_hash}"
        )
        return self._get_template_content(candidate_hashes[selected_hash])

    def get_rich_context(
        self, repo_path: str, code_files: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Injects rich context like commits, coverage, etc.
        """
        contexts = {}
        try:
            commits = subprocess.check_output(
                ["git", "-C", repo_path, "log", "--oneline", "-n", "5"]
            ).decode()
            contexts["recent_commits"] = commits
        except Exception as e:
            logger.debug(f"No git history available: {e}")
            contexts["recent_commits"] = "No git history available."

        coverage_path = os.path.join(repo_path, "coverage.json")
        if os.path.exists(coverage_path):
            try:
                with open(coverage_path, "r") as f:
                    contexts["coverage"] = json.load(f)
            except Exception as e:
                logger.debug(f"Failed to load coverage: {e}")
                contexts["coverage"] = "No coverage report available."
        else:
            contexts["coverage"] = "No coverage report available."

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "RichContextGenerated",
                "contexts": list(contexts.keys()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "get_rich_context",
            }
        )
        return contexts

    def adapt_chain(self, prior_quality: float, threshold: float = 0.8) -> List[str]:
        """
        Dynamically adjusts chain steps based on quality.
        """
        chain = (
            ["generation", "critique", "refinement"]
            if prior_quality >= threshold
            else ["generation", "critique", "refinement", "additional_critique"]
        )
        logger.debug(f"Adapted chain based on prior_quality {prior_quality}: {chain}")

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "ChainAdapted",
                "prior_quality": prior_quality,
                "chain": chain,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "adapt_chain",
            }
        )
        return chain

    async def close(self):
        """Closes resources held by the director."""
        await self.multi_vdb.close()
        await self.tracker.close()
        logger.info("AdaptivePromptDirector resources closed.")
        add_provenance(
            {
                "action": "DirectorClosed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


# Plugin Registration
def register_prompt_builder(name: str, builder_class: type):
    """
    Registers a custom AgenticPromptBuilder subclass.
    """
    if not issubclass(builder_class, AgenticPromptBuilder):
        raise ValueError(
            f"Builder {builder_class} must be a subclass of AgenticPromptBuilder"
        )
    PROMPT_BUILDER_REGISTRY[name] = builder_class
    logger.info(f"Registered custom prompt builder: {name}")


class AgenticPromptBuilder(abc.ABC):
    """
    Abstract base for building prompts within an agentic chain.
    REFACTORED: Uses central runner logging and LLM client.
    """

    def __init__(self, director: AdaptivePromptDirector):
        self.director = director

    @abc.abstractmethod
    async def build(self, **kwargs) -> str:
        """Builds a prompt for the specified type and arguments."""
        pass

    def _advanced_sanitize(self, text: str) -> str:
        """
        Sanitizes text using predefined patterns, avoiding code variables.
        """
        pre_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # REFACTORED: Use internal helper function
        text = _local_regex_sanitize(text)

        post_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": "TextSanitized",
                "pre_hash": pre_hash,
                "post_hash": post_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "advanced_sanitize",
            }
        )
        if COMPLIANCE_MODE:
            add_provenance(
                {
                    "action": "ComplianceSensitiveData",
                    "scrubbed_fields": {
                        "text": {"pre_hash": pre_hash, "post_hash": post_hash}
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        return text

    async def _manage_tokens(self, prompt: str) -> str:
        """
        Manages token count to stay within limits using tiktoken and LLM summarization.
        REFACTORED: Replaces TokenizerService and utils.summarize_text.
        """
        try:
            # REFACTORED: Use tiktoken directly
            tokenizer = tiktoken.get_encoding("cl100k_base")
            tokens = len(tokenizer.encode(prompt))
        except Exception as e:
            logger.warning(f"Tiktoken failed: {e}, falling back to rough estimate.")
            tokens = len(prompt) // 4  # Rough estimate

        if tokens > MAX_PROMPT_TOKENS:
            logger.warning(f"Prompt over limit ({tokens}); summarizing.")

            # REFACTORED: Use runner.llm_client.call_llm_api
            time.time()
            model = "gpt-3.5-turbo"  # Fast summarizer
            try:
                summary_response = await call_llm_api(
                    prompt=f"Summarize this prompt text concisely, preserving all key instructions and context, to fit token limits:\n\n{prompt}",
                    model=model,
                )
                prompt = summary_response["content"]

                # REMOVED: Manual LLM Metrics (Handled by llm_client)
                # LLM_CALLS_TOTAL.labels(provider="testgen_prompt", model=model, task="manage_tokens_summarize").inc()
                # LLM_LATENCY_SECONDS.labels(provider="testgen_prompt", model=model, task="manage_tokens_summarize").observe(time.time() - start_time)

                new_tokens = len(tokenizer.encode(prompt))
                add_provenance(
                    {
                        "action": "PromptSummarized",
                        "original_tokens": tokens,
                        "new_tokens": new_tokens,
                        "trigger": "manage_tokens",
                    }
                )

            except Exception as e:
                logger.error(
                    f"Failed to summarize prompt for token management: {e}",
                    exc_info=True,
                )
                if not isinstance(e, LLMError):
                    LLM_ERRORS_TOTAL.labels(
                        provider="testgen_prompt",
                        model=model,
                        error_type=type(e).__name__,
                    ).inc()
                # Hard truncate as a last resort
                prompt = prompt[: MAX_PROMPT_TOKENS * 4]  # Approximate
        return prompt

    def _add_explainability(
        self,
        prompt: str,
        rationale: str,
        context_hashes: Dict[str, str],
        template_hash: str,
        chain_steps: List[str],
    ) -> str:
        """
        Adds explainability metadata to the prompt.
        """
        explainability = f"""
/* Explainability:
    Rationale: {rationale}
    Context Hashes: {json.dumps(context_hashes)}
    Template Hash: {template_hash}
    Chain Steps: {json.dumps(chain_steps)}
*/
"""
        return f"{explainability}\n{prompt}"


class DefaultPromptBuilder(AgenticPromptBuilder):
    """
    Builds prompts for test generation, critique, and refinement.
    REFACTORED: Uses central runner logging.
    """

    async def get_template_candidates(
        self, prompt_type: str, language: str, framework: str
    ) -> List[str]:
        """
        Gets candidate template files for the given prompt type, language, and framework.
        """
        candidates = []
        if language in SUPPORTED_LANGUAGES and framework in SUPPORTED_FRAMEWORKS.get(
            language, []
        ):
            candidates.append(f"{language}_{framework}_{prompt_type}.jinja")
        candidates.append(f"{language}_default_{prompt_type}.jinja")
        candidates.append(f"default_{prompt_type}.jinja")

        valid_candidates = [
            c for c in candidates if os.path.exists(os.path.join(TEMPLATE_DIR, c))
        ]
        logger.debug(
            f"Template candidates for {prompt_type}, {language}, {framework}: {valid_candidates}"
        )
        return valid_candidates

    async def build(self, prompt_type: str, **kwargs) -> str:
        """
        Builds a prompt for the specified type with rich context and sanitization.
        """
        language = kwargs.get("language", "python")
        framework = kwargs.get("test_style", "pytest")
        primary_metric = kwargs.get("primary_metric", "coverage")

        candidates = await self.get_template_candidates(
            prompt_type, language, framework
        )
        template_str = self.director._select_template(candidates, primary_metric)
        template = Template(template_str)
        template_hash = hashlib.sha256(template_str.encode()).hexdigest()

        code_to_test = "\n".join(kwargs.get("code_files", {}).values())
        rag_contexts = await self.director.multi_vdb.query_relevant_context(
            code_to_test
        )
        context_hashes = {
            k: hashlib.sha256(v.encode()).hexdigest() for k, v in rag_contexts.items()
        }
        kwargs["rag_contexts"] = rag_contexts

        repo_path = kwargs.get("repo_path", ".")
        rich_context = self.director.get_rich_context(
            repo_path, kwargs.get("code_files", {})
        )
        kwargs["rich_context"] = rich_context

        sanitized_kwargs = kwargs.copy()
        for key, value in kwargs.items():
            if isinstance(value, str):
                sanitized_kwargs[key] = self._advanced_sanitize(value)
            elif isinstance(value, dict):
                sanitized_kwargs[key] = {
                    k: self._advanced_sanitize(v) if isinstance(v, str) else v
                    for k, v in value.items()
                }

        prompt = template.render(**sanitized_kwargs)

        chain_steps = self.director.adapt_chain(kwargs.get("prior_quality", 1.0))
        rationale = f"Prompt for {prompt_type} in {language} using {framework}. Selected template based on {primary_metric} performance."
        prompt = self._add_explainability(
            prompt, rationale, context_hashes, template_hash, chain_steps
        )

        prompt = await self._manage_tokens(prompt)

        if self.director.human_review_callback and prompt_type == "generation":
            review_result = self.director.human_review_callback(prompt)
            if asyncio.iscoroutine(review_result):
                review_result = await review_result
            if not review_result:
                logger.error("Human review rejected the prompt.")
                raise ValueError("Human review rejected the prompt.")

        # REFACTORED: Use add_provenance
        add_provenance(
            {
                "action": f"{prompt_type.capitalize()} Prompt Built",
                "length": len(prompt),
                "tokens": len(tiktoken.get_encoding("cl100k_base").encode(prompt)),
                "template_hash": template_hash,
                "context_hashes": context_hashes,
                "chain_steps": chain_steps,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "build_prompt",
            }
        )
        return prompt


# Public API
multi_vdb = MultiVectorDBManager()
tracker = AdvancedTemplateTracker()
director = AdaptivePromptDirector(multi_vdb, tracker)
agentic_builder = DefaultPromptBuilder(director)


def build_agentic_prompt(prompt_type: str, builder_name: str = "test", **kwargs) -> str:
    """
    Public API to build an agentic prompt using a specified builder.
    """
    if "test" not in PROMPT_BUILDER_REGISTRY:
        register_prompt_builder("test", DefaultPromptBuilder)

    builder_class = PROMPT_BUILDER_REGISTRY.get(builder_name)
    if not builder_class:
        raise ValueError(f"Prompt builder '{builder_name}' not registered.")

    builder = builder_class(director)
    return asyncio.run(builder.build(prompt_type, **kwargs))


def initialize_codebase_for_rag(repo_path: str):
    """
    Initializes the vector database with codebase contents.
    REFACTORED: Uses central runner logging.
    """
    code_files = {}
    test_files = {}
    doc_files = {}
    dep_files = {}
    failure_logs = {}

    for root, _, files in os.walk(repo_path):
        for file in files:
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                if file.endswith((".py", ".js", ".ts", ".java", ".rs", ".go")):
                    code_files[filepath] = content
                elif "test" in file.lower() or file.lower().startswith("test_"):
                    test_files[filepath] = content
                elif file.endswith((".md", ".rst", ".txt")):
                    doc_files[filepath] = content
                elif file in ("requirements.txt", "package.json", "Cargo.toml"):
                    dep_files[filepath] = content
                elif (
                    file.lower().endswith((".log", ".txt"))
                    and "fail" in content.lower()
                ):  # FIX: Added case-insensitivity check to endswith
                    content = content[-20000:]
                    failure_logs[filepath] = content
            except Exception as e:
                logger.debug(f"Failed to read file {filepath}: {e}")
                continue

    asyncio.run(multi_vdb.add_files("codebase", code_files))
    asyncio.run(multi_vdb.add_files("tests", test_files))
    asyncio.run(multi_vdb.add_files("docs", doc_files))
    asyncio.run(multi_vdb.add_files("dependencies", dep_files))
    asyncio.run(multi_vdb.add_files("historical_failures", failure_logs))
    logger.info("Multi-RAG initialization complete. Indexed files across collections.")

    # REFACTORED: Use add_provenance
    add_provenance(
        {
            "action": "RAGInitialized",
            "repo_path": repo_path,
            "file_counts": {
                "codebase": len(code_files),
                "tests": len(test_files),
                "docs": len(doc_files),
                "dependencies": len(dep_files),
                "historical_failures": len(failure_logs),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger": "initialize_codebase_for_rag",
        }
    )


async def startup():
    """Initializes all necessary services on application startup."""
    logger.info("Initializing TestGen Prompt service components...")
    asyncio.create_task(start_health_server())
    logger.info("TestGen Prompt service components initialized.")
    add_provenance(
        {"action": "Startup", "timestamp": datetime.now(timezone.utc).isoformat()}
    )


async def shutdown():
    """Closes all connections and cleans up resources on application shutdown."""
    logger.info("Shutting down TestGen Prompt service components...")
    await director.close()
    logger.info("TestGen Prompt service components shut down.")
    add_provenance(
        {"action": "Shutdown", "timestamp": datetime.now(timezone.utc).isoformat()}
    )


async def example_human_review(prompt: str) -> bool:
    """
    Example async human review callback for prompt approval.
    """
    print("Review prompt:", prompt[:200], "...")
    return input("Approve? (y/n): ").lower() == "y"
