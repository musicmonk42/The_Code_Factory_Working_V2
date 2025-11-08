# plugins/dlt_network_config_manager.py
"""
A module for managing DLT (Distributed Ledger Technology) network configurations.
This includes connection profiles, node URLs, smart contract addresses, ABI paths,
and secure handling of private keys.

Features:
-   Centralized Configuration: Load DLT network settings from a single source.
-   Secure Credential Handling: Integrates with environment variables (or a secret manager) for sensitive data.
-   Environment Switching: Easily manage configurations for different environments (dev, staging, prod).
-   Validation: Strict validation of configuration parameters (Pydantic v2).
-   Extensibility: Designed to support various DLT types (Fabric, EVM, Corda, etc.) and off-chain storage.
-   Runtime Refresh: Provides a mechanism to reload configurations at runtime (hash-based change detection).
-   Metrics: Optional Prometheus metrics with safe idempotent registration.
"""

from __future__ import annotations

import os
import sys
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
import asyncio
import hashlib
import threading
import time

# Pydantic v2 imports
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    PrivateAttr,
    AnyHttpUrl,
    field_validator,
    model_validator,
    ConfigDict,
)

# --- Optional Dependencies for Production Readiness ---
try:
    import boto3
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    ClientError = None
    logging.getLogger(__name__).warning("Boto3 not available. AWS Secrets Manager integration will be disabled.")

try:
    from prometheus_client import Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logging.getLogger(__name__).warning("Prometheus client not available. Metrics will not be collected.")

# --- Structured Logging Setup ---
class ConfigManagerLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter for DLT Network Config Manager."""
    def process(self, msg, kwargs):
        extra = kwargs.get('extra', {})
        extra['component'] = self.extra.get('component', 'DLTConfigManager')
        kwargs['extra'] = extra
        return msg, kwargs

_base_logger = logging.getLogger(__name__)
_base_logger.setLevel(logging.INFO)
if not _base_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(name)s - component:%(component)s - %(message)s')
    handler.setFormatter(formatter)
    _base_logger.addHandler(handler)

logger = ConfigManagerLoggerAdapter(_base_logger, {'component': 'DLTConfigManager'})

# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# Path validation toggles
VALIDATE_PATHS = os.getenv("DLT_VALIDATE_PATHS", "false").lower() == "true"  # validate paths even outside production
ENFORCE_PATHS_IN_PROD = os.getenv("DLT_ENFORCE_PATHS_IN_PROD", "true").lower() == "true"  # enforce in production

# --- Metrics (Idempotent Registration; no private API use) ---
_METRICS: Dict[str, Any] = {}

def _noop_metric(metric_type):
    """Returns a no-op object with labels and increment/observe methods."""
    class _Noop:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
    return _Noop()

def get_or_create_metric(metric_type, name, documentation, labelnames: Tuple[str, ...] = (), buckets=None):
    if not PROMETHEUS_AVAILABLE:
        return _noop_metric(metric_type)
    if name in _METRICS:
        return _METRICS[name]
    try:
        if metric_type is Histogram:
            metric = metric_type(name, documentation, labelnames=labelnames, buckets=buckets or Histogram.DEFAULT_BUCKETS)
        else:
            metric = metric_type(name, documentation, labelnames=labelnames)
        _METRICS[name] = metric
        return metric
    except ValueError:
        logger.warning(f"Metric '{name}' already registered. Using no-op for this instance.")
        metric = _noop_metric(metric_type)
        _METRICS[name] = metric
        return metric

def _get_metric_factory():
    """Consistent factory function that always returns a dictionary with all expected metric keys."""
    return {
        "load_total": get_or_create_metric(Counter, "dlt_config_load_total", "Total DLT config load attempts", ("status",)),
        "validation_errors": get_or_create_metric(Counter, "dlt_config_validation_errors_total", "Total DLT config validation errors", ("dlt_type",)),
        "refresh_total": get_or_create_metric(Counter, "dlt_config_refresh_total", "Total DLT config refresh attempts", ("status",)),
        "change_total": get_or_create_metric(Counter, "dlt_config_change_total", "Total DLT config changes detected"),
        "load_latency": get_or_create_metric(Histogram, "dlt_config_load_latency_seconds", "Latency of DLT config loads", ("phase",))
    }

# --- Custom Exceptions ---
class DLTClientConfigurationError(Exception):
    """Custom exception for DLT client configuration errors."""
    def __init__(self, message: str, component: str = "DLTConfigManager"):
        super().__init__(message)
        self.component = component
        logger.error(f"DLTClientConfigurationError in {component}: {message}")

# --- Security: Secret Scrubbing ---
AZURE_CONN_RE = re.compile(r"DefaultEndpointsProtocol=https;AccountName=([^;]+);AccountKey=([^;]+)", re.IGNORECASE)
GENERIC_SECRET_KV_RE = re.compile(r"(?i)(private_key|password|api_key|secret|token|jwt)[\"'\s]*[:=][\"'\s]*([a-zA-Z0-9\-_./+=]+)")
AWS_ACCESS_KEY_RE = re.compile(r"aws_access_key_id=([A-Za-z0-9]+)", re.IGNORECASE)
AWS_SECRET_KEY_RE = re.compile(r"aws_secret_access_key=([A-Za-z0-9/+=]+)", re.IGNORECASE)
CLIENT_SECRET_RE = re.compile(r"client_secret=([A-Za-z0-9\-_]+)", re.IGNORECASE)
GENERIC_SECRET_VALUE_RE = re.compile(r"(private_key|password|api_key|secret|token|jwt|access_key|secret_key|credentials)", re.IGNORECASE)


def _scrub_string_value(s: str) -> str:
    # Handle Azure connection strings explicitly first
    s = AZURE_CONN_RE.sub("DefaultEndpointsProtocol=https;AccountName=[REDACTED];AccountKey=[REDACTED]", s)
    # Generic key=value secrets (keep key, mask value)
    s = GENERIC_SECRET_KV_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", s)
    # Specific patterns
    s = AWS_ACCESS_KEY_RE.sub("aws_access_key_id=[REDACTED]", s)
    s = AWS_SECRET_KEY_RE.sub("aws_secret_access_key=[REDACTED]", s)
    s = CLIENT_SECRET_RE.sub("client_secret=[REDACTED]", s)
    # Generic keys with values in a dict
    if GENERIC_SECRET_VALUE_RE.search(s):
        return "[REDACTED]"
    return s

def scrub_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrubs sensitive data from a dictionary."""
    sanitized_data: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            sanitized_data[key] = scrub_secrets(value)
        elif isinstance(value, list):
            sanitized_data[key] = [scrub_secrets(item) if isinstance(item, dict) else (_scrub_string_value(item) if isinstance(item, str) else item) for item in value]
        elif isinstance(value, str):
            if GENERIC_SECRET_VALUE_RE.search(key):
                sanitized_data[key] = "[REDACTED]"
            else:
                sanitized_data[key] = _scrub_string_value(value)
        else:
            sanitized_data[key] = value
    return sanitized_data

# --- Security: Secure Credential Loading ---
def _load_secret_from_aws_secrets_manager(secret_name: str) -> Optional[str]:
    if not BOTO3_AVAILABLE:
        logger.warning(f"Boto3 not available. Cannot load secret '{secret_name}' from AWS Secrets Manager.")
        return None
    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
        if 'SecretString' in response:
            return response['SecretString']
        elif 'SecretBinary' in response:
            return response['SecretBinary'].decode('utf-8')  # Assume UTF-8 for binary secrets
        return None
    except ClientError as e:
        logger.error(f"Failed to retrieve secret '{secret_name}' from AWS Secrets Manager: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error retrieving secret '{secret_name}' from AWS Secrets Manager: {e}", exc_info=True)
        return None

# --- Configuration Schemas (Pydantic v2) ---
class S3OffChainConfig(BaseModel):
    bucket_name: str = Field(..., description="AWS S3 bucket name for off-chain storage.")
    region_name: Optional[str] = Field(None, description="AWS region, defaults to env AWS_REGION or 'us-east-1'.")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID, defaults to env AWS_ACCESS_KEY_ID.")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key, defaults to env AWS_SECRET_ACCESS_KEY.")

    model_config = ConfigDict(extra='forbid')

class GcsOffChainConfig(BaseModel):
    bucket_name: str = Field(..., description="GCS bucket name for off-chain storage.")
    project_id: Optional[str] = Field(None, description="GCP project ID, defaults to env GCP_PROJECT_ID.")
    credentials_path: Optional[str] = Field(None, description="Path to GCP service account key file, defaults to env GOOGLE_APPLICATION_CREDENTIALS.")

    model_config = ConfigDict(extra='forbid')

class AzureBlobOffChainConfig(BaseModel):
    connection_string: str = Field(..., description="Azure Storage account connection string.")
    container_name: str = Field(..., description="Azure Blob container name.")

    model_config = ConfigDict(extra='forbid')

class IpfsOffChainConfig(BaseModel):
    api_url: AnyHttpUrl = Field(..., description="URL of the IPFS API endpoint (e.g., 'http://127.0.0.1:5001').")

    model_config = ConfigDict(extra='forbid')

class FabricDLTConfig(BaseModel):
    channel_name: str = Field(..., description="Name of the Fabric channel.")
    chaincode_name: str = Field(..., description="Name of the deployed chaincode.")
    org_name: str = Field(..., description="Name of the Fabric organization.")
    user_name: str = Field(..., description="Name of the user identity for transactions.")
    network_profile_path: str = Field(..., description="Path to the Fabric network.json file.")
    peer_names: List[str] = Field(default_factory=list, description="List of peer names for transaction endorsement/query.")
    invoke_timeout: int = Field(60, description="Timeout for chaincode invoke operations in seconds.")
    query_timeout: int = Field(30, description="Timeout for chaincode query operations in seconds.")

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def _validate_paths(self) -> 'FabricDLTConfig':
        should_check = (PRODUCTION_MODE and ENFORCE_PATHS_IN_PROD) or VALIDATE_PATHS
        if should_check and not os.path.exists(self.network_profile_path):
            raise ValueError(f"Fabric network_profile_path does not exist: {self.network_profile_path}")
        return self

class EvmDLTConfig(BaseModel):
    rpc_url: AnyHttpUrl = Field(..., description="EVM node RPC endpoint URL.")
    chain_id: int = Field(..., description="Chain ID of the EVM network.")
    contract_address: str = Field(..., description="Address of the deployed Checkpoint smart contract.")
    contract_abi_path: str = Field(..., description="Path to the smart contract ABI JSON file.")
    private_key: Optional[str] = Field(None, description="Hex string of the private key for transaction signing (sensitive).")
    private_key_secret_id: Optional[str] = Field(None, description="AWS Secrets Manager ID for the private key.")
    poa_middleware: bool = Field(False, description="Set to True for Proof-of-Authority (PoA) chains (e.g., Ganache, Besu).")
    default_gas_limit: int = Field(2_000_000, description="Default gas limit for transactions.")
    default_max_fee_per_gas: Optional[int] = Field(None, description="Default maxFeePerGas (in Gwei) for EIP-1559 transactions.")
    default_max_priority_fee_per_gas: Optional[int] = Field(None, description="Default maxPriorityFeePerGas (in Gwei) for EIP-1559 transactions.")
    tx_confirm_timeout: int = Field(120, description="Timeout for transaction confirmation in seconds.")

    model_config = ConfigDict(extra='forbid')

    @field_validator('rpc_url')
    @classmethod
    def validate_rpc_url_https(cls, v: AnyHttpUrl) -> AnyHttpUrl:
        if PRODUCTION_MODE and v.scheme != 'https':
            raise ValueError("RPC URL must use HTTPS in production mode.")
        return v

    @field_validator('contract_address')
    @classmethod
    def validate_contract_address(cls, v: str) -> str:
        # Allow placeholders in non-production environments
        if not PRODUCTION_MODE and v.endswith("..."):
            return v
        # Strict 0x-prefixed, 20-byte address check
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", v):
            raise ValueError("contract_address must be a 0x-prefixed 20-byte hex string.")
        return v

    @model_validator(mode='after')
    def enforce_private_key_presence(self) -> 'EvmDLTConfig':
        if not self.private_key and self.private_key_secret_id:
            loaded_key = _load_secret_from_aws_secrets_manager(self.private_key_secret_id)
            if loaded_key:
                self.private_key = loaded_key
            elif PRODUCTION_MODE:
                raise ValueError(f"Private key secret '{self.private_key_secret_id}' not found in Secrets Manager in production mode.")
        
        # Enforce at least one credential path; in PRODUCTION require presence
        if PRODUCTION_MODE and not (self.private_key or self.private_key_secret_id):
            raise ValueError("In PRODUCTION_MODE, EVM config requires a private_key or private_key_secret_id.")
        # If private_key present, validate hex format 0x + 64 hex chars (32 bytes)
        if self.private_key and not re.fullmatch(r"0x[a-fA-F0-9]{64}", self.private_key):
            raise ValueError("private_key must be a 0x-prefixed 32-byte hex string (64 hex chars).")
        # Non-negative numeric values
        for field_name in ('default_gas_limit', 'default_max_fee_per_gas', 'default_max_priority_fee_per_gas', 'tx_confirm_timeout'):
            val = getattr(self, field_name)
            if val is not None and val < 0:
                raise ValueError(f"{field_name} must be non-negative.")
        if self.chain_id <= 0:
            raise ValueError("chain_id must be > 0.")
        # Optional file existence check
        should_check = (PRODUCTION_MODE and ENFORCE_PATHS_IN_PROD) or VALIDATE_PATHS
        if should_check and not os.path.exists(self.contract_abi_path):
            raise ValueError(f"EVM contract_abi_path does not exist: {self.contract_abi_path}")
        return self

class CordaDLTConfig(BaseModel):
    rpc_url: AnyHttpUrl = Field(..., description="Corda node RPC endpoint URL.")
    user: str = Field(..., description="Corda RPC username.")
    password: Optional[str] = Field(None, description="Corda RPC password (sensitive).")
    password_secret_id: Optional[str] = Field(None, description="AWS Secrets Manager ID for the password.")

    model_config = ConfigDict(extra='forbid')

    @field_validator('rpc_url')
    @classmethod
    def validate_rpc_url_https(cls, v: AnyHttpUrl) -> AnyHttpUrl:
        if PRODUCTION_MODE and v.scheme != 'https':
            raise ValueError("RPC URL must use HTTPS in production mode.")
        return v

    @model_validator(mode='after')
    def enforce_password_presence(self) -> 'CordaDLTConfig':
        if self.password_secret_id:
            loaded_password = _load_secret_from_aws_secrets_manager(self.password_secret_id)
            if loaded_password:
                self.password = loaded_password
            elif PRODUCTION_MODE:
                raise ValueError(f"Password secret '{self.password_secret_id}' not found in Secrets Manager in production mode.")
        
        if PRODUCTION_MODE and not (self.password or self.password_secret_id):
            raise ValueError("In PRODUCTION_MODE, Corda config requires a password or password_secret_id.")
        return self

class SimpleDLTConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

class DLTNetworkConfig(BaseModel):
    name: str = Field(..., description="Unique name for this DLT network configuration (e.g., 'mainnet-fabric', 'goerli-evm').")
    dlt_type: str = Field(..., description="Type of DLT ('fabric', 'evm', 'corda', 'simple').")
    off_chain_storage_type: str = Field('s3', description="Type of off-chain storage ('s3', 'gcs', 'azure_blob', 'ipfs', 'in_memory').")

    fabric: Optional[FabricDLTConfig] = None
    evm: Optional[EvmDLTConfig] = None
    corda: Optional[CordaDLTConfig] = None
    simple: Optional[SimpleDLTConfig] = None

    s3: Optional[S3OffChainConfig] = None
    gcs: Optional[GcsOffChainConfig] = None
    azure_blob: Optional[AzureBlobOffChainConfig] = None
    ipfs: Optional[IpfsOffChainConfig] = None

    default_timeout_seconds: int = Field(30, description="Default timeout for DLT operations in seconds.")
    retry_attempts: int = Field(5, description="Number of retry attempts for DLT operations.")
    retry_backoff_factor: float = Field(2.0, description="Factor for exponential backoff between retries.")
    
    secret_scrub_patterns: List[str] = Field(default_factory=list, description="Regex patterns for scrubbing secrets from logs/exceptions for this specific network.")

    _CONFIG_SCHEMA_VERSION = PrivateAttr(1)

    model_config = ConfigDict(extra='forbid')

    @field_validator('dlt_type')
    @classmethod
    def validate_dlt_type(cls, v: str) -> str:
        allowed_types = ['fabric', 'evm', 'corda', 'simple']
        if v not in allowed_types:
            raise ValueError(f"DLT type must be one of {allowed_types}.")
        return v

    @field_validator('off_chain_storage_type')
    @classmethod
    def validate_off_chain_storage_type(cls, v: str) -> str:
        allowed_types = ['s3', 'gcs', 'azure_blob', 'ipfs', 'in_memory']
        if v not in allowed_types:
            raise ValueError(f"Off-chain storage type must be one of {allowed_types}.")
        return v

    @model_validator(mode='after')
    def validate_off_chain_config(self) -> "DLTNetworkConfig":
        if self.off_chain_storage_type != "in_memory":
            if self.off_chain_storage_type == 's3' and not self.s3:
                raise ValueError("Off-chain storage type 's3' requires 's3' configuration.")
            if self.off_chain_storage_type == 'gcs' and not self.gcs:
                raise ValueError("Off-chain storage type 'gcs' requires 'gcs' configuration.")
            if self.off_chain_storage_type == 'azure_blob' and not self.azure_blob:
                raise ValueError("Off-chain storage type 'azure_blob' requires 'azure_blob' configuration.")
            if self.off_chain_storage_type == 'ipfs' and not self.ipfs:
                raise ValueError("Off-chain storage type 'ipfs' requires 'ipfs' configuration.")
        return self

    @classmethod
    def load_and_validate(cls, config_data: Dict[str, Any]) -> "DLTNetworkConfig":
        migrated_data = cls._migrate_schema(config_data)
        try:
            # Strip internal fields before validation
            internal_keys = ['_CONFIG_SCHEMA_VERSION']
            cleaned_data = {k: v for k, v in migrated_data.items() if k not in internal_keys}
            return cls.model_validate(cleaned_data)  # Pydantic v2
        except ValidationError as e:
            logger.error(f"DLT Network Config Validation Error: {e.errors()}", exc_info=True, extra={'config_name': migrated_data.get('name')})
            raise DLTClientConfigurationError(f"Invalid DLT Network Configuration: {e.errors()}", "ConfigManager")

    @classmethod
    def _migrate_schema(cls, config_data: Dict[str, Any]) -> Dict[str, Any]:
        # Work on a deep copy to avoid mutating caller data
        migrated = json.loads(json.dumps(config_data))
        current_version = migrated.get('_CONFIG_SCHEMA_VERSION', 0)
        if current_version < 1:
            logger.info(f"Migrating DLT Network Config '{migrated.get('name', 'unknown')}' from v0 to v1.", extra={'config_name': migrated.get('name')})
            # Example migration: old 'evm_private_key' -> evm.private_key
            if 'evm_private_key' in migrated:
                migrated.setdefault('evm', {})['private_key'] = migrated.pop('evm_private_key')
            migrated['_CONFIG_SCHEMA_VERSION'] = 1
        # Normalize name to lowercase for consistency across sources
        if 'name' in migrated and isinstance(migrated['name'], str):
            migrated['name'] = migrated['name'].lower()
        return migrated

# --- Helpers for hashing and normalization (consistent across load/refresh) ---

_SENSITIVE_KEYS = re.compile(r"(?i)(private_key|password|secret|token|api_key|jwt)")

def _strip_sensitive_fields(obj: Any) -> Any:
    """Return a deep-copied object with sensitive values removed/masked for hashing."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if _SENSITIVE_KEYS.search(k):
                out[k] = "__PRESENT__" if v else "__ABSENT__"
            else:
                out[k] = _strip_sensitive_fields(v)
        return out
    if isinstance(obj, list):
        return [_strip_sensitive_fields(x) for x in obj]
    return obj

def _normalize_config_for_hash(raw_cfg: Dict[str, Any]) -> Dict[str, Any]:
    migrated = DLTNetworkConfig._migrate_schema(raw_cfg)
    return _strip_sensitive_fields(migrated)

def _compute_raw_configs_hash(name_to_cfg: Dict[str, Dict[str, Any]]) -> str:
    # Normalize each config, sort by name, stable serialization
    normalized: Dict[str, Any] = {}
    for name, cfg in name_to_cfg.items():
        normalized[name.lower()] = _normalize_config_for_hash(cfg)
    s = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

class DLTNetworkConfigManager:
    _instance: Optional["DLTNetworkConfigManager"] = None
    _configs: Dict[str, DLTNetworkConfig] = {}
    _last_config_hash: Optional[str] = None
    _config_refresh_interval: int = 300  # seconds

    # Thread-safety for updates/reads
    _state_lock: threading.RLock

    def __new__(cls, config_refresh_interval: Optional[int] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._state_lock = threading.RLock()
            if config_refresh_interval is not None:
                cls._instance._config_refresh_interval = config_refresh_interval
            cls._instance._load_all_configs_from_env()
        return cls._instance

    # ------------------ Raw env collection ------------------

    def _collect_raw_configs_from_env(self) -> Dict[str, Dict[str, Any]]:
        """
        Collect raw configs from environment variables into a mapping name(lower) -> raw dict.
        Precedence: individual DLT_NETWORK_CONFIG_<NAME>_JSON overrides entries in DLT_NETWORK_CONFIGS_JSON (if same name).
        """
        raw: Dict[str, Dict[str, Any]] = {}
        metrics = _get_metric_factory()

        all_configs_json = os.getenv("DLT_NETWORK_CONFIGS_JSON")
        if all_configs_json:
            try:
                parsed = json.loads(all_configs_json)
                if isinstance(parsed, list):
                    # Array of objects
                    for cfg in parsed:
                        if isinstance(cfg, dict):
                            name = cfg.get("name")
                            if name:
                                norm = str(name).lower()
                                cfg['name'] = norm
                                raw[norm] = cfg
                elif isinstance(parsed, dict):
                    # Dict of name -> cfg, or a single config
                    is_single_config = 'dlt_type' in parsed and 'name' in parsed
                    if is_single_config:
                        name = parsed.get("name")
                        if name:
                            norm = str(name).lower()
                            parsed['name'] = norm
                            raw[norm] = parsed
                    else:
                        for name, cfg in parsed.items():
                            if isinstance(cfg, dict):
                                norm = str(name).lower()
                                cfg.setdefault("name", norm)
                                cfg['name'] = str(cfg['name']).lower()
                                raw[norm] = cfg
                else:
                    logger.warning("DLT_NETWORK_CONFIGS_JSON must be a JSON array or object; ignoring.")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse DLT_NETWORK_CONFIGS_JSON: {e}")
                metrics["validation_errors"].labels(dlt_type='unknown').inc()

        # Individual env vars
        for env_var_name, env_var_value in os.environ.items():
            if env_var_name.startswith("DLT_NETWORK_CONFIG_") and env_var_name.endswith("_JSON"):
                config_name_from_env = env_var_name[len("DLT_NETWORK_CONFIG_"):-len("_JSON")].lower()
                try:
                    cfg = json.loads(env_var_value)
                    if not isinstance(cfg, dict):
                        logger.warning(f"Env var {env_var_name} does not contain a JSON object; skipping.")
                        continue
                    cfg.setdefault("name", config_name_from_env)
                    norm = str(cfg["name"]).lower()
                    cfg['name'] = norm
                    raw[norm] = cfg  # override by individual var
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse {env_var_name}: {e}")
                    metrics["validation_errors"].labels(dlt_type='unknown').inc()
        return raw

    # ------------------ Load and refresh ------------------

    def _load_all_configs_from_env(self):
        start = time.monotonic()
        metrics = _get_metric_factory()
        with self._state_lock:
            loaded_configs: Dict[str, DLTNetworkConfig] = {}
            raw_map = self._collect_raw_configs_from_env()

            # Validate and build objects
            for name, raw_cfg in raw_map.items():
                try:
                    self._add_config(raw_cfg, loaded_configs)
                except (ValidationError, DLTClientConfigurationError):
                    # Already logged and metered in _add_config
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error validating config '{name}': {e}", exc_info=True)
                    # Attempt to label by dlt_type if available
                    dlt_type = (raw_cfg.get('dlt_type') or 'unknown')
                    metrics["validation_errors"].labels(dlt_type=str(dlt_type)).inc()
                    continue

            # Atomically replace
            self.__class__._configs = loaded_configs
            # Compute hash consistently from raw data (normalized, secrets stripped)
            self.__class__._last_config_hash = _compute_raw_configs_hash(raw_map)

        metrics["load_latency"].labels(phase="load_all").observe(time.monotonic() - start)
        logger.info(f"Loaded {len(loaded_configs)} DLT network configurations.", extra={'current_configs': list(loaded_configs.keys())})
        metrics["load_total"].labels(status="success").inc()

    def _add_config(self, config_data: Dict[str, Any], target_dict: Dict[str, DLTNetworkConfig]):
        scrubbed_config_data = scrub_secrets(config_data.copy())
        try:
            validated_config = DLTNetworkConfig.load_and_validate(config_data)
            if validated_config.name in target_dict:
                logger.warning(f"Duplicate DLT network config name detected: '{validated_config.name}'. Overwriting existing.", extra={'config_name': validated_config.name})
            target_dict[validated_config.name] = validated_config
        except (ValidationError, DLTClientConfigurationError) as e:
            metrics = _get_metric_factory()
            # Low-cardinality label
            dlt_type = (config_data.get('dlt_type') or 'unknown')
            metrics["validation_errors"].labels(dlt_type=str(dlt_type)).inc()
            logger.error(f"Invalid DLT network configuration for '{config_data.get('name', 'unnamed')}': {e}", exc_info=True, extra={'config_data': scrubbed_config_data})
            raise

    async def refresh_configs_if_changed(self) -> bool:
        """
        Recompute a stable hash of raw configs from env and reload only on change.
        Returns True if a reload happened.
        """
        metrics = _get_metric_factory()
        metrics["refresh_total"].labels(status="attempt").inc()
        raw_map = self._collect_raw_configs_from_env()
        current_hash = _compute_raw_configs_hash(raw_map)

        with self._state_lock:
            if current_hash != self._last_config_hash:
                logger.info("Detected DLT network configuration change. Reloading configurations...",
                            extra={'old_hash': self._last_config_hash, 'new_hash': current_hash})
                self._load_all_configs_from_env()  # will recompute hash internally from current env
                logger.info(f"DLT network configurations reloaded. Now {len(self._configs)} active configs.",
                            extra={'current_configs': list(self._configs.keys())})
                metrics["refresh_total"].labels(status="success").inc()
                metrics["change_total"].inc()
                return True

        metrics["refresh_total"].labels(status="no_change").inc()
        return False

    # Optional: background refresher
    def start_background_refresh(self, stop_event: Optional[threading.Event] = None, jitter: float = 0.2) -> threading.Thread:
        """
        Starts a background thread that periodically calls refresh_configs_if_changed.
        jitter: fraction of interval to randomize (+/-) to avoid synchronization across processes.
        Returns the thread object (daemon).
        """
        import random
        if stop_event is None:
            stop_event = threading.Event()

        def _loop():
            while not stop_event.is_set():
                try:
                    # In a real-world scenario, you might not use asyncio.run in a sync thread like this,
                    # but for this simple example and to avoid refactoring the async method, it works.
                    # A better pattern is to use `asyncio.create_task` and a single event loop.
                    # Here, we create a new event loop per call.
                    asyncio.run(self.refresh_configs_if_changed())
                except Exception as e:
                    logger.error(f"Background refresh error: {e}", exc_info=True)
                base = self._config_refresh_interval
                delta = base * jitter
                sleep_time = max(1.0, base + random.uniform(-delta, delta))
                stop_event.wait(sleep_time)

        t = threading.Thread(target=_loop, name="DLTConfigRefreshThread", daemon=True)
        t.start()
        return t

    # ------------------ Accessors ------------------

    def get_config(self, name: str) -> Optional[DLTNetworkConfig]:
        with self._state_lock:
            return self._configs.get(name.lower())

    def get_all_configs(self) -> Dict[str, DLTNetworkConfig]:
        with self._state_lock:
            # Return a shallow copy to prevent external mutation
            return dict(self._configs)

    def get_default_config(self) -> Optional[DLTNetworkConfig]:
        with self._state_lock:
            if not self._configs:
                return None
            # Deterministic: pick the alphabetically-first name
            first_name = sorted(self._configs.keys())[0]
            return self._configs[first_name]

# --- Factory for external use (avoid import-time side effects) ---
def get_dlt_network_config_manager(config_refresh_interval: Optional[int] = None) -> DLTNetworkConfigManager:
    """
    Returns the singleton DLTNetworkConfigManager instance (creating it on first call).
    Prefer this over importing a pre-instantiated singleton to avoid import-time side effects.
    """
    return DLTNetworkConfigManager(config_refresh_interval=config_refresh_interval)

# --- Example Usage and Test ---
if __name__ == "__main__":
    async def run_tests():
        _base_logger.setLevel(logging.DEBUG)
        print("\n--- Running DLT Network Config Manager Tests ---")

        # --- Test 1: Load from Environment Variables ---
        print("\nTest 1: Loading configs from environment variables.")
        
        # Clear potentially conflicting env keys
        for key in list(os.environ.keys()):
            if key.startswith("DLT_NETWORK_CONFIG_") or key == "DLT_NETWORK_CONFIGS_JSON":
                del os.environ[key]

        # Ensure PRODUCTION_MODE is False for tests
        os.environ["PRODUCTION_MODE"] = "false"
        os.environ["DLT_VALIDATE_PATHS"] = "true"

        os.environ["DLT_NETWORK_CONFIG_DEV_FABRIC_JSON"] = json.dumps({
            "name": "dev-fabric",
            "dlt_type": "fabric",
            "off_chain_storage_type": "s3",
            "fabric": {
                "channel_name": "dev-channel", "chaincode_name": "dev-cc", "org_name": "dev-org",
                "user_name": "user1", "network_profile_path": __file__,  # point to existing file for path check
                "peer_names": ["peer0.dev.com"], "invoke_timeout": 30
            },
            "s3": {"bucket_name": "dev-s3-bucket", "region_name": "us-east-1"},
            "secret_scrub_patterns": ["dev_secret"]
        })
        os.environ["DLT_NETWORK_CONFIG_PROD_EVM_JSON"] = json.dumps({
            "name": "prod-evm",
            "dlt_type": "evm",
            "off_chain_storage_type": "azure_blob",
            "evm": {
                "rpc_url": "https://mainnet.infura.io/v3/abc", "chain_id": 1, "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                "contract_abi_path": __file__,  # point to existing file for path check
                "private_key_secret_id": "evm/prod-private-key",
                "default_max_fee_per_gas": 100
            },
            "azure_blob": {"connection_string": "DefaultEndpointsProtocol=https;AccountName=myacct;AccountKey=supersecret", "container_name": "prod-blob-container"},
            "secret_scrub_patterns": ["PROD_PRIVATE_KEY"]
        })
        os.environ["DLT_NETWORK_CONFIGS_JSON"] = json.dumps([
            {
                "name": "test-simple",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory"
            },
            {
                "name": "test-gcs",
                "dlt_type": "simple",
                "off_chain_storage_type": "gcs",
                "gcs": {"bucket_name": "test-gcs-bucket"}
            }
        ])

        # Mock AWS Secrets Manager for testing private key loading
        original_boto3_client = None
        class MockSecretsManagerClient:
            def get_secret_value(self, SecretId):
                if SecretId == "evm/prod-private-key":
                    return {"SecretString": "0x" + "A"*64}
                raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue") if ClientError else Exception("Not found")

        if BOTO3_AVAILABLE:
            original_boto3_client = boto3.client
            def _mock_boto3_client(service_name, *args, **kwargs):
                if service_name == "secretsmanager":
                    return MockSecretsManagerClient()
                return original_boto3_client(service_name, *args, **kwargs)
            boto3.client = _mock_boto3_client
        else:
            print("Boto3 not available, skipping secret loading test path.")

        # Instantiate manager on demand
        mgr = get_dlt_network_config_manager()
        configs = mgr.get_all_configs()
        
        print(f"Loaded {len(configs)} configurations:")
        for name, cfg in configs.items():
            print(f"  - {name}: DLT Type={cfg.dlt_type}, Off-chain={cfg.off_chain_storage_type}")
            if cfg.evm:
                print(f"    EVM private_key loaded: {'present' if bool(cfg.evm.private_key) else 'absent'}")
        assert "dev-fabric" in configs
        assert "prod-evm" in configs
        assert "test-simple" in configs
        assert "test-gcs" in configs

        if BOTO3_AVAILABLE:
            assert configs["prod-evm"].evm.private_key == "0x" + "A"*64
        else:
            # Without boto3, private_key stays None (allowed outside PRODUCTION_MODE)
            assert configs["prod-evm"].evm.private_key is None

        print("Test 1 PASSED: Configurations loaded and validated.")

        # --- Test 2: Runtime Refresh ---
        print("\nTest 2: Runtime Refresh of configurations.")
        original_hash = mgr._last_config_hash
        print(f"Initial config hash: {original_hash}")

        os.environ["DLT_NETWORK_CONFIG_NEW_SIMPLE_JSON"] = json.dumps({
            "name": "new-simple",
            "dlt_type": "simple",
            "off_chain_storage_type": "in_memory",
            "default_timeout_seconds": 99
        })

        # No wait needed for async test, call directly
        refreshed = await mgr.refresh_configs_if_changed()
        
        print(f"Refreshed status: {refreshed}")
        new_configs = mgr.get_all_configs()
        new_hash = mgr._last_config_hash
        print(f"New config hash: {new_hash}")

        assert refreshed is True
        assert "new-simple" in new_configs
        assert new_configs["new-simple"].default_timeout_seconds == 99
        assert new_hash != original_hash

        print("Test 2 PASSED: Configurations refreshed at runtime.")

        # --- Test 3: Production-specific validations (skipped unless explicitly enabled) ---
        os.environ["PRODUCTION_MODE"] = "true"
        os.environ["DLT_VALIDATE_PATHS"] = "false"
        mgr = get_dlt_network_config_manager()

        if PRODUCTION_MODE:
            print("\nTest 3.1: Insecure HttpUrl (non-HTTPS in prod)")
            os.environ["DLT_NETWORK_CONFIG_INSECURE_EVM_JSON"] = json.dumps({
                "name": "insecure-evm",
                "dlt_type": "evm",
                "off_chain_storage_type": "in_memory",
                "evm": {
                    "rpc_url": "http://insecure.com", "chain_id": 1, "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                    "contract_abi_path": __file__, "private_key": "0x" + "1"*64
                }
            })
            try:
                await mgr.refresh_configs_if_changed()
                assert "insecure-evm" not in mgr.get_all_configs()
                print("Test 3.1 PASSED: Insecure HttpUrl was rejected in production mode.")
            except DLTClientConfigurationError:
                print("Test 3.1 PASSED: Expected error on insecure HttpUrl.")

            print("\nTest 3.2: Missing private_key_secret_id/private_key in prod for EVM")
            os.environ["DLT_NETWORK_CONFIG_MISSING_KEY_EVM_JSON"] = json.dumps({
                "name": "missing-key-evm",
                "dlt_type": "evm",
                "off_chain_storage_type": "in_memory",
                "evm": {
                    "rpc_url": "https://secure.com", "chain_id": 1, "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                    "contract_abi_path": __file__
                }
            })
            try:
                await mgr.refresh_configs_if_changed()
                assert "missing-key-evm" not in mgr.get_all_configs()
                print("Test 3.2 PASSED: Missing private_key was rejected in production mode.")
            except DLTClientConfigurationError:
                print("Test 3.2 PASSED: Expected error on missing private key in production.")
        else:
            print("Production-mode specific tests skipped (PRODUCTION_MODE=false).")

        # Restore boto3.client mock if changed
        if BOTO3_AVAILABLE and original_boto3_client:
            boto3.client = original_boto3_client

        print("\n--- All DLT Network Config Manager Tests Complete ---")

    # Example: os.environ["PRODUCTION_MODE"] = "true"  # Uncomment to test production-specific validations
    asyncio.run(run_tests())