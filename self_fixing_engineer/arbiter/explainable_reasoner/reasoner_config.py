import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    HttpUrl,
    ConfigDict,
    model_validator,
)
import structlog
import yaml

# Configure structlog for consistency with other modules
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
logger = structlog.get_logger(__name__)


# --- SensitiveValue Wrapper ---
class SensitiveValue:
    """A wrapper for sensitive values to prevent accidental logging."""

    def __init__(self, value: str):
        self._value = value

    def get_actual_value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "[REDACTED]"

    def __repr__(self) -> str:
        return self.__str__()


# --- Reasoner Configuration (Pydantic Model) ---
class ReasonerConfig(BaseModel):
    """Configuration for the Reasoner, loaded from defaults, files, or environment variables."""

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    model_name: str = "distilgpt2"
    device: Union[int, str] = "0"
    max_workers: int = 4
    timeout: int = 300
    max_generation_tokens: int = 1024
    temperature_explain: float = 0.7
    temperature_reason: float = 0.5
    temperature_neutral: float = 0.8
    temperature_negative: float = 0.9
    history_db: str = "sqlite:///data/reasoner_history.db"
    max_history_size: int = 1000
    strict_mode: bool = False
    mock_mode: bool = False
    log_prompts: bool = True
    model_cache_dir: str = str(Path.home() / ".cache" / "huggingface" / "hub")
    db_path: str = "sqlite:///data/db.sqlite"
    audit_ledger_url: Optional[HttpUrl] = "https://localhost:8080/audit"
    cloud_fallback_api_key: Optional[SensitiveValue] = None
    postgres_db_url: Optional[SensitiveValue] = None
    redis_url: Optional[SensitiveValue] = None
    audit_max_retries: int = 3
    cache_ttl: int = 3600
    sanitization_options: Dict[str, Any] = Field(
        default_factory=lambda: {
            "redact_keys": ["api_key", "password"],
            "redact_patterns": [r"\b\d{16}\b"],
            "max_nesting_depth": 10,
        }
    )
    model_configs: List[Dict[str, Any]] = Field(default_factory=lambda: [])
    model_cooldown_period: int = 300
    model_reload_retries: int = 5
    auth_enabled: bool = False
    audit_log_enabled: bool = True
    distributed_history_backend: str = "sqlite"

    @classmethod
    def from_env(cls):
        env_vars = {}
        for field_name, field in cls.model_fields.items():
            env_var_name = f"REASONER_{field_name.upper()}"
            if env_var_name in os.environ:
                value = os.environ[env_var_name]
                try:
                    if field.annotation in (Dict[str, Any], List[Dict[str, Any]]):
                        value = json.loads(value)
                    elif field.annotation == HttpUrl:
                        value = str(value)
                    elif field.annotation == Optional[SensitiveValue]:
                        value = SensitiveValue(value) if value else None
                    env_vars[field_name] = value
                except json.JSONDecodeError:
                    env_vars[field_name] = value
        return cls(**env_vars)

    @model_validator(mode="after")
    def validate_dependencies(self):
        """Validates critical fields based on other settings."""
        if self.distributed_history_backend == "postgres" and not (
            self.postgres_db_url and self.postgres_db_url.get_actual_value()
        ):
            raise ValueError(
                "Postgres URL is required when 'distributed_history_backend' is 'postgres'."
            )
        if self.audit_log_enabled and not self.audit_ledger_url:
            raise ValueError("Audit URL is required when 'audit_log_enabled' is True.")
        return self

    def get_public_config(self) -> Dict[str, Any]:
        """
        Returns a dictionary representation of the configuration,
        with sensitive values redacted for public exposure (e.g., API info).
        """
        public_config = self.model_dump(mode="json")
        # Redact sensitive values explicitly
        if self.cloud_fallback_api_key:
            public_config["cloud_fallback_api_key"] = str(self.cloud_fallback_api_key)
        if self.postgres_db_url:
            public_config["postgres_db_url"] = str(self.postgres_db_url)
        if self.redis_url:
            public_config["redis_url"] = str(self.redis_url)
        public_config["audit_ledger_url"] = str(self.audit_ledger_url)
        return public_config

    @classmethod
    def from_file(cls, file_path: Union[str, Path]) -> "ReasonerConfig":
        """
        Creates a ReasonerConfig instance from a JSON or YAML file.
        Note: This requires 'PyYAML' for YAML files.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found at: {file_path}")

        try:
            if file_path.suffix.lower() in [".yaml", ".yml"]:
                with open(file_path, "r", encoding="utf-8") as f:
                    config_dict = yaml.safe_load(f)
            elif file_path.suffix.lower() == ".json":
                with open(file_path, "r", encoding="utf-8") as f:
                    config_dict = json.load(f)
            else:
                raise ValueError(
                    f"Unsupported file format: {file_path.suffix}. Use .json or .yaml/.yml."
                )

            # Use model_validate to apply Pydantic's internal validation
            return cls.model_validate(config_dict)
        except ValidationError as e:
            logger.error(f"Failed to validate config from file {file_path}: {e}")
            raise
        except Exception as e:
            logger.critical(
                f"Failed to load config from file {file_path}: {e}", exc_info=True
            )
            raise
