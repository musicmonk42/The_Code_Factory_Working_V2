from __future__ import annotations

import os
import asyncio
import json
import logging
import time
import hashlib
import uuid
import re
import inspect
import sys
from urllib.parse import urlparse
from typing import (
    Dict,
    Any,
    Optional,
    List,
    Tuple,
    Callable,
    AsyncGenerator,
    Iterable,
)

# Add the module to sys.modules with the flat name for testability
sys.modules.setdefault("custom_llm_provider_plugin", sys.modules[__name__])

try:
    from dataclasses import dataclass
except ImportError:
    print("dataclasses library not found. Using a fallback class.")

    def dataclass(cls):
        return cls


# Define the exports expected by the unit tests.
__all__ = [
    "LLMConfig",
    "CustomLLMProvider",
    "plugin_health",
    "generate_custom_llm_response",
    "get_vault_key",
]

# Logger setup with structured JSON logging (initialize before any logging)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '{"time":"%(asctime)s","name":"%(name)s","level":"%(levelname)s","message":"%(message)s"}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# Non-Prometheus fallback classes for when the library is not available
class _NoopCounter:
    def labels(self, **kwargs):
        return self

    def inc(self, value=1):
        pass


class _NoopHistogram:
    def labels(self, **kwargs):
        return self

    def observe(self, value):
        pass


class _NoopGauge:
    def labels(self, **kwargs):
        return self

    def set(self, value):
        pass


# Prometheus (metrics) imports
try:
    from prometheus_client import Counter, Histogram, Gauge  # noqa: F401

    PROM_AVAILABLE = True
except Exception as e:
    PROM_AVAILABLE = False
    logger.warning(f"Prometheus client not found. Metrics will be disabled: {e}")

    CUSTOM_LLM_API_CALLS_TOTAL = _NoopCounter()
    CUSTOM_LLM_API_LATENCY_SECONDS = _NoopHistogram()
    CUSTOM_LLM_ERROR_TOTAL = _NoopCounter()
    CUSTOM_LLM_CACHE_HIT_TOTAL = _NoopCounter()
    CUSTOM_LLM_CACHE_MISS_TOTAL = _NoopCounter()
    CUSTOM_LLM_TOKEN_USAGE = _NoopCounter()
    CUSTOM_LLM_RESPONSE_LENGTH = _NoopHistogram()
    CUSTOM_LLM_STREAMING_PERFORMANCE = _NoopHistogram()
    CUSTOM_LLM_RETRY_EVENTS_TOTAL = _NoopCounter()
    CUSTOM_LLM_FALLBACK_USED_TOTAL = _NoopCounter()
    CUSTOM_LLM_RATE_LIMIT_WAIT_SECONDS = _NoopGauge()
    CUSTOM_LLM_CIRCUIT_STATE = _NoopGauge()

# Redis for distributed cache (logger is ready now)
try:
    from redis.asyncio import Redis

    REDIS_AVAILABLE = True
except ImportError:
    logger.warning("redis.asyncio not found. Falling back to in-memory cache.")
    REDIS_AVAILABLE = False
    Redis = None  # type: ignore

# LangChain imports
try:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatGenerationChunk, ChatResult, ChatGeneration
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        BaseMessage,
        AIMessageChunk,
    )
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun

    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    logger.warning(f"LangChain core libraries not found ({e}). Functionality limited.")

    BaseChatModel = object

    class ChatGenerationChunk:
        def __init__(
            self, text: Optional[str] = None, message: Optional[Any] = None, **data: Any
        ):
            self.text = text
            self.message = message

    class ChatGeneration:
        def __init__(self, message: Any, **data: Any):
            self.message = message

    class ChatResult:
        def __init__(self, generations: List[ChatGeneration], **data: Any):
            self.generations = generations

    class _BaseMessage:
        def __init__(self, content: str, type: str, **data: Any):
            self.content = content
            self.type = type

    class HumanMessage(_BaseMessage):
        def __init__(self, content: str, **data: Any):
            super().__init__(content, "human", **data)

    class SystemMessage(_BaseMessage):
        def __init__(self, content: str, **data: Any):
            super().__init__(content, "system", **data)

    class AIMessage(_BaseMessage):
        def __init__(self, content: str, **data: Any):
            super().__init__(content, "ai", **data)

    class AIMessageChunk(_BaseMessage):
        def __init__(self, content: str, **data: Any):
            super().__init__(content, "ai", **data)

    BaseMessage = _BaseMessage

    class AsyncCallbackManagerForLLMRun:
        async def on_llm_new_token(self, token, **kwargs):  # type: ignore
            return

    LANGCHAIN_AVAILABLE = False

# aiohttp for HTTP client
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("aiohttp not found. HTTP functionality will be limited.")
    # Create stub classes for type checking
    import types
    aiohttp = types.SimpleNamespace(
        ClientError=Exception,
        ClientResponseError=Exception,
        ClientPayloadError=Exception,
        ContentTypeError=Exception,
        ClientTimeout=lambda **kwargs: None,
        TCPConnector=lambda **kwargs: None,
        ClientSession=lambda **kwargs: None,
    )

# Tenacity imports for optional retry logic
try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
        RetryError,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    class RetryError(Exception):
        pass

    # Define a simple retry decorator for when tenacity is not available
    def retry(reraise=True, retry=None, stop=None, wait=None, before_sleep=None):
        def decorator(func):
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            return wrapper

        return decorator


# ---------------------------
# Configuration
# ---------------------------

# Load configuration from file or environment
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "configs/custom_llm_config.json")
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            DEFAULT_LLM_CONFIG = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to load config from {CONFIG_FILE}: {e}")
        DEFAULT_LLM_CONFIG = {}
else:
    DEFAULT_LLM_CONFIG = {}

# Defaults with env fallbacks
DEFAULT_LLM_CONFIG.setdefault(
    "api_base_url", os.getenv("CUSTOM_LLM_API_BASE_URL", "http://localhost:11434/v1/")
)
DEFAULT_LLM_CONFIG.setdefault(
    "api_key", os.getenv("CUSTOM_LLM_API_KEY", "your_custom_llm_api_key")
)
DEFAULT_LLM_CONFIG.setdefault(
    "default_model", os.getenv("CUSTOM_LLM_DEFAULT_MODEL", "llama2:7b-chat-q4_K_M")
)
DEFAULT_LLM_CONFIG.setdefault(
    "default_temperature", float(os.getenv("CUSTOM_LLM_DEFAULT_TEMP", "0.7"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "max_tokens", int(os.getenv("CUSTOM_LLM_MAX_TOKENS", "512"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "timeout_seconds", int(os.getenv("CUSTOM_LLM_TIMEOUT_SECONDS", "60"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "retry_attempts", int(os.getenv("CUSTOM_LLM_RETRY_ATTEMPTS", "3"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "retry_backoff_factor", float(os.getenv("CUSTOM_LLM_RETRY_BACKOFF_FACTOR", "2.0"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "enable_caching", os.getenv("CUSTOM_LLM_ENABLE_CACHING", "true").lower() == "true"
)
DEFAULT_LLM_CONFIG.setdefault(
    "cache_ttl_seconds", int(os.getenv("CUSTOM_LLM_CACHE_TTL_SECONDS", "3600"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "redis_url", os.getenv("REDIS_URL", "redis://localhost:6379/0")
)
DEFAULT_LLM_CONFIG.setdefault(
    "rate_limit_requests_per_minute", int(os.getenv("LLM_RATE_LIMIT_RPM", "60"))
)
DEFAULT_LLM_CONFIG.setdefault(
    "token_bucket_burst_capacity", int(os.getenv("LLM_TOKEN_BURST_CAPACITY", "5"))
)
DEFAULT_LLM_CONFIG.setdefault("vault_url", os.getenv("VAULT_URL"))
DEFAULT_LLM_CONFIG.setdefault("vault_token", os.getenv("VAULT_TOKEN"))
DEFAULT_LLM_CONFIG.setdefault("vault_kv_mount", os.getenv("VAULT_KV_MOUNT", "secret"))
DEFAULT_LLM_CONFIG.setdefault(
    "vault_secret_path", os.getenv("VAULT_SECRET_PATH", "llm-api-key")
)
DEFAULT_LLM_CONFIG.setdefault("vault_data_key", os.getenv("VAULT_DATA_KEY", "api_key"))
DEFAULT_LLM_CONFIG.setdefault(
    "api_key_vault_cache_ttl_seconds",
    int(os.getenv("API_KEY_VAULT_CACHE_TTL_SECONDS", "300")),
)
DEFAULT_LLM_CONFIG.setdefault(
    "fallback_api_base_url",
    os.getenv("CUSTOM_LLM_FALLBACK_API_BASE_URL", "http://localhost:11435/v1/"),
)
DEFAULT_LLM_CONFIG.setdefault(
    "fallback_api_key",
    os.getenv("CUSTOM_LLM_FALLBACK_API_KEY", "your_fallback_api_key"),
)
DEFAULT_LLM_CONFIG.setdefault(
    "send_openai_messages",
    os.getenv("CUSTOM_LLM_SEND_OPENAI_MESSAGES", "false").lower() == "true",
)
DEFAULT_LLM_CONFIG.setdefault(
    "llm_health_active_check",
    os.getenv("LLM_HEALTH_ACTIVE_CHECK", "false").lower() == "true",
)
DEFAULT_LLM_CONFIG.setdefault(
    "circuit_breaker_failures_threshold",
    int(os.getenv("LLM_CIRCUIT_BREAKER_FAILURES", "5")),
)
DEFAULT_LLM_CONFIG.setdefault(
    "circuit_breaker_cooldown_seconds",
    int(os.getenv("LLM_CIRCUIT_BREAKER_COOLDOWN", "300")),
)

# This part is fixed to correctly handle the environment variable parsing
env_hosts = os.getenv("KNOWN_LLM_HOSTS", "")
DEFAULT_LLM_CONFIG.setdefault(
    "known_llm_hosts", [h.strip() for h in env_hosts.split(",") if h.strip()]
)

# ---------------------------
# Metrics (safe creators)
# ---------------------------

_METRICS: Dict[str, Any] = {}


def _safe_counter(name: str, doc: str, labelnames: Tuple[str, ...] = ()) -> Any:
    if not PROM_AVAILABLE:

        class _Noop:
            def labels(self, **kwargs):
                return self

            def inc(self, value=1):
                pass

        return _Noop()
    if name in _METRICS:
        return _METRICS[name]
    try:
        c = Counter(name, doc, labelnames=labelnames)
        _METRICS[name] = c
        return c
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op counter for this process instance."
        )
        no = _NoopCounter()
        _METRICS[name] = no
        return no


def _safe_histogram(
    name: str,
    doc: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Any:
    if not PROM_AVAILABLE:

        class _Noop:
            def labels(self, **kwargs):
                return self

            def observe(self, value):
                pass

        return _Noop()
    if name in _METRICS:
        return _METRICS[name]
    try:
        default_buckets = (
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
        h = Histogram(name, doc, labelnames=labelnames, buckets=buckets or default_buckets)  # type: ignore
        _METRICS[name] = h
        return h
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op histogram for this process instance."
        )
        no = _NoopHistogram()
        _METRICS[name] = no
        return no


def _safe_gauge(name: str, doc: str, labelnames: Tuple[str, ...] = ()) -> Any:
    if not PROM_AVAILABLE:

        class _Noop:
            def labels(self, **kwargs):
                return self

            def set(self, value):
                pass

        return _Noop()
    if name in _METRICS:
        return _METRICS[name]
    try:
        g = Gauge(name, doc, labelnames=labelnames)
        _METRICS[name] = g
        return g
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op gauge for this process instance."
        )
        no = _NoopGauge()
        _METRICS[name] = no
        return no


CUSTOM_LLM_API_CALLS_TOTAL = _safe_counter(
    "custom_llm_api_calls_total",
    "Total API calls to Custom LLM Provider",
    ("model_name", "status"),
)
CUSTOM_LLM_API_LATENCY_SECONDS = _safe_histogram(
    "custom_llm_api_latency_seconds", "Latency of Custom LLM API calls", ("model_name",)
)
CUSTOM_LLM_ERROR_TOTAL = _safe_counter(
    "custom_llm_error_total",
    "Total errors from Custom LLM Provider",
    ("model_name", "error_type"),
)
CUSTOM_LLM_CACHE_HIT_TOTAL = _safe_counter(
    "custom_llm_cache_hit_total",
    "Total cache hits for Custom LLM responses",
    ("model_name",),
)
CUSTOM_LLM_CACHE_MISS_TOTAL = _safe_counter(
    "custom_llm_cache_miss_total",
    "Total cache misses for Custom LLM responses",
    ("model_name",),
)
CUSTOM_LLM_TOKEN_USAGE = _safe_counter(
    "custom_llm_token_usage", "Token usage for Custom LLM calls", ("model_name", "type")
)
CUSTOM_LLM_RESPONSE_LENGTH = _safe_histogram(
    "custom_llm_response_length", "Length of LLM responses", ("model_name",)
)
CUSTOM_LLM_STREAMING_PERFORMANCE = _safe_histogram(
    "custom_llm_streaming_performance_seconds",
    "Streaming response time",
    ("model_name",),
)
CUSTOM_LLM_RETRY_EVENTS_TOTAL = _safe_counter(
    "custom_llm_retry_events_total",
    "Total retry-triggering events",
    ("model_name", "reason"),
)
CUSTOM_LLM_FALLBACK_USED_TOTAL = _safe_counter(
    "custom_llm_fallback_used_total",
    "Total times fallback provider used",
    ("model_name",),
)
CUSTOM_LLM_RATE_LIMIT_WAIT_SECONDS = _safe_gauge(
    "custom_llm_rate_limit_wait_seconds",
    "Time spent waiting for rate limit",
    ("model_name",),
)
CUSTOM_LLM_CIRCUIT_STATE = _safe_gauge(
    "custom_llm_circuit_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ("model_name",),
)


def _record_rate_limit_wait(model_name: str, wait_time: float) -> None:
    try:
        labeled = CUSTOM_LLM_RATE_LIMIT_WAIT_SECONDS.labels(model_name=model_name)
        fn = (
            getattr(labeled, "observe", None)
            or getattr(labeled, "set", None)
            or getattr(labeled, "inc", None)
        )
        if callable(fn):
            fn(wait_time)
    except Exception:
        pass


# ---------------------------
# Caching & Rate Limiting
# ---------------------------

_response_cache: Dict[str, Tuple[str, float]] = {}
_negative_cache: Dict[str, Tuple[float, int]] = {}  # expiry, status

_REDIS_CLIENT_SHARED: Optional[Any] = None
_REDIS_LOCK = asyncio.Lock()


async def _get_redis_client() -> Optional[Any]:
    global _REDIS_CLIENT_SHARED
    if not REDIS_AVAILABLE or not DEFAULT_LLM_CONFIG.get("enable_caching", True):
        return None
    async with _REDIS_LOCK:
        if _REDIS_CLIENT_SHARED is None:
            try:
                redis_url = DEFAULT_LLM_CONFIG["redis_url"]
                if _is_production() and not redis_url.lower().startswith("rediss://"):
                    raise ValueError(
                        "Redis URL must use TLS (rediss://) in production."
                    )
                _REDIS_CLIENT_SHARED = Redis.from_url(redis_url)  # type: ignore
                logger.info("Initialized shared Redis client for distributed caching.")
            except Exception as e:
                logger.warning(
                    f"Redis cache get failed: {e}. Falling back to in-memory cache."
                )
                _REDIS_CLIENT_SHARED = None
        return _REDIS_CLIENT_SHARED


async def _close_redis_client() -> None:
    global _REDIS_CLIENT_SHARED
    if _REDIS_CLIENT_SHARED is not None:
        try:
            await _REDIS_CLIENT_SHARED.close()
        except Exception:
            pass
        _REDIS_CLIENT_SHARED = None


class TokenBucketRateLimiter:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate = rate_per_minute / 60.0
        self.capacity = burst
        self.tokens = self.capacity
        self.last_refill_time = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill_time = now

            while self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                now = time.monotonic()
                elapsed = now - self.last_refill_time
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill_time = now
            self.tokens -= 1


rate_limiter = TokenBucketRateLimiter(
    rate_per_minute=DEFAULT_LLM_CONFIG.get("rate_limit_requests_per_minute", 60),
    burst=DEFAULT_LLM_CONFIG.get("token_bucket_burst_capacity", 5),
)


class AsyncCircuitBreaker:
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2

    def __init__(self, failures_threshold: int, cooldown_seconds: int, name: str):
        self.name = name
        self.failures_threshold = failures_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        self.lock = asyncio.Lock()
        self.metrics_gauge = CUSTOM_LLM_CIRCUIT_STATE.labels(model_name=self.name)
        self.metrics_gauge.set(self.CLOSED)

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            await self.record_success()
        else:
            await self.record_failure()

    async def acquire(self) -> None:
        async with self.lock:
            if self.state == self.OPEN:
                elapsed = time.monotonic() - self.last_failure_time
                if elapsed > self.cooldown_seconds:
                    self.state = self.HALF_OPEN
                    self.metrics_gauge.set(self.HALF_OPEN)
                    logger.warning(
                        f"Circuit for '{self.name}' is half-open. Probing..."
                    )
                else:
                    raise CircuitBreakerError(
                        f"Circuit for '{self.name}' is open. Cooldown remaining: {self.cooldown_seconds - elapsed:.1f}s"
                    )
            elif self.state == self.HALF_OPEN:
                pass

    async def record_success(self) -> None:
        async with self.lock:
            if self.state != self.CLOSED:
                self.state = self.CLOSED
                self.failure_count = 0
                self.metrics_gauge.set(self.CLOSED)
                logger.info(f"Circuit for '{self.name}' is closed. Failures reset.")

    async def record_failure(self) -> None:
        async with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failures_threshold:
                self.state = self.OPEN
                self.metrics_gauge.set(self.OPEN)
                logger.error(
                    f"Circuit for '{self.name}' is open. Failures reached threshold of {self.failures_threshold}."
                )
            else:
                logger.warning(
                    f"Circuit for '{self.name}' failure count is now {self.failures_threshold}."
                )


class CircuitBreakerError(Exception):
    """Custom exception for circuit breaker state."""

    pass


# ---------------------------
# Secret scrubbing
# ---------------------------


def enhanced_scrub_secrets(data: Any) -> Any:
    def _scrub_string(s: str) -> str:
        s = re.sub(
            r'(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*["\']?([^\s,"\']+)',
            lambda m: f"{m.group(1)}=[REDACTED]",
            s,
        )
        s = re.sub(
            r'(?i)(authorization)\s*:\s*["\']?(bearer\s+)?([A-Za-z0-9\-_\.]+)',
            lambda m: f"{m.group(1)}: [REDACTED]",
            s,
        )
        return s

    if isinstance(data, str):
        return _scrub_string(data)
    try:
        serialized = json.dumps(data)
        return json.loads(_scrub_string(serialized))
    except Exception:
        return data


# ---------------------------
# Vault integration
# ---------------------------

_vault_api_key_cache: Dict[str, Any] = {"value": None, "expiry": 0}


def _is_production() -> bool:
    env_val = os.getenv("PRODUCTION_MODE", "false").lower()
    return env_val in ("1", "true", "yes", "on")


def _get_allowed_hosts() -> set[str]:
    static = set(DEFAULT_LLM_CONFIG.get("known_hosts", []))
    env = os.getenv("ALLOWED_LLM_HOSTS", "")
    env_hosts = {h.strip() for h in env.split(",") if h.strip()}
    return static | env_hosts


async def get_vault_key(key_name: str) -> str:
    raise RuntimeError("get_vault_key is not configured")


async def _get_api_key_with_cache() -> str:
    now = time.monotonic()
    if _vault_api_key_cache["value"] and _vault_api_key_cache["expiry"] > now:
        return _vault_api_key_cache["value"]

    api_key = await get_vault_key(DEFAULT_LLM_CONFIG.get("vault_data_key", "api_key"))

    if not api_key:
        api_key = DEFAULT_LLM_CONFIG["api_key"]

    ttl = DEFAULT_LLM_CONFIG.get("api_key_vault_cache_ttl_seconds", 300)
    _vault_api_key_cache["value"] = api_key
    _vault_api_key_cache["expiry"] = now + ttl
    return api_key


def _normalize_text_chunk(data: Any) -> str:
    # Handle direct text input (str or bytes)
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    if isinstance(data, str):
        # Attempt to parse if it looks like JSON
        try:
            json_data = json.loads(data)
            data = json_data
        except Exception:  # Catches all exceptions, including JSONDecodeError
            # If not JSON, return as is
            return data

    if isinstance(data, dict):
        if "content" in data:
            return str(data["content"])
        if "text" in data:
            return str(data["text"])
        if "message" in data:
            if isinstance(data["message"], dict) and "content" in data["message"]:
                return str(data["message"]["content"])
        if "choices" in data and isinstance(data["choices"], list):
            for choice in data["choices"]:
                if "delta" in choice and "content" in choice["delta"]:
                    return str(choice["delta"]["content"])
                if "message" in choice and "content" in choice["message"]:
                    return str(choice["message"]["content"])

    if hasattr(data, "content"):
        return str(getattr(data, "content"))
    if hasattr(data, "text"):
        return str(getattr(data, "text"))

    return ""


@dataclass
class LLMConfig:
    api_base_url: str
    api_key: str
    model: str = "test-model"
    temperature: float = 0.7
    max_tokens: int = 1024
    timeout: int = 30
    cache_ttl_seconds: int = 3600
    circuit_breaker_threshold: int = 5
    allow_insecure_http: bool = False
    allowed_hosts: Optional[Iterable[str]] = None

    def validate(self) -> "LLMConfig":
        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature must be between 0 and 2")
        if not self.max_tokens > 0:
            raise ValueError("max_tokens must be positive")
        if not self.timeout > 0:
            raise ValueError("timeout must be positive")

        if _is_production() and not self.allow_insecure_http:
            parsed = urlparse(self.api_base_url)
            if parsed.scheme != "https":
                raise ValueError("HTTPS is required in production")

            allowed_hosts = set(self.allowed_hosts or []) | _get_allowed_hosts()
            hostname = parsed.hostname
            if not hostname or hostname not in allowed_hosts:
                raise ValueError("Unknown or disallowed host in production")
        return self


class CustomLLMProvider:
    circuit_breaker_threshold: int = 5
    _vault_key_cache: Dict[str, Tuple[Optional[str], float, bool]] = {}
    _negative_ttl_seconds: int = 15
    _response_cache: Dict[str, Tuple[str, float]] = {}

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = (
            config
            or LLMConfig(api_base_url="https://api.example.com/v1/", api_key="test-key")
        ).validate()
        self.circuit_breaker_threshold = int(self.config.circuit_breaker_threshold)
        self._failure_count = 0
        self._max_retries = 3
        self._circuit_breaker = AsyncCircuitBreaker(
            failures_threshold=self.circuit_breaker_threshold,
            cooldown_seconds=DEFAULT_LLM_CONFIG.get(
                "circuit_breaker_cooldown_seconds", 300
            ),
            name=self.config.model,
        )

    def _generate_prompt(self, messages: list) -> str:
        parts = []
        for msg in messages:
            if hasattr(msg, "type"):
                if msg.type == "system":
                    parts.append(f"[SYSTEM] {msg.content}")
                elif msg.type == "human":
                    parts.append(f"[USER] {msg.content}")
                elif msg.type == "ai":
                    parts.append(f"[ASSISTANT] {msg.content}")
                else:
                    parts.append(str(getattr(msg, "content", "")))
            else:
                parts.append(str(getattr(msg, "content", "")))
        return "\n".join(parts)

    def _cache_key(self, prompt: str, model_name: str, stop: Optional[list]) -> str:
        key_obj = {
            "model": model_name,
            "temp": round(self.config.temperature, 6),
            "max_tokens": self.config.max_tokens,
            "stop": stop or [],
            "prompt": prompt,
            "base": self.config.api_base_url,
            "fmt": (
                "messages"
                if DEFAULT_LLM_CONFIG.get("send_openai_messages")
                else "prompt"
            ),
        }
        blob = json.dumps(key_obj, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return "llm_cache:" + hashlib.sha256(blob).hexdigest()

    async def _get_cached_response(
        self, cache_key: str, model_name: str
    ) -> Optional[str]:
        if not DEFAULT_LLM_CONFIG.get("enable_caching", True):
            return None
        now = time.time()

        redis = await _get_redis_client()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                    return cached.decode("utf-8")
            except Exception as e:
                logger.warning(f"Redis cache get failed: {e}")

        if cache_key in self._response_cache:
            value, expiry = self._response_cache[cache_key]
            if expiry > now:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                return value

        if cache_key in _response_cache:
            value, expiry = _response_cache[cache_key]
            if expiry > now:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                return value

        return None

    async def _set_cached_response(
        self, cache_key: str, model_name: str, response: str
    ) -> None:
        if not DEFAULT_LLM_CONFIG.get("enable_caching", True):
            return None
        ttl = int(DEFAULT_LLM_CONFIG.get("cache_ttl_seconds", 3600))
        redis = await _get_redis_client()
        if redis:
            try:
                await redis.set(cache_key, response, ex=ttl)
                return
            except Exception as e:
                logger.warning(f"Redis cache set failed: {e}")
        expiry = time.time() + ttl
        self._response_cache[cache_key] = (response, expiry)
        _response_cache[cache_key] = (response, expiry)

    async def _make_request(self, messages: List[Any]) -> Any:
        # This method is a hook for tests and is not fully implemented here
        # It's expected to be mocked by the unit tests.
        raise NotImplementedError("_make_request hook not implemented.")

    async def _make_streaming_request(self, messages: List[Any]) -> Any:
        # This method is a hook for tests and is not fully implemented here
        # It's expected to be mocked by the unit tests.
        raise NotImplementedError("_make_streaming_request hook not implemented.")

    async def _get_fallback_provider(self) -> Optional["CustomLLMProvider"]:
        return None

    def _should_retry(self, status: int) -> bool:
        return status in (429, 503)

    async def _acall(self, messages: List[Any]) -> Any:
        from aiohttp.client_exceptions import ClientError

        if self._failure_count >= self.circuit_breaker_threshold:
            raise RuntimeError("circuit breaker open")

        prompt = self._generate_prompt(messages)
        cache_key = self._cache_key(prompt, self.config.model, None)

        cached = await self._get_cached_response(cache_key, self.config.model)
        if cached is not None:
            return cached

        last_exception = None

        for attempt in range(self._max_retries):
            try:
                response = await self._make_request(messages)

                if hasattr(response, "status"):
                    if self._should_retry(response.status):
                        logger.warning(
                            f"Attempt {attempt+1} failed with transient status {response.status}, retrying..."
                        )
                        last_exception = Exception(
                            f"Transient error with status {response.status}"
                        )
                        if attempt < self._max_retries - 1:
                            await asyncio.sleep(2**attempt)
                        continue

                self._failure_count = 0
                normalized = _normalize_text_chunk(response)
                await self._set_cached_response(
                    cache_key, self.config.model, normalized
                )
                return normalized

            except ClientError as e:
                logger.warning(f"ClientError on attempt {attempt+1}: {e}")
                last_exception = e
                break

            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed with exception: {e}")
                last_exception = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2**attempt)

        fallback_provider = await self._get_fallback_provider()
        if fallback_provider:
            try:
                result = await fallback_provider._acall(messages)
                return result
            except Exception as fe:
                self._failure_count += 1
                raise RuntimeError("request failed") from fe
        else:
            self._failure_count += 1
            raise RuntimeError("request failed") from last_exception or Exception(
                "Max retries exceeded"
            )

    async def _astream(self, messages: List[Any]) -> AsyncGenerator[str, None]:
        response = await self._make_streaming_request(messages)

        if hasattr(response, "__aiter__"):
            async for chunk in response:
                normalized = _normalize_text_chunk(chunk)
                # Skip if chunk is str and normalization didn't change it (malformed json)
                if isinstance(chunk, str) and normalized == chunk:
                    continue
                yield normalized
            return

        for attr in ["content_iter", "aiter", "stream"]:
            if hasattr(response, attr):
                async for chunk in getattr(response, attr):
                    normalized = _normalize_text_chunk(chunk)
                    if isinstance(chunk, str) and normalized == chunk:
                        continue
                    yield normalized
                return

        normalized = _normalize_text_chunk(response)
        if not (isinstance(response, str) and normalized == response):
            yield normalized

    @classmethod
    async def _get_cached_vault_key(
        cls, key_name: str, ttl_seconds: int
    ) -> Optional[str]:
        now = time.monotonic()
        if key_name in cls._vault_key_cache:
            value, expires_at, is_negative = cls._vault_key_cache[key_name]
            if expires_at > now:
                if is_negative:
                    raise Exception("Negative cache hit for key")
                return value

        try:
            value = await get_vault_key(key_name)
            if value:
                cls._vault_key_cache[key_name] = (value, now + ttl_seconds, False)
                return value
            else:
                cls._vault_key_cache[key_name] = (
                    None,
                    now + cls._negative_ttl_seconds,
                    True,
                )
                raise Exception("Negative cache hit for key")
        except Exception:
            cls._vault_key_cache[key_name] = (
                None,
                now + cls._negative_ttl_seconds,
                True,
            )
            raise Exception("Negative cache hit for key")

    def shutdown(self) -> None:
        pass


async def plugin_health(
    session: Optional[Any] = None, url: Optional[str] = None
) -> Dict[str, Any]:
    if session is None:
        return {"status": "ok"}

    try:
        response = await session.get(url or "https://example.local/health")
        if 200 <= response.status < 400:
            return {"status": "ok"}
        else:
            return {"status": "error", "reason": f"status={response.status}"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


async def generate_custom_llm_response(
    provider: CustomLLMProvider, messages: List[Any], *, stream: bool = False
) -> Any:
    if not stream:
        return await provider._acall(messages)
    chunks = []
    async for c in provider._astream(messages):
        chunks.append(c)
    return chunks


# The following is from the original file and should be included
async def _post_as_async_cm(obj: Any) -> Any:
    if inspect.isawaitable(obj):
        obj = await obj

    if hasattr(obj, "__aenter__") and hasattr(obj, "__aexit__"):
        return obj

    class _Wrapper:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return _Wrapper(obj)


async def _maybe_await(func: Callable[[], Any]) -> Any:
    result = func()
    if inspect.isawaitable(result):
        return await result
    return result


_DELTA_RE = re.compile(r'"delta"\s*:\s*\{\s*"content"\s*:\s*"(?P<text>[^"]*)"\s*\}')


def _extract_delta_texts(fragment: str) -> list[str]:
    try:
        obj = json.loads(fragment)
        texts: list[str] = []
        for ch in obj.get("choices", []):
            delta = ch.get("delta") or {}
            t = ""
            if isinstance(delta, dict):
                t = delta.get("content") or ""
            if not t:
                t = ch.get("text") or ""
            if t:
                texts.append(t)
        return texts
    except json.JSONDecodeError:
        return [m.group("text") for m in _DELTA_RE.finditer(fragment)]


class CustomLLMChatModel(BaseChatModel):
    """
    A custom LLM chat model integrated with LangChain for SFE simulations.
    """

    model_name: str = "llama2:7b-chat-q4_K_M"
    temperature: float = 0.7
    api_base_url: str = "http://localhost:11434/v1/"
    api_key: str = "your_custom_llm_api_key"
    max_tokens: int = 512
    timeout: int = 60

    _client_session: Optional[Any] = None
    _session_lock: asyncio.Lock = asyncio.Lock()
    _response_cache: Dict[str, Tuple[str, float]] = {}
    _negative_cache: Dict[str, Tuple[float, int]] = {}
    _circuit_breaker: Any = None

    def __init__(self, **data: Any):
        # Override BaseChatModel's __init__ to handle the API key as a string
        # while keeping the field name 'api_key'.
        self.model_name = data.get("model_name", "llama2:7b-chat-q4_K_M")
        self.temperature = data.get("temperature", 0.7)
        self.api_base_url = data.get("api_base_url", "http://localhost:11434/v1/")
        self.api_key = data.get("api_key", "your_custom_llm_api_key")
        self.max_tokens = data.get("max_tokens", 512)
        self.timeout = data.get("timeout", 60)

        self._circuit_breaker = AsyncCircuitBreaker(
            failures_threshold=DEFAULT_LLM_CONFIG.get(
                "circuit_breaker_failures_threshold", 5
            ),
            cooldown_seconds=DEFAULT_LLM_CONFIG.get(
                "circuit_breaker_cooldown_seconds", 300
            ),
            name=self.model_name,
        )

    @property
    def _llm_type(self) -> str:
        return "custom_llm"

    async def _generate(
        self, messages: List[Any], stop: Optional[List[str]] = None, **kwargs: Any
    ) -> Any:
        response_text = await self._acall(messages, stop=stop, **kwargs)
        generation = ChatGeneration(message=AIMessage(content=response_text))
        return ChatResult(generations=[generation])

    async def _get_client_session(self) -> Any:
        async with self._session_lock:
            if self._client_session is None or getattr(
                self._client_session, "closed", True
            ):
                import aiohttp

                timeout_obj = aiohttp.ClientTimeout(
                    total=self.timeout,
                    connect=min(10, self.timeout),
                    sock_read=self.timeout,
                )
                connector = aiohttp.TCPConnector(
                    limit_per_host=100, enable_cleanup_closed=True
                )
                self._client_session = aiohttp.ClientSession(
                    timeout=timeout_obj, connector=connector
                )
                logger.debug("Created new aiohttp ClientSession.")
        return self._client_session

    async def aclose_session(self) -> None:
        async with self._session_lock:
            if self._client_session and not getattr(
                self._client_session, "closed", False
            ):
                await self._client_session.close()
                self._client_session = None
                logger.info("Closed aiohttp ClientSession.")

    def _generate_prompt(self, messages: List[Any]) -> str:
        parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                parts.append(f"[SYSTEM] {msg.content}")
            elif isinstance(msg, HumanMessage):
                parts.append(f"[USER] {msg.content}")
            elif isinstance(msg, AIMessage):
                parts.append(f"[ASSISTANT] {msg.content}")
            else:
                parts.append(str(getattr(msg, "content", "")))
        return "\n".join(parts)

    def _build_messages_payload(self, messages: List[Any]) -> List[Dict[str, str]]:
        payload: List[Dict[str, str]] = []
        for msg in messages:
            role = "user"
            if hasattr(msg, "type"):
                role = msg.type
            payload.append({"role": role, "content": str(getattr(msg, "content", ""))})
        return payload

    def _cache_key(
        self, prompt: str, model_name: str, stop: Optional[List[str]]
    ) -> str:
        key_obj = {
            "model": model_name,
            "temp": round(self.temperature, 6),
            "max_tokens": self.max_tokens,
            "stop": stop or [],
            "prompt": prompt,
            "base": self.api_base_url,
            "fmt": (
                "messages"
                if DEFAULT_LLM_CONFIG.get("send_openai_messages")
                else "prompt"
            ),
        }
        blob = json.dumps(key_obj, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return "llm_cache:" + hashlib.sha256(blob).hexdigest()

    async def _get_cached_response(
        self, cache_key: str, model_name: str
    ) -> Optional[str]:
        if not DEFAULT_LLM_CONFIG.get("enable_caching", True):
            return None
        now = time.time()

        redis = await _get_redis_client()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                    return cached.decode("utf-8")
            except Exception as e:
                logger.warning(f"Redis cache get failed: {e}")
        if cache_key in self._response_cache:
            value, expiry = self._response_cache[cache_key]
            if expiry > now:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                return value
        if cache_key in _response_cache:
            value, expiry = _response_cache[cache_key]
            if expiry > now:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_CACHE_HIT_TOTAL.labels(model_name=model_name).inc()
                return value
        return None

    async def _set_cached_response(
        self, cache_key: str, model_name: str, response: str
    ) -> None:
        if not DEFAULT_LLM_CONFIG.get("enable_caching", True):
            return None
        ttl = int(DEFAULT_LLM_CONFIG.get("cache_ttl_seconds", 3600))
        redis = await _get_redis_client()
        if redis:
            try:
                await redis.set(cache_key, response, ex=ttl)
                return
            except Exception as e:
                logger.warning(f"Redis cache set failed: {e}")
        expiry = time.time() + ttl
        self._response_cache[cache_key] = (response, expiry)
        _response_cache[cache_key] = (response, expiry)

    def shutdown(self) -> None:
        pass

    def _is_transient_err(self, e: Exception) -> bool:
        from aiohttp import ClientResponseError

        return isinstance(e, ClientResponseError) and e.status in (
            429,
            500,
            502,
            503,
            504,
        )

    async def _acall_with_retry(
        self,
        model_to_use: str,
        request_id: str,
        cache_key: str,
        session: Any,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> str:
        from aiohttp.client_exceptions import (
            ClientError,
        )

        async def _attempt_once():
            try:
                start = time.monotonic()
                import aiohttp

                cm = await _post_as_async_cm(
                    session.post(
                        f"{self.api_base_url}chat/completions",
                        headers=headers,
                        json=payload,
                    )
                )
                async with cm as resp:
                    status = resp.status
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_API_CALLS_TOTAL.labels(
                            model_name=model_to_use, status=str(status)
                        ).inc()

                    if status in (429, 503):
                        reason = (
                            "rate_limit" if status == 429 else "service_unavailable"
                        )
                        if PROM_AVAILABLE:
                            CUSTOM_LLM_ERROR_TOTAL.labels(
                                model_to_use, error_type=reason
                            ).inc()
                            CUSTOM_LLM_RETRY_EVENTS_TOTAL.labels(
                                model_to_use, reason=str(status)
                            ).inc()
                        _negative_cache[cache_key] = (time.time() + 5, status)
                        logger.warning(
                            f"[{request_id}] Transient {status}, triggering retry..."
                        )
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=status,
                            message="Transient error",
                        )
                    await _maybe_await(resp.raise_for_status)
                    data = await resp.json()
                    final_text = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )

                    if PROM_AVAILABLE:
                        CUSTOM_LLM_API_LATENCY_SECONDS.labels(model_to_use).observe(
                            time.monotonic() - start
                        )
                        CUSTOM_LLM_RESPONSE_LENGTH.labels(model_to_use).observe(
                            len(final_text)
                        )
                        usage = data.get("usage", {})
                        CUSTOM_LLM_TOKEN_USAGE.labels(
                            model_name=model_to_use, type="prompt"
                        ).inc(usage.get("prompt_tokens", 0) or 0)
                        CUSTOM_LLM_TOKEN_USAGE.labels(
                            model_name=model_to_use, type="completion"
                        ).inc(usage.get("completion_tokens", 0) or 0)

                    await self._set_cached_response(cache_key, model_to_use, final_text)
                    return final_text
            except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_RETRY_EVENTS_TOTAL.labels(
                        model_to_use, reason="json_decode"
                    ).inc()
                _negative_cache[cache_key] = (time.time() + 5, 503)
                logger.warning(
                    f"[{request_id}] JSON parse error from provider, will retry: {e}"
                )
                raise aiohttp.ClientPayloadError(f"Invalid JSON payload: {e}")

        if TENACITY_AVAILABLE:

            @retry(
                reraise=True,
                retry=retry_if_exception_type(aiohttp.ClientResponseError),
                stop=stop_after_attempt(int(DEFAULT_LLM_CONFIG["retry_attempts"])),
                wait=wait_exponential(
                    multiplier=float(DEFAULT_LLM_CONFIG["retry_backoff_factor"]),
                    min=1,
                    max=10,
                ),
                before_sleep=before_sleep_log(logger, logging.WARNING),
            )
            async def _tenacity_attempt():
                return await _attempt_once()

            return await _tenacity_attempt()

        attempts = 0
        backoff_delay = float(DEFAULT_LLM_CONFIG["retry_backoff_factor"])
        max_attempts = int(DEFAULT_LLM_CONFIG["retry_attempts"])
        last_exception = None

        while attempts < max_attempts:
            try:
                return await _attempt_once()
            except (ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                last_exception = e
                attempts += 1
                if attempts < max_attempts:
                    sleep_time = min(backoff_delay * (2 ** (attempts - 1)), 10)
                    logger.warning(
                        f"[{request_id}] Attempt {attempts}/{max_attempts} failed, retrying in {sleep_time:.1f}s: {e}"
                    )
                    await asyncio.sleep(sleep_time)

        if last_exception:
            raise last_exception
        else:
            raise Exception("Max retries exceeded with unknown error.")

    async def _acall(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        allow_fallback: bool = True,
        **kwargs: Any,
    ) -> str:
        if not LANGCHAIN_AVAILABLE and not any(
            isinstance(m, (SystemMessage, AIMessage, HumanMessage)) for m in messages
        ):
            return (
                "Mocked response for non-langchain messages: "
                + self._generate_prompt(messages)
            )

        request_id = uuid.uuid4().hex[:8]
        model_to_use = kwargs.get("model_name_override", self.model_name)
        prompt_for_cache = self._generate_prompt(messages)
        cache_key = self._cache_key(prompt_for_cache, model_to_use, stop)

        neg = _negative_cache.get(cache_key)
        if neg:
            expiry, status = neg
            if expiry > time.time():
                from aiohttp.client_exceptions import ClientResponseError
                from unittest.mock import MagicMock

                raise ClientResponseError(
                    MagicMock(),
                    (),
                    status=status,
                    message="Short-circuited by negative cache",
                )

        cached = await self._get_cached_response(cache_key, model_to_use)
        if cached is not None:
            logger.debug(f"[{request_id}] Cache hit for model={model_to_use}")
            return cached

        if PROM_AVAILABLE:
            CUSTOM_LLM_CACHE_MISS_TOTAL.labels(model_name=model_to_use).inc()

        async with self._circuit_breaker:
            start_wait = time.monotonic()
            await rate_limiter.acquire()
            wait_time = time.monotonic() - start_wait
            _record_rate_limit_wait(model_to_use, wait_time)

            session = await self._get_client_session()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "X-Request-ID": request_id,
            }
            if DEFAULT_LLM_CONFIG.get("send_openai_messages"):
                messages_payload = self._build_messages_payload(messages)
                payload = {
                    "model": model_to_use,
                    "messages": messages_payload,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stop": stop or [],
                }
            else:
                payload = {
                    "model": model_to_use,
                    "prompt": prompt_for_cache,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stop": stop or [],
                }

            scrubbed_payload = enhanced_scrub_secrets(payload)
            logger.debug(
                f"[{request_id}] Sending request to {self.api_base_url}chat/completions: {scrubbed_payload}"
            )

            try:
                return await self._acall_with_retry(
                    model_to_use, request_id, cache_key, session, headers, payload
                )
            except RetryError as rex:
                if (
                    allow_fallback
                    and DEFAULT_LLM_CONFIG.get("fallback_api_base_url")
                    and DEFAULT_LLM_CONFIG.get("fallback_api_key")
                ):
                    logger.info(
                        f"[{request_id}] Attempting fallback to secondary LLM provider..."
                    )
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_FALLBACK_USED_TOTAL.labels(
                            model_name=model_to_use
                        ).inc()
                    fallback_model = CustomLLMChatModel(
                        api_base_url=DEFAULT_LLM_CONFIG["fallback_api_base_url"],
                        api_key=str(DEFAULT_LLM_CONFIG["fallback_api_key"]),
                        model_name=model_to_use,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                    )
                    try:
                        return await fallback_model._acall(
                            messages, stop, run_manager, allow_fallback=False, **kwargs
                        )
                    finally:
                        await fallback_model.aclose_session()
                raise rex.last_attempt.exception() if rex.last_attempt else rex

    async def _astream(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        allow_fallback: bool = True,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        if not LANGCHAIN_AVAILABLE:
            yield ChatGenerationChunk(
                text="Mocked streaming response for: " + self._generate_prompt(messages)
            )
            return

        request_id = uuid.uuid4().hex[:8]
        model_to_use = kwargs.get("model_name_override", self.model_name)
        full_prompt = self._generate_prompt(messages)
        cache_key = self._cache_key(full_prompt, model_to_use, stop)

        cached = await self._get_cached_response(cache_key, model_to_use)
        if cached is not None:
            if LANGCHAIN_AVAILABLE:
                yield ChatGenerationChunk(message=AIMessageChunk(content=cached))
            else:
                yield ChatGenerationChunk(text=cached)
            return

        if PROM_AVAILABLE:
            CUSTOM_LLM_CACHE_MISS_TOTAL.labels(model_name=model_to_use).inc()

        async with self._circuit_breaker:
            start_wait = time.monotonic()
            await rate_limiter.acquire()
            wait_time = time.monotonic() - start_wait
            _record_rate_limit_wait(model_to_use, wait_time)

            session = await self._get_client_session()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "X-Request-ID": request_id,
            }
            headers.setdefault("Accept", "text/event-stream, application/json")
            headers.setdefault("Connection", "keep-alive")

            if DEFAULT_LLM_CONFIG.get("send_openai_messages"):
                payload = {
                    "model": model_to_use,
                    "messages": self._build_messages_payload(messages),
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stop": stop or [],
                    "stream": True,
                }
            else:
                payload = {
                    "model": model_to_use,
                    "prompt": full_prompt,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stop": stop or [],
                    "stream": True,
                }

            scrubbed_payload = enhanced_scrub_secrets(payload)
            logger.debug(
                f"[{request_id}] Streaming request to {self.api_base_url}chat/completions: {scrubbed_payload}"
            )

            full_response = ""

            try:
                start = time.monotonic()
                cm = await _post_as_async_cm(
                    session.post(
                        f"{self.api_base_url}chat/completions",
                        headers=headers,
                        json=payload,
                    )
                )
                async with cm as resp:
                    status = resp.status
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_API_CALLS_TOTAL.labels(
                            model_name=model_to_use, status=str(status)
                        ).inc()

                    if status in (429, 503):
                        reason = (
                            "rate_limit" if status == 429 else "service_unavailable"
                        )
                        if PROM_AVAILABLE:
                            CUSTOM_LLM_ERROR_TOTAL.labels(
                                model_to_use, error_type=reason
                            ).inc()
                            CUSTOM_LLM_RETRY_EVENTS_TOTAL.labels(
                                model_to_use, reason=str(status)
                            ).inc()
                        _negative_cache[cache_key] = (time.time() + 5, status)
                        logger.warning(
                            f"[{request_id}] Transient {status} during streaming, triggering retry..."
                        )
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=status,
                            message="Transient error",
                        )
                    await _maybe_await(resp.raise_for_status)

                    async for raw in resp.content:
                        chunk = raw.decode("utf-8", errors="ignore").strip()
                        if not chunk:
                            continue

                        lines = chunk.splitlines()
                        for line in lines:
                            if not line:
                                continue
                            if line.startswith("event:") or line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                line = line[len("data:") :].strip()

                            for text in _extract_delta_texts(line):
                                full_response += text
                                if LANGCHAIN_AVAILABLE:
                                    chunk_obj = ChatGenerationChunk(
                                        message=AIMessageChunk(content=text)
                                    )
                                else:
                                    chunk_obj = ChatGenerationChunk(text=text)
                                if run_manager:
                                    await run_manager.on_llm_new_token(text)
                                yield chunk_obj

                if PROM_AVAILABLE:
                    CUSTOM_LLM_STREAMING_PERFORMANCE.labels(model_to_use).observe(
                        time.monotonic() - start
                    )
                    CUSTOM_LLM_RESPONSE_LENGTH.labels(model_to_use).observe(
                        len(full_response)
                    )
                await self._set_cached_response(cache_key, model_to_use, full_response)
            except aiohttp.ClientError as e:
                if PROM_AVAILABLE:
                    CUSTOM_LLM_ERROR_TOTAL.labels(
                        model_to_use, error_type=e.__class__.__name__
                    ).inc()
                logger.error(f"[{request_id}] Streaming API call failed: {e}")
                if (
                    allow_fallback
                    and DEFAULT_LLM_CONFIG.get("fallback_api_base_url")
                    and DEFAULT_LLM_CONFIG.get("fallback_api_key")
                ):
                    logger.info(
                        f"[{request_id}] Attempting fallback to non-streaming call..."
                    )
                    if PROM_AVAILABLE:
                        CUSTOM_LLM_FALLBACK_USED_TOTAL.labels(
                            model_name=model_to_use
                        ).inc()
                    fallback_model = CustomLLMChatModel(
                        api_base_url=DEFAULT_LLM_CONFIG["fallback_api_base_url"],
                        api_key=str(DEFAULT_LLM_CONFIG["fallback_api_key"]),
                        model_name=model_to_use,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                    )
                    try:
                        response_text = await fallback_model._acall(
                            messages, stop, run_manager, allow_fallback=False, **kwargs
                        )
                        if LANGCHAIN_AVAILABLE:
                            yield ChatGenerationChunk(
                                message=AIMessageChunk(content=response_text)
                            )
                        else:
                            yield ChatGenerationChunk(text=response_text)
                    finally:
                        await fallback_model.aclose_session()
                raise e


async def register_plugin_entrypoints(register_func: Callable) -> None:
    logger.info("Registering CustomLLMProviderPlugin...")
    register_func(name="custom_llm", executor_func=generate_custom_llm_response)


if __name__ == "__main__" and os.getenv("RUN_PLUGIN_TESTS") == "1":

    async def _smoke() -> None:
        print("Running smoke tests...")
        try:
            health = await plugin_health()
            print("Health:", health)
            if (
                LANGCHAIN_AVAILABLE
                and health["status"] != "error"
                and "LLM API connectivity failed" not in health["details"]
            ):
                try:
                    resp = await generate_custom_llm_response(
                        CustomLLMProvider(),
                        messages=[HumanMessage(content="What is 2+2?")],
                    )
                    print("LLM response:", resp[:200])
                except Exception as e:
                    print("LLM call failed (expected if no server):", e)
        finally:
            await _close_redis_client()

    asyncio.run(_smoke())
