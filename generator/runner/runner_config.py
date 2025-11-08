# runner/config.py
from pydantic import BaseModel, Field, SecretStr, validator, root_validator, model_validator, PydanticUserError
from dotenv import load_dotenv
import yaml
import os
import asyncio
import time
from typing import Dict, Any, Callable, Optional, List, Union
import multiprocessing
import json
from cryptography.fernet import Fernet
import logging
import sys  # Added for TESTING guard

# --- TESTING Guard ---
# Guard to prevent watchers from running during test collection/execution
TESTING = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)
if TESTING:
    logging.warning("TESTING environment detected. Watchers will be disabled.")

try:
    import hvac  # Hashicorp Vault client (add to reqs: hvac)
except ImportError:
    hvac = None

try:
    from deepdiff import DeepDiff  # For config diffing (add to reqs: deepdiff)
except ImportError:
    DeepDiff = None

try:
    import watchfiles  # For file watching (add to reqs: watchfiles)
except ImportError:
    watchfiles = None

try:
    import aiohttp # For remote config fetching (add to reqs: aiohttp)
except ImportError:
    aiohttp = None

load_dotenv()

logger = logging.getLogger(__name__)

# --- Configuration Schema Versioning ---
CURRENT_VERSION = 4  # Increment to reflect new features like secret pattern overrides

class RunnerConfig(BaseModel):
    """
    Configuration for the Runner system.

    This class defines the structure and validation for runner settings,
    with support for versioning, secrets management, context-aware defaults,
    commercial features, and enhanced extensibility.
    """
    version: int = Field(1, description="Config schema version for migration tracking. Must be <= CURRENT_VERSION.")
    
    # Core Workflow Settings
    backend: str = Field(..., description="Execution backend (e.g., 'docker', 'podman', 'kubernetes', 'lambda', 'ssh', 'nodejs', 'go', 'java').")
    framework: str = Field(..., description="Test framework (e.g., 'pytest', 'unittest', 'jest', 'go test', 'junit').")
    parallel_workers: int = Field(4, description="Number of parallel workers; suggested based on CPU cores for local execution.")
    timeout: int = Field(300, description="Execution timeout for a single test run in seconds.")
    mutation: bool = Field(False, description="Enable mutation testing for code quality assessment.")
    fuzz: bool = Field(False, description="Enable fuzz testing for robustness analysis.")
    doc_framework: str = Field('auto', description="Documentation generation framework ('auto', 'sphinx', 'mkdocs', 'javadoc', 'jsdoc'). 'auto' attempts detection.")

    # Distributed Execution Settings
    distributed: bool = Field(False, description="Enable distributed execution across multiple nodes.")
    dist_url: str = Field('', description="URL for distributed coordinator or remote config fetch endpoint.")
    
    # Custom Commands
    custom_setup: str = Field('', description="Custom command or script to run during environment setup within the backend.")
    
    # Resource Limits and Isolation
    resources: Dict[str, Any] = Field(default_factory=dict, description="Resource limits (e.g., {'cpu': 2, 'memory': '1g'}) applied to backend execution environments.")
    network: Dict[str, Any] = Field(default_factory=dict, description="Network controls (e.g., {'allow_internet': True}) for the execution environment.")
    security: Dict[str, Any] = Field(default_factory=dict, description="Security settings (e.g., {'user': 'nobody', 'capabilities_drop': ['SYS_ADMIN']}) for sandbox isolation.")
    
    # Secrets Management (Integration with external Vault/KMS)
    vault_url: Optional[str] = Field(None, description="Hashicorp Vault URL for centralized secret management.")
    vault_token: Optional[SecretStr] = Field(None, description="Vault access token (sensitive; loaded from environment or encrypted config).")
    api_key: Optional[SecretStr] = Field(None, description="General API key for external services (sensitive; loaded from environment or encrypted config).")
    
    # LLM provider secrets (populated from env or vault)
    llm_provider_api_key: Optional[SecretStr] = Field(
        None, description="API key for the default LLM provider (overridable per provider)."
    )
    
    # --- Commercial Configs ---
    commercial_mode_enabled: bool = Field(False, description="Enable commercial mode features (e.g., extended usage limits, premium support).")
    max_iterations_commercial: Optional[int] = Field(None, description="Maximum iterations for workflows in commercial mode. Set to `null` for unlimited iterations if commercial_mode_enabled is true.")
    billing_enabled: bool = Field(False, description="Enable billing tracking and enforcement based on usage thresholds.")
    usage_thresholds: Dict[str, int] = Field(
        default_factory=lambda: {'workflow_runs': 50, 'llm_tokens': 100000, 'mutation_tests': 100, 'fuzz_runs': 50},
        description="Free-tier or base-plan usage thresholds (e.g., {'workflow_runs': 50, 'llm_tokens': 100000}). Exceeding these may trigger alerts or billing."
    )
    cost_per_token: float = Field(0.00001, description="Cost per LLM token in base currency unit (e.g., USD), for billing calculation.")
    billing_period_days: int = Field(30, description="Billing cycle period in days, for resetting usage counts.")
    alert_threshold_percent: float = Field(0.8, description="Percentage (0.0-1.0) of usage threshold at which to send alerts before limits are hit.")
    
    # --- Observability and Monitoring ---
    instance_id: str = Field(..., description="Unique identifier for this runner instance, used in metrics and logs.")
    log_sinks: List[Dict[str, Any]] = Field(default_factory=lambda: [{'type': 'stream', 'config': {}}], description="List of logging destinations (e.g., file, stream, datadog, splunk_hec).")
    real_time_log_streaming: bool = Field(True, description="Enable non-blocking real-time log streaming to TUI/API.")
    metrics_interval_seconds: int = Field(1, description="Interval in seconds for updating real-time metrics.")
    alert_monitor_interval_seconds: int = Field(60, description="Interval in seconds for the alert monitoring system to check thresholds.")
    
    # --- Customization and Extensibility ---
    # Optional. Can include specific configurations for backends (e.g., AWS region, Lambda function name)
    # These would typically be nested dictionaries
    aws_region: Optional[str] = Field(None, description="AWS region for Lambda or CloudWatch backend.")
    lambda_function_name: Optional[str] = Field(None, description="AWS Lambda function name for serverless execution.")
    k8s: Dict[str, Any] = Field(default_factory=dict, description="Kubernetes specific configurations (e.g., 'namespace', 'service_account_name').")
    ssh: Dict[str, Any] = Field(default_factory=dict, description="SSH backend configurations (host, user, key_path, remote_work_dir).")
    libvirt_uri: Optional[str] = Field(None, description="Libvirt connection URI (e.g., 'qemu:///system').")
    vm_name: Optional[str] = Field(None, description="Virtual machine name for Libvirt or Firecracker backend.")

    # --- Security Enhancements ---
    custom_redaction_patterns: List[str] = Field(default_factory=list, description="List of custom regex patterns for PII/secret redaction. These are added to default patterns.")
    encryption_algorithm: str = Field('fernet', description="Symmetric encryption algorithm for logs/secrets ('fernet', 'aes').")
    encryption_key_env_var: Optional[str] = Field(None, description="Environment variable holding the base encryption key for logs/secrets.")
    log_signing_enabled: bool = Field(True, description="Enable cryptographic signing of log entries for integrity.")
    log_signing_algo: str = Field('hmac', description="Algorithm for log signing ('hmac', 'rsa', 'ecdsa').")
    log_signing_key_env_var: Optional[str] = Field(None, description="Environment variable for log signing key (HMAC) or private key PEM path (RSA/ECDSA).")

    @validator('version')
    def validate_version(cls, v):
        if v > CURRENT_VERSION:
            logger.warning(f"Config version {v} is newer than supported version {CURRENT_VERSION}. This might lead to unexpected behavior.")
        return v

    @validator('backend')
    def validate_backend(cls, v):
        allowed = ['docker', 'podman', 'firecracker', 'kubernetes', 'lambda', 'libvirt', 'ssh', 'nodejs', 'go', 'java']
        if v not in allowed:
            raise ValueError(f"Invalid backend: {v}. Allowed: {allowed}")
        return v

    @validator('framework')
    def validate_framework(cls, v):
        allowed = ['auto', 'pytest', 'unittest', 'nose2', 'behave', 'robot', 'jest', 'mocha', 'go test', 'junit', 'gradle', 'selenium']
        if v not in allowed:
            raise ValueError(f"Invalid framework: {v}. Allowed: {allowed}")
        return v
    
    @validator('doc_framework')
    def validate_doc_framework(cls, v):
        allowed = ['auto', 'sphinx', 'mkdocs', 'javadoc', 'jsdoc', 'go_doc']
        if v not in allowed:
            raise ValueError(f"Invalid doc_framework: {v}. Allowed: {allowed}")
        return v

    @validator('alert_threshold_percent')
    def validate_alert_threshold_percent(cls, v):
        if not (0.0 <= v <= 1.0):
            raise ValueError("alert_threshold_percent must be between 0.0 and 1.0.")
        return v

    @root_validator(pre=True)
    def _pull_secrets(cls, values: dict) -> dict:
        """Pull any LLM_*_API_KEY env vars into the model."""
        # This generic logic will capture OPENAI_API_KEY, CLAUDE_API_KEY, etc.
        # It also serves as the default for the explicit llm_provider_api_key field.
        for key, val in os.environ.items():
            if key.upper().endswith("_API_KEY") and "LLM" in key.upper():
                # We normalize to the snake_case of the env var for generic storage,
                # but only map to the explicit llm_provider_api_key field here
                # if the env var matches the desired structure for the default key.
                if key.upper() == "LLM_PROVIDER_API_KEY" or key.upper() == "RUNNER_LLM_PROVIDER_API_KEY":
                    values["llm_provider_api_key"] = SecretStr(val)
                # Note: We let other LLM keys pass through to be handled by other mechanisms 
                # or just available in the environment for provider-specific lookup.
        return values

    @model_validator(mode='after')
    def post_validate(self) -> 'RunnerConfig':
        # Cross-field validation and conditional requirements
        if self.distributed and not self.dist_url:
            raise PydanticUserError("dist_url required if distributed is True", code="dist_url_required")
        
        if self.commercial_mode_enabled and self.max_iterations_commercial is not None and self.max_iterations_commercial <= 0:
            raise PydanticUserError("max_iterations_commercial must be None (for unlimited) or a positive integer if commercial mode is enabled.", code="max_iterations_commercial_invalid")

        if self.billing_enabled:
            if not self.usage_thresholds or not isinstance(self.usage_thresholds, dict):
                raise PydanticUserError("usage_thresholds must be defined as a dictionary when billing_enabled is True.", code="usage_thresholds_required")
            if self.cost_per_token <= 0:
                raise PydanticUserError("cost_per_token must be a positive value when billing_enabled is True.", code="cost_per_token_invalid")
            if self.billing_period_days <= 0:
                raise PydanticUserError("billing_period_days must be a positive integer.", code="billing_period_days_invalid")
        
        # Backend-specific requirements check (simplified)
        backend = self.backend
        if backend == 'lambda' and not (self.aws_region and self.lambda_function_name):
            raise PydanticUserError("For 'lambda' backend, 'aws_region' and 'lambda_function_name' are required.", code="lambda_config_required")
        if backend == 'kubernetes' and not self.k8s:
            raise PydanticUserError("For 'kubernetes' backend, 'k8s' configuration is required.", code="k8s_config_required")
        if backend == 'ssh' and not self.ssh:
            raise PydanticUserError("For 'ssh' backend, 'ssh' configuration (host, user) is required.", code="ssh_config_required")
        if backend == 'libvirt' and not (self.libvirt_uri and self.vm_name):
            raise PydanticUserError("For 'libvirt' backend, 'libvirt_uri' and 'vm_name' are required.", code="libvirt_config_required")
        if backend == 'firecracker' and not self.vm_name:
            raise PydanticUserError("For 'firecracker' backend, 'vm_name' is required.", code="firecracker_config_required")

        return self

    def validator(self):
        """Legacy method alias for validation (calls Pydantic's)."""
        self.model_validate(self.model_dump()) # Ensure Pydantic v2 method name is used

    @classmethod
    def suggest(cls) -> Dict[str, Any]:
        """Context-aware suggestions based on hardware/environment."""
        suggestions = {}
        suggestions['parallel_workers'] = multiprocessing.cpu_count()
        total_mem_gb = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024 ** 3) if hasattr(os, 'sysconf') else 4
        suggestions['resources'] = {'cpu': suggestions['parallel_workers'], 'memory': f"{int(total_mem_gb / 2)}g"}
        suggestions['instance_id'] = os.getenv('HOSTNAME', 'default_runner_instance')
        
        logger.info(f"Config suggestions: {suggestions}")
        return suggestions

    def generate_docs(self, format: str = 'markdown') -> str:
        """Auto-generate config documentation based on Pydantic schema."""
        schema = self.model_json_schema()
        if format == 'yaml':
            clean_schema = json.loads(json.dumps(schema))
            return yaml.dump(clean_schema, default_flow_style=False, sort_keys=False)
        elif format == 'markdown':
            md = f"# RunnerConfig Schema (Version {CURRENT_VERSION})\n\n"
            md += "This document describes the configuration options for the Runner system, automatically generated from its Pydantic schema.\n\n"
            md += "## Overview\n\n"
            md += f"The current configuration schema version is `{CURRENT_VERSION}`. The `load_config` function supports automatic migration from older versions.\n\n"
            md += "## Fields\n\n"
            
            # Categorize fields for better readability and DX
            categories = {
                "Core Workflow Settings": ["version", "backend", "framework", "parallel_workers", "timeout", "mutation", "fuzz", "doc_framework"],
                "Distributed Execution Settings": ["distributed", "dist_url"],
                "Custom Commands & Setup": ["custom_setup"],
                "Resource & Security Isolation": ["resources", "network", "security"],
                "Secrets Management": ["vault_url", "vault_token", "api_key", "llm_provider_api_key"],
                "Commercial & Billing Features": ["commercial_mode_enabled", "max_iterations_commercial", "billing_enabled", "usage_thresholds", "cost_per_token", "billing_period_days", "alert_threshold_percent"],
                "Observability & Monitoring": ["instance_id", "log_sinks", "real_time_log_streaming", "metrics_interval_seconds", "alert_monitor_interval_seconds"],
                "Backend Specific Configurations": ["aws_region", "lambda_function_name", "k8s", "ssh", "libvirt_uri", "vm_name"],
                "Security Enhancements (Redaction/Encryption/Signing)": ["custom_redaction_patterns", "encryption_algorithm", "encryption_key_env_var", "log_signing_enabled", "log_signing_algo", "log_signing_key_env_var"]
            }

            for category, fields in categories.items():
                md += f"### {category}\n\n"
                for field in fields:
                    if field in schema['properties']:
                        info = schema['properties'][field]
                        field_type = info.get('type', 'any')
                        if 'anyOf' in info:
                            types = [t.get('type', 'any') for t in info['anyOf'] if 'type' in t]
                            field_type = " or ".join(types)
                        elif '$ref' in info:
                            ref_name = info['$ref'].split('/')[-1]
                            field_type = f"object (`{ref_name}`)"

                        default_value = info.get('default', 'N/A')
                        if default_value == {} or default_value == []: # Handle default_factory for empty dict/list
                            default_value = info.get('defaultFactory', 'N/A')
                        if isinstance(default_value, dict) or isinstance(default_value, list):
                            default_value = json.dumps(default_value) # Represent dicts/lists as JSON string

                        description = info.get('description', 'No description provided.')
                        
                        # Add notes for sensitive fields or fields loaded from env
                        extra_notes = []
                        if field in ["vault_token", "api_key", "llm_provider_api_key"]:
                            extra_notes.append("Sensitive: Should be loaded from environment variables or a secure vault. **Will be masked in logs.**")
                        if field in ["encryption_key_env_var", "log_signing_key_env_var"]:
                            extra_notes.append("Environment variable name for a secret key.")

                        md += f"- **`{field}`**: {description} (Type: `{field_type}`, Default: `{default_value}`)\n"
                        if extra_notes:
                            md += f"  *Notes*: {' '.join(extra_notes)}\n"
                        
                        # Add details for nested dicts (e.g., resources, network, security, k8s, ssh)
                        if "$ref" in info and info['$ref'].startswith('#/$defs/'):
                            nested_schema_name = info['$ref'].split('/')[-1]
                            if nested_schema_name in schema['$defs']:
                                md += f"  *Nested fields for `{field}` (object `{nested_schema_name}`):*\n"
                                for nested_field, nested_info in schema['$defs'][nested_schema_name]['properties'].items():
                                    nested_type = nested_info.get('type', 'any')
                                    nested_default = nested_info.get('default', 'N/A')
                                    nested_desc = nested_info.get('description', 'No description.')
                                    md += f"    - `{nested_field}`: {nested_desc} (Type: `{nested_type}`, Default: `{nested_default}`)\n"
                md += "\n"

            return md
        return json.dumps(schema, indent=2)

    def encrypt_secrets(self, key: bytes):
        """Encrypt secret fields using Fernet."""
        f = Fernet(key)
        
        # Helper to encrypt a SecretStr field
        def _encrypt_field(field_value):
            if field_value and isinstance(field_value, SecretStr):
                try:
                    encrypted_value = f.encrypt(field_value.get_secret_value().encode()).decode()
                    return SecretStr(encrypted_value)
                except Exception as e:
                    logger.error(f"Failed to encrypt secret field: {e}", exc_info=True)
                    return field_value # Return original value on failure
            return field_value
            
        self.api_key = _encrypt_field(self.api_key)
        self.vault_token = _encrypt_field(self.vault_token)
        self.llm_provider_api_key = _encrypt_field(self.llm_provider_api_key)
        logger.info("Secret fields encrypted successfully.")


    def decrypt_secrets(self, key: bytes):
        """Decrypt secret fields."""
        f = Fernet(key)
        
        # Helper to decrypt a SecretStr field
        def _decrypt_field(field_value, field_name):
            if field_value and isinstance(field_value, SecretStr):
                try:
                    decrypted_value = f.decrypt(field_value.get_secret_value().encode()).decode()
                    return SecretStr(decrypted_value)
                except Exception as e:
                    logger.error(f"Failed to decrypt secret field '{field_name}': {e}", exc_info=True)
                    return SecretStr("[DECRYPTION_FAILED]") # Mask if decryption fails
            return field_value
            
        self.api_key = _decrypt_field(self.api_key, 'api_key')
        self.vault_token = _decrypt_field(self.vault_token, 'vault_token')
        self.llm_provider_api_key = _decrypt_field(self.llm_provider_api_key, 'llm_provider_api_key')
        logger.info("Secret fields decrypted successfully.")


    async def fetch_vault_secrets(self):
        """Integrate with Hashicorp Vault for secrets."""
        if not self.vault_url or not hvac:
            if not hvac:
                logger.warning("hvac package not installed. Cannot fetch secrets from Vault.")
            return

        if not self.vault_token:
            logger.warning("Vault token not provided in config. Cannot authenticate with Vault.")
            return

        try:
            # Ensure the token value is retrieved correctly for authentication
            client = hvac.Client(url=self.vault_url, token=self.vault_token.get_secret_value())
            if client.is_authenticated():
                secrets_response = client.secrets.kv.v2.read_secret_version(path='runner/secrets')
                if secrets_response and 'data' in secrets_response and 'data' in secrets_response['data']:
                    secrets = secrets_response['data']['data']
                    if 'api_key' in secrets:
                        self.api_key = SecretStr(secrets['api_key']) # Store as SecretStr
                        logger.info("API key fetched from Vault.")
                    if 'llm_provider_api_key' in secrets:
                        self.llm_provider_api_key = SecretStr(secrets['llm_provider_api_key'])
                        logger.info("LLM provider API key fetched from Vault.")
                    logger.info("Vault secrets loaded successfully.")
                else:
                    logger.warning("No secrets found at 'runner/secrets' in Vault.")
            else:
                logger.error("Vault authentication failed. Check URL and token.")
        except Exception as e:
            logger.error(f"Error fetching secrets from Vault: {e}", exc_info=True)

def load_config(config_file: str, overrides: Optional[Dict[str, Any]] = None) -> RunnerConfig:
    """
    Load config from YAML, apply env overrides, handle versioning/migrations.
    Args:
        config_file (str): Path to the YAML configuration file.
        overrides (Optional[Dict[str, Any]]): Dictionary of settings to override.
    Returns:
        RunnerConfig: The validated and migrated RunnerConfig instance.
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML config file {config_file}: {e}")
        raise

    if overrides:
        data.update(overrides)
        logger.debug(f"Applied {len(overrides)} CLI/function overrides to config.")

    # Apply Environment Variable Overrides
    env_map = {
        'RUNNER_VERSION': 'version',
        'RUNNER_BACKEND': 'backend',
        'RUNNER_FRAMEWORK': 'framework',
        'RUNNER_PARALLEL_WORKERS': 'parallel_workers',
        'RUNNER_TIMEOUT': 'timeout',
        'RUNNER_MUTATION': 'mutation',
        'RUNNER_FUZZ': 'fuzz',
        'RUNNER_DOC_FRAMEWORK': 'doc_framework',
        'RUNNER_DISTRIBUTED': 'distributed',
        'RUNNER_DIST_URL': 'dist_url',
        'RUNNER_CUSTOM_SETUP': 'custom_setup',
        'RUNNER_VAULT_URL': 'vault_url',
        'RUNNER_VAULT_TOKEN': 'vault_token',
        'RUNNER_API_KEY': 'api_key',
        'RUNNER_LLM_PROVIDER_API_KEY': 'llm_provider_api_key', # Added
        'RUNNER_COMMERCIAL_MODE_ENABLED': 'commercial_mode_enabled',
        'RUNNER_MAX_ITERATIONS_COMMERCIAL': 'max_iterations_commercial',
        'RUNNER_BILLING_ENABLED': 'billing_enabled',
        'RUNNER_COST_PER_TOKEN': 'cost_per_token',
        'RUNNER_BILLING_PERIOD_DAYS': 'billing_period_days',
        'RUNNER_ALERT_THRESHOLD_PERCENT': 'alert_threshold_percent',
        'RUNNER_INSTANCE_ID': 'instance_id',
        'RUNNER_LOG_SINKS': 'log_sinks',
        'RUNNER_REAL_TIME_LOG_STREAMING': 'real_time_log_streaming',
        'RUNNER_METRICS_INTERVAL_SECONDS': 'metrics_interval_seconds',
        'RUNNER_ALERT_MONITOR_INTERVAL_SECONDS': 'alert_monitor_interval_seconds',
        'RUNNER_AWS_REGION': 'aws_region',
        'RUNNER_LAMBDA_FUNCTION_NAME': 'lambda_function_name',
        'RUNNER_LIBVIRT_URI': 'libvirt_uri',
        'RUNNER_VM_NAME': 'vm_name',
        'RUNNER_CUSTOM_REDACTION_PATTERNS': 'custom_redaction_patterns',
        'RUNNER_ENCRYPTION_ALGORITHM': 'encryption_algorithm',
        'RUNNER_ENCRYPTION_KEY_ENV_VAR': 'encryption_key_env_var',
        'RUNNER_LOG_SIGNING_ENABLED': 'log_signing_enabled',
        'RUNNER_LOG_SIGNING_ALGO': 'log_signing_algo',
        'RUNNER_LOG_SIGNING_KEY_ENV_VAR': 'log_signing_key_env_var',
    }
    for env_key, field_name in env_map.items():
        if env_val := os.getenv(env_key):
            try:
                field_type_info = RunnerConfig.model_fields[field_name].annotation
                
                # Handle Optional types by unwrapping
                if hasattr(field_type_info, '__origin__') and field_type_info.__origin__ is Union:
                    # Look for non-None type in Optional[T]
                    actual_type = next((arg for arg in field_type_info.__args__ if arg is not type(None)), str)
                else:
                    actual_type = field_type_info

                if actual_type is int: data[field_name] = int(env_val)
                elif actual_type is float: data[field_name] = float(env_val)
                elif actual_type is bool: data[field_name] = env_val.lower() == 'true'
                elif actual_type is SecretStr: data[field_name] = SecretStr(env_val)
                elif actual_type == List[str]: data[field_name] = json.loads(env_val)
                elif actual_type == List[Dict[str, Any]]: data[field_name] = json.loads(env_val)
                else: data[field_name] = env_val
                logger.debug(f"Environment variable override: {env_key}={env_val} applied to '{field_name}'.")
            except ValueError as e:
                logger.warning(f"Failed to cast env var '{env_key}' value '{env_val}' to type '{field_type_info}' for field '{field_name}': {e}. Skipping override.")
            except json.JSONDecodeError as e:
                 logger.warning(f"Failed to parse JSON from env var '{env_key}' value '{env_val}' for field '{field_name}': {e}. Skipping override.")

    # Perform schema migration if needed
    current_version_in_file = data.get('version', 1)
    if current_version_in_file < CURRENT_VERSION:
        logger.info(f"Migrating config from version {current_version_in_file} to {CURRENT_VERSION}.")
        migrations: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            1: lambda d: {
                **d,
                'resources': d.get('resources', {}),
                'network': d.get('network', {}),
                'security': d.get('security', {}),
                'version': 2
            },
            2: lambda d: {
                **d,
                'commercial_mode_enabled': d.get('commercial_mode_enabled', False),
                'max_iterations_commercial': d.get('max_iterations_commercial', None),
                'billing_enabled': d.get('billing_enabled', False),
                'usage_thresholds': d.get('usage_thresholds', {'workflow_runs': 50, 'llm_tokens': 100000}),
                'cost_per_token': d.get('cost_per_token', 0.00001),
                'billing_period_days': d.get('billing_period_days', 30),
                'alert_threshold_percent': d.get('alert_threshold_percent', 0.8),
                'version': 3
            },
            3: lambda d: {
                **d,
                'fuzz': d.get('fuzz', False),
                'doc_framework': d.get('doc_framework', 'auto'),
                'instance_id': d.get('instance_id', os.getenv('HOSTNAME', 'default_runner_instance')),
                'log_sinks': d.get('log_sinks', [{'type': 'stream', 'config': {}}]),
                'real_time_log_streaming': d.get('real_time_log_streaming', True),
                'metrics_interval_seconds': d.get('metrics_interval_seconds', 1),
                'alert_monitor_interval_seconds': d.get('alert_monitor_interval_seconds', 60),
                'aws_region': d.get('aws_region', None),
                'lambda_function_name': d.get('lambda_function_name', None),
                'k8s': d.get('k8s', {}),
                'ssh': d.get('ssh', {}),
                'libvirt_uri': d.get('libvirt_uri', None),
                'vm_name': d.get('vm_name', None),
                'custom_redaction_patterns': d.get('custom_redaction_patterns', []),
                'encryption_algorithm': d.get('encryption_algorithm', 'fernet'),
                'encryption_key_env_var': d.get('encryption_key_env_var', None),
                'log_signing_enabled': d.get('log_signing_enabled', True),
                'log_signing_algo': d.get('log_signing_algo', 'hmac'),
                'log_signing_key_env_var': d.get('log_signing_key_env_var', None),
                'version': 4
            },
        }
        # Apply migrations sequentially until current version is reached
        while data.get('version', 1) < CURRENT_VERSION:
            mig_func = migrations.get(data['version'])
            if mig_func:
                data = mig_func(data)
                logger.info(f"Migrated config to version {data['version']}.")
            else:
                logger.error(f"No migration function found for config version {data['version']}. Skipping remaining migrations. Config might be incomplete or invalid.")
                break # Break if a migration step is missing
        data['version'] = CURRENT_VERSION # Ensure final version is set
    elif current_version_in_file > CURRENT_VERSION:
        logger.warning(f"Config file version {current_version_in_file} is newer than runner's supported version {CURRENT_VERSION}. This might lead to unexpected behavior. Consider upgrading runner software.")
    
    config = RunnerConfig(**data)
    logger.info("Configuration loaded and validated successfully.")
    return config

class ConfigWatcher:
    """
    Live config reloader: Watches a local config file, validates, diffs, and calls a callback on change.
    Supports polling as a fallback if `watchfiles` is not installed.
    """
    def __init__(self, config_file: str, callback: Callable[[RunnerConfig, Optional[Dict[str, Any]]], None]):
        self.config_file = Path(config_file)
        self.callback = callback
        self.current_config: Optional[RunnerConfig] = None
        self.last_mtime = 0.0
        self.watch_task: Optional[asyncio.Task] = None
        logger.info(f"ConfigWatcher initialized for '{self.config_file}'.")

    async def start(self):
        """Starts the configuration watching process."""
        try:
            self.current_config = load_config(str(self.config_file))
            self.last_mtime = os.path.getmtime(self.config_file)
        except FileNotFoundError:
            logger.error(f"ConfigWatcher: File not found '{self.config_file}'. Watcher cannot start.")
            return
        except Exception as e:
            logger.error(f"ConfigWatcher: Error loading initial config '{self.config_file}': {e}. Watcher cannot start.")
            return

        if TESTING:
            logger.warning("TESTING environment detected. ConfigWatcher will load config once but will not start file watching.")
            return # Do not start any watch tasks
        
        logger.info("ConfigWatcher started.")
        if watchfiles:
            logger.info(f"Using 'watchfiles' for efficient file watching on '{self.config_file}'.")
            try:
                async for changes in watchfiles.awatch(self.config_file):
                    # `changes` is a set of (WatchMode, path) tuples
                    # WatchMode.added, .modified, .deleted
                    modified_files = {path for change_type, path in changes if change_type == watchfiles.Change.modified}
                    if str(self.config_file.resolve()) in modified_files: # Check if *our* config file was modified
                        logger.debug(f"Detected changes in config file: {changes}")
                        await self._reload()
                    else:
                        logger.debug(f"Detected non-config file changes: {changes}. Ignoring for config reload.")
            except Exception as e:
                logger.error(f"Error in 'watchfiles' watcher: {e}. Falling back to polling.", exc_info=True)
                await self._start_polling_fallback()
        else:
            logger.warning("'watchfiles' not installed. Falling back to polling for config changes.")
            await self._start_polling_fallback()

    async def _start_polling_fallback(self):
        """Starts a polling mechanism if watchfiles is not available or fails."""
        # Use a sensible default polling interval if current_config is not yet available
        polling_interval = self.current_config.metrics_interval_seconds * 5 if self.current_config else 5
        logger.info(f"Starting config polling every {polling_interval} seconds.")
        while True:
            await self._reload()
            await asyncio.sleep(polling_interval)

    async def _reload(self):
        """Internal method to perform the config reload, validation, and diffing."""
        try:
            mtime = os.path.getmtime(self.config_file)
            if mtime > self.last_mtime:
                logger.info(f"Config file '{self.config_file}' modified. Reloading...")
                new_config = load_config(str(self.config_file))
                
                diff = {}
                if DeepDiff and self.current_config:
                    try:
                        diff_result = DeepDiff(self.current_config.model_dump(), new_config.model_dump(), ignore_order=True, view='tree') # Use 'tree' view for better diff
                        if not diff_result:
                            logger.info("Config file changed but no significant differences detected after loading.")
                            self.last_mtime = mtime
                            return
                        diff = diff_result.to_json() # Convert DeepDiff object to JSON string for logging/passing
                    except Exception as diff_e:
                        logger.warning(f"Failed to compute DeepDiff for config: {diff_e}. Proceeding without diff details.", exc_info=True)
                        diff = {"error": str(diff_e)}
                else:
                    logger.warning("DeepDiff not installed. Cannot show config changes.")
                    diff = {"message": "DeepDiff not available, changes applied but not shown."}

                try:
                    # Run the synchronous, potentially blocking callback
                    # in a separate thread to not block the async loop.
                    await asyncio.to_thread(self.callback, new_config, diff)
                except Exception as e:
                    logger.error(f"Error executing config reload callback: {e}", exc_info=True)
                
                self.current_config = new_config
                self.last_mtime = mtime
                logger.info(f"Config reloaded successfully. Differences: {diff}")
            else:
                logger.debug("Config file not modified since last check.")
        except Exception as e:
            logger.error(f"Failed to reload config from '{self.config_file}': {e}", exc_info=True)

    async def fetch_remote(self, remote_url: Optional[str] = None):
        """
        Fetches configuration from a remote URL.
        Args:
            remote_url (Optional[str]): The URL to fetch the config from. If None, uses dist_url from current_config.
        """
        if not aiohttp:
            logger.error("aiohttp package not installed. Cannot fetch remote config.")
            return
            
        fetch_url = remote_url or (self.current_config.dist_url if self.current_config else None)
        if not fetch_url or not fetch_url.startswith('http'):
            logger.warning("No valid HTTP(s) remote URL configured for fetching.")
            return

        logger.info(f"Fetching config from remote URL: {fetch_url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(fetch_url) as resp:
                    resp.raise_for_status() # Raise exception for 4xx/5xx responses
                    data = yaml.safe_load(await resp.text())
                    new_config = RunnerConfig(**data)
                    
                    diff = {}
                    if DeepDiff and self.current_config:
                        try:
                            diff_result = DeepDiff(self.current_config.model_dump(), new_config.model_dump(), ignore_order=True, view='tree')
                            if not diff_result:
                                logger.info("Remote config fetched, but no significant differences detected.")
                                return
                            diff = diff_result.to_json()
                        except Exception as diff_e:
                            logger.warning(f"Failed to compute DeepDiff for remote config: {diff_e}. Proceeding without diff details.", exc_info=True)
                            diff = {"error": str(diff_e)}
                    else:
                        logger.warning("DeepDiff not installed. Cannot show remote config changes.")
                        diff = {"message": "DeepDiff not available, changes applied but not shown."}

                    try:
                        # Run the synchronous, potentially blocking callback
                        # in a separate thread to not block the async loop.
                        await asyncio.to_thread(self.callback, new_config, diff)
                    except Exception as e:
                        logger.error(f"Error executing remote config reload callback: {e}", exc_info=True)
                        
                    self.current_config = new_config
                    logger.info(f"Remote config fetched and applied. Differences: {diff}")
        except aiohttp.ClientError as e:
            logger.error(f"Failed to fetch remote config from {fetch_url}: Network error: {e}", exc_info=True)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse remote config from {fetch_url}: Invalid YAML: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error fetching remote config from {fetch_url}: {e}", exc_info=True)

# Example usage/tests would go in a separate file or in __main__ block
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create a dummy config.yaml for testing
    dummy_config_content = """
version: 1
backend: docker
framework: pytest
parallel_workers: 2
timeout: 600
distributed: false
dist_url: ""
commercial_mode_enabled: true
max_iterations_commercial: 100
billing_enabled: true
usage_thresholds:
  workflow_runs: 5
  llm_tokens: 1000
cost_per_token: 0.000015
billing_period_days: 7
alert_threshold_percent: 0.9
instance_id: test_instance
"""
    config_file_path = 'test_config_runner.yaml'
    with open(config_file_path, 'w') as f:
        f.write(dummy_config_content)

    print(f"Loading config from {config_file_path}")
    loaded_config = load_config(config_file_path)
    print(loaded_config.model_dump_json(indent=2))

    print("\n--- Generating Markdown Docs for Config ---")
    md_docs = loaded_config.generate_docs(format='markdown')
    print(md_docs)

    print("\n--- Generating YAML Docs for Config ---")
    yaml_docs = loaded_config.generate_docs(format='yaml')
    print(yaml_docs)

    # Clean up dummy config file
    os.remove(config_file_path)

    # Test migration from v1 to v4
    print("\n--- Testing config migration from V1 to V4 ---")
    v1_config_content_for_migration = """
version: 1
backend: docker
framework: pytest
parallel_workers: 1
timeout: 300
"""
    v1_config_file_path_for_migration = 'test_config_v1_migration.yaml'
    with open(v1_config_file_path_for_migration, 'w') as f:
        f.write(v1_config_content_for_migration)
    
    migrated_config = load_config(v1_config_file_path_for_migration)
    print(f"Migrated Config Version: {migrated_config.version}")
    print(migrated_config.model_dump_json(indent=2))
    assert migrated_config.version == CURRENT_VERSION
    assert 'resources' in migrated_config.model_dump()
    assert 'commercial_mode_enabled' in migrated_config.model_dump()
    assert 'fuzz' in migrated_config.model_dump() # Check for latest fields
    assert migrated_config.instance_id == os.getenv('HOSTNAME', 'default_runner_instance')

    os.remove(v1_config_file_path_for_migration)

    # Test environment variable override
    print("\n--- Testing Environment Variable Override ---")
    os.environ['RUNNER_TIMEOUT'] = '120'
    os.environ['RUNNER_BACKEND'] = 'podman'
    os.environ['RUNNER_COMMERCIAL_MODE_ENABLED'] = 'True'
    os.environ['RUNNER_LOG_SINKS'] = '[{"type": "file", "config": {"path": "/var/log/runner.log"}}]'
    os.environ['RUNNER_LLM_PROVIDER_API_KEY'] = 'sk-llm-12345'
    env_overridden_config = load_config(config_file_path='dummy_config.yaml', # Use a minimal config for env overrides
        overrides={
            'version': 4, # Ensure it's latest version for env override test
            'backend': 'docker', # This will be overridden by env
            'framework': 'pytest',
            'instance_id': 'env_test_instance'
        }
    )
    print(f"Env Overridden Timeout: {env_overridden_config.timeout}")
    print(f"Env Overridden Backend: {env_overridden_config.backend}")
    print(f"Env Overridden Commercial Mode: {env_overridden_config.commercial_mode_enabled}")
    print(f"Env Overridden Log Sinks: {env_overridden_config.log_sinks}")
    # llm_provider_api_key is SecretStr, so printing the object will mask the value by default, but we assert the internal value via the env loader
    print(f"Env Overridden LLM API Key: {env_overridden_config.llm_provider_api_key}") 
    assert env_overridden_config.timeout == 120
    assert env_overridden_config.backend == 'podman'
    assert env_overridden_config.commercial_mode_enabled is True
    assert env_overridden_config.log_sinks == [{"type": "file", "config": {"path": "/var/log/runner.log"}}]
    assert env_overridden_config.llm_provider_api_key.get_secret_value() == 'sk-llm-12345'
    
    del os.environ['RUNNER_TIMEOUT']
    del os.environ['RUNNER_BACKEND']
    del os.environ['RUNNER_COMMERCIAL_MODE_ENABLED']
    del os.environ['RUNNER_LOG_SINKS']
    del os.environ['RUNNER_LLM_PROVIDER_API_KEY']
    # Also clean up the API key that was pulled in by the _pull_secrets validator if it existed in the environment outside RUNNER_*
    if 'LLM_PROVIDER_API_KEY' in os.environ:
        del os.environ['LLM_PROVIDER_API_KEY']
    if 'OPENAI_API_KEY' in os.environ:
        del os.environ['OPENAI_API_KEY']


    # Create dummy_config.yaml if it doesn't exist for the above test to not fail
    if not Path('dummy_config.yaml').exists():
        Path('dummy_config.yaml').write_text("""
version: 4
backend: docker
framework: pytest
parallel_workers: 1
timeout: 300
instance_id: dummy_id
""")
    if Path('dummy_config.yaml').exists():
        os.remove('dummy_config.yaml')