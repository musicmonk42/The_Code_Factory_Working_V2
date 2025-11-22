import os
import json
import logging
import hashlib
from typing import Dict, Any, List, Optional, Annotated, Literal
from pydantic import (
    BaseModel, Field, ValidationError, model_validator, ConfigDict, RootModel, PlainSerializer,
    AnyUrl, field_validator, ValidationInfo
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

# --- Robustness for environs Failure ---
try:
    from environs import Env
except ImportError:
    logging.warning("environs package not found. Using os.getenv for environment variables.")
    class Env:
        def read_env(self):
            pass
        def str(self, key, default=None):
            return os.getenv(key, default)

logger = logging.getLogger(__name__)

# --- Environment Setup ---
env = Env()
env.read_env()

# --- Secure Key Management Utilities ---
# This is an enhanced SensitiveValue that uses a Pydantic RootModel for a clean representation
# while ensuring the actual value is redacted during serialization.
SensitiveString = Annotated[
    str,
    PlainSerializer(lambda x: "[SENSITIVE]", return_type=str)
]

class SensitiveValue(RootModel[str]):
    """
    A Pydantic wrapper for sensitive string values that automatically redacts
    itself when serialized to JSON or other formats.
    """
    def __str__(self) -> str:
        return "[SENSITIVE]"
    
    def __repr__(self) -> str:
        return "SensitiveValue('[SENSITIVE]')"
    
    def get_actual_value(self) -> str:
        """Returns the actual, unredacted string value."""
        # In Pydantic v2 with RootModel, use model_dump()
        return self.model_dump()
    
    def __hash__(self) -> int:
        return hash(self.model_dump())
    
    def __eq__(self, other) -> bool:
        if isinstance(other, SensitiveValue):
            return self.model_dump() == other.model_dump()
        return False

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return {"type": "string"}
    
    def model_dump(self, **kwargs):
        """Override to return the actual value for internal use"""
        return super().model_dump()

    def model_dump_json(self, **kwargs):
        """Override to return redacted value for JSON serialization"""
        return '"[SENSITIVE]"'

# --- Configuration Model for Meta-Learning Orchestrator ---
class MetaLearningConfig(BaseSettings):
    """
    Configuration settings for the Meta-Learning Orchestrator.
    Settings are loaded from environment variables (prefixed with ML_) or .env file.
    """
    model_config = SettingsConfigDict(
        env_prefix="ML_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Data Lake Configuration
    DATA_LAKE_PATH: str = Field(default="data/meta_learning_data.jsonl", description="Local file path for data lake fallback.")
    DATA_LAKE_S3_BUCKET: str = Field(default="my-meta-learning-data-bucket", description="S3 bucket name for data lake.")
    DATA_LAKE_S3_PREFIX: str = Field(default="meta_learning/records/", description="S3 prefix for data lake objects.")
    USE_S3_DATA_LAKE: bool = Field(default=False, description="Enable S3 as data lake.")

    # Audit Log Configuration
    AUDIT_LEDGER_URL: AnyUrl = Field(default="http://localhost:8000/audit_ledger", description="URL for the audit ledger service.")
    LOCAL_AUDIT_LOG_PATH: str = Field(default="data/meta_learning_audit.jsonl", description="Local file path for audit log fallback.")
    AUDIT_ENCRYPTION_KEY: Optional[SensitiveValue] = Field(default=None, description="Fernet encryption key for audit logs.")
    AUDIT_SIGNING_PRIVATE_KEY: Optional[SensitiveValue] = Field(default=None, description="ECDSA private key for signing audit logs (PEM format).")
    AUDIT_SIGNING_PUBLIC_KEY: Optional[SensitiveValue] = Field(default=None, description="ECDSA public key for verifying audit logs (PEM format).")
    AUDIT_LOG_ROTATION_SIZE_MB: int = Field(default=100, ge=1, description="Size in MB before audit log rotation.")
    AUDIT_LOG_MAX_FILES: int = Field(default=10, ge=1, description="Maximum number of rotated audit log files to keep.")

    # Rate Limiting Configuration
    LLM_RATE_LIMIT_CALLS: int = Field(default=100, ge=1, description="Number of LLM calls allowed per rate limit period.")
    LLM_RATE_LIMIT_PERIOD: int = Field(default=60, ge=1, description="The period in seconds for the LLM rate limit.")

    # Agent Configuration
    DEFAULT_LANGUAGE: str = Field(default="en", description="Default language for agent communication (e.g., 'en' for English).")
    
    # Additional fields expected by tests
    DEFAULT_PROVIDER: str = Field(default="openai", description="Default LLM provider")
    DEFAULT_LLM_MODEL: str = Field(default="gpt-3.5-turbo", description="Default LLM model")
    DEFAULT_TEMP: float = Field(default=0.7, description="Default temperature for LLM")
    MEMORY_WINDOW: int = Field(default=5, description="Conversation memory window size")
    MAX_META_LEARNING_CORRECTIONS: int = Field(default=10, description="Maximum corrections to store")
    MAX_CORRECTION_ENTRY_SIZE: int = Field(default=10000, description="Maximum size of correction entry")
    FALLBACK_PROVIDER: str = Field(default="anthropic", description="Fallback LLM provider")
    FALLBACK_LLM_CONFIG: dict = Field(default={"model": "claude-2", "temperature": 0.7}, description="Fallback LLM configuration")
    MAX_MM_DATA_SIZE_MB: int = Field(default=100, description="Maximum multimodal data size in MB")
    GDPR_MODE: bool = Field(default=True, description="Enable GDPR mode")
    CACHE_EXPIRATION_SECONDS: int = Field(default=3600, description="Cache expiration in seconds")
    POSTGRES_DB_URL: Optional[str] = Field(default=None, description="PostgreSQL database URL")
    LLM_ERRORS_TOTAL: Optional[str] = Field(default=None, description="LLM errors total metric name")

    # Kafka Streaming Ingestion Configuration
    KAFKA_BOOTSTRAP_SERVERS: str = Field(default="localhost:9092", description="Comma-separated Kafka bootstrap servers.")
    KAFKA_TOPIC: str = Field(default="meta_learning_events", description="Kafka topic for meta-learning events.")
    KAFKA_AUDIT_TOPIC: str = Field(default="meta_learning_audit_log", description="Kafka topic for audit logs.")
    USE_KAFKA_INGESTION: bool = Field(default=False, description="Enable Kafka for event ingestion.")
    USE_KAFKA_AUDIT: bool = Field(default=False, description="Enable Kafka for audit log ingestion.")

    # Redis for Distributed Lock (High Availability)
    REDIS_URL: AnyUrl = Field(default="redis://localhost:6379/0", description="Redis URL for distributed lock.")
    REDIS_LOCK_KEY: str = Field(default="meta_learning_orchestrator_leader_lock", description="Key for Redis distributed lock.")
    REDIS_LOCK_TTL_SECONDS: int = Field(default=60, ge=10, description="TTL for Redis lock in seconds.")

    # Training and Deployment Parameters
    MIN_RECORDS_FOR_TRAINING: int = Field(default=500, ge=1, description="Minimum records required to trigger training.")
    TRAINING_CHECK_INTERVAL_SECONDS: int = Field(default=3600, ge=60, description="Interval to check for new training data in seconds.")
    DEPLOYMENT_CHECK_INTERVAL_SECONDS: int = Field(default=1800, ge=60, description="Interval to check for new models to deploy in seconds.")
    MODEL_BENCHMARK_THRESHOLD: float = Field(default=0.85, ge=0.0, le=1.0, description="Performance threshold for model deployment.")
    
    # Service Endpoints
    ML_PLATFORM_ENDPOINT: AnyUrl = Field(default="http://localhost:8081/ml-platform", description="Endpoint for the ML Platform service.")
    AGENT_CONFIG_SERVICE_ENDPOINT: AnyUrl = Field(default="http://localhost:8082/agent-config", description="Endpoint for the Agent Configuration service.")
    POLICY_ENGINE_ENDPOINT: AnyUrl = Field(default="http://localhost:8083/policy-engine", description="Endpoint for the Policy Engine service.")
    
    # Deployment and Data Retention
    MAX_DEPLOYMENT_RETRIES: int = Field(default=5, ge=0, description="Maximum retries for model deployment.")
    DEPLOYMENT_RETRY_DELAY_SECONDS: int = Field(default=60, ge=1, description="Delay between deployment retries in seconds.")
    DATA_RETENTION_DAYS: int = Field(default=30, ge=1, description="Number of days to retain data.")

    # PII Redaction
    REDACT_PII_IN_LOGS: bool = Field(default=True, description="Enable PII redaction in logs.")
    PII_SENSITIVE_KEYS: List[str] = Field(
        default=["email", "password", "name", "ssn", "credit_card", "api_key"],
        description="List of keys to redact as PII in logs and data."
    )

    # Dynamic Configuration Reloading
    CONFIG_RELOAD_INTERVAL_SECONDS: int = Field(default=300, ge=0, description="Interval to check for config reloads (e.g., from Etcd). Set to 0 to disable.")
    CONFIG_SOURCE: str = Field(default="env", description="Source for dynamic configuration (e.g., 'env', 'file', 'etcd').")
    CONFIG_FILE_PATH: Optional[str] = Field(default=None, description="Path to a configuration file if CONFIG_SOURCE is 'file'.")
    ETCD_HOST: Optional[str] = Field(default=None, description="Etcd host for dynamic configuration.")
    ETCD_PORT: Optional[int] = Field(default=2379, description="Etcd port for dynamic configuration.")
    ETCD_PREFIX: Optional[str] = Field(default="/config/meta-learning", description="Etcd prefix for configuration keys.")

    @field_validator("DATA_LAKE_PATH", "LOCAL_AUDIT_LOG_PATH", "CONFIG_FILE_PATH", mode="before")
    def validate_file_paths(cls, v, info: ValidationInfo):
        """Ensures that file paths are valid and their parent directories exist."""
        if v:
            path = Path(v)
            try:
                parent_dir = path.parent
                if parent_dir and not parent_dir.exists():
                    parent_dir.mkdir(parents=True, exist_ok=True)
                # Create empty file if it doesn't exist, but skip for CONFIG_FILE_PATH
                # since it's optional and may not need to be created
                field_name = info.field_name if info else None
                if not path.exists() and field_name != 'CONFIG_FILE_PATH':
                    path.touch()
            except Exception as e:
                raise ValueError(f"Invalid file path or cannot create directory/file for {v}: {e}")
            return str(path)  # Return as string since we changed the type
        return v

    @field_validator("AUDIT_ENCRYPTION_KEY", "AUDIT_SIGNING_PRIVATE_KEY", "AUDIT_SIGNING_PUBLIC_KEY", mode="before")
    def handle_sensitive_values(cls, v):
        if v is None or (isinstance(v, str) and not v):
            return None
        return SensitiveValue(root=v)

    @model_validator(mode='after')
    def validate_kafka_settings(self):
        """Ensures Kafka bootstrap servers are not empty if Kafka is enabled."""
        if (self.USE_KAFKA_INGESTION or self.USE_KAFKA_AUDIT) and not self.KAFKA_BOOTSTRAP_SERVERS:
            raise ValueError("KAFKA_BOOTSTRAP_SERVERS must be set if Kafka ingestion/audit is enabled.")
        return self
    
    @field_validator("REDIS_URL")
    def validate_redis_url(cls, v):
        """Ensures Redis URL is a valid HTTP or Redis URL."""
        if v.scheme not in ("http", "https", "redis", "rediss"):
            raise ValueError("REDIS_URL must be a valid HTTP or Redis URL scheme (http, https, redis, rediss).")
        return v

    @field_validator("ML_PLATFORM_ENDPOINT", "AGENT_CONFIG_SERVICE_ENDPOINT", "POLICY_ENGINE_ENDPOINT")
    def validate_http_endpoints(cls, v):
        """Ensures HTTP endpoints are valid URLs."""
        if v.scheme not in ("http", "https"):
            raise ValueError(f"Endpoint {v} must use http or https scheme.")
        return v

    def reload_config(self):
        """
        Dynamically reloads configuration based on CONFIG_SOURCE.
        This is a placeholder for actual implementation with file/etcd watchers.
        """
        if self.CONFIG_SOURCE == "file" and self.CONFIG_FILE_PATH:
            try:
                with open(self.CONFIG_FILE_PATH, "r") as f:
                    new_config_data = json.load(f)
                new_config = MetaLearningConfig(**new_config_data)
                # Update current instance with new values
                self.__dict__.update(new_config.model_dump())
                logger.info(f"Configuration reloaded from file: {self.CONFIG_FILE_PATH}")
            except ValidationError as e:
                logger.error(f"Failed to reload config from file due to validation error. Path: {self.CONFIG_FILE_PATH}, Error: {str(e)}")
            except Exception as e:
                logger.error(f"An unexpected error occurred during config file reload: {str(e)}", exc_info=True)
        elif self.CONFIG_SOURCE == "etcd" and self.ETCD_HOST:
            logger.warning("Etcd configuration reloading is not yet implemented.")
            # TODO: Implement Etcd client to fetch and update configuration
        elif self.CONFIG_SOURCE == "env":
            logger.info("Configuration is set to reload from environment variables (default behavior).")
        else:
            logger.warning(f"Unsupported CONFIG_SOURCE: {self.CONFIG_SOURCE}. Configuration will not be dynamically reloaded.")

# --- Persona Management ---
def load_persona_dict() -> Dict[str, str]:
    """Load personas from a configuration file or database."""
    try:
        persona_file = env.str("PERSONA_FILE", "personas.json")
        if os.path.exists(persona_file):
            with open(persona_file, "r") as f:
                personas = json.load(f)
                if not isinstance(personas, dict):
                    raise ValueError("Persona file must contain a dictionary")
                return personas
        else:
            logger.warning(f"Persona file not found: {persona_file}. Using default personas.")
            return {"default": "You are a helpful AI assistant."}
    except Exception as e:
        logger.error(f"Failed to load personas: {str(e)}", exc_info=True)
        return {"default": "You are a helpful AI assistant."}

# --- Multi-Modal Schema ---
class MultiModalData(BaseModel):
    model_config = ConfigDict(strict=True)
    data_type: Literal['image', 'audio', 'video', 'text_file', 'pdf_file']
    data: bytes
    metadata: Dict[str, Any] = {}

    def model_dump_for_log(self) -> Dict[str, Any]:
        # Always compute hash, even for empty data
        sha = hashlib.sha256(self.data).hexdigest()
        return {"data_type": self.data_type, "data_hash": sha, "metadata": self.metadata}

# Instantiate config
try:
    Config = MetaLearningConfig()
    logger.info("Configuration loaded successfully.")
except ValidationError as e:
    logger.error(f"Configuration validation failed: {e.errors()}")
    raise