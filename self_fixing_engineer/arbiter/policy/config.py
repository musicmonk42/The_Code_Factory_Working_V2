# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Configuration management for the Arbiter system using pydantic-settings.
Supports environment variables, .env files, and runtime reloading.

Metrics:
- arbiter_config_errors_total: Total configuration errors (error_type)
- arbiter_config_initializations_total: Total configuration initializations (result)
- arbiter_config_reload_frequency_total: Total number of configuration reloads (result)
- arbiter_config_validation_duration_seconds: Duration of configuration validation (operation)
- arbiter_config_to_dict_cache_hits_total: Total cache hits/misses for to_dict calls (result)
- arbiter_config_redis_validation_duration_seconds: Duration of Redis URL validation (operation)
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import warnings
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import redis.asyncio as redis

# Import the centralized tracer configuration
from self_fixing_engineer.arbiter.otel_config import get_tracer
from cryptography.fernet import Fernet
from prometheus_client import REGISTRY, Counter, Histogram
from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub.utils")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)


# Helper function for idempotent metric creation
def _get_or_create_metric(
    metric_class: type, name: str, doc: str, labelnames: tuple, buckets: tuple = None
):
    """Idempotently create or retrieve a Prometheus metric."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    if buckets is not None and metric_class == Histogram:
        return metric_class(name, doc, labelnames=labelnames, buckets=buckets)
    return metric_class(name, doc, labelnames=labelnames)


# --- Prometheus Metrics (Define BEFORE using them) ---
CONFIG_ERRORS = _get_or_create_metric(
    Counter,
    "arbiter_config_errors_total",
    "Total configuration errors",
    labelnames=("error_type",),
)
CONFIG_INITIALIZATIONS = _get_or_create_metric(
    Counter,
    "arbiter_config_initializations_total",
    "Total configuration initializations",
    labelnames=("result",),
)
CONFIG_RELOAD_FREQUENCY = _get_or_create_metric(
    Counter,
    "arbiter_config_reload_frequency_total",
    "Total number of configuration reloads",
    labelnames=("result",),
)
CONFIG_VALIDATION_DURATION = _get_or_create_metric(
    Histogram,
    "arbiter_config_validation_duration_seconds",
    "Duration of configuration validation",
    labelnames=("operation",),
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)
CONFIG_TO_DICT_CACHE_HITS = _get_or_create_metric(
    Counter,
    "arbiter_config_to_dict_cache_hits_total",
    "Total cache hits/misses for to_dict calls",
    labelnames=("result",),
)
CONFIG_REDIS_VALIDATION_DURATION = _get_or_create_metric(
    Histogram,
    "arbiter_config_redis_validation_duration_seconds",
    "Duration of Redis URL validation",
    labelnames=("operation",),
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)

# Get tracer using centralized configuration
tracer = get_tracer("arbiter_config")


class ArbiterConfig(BaseSettings):
    """
    ArbiterConfig provides a production-ready configuration system using pydantic-settings.
    Settings can be loaded from environment variables or a .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        extra="ignore",
        validate_assignment=True,
        protected_namespaces=(),
    )

    _config_cache: Optional[Dict[str, Any]] = PrivateAttr(default=None)
    _config_cache_timestamp: float = PrivateAttr(default=0.0)
    _config_cache_ttl: float = PrivateAttr(default=300.0)  # 5-minute cache TTL
    _cache_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _redis_pool: Optional[redis.ConnectionPool] = PrivateAttr(default=None)
    _redis_pool_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _redis_pools: dict = PrivateAttr(
        default_factory=dict
    )  # Supporting multiple pools for different URLs

    # Core Settings
    POLICY_CONFIG_FILE_PATH: str = Field(
        default_factory=lambda: os.getenv(
            "POLICY_CONFIG_FILE_PATH",
            os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../policies.json")
            ),
        ),
        description="Absolute path to the policy configuration file.",
    )
    AUDIT_LOG_FILE_PATH: str = Field(
        default_factory=lambda: os.getenv(
            "AUDIT_LOG_FILE_PATH",
            os.path.abspath(
                os.path.join(os.path.dirname(__file__), "policy_audit.log")
            ),
        ),
        description="Absolute path to the audit log file.",
    )
    DEFAULT_AUTO_LEARN_POLICY: bool = Field(
        True, description="Default policy for auto-learning if not specified in rules."
    )
    POLICY_REFRESH_INTERVAL_SECONDS: int = Field(
        300, description="Interval for policy refresh in seconds"
    )
    LLM_POLICY_EVALUATION_ENABLED: bool = Field(
        True, description="Enable LLM-based policy evaluation."
    )
    VALID_DOMAIN_PATTERN: str = Field(
        r"^[a-zA-Z0-9_.-]+$",
        description="Regex pattern for validating policy domain names.",
    )
    MAX_LEARN_RETRIES: int = Field(
        5, ge=0, description="Maximum number of retries for a failed learning attempt."
    )
    REDIS_URL: Optional[str] = Field(
        default_factory=lambda: os.getenv("REDIS_URL", None),
        description="URL for the Redis instance, required in production.",
    )
    CONFIG_REFRESH_INTERVAL_SECONDS: int = Field(
        300, description="Config refresh interval in seconds"
    )

    # Circuit Breaker Settings
    CIRCUIT_BREAKER_STATE_TTL_SECONDS: int = Field(
        86400, description="Redis key TTL in seconds"
    )
    CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS: int = Field(
        3600, description="Interval for cleanup task in seconds"
    )
    REDIS_MAX_CONNECTIONS: int = Field(100, description="Redis connection pool size")
    REDIS_SOCKET_TIMEOUT: float = Field(
        5.0, description="Redis socket timeout in seconds"
    )
    REDIS_SOCKET_CONNECT_TIMEOUT: float = Field(
        5.0, description="Redis socket connect timeout in seconds"
    )
    CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL: float = Field(
        0.1, description="Minimum interval between Redis operations in seconds"
    )
    CIRCUIT_BREAKER_CRITICAL_PROVIDERS: str = Field(
        "", description="Comma-separated list of providers exempt from cleanup"
    )
    CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL: float = Field(
        300.0, description="Interval for logging validation errors in seconds"
    )
    CIRCUIT_BREAKER_MAX_PROVIDERS: int = Field(
        1000, description="Maximum number of providers"
    )
    PAUSE_CIRCUIT_BREAKER_TASKS: str = Field(
        "false", description="Set to 'true' to pause cleanup and refresh tasks"
    )
    POLICY_PAUSE_POLLING_INTERVAL: float = Field(
        60.0, description="Polling interval when policy refresh is paused"
    )

    # Secret Settings
    ENCRYPTION_KEY: SecretStr = Field(
        SecretStr(os.getenv("ENCRYPTION_KEY", "")),
        description="A 32-byte URL-safe base64-encoded key for Fernet encryption.",
    )
    OPENAI_API_KEY: SecretStr = Field(
        SecretStr(os.getenv("OPENAI_API_KEY", "")), description="API key for OpenAI."
    )
    ANTHROPIC_API_KEY: SecretStr = Field(
        SecretStr(os.getenv("ANTHROPIC_API_KEY", "")),
        description="API key for Anthropic.",
    )
    GEMINI_API_KEY: SecretStr = Field(
        SecretStr(os.getenv("GOOGLE_API_KEY", "")),
        description="API key for Google Gemini.",
    )

    # LLM Settings
    LLM_PROVIDER: str = Field(
        "openai", description="Default LLM provider to use (openai, anthropic, gemini)."
    )
    LLM_MODEL: str = Field("gpt-4o-mini", description="Default model name for LLM.")
    LLM_API_URL: Optional[str] = Field(
        None, description="Custom API URL for LLM provider, must be a valid URL."
    )
    LLM_API_TIMEOUT_SECONDS: float = Field(
        30, gt=0, description="Timeout for LLM API calls in seconds."
    )
    LLM_API_BACKOFF_MAX_SECONDS: float = Field(
        60.0, description="Maximum backoff seconds for LLM API circuit breaker"
    )
    LLM_API_FAILURE_THRESHOLD: int = Field(
        3, description="Failure threshold for LLM API circuit breaker"
    )
    LLM_POLICY_MIN_TRUST_SCORE: float = Field(
        0.5,
        ge=0,
        le=1,
        description="Minimum trust score for LLM policy decisions to be considered valid.",
    )
    LLM_VALID_RESPONSES: list = Field(
        default_factory=lambda: ["YES", "NO"], description="Valid responses from LLM"
    )

    # Role Mappings
    ROLE_MAPPINGS: Dict[str, list] = Field(
        default_factory=lambda: {
            "admin": ["admin", "user", "explorer_user"],
            "auditor": ["auditor", "user"],
            "explorer_user": ["explorer_user", "user"],
            "guest": ["guest"],
            "*": ["user"],
        },
        description="User role mappings",
    )

    # Decision Optimizer Settings
    DECISION_OPTIMIZER_SETTINGS: Dict[str, Any] = Field(
        default_factory=lambda: {
            "score_threshold": 0.5,
            "temporal_window_seconds": 86400,
            "anomaly_threshold": 3.0,
            "feedback_db_path": os.getenv(
                "FEEDBACK_DB_PATH",
                os.path.abspath(os.path.join(os.path.dirname(__file__), "feedback.db")),
            ),
            "llm_feedback_enabled": False,
            "llm_feedback_model": os.getenv("LLM_FEEDBACK_MODEL", "gpt-4o-mini"),
            "llm_feedback_api_url": os.getenv(
                "LLM_FEEDBACK_API_URL", "http://localhost:11434/api/generate"
            ),
            "llm_feedback_api_key": os.getenv("LLM_FEEDBACK_API_KEY", "dummy_key"),
            "llm_call_latency_buckets": (0.1, 0.5, 1, 2, 5, 10, 30, 60),
            "feedback_processing_buckets": (0.001, 0.01, 0.1, 1, 10),
            "score_rules": {
                "login_attempts_penalty": -0.2,
                "device_trusted_bonus": 0.3,
                "recent_login_bonus": 0.1,
                "admin_user_bonus": 0.2,
                "default_score": 0.5,
            },
        },
        description="Configuration for the decision optimizer.",
    )

    @field_validator("DECISION_OPTIMIZER_SETTINGS", mode="before")
    @classmethod
    def parse_optimizer_settings(cls, v):

        # If pydantic passes the entire environment as a dict, reject it
        if isinstance(v, dict):
            # Check if it's actually the environment dict by looking for definitive markers
            # Unix and Windows environment markers
            if all(k in v for k in ["PATH", "HOME"]) or all(
                k in v for k in ["PATH", "SYSTEMROOT"]
            ):
                raise ValueError(
                    "DECISION_OPTIMIZER_SETTINGS appears to be the full environment dict"
                )
            # Otherwise, assume it's a valid settings dict
            return v

        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if not isinstance(parsed, dict):
                    raise ValueError()
                return parsed
            except Exception:
                raise ValueError(
                    "DECISION_OPTIMIZER_SETTINGS must be a dict or a JSON string representing a dict"
                )
        if v is None:
            # Allow default factory to work
            return None
        raise ValueError("DECISION_OPTIMIZER_SETTINGS must be a dict")

    def get_redis_pool(self, redis_url: str):
        with self._redis_pool_lock:
            if redis_url not in self._redis_pools:
                self._redis_pools[redis_url] = redis.ConnectionPool.from_url(redis_url)
            return self._redis_pools[redis_url]

    @model_validator(mode="before")
    @classmethod
    def validate_secrets(cls, values: dict) -> dict:
        """Validates critical secrets, file paths, LLM settings, and circuit breaker settings."""
        with tracer.start_as_current_span("validate_secrets") as span:
            start_time = time.monotonic()
            is_production = os.getenv("APP_ENV", "development") == "production"
            # Validate ENCRYPTION_KEY and REDIS_URL in production
            if is_production:
                if not values.get("ENCRYPTION_KEY"):
                    CONFIG_ERRORS.labels(error_type="missing_encryption_key").inc()
                    span.record_exception(
                        ValueError("ENCRYPTION_KEY must be set in production.")
                    )
                    raise ValueError("ENCRYPTION_KEY must be set in production.")
                if not values.get("REDIS_URL"):
                    CONFIG_ERRORS.labels(error_type="missing_redis_url").inc()
                    span.record_exception(
                        ValueError("REDIS_URL must be set in production.")
                    )
                    raise ValueError("REDIS_URL must be set in production.")
                for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
                    if not values.get(key):
                        logger.debug(
                            f"{key} is not set. LLM functionality for {key.split('_')[0].lower()} will be disabled."
                        )
                        span.set_attribute(f"{key.lower()}_status", "missing")
                if values.get("ENCRYPTION_KEY"):
                    try:
                        key = values["ENCRYPTION_KEY"].encode("utf-8")
                        if (
                            len(key) != 44
                        ):  # Fernet keys are 32 bytes, base64-encoded to 44 characters
                            raise ValueError(
                                "ENCRYPTION_KEY must be a 32-byte base64-encoded string"
                            )
                        Fernet(key)
                        span.set_attribute("encryption_key_status", "valid")
                    except Exception as e:
                        CONFIG_ERRORS.labels(error_type="invalid_encryption_key").inc()
                        span.record_exception(e)
                        raise ValueError(f"Invalid ENCRYPTION_KEY: {e}")
            # Validate regex pattern
            try:
                if values.get("VALID_DOMAIN_PATTERN"):
                    re.compile(values["VALID_DOMAIN_PATTERN"])
                    span.set_attribute("valid_domain_pattern_status", "valid")
            except re.error as e:
                logger.error(f"Invalid VALID_DOMAIN_PATTERN: {e}")
                CONFIG_ERRORS.labels(error_type="invalid_regex_pattern").inc()
                span.record_exception(e)
                span.set_attribute("valid_domain_pattern_status", "invalid")
                values["VALID_DOMAIN_PATTERN"] = r"^[a-zA-Z0-9_.-]+$"
            # Validate file paths
            for path_key in (
                "POLICY_CONFIG_FILE_PATH",
                "AUDIT_LOG_FILE_PATH",
                "DECISION_OPTIMIZER_SETTINGS",
            ):
                if path_key == "DECISION_OPTIMIZER_SETTINGS":
                    path = values.get(path_key, {}).get("feedback_db_path", "")
                else:
                    path = values.get(path_key, "")
                if path and not os.path.isabs(path):
                    logger.error(
                        f"Invalid {path_key}: {path} must be an absolute path."
                    )
                    CONFIG_ERRORS.labels(error_type="invalid_file_path").inc()
                    span.record_exception(
                        ValueError(f"{path_key} must be an absolute path.")
                    )
                    span.set_attribute(f"{path_key.lower()}_status", "invalid")
                    if path_key == "POLICY_CONFIG_FILE_PATH":
                        values[path_key] = os.path.abspath(
                            os.path.join(os.path.dirname(__file__), "../policies.json")
                        )
                    elif path_key == "AUDIT_LOG_FILE_PATH":
                        values[path_key] = os.path.abspath(
                            os.path.join(os.path.dirname(__file__), "policy_audit.log")
                        )
                    else:
                        values[path_key]["feedback_db_path"] = os.path.abspath(
                            os.path.join(os.path.dirname(__file__), "feedback.db")
                        )
                else:
                    span.set_attribute(f"{path_key.lower()}_status", "valid")
            # Validate circuit breaker critical providers
            critical_providers = values.get("CIRCUIT_BREAKER_CRITICAL_PROVIDERS", "")
            if critical_providers:
                providers = [
                    p.strip() for p in critical_providers.split(",") if p.strip()
                ]
                invalid_providers = [
                    p for p in providers if not re.match(r"^[a-zA-Z0-9_-]+$", p)
                ]
                if invalid_providers:
                    logger.error(
                        f"Invalid CIRCUIT_BREAKER_CRITICAL_PROVIDERS: {invalid_providers}"
                    )
                    CONFIG_ERRORS.labels(error_type="invalid_critical_providers").inc()
                    span.record_exception(
                        ValueError(
                            f"Invalid CIRCUIT_BREAKER_CRITICAL_PROVIDERS: {invalid_providers}"
                        )
                    )
                    span.set_attribute("critical_providers_status", "invalid")
                    values["CIRCUIT_BREAKER_CRITICAL_PROVIDERS"] = ""
                else:
                    span.set_attribute("critical_providers_status", "valid")
            # Validate LLM provider
            llm_provider = values.get("LLM_PROVIDER", "openai")
            valid_providers = {"openai", "anthropic", "gemini", "google"}
            if llm_provider not in valid_providers:
                logger.error(
                    f"Invalid LLM_PROVIDER: {llm_provider}. Must be one of {valid_providers}"
                )
                CONFIG_ERRORS.labels(error_type="invalid_llm_provider").inc()
                span.record_exception(
                    ValueError(f"Invalid LLM_PROVIDER: {llm_provider}")
                )
                span.set_attribute("llm_provider_status", "invalid")
                values["LLM_PROVIDER"] = "openai"
            else:
                span.set_attribute("llm_provider_status", "valid")
            # Validate LLM model based on provider
            llm_model = values.get("LLM_MODEL", "gpt-4o-mini")
            valid_models = {
                "openai": {"gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"},
                "anthropic": {"claude-3-opus", "claude-3-sonnet", "claude-3-haiku"},
                "gemini": {"gemini-1.5-pro", "gemini-1.5-flash"},
                "google": {"gemini-1.5-pro", "gemini-1.5-flash"},
            }
            if llm_model not in valid_models.get(llm_provider, set()):
                logger.error(
                    f"Invalid LLM_MODEL: {llm_model} for provider {llm_provider}. Must be one of {valid_models.get(llm_provider, [])}"
                )
                CONFIG_ERRORS.labels(error_type="invalid_llm_model").inc()
                span.record_exception(
                    ValueError(
                        f"Invalid LLM_MODEL: {llm_model} for provider {llm_provider}"
                    )
                )
                span.set_attribute("llm_model_status", "invalid")
                values["LLM_MODEL"] = (
                    "gpt-4o-mini"
                    if llm_provider == "openai"
                    else (
                        "claude-3-sonnet"
                        if llm_provider == "anthropic"
                        else "gemini-1.5-flash"
                    )
                )
            else:
                span.set_attribute("llm_model_status", "valid")
            # Validate LLM API URL
            llm_api_url = values.get("LLM_API_URL", None)
            if llm_api_url and not re.match(
                r"^https?://[\w.-]+(:\d+)?(/.*)?$", llm_api_url
            ):
                logger.error(f"Invalid LLM_API_URL: {llm_api_url}")
                CONFIG_ERRORS.labels(error_type="invalid_llm_api_url").inc()
                span.record_exception(ValueError(f"Invalid LLM_API_URL: {llm_api_url}"))
                span.set_attribute("llm_api_url_status", "invalid")
                values["LLM_API_URL"] = None
            else:
                span.set_attribute("llm_api_url_status", "valid")

            CONFIG_VALIDATION_DURATION.labels(operation="validate_secrets").observe(
                time.monotonic() - start_time
            )
            return values

    @model_validator(mode="after")
    def validate_redis_url(self):
        # Skip Redis connection validation in CI/test environments
        if (
            os.getenv("CI") in ("1", "true", "True", "TRUE")
            or os.getenv("GITHUB_ACTIONS") in ("1", "true", "True", "TRUE")
            or os.getenv("TESTING") == "1"
            or os.getenv("ENVIRONMENT") == "test"
        ):
            logger.info(
                "Skipping Redis URL connection validation (CI/test environment detected)"
            )
            if self.REDIS_URL:
                # Basic URL format validation without connection
                try:
                    parsed = urlparse(self.REDIS_URL)
                    if parsed.scheme not in ("redis", "rediss"):
                        logger.warning(f"Invalid Redis scheme: {parsed.scheme}")
                except Exception as e:
                    logger.warning(f"Redis URL format validation failed: {e}")
            return self

        url = self.REDIS_URL
        if url:
            start_time = time.time()
            with tracer.start_as_current_span("validate_redis_url") as span:
                try:
                    conn = redis.Redis.from_url(url)
                    try:
                        # Check if we're in an async context
                        asyncio.get_running_loop()
                        # We're already in async - skip ping or schedule it
                        logger.debug("Skipping Redis ping in validator (async context)")
                        span.set_attribute(
                            "redis_validation_status", "skipped_async_context"
                        )
                    except RuntimeError:
                        # No running loop - safe to use asyncio.run
                        pong = asyncio.run(conn.ping())
                        if not pong:
                            raise ValueError("Redis ping failed")
                        logger.info("Redis URL validated successfully.")
                        span.set_attribute("redis_validation_status", "valid")
                except Exception as e:
                    logger.error(f"Invalid REDIS_URL: {e}")
                    CONFIG_ERRORS.labels(error_type="invalid_redis_url").inc()
                    span.record_exception(e)
                    span.set_attribute("redis_validation_status", "invalid")
                    if os.getenv("APP_ENV") == "production":
                        raise ValueError(f"Invalid REDIS_URL: {e}")
                finally:
                    CONFIG_REDIS_VALIDATION_DURATION.labels(
                        operation="validate_redis_url"
                    ).observe(time.time() - start_time)
        return self

    async def reload_config(self) -> None:
        """Reloads configuration from environment variables and .env file."""
        with tracer.start_as_current_span("reload_config") as span:
            try:
                # Re-read from environment and .env file using proper Pydantic V2 pattern
                new_config = type(self)(_env_file=self.model_config.get("env_file"))

                # Atomically update all fields from the reloaded config
                for field in self.model_fields:
                    if hasattr(new_config, field):
                        setattr(self, field, getattr(new_config, field))

                # Invalidate cache
                with self._cache_lock:
                    self._config_cache = None
                    self._config_cache_timestamp = 0.0

                logger.info("ArbiterConfig reloaded successfully.")
                CONFIG_RELOAD_FREQUENCY.labels(result="success").inc()
                span.set_attribute("reload_status", "success")

            except Exception as e:
                logger.error(f"Failed to reload ArbiterConfig: {e}")
                CONFIG_RELOAD_FREQUENCY.labels(result="failed").inc()
                CONFIG_ERRORS.labels(error_type="reload_failed").inc()
                span.record_exception(e)
                span.set_attribute("reload_status", "failed")
                raise

    def to_dict(self) -> Dict[str, Any]:
        """Exports a dictionary representation of the config, with secrets redacted."""
        with tracer.start_as_current_span("to_dict") as span:
            with self._cache_lock:
                current_time = time.monotonic()
                if (
                    self._config_cache is not None
                    and (current_time - self._config_cache_timestamp)
                    < self._config_cache_ttl
                ):
                    CONFIG_TO_DICT_CACHE_HITS.labels(result="hit").inc()
                    span.set_attribute("cache_status", "hit")
                    return self._config_cache

                result = self.model_dump()
                for key, value in result.items():
                    if isinstance(self.model_fields[key].default, SecretStr) or key in [
                        "ENCRYPTION_KEY",
                        "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY",
                        "GEMINI_API_KEY",
                    ]:
                        result[key] = "[REDACTED]"
                        span.set_attribute(f"field.{key}", "redacted")
                    elif isinstance(value, dict):
                        for sub_key in value:
                            if "api_key" in sub_key or "secret" in sub_key:
                                value[sub_key] = "[REDACTED]"
                                span.set_attribute(f"field.{key}.{sub_key}", "redacted")

                self._config_cache = result
                self._config_cache_timestamp = current_time
                CONFIG_TO_DICT_CACHE_HITS.labels(result="miss").inc()
                span.set_attribute("cache_status", "miss")
                return result

    def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        """Retrieves the API key for a given LLM provider from environment variables."""
        with tracer.start_as_current_span(
            "get_api_key_for_provider", attributes={"provider": provider}
        ) as span:
            if provider == "openai":
                key = os.getenv("OPENAI_API_KEY")
                span.set_attribute("provider_key", "OPENAI_API_KEY")
            elif provider == "anthropic":
                key = os.getenv("ANTHROPIC_API_KEY")
                span.set_attribute("provider_key", "ANTHROPIC_API_KEY")
            elif provider == "gemini" or provider == "google":
                key = os.getenv("GOOGLE_API_KEY")
                span.set_attribute("provider_key", "GOOGLE_API_KEY")
            else:
                key = os.getenv("LLM_API_KEY")
                logger.warning(
                    f"No specific API key found for provider: {provider}. Falling back to LLM_API_KEY."
                )
                span.set_attribute("provider_key", "LLM_API_KEY")
            span.set_attribute("key_status", "present" if key else "missing")
            return key


_instance = None
_lock = threading.Lock()


def get_config() -> ArbiterConfig:
    """Factory function to get the singleton ArbiterConfig instance."""
    with tracer.start_as_current_span("get_config") as span:
        global _instance
        if _instance is None:
            with _lock:
                if _instance is None:
                    try:
                        _instance = ArbiterConfig()
                        logger.info("ArbiterConfig instance created and initialized.")
                        CONFIG_INITIALIZATIONS.labels(result="success").inc()
                        span.set_attribute("initialization_status", "success")
                    except Exception as e:
                        logger.error(f"Failed to initialize ArbiterConfig: {e}")
                        CONFIG_ERRORS.labels(error_type="initialization_failed").inc()
                        span.record_exception(e)
                        span.set_attribute("initialization_status", "failed")
                        raise
        return _instance


# DO NOT instantiate at module import time - this causes test issues
# Users should call get_config() when they need the config instance
