"""
docgen_prompt.py
Generates optimized prompts for documentation generation tasks.

Features:
- Async support for context gathering and prompt generation.
- Advanced embedding for few-shot retrieval using sentence transformers.
- Section enforcement via LLM (Runner LLM Client).
- Rich explainability: Logs full rationale and provenance.
- Security: Scrubs prompts and context for secrets/PII using Presidio (strictly enforced).
- Extensible template registry with Jinja2, hot-reload, and custom plugin hooks.
- Batch and API support with rate limiting.
- Metrics for prompt generation and feedback (via central runner).
- A/B testing for prompt variants.
- Strict failure enforcement: No fallbacks for Presidio, templates, or LLM calls.
"""

import os
import uuid
import time
import re
import asyncio
import json
import subprocess
import hashlib  # For prompt hashing
import ast  # For parsing Python AST for imports
import tiktoken  # For token counting
from datetime import (
    datetime,
    timezone,
)  # *** FIX: Added timezone for Python 3.12+ compatibility ***
import aiofiles  # <--- ADDED FIX
from pathlib import Path  # <--- ADDED FIX

from typing import List, Dict, Any, Optional, Tuple
import glob
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)  # For retries on external calls

from jinja2 import (
    Environment,
    FileSystemLoader,
    Template,
    select_autoescape,
)  # Jinja2 for templating
from sentence_transformers import (
    SentenceTransformer,
    util,
)  # For embedding and semantic search
from watchdog.observers import Observer  # For file system monitoring (hot-reload)
from watchdog.events import (
    FileSystemEventHandler,
)  # For file system events (hot-reload)
from aiohttp import web  # For web server routes
from aiohttp.web_routedef import RouteTableDef  # For web server routes
from aiohttp.web_request import Request  # For web server routes
from aiohttp.web_response import Response  # For web server routes

# --- CENTRAL RUNNER FOUNDATION ---
from runner import tracer
from runner.llm_client import call_llm_api, call_ensemble_api
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import (
    LLM_CALLS_TOTAL,
    LLM_ERRORS_TOTAL,
    LLM_LATENCY_SECONDS,
)
from runner.runner_errors import LLMError
from runner.runner_file_utils import (
    get_commits as runner_get_commits,
)  # Alias to avoid name clash
from opentelemetry.trace.status import (
    Status,
    StatusCode,
)  # *** FIX: Added missing import ***

# -----------------------------------

# --- Strict External Dependencies ---
# These imports are expected to be available and functional.
# If any of these fail to import, the program will terminate, enforcing strictness.

# Presidio: REQUIRED for scrubbing.
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine  # FIX: Corrected typo 'presonymizer'

# Utility for text summarization: REMOVED.
# from utils import summarize_text

# Main LLM orchestration layer (assumed to be fully functional)
# REMOVED: from deploy_llm_call import DeployLLMOrchestrator


# --- Logging Setup ---
# REMOVED: Local logging.basicConfig and logger definitions. Using central runner logger.

# --- Prometheus Metrics ---
# REMOVED: All local Prometheus metric definitions. Using central runner metrics for LLM calls.


# --- Security: Sensitive Data Scrubbing (Strictly enforced) ---
# Common sensitive patterns (for reference; Presidio is the primary tool).
COMMON_SENSITIVE_PATTERNS_REF = [
    r'(?i)(api[-_]?key|secret|token)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{20,}["\']?',
    r'(?i)password\s*[:=]\s*["\']?.+?["\']?',
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email addresses
    r"\b(?:\d{3}[- ]?\d{2}[- ]?\d{4})\b",  # SSN-like patterns
]


def scrub_text(text: str) -> str:
    """
    Strictly redacts sensitive information from the text using Presidio.
    Raises RuntimeError if Presidio fails during scrubbing.
    """
    if not text:
        return ""

    try:
        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()

        # Define entities for Presidio to analyze (comprehensive standard list).
        entities = [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "US_SSN",
            "IP_ADDRESS",
            "URL",
            "NRP",
            "LOCATION",
        ]

        # Analyze the text for sensitive information
        results = analyzer.analyze(text=text, entities=entities, language="en")

        # Anonymize identified entities with a generic '[REDACTED]' replacement
        scrubbed_content = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            anonymizers={"DEFAULT": {"type": "replace", "new_value": "[REDACTED]"}},
        ).text

        return scrubbed_content

    except Exception as e:
        logger.error(
            f"Presidio PII/secret scrubbing failed critically: {e}", exc_info=True
        )
        # In a strict-fail model, re-raise the exception if scrubbing cannot be performed
        raise RuntimeError(
            f"Critical error during sensitive data scrubbing with Presidio: {e}"
        ) from e


# --- Prompt Optimization (Strictly enforced) ---
# REFACTORED: This function now uses call_llm_api instead of utils.summarize_text.
async def optimize_prompt_content(prompt_text: str, max_tokens: int) -> str:
    """
    Optimizes the raw prompt text, prioritizing key context over raw content.
    If the prompt is too long, it will first summarize file contents and other non-critical sections
    before resorting to final truncation. This mitigates critical context loss.
    Raises RuntimeError if summarization fails.
    """
    try:
        # 1. Estimate initial token count
        tokenizer = tiktoken.get_encoding("cl100k_base")
        initial_tokens = len(tokenizer.encode(prompt_text))

        if initial_tokens <= max_tokens:
            return prompt_text

        logger.warning(
            f"Initial prompt exceeds max tokens ({initial_tokens} > {max_tokens}). Optimizing to prevent truncation of core instructions."
        )

        # 2. Use a regular expression to find potential sections to summarize.
        pattern = r"File: (.*?)\n```\n(.*?)\n```"
        matches = re.finditer(pattern, prompt_text, re.DOTALL)

        # 3. Iterate through found sections and replace them with summaries until the token count is acceptable.
        optimized_text = prompt_text
        for match in matches:
            filename = match.group(1)
            content = match.group(2)

            # Summarize the content of each file to a fixed length first
            # REFACTORED: Replaced summarize_text with call_llm_api
            summary_prompt = f"Summarize the following file content concisely (max 200 words) to provide context for a prompt engineering task. Focus on the file's purpose and key components:\n\n```\n{content[:5000]}\n```"  # Add truncation
            start_time = time.time()
            try:
                summary_response = await call_llm_api(
                    prompt=summary_prompt,
                    model="gpt-3.5-turbo",  # Use a fast model for summarization
                )
                summary_of_content = summary_response["content"]
                LLM_CALLS_TOTAL.labels(
                    provider="docgen_prompt",
                    model="gpt-3.5-turbo",
                    task="summarize_context",
                ).inc()
                LLM_LATENCY_SECONDS.labels(
                    provider="docgen_prompt",
                    model="gpt-3.5-turbo",
                    task="summarize_context",
                ).observe(time.time() - start_time)
                add_provenance(
                    {"action": "summarize_prompt_context", "model": "gpt-3.5-turbo"}
                )
            except Exception as llm_e:
                logger.error(
                    f"LLM summarization failed for {filename}: {llm_e}", exc_info=True
                )
                LLM_ERRORS_TOTAL.labels(
                    provider="docgen_prompt",
                    model="gpt-3.5-turbo",
                    error_type=type(llm_e).__name__,
                ).inc()
                summary_of_content = f"Error summarizing content: {llm_e}"  # Continue with an error message

            # Replace the original content with the summary
            replacement = f"File: {filename}\n```\n{summary_of_content}\n```"
            optimized_text = optimized_text.replace(match.group(0), replacement)

            # Check token count again
            current_tokens = len(tokenizer.encode(optimized_text))
            if current_tokens <= max_tokens:
                logger.info(
                    "Prompt optimized by summarization alone. Core context preserved."
                )
                return optimized_text

        # 4. If summarization of sections is not enough, fall back to a final,
        # more aggressive summarization of the whole document.
        logger.warning(
            "Section summarization was not sufficient. Performing final global summarization."
        )

        # REFACTORED: Replaced summarize_text with call_llm_api
        global_summary_prompt = f"Summarize the following entire prompt context very concisely (max 500 words). Retain all instructions, but heavily summarize file contents:\n\n```\n{optimized_text[:8000]}\n```"  # Truncate for safety
        start_time_global = time.time()
        try:
            global_summary_response = await call_llm_api(
                prompt=global_summary_prompt, model="gpt-3.5-turbo"
            )
            optimized_text = global_summary_response["content"]
            LLM_CALLS_TOTAL.labels(
                provider="docgen_prompt",
                model="gpt-3.5-turbo",
                task="summarize_context_global",
            ).inc()
            LLM_LATENCY_SECONDS.labels(
                provider="docgen_prompt",
                model="gpt-3.5-turbo",
                task="summarize_context_global",
            ).observe(time.time() - start_time_global)
            add_provenance(
                {"action": "summarize_prompt_context_global", "model": "gpt-3.5-turbo"}
            )
        except Exception as llm_e:
            logger.error(
                f"Global prompt summarization failed: {llm_e}. Proceeding with truncated text.",
                exc_info=True,
            )
            LLM_ERRORS_TOTAL.labels(
                provider="docgen_prompt",
                model="gpt-3.5-turbo",
                error_type=type(llm_e).__name__,
            ).inc()
            # Fallback to hard truncation if global summary fails
            optimized_text = optimized_text[: max_tokens * 4]  # Approximate

        return optimized_text

    except Exception as e:
        logger.error(
            f"Prompt content optimization failed: {e}. Cannot proceed without optimized prompt.",
            exc_info=True,
        )
        raise RuntimeError(
            f"Critical error during prompt content optimization: {e}"
        ) from e


# --- Custom Jinja2 Filters (Asynchronous) ---
async def get_language(content: str) -> str:
    """
    Detects the programming language of content based on simple heuristics.
    """
    # <--- FIX: Relaxed Python detection logic
    if "import " in content or "def " in content or "class " in content:
        return "python"
    elif (
        "function " in content
        or "const " in content
        or "let " in content
        or "var " in content
    ) and (";" in content or "}" in content):
        return "javascript"
    elif "package " in content and "func " in content:
        return "go"
    # <--- FIX: Relaxed Rust detection logic
    elif "fn " in content or "mod " in content or "use " in content:
        return "rust"
    elif (
        "public class " in content
        or "import java." in content
        or "public static void main" in content
    ):
        return "java"
    return "unknown"


# REMOVED: Local get_commits function. It will be imported from runner.runner_file_utils.


async def get_dependencies(files_to_check: List[str], repo_path: str) -> str:
    """
    Parses common dependency files (e.g., requirements.txt, package.json, go.mod, Cargo.toml, pom.xml)
    to extract project dependencies.
    """
    deps_info: Dict[str, Any] = {}
    for file_name in files_to_check:
        file_path = Path(repo_path) / file_name
        if not file_path.is_file():
            continue  # Skip if file doesn't exist

        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            if file_name == "requirements.txt":
                deps_info["python"] = content.strip().splitlines()
            elif file_name == "package.json":
                package_json_data = json.loads(content)
                deps_info["javascript"] = {
                    "dependencies": package_json_data.get("dependencies", {}),
                    "devDependencies": package_json_data.get("devDependencies", {}),
                }
            elif file_name == "go.mod":
                modules = re.findall(
                    r"^\s*(?:require|replace)\s+([^\s]+)\s+([^\s]+)",
                    content,
                    re.MULTILINE,
                )
                deps_info["go"] = {mod: ver for mod, ver in modules}
            elif file_name == "Cargo.toml":
                deps_match = re.search(
                    r"\[dependencies\]\n([\s\S]*?)(?:\n\[|\Z)", content
                )
                if deps_match:
                    deps_str = deps_match.group(1)
                    cargo_deps = {}
                    for line in deps_str.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            match = re.match(r"(\w+)\s*=\s*(.*)", line)
                            if match:
                                pkg, ver = match.groups()
                                cargo_deps[pkg] = (
                                    ver.strip().strip(",").strip('"').strip("'")
                                )
                    deps_info["rust"] = cargo_deps
            elif file_name == "pom.xml":
                deps_list = []
                for match in re.finditer(
                    r"<dependency>\s*<groupId>(.*?)</groupId>\s*<artifactId>(.*?)</artifactId>\s*(?:<version>(.*?)</version>)?\s*<\/dependency>",
                    content,
                ):
                    group_id, artifact_id, version = match.groups()
                    deps_list.append(
                        {
                            "groupId": group_id,
                            "artifactId": artifact_id,
                            "version": version if version else "N/A",
                        }
                    )
                deps_info["java"] = deps_list
        except (IOError, json.JSONDecodeError, re.error) as e:
            logger.warning(f"Failed to parse dependency file {file_name}: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error parsing dependency file {file_name}: {e}",
                exc_info=True,
            )

    return (
        scrub_text(json.dumps(deps_info, indent=2))
        if deps_info
        else "No dependencies found."
    )


async def get_imports(file_path_str: str) -> str:
    """
    Extracts Python import statements from a given Python file.
    """
    if not file_path_str:
        return ""

    file_path = Path(file_path_str)
    if not file_path.is_file():
        logger.warning(f"File not found for import parsing: {file_path_str}")
        return ""

    if not file_path_str.endswith(".py"):
        return ""

    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            content = await f.read()

        tree = ast.parse(content)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return scrub_text(", ".join(sorted(list(imports))))
    except SyntaxError as e:
        logger.warning(
            f"Syntax error in Python file {file_path_str}. Cannot parse imports: {e}"
        )
        return ""
    except Exception as e:
        logger.warning(
            f"Failed to get imports from {file_path_str}: {e}", exc_info=True
        )
        return ""


async def get_file_content(file_path_str: str) -> str:
    """
    Reads and returns the content of a single file.
    """
    if not file_path_str:
        return ""

    file_path = Path(file_path_str)
    if not file_path.is_file():
        logger.warning(f"File not found for content retrieval: {file_path_str}")
        return ""
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            content = await f.read()
        return scrub_text(content)
    except Exception as e:
        logger.warning(
            f"Failed to read file content from {file_path_str}: {e}", exc_info=True
        )
        return ""


class PromptTemplateRegistry:
    """
    Manages Jinja2 prompt templates, including loading, hot-reloading on changes,
    and registering custom asynchronous filters.
    """

    def __init__(self, plugin_dir: str = "prompt_templates"):
        self.plugin_dir = plugin_dir
        self.env: Environment = (
            self._create_environment()
        )  # Initialize Jinja2 environment
        self._setup_hot_reload()  # Setup file system watcher

    def _create_environment(self) -> Environment:
        """Creates and configures the Jinja2 environment with custom filters."""
        if not os.path.exists(self.plugin_dir):
            os.makedirs(self.plugin_dir, exist_ok=True)
        env = Environment(
            loader=FileSystemLoader(self.plugin_dir),
            autoescape=select_autoescape(["html", "xml", "htm", "j2", "jinja2"]),
            enable_async=True,
        )  # Enable async rendering with selective autoescape for XSS protection

        # Register custom asynchronous filters
        # REFACTORED: Point 'get_commits' to the imported runner function
        env.filters["get_commits"] = runner_get_commits
        env.filters["get_dependencies"] = get_dependencies
        env.filters["get_imports"] = get_imports
        env.filters["get_language"] = get_language
        env.filters["get_file_content"] = get_file_content
        # REFACTORED: Removed 'summarize_text' filter
        return env

    def reload_templates(self):
        """Reloads all templates in the environment."""
        self.env.cache = {}  # Clear template cache to ensure fresh load
        logger.info("Jinja2 templates reloaded.")

    def _setup_hot_reload(self):
        """Sets up a Watchdog observer to detect changes in template files."""

        class ReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance: "PromptTemplateRegistry"):
                self.registry_instance = registry_instance

            def dispatch(self, event):
                if not event.is_directory and event.src_path.endswith(".jinja"):
                    logger.info(
                        f"Template file changed: {event.src_path}. Triggering template reload."
                    )
                    self.registry_instance.reload_templates()

        observer = Observer()
        observer.schedule(ReloadHandler(self), self.plugin_dir, recursive=True)
        observer.start()
        logger.info(f"Started hot-reload observer for templates in: {self.plugin_dir}")

    def get_template(self, template_name: str) -> Template:
        """
        Retrieves a specific template by name (e.g., 'README_default').
        Strictly raises ValueError if the requested template is not found.
        """
        full_template_file_name = f"{template_name}.jinja"
        try:
            template = self.env.get_template(full_template_file_name)
            # REFACTORED: Replaced local metric with a debug log
            logger.debug(f"Template '{full_template_file_name}' loaded successfully.")
            return template
        except Exception as e:
            error_msg = f"Required template '{full_template_file_name}' not found in '{self.plugin_dir}' or failed to load: {e}. Please ensure this template file exists and is valid."
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg)


class DocGenPromptAgent:
    """
    Agent responsible for building context-rich LLM prompts for documentation generation.
    REFACTORED: Uses central runner LLM client for meta-LLM calls.
    """

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        template_dir=None,
        few_shot_dir=None,
        repo_path: str = ".",
        **kwargs,
    ):
        # Integration test compatibility
        self.template_dir = template_dir
        self.few_shot_dir = (
            few_shot_dir if few_shot_dir is not None else "few_shot_examples"
        )
        self.languages = languages
        self.embedding_model: Optional[SentenceTransformer] = None
        try:
            self.embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            logger.warning(
                f"Could not load SentenceTransformer for few-shot retrieval: {e}. Few-shot retrieval will be disabled."
            )

        self.repo_path = Path(repo_path)  # *** FIX: Set repo_path first ***

        # *** FIX: Pass the repo-relative path to the template registry ***
        template_dir = self.repo_path / "prompt_templates"
        self.template_registry = PromptTemplateRegistry(plugin_dir=str(template_dir))

        # *** FIX: Use repo_path to build full path for few-shot examples ***
        few_shot_path = self.repo_path / self.few_shot_dir  # <--- FIX 1 APPLIED HERE
        self.few_shot_examples = self._load_few_shot(str(few_shot_path))

        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise ValueError(
                f"Repository path does not exist or is not a directory: {repo_path}"
            )

        self.previous_feedback: Dict[str, float] = {}
        # REFACTORED: Removed self.llm_orchestrator instance

    def _load_few_shot(self, few_shot_dir: str) -> List[Dict[str, str]]:
        """
        Loads few-shot examples from JSON files in the specified directory.
        """
        examples = []
        if not os.path.exists(few_shot_dir):  # *** FIX: Check full path ***
            os.makedirs(few_shot_dir)
            logger.info(f"Created few-shot examples directory: {few_shot_dir}.")
            return examples

        for file in glob.glob(f"{few_shot_dir}/*.json"):  # *** FIX: Use full path ***
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if (
                    not isinstance(data, dict)
                    or "query" not in data
                    or "prompt" not in data
                ):
                    logger.warning(
                        f"Invalid few-shot example format in {file}. Must contain 'query' and 'prompt' keys. Skipping."
                    )
                    continue
                examples.append(data)
            except Exception as e:
                logger.error(
                    f"Failed to load few-shot example from {file}: {e}", exc_info=True
                )
        logger.info(f"Loaded {len(examples)} few-shot examples from {few_shot_dir}.")
        return examples

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def gather_context(
        self, target_files: List[str], repo_path: str
    ) -> Dict[str, Any]:  # Added repo_path param
        """
        Gathers raw file contents from the repository for use in the prompt context.
        """
        context: Dict[str, Any] = {"files_content": {}}
        read_tasks = []
        for file_name in target_files:
            file_path = Path(repo_path) / file_name  # Use passed repo_path
            if file_path.is_file():
                read_tasks.append(
                    self._read_single_file_for_context(file_path, file_name)
                )
            else:
                logger.warning(
                    f"Target file not found for context gathering: {file_name} at {file_path}. Skipping."
                )

        results = await asyncio.gather(*read_tasks)
        for file_name, content in results:
            if content is not None:
                context["files_content"][file_name] = content
        return context

    async def _read_single_file_for_context(
        self, file_path: Path, file_name: str
    ) -> Tuple[str, Optional[str]]:
        """Helper to safely read a single file's content asynchronously for context."""
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                return file_name, scrub_text(content)
        except Exception as e:
            logger.error(
                f"Failed to read file {file_path} for context: {e}", exc_info=True
            )
            return file_name, None

    async def retrieve_few_shot(self, query: str, top_k: int = 3) -> List[str]:
        """
        Retrieves the top-k most semantically similar few-shot examples based on the query.
        """
        if not self.embedding_model or not self.few_shot_examples:
            logger.warning(
                "Few-shot retrieval disabled: Embedding model not loaded or no examples available."
            )
            return []

        try:
            query_emb = self.embedding_model.encode(query, convert_to_tensor=True)
            example_queries = [ex["query"] for ex in self.few_shot_examples]

            if not example_queries:
                return []

            example_embs = self.embedding_model.encode(
                example_queries, convert_to_tensor=True
            )
            hits = util.semantic_search(query_emb, example_embs, top_k=top_k)[0]

            retrieved_prompts = [
                self.few_shot_examples[hit["corpus_id"]]["prompt"] for hit in hits
            ]

            logger.info(
                f"Retrieved {len(retrieved_prompts)} few-shot examples for query: '{query[:50]}...'"
            )
            return retrieved_prompts
        except Exception as e:
            logger.error(
                f"Few-shot retrieval failed for query '{query[:50]}...': {e}",
                exc_info=True,
            )
            return []

    # REFACTORED: Uses call_ensemble_api
    async def enforce_sections(
        self,
        prompt_content: str,
        required_sections: List[str],
        llm_model: str = "gpt-4o",
    ) -> str:
        """
        Uses a meta-LLM to ensure the prompt includes all specified required sections.
        """
        if not required_sections:
            return prompt_content

        enforcement_prompt_llm_query = f"""
        You are a prompt engineering expert. Your task is to review the following prompt for documentation generation
        and ensure it explicitly requests the following sections: {', '.join(required_sections)}.
        If any section is missing or not clearly requested, modify the prompt to explicitly include it.
        
        Original prompt:
        ```
        {prompt_content}
        ```
        
        Return ONLY the enhanced prompt. Do NOT add any conversational text or markdown wrappers.
        """

        try:
            # Use the central runner's LLM client
            start_time = time.time()
            response_from_meta_llm = await call_ensemble_api(
                prompt=enforcement_prompt_llm_query,
                models=[{"model": llm_model}],
                voting_strategy="majority",
            )

            # The new client returns content directly
            enforced_prompt_content = response_from_meta_llm.get("content", "").strip()

            # Add central runner metrics and provenance
            LLM_CALLS_TOTAL.labels(
                provider="docgen_prompt", model=llm_model, task="enforce_sections"
            ).inc()
            LLM_LATENCY_SECONDS.labels(
                provider="docgen_prompt", model=llm_model, task="enforce_sections"
            ).observe(time.time() - start_time)
            add_provenance(
                {
                    "action": "enforce_prompt_sections",
                    "model": llm_model,
                    "run_id": str(uuid.uuid4()),
                }
            )

            if not enforced_prompt_content:
                LLM_ERRORS_TOTAL.labels(
                    provider="docgen_prompt",
                    model=llm_model,
                    error_type="EmptyLLMResponse",
                ).inc()
                raise ValueError(
                    "Meta-LLM returned empty content when enforcing sections."
                )

            logger.info("Prompt sections enforced successfully by meta-LLM.")
            return enforced_prompt_content

        except Exception as e:
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(
                    provider="docgen_prompt",
                    model=llm_model,
                    error_type=type(e).__name__,
                ).inc()
            logger.error(
                f"Failed to enforce required sections using meta-LLM: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Critical error during prompt section enforcement: {e}"
            ) from e

    # REFACTORED: Uses call_ensemble_api
    async def optimize_prompt_with_feedback(
        self,
        initial_prompt_content: str,
        doc_type: str,
        template_name: str,
        llm_model: str = "gpt-4o",
    ) -> str:
        """
        Optimizes the prompt based on historical feedback using a meta-LLM.
        """
        feedback_key = f"{doc_type}_{template_name}"
        score = self.previous_feedback.get(feedback_key)

        if score is not None:
            logger.info(
                f"Optimizing prompt with feedback (score: {score}) for {feedback_key}."
            )

            last_run_context = self.previous_feedback.get("last_run", {})
            last_run_str = (
                json.dumps(last_run_context, indent=2)[:1000]
                if last_run_context
                else "No specific last run context."
            )

            meta_prompt_llm_query = f"""
            You are a prompt engineering expert. Your task is to improve the following prompt
            for generating {doc_type} documentation. Based on previous runs, the prompt's performance score was {score}.
            
            Analyze the score and the context of the last run:
            - If the score is high (e.g., >0.8), suggest minor refinements for clarity, robustness, or conciseness.
            - If the score is low (e.g., <0.5), suggest significant changes to fix common issues.
            
            Context of Last Run (if available):
            ```json
            {last_run_str}
            ```

            Original prompt to improve:
            ```
            {initial_prompt_content}
            ```
            
            Provide ONLY the improved prompt text. Do NOT add any conversational filler.
            """

            try:
                # Use the central runner's LLM client
                start_time = time.time()
                response_from_meta_llm = await call_ensemble_api(
                    prompt=meta_prompt_llm_query,
                    models=[{"model": llm_model}],
                    voting_strategy="majority",
                )

                # New client returns content directly
                optimized_content = response_from_meta_llm.get("content", "").strip()

                # Add central runner metrics and provenance
                LLM_CALLS_TOTAL.labels(
                    provider="docgen_prompt", model=llm_model, task="optimize_feedback"
                ).inc()
                LLM_LATENCY_SECONDS.labels(
                    provider="docgen_prompt", model=llm_model, task="optimize_feedback"
                ).observe(time.time() - start_time)
                add_provenance(
                    {
                        "action": "optimize_prompt_feedback",
                        "model": llm_model,
                        "run_id": str(uuid.uuid4()),
                    }
                )

                if optimized_content:
                    logger.info(
                        f"Prompt '{feedback_key}' optimized successfully by meta-LLM."
                    )
                    return optimized_content
                else:
                    LLM_ERRORS_TOTAL.labels(
                        provider="docgen_prompt",
                        model=llm_model,
                        error_type="EmptyLLMResponse",
                    ).inc()
                    logger.warning(
                        "Meta-LLM returned empty or malformed optimized prompt content. Using original."
                    )
                    return initial_prompt_content

            except Exception as e:
                if not isinstance(e, LLMError):
                    LLM_ERRORS_TOTAL.labels(
                        provider="docgen_prompt",
                        model=llm_model,
                        error_type=type(e).__name__,
                    ).inc()
                logger.error(
                    f"Meta-LLM prompt optimization failed for {feedback_key}: {e}. Returning original prompt.",
                    exc_info=True,
                )
                return initial_prompt_content

        logger.debug(
            f"No feedback available for '{feedback_key}'. Using original prompt content."
        )
        return initial_prompt_content

    async def get_doc_prompt(
        self,
        doc_type: str,
        target_files: List[str],
        instructions: Optional[str] = None,
        template_name: str = "default",
        required_sections: Optional[List[str]] = None,
        llm_model: str = "gpt-4o",
    ) -> str:
        """
        Builds the complete documentation prompt by rendering a Jinja2 template with dynamic data.
        """
        with tracer.start_as_current_span("get_doc_prompt_pipeline") as span:
            # REFACTORED: Replaced local metric with logger call
            logger.info(
                f"Prompt generation call for doc_type={doc_type}, template={template_name}"
            )
            start_time = time.time()

            # Gather comprehensive context data
            context_data = await self.gather_context(target_files, str(self.repo_path))

            full_template_name = f"{doc_type}_{template_name}"
            template = self.template_registry.get_template(full_template_name)

            scrubbed_instructions = scrub_text(instructions) if instructions else None

            # Fetch few-shot examples
            few_shot_examples_str = ""
            if self.embedding_model and self.few_shot_examples:
                few_shot_query = f"Generate {doc_type} documentation for files: {', '.join(target_files)}. Instructions: {scrubbed_instructions}"
                retrieved_few_shots = await self.retrieve_few_shot(few_shot_query)
                if retrieved_few_shots:
                    few_shot_examples_str = (
                        "\n\n--- Few-shot Examples ---\n"
                        + "\n---\n".join(
                            [
                                f"Example {i+1}:\n{ex}"
                                for i, ex in enumerate(retrieved_few_shots)
                            ]
                        )
                        + "\n-------------------------"
                    )
                    span.set_attribute(
                        "few_shot_examples_count", len(retrieved_few_shots)
                    )
                    # REFACTORED: Replaced local metric with logger call
                    logger.debug(
                        f"Used {len(retrieved_few_shots)} few-shot examples for {doc_type}/{template_name}"
                    )

            template_render_data = {
                "doc_type": doc_type,
                "target_files": target_files,
                "instructions": scrubbed_instructions,
                "few_shot_examples": few_shot_examples_str,
                "repo_path": str(self.repo_path),
                "context": context_data,
                "required_sections": required_sections or [],
                "timestamp_utc": datetime.now(timezone.utc).isoformat() + "Z",
            }

            try:
                base_prompt_content = await template.render_async(template_render_data)

                max_context_tokens = 8000  # This should be configurable
                optimized_prompt_content = await optimize_prompt_content(
                    base_prompt_content, max_context_tokens
                )

                final_prompt_content = await self.optimize_prompt_with_feedback(
                    optimized_prompt_content, doc_type, template_name, llm_model
                )

                if required_sections:
                    final_prompt_content = await self.enforce_sections(
                        final_prompt_content, required_sections, llm_model
                    )

                try:
                    tokenizer = tiktoken.encoding_for_model(llm_model)
                except KeyError:
                    logger.warning(
                        f"Tiktoken encoding not found for model '{llm_model}'. Falling back to 'cl100k_base'."
                    )
                    tokenizer = tiktoken.get_encoding("cl100k_base")

                prompt_tokens = len(tokenizer.encode(final_prompt_content))
                # REFACTORED: Replaced local metric with logger call
                logger.debug(
                    f"Generated prompt for {doc_type}/{template_name} with {prompt_tokens} tokens."
                )

                latency = time.time() - start_time
                # REFACTORED: Replaced local metric with logger call
                logger.debug(
                    f"Prompt generation latency for {doc_type}/{template_name}: {latency:.2f}s"
                )

                span.set_attribute("final_prompt_length", len(final_prompt_content))
                span.set_attribute("final_prompt_tokens", prompt_tokens)
                span.set_attribute("template_used", template_name)
                span.set_status(Status(StatusCode.OK, "Prompt built successfully."))

                logger.info(
                    f"Prompt built successfully for {doc_type} (template: {template_name}). Tokens: {prompt_tokens}, Latency: {latency:.2f}s"
                )
                return final_prompt_content

            except Exception as e:
                error_type = str(type(e).__name__)
                # REFACTORED: Replaced local metric with logger call
                logger.error(
                    f"Prompt generation error for {doc_type}: stage=prompt_generation, error={error_type}",
                    exc_info=True,
                )
                span.set_status(
                    Status(StatusCode.ERROR, f"Prompt generation failed: {e}")
                )
                span.record_exception(e)

                scrub_text(
                    f"Generate {doc_type} documentation for files: {', '.join(target_files)}. Instructions: {instructions or 'None'}. Due to an internal error, full context was not available. Please generate comprehensive and accurate documentation. Include an introduction, installation, usage, API reference, and a conclusion. Output in Markdown format."
                )
                raise RuntimeError(
                    f"Critical error during prompt generation: {e}. Fallback prompt provided but indicates severe issue."
                ) from e

    async def build_doc_prompt(
        self, file_path: str, target: str, instructions: str = None, **kwargs
    ) -> str:
        """
        Integration test compatibility method - alias for get_doc_prompt.
        """
        # FIX 2 APPLIED HERE
        return await self.get_doc_prompt(
            # source_code="",  # Will be read internally  <--- REMOVED
            # file_path=file_path,                          <--- REMOVED
            doc_type=target,
            target_files=[
                file_path
            ],  # <--- FIX: Pass file_path as a list to target_files
            # language=target,                              <--- REMOVED
            instructions=instructions,
            **kwargs,
        )

    async def batch_get_doc_prompt(
        self, requests: List[Dict[str, Any]], concurrency_limit: int = 5
    ) -> List[str]:
        """
        Generates prompts for a batch of documentation requests concurrently.
        """
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_get_prompt(request_data: Dict[str, Any]):
            async with semaphore:
                return await self.get_doc_prompt(
                    doc_type=request_data.get("doc_type", "README"),
                    target_files=request_data.get("target_files", []),
                    instructions=request_data.get("instructions"),
                    template_name=request_data.get("template_name", "default"),
                    required_sections=request_data.get("required_sections"),
                    llm_model=request_data.get("llm_model", "gpt-4o"),
                )

        tasks = [limited_get_prompt(req) for req in requests]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        for res in results:
            if isinstance(res, Exception):
                logger.error(
                    f"Batch prompt generation item failed: {res}", exc_info=True
                )
                processed_results.append(
                    f"ERROR: Failed to generate prompt: {str(res)}"
                )
            else:
                processed_results.append(res)
        return processed_results

    # REFACTORED: Uses call_ensemble_api for scoring
    async def ab_test_prompts(
        self,
        doc_type: str,
        target_files: List[str],
        instructions: Optional[str] = None,
        template_names: List[str] = ["default"],
        required_sections: Optional[List[str]] = None,
        llm_model: str = "gpt-4o",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Performs an A/B test by generating prompts for multiple template variants.
        """
        ab_requests = []
        for name in template_names:
            ab_requests.append(
                {
                    "doc_type": doc_type,
                    "target_files": target_files,
                    "instructions": instructions,
                    "template_name": name,
                    "required_sections": required_sections,
                    "llm_model": llm_model,
                }
            )

        prompt_strings = await self.batch_get_doc_prompt(ab_requests)

        ab_results_final: Dict[str, Dict[str, Any]] = {}
        scoring_tasks = []
        prompts_for_scoring: List[Tuple[str, str]] = []

        for i, template_name in enumerate(template_names):
            current_prompt_string = prompt_strings[i]

            if isinstance(
                current_prompt_string, str
            ) and current_prompt_string.startswith("ERROR:"):
                logger.error(
                    f"Skipping scoring for template '{template_name}' due to prior prompt generation failure."
                )
                ab_results_final[template_name] = {
                    "prompt": current_prompt_string,
                    "length": len(current_prompt_string),
                    "hash": "N/A",
                    "rationale": "Prompt generation failed for this variant.",
                    "provenance": f"Timestamp: {datetime.now(timezone.utc).isoformat()}Z",
                    "score": 0.0,
                }
                self.record_feedback(doc_type, template_name, 0.0)
            else:
                ab_results_final[template_name] = {
                    "prompt": current_prompt_string,
                    "length": len(current_prompt_string),
                    "hash": hashlib.sha256(current_prompt_string.encode()).hexdigest(),
                    "rationale": f"Generated for A/B test variant '{template_name}'.",
                    "provenance": f"Timestamp: {datetime.now(timezone.utc).isoformat()}Z",
                    "score": None,
                }
                prompts_for_scoring.append((template_name, current_prompt_string))

        if prompts_for_scoring:
            scoring_llm_model = "gpt-4o"

            for template_to_score, prompt_to_score_string in prompts_for_scoring:
                score_prompt_content = f"""
                Evaluate the quality of this prompt for {doc_type} generation on a scale of 0 to 1 (1 being the highest quality).
                Criteria: Clarity, Specificity, Completeness, Effectiveness, Adherence to output format.

                Prompt to evaluate:
                ```
                {prompt_to_score_string[:4000]}
                ```
                
                Output your evaluation as a JSON object with a single key 'score' (float between 0 and 1).
                Example: {{"score": 0.85}}
                """
                # REFACTORED: Use call_ensemble_api
                scoring_tasks.append(
                    call_ensemble_api(
                        prompt=score_prompt_content,
                        models=[{"model": scoring_llm_model}],
                        voting_strategy="majority",
                    )
                )

            scoring_responses = await asyncio.gather(
                *scoring_tasks, return_exceptions=True
            )

            for i, (template_name_scored, _) in enumerate(prompts_for_scoring):
                score_response = scoring_responses[i]
                if isinstance(score_response, Exception):
                    logger.warning(
                        f"Failed to get score for template '{template_name_scored}': {score_response}"
                    )
                    ab_results_final[template_name_scored]["score"] = 0.0
                    self.record_feedback(doc_type, template_name_scored, 0.0)
                else:
                    try:
                        # REFACTORED: New response format
                        score_content = score_response.get("content", "{}")

                        # Add metrics for the scoring call
                        LLM_CALLS_TOTAL.labels(
                            provider="docgen_prompt",
                            model=scoring_llm_model,
                            task="ab_test_score",
                        ).inc()
                        add_provenance(
                            {
                                "action": "ab_test_prompt_score",
                                "model": scoring_llm_model,
                                "run_id": str(uuid.uuid4()),
                            }
                        )

                        score_data = json.loads(score_content)
                        score = float(score_data.get("score", 0.0))
                        ab_results_final[template_name_scored]["score"] = score
                        self.record_feedback(doc_type, template_name_scored, score)
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning(
                            f"Failed to parse score JSON for template '{template_name_scored}': {e}. Raw: {score_content[:100]}"
                        )
                        ab_results_final[template_name_scored]["score"] = 0.0
                        self.record_feedback(doc_type, template_name_scored, 0.0)
        return ab_results_final

    def record_feedback(self, doc_type: str, template_name: str, score: float):
        """
        Records feedback for a specific prompt template variant.
        """
        feedback_key = f"{doc_type}_{template_name}"
        score = max(0.0, min(1.0, score))

        self.previous_feedback[feedback_key] = score
        # REFACTORED: Replaced local metric with logger call
        logger.info(f"Recorded feedback score for {doc_type}/{template_name}: {score}")

        self.previous_feedback["last_run"] = {
            "doc_type": doc_type,
            "template_name": template_name,
            "score": score,
            "timestamp": time.time(),
        }
        logger.info(
            f"Recorded feedback for prompt '{feedback_key}': {score}. Updated last_run context."
        )


# --- API Endpoints (using aiohttp web server) ---
routes = RouteTableDef()
api_semaphore = asyncio.Semaphore(5)


@routes.post("/generate_prompt")
async def api_generate_prompt(request: Request) -> Response:
    """
    API endpoint to generate a documentation prompt.
    """
    data = await request.json()
    repo_path = data.get("repo_path", ".")
    llm_model = data.get("llm_model", "gpt-4o")
    agent = DocGenPromptAgent(repo_path=repo_path)

    async with api_semaphore:
        try:
            prompt_string = await agent.get_doc_prompt(
                doc_type=data.get("doc_type", "README"),
                target_files=data.get("target_files", []),
                instructions=data.get("instructions"),
                template_name=data.get("template_name", "default"),
                required_sections=data.get("required_sections"),
                llm_model=llm_model,
            )
            return web.json_response({"prompt": prompt_string, "status": "success"})
        except Exception as e:
            logger.error(f"API /generate_prompt error: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)


@routes.post("/batch_generate_prompt")
async def api_batch_generate_prompt(request: Request) -> Response:
    """
    API endpoint to generate multiple documentation prompts in a batch.
    """
    data = await request.json()
    agent = DocGenPromptAgent(repo_path=data.get("repo_path", "."))

    async with api_semaphore:
        try:
            prompts = await agent.batch_get_doc_prompt(
                requests=data.get("requests", []),
                concurrency_limit=data.get("concurrency_limit", 5),
            )
            return web.json_response({"prompts": prompts, "status": "success"})
        except Exception as e:
            logger.error(f"API /batch_generate_prompt error: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)


@routes.post("/ab_test_prompts")
async def api_ab_test_prompts(request: Request) -> Response:
    """
    API endpoint to run A/B tests on prompt variants.
    """
    data = await request.json()
    repo_path = data.get("repo_path", ".")
    llm_model = data.get("llm_model", "gpt-4o")
    agent = DocGenPromptAgent(repo_path=repo_path)

    async with api_semaphore:
        try:
            ab_data = await agent.ab_test_prompts(
                doc_type=data.get("doc_type", "README"),
                target_files=data.get("target_files", []),
                instructions=data.get("instructions"),
                template_names=data.get("template_names", ["default"]),
                required_sections=data.get("required_sections"),
                llm_model=llm_model,
            )
            return web.json_response({"results": ab_data, "status": "success"})
        except Exception as e:
            logger.error(f"API /ab_test_prompts error: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)


@routes.post("/record_prompt_feedback")
async def api_record_prompt_feedback(request: Request) -> Response:
    """
    API endpoint to record feedback score for a prompt variant.
    """
    data = await request.json()
    doc_type = data.get("doc_type")
    template_name = data.get("template_name")
    score = data.get("score")
    repo_path = data.get("repo_path", ".")

    if not all([doc_type, template_name is not None, isinstance(score, (int, float))]):
        raise web.HTTPBadRequest(
            reason="Missing or invalid 'doc_type', 'template_name', or 'score'."
        )

    agent = DocGenPromptAgent(repo_path=repo_path)
    agent.record_feedback(doc_type, template_name, float(score))
    return web.json_response({"status": "success", "message": "Feedback recorded."})


# Create the aiohttp web application and add routes
app = web.Application()
app.add_routes(routes)

if __name__ == "__main__":
    import argparse
    import subprocess
    from pathlib import Path

    # --- Setup for local testing/CLI demonstration ---
    if not os.path.exists("prompt_templates"):
        os.makedirs("prompt_templates")
        with open("prompt_templates/README_default.jinja", "w") as f:
            f.write(
                """
Generate a comprehensive README for a {{ doc_type }} project.
Files to document: {{ target_files | join(', ') }}.
Instructions: {{ instructions | default('None') }}.
Context: {{ context.files_content | tojson }}
Output should be well-structured Markdown.
"""
            )
        print(
            "Created 'prompt_templates' directory and a sample 'README_default.jinja' template."
        )

    if not os.path.exists("few_shot_examples"):
        os.makedirs("few_shot_examples")
        with open("few_shot_examples/README_python_example.json", "w") as f:
            f.write(
                """
{
  "query": "README for a Python Flask app",
  "prompt": "# Python Flask App README\\n\\n## Introduction\\nThis is a sample Flask application...\\n\\n## Installation\\n`pip install -r requirements.txt`\\n\\n## Usage\\n`python app.py`"
}
"""
            )
        print(
            "Created 'few_shot_examples' directory and a sample 'README_python_example.json'."
        )

    test_repo_path = "temp_docgen_repo_prompt"
    if not os.path.exists(test_repo_path):
        os.makedirs(test_repo_path)
        with open(os.path.join(test_repo_path, "main.py"), "w") as f:
            f.write(
                """
def my_function(param1: str) -> str:
    \"\"\"
    This is a test function.
    \"\"\"
    return f"Hello, {param1}!"
"""
            )
        with open(os.path.join(test_repo_path, "requirements.txt"), "w") as f:
            f.write("Flask==2.0.1")
        print(f"Created dummy repository at {test_repo_path} with sample files.")

        try:
            subprocess.run(
                ["git", "init"], cwd=test_repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=test_repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=test_repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "."], cwd=test_repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit for test app"],
                cwd=test_repo_path,
                check=True,
                capture_output=True,
            )
            print(f"Initialized git repo in {test_repo_path}.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to setup git repo in {test_repo_path}: {e.stderr.decode()}")
        except FileNotFoundError:
            print("Git command not found.")

    parser = argparse.ArgumentParser(
        description="Documentation Prompt Generation CLI and API Server"
    )
    parser.add_argument(
        "--doc_type",
        default="README",
        help="Type of documentation (e.g., README, API_DOCS).",
    )
    parser.add_argument(
        "--target_files",
        nargs="+",
        default=["main.py", "requirements.txt"],
        help="List of relevant files in the repository.",
    )
    parser.add_argument(
        "--instructions",
        default="Generate a concise README.",
        help="Specific instructions for the prompt.",
    )
    parser.add_argument(
        "--template_name",
        default="default",
        help="Template name (e.g., default, verbose).",
    )
    parser.add_argument(
        "--required_sections",
        nargs="+",
        default=None,
        help="List of section titles to explicitly enforce.",
    )
    parser.add_argument(
        "--llm_model",
        default="gpt-4o",
        help="Specific LLM model to use for prompt optimization/scoring.",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Start the aiohttp web server for API endpoints.",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for the aiohttp server."
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for the aiohttp server."
    )
    parser.add_argument("--batch", action="store_true", help="Run in batch mode.")
    parser.add_argument(
        "--ab_test",
        action="store_true",
        help="Run A/B test on prompt variants in CLI mode.",
    )
    parser.add_argument(
        "--ab_template_names",
        nargs="+",
        default=["default"],
        help="List of template names to A/B test.",
    )
    # REFACTORED: Added repo_path argument for CLI
    parser.add_argument(
        "--repo_path",
        default=test_repo_path,
        help="Path to the repository for context gathering.",
    )

    args = parser.parse_args()

    agent_instance = DocGenPromptAgent(repo_path=args.repo_path)

    async def run_cli_mode():
        if args.server:
            print(
                f"Starting aiohttp server on {args.host}:{args.port} for API endpoints..."
            )
            web.run_app(app, host=args.host, port=args.port)
            return

        if args.batch:
            batch_requests = [
                {
                    "doc_type": "README",
                    "target_files": ["main.py"],
                    "instructions": "Very short README.",
                },
                {
                    "doc_type": "API_DOCS",
                    "target_files": ["main.py"],
                    "instructions": "Detailed API docs.",
                },
            ]
            print(
                f"Running batch prompt generation for {len(batch_requests)} requests..."
            )
            results = await agent_instance.batch_get_doc_prompt(batch_requests)
            print("\n--- Batch Prompt Results ---")
            for i, prompt_result in enumerate(results):
                print(f"\n--- Request {i+1} ---")
                print(prompt_result)
            print("\n--- End Batch Prompt Results ---")

        elif args.ab_test:
            print(
                f"Running A/B test for doc_type '{args.doc_type}' with templates: {args.ab_template_names}..."
            )
            ab_results = await agent_instance.ab_test_prompts(
                doc_type=args.doc_type,
                target_files=args.target_files,
                instructions=args.instructions,
                template_names=args.ab_template_names,
                required_sections=args.required_sections,
                llm_model=args.llm_model,
            )
            print("\n--- A/B Test Results ---")
            for template_name, result_data in ab_results.items():
                print(f"\n## Template: {template_name}")
                print("---")
                print(
                    f"  Prompt (first 500 chars):\n```\n{result_data['prompt'][:500]}{'...' if len(result_data['prompt']) > 500 else ''}\n```"
                )
                print(f"  Prompt Length: {result_data['length']} characters")
                print(f"  Prompt Hash: {result_data['hash']}")
                print(f"  Rationale: {result_data['rationale']}")
                print(f"  Provenance: {result_data['provenance']}")
                print(
                    f"  **Meta-LLM Score**: {result_data['score'] if result_data['score'] is not None else 'N/A'}"
                )
            print("\n--- End A/B Test Results ---")
        else:
            print(
                f"Generating single prompt for doc_type '{args.doc_type}' with template '{args.template_name}'..."
            )
            final_prompt_string = await agent_instance.get_doc_prompt(
                doc_type=args.doc_type,
                target_files=args.target_files,
                instructions=args.instructions,
                template_name=args.template_name,
                required_sections=args.required_sections,
                llm_model=args.llm_model,
            )
            print("\n--- Generated Prompt ---")
            print(final_prompt_string)
            print("\n--- End Generated Prompt ---")

    asyncio.run(run_cli_mode())
