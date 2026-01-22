# D:\SFE\self_fixing_engineer\arbiter\config.py

# File: arbiter/config.py


import json
import logging
import os
import threading
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple

# Optional imports - make aiofiles optional since it's only used in refresh()
try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False
    aiofiles = None

import pydantic
import yaml
from cryptography.fernet import Fernet, InvalidToken
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from pydantic import Field, HttpUrl, SecretStr, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential

# Add compatibility layer
if hasattr(pydantic, "VERSION"):
    PYDANTIC_V2 = int(pydantic.VERSION.split(".")[0]) >= 2
else:
    PYDANTIC_V2 = False

# Modify imports
if PYDANTIC_V2:
    from pydantic_settings import BaseSettings, SettingsConfigDict
else:
    from pydantic import BaseSettings

    class SettingsConfigDict(dict):
        pass  # Dummy for v1


# Lazy import to avoid heavy initialization at module import time
# We defer importing get_tracer until it's actually needed to prevent
# triggering OpenTelemetry initialization at module import time.

_tracer_cache = None  # Cache for the tracer instance


def _get_tracer():
    """
    Lazy loader for OpenTelemetry tracer to avoid import-time initialization.

    Returns a cached tracer instance to avoid repeated imports.
    Falls back to NoOpTracer if OpenTelemetry is not available.
    """
    global _tracer_cache

    if _tracer_cache is not None:
        return _tracer_cache

    try:
        from arbiter.otel_config import get_tracer

        _tracer_cache = get_tracer(__name__)
        return _tracer_cache
    except Exception:
        # Import NoOpTracer if available, otherwise create a minimal one
        try:
            from arbiter.otel_config import NoOpTracer

            _tracer_cache = NoOpTracer()
            return _tracer_cache
        except ImportError:
            # Minimal no-op tracer as last resort
            from contextlib import contextmanager

            @contextmanager
            def noop_span(name):
                yield type("NoOpSpan", (), {})()

            _tracer_cache = type(
                "NoOpTracer", (), {"start_as_current_span": noop_span}
            )()
            return _tracer_cache


# Mock/Plausholder imports for a self-contained fix
try:
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter_plugin_registry import PlugInKind, registry
except ImportError:

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True


# Configure logging for this specific config module
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ArbiterConfig")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)

# Lock for thread-safe metric registration
_metrics_lock = threading.Lock()


# --- Helper functions for idempotent and thread-safe metric creation ---
def get_or_create_counter(
    name: str, documentation: str, labelnames: Tuple[str, ...] = ()
):
    with _metrics_lock:
        try:
            return Counter(name, documentation, labelnames=labelnames)
        except ValueError:
            return REGISTRY._names_to_collectors[name]


def get_or_create_gauge(
    name: str, documentation: str, labelnames: Tuple[str, ...] = ()
):
    with _metrics_lock:
        try:
            return Gauge(name, documentation, labelnames=labelnames)
        except ValueError:
            return REGISTRY._names_to_collectors[name]


def get_or_create_histogram(
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Tuple[float, ...] = (0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10),
):
    with _metrics_lock:
        try:
            return Histogram(
                name, documentation, labelnames=labelnames, buckets=buckets
            )
        except ValueError:
            return REGISTRY._names_to_collectors[name]


# Prometheus metrics
CONFIG_ACCESS = get_or_create_counter(
    "config_access_total", "Total configuration accesses", ("setting",)
)
CONFIG_ERRORS = get_or_create_counter(
    "config_errors_total", "Total configuration errors", ("error_type",)
)
CONFIG_OPS_TOTAL = get_or_create_counter(
    "config_ops_total", "Total config operations", ["operation"]
)


class ConfigError(Exception):
    """Custom exception for configuration errors."""

    pass


# --- Nested Pydantic Model for LLM Settings ---
class LLMSettings(BaseSettings):
    default_provider: str = Field("openai", env="LLM_DEFAULT_PROVIDER")
    retry_providers: List[str] = Field(
        ["anthropic", "google"], env="LLM_RETRY_PROVIDERS"
    )
    timeout_seconds: float = Field(30.0, env="LLM_TIMEOUT_SECONDS")
    api_url: HttpUrl = Field(
        default="https://api.openai.com/v1/completions", env="LLM_API_URL"
    )
    api_key: Optional[SecretStr] = Field(
        default=SecretStr("sk-dummy-llm-key-for-tests"), env="OPENAI_API_KEY"
    )
    model_name: str = Field(default="gpt-4o-mini", env="LLM_MODEL_NAME")
    temperature: float = Field(default=0.7, env="LLM_TEMPERATURE")
    max_tokens: int = Field(default=500, env="LLM_MAX_TOKENS")
    top_p: float = Field(default=1.0, env="LLM_TOP_P")
    frequency_penalty: float = Field(default=0.0, env="LLM_FREQUENCY_PENALTY")
    presence_penalty: float = Field(default=0.0, env="LLM_PRESENCE_PENALTY")
    system_prompt: Optional[str] = Field(default="", env="LLM_SYSTEM_PROMPT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        env_prefix="LLM_",
    )


# --- Primary Configuration Class using Pydantic BaseSettings ---
class ArbiterConfig(BaseSettings):
    """
    Centralized configuration management for the Arbiter AI Assistant and OmniCore ecosystem.
    Leverages Pydantic BaseSettings for automatic loading from environment variables and .env files.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.production", ".env.development"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        env_nested_delimiter="__",
        validate_default=True,
    )

    # --- Core System Settings ---
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    REDIS_POOL_SIZE: int = Field(default=10, env="REDIS_POOL_SIZE")
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        default="localhost:9092", env="KAFKA_BOOTSTRAP_SERVERS"
    )

    DB_PATH: str = Field(default="sqlite:///./omnicore.db", env="DATABASE_URL")
    DB_POOL_SIZE: int = Field(default=50, env="DB_POOL_SIZE")
    DB_POOL_MAX_OVERFLOW: int = Field(default=20, env="DB_POOL_MAX_OVERFLOW")
    DB_RETRY_ATTEMPTS: int = Field(default=3, env="DB_RETRY_ATTEMPTS")
    DB_RETRY_DELAY: float = Field(default=1.0, env="DB_RETRY_DELAY")
    DB_CIRCUIT_THRESHOLD: int = Field(default=3, env="DB_CIRCUIT_THRESHOLD")
    DB_CIRCUIT_TIMEOUT: int = Field(default=60, env="DB_CIRCUIT_TIMEOUT")
    DB_BATCH_SIZE: int = Field(default=100, env="DB_BATCH_SIZE")

    NEO4J_URI: str = Field(default="neo4j://localhost:7687", env="NEO4J_URI")
    NEO4J_USER: str = Field(default="neo4j", env="NEO4J_USER")
    NEO4J_PASSWORD: SecretStr = Field(
        default=SecretStr("password"), env="NEO4J_PASSWORD"
    )

    REPORTS_DIRECTORY: str = Field(default="reports", env="REPORTS_DIRECTORY")
    CODEBASE_PATHS: List[str] = Field(
        default_factory=lambda: ["."], env="CODEBASE_PATHS"
    )

    TF_ENABLE_ONEDNN_OPTS: str = Field(default="1", env="TF_ENABLE_ONEDNN_OPTS")

    ENCRYPTION_PASSWORD: str = Field(
        default="darshan",
        env="ENCRYPTION_PASSWORD",
        description="Password for API encryption (default: 'darshan')",
    )
    # Default Fernet key for development/testing. MUST be overridden in production via ENCRYPTION_KEY env var.
    ENCRYPTION_KEY: Optional[SecretStr] = Field(
        default=SecretStr("0mRtqFHlMkj0xTZO14sBFr1H6jkmmI0LWyK97sGyGew="),
        env="ENCRYPTION_KEY",
    )
    ENCRYPTION_KEY_BYTES: bytes = b""

    MAX_LEARN_RETRIES: int = Field(default=3, env="MAX_LEARN_RETRIES")
    VALID_DOMAIN_PATTERN: str = Field(
        default=r"^[a-zA-Z0-9_.-]+$", env="VALID_DOMAIN_PATTERN"
    )
    ML_MODEL_PATH: str = Field(
        default="models/relevance_classifier.pth", env="ML_MODEL_PATH"
    )
    QUANTUM_ENABLED: bool = Field(default=False, env="ENABLE_QUANTUM")
    KNOWLEDGE_REFRESH_INTERVAL: int = Field(
        default=86400, env="KNOWLEDGE_REFRESH_INTERVAL"
    )
    LOW_CONFIDENCE_THRESHOLD: float = Field(default=0.2, env="LOW_CONFIDENCE_THRESHOLD")
    SIMILARITY_THRESHOLD: float = Field(default=0.8, env="SIMILARITY_THRESHOLD")
    POLICY_CONFIG_FILE: str = Field(default="./policies.json", env="POLICY_CONFIG_FILE")

    # --- Audit Settings ---
    AUDIT_BUFFER_SIZE: int = Field(default=100, env="AUDIT_BUFFER_SIZE")
    AUDIT_FLUSH_INTERVAL: float = Field(default=1.0, env="AUDIT_FLUSH_INTERVAL")
    AUDIT_BLOCKCHAIN_ENABLED: bool = Field(
        default=False, env="AUDIT_BLOCKCHAIN_ENABLED"
    )
    WEB3_PROVIDER_URL: Optional[HttpUrl] = Field(default=None, env="WEB3_PROVIDER_URL")

    # --- Agent State Settings ---
    AGENT_STATE_BATCH_SIZE: int = Field(default=100, env="AGENT_STATE_BATCH_SIZE")
    AGENT_STATE_QUERY_LIMIT: int = Field(default=1000, env="AGENT_STATE_QUERY_LIMIT")

    # --- Message Bus Settings ---
    ARBITER_SHARDS: int = Field(default=4, env="ARBITER_SHARDS")
    MESSAGE_BUS_SHARD_COUNT: int = Field(default=4, env="MESSAGE_BUS_SHARD_COUNT")
    MESSAGE_BUS_MAX_QUEUE_SIZE: int = Field(
        default=10000, env="MESSAGE_BUS_MAX_QUEUE_SIZE"
    )
    MESSAGE_BUS_WORKERS_PER_SHARD: int = Field(
        default=2, env="MESSAGE_BUS_WORKERS_PER_SHARD"
    )

    # --- LLM Integration Settings (now nested) ---
    llm: LLMSettings = Field(default_factory=LLMSettings)

    # --- External Integrations ---
    ZMQ_BUG_ADDRESS: str = Field(default="tcp://localhost:5555", env="ZMQ_BUG_ADDRESS")
    JIRA_ENABLED: bool = Field(default=False, env="JIRA_ENABLED")
    JIRA_API_URL: Optional[HttpUrl] = Field(default=None, env="JIRA_API_URL")
    JIRA_API_TOKEN: Optional[SecretStr] = Field(default=None, env="JIRA_API_TOKEN")
    JIRA_PROJECT_KEY: str = Field(default="", env="JIRA_PROJECT_KEY")
    JIRA_ISSUE_TYPE: str = Field(default="", env="JIRA_ISSUE_TYPE")
    SLACK_WEBHOOK_URL: Optional[HttpUrl] = Field(default=None, env="SLACK_WEBHOOK_URL")
    SLACK_CHANNEL: str = Field(default="", env="SLACK_CHANNEL")
    EMAIL_ENABLED: bool = Field(default=False, env="EMAIL_ENABLED")
    EMAIL_SENDER: str = Field(default="", env="EMAIL_SENDER")
    EMAIL_RECIPIENTS: str = Field(default="", env="EMAIL_RECIPIENTS")
    EMAIL_RECIPIENTS_LIST: List[str] = Field(default_factory=list)

    EMAIL_SMTP_SERVER: str = Field(default="", env="EMAIL_SMTP_SERVER")
    EMAIL_SMTP_PORT: int = Field(default=587, env="EMAIL_SMTP_PORT")
    EMAIL_SMTP_USERNAME: Optional[str] = Field(default=None, env="EMAIL_SMTP_USERNAME")
    EMAIL_SMTP_PASSWORD: Optional[SecretStr] = Field(
        default=None, env="EMAIL_SMTP_PASSWORD"
    )
    EMAIL_USE_TLS: bool = Field(default=True, env="EMAIL_USE_TLS")
    EMAIL_TIMEOUT_SECONDS: float = Field(default=10.0, env="EMAIL_TIMEOUT_SECONDS")
    PAGERDUTY_ENABLED: bool = Field(default=False, env="PAGERDUTY_ENABLED")
    PAGERDUTY_ROUTING_KEY: Optional[SecretStr] = Field(
        default=None, env="PAGERDUTY_ROUTING_KEY"
    )
    PAGERDUTY_API_TIMEOUT_SECONDS: float = Field(
        default=10.0, env="PAGERDUTY_API_TIMEOUT_SECONDS"
    )

    # --- API Keys and Secrets (Mapped from .env directly) ---
    ADMIN_API_KEY: Optional[SecretStr] = Field(
        default=SecretStr("dummy-admin-key-for-tests"), env="ADMIN_API_KEY"
    )
    ANTHROPIC_API_KEY: Optional[SecretStr] = Field(
        default=None, env="ANTHROPIC_API_KEY"
    )
    GOOGLE_API_KEY: Optional[SecretStr] = Field(default=None, env="GOOGLE_API_KEY")
    CDP_API_KEY: SecretStr = Field(default=SecretStr(""), env="CDP_API_KEY")
    GLASSDOOR_API_KEY: SecretStr = Field(default=SecretStr(""), env="GLASSDOOR_API_KEY")
    EPA_API_KEY: SecretStr = Field(default=SecretStr(""), env="EPA_API_KEY")
    OSHA_API_KEY: SecretStr = Field(default=SecretStr(""), env="OSHA_API_KEY")
    DOL_API_KEY: SecretStr = Field(default=SecretStr(""), env="DOL_API_KEY")
    FEC_API_KEY: SecretStr = Field(default=SecretStr(""), env="FEC_API_KEY")
    SEC_EDGAR_USER_AGENT: str = Field(default="", env="SEC_EDGAR_USER_AGENT")
    SEC_EDGAR_CIK: str = Field(default="", env="SEC_EDGAR_CIK")
    CENSUS_API_KEY: SecretStr = Field(default=SecretStr(""), env="CENSUS_API_KEY")
    BLS_API_KEY: SecretStr = Field(default=SecretStr(""), env="BLS_API_KEY")
    USDA_API_KEY: SecretStr = Field(default=SecretStr(""), env="USDA_API_KEY")
    ALPHAVANTAGE_API_KEY: SecretStr = Field(
        default=SecretStr(""), env="ALPHAVANTAGE_API_KEY"
    )
    BRANDFETCH_API_KEY: SecretStr = Field(
        default=SecretStr(""), env="BRANDFETCH_API_KEY"
    )
    FINNHUB_API_KEY: SecretStr = Field(default=SecretStr(""), env="FINNHUB_API_KEY")
    POLYGON_API_KEY: SecretStr = Field(default=SecretStr(""), env="POLYGON_API_KEY")
    NEWSAPI_KEY: SecretStr = Field(default=SecretStr(""), env="NEWSAPI_KEY")
    AWS_ACCESS_KEY_ID: SecretStr = Field(default=SecretStr(""), env="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: SecretStr = Field(
        default=SecretStr(""), env="AWS_SECRET_ACCESS_KEY"
    )
    AWS_REGION: str = Field(default="us-east-1", env="AWS_REGION")
    EXPLORER_MOCK_MODE: bool = Field(default=False, env="EXPLORER_MOCK_MODE")

    SECRET_KEY: SecretStr = Field(default=SecretStr(""), env="SECRET_KEY")
    JWT_SECRET_KEY: SecretStr = Field(default=SecretStr(""), env="JWT_SECRET_KEY")
    ARENA_JWT_SECRET: SecretStr = Field(
        default=SecretStr("default-arena-jwt-secret"), env="ARENA_JWT_SECRET"
    )

    STRIPE_SECRET_KEY: SecretStr = Field(default=SecretStr(""), env="STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY: str = Field(default="", env="STRIPE_PUBLISHABLE_KEY")
    STRIPE_WEBHOOK_SECRET: SecretStr = Field(
        default=SecretStr(""), env="STRIPE_WEBHOOK_SECRET"
    )
    CAPTCHA_API_KEY: SecretStr = Field(default=SecretStr(""), env="CAPTCHA_API_KEY")

    # --- System Operational Parameters ---
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    HEALTH_CHECK_ENDPOINT: str = Field(default="/health", env="HEALTH_CHECK_ENDPOINT")
    HEALTH_CHECK_PORT: int = Field(default=8080, env="HEALTH_CHECK_PORT")
    # Security: Default to localhost; use environment variable to bind to all interfaces if needed
    API_HOST: str = Field(default="127.0.0.1", env="API_HOST")
    API_PORT: int = Field(default=8000, env="API_PORT")
    RELOAD_VALIDATE_FILES: bool = Field(default=False, env="RELOAD_VALIDATE_FILES")

    # --- Advanced Features ---
    EXPERIMENTAL_FEATURES_ENABLED: bool = Field(
        default=False, env="EXPERIMENTAL_FEATURES_ENABLED"
    )
    PLUGIN_DIR: str = Field(default="./plugins", env="PLUGIN_DIR")
    DREAM_MODE_ENABLED: bool = Field(default=False, env="DREAM_MODE_ENABLED")
    PLUGIN_CONFIG: str = Field(default="{}", env="PLUGIN_CONFIG")

    # AI Model Specific Settings (from .env)
    DREAM_MODE_MODEL: str = Field(default="gpt2", env="DREAM_MODE_MODEL")
    REASONER_MODEL: str = Field(default="gpt2-large", env="REASONER_MODEL")
    DREAM_MODE_DEVICE: str = Field(default="-1", env="DREAM_MODE_DEVICE")
    REASONER_DEVICE: str = Field(default="-1", env="REASONER_DEVICE")
    TRANSFORMERS_OFFLINE: bool = Field(default=False, env="TRANSFORMERS_OFFLINE")

    # Merkle Tree settings (related to audit, but global for persistence)
    MERKLE_TREE_PRIVATE_KEY: Optional[SecretStr] = Field(
        default=None, env="MERKLE_TREE_PRIVATE_KEY"
    )
    MERKLE_TREE_BRANCHING_FACTOR: int = Field(
        default=2, env="MERKLE_TREE_BRANCHING_FACTOR"
    )

    # -------- Dream Mode --------
    DREAM_MODE_MAX_WORKERS: int = Field(default=2, env="DREAM_MODE_MAX_WORKERS")
    DREAM_MODE_TIMEOUT: int = Field(default=120, env="DREAM_MODE_TIMEOUT")
    DREAM_MODE_TEMP_POSITIVE: float = Field(default=0.8, env="DREAM_MODE_TEMP_POSITIVE")
    DREAM_MODE_TEMP_NEUTRAL: float = Field(default=0.5, env="DREAM_MODE_TEMP_NEUTRAL")
    DREAM_MODE_TEMP_NEGATIVE: float = Field(default=0.3, env="DREAM_MODE_TEMP_NEGATIVE")
    DREAM_MODE_HISTORY_DB: str = Field(
        default="sqlite:///./dream_history.db", env="DREAM_MODE_HISTORY_DB"
    )
    DREAM_MODE_MAX_HISTORY: int = Field(default=100, env="DREAM_MODE_MAX_HISTORY")
    DREAM_MODE_STRICT_MODE: bool = Field(default=False, env="DREAM_MODE_STRICT_MODE")
    DREAM_MODE_MOCK_MODE: bool = Field(default=False, env="DREAM_MODE_MOCK_MODE")

    # -------- Reasoner --------
    REASONER_MAX_WORKERS: int = Field(default=2, env="REASONER_MAX_WORKERS")
    REASONER_TIMEOUT: int = Field(default=60, env="REASONER_TIMEOUT")
    REASONER_MAX_TOKENS: int = Field(default=500, env="REASONER_MAX_TOKENS")
    REASONER_TEMP: float = Field(default=0.7, env="REASONER_TEMP")
    REASONER_TEMP_EXPLAIN: float = Field(default=0.5, env="REASONER_TEMP_EXPLAIN")
    REASONER_TEMP_REASON: float = Field(default=0.6, env="REASONER_TEMP_REASON")
    REASONER_TEMP_NEUTRAL: float = Field(default=0.5, env="REASONER_TEMP_NEUTRAL")
    REASONER_TEMP_POSITIVE: float = Field(default=0.8, env="REASONER_TEMP_POSITIVE")
    REASONER_TEMP_NEGATIVE: float = Field(default=0.3, env="REASONER_TEMP_NEGATIVE")
    REASONER_HISTORY_DB: str = Field(
        default="sqlite:///./reasoner_history.db", env="REASONER_HISTORY_DB"
    )
    REASONER_MAX_HISTORY: int = Field(default=100, env="REASONER_MAX_HISTORY")
    REASONER_STRICT_MODE: bool = Field(default=False, env="REASONER_STRICT_MODE")
    REASONER_MOCK_MODE: bool = Field(default=False, env="REASONER_MOCK_MODE")
    REASONER_LOG_PROMPTS: bool = Field(default=False, env="REASONER_LOG_PROMPTS")

    # --- Feature Toggles (from .env) ---
    ENABLE_LIVE_COMPANY_LOOKUP: bool = Field(
        default=True, env="ENABLE_LIVE_COMPANY_LOOKUP"
    )
    ENABLE_LIVE_TICKERS: bool = Field(default=True, env="ENABLE_LIVE_TICKERS")
    ENABLE_YAHOO_FINANCE: bool = Field(default=True, env="ENABLE_YAHOO_FINANCE")
    ENABLE_EPA: bool = Field(default=True, env="ENABLE_EPA")
    ENABLE_OSHA: bool = Field(default=True, env="ENABLE_OSHA")
    ENABLE_DOL: bool = Field(default=True, env="ENABLE_DOL")
    ENABLE_FEC: bool = Field(default=True, env="ENABLE_FEC")
    ENABLE_SEC_EDGAR: bool = Field(default=True, env="ENABLE_SEC_EDGAR")
    ENABLE_GD: bool = Field(default=True, env="ENABLE_GD")
    ENABLE_CDP: bool = Field(default=True, env="ENABLE_CDP")
    ENABLE_SUS: bool = Field(default=True, env="ENABLE_SUS")
    DEV_WEBHOOK_BYPASS: bool = Field(default=True, env="DEV_WEBHOOK_BYPASS")
    ENABLE_BINANCE: bool = Field(default=False, env="ENABLE_BINANCE")
    ENABLE_FINNHUB: bool = Field(default=True, env="ENABLE_FINNHUB")
    ENABLE_POLYGON: bool = Field(default=True, env="ENABLE_POLYGON")

    # --- Internal state (managed by class methods, not from env) ---
    _is_initialized: bool = False
    _loaded_at: Optional[str] = None

    DEFAULT_API_TIMEOUT_SECONDS: float = Field(
        default=30.0, env="DEFAULT_API_TIMEOUT_SECONDS"
    )
    FRONTEND_URL: HttpUrl = Field(default="http://localhost:8000", env="FRONTEND_URL")
    ARENA_PORT: int = Field(default=9001, env="ARENA_PORT")

    # --- Missing fields to complete the class ---
    REDIS_MAX_CONNECTIONS: int = Field(10, description="Maximum Redis connections")
    CONFIG_REFRESH_INTERVAL_SECONDS: int = Field(
        300, description="Interval for config refresh"
    )
    ZOOKEEPER_URL: Optional[str] = Field(default=None, env="ZOOKEEPER_URL")
    KAFKA_SCHEMA_REGISTRY_URL: Optional[HttpUrl] = Field(
        default=None, env="KAFKA_SCHEMA_REGISTRY_URL"
    )
    GROWTH_MAX_OPERATIONS: int = Field(
        1000, description="Max pending operations for growth manager"
    )
    ARRAY_STORAGE_TYPE: str = Field(
        "json", description="Array storage type: json, sqlite, redis, postgres"
    )
    ARRAY_STORAGE_PATH: str = Field(
        "./arrays.json", description="Path for array storage"
    )
    ARRAY_MAX_SIZE: int = Field(100000, description="Max size for array backend")
    ARRAY_ENCRYPTION_ENABLED: bool = Field(
        False, description="Enable encryption for array backend"
    )
    ARRAY_PAGE_SIZE: int = Field(1000, description="Page size for array backend")
    ANALYZER_MAX_WORKERS: int = Field(
        4, description="Max workers for codebase analyzer"
    )
    ENABLE_CRITICAL_FAILURES: bool = Field(
        default=False, env="ENABLE_CRITICAL_FAILURES"
    )
    AI_API_TIMEOUT: int = Field(30, description="Default timeout for AI API calls")
    MEMORY_LIMIT: int = Field(40, description="Memory limit in GB")
    OMNICORE_URL: HttpUrl = Field(
        default="https://api.example.com", description="OmniCore API endpoint"
    )
    ROLE_MAP: Dict[str, int] = Field(
        default_factory=lambda: {"guest": 0, "user": 1, "explorer_user": 2, "admin": 3}
    )
    ALERT_WEBHOOK_URL: Optional[HttpUrl] = Field(default=None, env="ALERT_WEBHOOK_URL")
    SENTRY_DSN: Optional[str] = Field(default=None, env="SENTRY_DSN")
    PROMETHEUS_GATEWAY: Optional[HttpUrl] = Field(
        default=None, env="PROMETHEUS_GATEWAY"
    )
    RL_MODEL_PATH: str = Field(
        default="./models/ppo_model.zip", description="Path to save/load RL model"
    )
    SLACK_AUTH_TOKEN: Optional[SecretStr] = Field(default=None, env="SLACK_AUTH_TOKEN")

    # --- MetaSupervisor Threshold Settings ---
    PLUGIN_ERROR_THRESHOLD: float = Field(
        default=0.1, description="Threshold for plugin error rate (0-1)"
    )
    TEST_FAILURE_THRESHOLD: float = Field(
        default=0.1, description="Threshold for test failure rate (0-1)"
    )
    ETHICS_DRIFT_THRESHOLD: float = Field(
        default=0.1, description="Threshold for ethics drift detection (0-1)"
    )
    MODEL_RETRAIN_EPOCHS: int = Field(
        default=10, description="Number of epochs for model retraining"
    )
    SUPERVISOR_RATE_LIMIT_OPS: int = Field(
        default=10, description="Rate limit for supervisor operations per time period"
    )
    SUPERVISOR_RATE_LIMIT_PERIOD: float = Field(
        default=1.0, description="Time period in seconds for rate limiting"
    )
    PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD: float = Field(
        default=0.8, description="Threshold for proactive hot-swap predictions"
    )
    SUPERVISOR_PERFORMANCE_THRESHOLD: float = Field(
        default=0.5, description="Threshold for supervisor self-performance"
    )
    AUDIT_LOG_RETENTION_DAYS: int = Field(
        default=30, description="Number of days to retain audit logs"
    )

    _singleton_lock: ClassVar[threading.Lock] = threading.Lock()
    _instance: ClassVar[Optional["ArbiterConfig"]] = None

    def __init__(self, **data):
        """Initialize config with cipher attribute."""
        super().__init__(**data)
        self._cipher = None
        self._sensitive_fields = {}

    @field_validator(
        "OMNICORE_URL",
        "SLACK_WEBHOOK_URL",
        "ALERT_WEBHOOK_URL",
        "PROMETHEUS_GATEWAY",
        "KAFKA_SCHEMA_REGISTRY_URL",
        mode="before",
    )
    def ensure_https_in_prod(cls, v):
        if (
            v
            and "://localhost" not in v
            and os.getenv("ENV") == "production"
            and not v.startswith("https://")
        ):
            raise ValueError(f"URL '{v}' must use HTTPS in production")
        return v

    @field_validator("ALPHAVANTAGE_API_KEY", mode="before")
    def validate_api_key(cls, v):
        if v and len(v) < 10:
            raise ValueError("API key must be at least 10 characters long.")
        return v

    @field_validator("EMAIL_RECIPIENTS", mode="before")
    def validate_email_recipients(cls, v):
        """Validate EMAIL_RECIPIENTS is a string."""
        if v and not isinstance(v, str):
            raise ValueError("EMAIL_RECIPIENTS must be a comma-separated string")
        return v

    @classmethod
    def initialize(cls) -> "ArbiterConfig":
        """
        Thread-safe singleton initialization.
        """
        CONFIG_ACCESS.labels(setting="initialize").inc()

        # Fast path - if already initialized
        if cls._instance is not None:
            logger.info(
                "ArbiterConfig already initialized. Returning existing instance."
            )
            return cls._instance

        # Thread-safe initialization
        with cls._singleton_lock:
            # Double-check pattern
            if cls._instance is not None:
                return cls._instance

            logger.info("Initializing ArbiterConfig (Pydantic mode)...")
            try:
                # Create the instance
                instance = cls()

                # Process encryption key
                encryption_key_val = (
                    instance.ENCRYPTION_KEY.get_secret_value()
                    if instance.ENCRYPTION_KEY
                    else ""
                )
                if (
                    encryption_key_val
                    and encryption_key_val
                    != "default-encryption-key-for-tests-only-must-be-32-bytes"
                ):
                    try:
                        instance.ENCRYPTION_KEY_BYTES = encryption_key_val.encode(
                            "utf-8"
                        )
                        instance._cipher = Fernet(instance.ENCRYPTION_KEY_BYTES)
                        logger.info("Encryption key loaded and validated.")
                    except Exception as e:
                        logger.critical(f"Invalid ENCRYPTION_KEY: {e}")
                        raise ConfigError(f"Invalid ENCRYPTION_KEY: {e}")
                else:
                    instance.ENCRYPTION_KEY_BYTES = Fernet.generate_key()
                    instance._cipher = Fernet(instance.ENCRYPTION_KEY_BYTES)
                    logger.warning(
                        "Generated new encryption key for development/testing."
                    )
                    instance.ENCRYPTION_KEY = SecretStr(
                        instance.ENCRYPTION_KEY_BYTES.decode("utf-8")
                    )

                instance._validate_custom_settings()

                # Create directories with path validation
                if instance.DB_PATH.startswith("sqlite:///"):
                    db_file_path = instance.DB_PATH.replace("sqlite:///", "")
                    db_dir = os.path.dirname(db_file_path)
                    if db_dir:
                        os.makedirs(db_dir, exist_ok=True)

                # Import safe_makedirs from utils to handle malformed paths
                from arbiter.utils import safe_makedirs

                instance.PLUGIN_DIR, _ = safe_makedirs(instance.PLUGIN_DIR, "./plugins")
                instance.REPORTS_DIRECTORY, _ = safe_makedirs(
                    instance.REPORTS_DIRECTORY, "./reports"
                )

                instance._is_initialized = True
                instance._loaded_at = datetime.now().isoformat()

                # Set singleton instance
                cls._instance = instance

                logger.info(f"ArbiterConfig initialized at {instance._loaded_at}")
                return instance

            except Exception as e:
                CONFIG_ERRORS.labels(error_type="initialization_fail").inc()
                logger.critical(
                    f"Failed to initialize ArbiterConfig: {e}", exc_info=True
                )
                raise ConfigError(f"Configuration initialization failed: {e}")

    @classmethod
    def load_from_file(cls, file_path: str) -> "ArbiterConfig":
        """
        Load configuration from a JSON or YAML file.

        Args:
            file_path: Path to configuration file

        Returns:
            ArbiterConfig instance with loaded values

        Raises:
            IOError: If file cannot be read
            ValueError: If file format is invalid
        """
        if not file_path.endswith((".json", ".yaml", ".yml")):
            raise ValueError(f"Unsupported file format: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                if file_path.endswith(".json"):
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError as e:
                        logger.error(
                            f"Invalid JSON in config file {file_path}: {e}",
                            exc_info=True,
                        )
                        raise ValueError(f"Invalid JSON in config file: {e}")
                else:
                    try:
                        data = yaml.safe_load(f)
                    except yaml.YAMLError as e:
                        logger.error(
                            f"Invalid YAML in config file {file_path}: {e}",
                            exc_info=True,
                        )
                        raise ValueError(f"Invalid YAML in config file: {e}")

            # Create instance with loaded data
            return cls(**data)
        except IOError as e:
            logger.error(f"Failed to load config file {file_path}: {e}", exc_info=True)
            raise IOError(f"Failed to load config file: {e}")

    @classmethod
    def load_from_env(cls) -> "ArbiterConfig":
        """
        Load configuration from environment variables.
        This is what Pydantic does by default, but provided for compatibility.
        """
        return cls()

    def decrypt_sensitive_fields(self) -> None:
        """Decrypt sensitive fields if they were encrypted."""
        if not hasattr(self, "_cipher") or self._cipher is None:
            if self.ENCRYPTION_KEY:
                try:
                    key = self.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
                    self._cipher = Fernet(key)
                except Exception:
                    logger.warning(
                        "Encryption key not set. Skipping decryption of sensitive fields."
                    )
                    return
            else:
                logger.warning(
                    "Encryption key not set. Skipping decryption of sensitive fields."
                )
                return

        if not hasattr(self, "_sensitive_fields"):
            self._sensitive_fields = {}

        # Decrypt fields that were encrypted
        for field_name, encrypted_value in self._sensitive_fields.items():
            if encrypted_value:
                try:
                    decrypted = self._cipher.decrypt(encrypted_value.encode()).decode()
                    setattr(self, field_name, SecretStr(decrypted))
                except InvalidToken:
                    logger.error(f"Failed to decrypt {field_name}: ", exc_info=True)
                    setattr(self, field_name, None)

    def encrypt_sensitive_fields(self) -> None:
        """Encrypt sensitive fields for storage."""
        if not hasattr(self, "_cipher") or self._cipher is None:
            if self.ENCRYPTION_KEY:
                try:
                    key = self.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
                    self._cipher = Fernet(key)
                except Exception:
                    logger.warning("Cannot encrypt without valid encryption key")
                    return
            else:
                return

        if not hasattr(self, "_sensitive_fields"):
            self._sensitive_fields = {}

        # List of fields to encrypt
        sensitive_field_names = [
            "EMAIL_SMTP_PASSWORD",
            "ADMIN_API_KEY",
            "JWT_SECRET_KEY",
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
        ]

        for field_name in sensitive_field_names:
            value = getattr(self, field_name, None)
            if value and isinstance(value, SecretStr):
                raw_value = value.get_secret_value()
                if raw_value:
                    encrypted = self._cipher.encrypt(raw_value.encode()).decode()
                    self._sensitive_fields[field_name] = encrypted

    def _validate_custom_settings(self):
        if self.MAX_LEARN_RETRIES < 0:
            raise ConfigError("MAX_LEARN_RETRIES must be non-negative.")
        if self.KNOWLEDGE_REFRESH_INTERVAL <= 0:
            raise ConfigError("KNOWLEDGE_REFRESH_INTERVAL must be positive.")
        if not (0.0 <= self.LOW_CONFIDENCE_THRESHOLD <= 1.0):
            raise ConfigError("LOW_CONFIDENCE_THRESHOLD must be between 0.0 and 1.0.")
        if not (0.0 <= self.SIMILARITY_THRESHOLD <= 1.0):
            raise ConfigError("SIMILARITY_THRESHOLD must be between 0.0 and 1.0.")
        if self.SIMILARITY_THRESHOLD < self.LOW_CONFIDENCE_THRESHOLD:
            logger.warning(
                "SIMILARITY_THRESHOLD is less than LOW_CONFIDENCE_THRESHOLD."
            )
        if self.LOG_LEVEL.upper() not in [
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        ]:
            logger.warning(f"Invalid LOG_LEVEL '{self.LOG_LEVEL}'. Defaulting to INFO.")
            self.LOG_LEVEL = "INFO"
        if self.DEFAULT_API_TIMEOUT_SECONDS <= 0:
            raise ConfigError("DEFAULT_API_TIMEOUT_SECONDS must be positive.")

        llm_api_key = self.llm.api_key.get_secret_value() if self.llm.api_key else ""
        if not llm_api_key or llm_api_key == "sk-dummy-llm-key-for-tests":
            logger.warning(
                "LLM_API_KEY is missing or set to a dummy value. LLM functionality may be limited."
            )

        if not self.ML_MODEL_PATH:
            logger.warning("ML_MODEL_PATH is not set. ML model loading may fail.")

        current_email_recipients = self.EMAIL_RECIPIENTS
        if isinstance(current_email_recipients, str):
            self.EMAIL_RECIPIENTS_LIST = [
                r.strip() for r in current_email_recipients.split(",") if r.strip()
            ]
        else:
            self.EMAIL_RECIPIENTS_LIST = []

        critical_secrets = {
            "LLM_API_KEY": self.llm.api_key,
            "ENCRYPTION_KEY": self.ENCRYPTION_KEY,
            "ADMIN_API_KEY": self.ADMIN_API_KEY,
        }
        for key_name, secret_field in critical_secrets.items():
            if (
                secret_field
                and isinstance(secret_field, SecretStr)
                and secret_field.get_secret_value().strip() == ""
            ):
                logger.warning(
                    f"CRITICAL: {key_name} is not set or empty. This may impact core functionality."
                )

    @classmethod
    def validate_file(cls, file_path: str) -> bool:
        CONFIG_ACCESS.labels(setting="validate_file").inc()
        if not cls._instance or not cls._instance.RELOAD_VALIDATE_FILES:
            return True
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                compile(f.read(), file_path, "exec")
            return True
        except SyntaxError as e:
            CONFIG_ERRORS.labels(error_type="file_validation_fail").inc()
            logger.error(f"Invalid syntax in {file_path}: {e}")
            return False

    @classmethod
    def reload(cls):
        CONFIG_ACCESS.labels(setting="reload").inc()
        cls._instance = None
        global arbiter_config
        global settings
        arbiter_config = ArbiterConfig.initialize()
        settings = arbiter_config
        logger.info("ArbiterConfig reloaded.")

    async def refresh(self) -> None:
        """
        Refreshes configuration from environment variables and files asynchronously.

        Raises:
            ValueError: If configuration validation fails.
        """
        # Get tracer lazily to avoid import-time initialization
        _tracer = _get_tracer()
        with _tracer.start_as_current_span("config_refresh"):
            try:
                new_config = ArbiterConfig()
                for field in self.model_fields:
                    setattr(self, field, getattr(new_config, field))

                # Async load personas
                persona_file_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "personas.json"
                )
                if os.path.exists(persona_file_path) and AIOFILES_AVAILABLE:
                    async with aiofiles.open(
                        persona_file_path, "r", encoding="utf-8"
                    ) as f:
                        personas = json.loads(await f.read())
                        if isinstance(personas, dict):
                            self.PERSONAS = personas
                elif os.path.exists(persona_file_path):
                    # Fallback to sync file reading if aiofiles not available
                    with open(persona_file_path, "r", encoding="utf-8") as f:
                        personas = json.load(f)
                        if isinstance(personas, dict):
                            self.PERSONAS = personas

                self._loaded_at = datetime.now().isoformat()
                CONFIG_OPS_TOTAL.labels(operation="refresh").inc()
                logger.info("Configuration refreshed successfully.")
            except Exception as e:
                logger.error(f"Configuration refresh failed: {e}", exc_info=True)
                CONFIG_ERRORS.labels(error_type="refresh_fail").inc()
                raise ValueError(f"Configuration refresh failed: {e}") from e

    @classmethod
    async def stream_config_change(cls, key: str, value: Any):
        """
        Stream configuration changes to Redis.

        Args:
            key: Configuration key that changed
            value: New value for the key
        """
        import redis.asyncio as redis

        if not cls._instance:
            logger.error("ArbiterConfig not initialized, cannot stream config changes.")
            CONFIG_ERRORS.labels(error_type="stream_config_fail").inc()
            return

        try:
            redis_url_str = cls._instance.REDIS_URL
            async with redis.from_url(
                redis_url_str, decode_responses=True
            ) as redis_client:
                # Safely redact secret values before publishing
                safe_value = value
                if isinstance(value, SecretStr):
                    safe_value = "[REDACTED]"

                await redis_client.publish(
                    "config_events",
                    json.dumps(
                        {
                            "key": key,
                            "value": safe_value,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    ),
                )
        except Exception as e:
            logger.error(f"Failed to stream config change to Redis: {e}")
            CONFIG_ERRORS.labels(error_type="stream_redis_fail").inc()

    def model_dump(self):
        """Compatibility method for both Pydantic v1 and v2"""
        if PYDANTIC_V2:
            return super().model_dump()
        else:
            return self.dict()

    def to_dict(self) -> Dict[str, Any]:
        CONFIG_ACCESS.labels(setting="to_dict").inc()
        config_dict = self.model_dump()

        # Redact secrets and handle specific types for dictionary output
        for field_name, value in config_dict.items():
            if isinstance(value, SecretStr):
                config_dict[field_name] = "[REDACTED]"
            elif isinstance(value, HttpUrl):
                config_dict[field_name] = str(value)
            elif isinstance(value, Enum):
                config_dict[field_name] = value.value
            elif field_name == "llm" and isinstance(value, dict):
                if "api_key" in value:
                    value["api_key"] = "[REDACTED]"

        config_dict["ENCRYPTION_KEY_BYTES"] = "[REDACTED_BYTES]"
        config_dict["EMAIL_RECIPIENTS_LIST"] = self.EMAIL_RECIPIENTS_LIST

        config_dict["_is_initialized"] = self._is_initialized
        config_dict["_loaded_at"] = self._loaded_at

        return config_dict

    async def rotate_encryption_key(self) -> None:
        """
        Rotates the encryption key.
        This operation should be handled with extreme care in a production environment.
        """
        try:
            self.ENCRYPTION_KEY.get_secret_value()
            new_key = Fernet.generate_key()
            self.ENCRYPTION_KEY = SecretStr(new_key.decode())
            # In a real-world scenario, you would need to:
            # 1. Update all encrypted data with the new key.
            # 2. Persist the new key to a secure vault (e.g., AWS Secrets Manager, HashiCorp Vault).
            # 3. Handle key distribution to all relevant services.
            logger.warning(
                "Encryption key rotated. Remember to re-encrypt all data and update a secure vault."
            )
            CONFIG_OPS_TOTAL.labels(operation="key_rotation").inc()
        except Exception as e:
            logger.error(f"Encryption key rotation failed: {e}", exc_info=True)
            CONFIG_ERRORS.labels(error_type="key_rotation_fail").inc()
            raise ValueError(f"Key rotation failed: {e}") from e

    def health_check(self) -> Dict[str, Any]:
        """
        Checks configuration validity and returns a health status.

        Returns:
            Dict with health status and details.
        """
        try:
            # Re-validate the current model instance
            self.model_validate(self.model_dump())
            return {"status": "healthy", "loaded_at": self._loaded_at}
        except Exception as e:
            logger.error(f"Configuration health check failed: {e}", exc_info=True)
            CONFIG_ERRORS.labels(error_type="health_check_fail").inc()
            return {"status": "unhealthy", "error": str(e)}

    @property
    def DATABASE_URL(self) -> str:
        """Alias for DB_PATH for backward compatibility."""
        return self.DB_PATH


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def load_persona_dict() -> Dict[str, str]:
    """
    Loads persona definitions from a JSON file.
    This is a placeholder implementation.
    """
    persona_file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "personas.json"
    )

    if os.path.exists(persona_file_path):
        try:
            with open(persona_file_path, "r", encoding="utf-8") as f:
                personas = json.load(f)
                if isinstance(personas, dict) and all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in personas.items()
                ):
                    logger.info(f"Loaded personas from {persona_file_path}.")
                    CONFIG_OPS_TOTAL.labels(operation="load_persona").inc()
                    return personas
                else:
                    logger.warning(
                        f"Persona file {persona_file_path} content is invalid. Expected Dict[str, str]. Using empty dict."
                    )
                    return {}
        except (json.JSONDecodeError, IOError) as e:
            logger.error(
                f"Failed to load personas from {persona_file_path}: {e}. Using empty dict.",
                exc_info=True,
            )
            CONFIG_ERRORS.labels(error_type="load_persona_fail").inc()
            raise  # Re-raise for tenacity
    else:
        logger.info(
            f"Persona file {persona_file_path} not found. Using empty dict for personas."
        )
        return {}


# The global instance will now be managed by initialize()
arbiter_config: Optional[ArbiterConfig] = None
settings: Optional[ArbiterConfig] = None

# Register as a plugin for dynamic management
registry.register(
    kind=PlugInKind.CORE_SERVICE,
    name="ArbiterConfig",
    version="1.0.0",
    author="Arbiter Team",
)(ArbiterConfig)
