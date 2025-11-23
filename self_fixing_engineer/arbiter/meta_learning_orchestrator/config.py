import asyncio
import json
import logging
import os
from typing import Optional

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Note: The following are optional dependencies for full functionality.
# Install with: pip install watchdog etcd3-py boto3 aioredis
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_INSTALLED = True
except ImportError:
    WATCHDOG_INSTALLED = False
try:
    import etcd3

    ETCD3_INSTALLED = True
except ImportError:
    ETCD3_INSTALLED = False
try:
    import boto3

    BOTO3_INSTALLED = True
except ImportError:
    BOTO3_INSTALLED = False
try:
    import redis.asyncio as redis

    AIOREDIS_INSTALLED = True
except ImportError:
    AIOREDIS_INSTALLED = False

logger = logging.getLogger(__name__)


# --- Configuration for Meta-Learning Orchestrator ---
class MetaLearningConfig(BaseSettings):
    """
    Configuration settings for the Meta-Learning Orchestrator.
    Now with full dynamic reloading (file watcher, Etcd support), secure key enforcement, and health checks.
    Settings are loaded from environment variables (prefixed with ML_) or .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="ML_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Security Mode
    SECURE_MODE: bool = Field(
        default=False, description="If true, enforce presence of security keys."
    )

    # Data Lake Configuration - Changed from DirectoryPath to str with validation
    DATA_LAKE_PATH: str = Field(
        default="data/data_lake.jsonl", description="Path for data lake file."
    )
    DATA_LAKE_S3_BUCKET: str = Field(
        default="my-meta-learning-data-bucket",
        description="S3 bucket name for data lake.",
    )
    DATA_LAKE_S3_PREFIX: str = Field(
        default="meta_learning/records/", description="S3 prefix for data lake objects."
    )
    USE_S3_DATA_LAKE: bool = Field(default=False, description="Enable S3 as data lake.")

    # Audit Log Configuration - Changed from FilePath to str with validation
    LOCAL_AUDIT_LOG_PATH: str = Field(
        default="data/meta_learning_audit.jsonl",
        description="Local file path for audit log.",
    )
    AUDIT_ENCRYPTION_KEY: Optional[str] = Field(
        default=None, description="Fernet encryption key for audit logs."
    )
    AUDIT_SIGNING_PRIVATE_KEY: Optional[str] = Field(
        default=None,
        description="ECDSA private key for signing audit logs (PEM format).",
    )
    AUDIT_SIGNING_PUBLIC_KEY: Optional[str] = Field(
        default=None,
        description="ECDSA public key for verifying audit logs (PEM format).",
    )
    AUDIT_LOG_ROTATION_SIZE_MB: int = Field(
        default=100, ge=1, description="Size in MB before audit log rotation."
    )
    AUDIT_LOG_MAX_FILES: int = Field(
        default=10, ge=1, description="Maximum number of rotated audit log files."
    )

    # Kafka Streaming Ingestion Configuration
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        default="localhost:9092", description="Comma-separated Kafka bootstrap servers."
    )
    KAFKA_TOPIC: str = Field(
        default="meta_learning_events",
        description="Kafka topic for meta-learning events.",
    )
    KAFKA_AUDIT_TOPIC: str = Field(
        default="meta_learning_audit_log", description="Kafka topic for audit logs."
    )
    USE_KAFKA_INGESTION: bool = Field(
        default=False, description="Enable Kafka for event ingestion."
    )
    USE_KAFKA_AUDIT: bool = Field(
        default=False, description="Enable Kafka for audit log ingestion."
    )

    # Redis for Distributed Lock
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for distributed lock.",
    )
    REDIS_LOCK_KEY: str = Field(
        default="meta_learning_orchestrator_leader_lock",
        description="Key for Redis distributed lock.",
    )
    REDIS_LOCK_TTL_SECONDS: int = Field(
        default=60, ge=10, description="TTL for Redis lock in seconds."
    )

    # Training and Deployment Parameters
    MIN_RECORDS_FOR_TRAINING: int = Field(
        default=500, ge=1, description="Minimum records required to trigger training."
    )
    TRAINING_CHECK_INTERVAL_SECONDS: int = Field(
        default=3600, ge=60, description="Interval to check for new training data."
    )
    DEPLOYMENT_CHECK_INTERVAL_SECONDS: int = Field(
        default=1800, ge=60, description="Interval to check for new models to deploy."
    )
    MODEL_BENCHMARK_THRESHOLD: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Performance threshold for model deployment.",
    )

    # Service Endpoints - Using str instead of HttpUrl for flexibility in testing
    ML_PLATFORM_ENDPOINT: str = Field(
        default="http://localhost:8081/ml-platform",
        description="Endpoint for the ML Platform service.",
    )
    AGENT_CONFIG_SERVICE_ENDPOINT: str = Field(
        default="http://localhost:8082/agent-config",
        description="Endpoint for Agent Config service.",
    )
    POLICY_ENGINE_ENDPOINT: str = Field(
        default="http://localhost:8083/policy-engine",
        description="Endpoint for Policy Engine service.",
    )

    # Deployment and Data Retention
    MAX_DEPLOYMENT_RETRIES: int = Field(
        default=5, ge=0, description="Maximum retries for model deployment."
    )
    DEPLOYMENT_RETRY_DELAY_SECONDS: int = Field(
        default=60, ge=1, description="Delay between deployment retries."
    )
    DATA_RETENTION_DAYS: int = Field(
        default=30, ge=1, description="Number of days to retain data."
    )

    # PII Redaction
    REDACT_PII_IN_LOGS: bool = Field(
        default=True, description="Enable PII redaction in logs."
    )

    # Dynamic Configuration Reloading
    CONFIG_RELOAD_INTERVAL_SECONDS: int = Field(
        default=0,
        ge=0,
        description="Interval to check for config reloads. 0 to disable.",
    )
    CONFIG_SOURCE: str = Field(
        default="env", description="Source for dynamic config ('env', 'file', 'etcd')."
    )
    CONFIG_FILE_PATH: Optional[str] = Field(
        default=None, description="Path to a config file if CONFIG_SOURCE is 'file'."
    )
    ETCD_HOST: Optional[str] = Field(
        default=None, description="Etcd host for dynamic configuration."
    )
    ETCD_PORT: Optional[int] = Field(
        default=2379, description="Etcd port for dynamic configuration."
    )
    ETCD_PREFIX: str = Field(
        default="/meta_learning/config/",
        description="Etcd key prefix for dynamic configuration.",
    )

    @field_validator(
        "DATA_LAKE_PATH", "LOCAL_AUDIT_LOG_PATH", "CONFIG_FILE_PATH", mode="before"
    )
    def validate_file_paths(cls, v: Optional[str]) -> Optional[str]:
        """Ensures that parent directories exist for file paths."""
        if v:
            try:
                parent_dir = os.path.dirname(v)
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                    logger.debug(f"Created parent directory: {parent_dir}")

                # Check if parent directory is writable
                if parent_dir and os.path.exists(parent_dir):
                    if not os.access(parent_dir, os.W_OK):
                        raise ValueError(f"Directory {parent_dir} is not writable")
            except OSError as e:
                raise ValueError(
                    f"Invalid file path or cannot create directory for {v}: {e}"
                )
        return v

    @field_validator(
        "AUDIT_ENCRYPTION_KEY", "AUDIT_SIGNING_PRIVATE_KEY", "AUDIT_SIGNING_PUBLIC_KEY"
    )
    def validate_security_keys(
        cls, v: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        """Ensures sensitive keys are set if SECURE_MODE is enabled."""
        field_name = info.field_name
        secure_mode = info.data.get("SECURE_MODE", False)

        if secure_mode and not v:
            raise ValueError(f"{field_name} must be set when SECURE_MODE is enabled.")
        if not v:
            logger.warning(
                f"{field_name} is not set. Related security features will be disabled."
            )
        return v

    @field_validator("KAFKA_BOOTSTRAP_SERVERS")
    def validate_kafka_brokers(cls, v: str, info: ValidationInfo) -> str:
        """Ensures Kafka bootstrap servers are valid if Kafka is enabled."""
        use_kafka_ingestion = info.data.get("USE_KAFKA_INGESTION", False)
        use_kafka_audit = info.data.get("USE_KAFKA_AUDIT", False)

        if use_kafka_ingestion or use_kafka_audit:
            if not v:
                raise ValueError(
                    "KAFKA_BOOTSTRAP_SERVERS must be set if Kafka is enabled."
                )
            # Basic validation of broker format
            brokers = v.split(",")
            for broker in brokers:
                broker = broker.strip()
                if ":" not in broker:
                    raise ValueError(
                        f"Invalid Kafka broker format: '{broker}'. Expected 'host:port'."
                    )
        return v

    @field_validator("REDIS_URL")
    def validate_redis_url(cls, v: str) -> str:
        """Validates Redis URL format."""
        if not v.startswith(("redis://", "rediss://", "http://", "https://")):
            raise ValueError("REDIS_URL must be a valid HTTP or Redis URL scheme")
        return v

    @field_validator(
        "ML_PLATFORM_ENDPOINT",
        "AGENT_CONFIG_SERVICE_ENDPOINT",
        "POLICY_ENGINE_ENDPOINT",
    )
    def validate_endpoints(cls, v: str, info: ValidationInfo) -> str:
        """Validates endpoint URLs."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"Endpoint {v} must use http or https scheme")
        return v

    @field_validator("DATA_RETENTION_DAYS")
    def validate_retention(cls, v: int, info: ValidationInfo) -> int:
        use_s3 = info.data.get("USE_S3_DATA_LAKE", False)
        if use_s3 and v > 365:
            logger.warning(
                "High retention days (>365) with S3 may incur significant costs. Consider S3 Lifecycle Policies."
            )
        return v

    def reload_config(self):
        """Reload configuration based on CONFIG_SOURCE."""
        if self.CONFIG_SOURCE == "env":
            logger.info("Environment source doesn't support dynamic reloading.")
        elif self.CONFIG_SOURCE == "file":
            self._reload_from_file()
        elif self.CONFIG_SOURCE == "etcd":
            logger.warning("Etcd configuration reloading is not yet implemented.")
        else:
            logger.warning(f"Unsupported CONFIG_SOURCE: {self.CONFIG_SOURCE}")

    def _reload_from_file(self):
        """Reload configuration from file."""
        if not self.CONFIG_FILE_PATH or not os.path.exists(self.CONFIG_FILE_PATH):
            logger.error(f"Config file not found: {self.CONFIG_FILE_PATH}")
            return

        try:
            with open(self.CONFIG_FILE_PATH, "r") as f:
                config_data = json.load(f)

            # Update current instance with new values
            for key, value in config_data.items():
                if key.startswith("ML_"):
                    attr_name = key[3:]  # Remove ML_ prefix
                    if hasattr(self, attr_name):
                        # Get the field info to properly convert the value
                        field_info = self.model_fields.get(attr_name)
                        if field_info:
                            # Convert string values to proper types
                            if field_info.annotation == bool:
                                converted_value = (
                                    value.lower() in ("true", "1", "yes")
                                    if isinstance(value, str)
                                    else bool(value)
                                )
                            elif field_info.annotation == int:
                                converted_value = int(value)
                            elif field_info.annotation == float:
                                converted_value = float(value)
                            else:
                                converted_value = value
                            setattr(self, attr_name, converted_value)
                        else:
                            setattr(self, attr_name, value)

            logger.info(f"Configuration reloaded from {self.CONFIG_FILE_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to reload config from file: Invalid JSON - {e}")
        except Exception as e:
            logger.error(f"Failed to reload config from file: {e}")

    async def start_watcher(self):
        """
        Starts a background task to watch for configuration changes from file or Etcd.
        """
        if self.CONFIG_RELOAD_INTERVAL_SECONDS <= 0:
            logger.info("Dynamic configuration reloading is disabled.")
            return

        loop = asyncio.get_event_loop()

        if self.CONFIG_SOURCE == "file" and self.CONFIG_FILE_PATH:
            if not WATCHDOG_INSTALLED:
                logger.error("'watchdog' is not installed. Cannot start file watcher.")
                return

            class ConfigFileEventHandler(FileSystemEventHandler):
                def __init__(self, config_instance):
                    self.config = config_instance

                def on_modified(self, event):
                    if event.src_path == self.config.CONFIG_FILE_PATH:
                        logger.info(f"Config file {event.src_path} modified.")
                        self.config._reload_from_file()

            observer = Observer()
            observer.schedule(
                ConfigFileEventHandler(self), os.path.dirname(self.CONFIG_FILE_PATH)
            )
            await loop.run_in_executor(None, observer.start)
            logger.info(f"Started file watcher for {self.CONFIG_FILE_PATH}")

        elif self.CONFIG_SOURCE == "etcd" and self.ETCD_HOST:
            if not ETCD3_INSTALLED:
                logger.error("'etcd3' is not installed. Cannot start Etcd watcher.")
                return

            def watch_callback(event):
                logger.info("Received Etcd configuration update.")
                self._load_from_etcd(etcd_client)

            try:
                etcd_client = etcd3.client(host=self.ETCD_HOST, port=self.ETCD_PORT)
                etcd_client.add_watch_prefix_callback(self.ETCD_PREFIX, watch_callback)
                logger.info(f"Started Etcd watcher for prefix {self.ETCD_PREFIX}")
            except Exception as e:
                logger.error(
                    f"Failed to initialize Etcd client or watcher: {e}", exc_info=True
                )

    def _load_from_etcd(self, client):
        """Helper to load config from Etcd."""
        try:
            logger.info(f"Fetching configuration from Etcd prefix: {self.ETCD_PREFIX}")
            configs = client.get_prefix(self.ETCD_PREFIX)
            for key, value in configs:
                key_str = key.decode() if isinstance(key, bytes) else key
                value_str = value.decode() if isinstance(value, bytes) else value
                attr_name = key_str.split("/")[-1].upper()
                if hasattr(self, attr_name):
                    setattr(self, attr_name, value_str)
            logger.info("Configuration loaded from Etcd")
        except Exception as e:
            logger.error(f"Failed to reload config from Etcd: {e}", exc_info=True)

    async def is_healthy(self) -> bool:
        """Checks if configured dependencies are accessible and healthy."""
        logger.info("Performing configuration health checks...")
        healthy = True
        if AIOREDIS_INSTALLED:
            try:
                redis = aioredis.from_url(self.REDIS_URL, decode_responses=True)
                await redis.ping()
                logger.info("✅ Redis connection successful.")
                await redis.close()
            except Exception as e:
                logger.error(f"❌ Redis health check failed: {e}")
                healthy = False

        if self.USE_S3_DATA_LAKE:
            if BOTO3_INSTALLED:
                try:
                    s3 = boto3.client("s3")
                    s3.head_bucket(Bucket=self.DATA_LAKE_S3_BUCKET)
                    logger.info(
                        f"✅ S3 bucket '{self.DATA_LAKE_S3_BUCKET}' accessible."
                    )
                except Exception as e:
                    logger.error(f"❌ S3 health check failed: {e}")
                    healthy = False
            else:
                logger.warning("⚠️ Boto3 not installed, cannot perform S3 health check.")

        return healthy


# Example usage (for testing/demonstration)
if __name__ == "__main__":
    from cryptography.fernet import Fernet

    logging.basicConfig(level=logging.INFO)

    os.environ["ML_DATA_LAKE_PATH"] = "/tmp/ml_data/data_lake.jsonl"
    os.environ["ML_USE_S3_DATA_LAKE"] = "false"
    os.environ["ML_KAFKA_BOOTSTRAP_SERVERS"] = "kafka1:9092,kafka2:9092"
    os.environ["ML_AUDIT_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["ML_REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["ML_ML_PLATFORM_ENDPOINT"] = "https://api.mlplatform.com/v1"

    try:
        config = MetaLearningConfig()
        logger.info("--- Configuration Loaded Successfully ---")
        logger.info(config.model_dump_json(indent=2))

        async def run_health_check():
            is_ok = await config.is_healthy()
            logger.info(f"--- Health Check Result: {'PASS' if is_ok else 'FAIL'} ---")

        asyncio.run(run_health_check())

    except ValidationError as e:
        logger.critical(f"Configuration validation failed: {e}")
    except Exception as e:
        logger.critical(
            f"An unexpected error occurred during setup: {e}", exc_info=True
        )
