"""
Configuration management for the server application.

This module provides centralized configuration management following industry
best practices including:
- 12-factor app principles (environment-based configuration)
- Type-safe configuration with Pydantic validation
- Secrets management with proper masking
- Environment variable loading with .env support
- Clear error messages for missing required configuration
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LLMProviderConfig(BaseSettings):
    """Configuration for LLM providers."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # OpenAI Configuration
    openai_api_key: Optional[SecretStr] = Field(
        default=None,
        description="OpenAI API key for GPT models"
    )
    openai_model: str = Field(
        default="gpt-4",
        description="Default OpenAI model to use"
    )
    openai_base_url: Optional[str] = Field(
        default=None,
        description="Custom OpenAI API base URL (for Azure OpenAI, etc.)"
    )
    
    # xAI Grok Configuration (supports both XAI_API_KEY and GROK_API_KEY)
    xai_api_key: Optional[SecretStr] = Field(
        default=None,
        description="xAI API key (alternative to grok_api_key)"
    )
    grok_api_key: Optional[SecretStr] = Field(
        default=None,
        description="xAI Grok API key"
    )
    grok_model: str = Field(
        default="grok-beta",
        description="Default Grok model to use"
    )
    
    # Ollama Configuration (local LLM)
    ollama_host: Optional[str] = Field(
        default=None,
        description="Ollama host URL (e.g., http://localhost:11434)"
    )
    ollama_model: str = Field(
        default="codellama",
        description="Default Ollama model to use"
    )
    
    # Anthropic Claude Configuration
    anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        description="Anthropic API key for Claude models"
    )
    anthropic_model: str = Field(
        default="claude-3-sonnet-20240229",
        description="Default Claude model to use"
    )
    
    # Google Gemini Configuration
    google_api_key: Optional[SecretStr] = Field(
        default=None,
        description="Google API key for Gemini models"
    )
    google_model: str = Field(
        default="gemini-pro",
        description="Default Gemini model to use"
    )
    
    # Default Provider Configuration
    default_llm_provider: str = Field(
        default="openai",
        description="Default LLM provider to use (openai, grok, anthropic, google, ollama)"
    )
    
    # LLM Request Configuration
    llm_timeout: int = Field(
        default=300,
        description="Timeout for LLM API requests in seconds",
        gt=0
    )
    llm_max_retries: int = Field(
        default=3,
        description="Maximum number of retries for LLM API requests",
        ge=0
    )
    llm_temperature: float = Field(
        default=0.7,
        description="Temperature for LLM generation",
        ge=0.0,
        le=2.0
    )
    
    # Feature Flags
    enable_ensemble_mode: bool = Field(
        default=False,
        description="Enable ensemble mode for LLM calls"
    )
    enable_llm_caching: bool = Field(
        default=True,
        description="Enable caching of LLM responses"
    )
    
    @field_validator("default_llm_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Validate that the provider is one of the supported options."""
        valid_providers = {"openai", "grok", "anthropic", "google", "ollama"}
        if v not in valid_providers:
            raise ValueError(
                f"Invalid LLM provider: {v}. Must be one of {valid_providers}"
            )
        return v
    
    def get_provider_api_key(self, provider: Optional[str] = None) -> Optional[str]:
        """
        Get the API key for the specified provider.
        
        Args:
            provider: Provider name (openai, grok, anthropic, google, ollama).
                     If None, uses default_llm_provider.
        
        Returns:
            API key as string, or None if not configured
        """
        provider = provider or self.default_llm_provider
        
        def _sanitize_api_key(key: Optional[str]) -> Optional[str]:
            """Sanitize API key by removing wrapping quotes and whitespace from Railway env vars."""
            if not key:
                return None
            sanitized = key.strip()
            # Remove wrapping quotes only (preserves quotes in the middle of values)
            if len(sanitized) >= 2:
                if (sanitized.startswith('"') and sanitized.endswith('"')) or \
                   (sanitized.startswith("'") and sanitized.endswith("'")):
                    sanitized = sanitized[1:-1]
            return sanitized if sanitized else None
        
        # Special handling for xAI/Grok - check both xai_api_key and grok_api_key
        if provider == "grok":
            # Prefer XAI_API_KEY, fallback to GROK_API_KEY
            xai_key = self.xai_api_key or self.grok_api_key
            if xai_key:
                return _sanitize_api_key(xai_key.get_secret_value())
            return None
        
        # Ollama doesn't use API keys
        if provider == "ollama":
            return None
        
        key_mapping = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "google": self.google_api_key,
        }
        
        secret_str = key_mapping.get(provider)
        if secret_str:
            return _sanitize_api_key(secret_str.get_secret_value())
        return None
    
    def get_provider_model(self, provider: Optional[str] = None) -> str:
        """
        Get the model name for the specified provider.
        
        Args:
            provider: Provider name. If None, uses default_llm_provider.
        
        Returns:
            Model name string
        """
        provider = provider or self.default_llm_provider
        
        model_mapping = {
            "openai": self.openai_model,
            "grok": self.grok_model,
            "anthropic": self.anthropic_model,
            "google": self.google_model,
            "ollama": self.ollama_model,
        }
        
        return model_mapping.get(provider, "gpt-4")
    
    def is_provider_configured(self, provider: Optional[str] = None) -> bool:
        """
        Check if a provider is properly configured with an API key or host.
        
        Args:
            provider: Provider name. If None, uses default_llm_provider.
        
        Returns:
            True if the provider has an API key configured or is Ollama with host
        """
        provider = provider or self.default_llm_provider
        
        # Special case for Ollama - check for host instead of API key
        if provider == "ollama":
            return self.ollama_host is not None
        
        # For other providers, check API key
        return self.get_provider_api_key(provider) is not None
    
    def get_available_providers(self) -> List[str]:
        """
        Get list of providers that have API keys configured.
        
        Returns:
            List of provider names that are configured
        """
        providers = []
        for provider in ["openai", "grok", "anthropic", "google", "ollama"]:
            if self.is_provider_configured(provider):
                providers.append(provider)
        return providers


class AgentConfig(BaseSettings):
    """Configuration for generator agents."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Agent Availability
    enable_codegen_agent: bool = Field(
        default=True,
        description="Enable code generation agent"
    )
    enable_testgen_agent: bool = Field(
        default=True,
        description="Enable test generation agent"
    )
    enable_deploy_agent: bool = Field(
        default=True,
        description="Enable deployment configuration agent"
    )
    enable_docgen_agent: bool = Field(
        default=True,
        description="Enable documentation generation agent"
    )
    enable_critique_agent: bool = Field(
        default=True,
        description="Enable critique/security scanning agent"
    )
    enable_clarifier: bool = Field(
        default=True,
        description="Enable requirements clarification"
    )
    
    # Agent Behavior
    strict_mode: bool = Field(
        default=False,
        description="Fail fast if agents cannot be imported (production mode)"
    )
    use_llm_clarifier: bool = Field(
        default=True,
        description="Use LLM-based clarifier instead of rule-based"
    )
    
    # Docker Validation Configuration
    docker_required: bool = Field(
        default=False,
        description="Require Docker for deployment validation. If False, Docker validation is skipped when Docker is unavailable."
    )
    
    # Storage Configuration
    upload_dir: Path = Field(
        default=Path("./uploads"),
        description="Directory for storing uploaded files and generated code"
    )
    
    @field_validator("upload_dir", mode="after")
    @classmethod
    def create_upload_dir(cls, v: Path) -> Path:
        """Ensure upload directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v


class ServerConfig(BaseSettings):
    """Main server configuration."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Application Settings
    app_env: str = Field(
        default="development",
        description="Application environment (development, staging, production)"
    )
    debug: bool = Field(
        default=True,
        description="Enable debug mode"
    )
    
    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    log_format: str = Field(
        default="json",
        description="Log format (json, text)"
    )
    
    # Redis Configuration
    redis_url: str = Field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"),
        description="Redis connection URL"
    )
    
    # Kafka Configuration
    kafka_enabled: bool = Field(
        default=False,
        description="Enable Kafka message bus (set to false for local-only operation)"
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka bootstrap servers (comma-separated list)"
    )
    kafka_max_retries: int = Field(
        default=3,
        description="Maximum number of Kafka connection retry attempts",
        ge=0,
        le=10
    )
    kafka_retry_backoff_ms: int = Field(
        default=1000,
        description="Base backoff time in milliseconds for Kafka retry attempts",
        ge=100,
        le=30000
    )
    kafka_connection_timeout_ms: int = Field(
        default=5000,
        description="Kafka connection timeout in milliseconds",
        ge=1000,
        le=60000
    )
    
    @field_validator("app_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        """Validate environment name."""
        valid_envs = {"development", "staging", "production"}
        if v not in valid_envs:
            logger.warning(
                f"Unknown app_env '{v}', expected one of {valid_envs}. "
                f"Defaulting to 'development'."
            )
            return "development"
        return v
    
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            logger.warning(
                f"Invalid log_level '{v}', expected one of {valid_levels}. "
                f"Defaulting to 'INFO'."
            )
            return "INFO"
        return v_upper
    
    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.app_env == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "development"


@lru_cache()
def get_llm_config() -> LLMProviderConfig:
    """
    Get the LLM provider configuration (cached).
    
    Returns:
        LLMProviderConfig instance
    """
    return LLMProviderConfig()


@lru_cache()
def get_agent_config() -> AgentConfig:
    """
    Get the agent configuration (cached).
    
    Returns:
        AgentConfig instance
    """
    return AgentConfig()


@lru_cache()
def get_server_config() -> ServerConfig:
    """
    Get the server configuration (cached).
    
    Returns:
        ServerConfig instance
    """
    return ServerConfig()


def detect_available_llm_provider() -> Optional[str]:
    """
    Auto-detect which LLM provider is available based on environment variables.
    
    Checks for API keys in this priority order:
    1. OPENAI_API_KEY → use OpenAI
    2. ANTHROPIC_API_KEY → use Anthropic/Claude
    3. XAI_API_KEY → use xAI/Grok
    4. GOOGLE_API_KEY → use Google/Gemini
    5. OLLAMA_HOST → use Ollama (local)
    
    Returns:
        Provider name (openai, anthropic, grok, google, ollama) or None if none found
    """
    # Check environment variables in priority order
    if os.getenv("OPENAI_API_KEY"):
        logger.info("Auto-detected OpenAI provider from OPENAI_API_KEY")
        return "openai"
    
    if os.getenv("ANTHROPIC_API_KEY"):
        logger.info("Auto-detected Anthropic provider from ANTHROPIC_API_KEY")
        return "anthropic"
    
    # xAI Grok can use either XAI_API_KEY or GROK_API_KEY
    if os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY"):
        logger.info("Auto-detected xAI/Grok provider from XAI_API_KEY or GROK_API_KEY")
        return "grok"
    
    if os.getenv("GOOGLE_API_KEY"):
        logger.info("Auto-detected Google/Gemini provider from GOOGLE_API_KEY")
        return "google"
    
    if os.getenv("OLLAMA_HOST"):
        logger.info("Auto-detected Ollama provider from OLLAMA_HOST")
        return "ollama"
    
    logger.warning("No LLM provider API keys found in environment variables")
    return None


def get_default_model_for_provider(provider: str) -> str:
    """
    Get the default model name for a given provider.
    
    Args:
        provider: Provider name (openai, anthropic, grok, google, ollama)
    
    Returns:
        Default model name for the provider
    """
    model_defaults = {
        "openai": "gpt-4-turbo",
        "anthropic": "claude-3-sonnet-20240229",
        "grok": "grok-beta",
        "google": "gemini-pro",
        "ollama": "codellama",
    }
    return model_defaults.get(provider, "gpt-4")


def validate_configuration() -> Dict[str, Any]:
    """
    Validate the overall configuration and return status.
    
    Returns:
        Dictionary with validation results including:
        - valid: bool indicating if configuration is valid
        - warnings: list of warning messages
        - errors: list of error messages
        - available_providers: list of configured LLM providers
    """
    results = {
        "valid": True,
        "warnings": [],
        "errors": [],
        "available_providers": [],
    }
    
    try:
        # Load configurations
        llm_config = get_llm_config()
        agent_config = get_agent_config()
        
        # Check LLM providers
        available_providers = llm_config.get_available_providers()
        results["available_providers"] = available_providers
        
        if not available_providers:
            results["warnings"].append(
                "No LLM providers configured. Agents will use fallback/mock behavior. "
                "Set API keys in .env file (OPENAI_API_KEY, GROK_API_KEY, etc.)"
            )
        else:
            logger.info(f"Available LLM providers: {', '.join(available_providers)}")
        
        # Check default provider
        if not llm_config.is_provider_configured(llm_config.default_llm_provider):
            results["warnings"].append(
                f"Default LLM provider '{llm_config.default_llm_provider}' is not configured. "
                f"Available providers: {available_providers or 'none'}"
            )
        
        # Check agent configuration
        if agent_config.strict_mode and not available_providers:
            results["errors"].append(
                "STRICT_MODE is enabled but no LLM providers are configured. "
                "Either configure an LLM provider or disable strict mode."
            )
            results["valid"] = False
        
        # Check upload directory
        if not agent_config.upload_dir.exists():
            results["warnings"].append(
                f"Upload directory '{agent_config.upload_dir}' does not exist. "
                "It will be created on first use."
            )
        
        # Log results
        if results["valid"]:
            logger.info("Configuration validation passed")
            if results["warnings"]:
                for warning in results["warnings"]:
                    logger.warning(warning)
        else:
            logger.error("Configuration validation failed")
            for error in results["errors"]:
                logger.error(error)
        
    except Exception as e:
        results["valid"] = False
        results["errors"].append(f"Configuration validation error: {e}")
        logger.exception("Failed to validate configuration")
    
    return results


def setup_logging(config: Optional[ServerConfig] = None) -> None:
    """
    Set up logging based on configuration.
    
    Args:
        config: Server configuration. If None, loads from get_server_config()
    """
    if config is None:
        config = get_server_config()
    
    # Set log level
    log_level = getattr(logging, config.log_level, logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        if config.log_format == "text"
        else "%(message)s",  # JSON formatting would be done by a handler
    )
    
    logger.info(
        f"Logging configured: level={config.log_level}, format={config.log_format}"
    )
