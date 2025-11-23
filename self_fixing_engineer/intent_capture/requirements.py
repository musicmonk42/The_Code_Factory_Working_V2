#
# Abbreviations Glossary:
# DB: Database
# ML: Machine Learning
# REQ: Requirements
# P1-P6: Production Priorities (P1: Input Validation, P2: Secrets, etc.)
# LLM: Large Language Model
# OTLP: OpenTelemetry Protocol
# UUID: Universally Unique Identifier
#
"""
Production-ready requirements management module (v1.2.0 - Enhanced UUID validation and rate limiting).

This module provides a comprehensive suite of tools for handling software requirements checklists.
It features a hybrid persistence model with a primary PostgreSQL database and a file-based fallback,
integrates Machine Learning (ML) for intelligent requirement suggestion, and is built with production-grade
features such as asynchronous operations, connection pooling, caching, retries, and detailed observability
(metrics and tracing).
"""

__version__ = "1.2.0"

import json
import os
import asyncio  # For async DB ops and ML model loading
from typing import List, Dict, Any, Optional
import logging
import datetime
import threading
import uuid
import io
import re  # For input sanitization
import time
import sys
import atexit  # For resource cleanup on shutdown

# Install with 'pip install aiofiles==24.1.0'
try:
    import aiofiles
except ImportError:
    aiofiles = None  # Will be checked before use

# P6: Tenacity for retries
# Install with 'pip install tenacity==9.1.2'
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    retry_if_exception_type,
)

# P4: Caching for embeddings and DB queries
# Install with 'pip install cachetools==6.1.0'
try:
    from cachetools import TTLCache, cached

    CACHETOOLS_AVAILABLE = True
except ImportError:
    TTLCache = None

    def cached(*args, **kwargs):
        return lambda func: func  # Dummy decorator

    CACHETOOLS_AVAILABLE = False

# P5: Observability: Prometheus Metrics
# Install with 'pip install prometheus-client==0.22.1'
try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server

    PROMETHEUS_AVAILABLE = True
    # Metrics for requirements suggestions
    REQ_SUGGESTIONS_TOTAL = Counter(
        "req_suggestions_total", "Total requirement suggestions", ["domain", "status"]
    )
    REQ_SUGGESTIONS_LATENCY_SECONDS = Histogram(
        "req_suggestions_latency_seconds",
        "Requirement suggestion latency in seconds",
        ["domain"],
    )
    # Metrics for DB operations
    DB_OPS_TOTAL = Counter(
        "req_db_ops_total", "Total DB operations", ["operation", "status"]
    )
    DB_OPS_LATENCY_SECONDS = Histogram(
        "req_db_ops_latency_seconds", "DB operation latency in seconds", ["operation"]
    )
    # Metrics for ML model loading
    ML_MODEL_LOAD_LATENCY_SECONDS = Histogram(
        "req_ml_model_load_latency_seconds", "ML model load latency in seconds"
    )
except ImportError:
    PROMETHEUS_AVAILABLE = False
    REQ_SUGGESTIONS_TOTAL, REQ_SUGGESTIONS_LATENCY_SECONDS = None, None
    DB_OPS_TOTAL, DB_OPS_LATENCY_SECONDS = None, None
    ML_MODEL_LOAD_LATENCY_SECONDS = None

# P5: Observability: OpenTelemetry Tracing
# Install with 'pip install opentelemetry-sdk==1.36.0'
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    OPENTELEMETRY_AVAILABLE = True
    # Initialize OpenTelemetry
    resource = Resource(attributes={SERVICE_NAME: "requirements-module"})
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(ConsoleSpanExporter())
    )  # Use ConsoleSpanExporter for local debugging
    trace.set_tracer_provider(trace_provider)
    # Fixed: Use correct number of arguments for get_tracer()
    tracer = trace.get_tracer(
        instrumenting_module_name=__name__, instrumenting_library_version=__version__
    )
except ImportError:
    tracer = None
    OPENTELEMETRY_AVAILABLE = False

# --- Production-Ready Dependency Documentation & Imports ---
# --- Database Dependencies (required for DB-backed checklists) ---
# Install with 'pip install asyncpg==0.30.0'
try:
    import asyncpg  # P3: Use asyncpg for async PostgreSQL operations

    DB_AVAILABLE = True
except ImportError:
    asyncpg = None
    DB_AVAILABLE = False
    logging.warning(
        "asyncpg not found. PostgreSQL integration will be skipped. Install with 'pip install asyncpg==0.30.0'."
    )

# --- Redis Dependencies ---
# Install with 'pip install redis[async]==6.4.0'
try:
    import redis.asyncio as redis  # P3: Use async Redis client

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False
    logging.warning(
        "redis.asyncio not found. Redis features will be skipped. Install with 'pip install redis[async]==6.4.0'."
    )

# --- ML/AI Dependencies (for requirements suggestion/embedding) ---
# Install with 'pip install sentence-transformers==5.1.0'
# Install with 'pip install torch==2.8.0' (dependency for sentence-transformers)
try:
    from sentence_transformers import SentenceTransformer, util

    ML_ENABLED = True
except ImportError:
    SentenceTransformer = None
    util = None
    ML_ENABLED = False
    logging.warning(
        "sentence-transformers not found. ML-driven suggestions will be disabled. Install with 'pip install sentence-transformers==5.1.0 torch==2.8.0'."
    )

# --- Data Processing Dependencies (for coverage reports) ---
# Install with 'pip install pandas==2.3.1'
try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False
    logging.warning(
        "pandas not found. Advanced coverage reports will be limited. Install with 'pip install pandas==2.3.1'."
    )

# Purpose: Provides a standard interface for interacting with Language Models.
# Install with 'pip install langchain-core==0.3.74'
from langchain_core.language_models.base import BaseLanguageModel

logger = logging.getLogger(__name__)


# --- Custom Exceptions ---
class RateLimitError(Exception):
    """Custom exception for API rate limiting errors."""

    pass


# --- Helper Classes ---
class NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def get_tracing_context(span_name: str):
    return (
        tracer.start_as_current_span(span_name)
        if OPENTELEMETRY_AVAILABLE
        else NullContext()
    )


# --- Constants ---
COSINE_SIM_THRESHOLD = 0.6
CACHE_TTL_SECONDS = 300
LLM_GEN_TIMEOUT_SECONDS = 30
LLM_PARSE_TIMEOUT_SECONDS = 10
CUSTOM_CHECKLISTS_FILE = "custom_checklists.json"
COVERAGE_HISTORY_FILE = "coverage_history.json"

# --- Static Data ---
REQUIREMENTS_CHECKLIST: List[Dict[str, Any]] = [
    {
        "id": "REQ001",
        "name": "user roles",
        "weight": 3,
        "description": "Define different user types and their permissions.",
    },
    {
        "id": "REQ002",
        "name": "error states",
        "weight": 2,
        "description": "Handle and document possible error conditions and messages.",
    },
    # ... and so on
]
DOMAIN_SPECIFIC: Dict[str, List[Dict[str, Any]]] = {
    "fintech": [
        {
            "id": "FIN001",
            "name": "AML/KYC compliance",
            "weight": 3,
            "description": "Anti-Money Laundering and Know Your Customer regulations.",
        },
        # ...
    ],
    "ui": [
        {
            "id": "UI001",
            "name": "accessibility standards",
            "weight": 3,
            "description": "Compliance with WCAG, ARIA for screen readers and assistive technologies.",
        },
        # ...
    ],
    # ...
}


# --- Utility Functions ---
def sanitize_text(text: str, allow_punct: bool = False, max_length: int = 1024) -> str:
    """Sanitizes input text to prevent injection or invalid characters, and truncates to a max length."""
    if not isinstance(text, str):
        text = str(text)
    text = text[:max_length]  # Prevent overly long inputs
    pattern = r"[^\w\s.,;!?-]" if allow_punct else r"[^\w\s-]"
    return re.sub(pattern, "", text).strip()


def validate_uuid(uuid_str: str) -> bool:
    """Validates if a string is a valid Universally Unique Identifier (UUID)."""
    try:
        uuid.UUID(uuid_str)
        return True
    except ValueError:
        return False


async def _load_json_file(file_path: str) -> Dict:
    """Asynchronously loads data from a JSON file."""
    if not aiofiles:
        logger.error(
            "aiofiles is not installed. File operations will fail. Run 'pip install aiofiles'."
        )
        return {}
    if not os.path.exists(file_path):
        return {}
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding {file_path}: {e}. Starting fresh.")
    except Exception as e:
        logger.error(f"Failed to load JSON file {file_path}: {e}", exc_info=True)
    return {}


async def _save_json_file(file_path: str, data: Dict):
    """Asynchronously saves data to a JSON file."""
    if not aiofiles:
        logger.error(
            "aiofiles is not installed. File operations will fail. Run 'pip install aiofiles'."
        )
        return
    try:
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2))
    except IOError as e:
        logger.error(f"Error saving {file_path}: {e}", exc_info=True)


class RequirementsManager:
    """
    Manages state and operations for requirements checklists, including ML models and DB connections.
    """

    def __init__(self):
        self._embedding_model: Optional[SentenceTransformer] = None
        self._db_pool: Optional[asyncpg.Pool] = None
        self._model_lock = (
            asyncio.Lock()
        )  # P3: Use asyncio.Lock for async model loading

    async def get_embedding_model(self) -> SentenceTransformer:  # P3: Make async
        """Thread-safe and async loading for production ML embedding models."""
        if not ML_ENABLED:
            raise ImportError(
                "Sentence Transformers not installed. Run 'pip install sentence-transformers'."
            )
        if self._embedding_model is None:
            async with self._model_lock:  # Acquire async lock
                if self._embedding_model is None:
                    start_time = time.perf_counter()
                    with get_tracing_context("load_embedding_model") as span:
                        logger.info(
                            "Loading SentenceTransformer model for requirements: 'all-MiniLM-L6-v2'"
                        )
                        # P3: Run blocking model load in a thread pool
                        self._embedding_model = await asyncio.to_thread(
                            SentenceTransformer, "all-MiniLM-L6-v2"
                        )
                        if OPENTELEMETRY_AVAILABLE:
                            span.set_attribute("model.name", "all-MiniLM-L6-v2")
                            span.set_status(trace.Status(trace.StatusCode.OK))
                    if PROMETHEUS_AVAILABLE and ML_MODEL_LOAD_LATENCY_SECONDS:
                        ML_MODEL_LOAD_LATENCY_SECONDS.observe(
                            time.perf_counter() - start_time
                        )
        return self._embedding_model

    async def get_db_conn_pool(
        self,
    ) -> asyncpg.Pool:  # P3: Make async and return a pool
        """Production: Securely connect to a requirements DB cluster (e.g., Postgres) using a connection pool."""
        if not DB_AVAILABLE:
            raise ImportError("asyncpg required for DB-backed checklists.")
        if self._db_pool is None:
            # P2: Secrets Handling - Get credentials from environment variables
            db_name = os.environ.get("REQ_DB_NAME", "requirements")
            db_user = os.environ.get("REQ_DB_USER", "user")
            db_pass = os.environ.get("REQ_DB_PASS", "pass")
            db_host = os.environ.get("REQ_DB_HOST", "localhost")
            db_port = int(os.environ.get("REQ_DB_PORT", 5432))

            # P1: Input Validation - Basic validation for DB connection parameters
            if not all([db_name, db_user, db_pass, db_host]):
                logger.critical(
                    "CRITICAL: Missing database connection environment variables. Exiting."
                )
                sys.exit(1)
            if (
                db_host == "localhost"
                and os.environ.get("PROD_MODE", "false").lower() == "true"
            ):
                logger.critical(
                    "CRITICAL: Localhost DB connection not allowed in production. Exiting."
                )
                sys.exit(1)

            self._db_pool = await asyncpg.create_pool(
                database=db_name,
                user=db_user,
                password=db_pass,
                host=db_host,
                port=db_port,
                min_size=1,  # P3: Connection pooling
                max_size=10,
                timeout=30,  # Connection timeout
            )
            logger.info(
                f"PostgreSQL connection pool created for {db_host}:{db_port}/{db_name}"
            )
        return self._db_pool

    @retry(
        stop=stop_after_attempt(3),  # P6: Retries for DB operations
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=(
            retry_if_exception_type((asyncpg.exceptions.PostgresError, RateLimitError))
            if DB_AVAILABLE
            else retry_if_exception_type(RateLimitError)
        ),  # Retry on specific DB errors
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def db_get_custom_checklists(self, project: Optional[str] = None) -> Dict:
        """Load custom checklists from a version-controlled DB asynchronously."""
        if not DB_AVAILABLE:
            return {}
        start_time = time.perf_counter()
        with get_tracing_context("db_get_custom_checklists") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("project", project)
            pool = await self.get_db_conn_pool()
            query = "SELECT project, domain, checklist FROM custom_checklists"
            if project:
                query += " WHERE project = $1"
                params = (project,)
            else:
                params = ()
            try:
                async with pool.acquire() as conn:  # P3: Acquire connection from pool
                    rows = await conn.fetch(query, *params)
                result = {}
                for proj, domain, checklist in rows:
                    result.setdefault(proj, {})[domain] = (
                        json.loads(checklist)
                        if isinstance(checklist, str)
                        else checklist
                    )
                logger.info(
                    f"Fetched {len(rows)} custom checklists from DB for project={project}."
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.set_attribute("checklist.count", len(rows))
                    span.set_status(trace.Status(trace.StatusCode.OK))
                if PROMETHEUS_AVAILABLE and DB_OPS_TOTAL:
                    DB_OPS_TOTAL.labels(
                        operation="get_checklists", status="success"
                    ).inc()
                    DB_OPS_LATENCY_SECONDS.labels(operation="get_checklists").observe(
                        time.perf_counter() - start_time
                    )
                return result
            except asyncpg.exceptions.PostgresError as e:
                # Hypothetical check for a rate limit error from the database
                if "rate limit" in str(e).lower():
                    logger.warning(
                        f"Database rate limit detected: {e}. Retrying via tenacity."
                    )
                    raise RateLimitError("DB rate limit exceeded") from e
                logger.error(f"DB read error for custom checklists: {e}", exc_info=True)
                raise  # Re-raise for tenacity to catch
            except Exception as e:
                logger.error(f"DB read error for custom checklists: {e}", exc_info=True)
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=str(e))
                    )
                if PROMETHEUS_AVAILABLE and DB_OPS_TOTAL:
                    DB_OPS_TOTAL.labels(
                        operation="get_checklists", status="failed"
                    ).inc()
                raise

    @retry(
        stop=stop_after_attempt(3),  # P6: Retries for DB operations
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=(
            retry_if_exception_type((asyncpg.exceptions.PostgresError, RateLimitError))
            if DB_AVAILABLE
            else retry_if_exception_type(RateLimitError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def db_save_custom_checklists(
        self, customs: Dict[str, Dict[str, List[Dict[str, Any]]]]
    ):
        """Persist custom checklists to DB in a robust, versioned way asynchronously."""
        if not DB_AVAILABLE:
            return
        start_time = time.perf_counter()
        with get_tracing_context("db_save_custom_checklists") as span:
            pool = await self.get_db_conn_pool()
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():  # P2: Atomic transaction
                        for project, domains in customs.items():
                            for domain, checklist in domains.items():
                                # P1: Sanitize DB inputs using parameterized queries (asyncpg handles this)
                                await conn.execute(
                                    """
                                    INSERT INTO custom_checklists (project, domain, checklist, updated_at)
                                    VALUES ($1, $2, $3::jsonb, NOW())
                                    ON CONFLICT (project, domain)
                                    DO UPDATE SET checklist = EXCLUDED.checklist, updated_at = NOW()
                                """,
                                    project,
                                    domain,
                                    json.dumps(checklist),
                                )
                logger.info("Custom checklists saved to DB.")
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(trace.Status(trace.StatusCode.OK))
                if PROMETHEUS_AVAILABLE and DB_OPS_TOTAL:
                    DB_OPS_TOTAL.labels(
                        operation="save_checklists", status="success"
                    ).inc()
                    DB_OPS_LATENCY_SECONDS.labels(operation="save_checklists").observe(
                        time.perf_counter() - start_time
                    )
            except asyncpg.exceptions.PostgresError as e:
                if "rate limit" in str(e).lower():
                    logger.warning(
                        f"Database rate limit detected: {e}. Retrying via tenacity."
                    )
                    raise RateLimitError("DB rate limit exceeded") from e
                logger.error(
                    f"DB write error for custom checklists: {e}", exc_info=True
                )
                raise
            except Exception as e:
                logger.error(
                    f"DB write error for custom checklists: {e}", exc_info=True
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=str(e))
                    )
                if PROMETHEUS_AVAILABLE and DB_OPS_TOTAL:
                    DB_OPS_TOTAL.labels(
                        operation="save_checklists", status="failed"
                    ).inc()
                raise  # Re-raise for tenacity to catch

    async def get_global_custom_checklists(
        self,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Hybrid: Prefer DB, fall back to file."""
        if DB_AVAILABLE:
            return await self.db_get_custom_checklists()
        return await _load_json_file(CUSTOM_CHECKLISTS_FILE)

    async def set_global_custom_checklists(self, customs):
        if DB_AVAILABLE:
            await self.db_save_custom_checklists(customs)
        else:
            await _save_json_file(CUSTOM_CHECKLISTS_FILE, customs)

    async def get_checklist(
        self, domain: Optional[str] = None, project: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Combines base, domain-specific, and custom requirements for a given context, using production DB."""
        combined = list(REQUIREMENTS_CHECKLIST)
        if domain and domain in DOMAIN_SPECIFIC:
            combined.extend(DOMAIN_SPECIFIC[domain])
        customs = await self.get_global_custom_checklists()
        proj_key = project or "default_project"
        project_customs = customs.get(proj_key, {})
        if domain in project_customs:
            combined.extend(project_customs[domain])
        unique_checklist = {
            item["id"]: item for item in combined if "id" in item
        }.values()
        return list(unique_checklist)

    async def add_item(
        self,
        domain: str,
        item_name: str,
        weight: int = 1,
        description: str = "",
        project: Optional[str] = None,
    ) -> str:
        """Adds a new custom requirement with a unique ID, persists in DB."""
        # P1: Input Validation/Sanitization with length limits
        sanitized_item_name = sanitize_text(
            item_name, allow_punct=False, max_length=256
        )
        sanitized_description = sanitize_text(
            description, allow_punct=True, max_length=2048
        )

        if not sanitized_item_name:
            raise ValueError(
                "Requirement name cannot be empty or contain only special characters."
            )

        new_item = {
            "id": f"CUST-{uuid.uuid4()}",
            "name": sanitized_item_name,
            "weight": weight,
            "description": sanitized_description,
        }
        project_key = project or "default_project"
        customs = await self.get_global_custom_checklists()
        customs.setdefault(project_key, {}).setdefault(domain, []).append(new_item)
        await self.set_global_custom_checklists(customs)

        # Audit logging
        user = os.environ.get("API_USER", "unknown_user")
        logger.info(
            f"AUDIT: User '{user}' added item '{sanitized_item_name}' to {domain} for project '{project_key}'."
        )

        return f"Added '{sanitized_item_name}' to {domain} checklist for project '{project_key}'."

    async def update_item_status(
        self,
        item_id: str,
        status: str,
        project: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> bool:
        """Updates the status of a checklist item and persists the change in DB or file."""
        # Validate UUID part of the item_id only for custom items
        if item_id.startswith("CUST-"):
            try:
                uuid_part = item_id[5:]  # Get everything after "CUST-"
                if not validate_uuid(uuid_part):
                    logger.error(
                        f"Invalid UUID in item_id '{item_id}'. Update aborted."
                    )
                    return False
            except (IndexError, TypeError):
                logger.error(f"Malformed item_id '{item_id}'. Update aborted.")
                return False

        valid_statuses = ["Covered", "Partially Covered", "Uncovered"]
        if status not in valid_statuses:
            logger.error(f"Invalid status '{status}'. Must be one of {valid_statuses}.")
            return False

        project_key = project or "default_project"
        customs = await self.get_global_custom_checklists()
        project_customs = customs.get(project_key, {})

        # Audit logging
        user = os.environ.get("API_USER", "unknown_user")

        if domain and domain in project_customs:
            for item in project_customs[domain]:
                if item.get("id") == item_id:
                    item["status"] = status
                    item["updated_at"] = datetime.datetime.utcnow().isoformat()
                    await self.set_global_custom_checklists(customs)
                    logger.info(
                        f"AUDIT: User '{user}' updated status of item '{item_id}' to '{status}' in domain '{domain}' for project '{project_key}'."
                    )
                    return True

        found_and_updated_in_memory = False
        for checklist in [REQUIREMENTS_CHECKLIST] + list(DOMAIN_SPECIFIC.values()):
            for item in checklist:
                if item.get("id") == item_id:
                    item["status"] = status
                    item["updated_at"] = datetime.datetime.utcnow().isoformat()
                    logger.info(
                        f"Updated status of global/domain item '{item_id}' to '{status}' in memory."
                    )
                    found_and_updated_in_memory = True
                    break
            if found_and_updated_in_memory:
                break

        if found_and_updated_in_memory:
            return True

        logger.warning(
            f"Item '{item_id}' not found in any checklist for project '{project_key}' and domain '{domain}'."
        )
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (asyncio.TimeoutError, json.JSONDecodeError, RateLimitError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _generate_novel_requirements(
        self, context: str, llm: BaseLanguageModel
    ) -> List[Dict[str, Any]]:
        """Use a fine-tuned LLM for requirements suggestion, trained on large software discussion corpora."""
        start_time = time.perf_counter()
        with get_tracing_context("generate_novel_requirements") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("context.length", len(context))
            prompt = f"""
            Based on the following project discussion, propose up to 3 novel, non-obvious functional or non-functional requirements.
            These should be specific to the context, not generic items like 'logging' or 'security'.
            For each suggestion, provide a 'name' and a 'description'.
            Respond ONLY with a valid JSON list of objects. Example: [{{"name": "Real-time Dashboard", "description": "A dashboard that updates financial transactions in real-time using WebSockets."}}]

            Project Context:
            ---
            {context}
            ---
            JSON Response:
            """
            try:
                # P6: Add timeout to LLM call
                response = await asyncio.wait_for(
                    llm.ainvoke(prompt), timeout=LLM_GEN_TIMEOUT_SECONDS
                )

                # Explicitly check for rate limit errors in the response content
                if (
                    hasattr(response, "content")
                    and "rate limit" in response.content.lower()
                ):
                    raise RateLimitError(
                        "LLM rate limit exceeded based on response content."
                    )

                suggestions = json.loads(response.content)

                # P1: Sanitize and validate LLM output
                if isinstance(suggestions, list):
                    sanitized_suggestions = []
                    for s in suggestions:
                        if isinstance(s, dict) and "name" in s and "description" in s:
                            sanitized_suggestions.append(
                                {
                                    "name": sanitize_text(
                                        s["name"], allow_punct=False, max_length=256
                                    ),
                                    "description": sanitize_text(
                                        s["description"],
                                        allow_punct=True,
                                        max_length=2048,
                                    ),
                                }
                            )
                    logger.info(
                        f"Generated {len(sanitized_suggestions)} novel requirements from LLM."
                    )
                    if OPENTELEMETRY_AVAILABLE:
                        span.set_attribute(
                            "novel_reqs.count", len(sanitized_suggestions)
                        )
                        span.set_status(trace.Status(trace.StatusCode.OK))
                    return sanitized_suggestions
                else:
                    raise ValueError("LLM response is not a list.")
            except RateLimitError as e:
                logger.warning(f"Rate limit hit: {e}. Retrying via tenacity.")
                raise e  # Let tenacity handle retry
            except asyncio.TimeoutError as e:
                logger.error("LLM call for novel requirements timed out.")
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description="LLM Timeout")
                    )
                raise e  # Re-raise for tenacity
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(
                    f"Failed to parse novel requirements from LLM: {e}", exc_info=True
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR, description=f"LLM parse failed: {e}"
                        )
                    )
                raise e  # Re-raise for tenacity
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred during LLM generation: {e}",
                    exc_info=True,
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"LLM generation failed: {e}",
                        )
                    )
                return []  # Don't retry on unknown errors
            finally:
                if PROMETHEUS_AVAILABLE and REQ_SUGGESTIONS_TOTAL:
                    status = (
                        "success"
                        if (
                            OPENTELEMETRY_AVAILABLE
                            and hasattr(span, "status")
                            and span.status.status_code == trace.StatusCode.OK
                        )
                        else "failed"
                    )
                    REQ_SUGGESTIONS_TOTAL.labels(domain="novel", status=status).inc()
                    REQ_SUGGESTIONS_LATENCY_SECONDS.labels(domain="novel").observe(
                        time.perf_counter() - start_time
                    )

    async def _suggest_via_embeddings(
        self,
        domain: str,
        transcript_snippet: str,
        existing_checklist: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Suggests requirements via cosine similarity of embeddings."""
        try:
            model = await self.get_embedding_model()
            transcript_embedding = await asyncio.to_thread(
                model.encode, transcript_snippet, convert_to_tensor=True
            )
            existing_names = {item["name"].lower() for item in existing_checklist}
            candidates = [
                req
                for req in REQUIREMENTS_CHECKLIST + DOMAIN_SPECIFIC.get(domain, [])
                if req["name"].lower() not in existing_names
            ]

            if candidates:
                candidate_descs = [req["description"] for req in candidates]
                # Prefer batch encode over individual for performance; see library docs for details.
                candidate_embeddings = await asyncio.to_thread(
                    model.encode, candidate_descs, convert_to_tensor=True
                )
                scores = util.pytorch_cos_sim(
                    transcript_embedding, candidate_embeddings
                )[0]
                return [
                    candidates[i]
                    for i, score in enumerate(scores)
                    if score.item() > COSINE_SIM_THRESHOLD
                ]
        except Exception as e:
            logger.error(
                f"Error during similarity-based suggestion: {e}", exc_info=True
            )
        return []

    async def suggest_requirements(
        self,
        domain: str,
        transcript_snippet: str,
        existing_checklist: List[Dict[str, Any]],
        llm: Optional[BaseLanguageModel] = None,
    ) -> List[Dict[str, Any]]:
        """Suggests new requirements using efficient embedding similarity and (if available) a fine-tuned LLM."""
        start_time = time.perf_counter()
        with get_tracing_context("suggest_requirements") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("domain", domain)
                span.set_attribute("llm_enabled", llm is not None)

            suggestions = []
            if ML_ENABLED:
                embedding_suggestions = await self._suggest_via_embeddings(
                    domain, transcript_snippet, existing_checklist
                )
                suggestions.extend(embedding_suggestions)
                if OPENTELEMETRY_AVAILABLE:
                    span.set_attribute(
                        "similarity_suggestions.count", len(embedding_suggestions)
                    )

            if llm:
                try:
                    novel_suggestions = await self._generate_novel_requirements(
                        transcript_snippet, llm
                    )
                    suggestions.extend(novel_suggestions)
                except Exception:  # Catch exceptions from retries
                    logger.warning(
                        "Failed to generate novel requirements from LLM after retries."
                    )

            final_suggestions = list(
                {s["name"].lower(): s for s in suggestions}.values()
            )
            logger.info(f"Hybrid suggestion generated: {len(final_suggestions)} items.")
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("final_suggestions.count", len(final_suggestions))
                span.set_status(trace.Status(trace.StatusCode.OK))
            if PROMETHEUS_AVAILABLE and REQ_SUGGESTIONS_TOTAL:
                REQ_SUGGESTIONS_TOTAL.labels(domain=domain, status="success").inc()
                REQ_SUGGESTIONS_LATENCY_SECONDS.labels(domain=domain).observe(
                    time.perf_counter() - start_time
                )
            return final_suggestions

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (asyncio.TimeoutError, json.JSONDecodeError, RateLimitError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def propose_checklist_updates(
        self,
        transcript: str,
        existing_checklist: List[Dict[str, Any]],
        llm: BaseLanguageModel,
    ) -> List[Dict[str, Any]]:
        """Uses a fine-tuned LLM to propose new checklist items based on conversation gaps."""
        start_time = time.perf_counter()
        with get_tracing_context("propose_checklist_updates") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("existing_checklist.count", len(existing_checklist))
            existing_names_str = ", ".join(
                [f"'{item['name']}'" for item in existing_checklist]
            )
            prompt = f"""
            Review the following conversation transcript and identify specific requirements that are discussed but are NOT on the existing checklist.
            For each missing requirement, formulate a concise 'name' and a one-sentence 'description'.
            Do not suggest items that are already on the checklist.
            Respond ONLY with a valid JSON list of objects. If no new items are found, return an empty list.

            Existing Checklist Items: {existing_names_str}

            Conversation Transcript:
            ---
            {transcript}
            ---
            JSON Response:
            """
            try:
                # P6: Add timeout to LLM call
                response = await asyncio.wait_for(
                    llm.ainvoke(prompt), timeout=LLM_GEN_TIMEOUT_SECONDS
                )

                if (
                    hasattr(response, "content")
                    and "rate limit" in response.content.lower()
                ):
                    raise RateLimitError(
                        "LLM rate limit exceeded based on response content."
                    )

                proposals = json.loads(response.content)

                # P1: Sanitize and validate LLM output
                if isinstance(proposals, list):
                    sanitized_proposals = []
                    for p in proposals:
                        if isinstance(p, dict) and "name" in p and "description" in p:
                            sanitized_proposals.append(
                                {
                                    "name": sanitize_text(
                                        p["name"], allow_punct=False, max_length=256
                                    ),
                                    "description": sanitize_text(
                                        p["description"],
                                        allow_punct=True,
                                        max_length=2048,
                                    ),
                                }
                            )
                    logger.info(
                        f"Proposed {len(sanitized_proposals)} new checklist items for self-updating."
                    )
                    if OPENTELEMETRY_AVAILABLE:
                        span.set_attribute(
                            "proposed_items.count", len(sanitized_proposals)
                        )
                        span.set_status(trace.Status(trace.StatusCode.OK))
                    return sanitized_proposals
                else:
                    raise ValueError("LLM response is not a list.")
            except RateLimitError as e:
                logger.warning(f"Rate limit hit: {e}. Retrying via tenacity.")
                raise e  # Let tenacity handle retry
            except asyncio.TimeoutError as e:
                logger.error("LLM call for checklist updates timed out.")
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description="LLM Timeout")
                    )
                raise e  # Re-raise for tenacity
            except Exception as e:
                logger.error(
                    f"Failed to generate checklist update proposals: {e}", exc_info=True
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"LLM proposal failed: {e}",
                        )
                    )
                raise e  # Re-raise for tenacity
            finally:
                if PROMETHEUS_AVAILABLE and REQ_SUGGESTIONS_TOTAL:
                    status = (
                        "success"
                        if (
                            OPENTELEMETRY_AVAILABLE
                            and hasattr(span, "status")
                            and span.status.status_code == trace.StatusCode.OK
                        )
                        else "failed"
                    )
                    REQ_SUGGESTIONS_TOTAL.labels(
                        domain="checklist_update", status=status
                    ).inc()
                    REQ_SUGGESTIONS_LATENCY_SECONDS.labels(
                        domain="checklist_update"
                    ).observe(time.perf_counter() - start_time)

    async def log_coverage_snapshot(
        self,
        project: str,
        domain: str,
        coverage_percent: float,
        covered_items: int,
        total_items: int,
    ):
        """Logs a timestamped coverage snapshot for a project in a scalable store (DB, cache, or stream)."""
        snapshot = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "domain": domain,
            "coverage_percent": round(coverage_percent, 2),
            "covered_items": covered_items,
            "total_items": total_items,
        }
        if REDIS_AVAILABLE:
            try:
                r = await redis.Redis.from_url(
                    os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                )
                key = f"coverage:{project}"
                await r.rpush(key, json.dumps(snapshot))
                logger.info(
                    f"Coverage snapshot logged to Redis for project {project}, domain {domain}."
                )
                return
            except Exception as e:
                logger.error(f"Redis coverage snapshot error: {e}")
        # Fallback: local file
        history = await _load_json_file(COVERAGE_HISTORY_FILE)
        history.setdefault(project, []).append(snapshot)
        await _save_json_file(COVERAGE_HISTORY_FILE, history)
        logger.info(
            f"Coverage snapshot logged to local file for project {project}, domain {domain}."
        )

    async def get_coverage_history(
        self, project: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Retrieves the coverage history for a given project from scalable storage."""
        if REDIS_AVAILABLE:
            try:
                r = await redis.Redis.from_url(
                    os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                )
                key = f"coverage:{project}"
                history_data = await r.lrange(key, 0, -1)
                history = [json.loads(row) for row in history_data]
                logger.info(
                    f"Fetched coverage history from Redis for project {project}."
                )
                return history
            except Exception as e:
                logger.error(f"Redis coverage history error: {e}")
        history = await _load_json_file(COVERAGE_HISTORY_FILE)
        logger.info(f"Fetched coverage history from local file for project {project}.")
        return history.get(project)

    async def generate_coverage_report(self, project: str) -> str:
        """Generates a markdown report of coverage progress over time."""
        history = await self.get_coverage_history(project)
        if not history:
            return f"No coverage history found for project **{project}**."
        report = f"# Coverage Report for Project: {project}\n\n"
        latest_by_domain = {}
        for item in history:
            latest_by_domain[item["domain"]] = item
        report += "## Latest Coverage per Domain\n"
        report += "| Domain | Coverage | Items Covered |\n"
        report += "|---|---|---|\n"
        for domain, item in latest_by_domain.items():
            report += f"| {domain} | **{item['coverage_percent']}%** | {item['covered_items']} / {item['total_items']} |\n"
        report += "\n## Trend Analysis\n"
        for domain in latest_by_domain:
            domain_history = [h for h in history if h["domain"] == domain]
            if len(domain_history) > 1:
                start_cov = domain_history[0]["coverage_percent"]
                end_cov = domain_history[-1]["coverage_percent"]
                trend = (
                    "📈 Increased"
                    if end_cov > start_cov
                    else "📉 Decreased" if end_cov < start_cov else " stagnant"
                )
                report += f"- **{domain}**: Coverage has {trend} from {start_cov}% to **{end_cov}%** over {len(domain_history)} snapshots.\n"
            else:
                report += f"- **{domain}**: Only one snapshot exists; no trend data available.\n"
        return report

    async def compute_coverage(
        self, gaps_table_markdown: str, llm: Optional[BaseLanguageModel] = None
    ) -> Dict[str, float]:
        """Robustly computes coverage stats from markdown table, using LLM parsing fallback for resilience to errors."""
        if PANDAS_AVAILABLE:
            try:
                # Clean up markdown table
                lines = gaps_table_markdown.strip().split("\n")
                # Filter out separator lines (containing only |, -, and spaces)
                data_lines = [
                    line for line in lines if not all(c in "|- " for c in line.strip())
                ]
                clean_markdown = "\n".join(data_lines)

                data = io.StringIO(clean_markdown)
                df = await asyncio.to_thread(
                    pd.read_csv, data, sep="|", skipinitialspace=True
                )

                # Remove empty columns
                df = df.dropna(axis=1, how="all")
                # Remove any empty rows
                df = df.dropna(how="all")

                # Clean column names
                df.columns = [col.strip() for col in df.columns]

                # Find status column
                status_col = next(
                    (c for c in df.columns if "status" in c.lower()), None
                )
                if status_col is None:
                    logger.warning("No 'Status' column found in markdown table")
                    return {"percent": 0.0, "covered": 0, "total": 0}

                # Clean values in the dataframe
                df = df.apply(
                    lambda col: col.str.strip() if col.dtype == "object" else col
                )

                total = len(df)
                if total == 0:
                    return {"percent": 0.0, "covered": 0, "total": 0}

                # Count rows where status contains 'covered' (case-insensitive)
                covered = (
                    df[status_col].str.lower().str.contains("covered", na=False).sum()
                )
                # But exclude "uncovered" status
                uncovered = (
                    df[status_col].str.lower().str.contains("uncovered", na=False).sum()
                )
                actual_covered = covered - uncovered

                return {
                    "percent": (actual_covered / total) * 100.0 if total > 0 else 0.0,
                    "covered": int(actual_covered),
                    "total": int(total),
                }
            except Exception as e:
                logger.warning(
                    f"Failed to compute coverage from markdown using pandas: {e}"
                )
        if llm:
            prompt = f"""
            Given the following markdown table, extract the total number of requirements, how many are covered (status contains 'covered'), and return percent as a float.

            Markdown Table:
            {gaps_table_markdown}
            ---
            Output as JSON: {{"percent": ..., "covered": ..., "total": ...}}
            """
            try:
                # P6: Add timeout to LLM call
                resp = await asyncio.wait_for(
                    llm.ainvoke(prompt), timeout=LLM_PARSE_TIMEOUT_SECONDS
                )
                obj = json.loads(resp.content)
                if all(k in obj for k in ("percent", "covered", "total")):
                    return obj
            except asyncio.TimeoutError:
                logger.error("LLM call for compute_coverage timed out.")
                return {"percent": 0.0, "covered": 0, "total": 0}
            except Exception as e:
                logger.error(
                    f"LLM fallback for compute_coverage failed: {e}", exc_info=True
                )
        return {"percent": 0.0, "covered": 0, "total": 0}

    def register_plugin_requirements(
        self, domain_name: str, requirements: List[Dict[str, Any]]
    ):
        """Allows plugins to dynamically register new requirement domains and types."""
        # P1: Input Validation/Sanitization for domain_name and requirement items
        sanitized_domain_name = sanitize_text(
            domain_name, allow_punct=False, max_length=100
        )
        if not sanitized_domain_name:
            logger.warning(
                "Attempted to register plugin requirements with an invalid domain name."
            )
            return

        if sanitized_domain_name not in DOMAIN_SPECIFIC:
            DOMAIN_SPECIFIC[sanitized_domain_name] = []
            logger.info(
                f"Plugin created new requirements domain: '{sanitized_domain_name}'."
            )
        for item in requirements:
            # Sanitize item content and validate UUID before adding
            item_id = item.get("id", f"PLUG-{uuid.uuid4()}")
            try:
                if "PLUG-" in item_id and not validate_uuid(item_id.split("-")[-1]):
                    logger.warning(
                        f"Invalid UUID in provided item_id '{item_id}'. Generating new one."
                    )
                    item_id = f"PLUG-{uuid.uuid4()}"
            except (IndexError, TypeError):
                logger.warning(f"Malformed item_id '{item_id}'. Generating new one.")
                item_id = f"PLUG-{uuid.uuid4()}"

            item_name = sanitize_text(
                item.get("name", ""), allow_punct=False, max_length=256
            )
            item_description = sanitize_text(
                item.get("description", ""), allow_punct=True, max_length=2048
            )
            item_weight = item.get("weight", 1)

            if not item_name:
                logger.warning(
                    f"Skipping plugin requirement with empty name in domain '{sanitized_domain_name}'."
                )
                continue

            sanitized_item = {
                "id": item_id,
                "name": item_name,
                "weight": item_weight,
                "description": item_description,
            }

            is_duplicate = any(
                existing.get("id") == sanitized_item["id"]
                or existing["name"].lower() == sanitized_item["name"].lower()
                for existing in DOMAIN_SPECIFIC[sanitized_domain_name]
                + REQUIREMENTS_CHECKLIST
            )
            if not is_duplicate:
                DOMAIN_SPECIFIC[sanitized_domain_name].append(sanitized_item)
                logger.info(
                    f"Plugin registered requirement '{sanitized_item['name']}' for domain '{sanitized_domain_name}'."
                )


# --- Singleton Instance and Cleanup ---
manager = RequirementsManager()


def shutdown_cleanup():
    """Performs cleanup of resources like database connection pools and Redis clients on application exit."""
    # Clean up PostgreSQL connection pool
    if manager._db_pool:
        try:
            logger.info("Closing PostgreSQL connection pool.")
            asyncio.run(manager._db_pool.close())
        except Exception as e:
            logger.error(f"Error while closing PostgreSQL DB pool: {e}", exc_info=True)

    # Clean up Redis connections
    if REDIS_AVAILABLE:
        try:
            logger.info("Closing Redis connections.")
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            # Create a temporary client instance just for closing the connections managed by the library's connection pool
            r = redis.Redis.from_url(redis_url)
            asyncio.run(r.close())
        except Exception as e:
            logger.error(f"Error while closing Redis connections: {e}", exc_info=True)

    # Clean up ML model from memory
    if ML_ENABLED and manager._embedding_model:
        logger.info("Unloading Machine Learning model from memory.")
        del manager._embedding_model


# Register the cleanup function to be called upon program exit
atexit.register(shutdown_cleanup)


# --- Exported functions for backwards compatibility ---
async def get_embedding_model():
    return await manager.get_embedding_model()


async def get_db_conn_pool():
    return await manager.get_db_conn_pool()


async def db_get_custom_checklists(project: Optional[str] = None):
    return await manager.db_get_custom_checklists(project)


async def db_save_custom_checklists(customs):
    return await manager.db_save_custom_checklists(customs)


async def get_global_custom_checklists():
    return await manager.get_global_custom_checklists()


async def set_global_custom_checklists(customs):
    return await manager.set_global_custom_checklists(customs)


async def get_checklist(domain: Optional[str] = None, project: Optional[str] = None):
    return await manager.get_checklist(domain, project)


async def add_item(
    domain: str,
    item_name: str,
    weight: int = 1,
    description: str = "",
    project: Optional[str] = None,
):
    return await manager.add_item(domain, item_name, weight, description, project)


async def update_item_status(
    item_id: str,
    status: str,
    project: Optional[str] = None,
    domain: Optional[str] = None,
):
    return await manager.update_item_status(item_id, status, project, domain)


async def _generate_novel_requirements(context: str, llm: BaseLanguageModel):
    return await manager._generate_novel_requirements(context, llm)


async def suggest_requirements(
    domain: str,
    transcript_snippet: str,
    existing_checklist: List[Dict[str, Any]],
    llm: Optional[BaseLanguageModel] = None,
):
    return await manager.suggest_requirements(
        domain, transcript_snippet, existing_checklist, llm
    )


async def propose_checklist_updates(
    transcript: str, existing_checklist: List[Dict[str, Any]], llm: BaseLanguageModel
):
    return await manager.propose_checklist_updates(transcript, existing_checklist, llm)


async def log_coverage_snapshot(
    project: str,
    domain: str,
    coverage_percent: float,
    covered_items: int,
    total_items: int,
):
    return await manager.log_coverage_snapshot(
        project, domain, coverage_percent, covered_items, total_items
    )


async def get_coverage_history(project: str):
    return await manager.get_coverage_history(project)


async def generate_coverage_report(project: str):
    return await manager.generate_coverage_report(project)


async def compute_coverage(
    gaps_table_markdown: str, llm: Optional[BaseLanguageModel] = None
):
    return await manager.compute_coverage(gaps_table_markdown, llm)


def register_plugin_requirements(domain_name: str, requirements: List[Dict[str, Any]]):
    return manager.register_plugin_requirements(domain_name, requirements)


# Global lock variables for backward compatibility
_file_lock = threading.Lock()
_model_lock = manager._model_lock
_EMBEDDING_MODEL = manager._embedding_model
_db_pool = manager._db_pool
