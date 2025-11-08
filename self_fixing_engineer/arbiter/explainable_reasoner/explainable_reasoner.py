import os
import sys
import json
import asyncio
import collections
import uuid
import time
import base64
import logging
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Callable, AsyncGenerator, Coroutine, Tuple
from enum import Enum
from typing_extensions import Self
import structlog
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from logging.handlers import RotatingFileHandler
from cryptography.fernet import Fernet
import secrets
from PIL import Image
import io
import hashlib

# Attempt to set UVloop policy if available
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# Add to requirements.txt: pybreaker>=1.0.0, sentry-sdk, PyJWT>=2.10.2, transformers>=4.49.1, pydantic>=2.11.7, pytest, pytest-asyncio, pytest-cov, hypothesis, locust, huggingface_hub, Pillow, cryptography, prometheus-client
__version__ = "1.2.0"

from prometheus_client import Gauge, Counter, Histogram

# --- Corrected Internal Imports ---
from arbiter.explainable_reasoner.metrics import METRICS, get_or_create_metric, get_metrics_content
from arbiter.explainable_reasoner.prompt_strategies import PromptStrategyFactory
from arbiter.explainable_reasoner.history_manager import (
    BaseHistoryManager, SQLiteHistoryManager, PostgresHistoryManager, RedisHistoryManager
)
from arbiter.explainable_reasoner.audit_ledger import AuditLedgerClient
from arbiter.explainable_reasoner.adapters import LLMAdapter, LLMAdapterFactory
from arbiter.explainable_reasoner.utils import (
    _sanitize_context, _simple_text_sanitize, _rule_based_fallback,
    _format_multimodal_for_prompt, rate_limited, redact_pii
)
from arbiter.explainable_reasoner.reasoner_errors import ReasonerError, ReasonerErrorCode

# Availability Checks & Conditional Imports
try:
    import pybreaker
    PYBREAKER_AVAILABLE = True
except ImportError:
    pybreaker = None
    PYBREAKER_AVAILABLE = False
    logging.getLogger(__name__).warning("Warning: pybreaker not found. Circuit breaker for model reloading is disabled.")
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    REDIS_AVAILABLE = False
    logging.getLogger(__name__).warning("Warning: redis.asyncio not found. Redis history manager is disabled.")
try:
    import asyncpg
    POSTGRES_AVAILABLE = True
except ImportError:
    asyncpg = None
    POSTGRES_AVAILABLE = False
    logging.getLogger(__name__).warning("Warning: asyncpg not found. Postgres history manager is disabled.")
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False
    logging.getLogger(__name__).warning("Warning: httpx not found. HTTP client features will fail.")
try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    jwt = None
    JWT_AVAILABLE = False
    logging.getLogger(__name__).warning("Warning: PyJWT not found. JWT authentication is disabled.")
try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    class DummySpan:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
        def set_attribute(self, *args, **kwargs): pass
        def record_exception(self, *args, **kwargs): pass
        def set_status(self, *args, **kwargs): pass
    class DummyTracer:
        def start_as_current_span(self, name: str, *args, **kwargs): return DummySpan()
    logging.getLogger(__name__).warning("Warning: opentelemetry not found. Tracing is disabled.")

if OTEL_AVAILABLE:
    tracer = trace.get_tracer(__name__)
else:
    # The DummyTracer is already defined, just need to instantiate it
    tracer = DummyTracer()

TRANSFORMERS_AVAILABLE = False
try:
    from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    import torch
    from huggingface_hub import HfApi
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    torch = None
    logging.getLogger(__name__).warning("Warning: Transformers/PyTorch not found. Using fallback mode.")
try:
    import sentry_sdk
    SENTRY_AVAILABLE = True
    if os.getenv("REASONER_SENTRY_DSN"):
        sentry_sdk.init(dsn=os.getenv("REASONER_SENTRY_DSN"), traces_sample_rate=0.1, release=f"explainable_reasoner@{__version__}")
except ImportError:
    sentry_sdk = None
    SENTRY_AVAILABLE = False

# Structured Logging Setup
log_file_path = os.getenv("REASONER_LOG_PATH", "reasoner.log")
log_handler = RotatingFileHandler(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5)
log_handler.setFormatter(logging.Formatter('%(message)s'))
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
_logger = structlog.get_logger(__name__)
logger = structlog.get_logger("ExplainableReasoner")
logging.getLogger().addHandler(log_handler)
logging.getLogger().setLevel(logging.INFO)

# Core Data Models and Errors
class SensitiveValue:
    """A wrapper for sensitive values to prevent accidental logging."""
    def __init__(self, value: str): self._value = value
    def get_actual_value(self) -> str: return self._value
    def __str__(self) -> str: return "[REDACTED]"
    def __repr__(self) -> str: return self.__str__()

# --- Consolidated and Corrected ReasonerConfig ---
class ReasonerConfig(BaseModel):
    """
    Configuration for the Explainable Reasoner, validated by Pydantic.
    All settings can be overridden by environment variables with the prefix REASONER_
    (e.g., REASONER_MODEL_NAME=... or REASONER_STRICT_MODE=true).
    """
    model_reload_retries: int = Field(5, ge=1, description="Max retries for model reload")
    context_buffer_tokens: int = Field(50, ge=10, description="Token buffer for context")
    max_context_bytes: int = Field(1_000_000, ge=1000, description="Max context size in bytes")
    calls_per_second: float = Field(10.0, gt=0.0, description="Rate limit per second")
    max_workers: int = Field(4, ge=1, description="Thread pool workers")
    max_concurrent_requests: int = Field(8, ge=1, description="Concurrent inference limit")
    model_configs: List[Dict[str, Any]] = Field(default_factory=list, description="Model configurations")
    model_name: str = Field("distilgpt2", description="Default model name")
    device: Union[int, str] = Field(-1, description="Device ID or name")
    mock_mode: bool = Field(False, description="Enable mock mode")
    cloud_fallback_model_enabled: bool = Field(False, description="Enable cloud fallback")
    cloud_fallback_model_name: str = Field("openai/gpt-4o-mini", description="Cloud model name")
    cloud_fallback_api_key: Optional[SensitiveValue] = Field(None, description="Cloud API key")
    strict_mode: bool = Field(False, description="Strict error handling")
    model_cache_dir: str = Field(str(Path.home() / '.cache' / 'huggingface' / 'hub'), description="Model cache dir")
    transformers_offline: bool = Field(False, description="Offline model loading for transformers")
    offline_only: bool = Field(False, description="Disables all cloud features and enforces local-only operation.")
    model_cooldown_period: int = Field(300, ge=60, description="Model cooldown in seconds")
    max_query_length: int = Field(2048, ge=100, description="Max query length")
    max_generation_tokens: int = Field(512, ge=50, description="Max tokens to generate")
    temperature_explain: float = Field(0.7, ge=0.0, le=2.0, description="Temperature for explain")
    temperature_reason: float = Field(0.7, ge=0.0, le=2.0, description="Temperature for reason")
    log_prompts: bool = Field(False, description="Log prompts")
    distributed_history_backend: str = Field("sqlite", description="History backend")
    history_db_path: str = Field("./history.db", description="SQLite DB path")
    postgres_db_url: Optional[SensitiveValue] = Field(None, description="Postgres URL")
    redis_url: Optional[SensitiveValue] = Field(None, description="Redis URL")
    max_history_size: int = Field(10, ge=1, description="Max history entries")
    history_retention_days: int = Field(30, ge=0, description="History retention days")
    audit_log_enabled: bool = Field(True, description="Enable audit logging")
    audit_ledger_url: Optional[SensitiveValue] = Field(None, description="Audit URL")
    audit_api_key: Optional[SensitiveValue] = Field(None, description="Audit API key")
    audit_max_retries: int = Field(3, ge=1, description="Audit retry limit")
    cache_ttl: int = Field(3600, ge=60, description="Cache TTL in seconds")
    jwt_secret_key: SensitiveValue = Field(
        default_factory=lambda: SensitiveValue("default-secret-key-change-me"),
        description="JWT secret key for RBAC. CHANGE THIS IN PRODUCTION."
    )
    sanitization_options: Dict[str, Any] = Field(default_factory=lambda: {
        "redact_keys": ["api_key", "password"], "redact_patterns": [r"\b\d{16}\b"], "max_nesting_depth": 10,
    }, description="Sanitization settings")
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='ignore')

    @classmethod
    def from_env(cls) -> Self:
        """Load config from environment variables with prefix REASONER_."""
        env_config = {}
        for k, v in os.environ.items():
            if k.startswith("REASONER_"):
                key = k.lower().replace("reasoner_", "")
                if key in cls.model_fields:
                    env_config[key] = v

        for key in ['cloud_fallback_api_key', 'postgres_db_url', 'redis_url', 'audit_ledger_url', 'audit_api_key', 'jwt_secret_key']:
            if key in env_config and env_config[key] is not None:
                env_config[key] = SensitiveValue(env_config[key])

        for key in ['model_configs', 'sanitization_options']:
            if key in env_config and isinstance(env_config[key], str):
                try:
                    env_config[key] = json.loads(env_config[key])
                except json.JSONDecodeError:
                    logger.error("config_invalid_json", field=key, value=env_config[key])
                    del env_config[key]

        try:
            return cls.model_validate(env_config)
        except ValidationError as e:
            logger.error("config_load_failure", error=str(e), exc_info=True)
            raise

    def get_public_config(self) -> Dict[str, Any]:
        """Returns a serializable dictionary with sensitive values redacted."""
        # Use python mode first to handle custom types
        config_dict = self.model_dump(mode='python')
        # Manually redact SensitiveValue fields
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if isinstance(value, SensitiveValue):
                config_dict[field_name] = "[REDACTED]"
        return config_dict

# Conditional Import for MultiModalData
try:
    from arbiter.models.multi_modal_schemas import (
        MultiModalData, ImageAnalysisResult, AudioAnalysisResult, VideoAnalysisResult, MultiModalAnalysisResult
    )
    MULTI_MODAL_SCHEMAS_AVAILABLE = True
except ImportError:
    logger.warning("multi_modal_schemas_missing", message="Using dummy MultiModalData")
    class MultiModalData(BaseModel):
        data_type: str
        data: bytes
        metadata: Dict = {}
    class MultiModalAnalysisResult(BaseModel): pass
    class ImageAnalysisResult(MultiModalAnalysisResult): pass
    class AudioAnalysisResult(MultiModalAnalysisResult): pass
    class VideoAnalysisResult(MultiModalAnalysisResult): pass
    MULTI_MODAL_SCHEMAS_AVAILABLE = False

# --- Main Class ---
class ExplainableReasoner:
    """
    Core reasoner for generating explanations using transformer models.
    Supports multi-model loading, async inference, history, and auditing.
    """
    def __init__(self, config: ReasonerConfig, settings: Optional[Any] = None, prompt_strategy_name: str = "default"):
        self.config: ReasonerConfig = config
        self.logger = structlog.get_logger(self.__class__.__name__)
        if settings and not isinstance(settings, collections.namedtuple):
            self.logger.warning("non_dummy_settings_provided", message="Ensure compatibility with ReasonerConfig")
        self.settings: Any = settings or collections.namedtuple('Settings', [])()
        self._instance_id = str(uuid.uuid4())
        self._model_pipelines: List[Dict[str, Any]] = []
        self._next_pipeline_idx: int = 0
        self._pipeline_lock: asyncio.Lock = asyncio.Lock()
        self._model_reload_tasks: Dict[str, asyncio.Task] = {}
        self._is_ready: bool = False
        self._failed_model_count: int = 0
        self._last_health_check: Optional[Dict[str, Any]] = None
        self._session_auth_enabled: bool = os.getenv("REASONER_SESSION_AUTH", "False").lower() == "true"

        if self.config.jwt_secret_key.get_actual_value() == "default-secret-key-change-me":
            raise ReasonerError("Change JWT secret! The default key is insecure.", code=ReasonerErrorCode.SECURITY_VIOLATION)
        if self._session_auth_enabled and (not os.getenv("JWT_AUD") or not os.getenv("JWT_ISS")):
            raise ReasonerError("JWT AUD/ISS env vars required for session auth.", code=ReasonerErrorCode.CONFIGURATION_ERROR)

        if self.config.offline_only and not TRANSFORMERS_AVAILABLE:
            msg = "Offline mode is enabled, but 'transformers' library is not installed."
            self.logger.critical("dependency_missing", library="transformers", mode="offline_only")
            raise ReasonerError(msg, code=ReasonerErrorCode.CONFIGURATION_ERROR)
        
        self.audit_ledger_client: Optional[AuditLedgerClient] = None
        if self.config.audit_log_enabled and self.config.audit_ledger_url:
            self.audit_ledger_client = AuditLedgerClient(
                ledger_url=self.config.audit_ledger_url.get_actual_value(),
                api_key=self.config.audit_api_key.get_actual_value() if self.config.audit_api_key else None
            )

        self._initialize_history_manager()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._inference_semaphore: Optional[asyncio.Semaphore] = None
        self._prompt_strategy = PromptStrategyFactory.get_strategy(prompt_strategy_name, self.logger)
        self._redis_client: Optional[aioredis.Redis] = None
        self._redis_pool: Optional[aioredis.ConnectionPool] = None

        if PYBREAKER_AVAILABLE:
            self._reload_breaker = pybreaker.CircuitBreaker(
                fail_max=self.config.model_reload_retries,
                reset_timeout=self.config.model_cooldown_period
            )
        else:
            self._reload_breaker = None
            self.logger.warning("pybreaker_unavailable", message="Circuit breaker for model reloading is disabled.")
        
        # Start tracemalloc for memory leak profiling
        # BUG FIX: Corrected function call from is_started() to is_tracing()
        if not tracemalloc.is_tracing():
            tracemalloc.start()

        get_or_create_metric(Gauge, "reasoner_instances", "Number of active reasoner instances", labelnames=("instance_id",)).labels(instance_id=self._instance_id).inc()
        self.logger.info("reasoner_initialized", config=self.config.get_public_config())

    def _initialize_history_manager(self) -> None:
        """Initializes the history manager based on configuration."""
        backend_choice = self.config.distributed_history_backend
        db_path = Path(self.config.history_db_path)

        if backend_choice == "postgres" and POSTGRES_AVAILABLE and self.config.postgres_db_url:
            self.history: BaseHistoryManager = PostgresHistoryManager(
                self.config.postgres_db_url.get_actual_value(), self.config.max_history_size,
                self.config.history_retention_days, self.audit_ledger_client
            )
        elif backend_choice == "redis" and REDIS_AVAILABLE and self.config.redis_url:
            self.history: BaseHistoryManager = RedisHistoryManager(
                self.config.redis_url.get_actual_value(), self.config.max_history_size,
                self.config.history_retention_days, self.audit_ledger_client
            )
        else:
            if backend_choice != "sqlite":
                self.logger.warning(
                    "history_backend_fallback",
                    configured_backend=backend_choice,
                    reason=f"Libraries or config for '{backend_choice}' are not available.",
                    fallback_to="sqlite"
                )
            if not db_path.parent.exists() or not db_path.parent.is_dir():
                msg = f"Invalid history DB path: Directory '{db_path.parent}' does not exist."
                self.logger.critical("history_init_failed_no_fallback", path=str(db_path.parent))
                raise ReasonerError(msg, code=ReasonerErrorCode.CONFIGURATION_ERROR)

            self.history: BaseHistoryManager = SQLiteHistoryManager(
                db_path, self.config.max_history_size, self.config.history_retention_days,
                self.audit_ledger_client
            )
        self._backend_name = self.history._backend_name
        self.logger.info("history_manager_initialized", backend=self._backend_name)

    async def async_init(self) -> None:
        """
        Performs asynchronous initialization of DB, thread pool, and models.
        """
        self.logger.info("async_initialization_start")
        start_time = time.monotonic()
        try:
            self.config.max_workers = os.cpu_count() or self.config.max_workers
            self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
            self._inference_semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
            self.logger.info(
                "executor_initialized",
                max_workers=self.config.max_workers,
                max_concurrent_requests=self.config.max_concurrent_requests
            )

            if self.config.redis_url and REDIS_AVAILABLE:
                await self._init_redis()

            await self._init_history_db()

            await self._initialize_models_async()
            if not self._model_pipelines:
                self.logger.critical("no_models_loaded", message="Reasoner has no inference capability")
            elif self._failed_model_count > 0:
                self.logger.warning("degraded_mode", failed_models=self._failed_model_count)

            if self._model_pipelines and not self.config.mock_mode:
                await self._run_model_readiness_test()

            self._pruning_task = asyncio.create_task(self._run_history_pruner())
            self._is_ready = True
            self.logger.info("async_initialization_complete")
        except Exception as e:
            self.logger.error("async_initialization_failed", error=str(e), exc_info=True)
            self._is_ready = False
            raise ReasonerError(f"Initialization failed: {e}", code=ReasonerErrorCode.CONFIGURATION_ERROR, original_exception=e) from e
        finally:
            if self._executor and not self._is_ready:
                await asyncio.to_thread(self._executor.shutdown, wait=False, cancel_futures=True)
            get_or_create_metric(Histogram, "reasoner_init_duration_seconds", "Duration of reasoner initialization").observe(time.monotonic() - start_time)

    async def _init_redis(self):
        try:
            if self.config.redis_url:
                redis_url_str = self.config.redis_url.get_actual_value()
                pool_size = self.config.max_concurrent_requests * 2
                self._redis_pool = aioredis.ConnectionPool.from_url(redis_url_str, max_connections=pool_size)
                self._redis_client = aioredis.Redis(connection_pool=self._redis_pool)
                await self._redis_client.ping()
                self.logger.info("redis_connection_pool_created", max_connections=pool_size)
        except Exception as e:
            self.logger.error("redis_pool_creation_failed", error=str(e), exc_info=True)
            self._redis_client = None
            self._redis_pool = None

    async def _init_history_db(self):
        retries = 3
        for attempt in range(retries):
            try:
                await self.history.init_db()
                self.logger.info("history_db_initialized")
                break
            except Exception as e:
                if attempt == retries - 1:
                    raise ReasonerError(f"History DB init failed after {retries} retries", code=ReasonerErrorCode.HISTORY_WRITE_FAILED, original_exception=e) from e
                self.logger.warning("history_db_init_failed", attempt=attempt+1, error=str(e))
                await asyncio.sleep(2 ** attempt)

        try:
            size = await self.history.get_size()
            get_or_create_metric(Gauge, "reasoner_history_entries_current", "Current number of history entries", labelnames=("backend",)).labels(backend=self._backend_name).set(size)
        except Exception as e:
            self.logger.error("history_size_fetch_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Failed to get history size: {e}", code=ReasonerErrorCode.HISTORY_READ_FAILED, original_exception=e) from e
    
    async def _run_model_readiness_test(self):
        async with self._inference_semaphore:
            try:
                result = await self._async_generate_text("Health check", max_length=5, temperature=0.5)
                get_or_create_metric(Counter, "reasoner_health_check_success", "Successful health checks", labelnames=("type",)).labels(type="model_init").inc()
                self.logger.info("model_readiness_test_passed", result=result[:50])
                self._last_health_check = {"status": "healthy", "messages": ["Model readiness test passed"]}
            except Exception as e:
                get_or_create_metric(Counter, "reasoner_health_check_errors", "Failed health checks", labelnames=("type",)).labels(type="model_init").inc()
                self.logger.warning("model_readiness_test_failed", error=str(e), exc_info=True)
                self._last_health_check = {"status": "degraded", "messages": [f"Model readiness test failed: {e}"]}

    async def _run_history_pruner(self) -> None:
        """Background task to periodically prune old history entries."""
        prune_interval_seconds = int(os.getenv("REASONER_PRUNE_INTERVAL_SECONDS", 24 * 60 * 60))
        while True:
            try:
                await self.history.prune_old_entries()
                self.logger.debug("history_prune_completed")
            except Exception as e:
                self.logger.error("history_prune_failed", error=str(e), exc_info=True)
            await asyncio.sleep(prune_interval_seconds)

    async def _initialize_models_async(self) -> None:
        """Loads models from Hugging Face or cloud backends."""
        if self.config.mock_mode:
            self.logger.info("mock_mode_enabled", message="Skipping model initialization")
            return
        if not TRANSFORMERS_AVAILABLE and not (self.config.cloud_fallback_model_enabled and not self.config.offline_only):
            self.logger.warning("no_models_available", message="Reasoner in fallback mode")
            return
        if self.config.cloud_fallback_model_enabled and not (self.config.cloud_fallback_api_key and self.config.cloud_fallback_api_key.get_actual_value()):
            self.logger.warning("cloud_fallback_disabled", reason="No API key provided")
            self.config.cloud_fallback_model_enabled = False
        if self.config.offline_only:
            self.logger.info("offline_only_mode_enabled", message="Cloud features are disabled.")

        model_configs_to_load = self.config.model_configs or []
        seen = set()
        unique_configs = []
        for cfg in model_configs_to_load:
            key = f"{cfg.get('model_name', self.config.model_name)}-{cfg.get('device', self.config.device)}"
            if key in seen:
                self.logger.warning("duplicate_model_config", key=key)
                continue
            seen.add(key)
            unique_configs.append(cfg)
        if self.config.cloud_fallback_model_enabled and not self.config.offline_only:
            unique_configs.insert(0, {"model_name": self.config.cloud_fallback_model_name})

        tasks = [asyncio.wait_for(self._load_single_model(cfg), timeout=600) for cfg in unique_configs]
        loaded_pipelines_info = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(loaded_pipelines_info):
            model_name = unique_configs[i].get("model_name", self.config.model_name)
            device = unique_configs[i].get("device", self.config.device)
            if isinstance(result, dict):
                self._model_pipelines.append(result)
                get_or_create_metric(Counter, "reasoner_model_load_success", "Successful model loads", labelnames=("model_name", "device")).labels(model_name=model_name, device=str(device)).inc()
            elif isinstance(result, Exception):
                self._failed_model_count += 1
                self.logger.error("model_load_failed", model_name=model_name, device=device, error=str(result), exc_info=True)
                get_or_create_metric(Counter, "reasoner_model_load_errors", "Failed model loads", labelnames=("model_name", "device")).labels(model_name=model_name, device=str(device)).inc()

    async def _load_single_model(self, model_cfg: Dict[str, Any], is_reload: bool = False) -> Optional[Dict[str, Any]]:
        model_name = model_cfg.get("model_name", self.config.model_name)
        device = model_cfg.get("device", self.config.device)
        if is_reload:
            model_key = f"{model_name}-{device}"
            await self._unload_model(model_key)
        try:
            if any(model_name.lower().startswith(p) for p in ("openai/", "google/", "anthropic/")) and not self.config.offline_only:
                adapter_type = model_name.split('/')[0].lower()
                adapter_config = {
                    "model_name": model_name,
                    "api_key": self.config.cloud_fallback_api_key.get_actual_value() if self.config.cloud_fallback_api_key else None,
                    "adapter_type": adapter_type
                }
                adapter = LLMAdapterFactory.get_adapter(adapter_config)
                pipeline_info = {
                    'pipeline': adapter, 'model_name': model_name, 'device': -2,
                    'last_failed_at': None, 'version': model_cfg.get('version', 'unknown')
                }
                self.logger.info("cloud_adapter_initialized", model_name=model_name)
                return pipeline_info
            
            if not TRANSFORMERS_AVAILABLE:
                self.logger.warning("transformers_unavailable", model_name=model_name)
                return None
            
            if not self.config.offline_only:
                try:
                    api = HfApi()
                    model_info = api.model_info(model_name)
                    if model_info.cardData and model_info.cardData.get("security-scan-status") == "malicious":
                        raise ReasonerError(f"Model {model_name} is marked as malicious.", code=ReasonerErrorCode.SECURITY_VIOLATION)
                except Exception as e:
                    self.logger.warning("model_scan_failed", model_name=model_name, error=str(e))
                    pass

            self.logger.info("loading_hf_model", model_name=model_name, device=device, attempt=1)
            pipeline_info = await self._execute_in_thread(self._load_hf_pipeline_sync, model_cfg, timeout=600)
            
            await asyncio.wait_for(
                self._execute_in_thread(lambda: pipeline_info['pipeline']("test", max_new_tokens=5)),
                timeout=15
            )
            self.logger.info("model_load_success", model_name=model_name, device=device, version=pipeline_info['version'])
            return pipeline_info
        except Exception as e:
            self.logger.error("model_load_failed", model_name=model_name, device=device, error=str(e), exc_info=True)
            get_or_create_metric(Counter, "reasoner_model_load_errors", "Failed model loads", labelnames=("model_name", "device")).labels(model_name=model_name, device=str(device)).inc()
            if self.config.transformers_offline and "offline" in str(e).lower():
                raise ReasonerError(f"Model '{model_name}' not in cache for offline mode", code=ReasonerErrorCode.MODEL_LOAD_FAILED, original_exception=e) from e
            if self.config.strict_mode and not is_reload:
                raise ReasonerError(f"Model initialization failed for {model_name}", code=ReasonerErrorCode.MODEL_LOAD_FAILED, original_exception=e) from e
            return None

    def _load_hf_pipeline_sync(self, model_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous loading of a Hugging Face pipeline. This runs in a thread."""
        model_name = model_cfg.get("model_name", self.config.model_name)
        device = model_cfg.get("device", self.config.device)
        model_kwargs = {"cache_dir": self.config.model_cache_dir, "local_files_only": self.config.transformers_offline, "trust_remote_code": False}
        
        if device != -1 and torch and torch.cuda.is_available():
            try:
                device_id = int(device)
                props = torch.cuda.get_device_properties(device_id)
                if props.total_memory < 8e9: # Warn for GPUs with less than 8GB VRAM
                    self.logger.warning("low_gpu_memory", device=device_id, memory_gb=round(props.total_memory/1e9, 2))
                
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True
                )
                model_kwargs.update({"quantization_config": quantization_config, "torch_dtype": torch.float16, "device_map": {"": device_id}})
            except (ValueError, IndexError):
                 self.logger.warning("invalid_cuda_device", device=device, fallback_to_cpu=True)
                 device = -1 # Fallback to CPU
        else:
            device = -1
            self.logger.info("loading_on_cpu", model_name=model_name)
        
        tokenizer = AutoTokenizer.from_pretrained(model_name, **model_kwargs)
        model_obj = AutoModelForCausalLM.from_pretrained(model_name, **model_kwargs)
        
        text_gen_pipeline = pipeline("text-generation", model=model_obj, device=device if device != -1 else None, tokenizer=tokenizer)
        
        if text_gen_pipeline.tokenizer.pad_token_id is None:
            text_gen_pipeline.tokenizer.pad_token_id = text_gen_pipeline.tokenizer.eos_token_id
            
        return {
            'pipeline': text_gen_pipeline, 'model_name': model_name, 'device': device,
            'last_failed_at': None, 'version': model_cfg.get('version', 'unknown')
        }

    async def _execute_in_thread(self, fn: Callable, *args: Any, timeout: int = 30, **kwargs: Any) -> Any:
        """Runs a synchronous function in the ThreadPoolExecutor."""
        if not self._executor or self._executor._shutdown:
            get_or_create_metric(Counter, "reasoner_executor_restarts_total", "Total number of executor restarts").inc()
            self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
            self.logger.info("executor_restarted", max_workers=self.config.max_workers)
        loop = asyncio.get_running_loop()
        try:
            get_or_create_metric(Gauge, "reasoner_executor_queue_size", "Current size of the executor queue").set(self._executor._work_queue.qsize())
            future = loop.run_in_executor(self._executor, lambda: fn(*args, **kwargs))
            return await asyncio.wait_for(future, timeout=timeout)
        except FuturesTimeoutError as e:
            raise ReasonerError(f"Operation '{fn.__name__}' timed out", code=ReasonerErrorCode.TIMEOUT, original_exception=e) from e
        except Exception as e:
            raise ReasonerError(f"Operation '{fn.__name__}' failed: {e}", code=ReasonerErrorCode.MODEL_INFERENCE_FAILED, original_exception=e) from e

    async def _get_next_pipeline(self) -> Optional[Dict[str, Any]]:
        """
        Selects the next available model pipeline using round-robin and health checks.
        """
        async with self._pipeline_lock:
            if not self._model_pipelines:
                return None
            
            num_pipelines = len(self._model_pipelines)
            for _ in range(num_pipelines):
                pipeline_info = self._model_pipelines[self._next_pipeline_idx % num_pipelines]
                self._next_pipeline_idx = (self._next_pipeline_idx + 1) % num_pipelines
                
                model_key = f"{pipeline_info['model_name']}-{pipeline_info['device']}"
                last_failed = pipeline_info.get('last_failed_at')

                if last_failed and (datetime.now(timezone.utc) - last_failed).total_seconds() < self.config.model_cooldown_period:
                    if model_key not in self._model_reload_tasks:
                        self.logger.warning("model_in_cooldown_initiating_reload", model_key=model_key)
                        task = asyncio.create_task(self._attempt_reload_model(pipeline_info, initial_delay=self.config.model_cooldown_period))
                        self._model_reload_tasks[model_key] = task
                    continue

                get_or_create_metric(Counter, "reasoner_model_pipeline_usage", "Usage count per model pipeline", labelnames=("model_name", "device")).labels(model_name=pipeline_info['model_name'], device=str(pipeline_info['device'])).inc()
                return pipeline_info
            
            if self.config.cloud_fallback_model_enabled and not self.config.offline_only:
                fallback = next((p for p in self._model_pipelines if isinstance(p['pipeline'], LLMAdapter)), None)
                if fallback:
                    self.logger.warning("using_cloud_fallback_all_local_failed", model_name=fallback['model_name'])
                    get_or_create_metric(Counter, "reasoner_model_pipeline_usage", "Usage count per model pipeline", labelnames=("model_name", "device")).labels(model_name=fallback['model_name'], device=str(fallback['device'])).inc()
                    return fallback

            self.logger.error("no_healthy_models_available")
            return None

    async def _unload_model(self, model_key: str) -> None:
        """Unloads a model to free resources."""
        async with self._pipeline_lock:
            found_idx = next((i for i, p in enumerate(self._model_pipelines) if f"{p['model_name']}-{p['device']}" == model_key), -1)
            if found_idx != -1:
                p_info = self._model_pipelines.pop(found_idx)
                self.logger.info("model_unloaded", model_key=model_key)
                get_or_create_metric(Counter, "reasoner_model_unload_total", "Total model unloads", labelnames=("model_name", "device")).labels(model_name=p_info['model_name'], device=str(p_info['device'])).inc()
                del p_info['pipeline']
                if TRANSFORMERS_AVAILABLE and torch and torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    self.logger.info("gpu_cache_cleared", model_key=model_key)

    async def _attempt_reload_model(self, model_info: Dict[str, Any], initial_delay: int, new_config: Optional[Dict[str, Any]] = None) -> None:
        """Wrapper for reloading a model that uses a circuit breaker to prevent repeated failures."""
        key = f"{model_info['model_name']}-{model_info['device']}"
        self.logger.info("initiating_model_reload_attempt", key=key)
        get_or_create_metric(Counter, "reasoner_model_reload_attempts", "Model reload attempts", labelnames=("model_name", "device")).labels(model_name=model_info['model_name'], device=str(model_info['device'])).inc()
        
        if not self._reload_breaker:
            await self._reload_model_with_retries(model_info, initial_delay, new_config)
            return

        try:
            await self._reload_breaker.call_async(
                self._reload_model_with_retries, model_info, initial_delay, new_config
            )
        except pybreaker.CircuitBreakerError:
            self.logger.error("model_reload_circuit_open", model_key=key, message="Circuit breaker is open, skipping reload attempt.")
        except Exception as e:
            self.logger.error("model_reload_unhandled_exception", model_key=key, error=str(e), exc_info=True)
        finally:
            async with self._pipeline_lock:
                if key in self._model_reload_tasks:
                    del self._model_reload_tasks[key]

    async def _reload_model_with_retries(self, model_info: Dict[str, Any], initial_delay: int, new_config: Optional[Dict[str, Any]]) -> None:
        """Internal logic to reload a model with exponential backoff. Raises Exception on failure to trigger circuit breaker."""
        key = f"{model_info['model_name']}-{model_info['device']}"
        await asyncio.sleep(initial_delay)

        for attempt in range(self.config.model_reload_retries):
            try:
                config_to_use = new_config or next(
                    (cfg for cfg in self.config.model_configs if cfg.get("model_name") == model_info['model_name']),
                    {}
                )
                if not config_to_use:
                    raise ReasonerError(f"No configuration found for reloading model {key}", ReasonerErrorCode.CONFIGURATION_ERROR)

                new_pipeline_info = await asyncio.wait_for(
                    self._load_single_model(config_to_use, is_reload=True),
                    timeout=600
                )
                if new_pipeline_info:
                    async with self._pipeline_lock:
                        found_idx = next((i for i, p in enumerate(self._model_pipelines) if p['model_name'] == model_info['model_name']), -1)
                        if found_idx != -1:
                            self._model_pipelines[found_idx] = new_pipeline_info
                        else:
                            self._model_pipelines.append(new_pipeline_info)
                    self.logger.info("model_reload_success", model_key=key)
                    get_or_create_metric(Counter, "reasoner_model_reload_success", "Successful model reloads", labelnames=("model_name", "device")).labels(model_name=new_pipeline_info['model_name'], device=str(new_pipeline_info['device'])).inc()
                    return
            except Exception as e:
                self.logger.error("model_reload_attempt_failed", model_key=key, attempt=attempt+1, error=str(e), exc_info=True)
                if attempt < self.config.model_reload_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self.logger.critical("model_reload_failed_max_retries", model_key=key)
                    raise ReasonerError(f"Model reload failed for {key} after max retries", code=ReasonerErrorCode.MODEL_LOAD_FAILED, original_exception=e) from e

    async def _truncate_prompt_if_needed(self, prompt: str, tokenizer: Any, max_new_tokens: int) -> str:
        """Truncates a prompt if it exceeds the model's context window by keeping the head and tail."""
        try:
            if not TRANSFORMERS_AVAILABLE:
                max_len = 4096 - max_new_tokens - self.config.context_buffer_tokens
                if len(prompt) > max_len:
                    self.logger.info("prompt_truncated_simple", original_length=len(prompt), new_length=max_len)
                    return prompt[:max_len]
                return prompt

            model_max_length = getattr(tokenizer, 'model_max_length', 4096)
            input_ids = tokenizer.encode(prompt, return_tensors="pt")
            prompt_length_tokens = input_ids.shape[1]

            if prompt_length_tokens >= model_max_length - max_new_tokens:
                new_length = model_max_length - max_new_tokens - self.config.context_buffer_tokens
                self.logger.info("prompt_truncated", original_length=prompt_length_tokens, new_length=new_length)
                get_or_create_metric(Counter, "reasoner_prompt_truncations", "Total number of prompt truncations", labelnames=("model_name",)).labels(model_name=tokenizer.name_or_path).inc()
                
                if new_length <= 50:
                    raise ReasonerError(f"Prompt is too long for model's context ({model_max_length} tokens) to be truncated meaningfully.", code=ReasonerErrorCode.INVALID_INPUT)

                keep_start_len = new_length // 2
                keep_end_len = new_length - keep_start_len
                
                start_ids = input_ids[:, :keep_start_len]
                end_ids = input_ids[:, -keep_end_len:]
                
                truncated_ids = torch.cat((start_ids, end_ids), dim=1)
                
                return tokenizer.decode(truncated_ids[0], skip_special_tokens=True)
            return prompt
        except Exception as e:
            self.logger.error("prompt_truncation_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Prompt truncation failed: {e}", code=ReasonerErrorCode.INVALID_INPUT, original_exception=e) from e

    def _rate_key_extractor(self, *args, **kwargs):
        session_id = kwargs.get('session_id', 'global')
        client_ip = kwargs.get('client_ip', 'unknown')
        return f"{session_id}:{client_ip}"

    @rate_limited(calls_per_second=10.0, key_extractor=_rate_key_extractor)
    async def _async_generate_text(self, prompt: str, max_length: int, temperature: float, multi_modal_data: Optional[Dict[str, Any]] = None, client_ip: Optional[str] = 'unknown') -> str:
        if not self._inference_semaphore:
            raise ReasonerError("Inference semaphore not initialized", code=ReasonerErrorCode.CONFIGURATION_ERROR)
        async with self._inference_semaphore:
            start_time = time.monotonic()
            if multi_modal_data and not MULTI_MODAL_SCHEMAS_AVAILABLE:
                self.logger.warning("multimodal_data_ignored", reason="Schemas unavailable")
                multi_modal_data = None
            pipeline_info = await self._get_next_pipeline()
            if not pipeline_info:
                raise ReasonerError("No model available for inference", code=ReasonerErrorCode.MODEL_NOT_INITIALIZED)
            model_pipeline = pipeline_info['pipeline']
            model_name = pipeline_info['model_name']
            try:
                if isinstance(model_pipeline, LLMAdapter):
                    result = await model_pipeline.generate(
                        prompt=prompt, multi_modal_data=multi_modal_data,
                        max_tokens=max_length, temperature=temperature
                    )
                else:
                    if TRANSFORMERS_AVAILABLE:
                        prompt = await self._truncate_prompt_if_needed(prompt, model_pipeline.tokenizer, max_length)
                    result = await self._execute_in_thread(
                        self._generate_text_sync, pipeline_info, prompt, max_length, temperature
                    )
                get_or_create_metric(Histogram, "reasoner_inference_duration_seconds", "Duration of inference operations", ("type", "strategy")).labels(type="generate", strategy="default").observe(time.monotonic() - start_time)
                return result
            except (httpx.HTTPStatusError if HTTPX_AVAILABLE else Exception) as e:
                 raise ReasonerError(f"HTTP error in inference: {e}", code=ReasonerErrorCode.SERVICE_UNAVAILABLE, original_exception=e) from e
            except asyncio.TimeoutError as e:
                raise ReasonerError("Inference timed out", code=ReasonerErrorCode.TIMEOUT, original_exception=e) from e
            except Exception as e:
                self.logger.error("inference_failed", model_name=model_name, error=str(e), exc_info=True)
                pipeline_info['last_failed_at'] = datetime.now(timezone.utc)
                raise ReasonerError(f"Inference failed for {model_name}", code=ReasonerErrorCode.MODEL_INFERENCE_FAILED, original_exception=e) from e

    def _generate_text_sync(self, pipeline_info: Dict[str, Any], prompt: str, max_new_tokens: int, temperature: float) -> str:
        """Synchronous text generation for Hugging Face pipelines."""
        if max_new_tokens < 1:
            raise ReasonerError("max_new_tokens must be at least 1", code=ReasonerErrorCode.INVALID_INPUT)
        if not prompt:
            raise ReasonerError("Empty prompt", code=ReasonerErrorCode.INVALID_INPUT)
        model_pipeline = pipeline_info['pipeline']
        generation_kwargs = {
            "max_new_tokens": max_new_tokens, "num_return_sequences": 1, "do_sample": True,
            "temperature": temperature, "truncation": True, "pad_token_id": model_pipeline.tokenizer.pad_token_id,
            "top_p": 0.9, "top_k": 50
        }
        try:
            result_raw = model_pipeline(prompt, **generation_kwargs)
            if isinstance(result_raw, list) and result_raw and 'generated_text' in result_raw[0]:
                generated_text = result_raw[0]["generated_text"]
                # Ensure we have a string result
                if not isinstance(generated_text, str):
                    generated_text = str(generated_text)
                processed_text = generated_text[len(prompt):].strip() if generated_text.startswith(prompt) else generated_text.strip()
                processed_text = _simple_text_sanitize(processed_text) if processed_text else "Generated response"
                get_or_create_metric(Counter, "reasoner_model_generation_tokens_total", "Total tokens generated", labelnames=("model_name", "task_type")).labels(
                    model_name=pipeline_info['model_name'], task_type="text_generation"
                ).inc(len(processed_text.split()))
                return processed_text
            # If we don't get expected format, return a default response
            return "Generated response"
        except (torch.cuda.OutOfMemoryError if torch and hasattr(torch.cuda, 'OutOfMemoryError') else Exception) as e:
            if "OutOfMemoryError" in str(type(e).__name__):
                raise ReasonerError("GPU out of memory", code=ReasonerErrorCode.CUDA_OOM, original_exception=e) from e
            # For other exceptions, return a fallback
            self.logger.warning("generation_format_unexpected", error=str(e))
            return "Generated response"

    async def _prepare_prompt_with_history(self, prompt_type: str, context: Dict[str, Any], query: str, session_id: Optional[str]) -> str:
        """Builds the full prompt including context, query, and history."""
        recent_history = await self.history.get_entries(limit=self.config.max_history_size, session_id=session_id)
        history_str = self._build_history_string(recent_history, session_id)
        formatted_context = {
            k: _format_multimodal_for_prompt(v) if isinstance(v, (MultiModalData, MultiModalAnalysisResult)) else v
            for k, v in context.items()
        }
        if prompt_type == "explain":
            prompt = await self._prompt_strategy.create_explanation_prompt(formatted_context, query, history_str=history_str)
        else:
            prompt = await self._prompt_strategy.create_reasoning_prompt(formatted_context, query, history_str=history_str)
        if len(prompt) > self.config.max_query_length:
            raise ReasonerError("Generated prompt too long", code=ReasonerErrorCode.INVALID_INPUT)
        if self.config.log_prompts:
            self.logger.info("prompt_generated", type=prompt_type, length=len(prompt))
        get_or_create_metric(Histogram, "reasoner_prompt_size_bytes", "Size of generated prompts in bytes", labelnames=("type",)).labels(type=prompt_type).observe(len(prompt.encode('utf-8')))
        return prompt

    async def _validate_session(self, session_id: str) -> bool:
        """Validates a session ID using Redis if available."""
        if self._session_auth_enabled:
            if not self._redis_client:
                self.logger.warning("session_validation_skipped_no_redis")
                if self.config.strict_mode:
                    raise ReasonerError("Session validation is required, but backend (Redis) is not available.", code=ReasonerErrorCode.CONFIGURATION_ERROR)
                return True 

            try:
                exists = await self._redis_client.exists(f"session:{session_id}")
                if not exists:
                    get_or_create_metric(Counter, "reasoner_session_validation_errors", "Session validation errors", labelnames=("type",)).labels(type="invalid_session").inc()
                    return False
                return True
            except Exception as e:
                get_or_create_metric(Counter, "reasoner_session_validation_errors", "Session validation errors", labelnames=("type",)).labels(type="redis_error").inc()
                self.logger.warning("session_validation_failed", error=str(e), exc_info=True)
                if self.config.strict_mode:
                    raise ReasonerError("Session validation failed due to backend error", code=ReasonerErrorCode.CONFIGURATION_ERROR, original_exception=e) from e
                return False
        return True

    async def _validate_request_inputs(
        self, query: str, context: Optional[Dict[str, Any]], session_id: Optional[str]
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        """Validates and sanitizes all incoming request data."""
        if not query or not query.strip():
            raise ReasonerError("Query cannot be empty.", code=ReasonerErrorCode.INVALID_INPUT)
        if len(query) > self.config.max_query_length:
            raise ReasonerError(f"Query exceeds max length of {self.config.max_query_length}", code=ReasonerErrorCode.INVALID_INPUT)
        if context and not isinstance(context, dict):
            raise ReasonerError("Context must be a dictionary.", code=ReasonerErrorCode.INVALID_INPUT)

        if self._session_auth_enabled and session_id:
            if not await self._validate_session(session_id):
                raise ReasonerError("Invalid or expired session ID.", code=ReasonerErrorCode.PERMISSION_DENIED)

        sanitized_query = _simple_text_sanitize(query)
        if not sanitized_query:
            raise ReasonerError("Query was entirely sanitized, resulting in an empty string.", code=ReasonerErrorCode.INVALID_INPUT)

        sanitized_context = await _sanitize_context(context or {}, self.config)
        if sanitized_context != (context or {}):
            get_or_create_metric(Counter, "reasoner_sensitive_data_redaction_total", "Total redactions of sensitive data", labelnames=("redaction_type",)).labels(redaction_type="context").inc()
        
        try:
            context_bytes = json.dumps(sanitized_context, default=str).encode('utf-8')
            if len(context_bytes) > self.config.max_context_bytes:
                raise ReasonerError(f"Context size ({len(context_bytes)} bytes) exceeds limit of {self.config.max_context_bytes}", code=ReasonerErrorCode.CONTEXT_SIZE_EXCEEDED)
        except TypeError as e:
            raise ReasonerError("Context contains non-serializable objects.", code=ReasonerErrorCode.INVALID_INPUT, original_exception=e) from e

        multi_modal_data = {k: v for k, v in sanitized_context.items() if isinstance(v, (MultiModalData, MultiModalAnalysisResult))}
        
        if MULTI_MODAL_SCHEMAS_AVAILABLE:
            for key, data in multi_modal_data.items():
                if isinstance(data, MultiModalData) and data.data_type == "image":
                    if len(data.data) > 5*1024*1024:
                        raise ReasonerError("Image data exceeds 5MB limit.", code=ReasonerErrorCode.INVALID_INPUT)
                    try:
                        img = Image.open(io.BytesIO(data.data))
                        img.verify()
                    except Exception as e:
                        raise ReasonerError(f"Invalid image data for key '{key}': {e}", code=ReasonerErrorCode.INVALID_INPUT, original_exception=e) from e

        return sanitized_query, sanitized_context, multi_modal_data

    async def _perform_inference_with_fallback(
        self, task_type: str, prompt: str, sanitized_query: str, sanitized_context: Dict, multi_modal_data: Dict, client_ip: Optional[str]
    ) -> Tuple[str, str]:
        """Executes the core model inference and applies a fallback if it fails."""
        try:
            temperature = self.config.temperature_explain if task_type == 'explain' else self.config.temperature_reason
            response_text = await self._async_generate_text(prompt, self.config.max_generation_tokens, temperature, multi_modal_data, client_ip=client_ip)
            response_type = f"model_{task_type}"
            return response_text, response_type
        except ReasonerError as e:
            self.logger.error("inference_failed_in_handler", task_type=task_type, error=str(e.message), code=e.code)
            if self.config.strict_mode:
                raise
            
            response_text = _rule_based_fallback(sanitized_query, sanitized_context, task_type)
            response_type = f"fallback_{task_type}"
            return response_text, response_type

    async def _finalize_request(
        self, task_type: str, sanitized_query: str, sanitized_context: Dict, response_text: str, response_type: str,
        cache_key: str, start_time: float, session_id: Optional[str] = None, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handles post-inference tasks: history, auditing, caching, and response formatting."""
        response_id = str(uuid.uuid4())
        
        await self.history.add_entry({
            "id": response_id, "query": sanitized_query, "context": sanitized_context,
            "response": response_text, "response_type": response_type,
            "timestamp": datetime.now(timezone.utc).isoformat(), "session_id": session_id
        })

        if self._redis_client and "fallback" not in response_type:
            try:
                await self._redis_client.set(cache_key, response_text, ex=self.config.cache_ttl)
            except Exception as e:
                self.logger.warning("cache_set_failed", key=cache_key, error=str(e))
                get_or_create_metric(Counter, "reasoner_cache_errors", "Cache operation errors", labelnames=("type",)).labels(type=task_type).inc()
        
        if self.audit_ledger_client:
            await self.audit_ledger_client.log_event(
                f"{task_type}_request",
                details={"query": sanitized_query, "response_id": response_id, "session_id": session_id, "user_id": user_id or "anon"},
                operator="system"
            )

        latency = time.monotonic() - start_time
        self.logger.info("request_success", task_type=task_type, latency=latency, response_type=response_type)
        get_or_create_metric(Counter, "reasoner_inference_success", "Successful inference requests", labelnames=("type",)).labels(type=task_type).inc()

        return {
            "id": response_id, "query": sanitized_query, task_type: response_text,
            "context_used": sanitized_context, "generated_by": response_type,
            "timestamp": datetime.now(timezone.utc).isoformat(), "latency_seconds": round(latency, 4)
        }

    async def _handle_request(self, task_type: str, query: str, context: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None, user_id: Optional[str] = None, client_ip: Optional[str] = None) -> Dict[str, Any]:
        """
        Generic handler for 'explain' and 'reason' tasks, refactored into sequential steps.
        """
        with tracer.start_as_current_span(f"reasoner.{task_type}"):
            start_time = time.monotonic()
            get_or_create_metric(Counter, "reasoner_requests_total", "Total requests", ("user_id", "task_type")).labels(user_id=user_id or "anon", task_type=task_type).inc()

            sanitized_query, sanitized_context, multi_modal_data = await self._validate_request_inputs(query, context, session_id)

            cache_key = f"{task_type}:{hashlib.sha256(json.dumps({'q': sanitized_query, 'c': sanitized_context}, sort_keys=True, default=str).encode()).hexdigest()}"
            if self._redis_client:
                try:
                    cached_response = await self._redis_client.get(cache_key)
                    if cached_response:
                        get_or_create_metric(Counter, "reasoner_cache_hits", "Cache hits", labelnames=("type",)).labels(type=task_type).inc()
                        self.logger.info("cache_hit", key=cache_key)
                        return {"result": cached_response.decode('utf-8'), "source": "cache", "id": str(uuid.uuid4())}
                    get_or_create_metric(Counter, "reasoner_cache_misses", "Cache misses", labelnames=("type",)).labels(type=task_type).inc()
                except Exception as e:
                    self.logger.warning("cache_get_failed", key=cache_key, error=str(e))
                    get_or_create_metric(Counter, "reasoner_cache_errors", "Cache operation errors", labelnames=("type",)).labels(type=task_type).inc()
            
            prompt = await self._prepare_prompt_with_history(task_type, sanitized_context, sanitized_query, session_id)
            
            response_text, response_type = await self._perform_inference_with_fallback(
                task_type, prompt, sanitized_query, sanitized_context, multi_modal_data, client_ip
            )

            return await self._finalize_request(
                task_type, sanitized_query, sanitized_context, response_text, response_type,
                cache_key, start_time, session_id, user_id
            )

    def _build_history_string(self, history_entries: List[Dict[str, Any]], session_id: Optional[str]) -> str:
        """Formats history entries into a prompt string."""
        if session_id:
            history_entries = [e for e in history_entries if e.get('session_id') == session_id]
        
        get_or_create_metric(Histogram, "reasoner_history_entries_used", "Number of history entries used in a prompt").observe(len(history_entries))
        
        if not history_entries:
            return ""
        history_parts = []
        for entry in reversed(history_entries):
            context_str = "; ".join([f"{k}: {str(v)[:100]}" for k, v in entry.get('context', {}).items()])
            response_sanitized = _simple_text_sanitize(entry['response'])
            if entry['response'] != response_sanitized:
                get_or_create_metric(Counter, "reasoner_sensitive_data_redaction_total", "Total redactions of sensitive data", labelnames=("redaction_type",)).labels(redaction_type="history").inc()
            history_parts.append(f"User Query: {entry['query']}\nContext: {context_str}\nAI Response: {response_sanitized[:200]}...")
        full_history = "\n---\n".join(history_parts)
        if len(full_history) > 5000:
            full_history = full_history[-5000:]
        return "\n--- Previous Conversation ---\n" + full_history + "\n--- End of Conversation ---\n\n"

    async def explain(self, query: str, context: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        return await self._handle_request("explain", query, context, **kwargs)

    async def reason(self, query: str, context: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        return await self._handle_request("reason", query, context, **kwargs)

    async def batch_explain(
        self,
        queries: List[str],
        contexts: List[Optional[Dict[str, Any]]],
        **kwargs
    ) -> List[Union[Dict[str, Any], Dict[str, str]]]:
        if not (len(queries) == len(contexts)):
            raise ReasonerError("Length of 'queries' and 'contexts' lists must be equal.", code=ReasonerErrorCode.INVALID_INPUT)

        tasks = [self.explain(query=q, context=c, **kwargs) for q, c in zip(queries, contexts)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        for res in results:
            if isinstance(res, ReasonerError):
                processed_results.append(res.to_api_response())
            elif isinstance(res, Exception):
                processed_results.append(ReasonerError(f"An unexpected error occurred: {res}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=res).to_api_response())
            else:
                processed_results.append(res)

        return processed_results

    async def health_check(self) -> Dict[str, Any]:
        """
        Performs a comprehensive health check.
        """
        status = "healthy"
        messages = []
        try:
            queue_size = self._executor._work_queue.qsize() if self._executor and hasattr(self._executor, '_work_queue') else "unknown"
            messages.append(f"Executor queue size: {queue_size}")
        except AttributeError:
            status = "degraded"
            messages.append("WARNING: Executor queue size unavailable")
        if not self._is_ready:
            status = "unhealthy"
            messages.append("CRITICAL: Reasoner not initialized")
        elif not self._model_pipelines:
            status = "unhealthy"
            messages.append("CRITICAL: No models loaded")
        else:
            failed_models = sum(1 for p in self._model_pipelines if p.get('last_failed_at'))
            if failed_models == len(self._model_pipelines):
                status = "unhealthy"
                messages.append("CRITICAL: All models failed")
            elif failed_models > 0:
                status = "degraded"
                messages.append(f"WARNING: {failed_models}/{len(self._model_pipelines)} models failed")
            
            messages.append(f"Models loaded: {len(self._model_pipelines)}")

        try:
            await self.history.get_size()
            messages.append("History database connection OK")
        except Exception as e:
            status = "unhealthy"
            messages.append(f"CRITICAL: History database connection failed: {e}")
        if self._redis_client:
            try:
                await self._redis_client.ping()
                messages.append("Redis connection OK")
            except Exception as e:
                status = "degraded"
                messages.append(f"WARNING: Redis connection failed: {e}")
        if self.audit_ledger_client:
            try:
                if await self.audit_ledger_client.health_check():
                    messages.append("Audit ledger connection OK")
                else:
                    status = "degraded"
                    messages.append("WARNING: Audit ledger connection failed")
            except Exception as e:
                status = "degraded"
                messages.append(f"WARNING: Audit ledger connection failed: {e}")
        if self._last_health_check:
            messages.append(f"Last init health check: {self._last_health_check['status']}.")
        if self._model_reload_tasks:
            messages.append(f"INFO: {len(self._model_reload_tasks)} models being reloaded")
        
        health_check_metric = get_or_create_metric(Counter, "reasoner_health_check_success", "Successful health checks", labelnames=("type",))
        if status == "healthy":
            health_check_metric.labels(type="full_check").inc()
        else:
            get_or_create_metric(Counter, "reasoner_health_check_errors", "Failed health checks", labelnames=("type",)).labels(type="full_check").inc()
        
        return {"status": status, "messages": messages, "timestamp": datetime.now(timezone.utc).isoformat()}

    async def get_history(self, limit: int = 10, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            return await self.history.get_entries(limit, session_id)
        except Exception as e:
            self.logger.error("history_retrieval_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Failed to retrieve history: {e}", code=ReasonerErrorCode.HISTORY_READ_FAILED, original_exception=e) from e

    async def clear_history(self, session_id: Optional[str] = None) -> None:
        try:
            await self.history.clear(session_id=session_id)
            self.logger.info("history_cleared", session_id=session_id or "all")
        except Exception as e:
            self.logger.error("history_clear_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Failed to clear history: {e}", code=ReasonerErrorCode.HISTORY_WRITE_FAILED, original_exception=e) from e

    async def purge_history(self, operator_id: str = "system_api_request") -> None:
        self.logger.warning("history_purge_requested", operator_id=operator_id)
        try:
            await self.history.purge_all(operator_id=operator_id)
            self.logger.info("history_purge_completed")
        except Exception as e:
            self.logger.error("history_purge_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Failed to purge history: {e}", code=ReasonerErrorCode.HISTORY_WRITE_FAILED, original_exception=e) from e

    async def export_history(self, output_format: str = "json", operator_id: str = "system_api_request") -> AsyncGenerator[Union[str, bytes], None]:
        self.logger.info("history_export_requested", format=output_format, operator_id=operator_id)
        try:
            async for chunk in self.history.export_history(output_format, operator_id=operator_id):
                yield chunk
        except Exception as e:
            self.logger.error("history_export_failed", error=str(e), exc_info=True)
            raise ReasonerError(f"Failed to export history: {e}", code=ReasonerErrorCode.HISTORY_READ_FAILED, original_exception=e) from e

    async def shutdown(self) -> None:
        """
        Gracefully shuts down the Reasoner.
        """
        self.logger.info("initiating_shutdown")
        if tracemalloc.is_tracing():
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('lineno')
            self.logger.info("memory_snapshot_at_shutdown", top_10_leaks=[str(stat) for stat in top_stats[:10]])
            tracemalloc.stop()

        start_time = time.monotonic()
        if hasattr(self, '_pruning_task') and self._pruning_task and not self._pruning_task.done():
            self._pruning_task.cancel()
        
        close_tasks = [task for task in self._model_reload_tasks.values() if not task.done()]
        for task in close_tasks:
            task.cancel()
        await asyncio.gather(*close_tasks, return_exceptions=True)
        self._model_reload_tasks.clear()

        if self._executor:
            self.logger.info("shutting_down_executor")
            await asyncio.to_thread(self._executor.shutdown, wait=True)
            self._executor = None
        
        close_coroutines = []
        if self.history:
            close_coroutines.append(self.history.aclose())
        if self.audit_ledger_client:
            close_coroutines.append(self.audit_ledger_client.close())
        if self._redis_client:
            close_coroutines.append(self._redis_client.close())
        if self._redis_pool:
            close_coroutines.append(self._redis_pool.disconnect())
        
        # Fix: await the coroutines directly, not call them
        await asyncio.gather(*close_coroutines, return_exceptions=True)
        
        for p_info in self._model_pipelines:
            if isinstance(p_info['pipeline'], LLMAdapter):
                await p_info['pipeline'].aclose()
        self._model_pipelines = []
        get_or_create_metric(Gauge, "reasoner_instances", "Number of active reasoner instances", labelnames=("instance_id",)).labels(instance_id=self._instance_id).dec()
        self.logger.info("shutdown_completed")
        get_or_create_metric(Histogram, "reasoner_shutdown_duration_seconds", "Duration of reasoner shutdown").observe(time.monotonic() - start_time)

# --- Plugin Wrapper ---
def plugin(kind, name, description, version):
    def decorator(cls):
        cls._plugin_info = {"kind": kind, "name": name, "description": description, "version": version}
        return cls
    return decorator

class PlugInKind:
    AI_ASSISTANT = "AI_ASSISTANT"

class ExecuteInput(BaseModel):
    action: str = Field(min_length=1, max_length=50)
    query: Optional[str] = Field(None, max_length=1000)
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    auth_token: Optional[str] = None
    queries: Optional[List[str]] = None
    contexts: Optional[List[Optional[Dict[str, Any]]]] = None
    session_ids: Optional[List[Optional[str]]] = None
    user_ids: Optional[List[Optional[str]]] = None
    client_ip: Optional[str] = None
    
    @classmethod
    def validate_for_action(cls, data: Dict[str, Any]) -> Self:
        action = data.get("action")
        if action in ("explain", "reason"):
            if not data.get("query"):
                raise ValueError("Query is required for 'explain' and 'reason' actions.")
            if data.get("queries"):
                raise ValueError("'queries' field is not allowed for single-request actions.")
        elif action == "batch_explain":
            if not data.get("queries"):
                raise ValueError("'queries' field is required for 'batch_explain' action.")
            if not data.get("contexts"):
                raise ValueError("'contexts' field is required for 'batch_explain' action.")
            if len(data["queries"]) != len(data["contexts"]):
                 raise ValueError("Length of 'queries' and 'contexts' must be equal.")
            if data.get("query"):
                raise ValueError("'query' field is not allowed for batch actions.")
        return cls.model_validate(data)

@plugin(PlugInKind.AI_ASSISTANT, name="explainable_reasoner", description="Generates explanations and reasoning with transformer-based models", version="1.2.0")
class ExplainableReasonerPlugin(ExplainableReasoner):
    """
    Plugin wrapper for ExplainableReasoner, exposing functionalities as actions.
    """
    def __init__(self, settings: Any = None):
        config = ReasonerConfig.from_env()
        super().__init__(config=config, settings=settings)
        self.logger.info("plugin_initialized", config=self.config.get_public_config())

    async def initialize(self):
        """Initializes the plugin."""
        self.logger.info("plugin_initializing")
        await self.async_init()
        self.logger.info("plugin_initialized_successfully")

    async def execute(self, action: str, **kwargs: Any) -> Any:
        """
        Executes a specific action.
        """
        self.logger.info("plugin_action_received", action=action)
        action_map: Dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {
            "explain": self.explain,
            "reason": self.reason,
            "batch_explain": self.batch_explain,
            "get_history": self.get_history,
            "health_check": self.health_check,
            "clear_history": self.clear_history,
            "purge_history": self.purge_history,
            "export_history": self.export_history
        }

        try:
            kwargs['action'] = action
            input_data = ExecuteInput.validate_for_action(kwargs)
            kwargs = input_data.model_dump(exclude_none=True)
            action = kwargs.pop('action')
        except (ValidationError, ValueError) as e:
            error = ReasonerError(f"Invalid input data: {e}", code=ReasonerErrorCode.INVALID_INPUT, original_exception=e)
            self.logger.error("input_validation_failed", error=str(error.message))
            return error.to_api_response()
        
        sensitive_actions = {"purge_history": "admin", "clear_history": "admin"}
        required_role = sensitive_actions.get(action)

        if required_role:
            if not self.config.jwt_secret_key or self.config.jwt_secret_key.get_actual_value() == "default-secret-key-change-me":
                self.logger.critical("rbac_misconfigured", action=action, reason="JWT secret key is not set or is the default.")
                return ReasonerError("Security feature misconfigured.", code=ReasonerErrorCode.CONFIGURATION_ERROR).to_api_response()

            token = kwargs.pop('auth_token', None)
            if not token:
                self.logger.warning("rbac_token_missing", action=action, required_role=required_role)
                return ReasonerError(f"Authentication token required for action '{action}'.", code=ReasonerErrorCode.SECURITY_VIOLATION).to_api_response()
            
            try:
                if not JWT_AVAILABLE:
                    raise ReasonerError("JWT library not installed, cannot perform RBAC.", code=ReasonerErrorCode.CONFIGURATION_ERROR)
                
                # Fix: Properly call jwt.decode with correct parameters
                decoded_token = await asyncio.get_running_loop().run_in_executor(
                    None, 
                    lambda: jwt.decode(
                        token, 
                        self.config.jwt_secret_key.get_actual_value(),
                        algorithms=["HS256"], 
                        audience=os.getenv("JWT_AUD", "reasoner-app"), 
                        issuer=os.getenv("JWT_ISS", "reasoner-iss")
                    )
                )
                user_role = decoded_token.get('role')
                if user_role != required_role:
                    self.logger.warning("rbac_permission_denied", action=action, required_role=required_role, user_role=user_role)
                    return ReasonerError(f"Permission denied. Action '{action}' requires role '{required_role}'.", code=ReasonerErrorCode.PERMISSION_DENIED).to_api_response()
            except Exception as e:
                if JWT_AVAILABLE and isinstance(e, jwt.InvalidTokenError):
                    self.logger.error("rbac_token_invalid", action=action, error=str(e))
                    return ReasonerError("Invalid or expired authentication token.", code=ReasonerErrorCode.SECURITY_VIOLATION, original_exception=e).to_api_response()
                # Re-raise other exceptions to be caught by the broader handler below
                raise
        
        try:
            if action in action_map:
                return await action_map[action](**kwargs)
            elif action == "get_metrics":
                return get_metrics_content().decode('utf-8')
            elif action == "list_actions":
                return list(action_map.keys())
            else:
                raise ReasonerError(f"Invalid action: {action}", code=ReasonerErrorCode.INVALID_INPUT)
        except ReasonerError as e:
            self.logger.error("plugin_error", action=action, error_code=e.code, message=str(e.message))
            return e.to_api_response()
        except Exception as e:
            error = ReasonerError(f"Unexpected error in action '{action}': {type(e).__name__}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e)
            self.logger.error("plugin_unexpected_error", action=action, message=str(e), exc_info=True)
            return error.to_api_response()

if __name__ == '__main__':
    async def main():
        """
        Example usage of the ExplainableReasonerPlugin in standalone mode.
        """
        print("--- Starting Reasoner Plugin Demo ---")
        plugin_instance = ExplainableReasonerPlugin()
        try:
            await plugin_instance.initialize()
            print("\n--- Plugin Initialized ---")
            print(f"Config: {plugin_instance.config.get_public_config()}")

            health = await plugin_instance.health_check()
            print("\n--- Health Check ---")
            print(json.dumps(health, indent=2))

            print("\n--- Available Actions ---")
            print(await plugin_instance.execute(action="list_actions"))

            print("\n--- Testing Batch Explain ---")
            try:
                batch_results = await plugin_instance.execute(
                    action="batch_explain",
                    queries=["What is a transformer model?", "Explain black holes simply."],
                    contexts=[{"source": "AI research paper"}, {"audience": "a 5-year-old"}]
                )
                print(json.dumps(batch_results, indent=2))
            except Exception as e:
                print(f"Batch explain failed: {e}")

            print("\n--- Testing Sensitive Action (Purge History) ---")
            # NOTE: This will fail without a valid JWT token. This is expected.
            purge_result = await plugin_instance.execute(action="purge_history", auth_token="dummy-token-for-testing")
            print(json.dumps(purge_result, indent=2))

        except ReasonerError as e:
            print(f"\nFATAL ERROR during initialization: {e.to_json(indent=2)}")
        finally:
            if plugin_instance._is_ready:
                await plugin_instance.shutdown()
                print("\n--- Plugin Shutdown Complete ---")

    asyncio.run(main())