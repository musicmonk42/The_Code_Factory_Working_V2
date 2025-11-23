# (full file with the applied fixes)
import os
from pathlib import Path
import sqlite3
import logging
import random
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
import re
from typing import Any, Dict, Optional, List, Callable
from datetime import datetime, date, time
import collections.abc
import asyncio
import uuid
from logging import Formatter
import sys
import threading
from functools import partial

# Prometheus client for metrics
try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        REGISTRY,
        generate_latest,
        CollectorRegistry,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Prometheus client not available. Metrics will not be collected in explain.py."
    )

# Pydantic for input validation
try:
    from pydantic import BaseModel, Field, ValidationError, validator

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Pydantic not available. Input validation will be skipped in explain.py."
    )

# LLM Integration
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import PromptTemplate
    from langchain_core.language_models import BaseChatModel

    LANGCHAIN_OPENAI_AVAILABLE = True
except ImportError:
    LANGCHAIN_OPENAI_AVAILABLE = False
    ChatOpenAI = None
    PromptTemplate = None
    BaseChatModel = None
    logging.getLogger(__name__).warning(
        "langchain_openai not available. LLM integration will be limited."
    )

# DLT Integration
try:
    from test_generation.audit_log import AuditLogger as DLTLogger
    from test_generation.agentic import SecretsManager as GlobalSecretsManager

    DLT_LOGGER_AVAILABLE = True
except ImportError:
    DLT_LOGGER_AVAILABLE = False
    DLTLogger = None
    GlobalSecretsManager = None
    logging.getLogger(__name__).warning("DLTLogger not available. Audit logging will be disabled.")

# aiosqlite for async DB access
try:
    import aiosqlite

    AIOSQLITE_AVAILABLE = True
except ImportError:
    AIOSQLITE_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "aiosqlite not available. Async DB access will not be available."
    )

# --- Module Docstring ---
"""
ExplainableReasoner: Async, Explainable, Plugin-ready LLM Reasoning Engine

- Async/await everything: DB, model, history, metrics
- Pluggable and observable for standalone use
- Prometheus metrics, structured logs, fallback rules
- Multi-model/device, quantization-ready
- Clean error handling, context sanitization, and history
"""


# Placeholder for ArbiterConfig for standalone execution/testing
class ArbiterConfig:
    LLM_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/completions")
    LLM_API_KEY = os.getenv("OPENAI_API_KEY")
    LLM_API_TIMEOUT_SECONDS = 30.0
    LLM_ENHANCEMENT_ENABLED = True
    LLM_RELATION_DISCOVERY_ENABLED = True
    LLM_CONSISTENCY_CHECK_ENABLED = True
    LLM_ENHANCE_MODEL = "gpt-4o-mini"
    LLM_RELATED_MODEL = "gpt-4o-mini"
    LLM_CONSISTENCY_MODEL = "gpt-4o-mini"
    ML_MODEL_PATH = "models/relevance_classifier.pth"
    ML_LEARNING_RATE = 0.001
    ML_TRAINING_EPOCHS = 100
    MIN_SCALER_SAMPLES = 5
    QUANTUM_ENABLED = False
    QUANTUM_DOMAINS = ["Companies", "Research"]
    QUANTUM_SHOTS = 1024
    KNOWLEDGE_REFRESH_INTERVAL = 3600
    KG_CPU_WORKERS = 4
    MAX_ESG_SCORE_CHANGE = 10.0
    ALLOW_SECTOR_CHANGE = False
    MIN_BUDGET_THRESHOLD_MULTIPLIER = 0.5
    KG_CONSISTENCY_ON_ADD = True
    SIMILARITY_THRESHOLD = 0.8
    MAX_LEARN_RETRIES = 3
    KEYWORD_SEARCH_ENABLED = True
    TRANSFORMERS_OFFLINE = False
    LLM_API_BACKOFF_MAX_SECONDS = 30


TRANSFORMERS_AVAILABLE = False
try:
    from transformers import (
        pipeline,
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )
    import torch

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Warning: Hugging Face Transformers and/or PyTorch not found. The Reasoner will operate in fallback mode."
    )


@dataclass
class ExplanationResult:
    id: str
    query: str
    explanation: str
    context_used: Dict[str, Any]
    generated_by: str
    timestamp: str


@dataclass
class ReasoningResult:
    id: str
    query: str
    reasoning: str
    context_used: Dict[str, Any]
    generated_by: str
    timestamp: str


@dataclass
class ReasoningHistory:
    id: str
    query: str
    context: Dict[str, Any]
    response: str
    response_type: str
    timestamp: str


@dataclass
class ReasonerConfig:
    model_name: str = "gpt2"
    device: int = -1
    max_workers: int = 2
    generation_timeout: int = 60
    max_generation_tokens: int = 500
    temperature_explain: float = 0.5
    temperature_reason: float = 0.6
    temperature_neutral: float = 0.5
    temperature_negative: float = 0.3
    history_db_path: str = "reasoner_history.db"
    max_history_size: int = 100
    strict_mode: bool = False
    mock_mode: bool = False
    log_prompts: bool = False
    model_cache_dir: str = os.path.join(Path.home(), ".cache", "huggingface", "hub")
    model_configs: Optional[List[Dict[str, Any]]] = None
    health_check_interval_seconds: int = 300


# --- Metrics (Idempotent and Thread-Safe Registration) ---
if PROMETHEUS_AVAILABLE:
    _metrics_registry = CollectorRegistry(auto_describe=True)
else:

    class DummyRegistry:
        def __init__(self, *args, **kwargs):
            pass

        def collect(self):
            return []

    _metrics_registry = DummyRegistry()

_metrics_lock = threading.Lock()


class DummyMetric:
    # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
    DEFAULT_BUCKETS = (
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
        float("inf"),
    )

    def labels(self, **kwargs):
        return self

    def observe(self, *args):
        pass

    def inc(self):
        pass

    def set(self, *args):
        pass


def get_or_create_metric(metric_type, name, documentation, labelnames=None, buckets=None):
    """Get or create a metric, handling both mock and real metric types"""
    try:
        with _metrics_lock:
            if name in _metrics_registry._names_to_collectors:
                existing_metric = _metrics_registry._names_to_collectors[name]
                # For mocked metrics, just return the existing one
                return existing_metric

            # Create new metric
            if buckets is not None:
                metric = metric_type(name, documentation, labelnames or [], buckets=buckets)
            else:
                metric = metric_type(name, documentation, labelnames or [])
            return metric
    except Exception:
        # Fallback for testing
        return DummyMetric()


METRICS = {
    "inference_total": get_or_create_metric(
        Counter,
        "reasoner_inference_total",
        "Total number of inferences processed",
        ["type"],
    ),
    "inference_success": get_or_create_metric(
        Counter,
        "reasoner_inference_success_total",
        "Total number of successful inferences",
        ["type"],
    ),
    "inference_errors": get_or_create_metric(
        Counter,
        "reasoner_inference_errors_total",
        "Total number of inference errors",
        ["type", "error_code"],
    ),
    "inference_duration_seconds": get_or_create_metric(
        Histogram,
        "reasoner_inference_duration_seconds",
        "Inference duration in seconds",
        ["type"],
    ),
    "history_entries_current": get_or_create_metric(
        Gauge, "reasoner_history_entries_current", "Current number of history entries"
    ),
    "history_operations_total": get_or_create_metric(
        Counter,
        "reasoner_history_operations_total",
        "Total history database operations",
        ["operation", "status"],
    ),
    "model_load_errors": get_or_create_metric(
        Counter,
        "reasoner_model_load_errors_total",
        "Total errors during model loading",
        ["model_name", "device"],
    ),
    "model_pipeline_usage": get_or_create_metric(
        Counter,
        "reasoner_model_pipeline_usage_total",
        "Total usage of each model pipeline",
        ["model_name", "device"],
    ),
    "health_status": get_or_create_metric(
        Gauge,
        "reasoner_health_status",
        "Health status of the reasoner (1=healthy, 0=unhealthy)",
        ["component"],
    ),
    "last_health_check_timestamp": get_or_create_metric(
        Gauge,
        "reasoner_last_health_check_timestamp_seconds",
        "Timestamp of the last health check",
    ),
    "explanation_latency": get_or_create_metric(
        Histogram,
        "explain_latency_seconds",
        "Explanation generation latency",
        ["result_id"],
    ),
}

logger = logging.getLogger("ExplainableReasoner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": %(message)s}'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# --- Async Executor (Robust, Process-Safe Pool) ---
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()
_executor_shutdown_event = asyncio.Event()


async def get_executor_async(max_workers: int) -> ThreadPoolExecutor:
    global _executor
    async with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max_workers)
            logger.info(f"ThreadPoolExecutor initialized with {max_workers} workers.")
            _executor_shutdown_event.clear()
        return _executor


async def shutdown_executor_async():
    global _executor
    async with _executor_lock:
        if _executor:
            logger.info("Shutting down ThreadPoolExecutor...")
            _executor_shutdown_event.set()
            _executor.shutdown(wait=True)
            _executor = None
            logger.info("ThreadPoolExecutor shut down.")


def _run_in_thread(fn: Callable, *args: Any, timeout: int = 15, **kwargs: Any) -> Any:
    if _executor is None:
        logger.critical(
            "ThreadPoolExecutor is not initialized or is shut down when _run_in_thread was called synchronously."
        )
        raise RuntimeError("ThreadPoolExecutor is not initialized or is shut down.")
    future = _executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        METRICS["inference_errors"].labels(type="timeout", error_code="TIMEOUT").inc()
        raise ReasonerError("Model inference timed out", code="TIMEOUT")
    except Exception as e:
        METRICS["inference_errors"].labels(type="inference", error_code="INFERENCE_FAILED").inc()
        raise ReasonerError(f"Model inference failed: {e}", code="INFERENCE_FAILED")


class ReasonerError(Exception):
    def __init__(self, message: str, code: str, original_exception: Optional[Exception] = None):
        self.message = message
        self.code = code
        self.original_exception = original_exception
        super().__init__(f"{message} (Code: {code})")


def _sanitize_input(text: str, max_length: int = 1024) -> str:
    if not isinstance(text, str):
        raise ReasonerError("Input must be a string", code="INVALID_INPUT")
    text = re.sub(r"[\x00-\x1F\x7F]", "", text.strip())
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"<[^>]*>", "", text)
    text = text[:max_length]
    if not text:
        raise ReasonerError("Input is empty or invalid after sanitization", code="EMPTY_INPUT")
    return text


def _sanitize_context(context: Dict[str, Any], max_size_bytes: int = 4096) -> Dict[str, Any]:
    if not isinstance(context, dict):
        raise ReasonerError("Context must be a dictionary", code="INVALID_CONTEXT")

    def _json_serializable_converter(obj: Any) -> Any:
        from enum import Enum

        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, collections.abc.Coroutine):
            logger.warning(
                f"Coroutine object found in context: {obj}. Converting to string. Caller should await this value."
            )
            return f"<{obj.__class__.__name__} object at {hex(id(obj))}, status: {obj.__getstate__()[0] if hasattr(obj, '__getstate__') else 'unknown'}>"
        if isinstance(obj, (dict, list)):
            return _deep_sanitize(obj)
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        try:
            if "pydantic" in sys.modules:
                from pydantic import BaseModel

                if isinstance(obj, BaseModel):
                    return obj.model_dump()
        except ImportError:
            pass
        if hasattr(obj, "__dict__"):
            serialized_dict = {}
            for k, v in obj.__dict__.items():
                if not k.startswith("_"):
                    try:
                        serialized_dict[k] = _json_serializable_converter(v)
                    except Exception:
                        serialized_dict[k] = str(v)
            return serialized_dict

        logger.warning(
            f"Non-JSON-serializable object of type {type(obj)} found in context. Converting to string."
        )
        return str(obj)

    def _deep_sanitize(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _json_serializable_converter(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_json_serializable_converter(elem) for elem in obj]
        else:
            return _json_serializable_converter(obj)

    try:
        sanitized_for_dumps = _deep_sanitize(context)
        context_json = json.dumps(sanitized_for_dumps, sort_keys=True, ensure_ascii=False)

        if len(context_json.encode("utf-8")) > max_size_bytes:
            logger.warning(
                f"Context size ({len(context_json.encode('utf-8'))} bytes) exceeds max_size ({max_size_bytes} bytes). Attempting to truncate."
            )

            truncated_json_str = context_json
            while len(truncated_json_str.encode("utf-8")) > max_size_bytes:
                last_comma = truncated_json_str.rfind(",")
                last_brace = truncated_json_str.rfind("{")
                last_bracket = truncated_json_str.rfind("[")

                cut_point = max(last_comma, last_brace, last_bracket)

                if cut_point == -1 or len(truncated_json_str) < 50:
                    logger.error(
                        "Could not truncate context while maintaining JSON structure. Returning minimal error context."
                    )
                    return {
                        "_truncated_context_error": f"Original context too large ({len(context_json.encode('utf-8'))} bytes) and could not be safely truncated. Content is likely incomplete."
                    }

                truncated_json_str = truncated_json_str[:cut_point]

                open_braces = truncated_json_str.count("{")
                close_braces = truncated_json_str.count("}")
                open_brackets = truncated_json_str.count("[")
                truncated_json_str = truncated_json_str.rstrip("{[,")
                close_brackets = truncated_json_str.count("]")

                if open_braces > close_braces:
                    truncated_json_str += "}" * (open_braces - close_braces)
                if open_brackets > close_brackets:
                    truncated_json_str += "]" * (open_brackets - close_brackets)

            try:
                return json.loads(truncated_json_str)
            except json.JSONDecodeError:
                logger.error("Truncating context broke JSON format. Returning minimal context.")
                return {
                    "_truncated_context_error": f"Original context too large ({len(context_json.encode('utf-8'))} bytes) and truncated. Content may be incomplete."
                }
        return json.loads(context_json)
    except json.JSONDecodeError as e:
        raise ReasonerError(
            f"Invalid context format after processing: {e}",
            code="INVALID_CONTEXT_FORMAT",
            original_exception=e,
        )
    except Exception as e:
        raise ReasonerError(
            f"Failed to sanitize context: {e}",
            code="CONTEXT_SANITIZATION_FAILED",
            original_exception=e,
        )


def _rule_based_fallback(query: str, context: Dict[str, Any], mode: str) -> str:
    summary = context.get("summary", "no specific context")
    details = context.get("details", "no further details")
    response_phrases = {
        "explain": [
            f"[Fallback] The explanation for '{query}' is based on available information: '{summary}'. It suggests that the outcome is a plausible result given the known conditions.",
            f"[Fallback] Considering the query '{query}' and context '{summary}', the system's best approximation indicates a logical progression leading to this state. Details: {details}.",
            f"[Fallback] While the advanced model is unavailable, a basic understanding of '{query}' with context '{summary}' implies a reasonable explanation based on foundational principles.",
        ],
        "reason": [
            f"[Fallback] Reasoning for '{query}': Given the summarized context '{summary}', a deduction can be made that the most likely conclusion is in line with the provided data.",
            f"[Fallback] Based on '{summary}', the system reasons that '{query}' leads to a conclusion consistent with the initial premise. Further details: {details}.",
            f"[Fallback] In the absence of a detailed model, simple logic applied to '{query}' within context '{summary}' indicates a straightforward deduction.",
        ],
    }
    return random.choice(response_phrases.get(mode, [f"[Fallback] Could not process '{query}'."]))


class HistoryManager:
    def __init__(self, db_path: str, max_size: int):
        if not AIOSQLITE_AVAILABLE:
            raise ImportError("aiosqlite is not available, HistoryManager cannot be used.")
        self.db_path = db_path
        self.max_size = max_size
        self._db_lock = asyncio.Lock()

    async def init_db(self):
        db_dir = Path(self.db_path).parent
        os.makedirs(db_dir, exist_ok=True)
        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS reasoner_history (
                            id TEXT PRIMARY KEY,
                            query TEXT NOT NULL,
                            context TEXT NOT NULL,
                            response TEXT NOT NULL,
                            response_type TEXT NOT NULL,
                            timestamp TEXT NOT NULL
                        )
                    """
                    )
                    await conn.commit()
                logger.info(f"Reasoner history database initialized at {self.db_path}")
                METRICS["history_operations_total"].labels(
                    operation="init_db", status="success"
                ).inc()
                METRICS["health_status"].labels(component="history_db").set(1)
        except (sqlite3.Error, aiosqlite.Error) as e:
            logger.error(f"Failed to initialize reasoner history database: {e}")
            METRICS["history_operations_total"].labels(operation="init_db", status="error").inc()
            METRICS["health_status"].labels(component="history_db").set(0)
            raise ReasonerError(
                "Database initialization failed",
                code="DB_INIT_FAILED",
                original_exception=e,
            )

    async def add_entry(self, entry: ReasoningHistory):
        try:
            async with self._db_lock:
                # Open the connection and insert the entry
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    await conn.execute(
                        """
                        INSERT INTO reasoner_history (id, query, context, response, response_type, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            entry.id,
                            entry.query,
                            json.dumps(entry.context, ensure_ascii=False),
                            entry.response,
                            entry.response_type,
                            entry.timestamp,
                        ),
                    )
                    await conn.execute(
                        """
                        DELETE FROM reasoner_history WHERE id NOT IN (
                            SELECT id FROM reasoner_history ORDER BY timestamp DESC LIMIT ?
                        )
                    """,
                        (self.max_size,),
                    )
                    await conn.commit()

                    # Compute current size here using the same connection (avoid re-acquiring the lock)
                    cursor = await conn.execute("SELECT COUNT(*) FROM reasoner_history")
                    row = await cursor.fetchone()
                    current_size = row[0] if row is not None else 0

                # Update metrics and finish
                METRICS["history_entries_current"].set(current_size)
                METRICS["history_operations_total"].labels(
                    operation="add_entry", status="success"
                ).inc()
                logger.debug(f"Added history entry {entry.id}, current size: {current_size}")
        except (sqlite3.Error, aiosqlite.Error) as e:
            logger.error(f"Failed to add history entry: {e}")
            METRICS["history_operations_total"].labels(operation="add_entry", status="error").inc()
            raise ReasonerError(
                "Failed to add history entry",
                code="DB_WRITE_FAILED",
                original_exception=e,
            )

    async def get_entries(self, limit: int = 10) -> List[ReasoningHistory]:
        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = await conn.execute(
                        """
                        SELECT id, query, context, response, response_type, timestamp
                        FROM reasoner_history ORDER BY timestamp DESC LIMIT ?
                    """,
                        (limit,),
                    )
                    entries = []
                    async for row in cursor:
                        try:
                            entry = ReasoningHistory(
                                id=row["id"],
                                query=row["query"],
                                context=json.loads(row["context"]),
                                response=row["response"],
                                response_type=row["response_type"],
                                timestamp=row["timestamp"],
                            )
                            entries.append(entry)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"Failed to decode context for history entry {row['id']}: {e}. Skipping this entry's context."
                            )
                            entry = ReasoningHistory(
                                id=row["id"],
                                query=row["query"],
                                context={"error": "Invalid JSON context"},
                                response=row["response"],
                                response_type=row["response_type"],
                                timestamp=row["timestamp"],
                            )
                            entries.append(entry)
                    METRICS["history_operations_total"].labels(
                        operation="get_entries", status="success"
                    ).inc()
                return entries
        except (sqlite3.Error, aiosqlite.Error) as e:
            logger.error(f"Failed to retrieve history entries: {e}")
            METRICS["history_operations_total"].labels(
                operation="get_entries", status="error"
            ).inc()
            raise ReasonerError(
                "Failed to retrieve history entries",
                code="DB_READ_FAILED",
                original_exception=e,
            )

    async def clear(self):
        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    await conn.execute("DELETE FROM reasoner_history")
                    await conn.commit()
                METRICS["history_entries_current"].set(0)
                METRICS["history_operations_total"].labels(
                    operation="clear_history", status="success"
                ).inc()
                logger.info("History database cleared.")
        except (sqlite3.Error, aiosqlite.Error) as e:
            logger.error(f"Failed to clear history: {e}")
            METRICS["history_operations_total"].labels(
                operation="clear_history", status="error"
            ).inc()
            raise ReasonerError(
                "Failed to clear history", code="DB_CLEAR_FAILED", original_exception=e
            )

    async def get_size(self) -> int:
        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    cursor = await conn.execute("SELECT COUNT(*) FROM reasoner_history")
                    size = (await cursor.fetchone())[0]
                return size
        except (sqlite3.Error, aiosqlite.Error) as e:
            logger.error(f"Failed to get history size: {e}")
            METRICS["history_operations_total"].labels(operation="get_size", status="error").inc()
            raise ReasonerError(
                "Failed to get history size",
                code="DB_READ_FAILED",
                original_exception=e,
            )


class ExplainableReasoner:
    def __init__(self, settings: Any, config: Optional[ReasonerConfig] = None):
        self.settings = settings
        self.config = config or ReasonerConfig()
        self.logger = logger
        self.pipelines: List[Dict[str, Any]] = []
        self.next_pipeline_idx = 0
        self._pipeline_lock = asyncio.Lock()

        db_path_from_settings = getattr(settings, "DB_PATH", "sqlite:///./local_database.db")

        if db_path_from_settings.startswith("sqlite:///"):
            base_db_dir = Path(db_path_from_settings.replace("sqlite:///", "")).parent
        else:
            base_db_dir = Path(db_path_from_settings).parent
            if str(base_db_dir) == ".":
                base_db_dir = Path(os.getcwd())

        history_db_full_dir = base_db_dir / "reasoner_data"
        os.makedirs(history_db_full_dir, exist_ok=True)
        history_db_full_path = str(history_db_full_dir / self.config.history_db_path)

        self.history = HistoryManager(
            db_path=history_db_full_path, max_size=self.config.max_history_size
        )
        # Ensure an executor attribute exists immediately so tests (and other code) can patch/inspect it before async_init.
        # Create an instance-owned executor by default. Async async_init will not override a pre-existing executor.
        self.executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=max(1, self.config.max_workers)
        )
        self._owns_executor = True  # track ownership so shutdown can be correct
        self._health_check_task: Optional[asyncio.Task] = None

        logger.info("ExplainableReasoner initialized (await async_init for full setup).")

    async def async_init(self):
        # Only obtain the global async executor if an instance-level executor hasn't been set.
        if self.executor is None:
            self.executor = await get_executor_async(self.config.max_workers)
            self._owns_executor = False
        else:
            self.logger.debug("Using pre-initialized executor (instance-level).")

        await self.history.init_db()
        await self._initialize_models_async()
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self._periodic_health_check())
        logger.info("ExplainableReasoner async initialization complete.")

    async def _initialize_models_async(self):
        if self.config.mock_mode:
            self.logger.info("Mock mode enabled, skipping model initialization.")
            self.pipelines = []
            METRICS["health_status"].labels(component="llm_models").set(1)
            return

        transformers_offline_setting = getattr(self.settings, "TRANSFORMERS_OFFLINE", False)
        if isinstance(transformers_offline_setting, str):
            transformers_offline_setting = transformers_offline_setting.lower() == "true"

        if not TRANSFORMERS_AVAILABLE:
            self.logger.warning(
                "Transformers library not available. Models will not be loaded. Operating in fallback mode."
            )
            self.pipelines = []
            METRICS["health_status"].labels(component="llm_models").set(0)
            return

        model_configs_to_load = self.config.model_configs
        if not model_configs_to_load:
            model_configs_to_load = [
                {
                    "model_name": self.config.model_name,
                    "device": self.config.device,
                    "quantization_config": {},
                }
            ]

        successful_loads = 0
        for model_cfg in model_configs_to_load:
            model_name = model_cfg.get("model_name", self.config.model_name)
            device = model_cfg.get("device", self.config.device)
            quantization_cfg_params = model_cfg.get("quantization_config", {})

            retries = 3
            initial_delay = 1
            for attempt in range(retries):
                try:
                    self.logger.info(
                        f"Attempting to load model: {model_name} on device {device} (attempt {attempt + 1}/{retries})"
                    )
                    model_kwargs = {
                        "cache_dir": self.config.model_cache_dir,
                        "local_files_only": transformers_offline_setting,
                    }

                    if device >= 0 and "torch" in sys.modules and torch.cuda.is_available():
                        current_quantization_config = BitsAndBytesConfig(
                            load_in_4bit=quantization_cfg_params.get("load_in_4bit", True),
                            bnb_4bit_compute_dtype=getattr(
                                torch,
                                quantization_cfg_params.get("bnb_4bit_compute_dtype", "float16"),
                            ),
                            bnb_4bit_quant_type=quantization_cfg_params.get(
                                "bnb_4bit_quant_type", "nf4"
                            ),
                            bnb_4bit_use_double_quant=quantization_cfg_params.get(
                                "bnb_4bit_use_double_quant", True
                            ),
                            bnb_4bit_quant_storage=getattr(
                                torch,
                                quantization_cfg_params.get("bnb_4bit_quant_storage", "uint8"),
                                torch.uint8,
                            ),
                        )
                        model_kwargs["quantization_config"] = current_quantization_config
                        model_kwargs["torch_dtype"] = getattr(
                            torch, quantization_cfg_params.get("torch_dtype", "float16")
                        )
                        self.logger.info(
                            f"Loading model '{model_name}' with 4-bit quantization on CUDA device {device}."
                        )
                    elif device >= 0:
                        self.logger.warning(
                            f"PyTorch and/or CUDA not found. Cannot apply BitsAndBytesConfig for quantization or load on GPU. Loading model '{model_name}' on CPU."
                        )
                        device = -1

                    tokenizer = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        partial(AutoTokenizer.from_pretrained, model_name, **model_kwargs),
                    )
                    model_obj = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        partial(
                            AutoModelForCausalLM.from_pretrained,
                            model_name,
                            **model_kwargs,
                        ),
                    )

                    text_generation_pipeline = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        partial(
                            pipeline,
                            "text-generation",
                            model=model_obj,
                            device=device,
                            tokenizer=tokenizer,
                        ),
                    )

                    if text_generation_pipeline.tokenizer.pad_token_id is None:
                        text_generation_pipeline.tokenizer.pad_token_id = (
                            text_generation_pipeline.tokenizer.eos_token_id
                        )

                    self.pipelines.append(
                        {
                            "pipeline": text_generation_pipeline,
                            "model_name": model_name,
                            "device": device,
                        }
                    )
                    self.logger.info(
                        f"Successfully loaded model '{model_name}' on device {device}."
                    )
                    successful_loads += 1
                    break
                except ImportError:
                    self.logger.warning(
                        f"Transformers library not available during retry for {model_name}. Operating in fallback mode."
                    )
                    METRICS["model_load_errors"].labels(model_name=model_name, device=device).inc()
                    break
                except Exception as e:
                    self.logger.error(
                        f"Failed to load NLP model '{model_name}' on device {device} (attempt {attempt + 1}): {e}",
                        exc_info=True,
                    )
                    METRICS["model_load_errors"].labels(model_name=model_name, device=device).inc()
                    if attempt < retries - 1:
                        sleep_time = initial_delay * (2**attempt)
                        self.logger.info(f"Retrying model load in {sleep_time} seconds...")
                        await asyncio.sleep(sleep_time)
                    else:
                        self.logger.error(
                            f"Max retries ({retries}) exceeded for model loading '{model_name}'. Operating in fallback mode for this model."
                        )
                        if self.config.strict_mode:
                            raise ReasonerError(
                                f"Model initialization failed after retries for {model_name}: {e}",
                                code="MODEL_INIT_FAILED",
                                original_exception=e,
                            )

        if not self.pipelines:
            self.logger.warning(
                "No models were successfully loaded. Reasoner will operate in fallback mode."
            )
            METRICS["health_status"].labels(component="llm_models").set(0)
        else:
            METRICS["health_status"].labels(component="llm_models").set(1)

    async def shutdown(self):
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                self.logger.info("Health check task cancelled.")
            self._health_check_task = None
        # If this instance created its own executor, shut it down here.
        if getattr(self, "_owns_executor", False) and self.executor is not None:
            try:
                self.logger.info("Shutting down instance-owned ThreadPoolExecutor...")
                self.executor.shutdown(wait=True)
            except Exception as e:
                self.logger.warning(f"Error shutting down instance executor: {e}")
            finally:
                self.executor = None
                self._owns_executor = False

        # Also attempt to shut down the shared async executor if it exists.
        await shutdown_executor_async()
        self.pipelines = []
        self.logger.info("ExplainableReasoner shut down.")

    async def _get_next_pipeline(self) -> Optional[Dict[str, Any]]:
        async with self._pipeline_lock:
            if not self.pipelines:
                return None

            pipeline_info = self.pipelines[self.next_pipeline_idx]
            self.next_pipeline_idx = (self.next_pipeline_idx + 1) % len(self.pipelines)
            METRICS["model_pipeline_usage"].labels(
                model_name=pipeline_info["model_name"], device=pipeline_info["device"]
            ).inc()
            return pipeline_info

    def _generate_text_sync(
        self,
        pipeline_info: Dict[str, Any],
        prompt: str,
        max_new_tokens: int,
        temperature: float,
    ) -> str:
        model_pipeline = pipeline_info["pipeline"]
        tokenizer = model_pipeline.tokenizer

        model_max_length = getattr(tokenizer, "model_max_length", 1024)
        if model_max_length is None or model_max_length > 100000:
            model_max_length = 1024

        input_ids = tokenizer.encode(prompt, return_tensors="pt")
        prompt_length_tokens = input_ids.shape[1]

        effective_max_new_tokens = min(max_new_tokens, model_max_length - prompt_length_tokens)
        effective_max_new_tokens = max(1, effective_max_new_tokens)

        if prompt_length_tokens >= model_max_length:
            self.logger.warning(
                f"Prompt (tokens: {prompt_length_tokens}) exceeds or fills model's absolute max input length ({model_max_length}). Truncating prompt."
            )
            target_length = max(10, model_max_length - max_new_tokens)
            if target_length <= 0:
                self.logger.warning(
                    f"Model max length ({model_max_length}) too small to even fit a truncated prompt for generation. Returning empty string."
                )
                return ""
            input_ids = input_ids[:, -target_length:]
            prompt = tokenizer.decode(input_ids[0], skip_special_tokens=True)
            prompt_length_tokens = input_ids.shape[1]

            if prompt_length_tokens >= model_max_length:
                self.logger.warning(
                    "Prompt still fills entire model context after truncation. Cannot generate new tokens. Returning empty string."
                )
                return ""

        generation_kwargs = {
            "max_new_tokens": effective_max_new_tokens,
            "num_return_sequences": 1,
            "do_sample": True,
            "temperature": temperature,
            "truncation": True,
            "pad_token_id": tokenizer.pad_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        }

        if pipeline_info["device"] >= 0 and "torch" in sys.modules and torch.cuda.is_available():
            input_ids = input_ids.to(f'cuda:{pipeline_info["device"]}')
            model_pipeline.model.to(f'cuda:{pipeline_info["device"]}')

        result_raw = model_pipeline(prompt, **generation_kwargs)

        if (
            isinstance(result_raw, list)
            and len(result_raw) > 0
            and "generated_text" in result_raw[0]
        ):
            generated_text = result_raw[0]["generated_text"]
            if generated_text.startswith(prompt):
                return generated_text[len(prompt) :].strip()
            return generated_text.strip()
        else:
            self.logger.error(
                f"Unexpected model output format from {pipeline_info['model_name']} on device {pipeline_info['device']}: {result_raw}"
            )
            raise ReasonerError(
                "Model returned an unexpected output format.", code="BAD_MODEL_OUTPUT"
            )

    async def _async_generate_text(self, prompt: str, max_length: int, temperature: float) -> str:
        pipeline_info = await self._get_next_pipeline()
        if pipeline_info is None:
            raise ReasonerError(
                "No text generation models are loaded or available.",
                code="MODEL_NOT_INITIALIZED",
            )

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._generate_text_sync,
                pipeline_info,
                prompt,
                max_length,
                temperature,
            )
            return result
        except FuturesTimeoutError:
            METRICS["inference_errors"].labels(type="timeout", error_code="TIMEOUT").inc()
            raise ReasonerError("Model inference timed out", code="TIMEOUT")
        except Exception as e:
            error_code = "GENERATION_ERROR"
            message = f"Text generation failed: {e}"
            if "index out of range in self" in str(
                e
            ) or "The attention mask is not all zeros" in str(e):
                error_code = "INVALID_INPUT_LENGTH"
                message = (
                    f"Model generation received invalid input length or attention mask issue: {e}"
                )
            elif "CUDA out of memory" in str(e):
                error_code = "CUDA_OUT_OF_MEMORY"
                message = f"CUDA out of memory during generation: {e}"
            elif "not initialized" in str(e) or "Model not loaded" in str(e):
                error_code = "MODEL_NOT_READY"
                message = f"Model was not ready for generation: {e}"

            METRICS["inference_errors"].labels(type="generation", error_code=error_code).inc()
            self.logger.error(
                f"Error during text generation for model {pipeline_info['model_name']} on device {pipeline_info['device']}: {message}",
                exc_info=True,
            )
            raise ReasonerError(message, code=error_code, original_exception=e)

    def _create_explanation_prompt(self, query: str, context: Dict[str, Any]) -> str:
        return (
            f"You are an expert explainable AI. Your task is to provide a concise, clear, and accurate explanation "
            f"for the following query based on the provided context. Focus on 'why' or 'how'. "
            f"Your explanation should be easy to understand for a non-technical audience, but also technically sound.\n\n"
            f"{{history_placeholder}}"
            f"Query: {query}\n\n"
            f"Context: {json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
            f"Explanation:"
        )

    def _create_reasoning_prompt(self, query: str, context: Dict[str, Any]) -> str:
        return (
            f"You are an expert logical reasoning AI. Your task is to provide a concise, clear, and accurate reasoning "
            f"for the following query based on the provided context. Focus on 'what' is the logical next step or conclusion. "
            f"Your reasoning should be precise and directly derived from the given information.\n\n"
            f"{{history_placeholder}}"
            f"Query: {query}\n\n"
            f"Context: {json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
            f"Reasoning:"
        )

    async def explain(self, query: str, context: Dict[str, Any] = None) -> ExplanationResult:
        context = context or {}
        sanitized_context = _sanitize_context(context)
        sanitized_query = _sanitize_input(query)

        METRICS["inference_total"].labels(type="explain").inc()
        with METRICS["inference_duration_seconds"].labels(type="explain").time():
            response_id = str(uuid.uuid4())
            response_text = ""
            response_type = "model_explanation"

            try:
                recent_history = await self.history.get_entries(limit=2)
                history_str = ""
                if recent_history:
                    history_str = "\n--- Recent Interactions ---\n"
                    for entry in recent_history:
                        history_str += f"Query: {entry.query}\n"
                        history_str += f"Response ({entry.response_type}): {entry.response}\n\n"
                    history_str += "---------------------------\n\n"

                if self.config.mock_mode or not self.pipelines:
                    response_text = _rule_based_fallback(
                        sanitized_query, sanitized_context, "explain"
                    )
                    response_type = "fallback_explanation"
                    self.logger.info(
                        json.dumps(
                            {
                                "event": "explanation_generated",
                                "id": response_id,
                                "query": sanitized_query,
                                "response_type": response_type,
                                "message": "Using rule-based fallback.",
                            }
                        )
                    )
                else:
                    prompt_template = self._create_explanation_prompt(
                        sanitized_query, sanitized_context
                    )
                    prompt = prompt_template.format(history_placeholder=history_str)

                    if self.config.log_prompts:
                        self.logger.info(
                            json.dumps(
                                {
                                    "event": "prompt_log",
                                    "type": "explain",
                                    "id": response_id,
                                    "prompt": prompt,
                                }
                            )
                        )
                    response_text = await self._async_generate_text(
                        prompt,
                        self.config.max_generation_tokens,
                        self.config.temperature_explain,
                    )
                    self.logger.info(
                        json.dumps(
                            {
                                "event": "explanation_generated",
                                "id": response_id,
                                "query": sanitized_query,
                                "response_type": response_type,
                            }
                        )
                    )

                timestamp = datetime.utcnow().isoformat()
                history_entry = ReasoningHistory(
                    id=response_id,
                    query=sanitized_query,
                    context=sanitized_context,
                    response=response_text,
                    response_type=response_type,
                    timestamp=timestamp,
                )
                await self.history.add_entry(history_entry)
                METRICS["inference_success"].labels(type="explain").inc()

                return ExplanationResult(
                    id=response_id,
                    query=sanitized_query,
                    explanation=response_text,
                    context_used=sanitized_context,
                    generated_by=response_type,
                    timestamp=timestamp,
                )
            except ReasonerError as e:
                self.logger.error(
                    json.dumps(
                        {
                            "event": "explanation_error",
                            "id": response_id,
                            "query": sanitized_query,
                            "code": e.code,
                            "message": e.message,
                            "original_exception": (
                                str(e.original_exception) if e.original_exception else None
                            ),
                        }
                    )
                )
                METRICS["inference_errors"].labels(type="explain", error_code=e.code).inc()
                raise
            except Exception as e:
                self.logger.error(
                    json.dumps(
                        {
                            "event": "explanation_unexpected_error",
                            "id": response_id,
                            "query": sanitized_query,
                            "message": str(e),
                        }
                    ),
                    exc_info=True,
                )
                METRICS["inference_errors"].labels(
                    type="explain", error_code="UNEXPECTED_ERROR"
                ).inc()
                raise ReasonerError(
                    f"An unexpected error occurred during reasoning: {e}",
                    code="UNEXPECTED_ERROR",
                    original_exception=e,
                )

    async def reason(self, query: str, context: Dict[str, Any] = None) -> ReasoningResult:
        context = context or {}
        sanitized_context = _sanitize_context(context)
        sanitized_query = _sanitize_input(query)

        METRICS["inference_total"].labels(type="reason").inc()
        with METRICS["inference_duration_seconds"].labels(type="reason").time():
            response_id = str(uuid.uuid4())
            response_text = ""
            response_type = "model_reasoning"

            try:
                recent_history = await self.history.get_entries(limit=2)
                history_str = ""
                if recent_history:
                    history_str = "\n--- Recent Interactions ---\n"
                    for entry in recent_history:
                        history_str += f"Query: {entry.query}\n"
                        history_str += f"Response ({entry.response_type}): {entry.response}\n\n"
                    history_str += "---------------------------\n\n"

                if self.config.mock_mode or not self.pipelines:
                    response_text = _rule_based_fallback(
                        sanitized_query, sanitized_context, "reason"
                    )
                    response_type = "fallback_reasoning"
                    self.logger.info(
                        json.dumps(
                            {
                                "event": "reasoning_generated",
                                "id": response_id,
                                "query": sanitized_query,
                                "response_type": response_type,
                                "message": "Using rule-based fallback.",
                            }
                        )
                    )
                else:
                    prompt_template = self._create_reasoning_prompt(
                        sanitized_query, sanitized_context
                    )
                    prompt = prompt_template.format(history_placeholder=history_str)

                    if self.config.log_prompts:
                        self.logger.info(
                            json.dumps(
                                {
                                    "event": "prompt_log",
                                    "type": "reason",
                                    "id": response_id,
                                    "prompt": prompt,
                                }
                            )
                        )
                    response_text = await self._async_generate_text(
                        prompt,
                        self.config.max_generation_tokens,
                        self.config.temperature_reason,
                    )
                    self.logger.info(
                        json.dumps(
                            {
                                "event": "reasoning_generated",
                                "id": response_id,
                                "query": sanitized_query,
                                "response_type": response_type,
                            }
                        )
                    )

                timestamp = datetime.utcnow().isoformat()
                history_entry = ReasoningHistory(
                    id=response_id,
                    query=sanitized_query,
                    context=sanitized_context,
                    response=response_text,
                    response_type=response_type,
                    timestamp=timestamp,
                )
                await self.history.add_entry(history_entry)
                METRICS["inference_success"].labels(type="reason").inc()

                return ReasoningResult(
                    id=response_id,
                    query=sanitized_query,
                    reasoning=response_text,
                    context_used=sanitized_context,
                    generated_by=response_type,
                    timestamp=timestamp,
                )
            except ReasonerError as e:
                self.logger.error(
                    json.dumps(
                        {
                            "event": "reasoning_error",
                            "id": response_id,
                            "query": sanitized_query,
                            "code": e.code,
                            "message": e.message,
                            "original_exception": (
                                str(e.original_exception) if e.original_exception else None
                            ),
                        }
                    )
                )
                METRICS["inference_errors"].labels(type="reason", error_code=e.code).inc()
                raise
            except Exception as e:
                self.logger.error(
                    json.dumps(
                        {
                            "event": "reasoning_unexpected_error",
                            "id": response_id,
                            "query": sanitized_query,
                            "message": str(e),
                        }
                    ),
                    exc_info=True,
                )
                METRICS["inference_errors"].labels(
                    type="reason", error_code="UNEXPECTED_ERROR"
                ).inc()
                raise ReasonerError(
                    f"An unexpected error occurred during reasoning: {e}",
                    code="UNEXPECTED_ERROR",
                    original_exception=e,
                )

    async def get_history(self, limit: int = 10) -> List[ReasoningHistory]:
        return await self.history.get_entries(limit)

    async def clear_history(self):
        await self.history.clear()

    async def _perform_health_check(self):
        self.logger.debug("Performing internal health check...")
        METRICS["last_health_check_timestamp"].set(datetime.utcnow().timestamp())

        try:
            await self.history.get_size()
            METRICS["health_status"].labels(component="history_db").set(1)
            self.logger.debug("History DB healthy.")
        except Exception as e:
            METRICS["health_status"].labels(component="history_db").set(0)
            self.logger.error(f"History DB unhealthy: {e}")

        if self.pipelines:
            METRICS["health_status"].labels(component="llm_models").set(1)
            self.logger.debug("LLM Models healthy (at least one loaded).")
        else:
            METRICS["health_status"].labels(component="llm_models").set(0)
            self.logger.warning("LLM Models unhealthy (no models loaded).")

        if self.executor is not None:
            METRICS["health_status"].labels(component="executor").set(1)
            self.logger.debug("Executor healthy.")
        else:
            METRICS["health_status"].labels(component="executor").set(0)
            self.logger.error("Executor unhealthy or shut down.")

        if os.getenv("OPENAI_API_KEY") or getattr(self.settings, "LLM_API_KEY_LOADED", False):
            METRICS["health_status"].labels(component="api_key_access").set(1)
            self.logger.debug("API Key access appears healthy.")
        else:
            METRICS["health_status"].labels(component="api_key_access").set(0)
            self.logger.warning("API Key access unhealthy (API key not found).")

    async def _periodic_health_check(self, interval_seconds: int = 3600):
        while True:
            try:
                await self._perform_health_check()
            except Exception as e:
                self.logger.error(f"Error during periodic health check: {e}", exc_info=True)
            await asyncio.sleep(interval_seconds)


def _process_prompt(prompt: str) -> str:
    return prompt.strip()


def _format_output(text: str) -> str:
    return text.strip()


async def _analyze_sentiment(text: str) -> str:
    if "positive" in text.lower():
        return "positive"
    elif "negative" in text.lower():
        return "negative"
    return "neutral"


def _placeholder_utility(data: Any) -> Any:
    return data


async def _placeholder_async(data: Dict[str, Any]) -> Dict[str, Any]:
    return data


def _validate_response(text: str) -> bool:
    return bool(text.strip())


# Input Validation for explain_result
if PYDANTIC_AVAILABLE:

    class ExplanationInput(BaseModel):
        result_id: str = Field(
            ...,
            pattern=r"^[a-zA-Z0-9_-]+$",
            description="Unique ID of the simulation result.",
        )
        status: str = Field(
            ...,
            pattern=r"^(COMPLETED|FAILED|ERROR|SKIPPED|TIMEOUT)$",
            description="Status of the simulation result.",
        )
        details: Dict[str, Any] = Field(
            default_factory=dict, description="Detailed results of the simulation."
        )

else:

    class ExplanationInput:
        def __init__(self, result_id: str, status: str, details: Dict[str, Any]):
            self.result_id = result_id
            self.status = status
            self.details = details


class ExplainableReasonerPlugin(ExplainableReasoner):
    def __init__(self, settings: Any = None):
        _settings = settings or ArbiterConfig()
        super().__init__(settings=_settings)
        if GlobalSecretsManager is not None:
            self.secrets_manager = GlobalSecretsManager()
        else:
            self.secrets_manager = None
        self.dlt_logger = DLTLogger.from_environment() if DLT_LOGGER_AVAILABLE else None

    async def initialize(self):
        await self.async_init()

    async def explain_result(self, result: Dict[str, Any]) -> str:
        correlation_id = result.get("id", str(uuid.uuid4()))

        try:
            if PYDANTIC_AVAILABLE:
                validated_result = ExplanationInput(
                    result_id=result.get("id", "N/A"),
                    status=result.get("status", "UNKNOWN"),
                    details=result,
                )
            else:
                validated_result = result
                # Basic manual validation
                if (
                    not isinstance(validated_result, dict)
                    or "id" not in validated_result
                    or "status" not in validated_result
                ):
                    raise ValueError("Invalid result input format.")
                # Manual regex check for status
                if not re.match(
                    r"^(COMPLETED|FAILED|ERROR|SKIPPED|TIMEOUT)$",
                    validated_result.get("status", "UNKNOWN"),
                ):
                    raise ValueError("Invalid status format.")
                validated_result = ExplanationInput(
                    result_id=validated_result.get("id"),
                    status=validated_result.get("status"),
                    details=validated_result,
                )

        except ValidationError as e:
            logger.error(f"Input validation failed for explain_result: {e}", exc_info=True)
            if self.dlt_logger:
                await self.dlt_logger.add_entry(
                    kind="explain",
                    name="explanation_error",
                    detail={
                        "result_id": result.get("id"),
                        "error": str(e),
                        "reason": "input_validation_failed",
                    },
                    agent_id="explain",
                    correlation_id=correlation_id,
                )
            raise ReasonerError(
                f"Input validation failed for explanation: {e}",
                code="INPUT_VALIDATION_ERROR",
            )
        except ValueError as e:
            logger.error(f"Input validation failed for explain_result: {e}", exc_info=True)
            if self.dlt_logger:
                await self.dlt_logger.add_entry(
                    kind="explain",
                    name="explanation_error",
                    detail={
                        "result_id": result.get("id"),
                        "error": str(e),
                        "reason": "input_validation_failed",
                    },
                    agent_id="explain",
                    correlation_id=correlation_id,
                )
            raise ReasonerError(
                f"Input validation failed for explanation: {e}",
                code="INPUT_VALIDATION_ERROR",
            )

        explanation_text = ""
        with METRICS["explanation_latency"].labels(result_id=validated_result.result_id).time():
            try:
                if not LANGCHAIN_OPENAI_AVAILABLE:
                    explanation_text = _rule_based_fallback(
                        f"explain result {validated_result.result_id}",
                        validated_result.details,
                        "explain",
                    )
                    logger.warning(
                        "langchain_openai not available. Using fallback for explanation generation."
                    )
                else:
                    grok_api_key = None
                    if self.secrets_manager:
                        grok_api_key = self.secrets_manager.get_secret(
                            "GROK_API_KEY", required=False
                        )

                    if not grok_api_key:
                        logger.warning(
                            "GROK_API_KEY not found. Using fallback for explanation generation."
                        )
                        explanation_text = _rule_based_fallback(
                            f"explain result {validated_result.result_id}",
                            validated_result.details,
                            "explain",
                        )
                    else:
                        llm = ChatOpenAI(api_key=grok_api_key, model="grok-3")
                        prompt_content = f"Explain the simulation result: {json.dumps(validated_result.details, indent=2)}. Focus on the overall outcome and key metrics."
                        prompt = PromptTemplate.from_template(prompt_content)
                        response = await llm.ainvoke(prompt)
                        explanation_text = response.content.strip()

                if self.dlt_logger:
                    await self.dlt_logger.add_entry(
                        kind="explain",
                        name="explanation_generated",
                        detail={
                            "result_id": validated_result.result_id,
                            "explanation": explanation_text[:200] + "...",
                            "status": validated_result.status,
                        },
                        agent_id="explain",
                        correlation_id=correlation_id,
                    )
                return explanation_text
            except Exception as e:
                logger.error(
                    f"Failed to generate explanation for result {validated_result.result_id}: {e}",
                    exc_info=True,
                )
                if self.dlt_logger:
                    await self.dlt_logger.add_entry(
                        kind="explain",
                        name="explanation_error",
                        detail={
                            "result_id": validated_result.result_id,
                            "error": str(e),
                            "reason": "llm_generation_failed",
                        },
                        agent_id="explain",
                        correlation_id=correlation_id,
                    )
                raise ReasonerError(
                    f"Failed to generate explanation: {e}",
                    code="EXPLANATION_GENERATION_FAILED",
                )

    async def execute(self, action: str, **kwargs) -> Any:
        try:
            if action == "explain":
                return await self.explain_result(kwargs.get("result"))
            elif action == "reason":
                return await self.reason(kwargs.get("query"), kwargs.get("context"))
            elif action == "get_history":
                return await self.get_history(kwargs.get("limit", 10))
            elif action == "clear_history":
                await self.clear_history()
                return {"status": "success", "message": "History cleared"}
            elif action == "get_metrics":
                if not PROMETHEUS_AVAILABLE:
                    raise ReasonerError(
                        "Prometheus client not available.", code="METRICS_UNAVAILABLE"
                    )
                return generate_latest(registry=_metrics_registry).decode("utf-8")
            elif action == "get_health":
                if not PROMETHEUS_AVAILABLE:
                    return {
                        "status": "warning",
                        "message": "Prometheus client not available, limited health status provided.",
                    }

                await self._perform_health_check()
                health_status = {}
                for metric in _metrics_registry.collect():
                    if metric.name == "reasoner_health_status":
                        for sample in metric.samples:
                            component = sample.labels.get("component")
                            if component:
                                health_status[component] = (
                                    "healthy" if sample.value == 1.0 else "unhealthy"
                                )
                return {
                    "status": "success",
                    "health": health_status,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            else:
                raise ValueError(f"Unknown action for ExplainableReasonerPlugin: {action}")
        except ReasonerError as e:
            self.logger.critical(
                f"Critical ReasonerError during execute action '{action}': {e.message} (Code: {e.code})",
                exc_info=True,
            )
            return {"status": "error", "message": e.message, "code": e.code}
        except Exception as e:
            self.logger.critical(
                f"Unexpected critical error during execute action '{action}': {e}",
                exc_info=True,
            )
            return {
                "status": "error",
                "message": f"An unexpected error occurred: {e}",
                "code": "UNEXPECTED_CRITICAL_ERROR",
            }
