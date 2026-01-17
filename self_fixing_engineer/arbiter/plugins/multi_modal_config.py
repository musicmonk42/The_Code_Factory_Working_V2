# D:\SFE\self_fixing_engineer\arbiter\plugins\multi_modal_config.py
import logging
import os
import re
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

# --- New Configuration Models ---


class CircuitBreakerConfig(BaseModel):
    """Configuration for the circuit breaker mechanism."""

    enabled: bool = Field(True, description="Enable/disable circuit breaker.")
    threshold: int = Field(5, description="Max consecutive failures before opening.")
    timeout_seconds: int = Field(
        300, description="Seconds before resetting to half-open."
    )
    modalities: List[str] = Field(
        default_factory=lambda: ["image", "audio", "video", "text"],
        description="List of modalities to apply circuit breaker to.",
    )


# --- Existing Configuration Models (Enhanced) ---


class ProcessorConfig(BaseModel):
    """Configuration for a specific modality's processing."""

    enabled: bool = Field(
        False, description="Enable or disable processing for this modality."
    )
    default_provider: str = Field(
        "default", description="The default provider to use for this modality."
    )
    provider_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="A dictionary of provider-specific configuration details.",
    )


class SecurityConfig(BaseModel):
    """Configuration for security and input/output validation."""

    sandbox_enabled: bool = Field(
        False,
        description="Enable a sandboxed environment for execution (if available).",
    )
    input_validation_rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="Rules for validating input data before processing (e.g., max size, max length).",
    )
    output_validation_rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="Rules for validating output from the models (e.g., min confidence, required fields).",
    )
    mask_pii_in_logs: bool = Field(
        True, description="Mask PII data in logs to enhance privacy."
    )
    compliance_frameworks: List[str] = Field(
        default_factory=lambda: ["NIST", "ISO27001"],
        description="List of compliance frameworks to which the plugin adheres.",
    )
    pii_patterns: Dict[str, str] = Field(
        default_factory=dict,
        description="A dictionary of custom regex patterns for PII.",
    )

    @field_validator("input_validation_rules", "output_validation_rules")
    @classmethod
    def validate_validation_rules(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that validation rules have expected keys and valid values."""
        for key, value in v.items():
            if key in ["max_size", "max_length"]:
                if not isinstance(value, int) or value <= 0:
                    raise ValueError(f"{key} must be a positive integer.")
            elif key == "min_confidence":
                if not isinstance(value, (int, float)) or not (0 <= value <= 1):
                    raise ValueError("min_confidence must be a number between 0 and 1.")
            # Add more specific rule checks as needed
        return v

    @field_validator("pii_patterns")
    @classmethod
    def validate_pii_patterns(cls, v: Dict[str, str]) -> Dict[str, str]:
        """Ensure PII patterns are valid regex."""
        for name, pattern in v.items():
            try:
                re.compile(pattern)
            except re.error:
                raise ValueError(f"Invalid regex pattern for '{name}': {pattern}")
        return v


class AuditLogConfig(BaseModel):
    """Configuration for the audit logging system."""

    enabled: bool = Field(True, description="Enable or disable audit logging.")
    log_level: str = Field("INFO", description="The minimum log level for auditing.")
    destination: str = Field(
        "console",
        description="The destination for audit logs ('console', 'file', or 'kafka').",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate that the log level is a valid Python logging level."""
        if logging.getLevelName(v.upper()) == f"Level {v.upper()}":
            raise ValueError(
                f"Invalid log level: '{v}'. Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
            )
        return v.upper()

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        """Validate that the log destination is one of the allowed options."""
        allowed_destinations = ["console", "file", "kafka"]
        if v not in allowed_destinations:
            raise ValueError(
                f"Invalid destination: '{v}'. Must be one of {allowed_destinations}."
            )
        return v


class MetricsConfig(BaseModel):
    """Configuration for the Prometheus metrics system."""

    enabled: bool = Field(
        True, description="Enable or disable Prometheus metric collection."
    )
    exporter_port: int = Field(
        9090, description="The port to expose Prometheus metrics on."
    )


class CacheConfig(BaseModel):
    """Configuration for the caching mechanism."""

    enabled: bool = Field(True, description="Enable or disable the caching feature.")
    type: str = Field("redis", description="The type of cache to use ('redis').")
    host: str = Field("localhost", description="The host for the cache server.")
    port: int = Field(6379, description="The port for the cache server.")
    ttl_seconds: int = Field(
        3600, description="The time-to-live for cache entries in seconds."
    )


class ComplianceConfig(BaseModel):
    """Configuration for compliance mapping and validation."""

    mapping: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="A mapping of modalities to a list of compliance controls.",
    )

    @field_validator("mapping")
    @classmethod
    def validate_compliance_mapping(
        cls, v: Dict[str, List[str]]
    ) -> Dict[str, List[str]]:
        """Ensure all compliance control IDs in the mapping are valid."""
        # This regex pattern is a placeholder; it should be refined based on actual control IDs.
        control_id_pattern = re.compile(r"^(NIST|ISO27001)-[A-Za-z0-9.-]+$")
        for modality, controls in v.items():
            for control_id in controls:
                if not control_id_pattern.match(control_id):
                    raise ValueError(
                        f"Invalid compliance control ID for modality '{modality}': '{control_id}'."
                    )
        return v


class MultiModalConfig(BaseModel):
    """
    Main configuration model for the MultiModal plugin.
    This model composes all the other configuration classes into a single,
    hierarchical, and self-documenting structure.

    Supported Environment Variables:
    - MULTI_MODAL_IMAGE_PROCESSING_ENABLED: bool
    - MULTI_MODAL_AUDIO_PROCESSING_ENABLED: bool
    - MULTI_MODAL_VIDEO_PROCESSING_ENABLED: bool
    - MULTI_MODAL_TEXT_PROCESSING_ENABLED: bool
    - MULTI_MODAL_SECURITY_MASK_PII: bool
    - MULTI_MODAL_SECURITY_SANDBOX_ENABLED: bool
    - MULTI_MODAL_AUDIT_LOG_CONFIG_ENABLED: bool
    - MULTI_MODAL_AUDIT_LOG_CONFIG_LOG_LEVEL: str
    - MULTI_MODAL_AUDIT_LOG_CONFIG_DESTINATION: str
    - MULTI_MODAL_METRICS_CONFIG_ENABLED: bool
    - MULTI_MODAL_METRICS_CONFIG_EXPORTER_PORT: int
    - MULTI_MODAL_CACHE_CONFIG_ENABLED: bool
    - MULTI_MODAL_CACHE_CONFIG_TYPE: str
    - MULTI_MODAL_CACHE_CONFIG_HOST: str
    - MULTI_MODAL_CACHE_CONFIG_PORT: int
    - MULTI_MODAL_CACHE_CONFIG_TTL_SECONDS: int
    - MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_ENABLED: bool
    - MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_THRESHOLD: int
    - MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_TIMEOUT_SECONDS: int
    - MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_MODALITIES: str (comma-separated list)
    """

    image_processing: ProcessorConfig = Field(
        default_factory=ProcessorConfig,
        description="Configuration for image processing.",
    )
    audio_processing: ProcessorConfig = Field(
        default_factory=ProcessorConfig,
        description="Configuration for audio processing.",
    )
    video_processing: ProcessorConfig = Field(
        default_factory=ProcessorConfig,
        description="Configuration for video processing.",
    )
    text_processing: ProcessorConfig = Field(
        default_factory=ProcessorConfig,
        description="Configuration for text processing.",
    )
    security_config: SecurityConfig = Field(
        default_factory=SecurityConfig,
        description="Global security and validation configuration.",
    )
    audit_log_config: AuditLogConfig = Field(
        default_factory=AuditLogConfig,
        description="Global audit logging configuration.",
    )
    metrics_config: MetricsConfig = Field(
        default_factory=MetricsConfig, description="Global metrics configuration."
    )
    cache_config: CacheConfig = Field(
        default_factory=CacheConfig, description="Global caching configuration."
    )
    compliance_config: ComplianceConfig = Field(
        default_factory=ComplianceConfig,
        description="Global compliance mapping configuration.",
    )
    circuit_breaker_config: CircuitBreakerConfig = Field(
        default_factory=CircuitBreakerConfig,
        description="Global circuit breaker configuration.",
    )
    user_id_for_auditing: str = Field(
        "system_user",
        description="The user ID to use for audit logs when one is not provided.",
    )
    current_model_version: Dict[str, str] = Field(
        default_factory=dict,
        description="A dictionary mapping modalities to their current active model versions.",
    )

    @classmethod
    def load_config(cls, config_file: str = None) -> "MultiModalConfig":
        """
        Loads configuration from a YAML file and overrides with environment variables.

        Args:
            config_file: Optional path to a YAML configuration file.

        Returns:
            A validated MultiModalConfig instance.
        """
        config_data = {}
        if config_file and os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    config_data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                logging.error(
                    f"Error loading YAML configuration file {config_file}: {e}"
                )
                raise

        # Map environment variables to a dictionary structure
        env_vars = {
            "MULTI_MODAL_IMAGE_PROCESSING_ENABLED": (
                "image_processing",
                "enabled",
                bool,
            ),
            "MULTI_MODAL_AUDIO_PROCESSING_ENABLED": (
                "audio_processing",
                "enabled",
                bool,
            ),
            "MULTI_MODAL_VIDEO_PROCESSING_ENABLED": (
                "video_processing",
                "enabled",
                bool,
            ),
            "MULTI_MODAL_TEXT_PROCESSING_ENABLED": ("text_processing", "enabled", bool),
            "MULTI_MODAL_SECURITY_MASK_PII": (
                "security_config",
                "mask_pii_in_logs",
                bool,
            ),
            "MULTI_MODAL_SECURITY_SANDBOX_ENABLED": (
                "security_config",
                "sandbox_enabled",
                bool,
            ),
            "MULTI_MODAL_AUDIT_LOG_CONFIG_ENABLED": (
                "audit_log_config",
                "enabled",
                bool,
            ),
            "MULTI_MODAL_AUDIT_LOG_CONFIG_LOG_LEVEL": (
                "audit_log_config",
                "log_level",
                str,
            ),
            "MULTI_MODAL_AUDIT_LOG_CONFIG_DESTINATION": (
                "audit_log_config",
                "destination",
                str,
            ),
            "MULTI_MODAL_METRICS_CONFIG_ENABLED": ("metrics_config", "enabled", bool),
            "MULTI_MODAL_METRICS_CONFIG_EXPORTER_PORT": (
                "metrics_config",
                "exporter_port",
                int,
            ),
            "MULTI_MODAL_CACHE_CONFIG_ENABLED": ("cache_config", "enabled", bool),
            "MULTI_MODAL_CACHE_CONFIG_TYPE": ("cache_config", "type", str),
            "MULTI_MODAL_CACHE_CONFIG_HOST": ("cache_config", "host", str),
            "MULTI_MODAL_CACHE_CONFIG_PORT": ("cache_config", "port", int),
            "MULTI_MODAL_CACHE_CONFIG_TTL_SECONDS": (
                "cache_config",
                "ttl_seconds",
                int,
            ),
            "MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_ENABLED": (
                "circuit_breaker_config",
                "enabled",
                bool,
            ),
            "MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_THRESHOLD": (
                "circuit_breaker_config",
                "threshold",
                int,
            ),
            "MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_TIMEOUT_SECONDS": (
                "circuit_breaker_config",
                "timeout_seconds",
                int,
            ),
        }

        # Handle special case for modalities list
        modalities_env = os.getenv("MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_MODALITIES")
        if modalities_env:
            if "circuit_breaker_config" not in config_data:
                config_data["circuit_breaker_config"] = {}
            config_data["circuit_breaker_config"]["modalities"] = [
                m.strip() for m in modalities_env.split(",")
            ]

        for env_var, (section, key, var_type) in env_vars.items():
            value = os.getenv(env_var)
            if value is not None:
                if section not in config_data:
                    config_data[section] = {}
                try:
                    if var_type == bool:
                        # Handle boolean conversion properly
                        config_data[section][key] = value.lower() in (
                            "true",
                            "1",
                            "yes",
                            "on",
                        )
                    else:
                        config_data[section][key] = var_type(value)
                except ValueError:
                    logging.warning(
                        f"Could not convert environment variable {env_var} value '{value}' to type {var_type.__name__}. Skipping."
                    )

        try:
            return cls.parse_obj(config_data)
        except ValidationError as e:
            logging.error(f"Configuration validation failed: {e}")
            raise
