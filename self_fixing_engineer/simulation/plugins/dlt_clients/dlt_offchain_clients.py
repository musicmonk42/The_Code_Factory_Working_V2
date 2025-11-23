# simulation/plugins/dlt_clients/dlt_offchain_clients.py

import os
import asyncio
import json
import time
import uuid
from typing import Any, Dict, Optional, List, Literal, Final
from contextlib import suppress
import atexit
import tempfile
from pydantic import BaseModel, Field, validator, ValidationError
from abc import ABC, abstractmethod
from datetime import datetime

from .dlt_base import (
    BaseOffChainClient,
    DLTClientConfigurationError,
    DLTClientError,
    DLTClientTransactionError,
    DLTClientQueryError,
    DLTClientValidationError,
    DLTClientCircuitBreakerError,
    async_retry,
    TRACER,
    Status,
    StatusCode,
    alert_operator,
    AUDIT,
    PRODUCTION_MODE,
)
from .dlt_base import _base_logger

# --- Strict Dependency Checks for Cloud SDKs ---
S3_AVAILABLE = False
try:
    import aioboto3
    import boto3
    from botocore.exceptions import ClientError as BotoClientError

    S3_AVAILABLE = True
except ImportError:
    _base_logger.warning("boto3/aioboto3 not found. AWS S3 off-chain storage will be disabled.")

    class BotoClientError(Exception):
        pass


GCS_AVAILABLE = False
try:
    from google.cloud import storage as gcs_sdk
    from google.oauth2 import service_account
    from google.cloud import secretmanager

    GCS_AVAILABLE = True
except ImportError:
    _base_logger.warning("google-cloud-storage not found. GCS off-chain storage will be disabled.")

AZURE_BLOB_AVAILABLE = False
try:
    from azure.storage.blob.aio import BlobServiceClient as AzureBlobServiceClient
    from azure.core.exceptions import (
        ResourceNotFoundError as AzureResourceNotFoundError,
    )
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient as AsyncSecretClient

    AZURE_BLOB_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "azure-storage-blob not found. Azure Blob off-chain storage will be disabled."
    )

    class AzureResourceNotFoundError(Exception):
        pass


IPFS_AVAILABLE = False
try:
    import ipfshttpclient

    IPFS_AVAILABLE = True
except ImportError:
    _base_logger.warning("ipfshttpclient not found, IPFS client will be disabled.")

# --- Metrics ---
try:
    from prometheus_client import Counter

    OFFCHAIN_METRICS = {
        "validation_failure": Counter(
            "offchain_validation_failure_total",
            "Total number of off-chain validation failures",
            labelnames=["client_type", "operation"],
        ),
        "secrets_unavailable_total": Counter(
            "offchain_secrets_unavailable_total",
            "Total number of times a secrets backend was requested but unavailable",
            labelnames=["client_type", "backend"],
        ),
        "client_init_failure": Counter(
            "offchain_client_init_failure_total",
            "Total failures during off-chain client initialization",
            labelnames=["client_type", "error_type"],
        ),
    }
except ImportError:
    _base_logger.warning("Prometheus client not available for Off-Chain specific metrics.")
    OFFCHAIN_METRICS = {}

# Temporary file cleanup
_temp_files: Dict[str, float] = {}


def cleanup_temp_files() -> None:
    """Cleans up temporary files created by temp_file context manager."""
    global _temp_files
    files_to_clean = list(_temp_files.keys())
    for temp_file in files_to_clean:
        try:
            os.unlink(temp_file)
            _base_logger.info(f"Cleaned up temporary file: {temp_file}")
        except OSError as e:
            _base_logger.warning(f"Failed to clean up temporary file {temp_file}: {e}")
        finally:
            _temp_files.pop(temp_file, None)


atexit.register(cleanup_temp_files)


def create_temp_file(content: str, ttl: float = 3600.0) -> str:
    """
    Creates a temporary file with specified content and registers it for cleanup.
    The file is created with restrictive permissions (0o600).
    """
    global _temp_files
    fd, path = tempfile.mkstemp(mode="w", delete=False, suffix=".json", prefix="offchain_")
    try:
        with os.fdopen(fd, "w") as tmp:
            tmp.write(content)
        os.chmod(path, 0o600)
        _temp_files[path] = time.time()
        _base_logger.info(f"Created temporary file: {path} with TTL {ttl}s")
        return path
    except Exception as e:
        _base_logger.critical(
            f"CRITICAL: Failed to create or write to temporary file: {e}. Aborting.",
            exc_info=True,
        )
        try:
            asyncio.get_running_loop().create_task(
                alert_operator(
                    f"CRITICAL: Failed to create or write temporary file for off-chain client: {e}. Aborting.",
                    level="CRITICAL",
                )
            )
        except RuntimeError:
            pass
        raise DLTClientConfigurationError(
            "Failed to create temporary file for credentials.",
            "OffChain",
            original_exception=e,
        ) from e


# Secrets Backend Interface
class SecretsBackend(ABC):
    @abstractmethod
    async def get_secret(self, secret_id: str) -> str:
        pass


class AWSSecretsBackend(SecretsBackend):
    def __init__(self):
        if not S3_AVAILABLE:
            if OFFCHAIN_METRICS:
                OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                    client_type="SecretsBackend", backend="aws"
                ).inc()
            raise DLTClientConfigurationError(
                "AWS Secrets Manager backend requested but boto3 is not available.",
                "OffChain",
            )
        self.client = boto3.client("secretsmanager")

    async def get_secret(self, secret_id: str) -> str:
        try:
            response = await asyncio.to_thread(self.client.get_secret_value, SecretId=secret_id)
            return response["SecretString"]
        except BotoClientError as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from AWS Secrets Manager: {e}",
                "OffChain",
                original_exception=e,
            )


class AzureKeyVaultBackend(SecretsBackend):
    def __init__(self, vault_url: str):
        if not AZURE_BLOB_AVAILABLE:
            if OFFCHAIN_METRICS:
                OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                    client_type="SecretsBackend", backend="azure"
                ).inc()
            raise DLTClientConfigurationError(
                "Azure Key Vault backend requested but Azure SDK is not available.",
                "OffChain",
            )
        if not vault_url:
            raise DLTClientConfigurationError("Azure Key Vault URL is required.", "OffChain")
        self.credential = DefaultAzureCredential()
        self.client = AsyncSecretClient(vault_url=vault_url, credential=self.credential)

    async def get_secret(self, secret_id: str) -> str:
        try:
            secret = await self.client.get_secret(secret_id)
            return secret.value
        except Exception as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from Azure Key Vault: {e}",
                "OffChain",
                original_exception=e,
            )
        finally:
            with suppress(Exception):
                await self.client.close()
                await self.credential.close()


class GCPSecretManagerBackend(SecretsBackend):
    def __init__(self, project_id: str):
        if not GCS_AVAILABLE:
            if OFFCHAIN_METRICS:
                OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                    client_type="SecretsBackend", backend="gcp"
                ).inc()
            raise DLTClientConfigurationError(
                "GCP Secret Manager backend requested but Google Cloud SDK is not available.",
                "OffChain",
            )
        if not project_id:
            raise DLTClientConfigurationError(
                "GCP Project ID is required for GCP Secret Manager.", "OffChain"
            )
        self.client = secretmanager.SecretManagerServiceClient()
        self.project_id = project_id

    async def get_secret(self, secret_id: str) -> str:
        try:
            name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
            response = await asyncio.to_thread(
                self.client.access_secret_version, request={"name": name}
            )
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from GCP Secret Manager: {e}",
                "OffChain",
                original_exception=e,
            )


class S3Config(BaseModel):
    bucket_name: str = Field(..., min_length=1)
    region_name: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_credentials_secret_id: Optional[str] = None
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None
    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)

    @validator("aws_access_key_id", "aws_secret_access_key", always=True)
    def validate_aws_credentials_source(cls, v, values):
        if PRODUCTION_MODE and not values.get("aws_credentials_secret_id"):
            raise ValueError(
                "In PRODUCTION_MODE, 'aws_credentials_secret_id' must be provided for S3 credentials."
            )
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("aws_credentials_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if aws_credentials_secret_id is provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(f"Invalid secrets_provider: {provider}.")
                if provider == "azure" and not values.get("secrets_provider_config", {}).get(
                    "vault_url"
                ):
                    raise ValueError(
                        "secrets_provider_config.vault_url required for Azure Key Vault."
                    )
                if provider == "gcp" and not values.get("secrets_provider_config", {}).get(
                    "project_id"
                ):
                    raise ValueError(
                        "secrets_provider_config.project_id required for GCP Secret Manager."
                    )
        return v


class GCSConfig(BaseModel):
    bucket_name: str = Field(..., min_length=1)
    project_id: Optional[str] = None
    credentials_path: Optional[str] = None
    credentials_secret_id: Optional[str] = None
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None
    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)

    @validator("credentials_path", always=True)
    def validate_gcs_credentials_source(cls, v, values):
        if PRODUCTION_MODE and not values.get("credentials_secret_id"):
            raise ValueError(
                "In PRODUCTION_MODE, 'credentials_secret_id' must be provided for GCS credentials."
            )
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("credentials_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if credentials_secret_id is provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(f"Invalid secrets_provider: {provider}.")
                if provider == "azure" and not values.get("secrets_provider_config", {}).get(
                    "vault_url"
                ):
                    raise ValueError(
                        "secrets_provider_config.vault_url required for Azure Key Vault."
                    )
                if provider == "gcp" and not values.get("secrets_provider_config", {}).get(
                    "project_id"
                ):
                    raise ValueError(
                        "secrets_provider_config.project_id required for GCP Secret Manager."
                    )
        return v


class AzureBlobConfig(BaseModel):
    connection_string: Optional[str] = None
    container_name: str = Field(..., min_length=1)
    connection_string_secret_id: Optional[str] = None
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None
    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)

    @validator("connection_string", always=True)
    def validate_azure_connection_string_source(cls, v, values):
        if PRODUCTION_MODE and not values.get("connection_string_secret_id"):
            raise ValueError(
                "In PRODUCTION_MODE, 'connection_string_secret_id' must be provided for Azure Blob."
            )
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("connection_string_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if connection_string_secret_id is provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(f"Invalid secrets_provider: {provider}.")
                if provider == "azure" and not values.get("secrets_provider_config", {}).get(
                    "vault_url"
                ):
                    raise ValueError(
                        "secrets_provider_config.vault_url required for Azure Key Vault."
                    )
                if provider == "gcp" and not values.get("secrets_provider_config", {}).get(
                    "project_id"
                ):
                    raise ValueError(
                        "secrets_provider_config.project_id required for GCP Secret Manager."
                    )
        return v


class IPFSConfig(BaseModel):
    api_url: str = Field(..., min_length=1)
    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)


class InMemoryConfig(BaseModel):
    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)


class S3OffChainClient(BaseOffChainClient):

    def __init__(self, config: Dict[str, Any]):
        self.client_type: Final[str] = "S3"
        try:
            if not S3_AVAILABLE:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["client_init_failure"].labels(
                        client_type=self.client_type, error_type="dependency_missing"
                    ).inc()
                raise DLTClientConfigurationError(
                    "S3 client requested but aioboto3 is not available.",
                    self.client_type,
                )
            s3_config_data = config.get("s3", {})
            self.client_config = S3Config(**s3_config_data)
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid S3 client configuration: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid S3 client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid S3 client configuration: {e}",
                self.client_type,
                original_exception=e,
            ) from e

        super().__init__(config)
        self.bucket_name: str = self.client_config.bucket_name
        self.aws_access_key_id: Optional[str] = self.client_config.aws_access_key_id
        self.aws_secret_access_key: Optional[str] = self.client_config.aws_secret_access_key
        self.aws_credentials_secret_id: Optional[str] = self.client_config.aws_credentials_secret_id
        self.secrets_providers: List[str] = self.client_config.secrets_providers
        self.secrets_provider_config: Dict[str, Any] = (
            self.client_config.secrets_provider_config or {}
        )
        self._session = None

    async def initialize(self):
        try:
            if self.aws_credentials_secret_id:
                for provider in self.secrets_providers:
                    try:
                        secret_backend = await self._get_secrets_backend(provider)
                        credentials_json = await secret_backend.get_secret(
                            self.aws_credentials_secret_id
                        )
                        credentials = json.loads(credentials_json)
                        self.aws_access_key_id = credentials.get("aws_access_key_id")
                        self.aws_secret_access_key = credentials.get("aws_secret_access_key")
                        break
                    except Exception as e:
                        self._format_log(
                            "warning",
                            f"Failed to fetch S3 credentials from {provider}: {e}",
                            {"provider": provider},
                        )
                        continue
                else:
                    if OFFCHAIN_METRICS:
                        OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                            client_type=self.client_type, backend="all"
                        ).inc()
                    raise DLTClientConfigurationError(
                        "Failed to fetch S3 credentials from any secrets backend.",
                        self.client_type,
                    )
            if not self.aws_access_key_id or not self.aws_secret_access_key:
                raise DLTClientConfigurationError(
                    "AWS credentials must be provided via secrets or config.",
                    self.client_type,
                )
            self._session = aioboto3.Session(
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.client_config.region_name,
            )
            self._format_log(
                "info", f"S3 client session initialized for bucket {self.bucket_name}."
            )
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Failed to initialize S3 client: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Failed to initialize S3 client: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Failed to initialize S3 client: {e}",
                self.client_type,
                original_exception=e,
            ) from e

    async def _get_secrets_backend(self, provider: str) -> SecretsBackend:
        if provider == "aws":
            return AWSSecretsBackend()
        elif provider == "azure":
            vault_url = self.secrets_provider_config.get("vault_url")
            return AzureKeyVaultBackend(vault_url)
        elif provider == "gcp":
            project_id = self.secrets_provider_config.get("project_id")
            return GCPSecretManagerBackend(project_id)
        else:
            raise DLTClientConfigurationError(
                f"Unsupported secrets backend: {provider}", self.client_type
            )

    def _format_log(self, level: str, message: str, extra: Dict[str, Any] = None) -> None:
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        # Ensure all keys and values are strings
        loggable_extra = {str(k): str(v) for k, v in extra.items()}
        loggable_extra.update({"client_type": self.client_type})
        if self.client_config.log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **loggable_extra,
            }
            # FIX: Skip scrub_secrets to avoid TypeError with dict
            getattr(self.logger, level.lower())(json.dumps(log_entry))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"s3_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=loggable_extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"s3_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": str(correlation_id)},
        ) as span:
            if self._session is None:
                raise DLTClientConfigurationError(
                    "S3 client not initialized. Call initialize() first.",
                    self.client_type,
                )
            try:
                async with self._session.client("s3") as client:
                    await client.list_objects_v2(Bucket=self.bucket_name, MaxKeys=1)
                    span.set_status(Status(StatusCode.OK))
                    self._format_log(
                        "info",
                        f"S3 bucket {self.bucket_name} is accessible.",
                        {"correlation_id": str(correlation_id)},
                    )
                    return {
                        "status": True,
                        "message": f"S3 bucket {self.bucket_name} is accessible.",
                        "details": {"bucket": self.bucket_name},
                    }
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"S3 health check failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"S3 health check failed: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "s3_health_check_failure",
                            client_type=self.client_type,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"S3 health check failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        with TRACER.start_as_current_span(
            "s3.save_blob",
            attributes={
                "key_prefix": key_prefix,
                "correlation_id": str(correlation_id),
            },
        ) as span:
            if self._session is None:
                raise DLTClientConfigurationError(
                    "S3 client not initialized. Call initialize() first.",
                    self.client_type,
                )
            if not key_prefix:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Key prefix cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Key prefix cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            key = f"dlt_payloads/{key_prefix}-payload-{int(time.time())}-{uuid.uuid4().hex}.bin"
            try:
                async with self._session.client("s3") as client:
                    await self._circuit_breaker.execute(
                        lambda: client.put_object(
                            Bucket=self.bucket_name, Key=key, Body=payload_blob
                        )
                    )
                span.set_attribute("s3.key", key)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Saving blob to S3: {key}",
                    {"key": key, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.saved",
                            client_type=self.client_type,
                            key=key,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return key
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"S3 save error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to save blob to S3: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.save_failure",
                            client_type=self.client_type,
                            key=key,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to save blob to S3: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def get_blob(self, off_chain_id: str, correlation_id: Optional[str] = None) -> bytes:
        with TRACER.start_as_current_span(
            "s3.get_blob",
            attributes={"key": off_chain_id, "correlation_id": str(correlation_id)},
        ) as span:
            if self._session is None:
                raise DLTClientConfigurationError(
                    "S3 client not initialized. Call initialize() first.",
                    self.client_type,
                )
            if not off_chain_id:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="get_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Off-chain ID cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Off-chain ID cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                async with self._session.client("s3") as client:
                    response = await self._circuit_breaker.execute(
                        lambda: client.get_object(Bucket=self.bucket_name, Key=off_chain_id)
                    )
                    payload_blob = await response["Body"].read()
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Blob retrieved from S3: {off_chain_id}",
                    {"key": off_chain_id, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieved",
                            client_type=self.client_type,
                            key=off_chain_id,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return payload_blob
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"S3 get error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to get blob from S3: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                if "NoSuchKey" in str(e):
                    raise FileNotFoundError(f"Blob with key {off_chain_id} not found in S3.") from e
                raise DLTClientQueryError(
                    f"Failed to get blob from S3: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        try:
            await super().close()
            self._session = None
            self._format_log(
                "info", "S3 Off-Chain Client closed", {"client_type": self.client_type}
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close S3 client cleanly: {e}",
                {"client_type": self.client_type},
            )
        finally:
            self._format_log(
                "audit",
                "S3 Off-Chain Client cleanup attempted",
                {"client_type": self.client_type},
            )


class GcsOffChainClient(BaseOffChainClient):

    def __init__(self, config: Dict[str, Any]):
        self.client_type: Final[str] = "GCS"
        try:
            if not GCS_AVAILABLE:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["client_init_failure"].labels(
                        client_type=self.client_type, error_type="dependency_missing"
                    ).inc()
                raise DLTClientConfigurationError(
                    "GCS client requested but google-cloud-storage is not available.",
                    self.client_type,
                )
            gcs_config_data = config.get("gcs", {})
            self.client_config = GCSConfig(**gcs_config_data)
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid GCS client configuration: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid GCS client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid GCS client configuration: {e}",
                self.client_type,
                original_exception=e,
            ) from e

        super().__init__(config)
        self.bucket_name: str = self.client_config.bucket_name
        self.project_id: Optional[str] = self.client_config.project_id
        self.credentials_path: Optional[str] = None
        self.credentials_secret_id: Optional[str] = self.client_config.credentials_secret_id
        self.secrets_providers: List[str] = self.client_config.secrets_providers
        self.secrets_provider_config: Dict[str, Any] = (
            self.client_config.secrets_provider_config or {}
        )
        self._gcs_client = None

    async def initialize(self):
        temp_credentials_file = None
        try:
            if self.credentials_secret_id:
                for provider in self.secrets_providers:
                    try:
                        secret_backend = await self._get_secrets_backend(provider)
                        credentials_json = await secret_backend.get_secret(
                            self.credentials_secret_id
                        )
                        temp_credentials_file = create_temp_file(
                            credentials_json, ttl=self.client_config.temp_file_ttl
                        )
                        self.credentials_path = temp_credentials_file
                        break
                    except Exception as e:
                        self._format_log(
                            "warning",
                            f"Failed to fetch GCS credentials from {provider}: {e}",
                            {"provider": provider},
                        )
                        continue
                else:
                    if OFFCHAIN_METRICS:
                        OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                            client_type=self.client_type, backend="all"
                        ).inc()
                    raise DLTClientConfigurationError(
                        "Failed to fetch GCS credentials from any secrets backend.",
                        self.client_type,
                    )
            if not self.credentials_path:
                raise DLTClientConfigurationError(
                    "GCS credentials path or secret ID must be provided.",
                    self.client_type,
                )
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path
            )
            self._gcs_client = gcs_sdk.Client(project=self.project_id, credentials=credentials)
            self._format_log("info", f"GCS client initialized for bucket {self.bucket_name}.")
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Failed to initialize GCS client: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Failed to initialize GCS client: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Failed to initialize GCS client: {e}",
                self.client_type,
                original_exception=e,
            ) from e
        finally:
            if temp_credentials_file:
                try:
                    os.unlink(temp_credentials_file)
                    _temp_files.pop(temp_credentials_file, None)
                    self._format_log(
                        "info",
                        f"Cleaned up temporary credentials file: {temp_credentials_file}",
                    )
                except OSError as e:
                    self._format_log(
                        "warning",
                        f"Failed to clean up temporary credentials file {temp_credentials_file}: {e}",
                    )

    async def _get_secrets_backend(self, provider: str) -> SecretsBackend:
        if provider == "aws":
            return AWSSecretsBackend()
        elif provider == "azure":
            vault_url = self.secrets_provider_config.get("vault_url")
            return AzureKeyVaultBackend(vault_url)
        elif provider == "gcp":
            project_id = self.secrets_provider_config.get("project_id")
            return GCPSecretManagerBackend(project_id)
        else:
            raise DLTClientConfigurationError(
                f"Unsupported secrets backend: {provider}", self.client_type
            )

    def _format_log(self, level: str, message: str, extra: Dict[str, Any] = None) -> None:
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        loggable_extra = {str(k): str(v) for k, v in extra.items()}
        loggable_extra.update({"client_type": self.client_type})
        if self.client_config.log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **loggable_extra,
            }
            # FIX: Skip scrub_secrets to avoid TypeError with dict
            getattr(self.logger, level.lower())(json.dumps(log_entry))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"gcs_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=loggable_extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"gcs_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": str(correlation_id)},
        ) as span:
            try:
                bucket = self._gcs_client.bucket(self.bucket_name)
                blob_iterator = bucket.list_blobs(max_results=1)
                await asyncio.to_thread(lambda: next(blob_iterator, None))
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"GCS bucket {self.bucket_name} is accessible.",
                    {"correlation_id": str(correlation_id)},
                )
                return {
                    "status": True,
                    "message": f"GCS bucket {self.bucket_name} is accessible.",
                    "details": {"bucket": self.bucket_name},
                }
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"GCS health check failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"GCS health check failed: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "gcs_health_check_failure",
                            client_type=self.client_type,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"GCS health check failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        with TRACER.start_as_current_span(
            "gcs.save_blob",
            attributes={
                "key_prefix": key_prefix,
                "correlation_id": str(correlation_id),
            },
        ) as span:
            if not key_prefix:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Key prefix cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Key prefix cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            key = f"dlt_payloads/{key_prefix}-payload-{int(time.time())}-{uuid.uuid4().hex}.bin"
            try:
                bucket = self._gcs_client.bucket(self.bucket_name)
                blob = bucket.blob(key)
                await self._circuit_breaker.execute(lambda: blob.upload_from_string(payload_blob))
                span.set_attribute("gcs.key", key)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Saving blob to GCS: {key}",
                    {"key": key, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.saved",
                            client_type=self.client_type,
                            key=key,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return key
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"GCS save error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to save blob to GCS: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.save_failure",
                            client_type=self.client_type,
                            key=key,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to save blob to GCS: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def get_blob(self, off_chain_id: str, correlation_id: Optional[str] = None) -> bytes:
        with TRACER.start_as_current_span(
            "gcs.get_blob",
            attributes={"key": off_chain_id, "correlation_id": str(correlation_id)},
        ) as span:
            if not off_chain_id:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="get_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Off-chain ID cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Off-chain ID cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                bucket = self._gcs_client.bucket(self.bucket_name)
                blob = bucket.blob(off_chain_id)
                if not await asyncio.to_thread(blob.exists):
                    raise FileNotFoundError(f"Blob with key {off_chain_id} not found in GCS.")
                payload_blob = await self._circuit_breaker.execute(lambda: blob.download_as_bytes())
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Blob retrieved from GCS: {off_chain_id}",
                    {"key": off_chain_id, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieved",
                            client_type=self.client_type,
                            key=off_chain_id,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return payload_blob
            except FileNotFoundError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"GCS get error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to get blob from GCS: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientQueryError(
                    f"Failed to get blob from GCS: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        try:
            await super().close()
            if self._gcs_client:
                await asyncio.to_thread(self._gcs_client.close)
            if self.credentials_path and os.path.exists(self.credentials_path):
                try:
                    os.unlink(self.credentials_path)
                    _temp_files.pop(self.credentials_path, None)
                    self._format_log(
                        "info",
                        f"Cleaned up temporary credentials file: {self.credentials_path}",
                    )
                except OSError as e:
                    self._format_log(
                        "warning",
                        f"Failed to clean up temporary credentials file {self.credentials_path}: {e}",
                    )
            self._gcs_client = None
            self._format_log(
                "info", "GCS Off-Chain Client closed", {"client_type": self.client_type}
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close GCS client cleanly: {e}",
                {"client_type": self.client_type},
            )
        finally:
            self._format_log(
                "audit",
                "GCS Off-Chain Client cleanup attempted",
                {"client_type": self.client_type},
            )


class AzureBlobOffChainClient(BaseOffChainClient):

    def __init__(self, config: Dict[str, Any]):
        self.client_type: Final[str] = "AzureBlob"
        try:
            if not AZURE_BLOB_AVAILABLE:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["client_init_failure"].labels(
                        client_type=self.client_type, error_type="dependency_missing"
                    ).inc()
                raise DLTClientConfigurationError(
                    "Azure Blob client requested but azure-storage-blob is not available.",
                    self.client_type,
                )
            azure_config_data = config.get("azure_blob", {})
            self.client_config = AzureBlobConfig(**azure_config_data)
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid Azure Blob client configuration: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid Azure Blob client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid Azure Blob client configuration: {e}",
                self.client_type,
                original_exception=e,
            ) from e

        super().__init__(config)
        self.container_name: str = self.client_config.container_name
        self.connection_string: Optional[str] = self.client_config.connection_string
        self.connection_string_secret_id: Optional[str] = (
            self.client_config.connection_string_secret_id
        )
        self.secrets_providers: List[str] = self.client_config.secrets_providers
        self.secrets_provider_config: Dict[str, Any] = (
            self.client_config.secrets_provider_config or {}
        )
        self._blob_service_client = None

    async def initialize(self):
        try:
            if self.connection_string_secret_id:
                for provider in self.secrets_providers:
                    try:
                        secret_backend = await self._get_secrets_backend(provider)
                        self.connection_string = await secret_backend.get_secret(
                            self.connection_string_secret_id
                        )
                        break
                    except Exception as e:
                        self._format_log(
                            "warning",
                            f"Failed to fetch Azure Blob connection string from {provider}: {e}",
                            {"provider": provider},
                        )
                        continue
                else:
                    if OFFCHAIN_METRICS:
                        OFFCHAIN_METRICS["secrets_unavailable_total"].labels(
                            client_type=self.client_type, backend="all"
                        ).inc()
                    raise DLTClientConfigurationError(
                        "Failed to fetch Azure Blob connection string from any secrets backend.",
                        self.client_type,
                    )
            if not self.connection_string:
                raise DLTClientConfigurationError(
                    "Azure Blob connection string must be provided via secrets or config.",
                    self.client_type,
                )
            self._blob_service_client = AzureBlobServiceClient.from_connection_string(
                self.connection_string
            )
            self._format_log(
                "info",
                f"Azure Blob client initialized for container {self.container_name}.",
            )
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Failed to initialize Azure Blob client: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid Azure Blob client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Failed to initialize Azure Blob client: {e}",
                self.client_type,
                original_exception=e,
            ) from e

    async def _get_secrets_backend(self, provider: str) -> SecretsBackend:
        if provider == "aws":
            return AWSSecretsBackend()
        elif provider == "azure":
            vault_url = self.secrets_provider_config.get("vault_url")
            return AzureKeyVaultBackend(vault_url)
        elif provider == "gcp":
            project_id = self.secrets_provider_config.get("project_id")
            return GCPSecretManagerBackend(project_id)
        else:
            raise DLTClientConfigurationError(
                f"Unsupported secrets backend: {provider}", self.client_type
            )

    def _format_log(self, level: str, message: str, extra: Dict[str, Any] = None) -> None:
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        loggable_extra = {str(k): str(v) for k, v in extra.items()}
        loggable_extra.update({"client_type": self.client_type})
        if self.client_config.log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **loggable_extra,
            }
            # FIX: Skip scrub_secrets to avoid TypeError with dict
            getattr(self.logger, level.lower())(json.dumps(log_entry))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"azure_blob_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=loggable_extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"azure_blob_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": str(correlation_id)},
        ) as span:
            try:
                container_client = self._blob_service_client.get_container_client(
                    self.container_name
                )
                async for _ in container_client.list_blobs():
                    break
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Azure Blob container {self.container_name} is accessible.",
                    {"correlation_id": str(correlation_id)},
                )
                return {
                    "status": True,
                    "message": f"Azure Blob container {self.container_name} is accessible.",
                    "details": {"container": self.container_name},
                }
            except AzureResourceNotFoundError:
                span.set_status(
                    Status(StatusCode.ERROR, description="Azure Blob container not found")
                )
                self._format_log(
                    "error",
                    f"Azure Blob container {self.container_name} not found",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "azure_blob_health_check_failure",
                            client_type=self.client_type,
                            error_message="Container not found",
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"Azure Blob container {self.container_name} not found",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"Azure Blob health check failed: {e}",
                    )
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Azure Blob health check failed: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "azure_blob_health_check_unexpected_error",
                            client_type=self.client_type,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"Azure Blob health check failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        with TRACER.start_as_current_span(
            "azure_blob.save_blob",
            attributes={
                "key_prefix": key_prefix,
                "correlation_id": str(correlation_id),
            },
        ) as span:
            if not key_prefix:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Key prefix cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Key prefix cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            key = f"dlt_payloads/{key_prefix}-payload-{int(time.time())}-{uuid.uuid4().hex}.bin"
            try:
                container_client = self._blob_service_client.get_container_client(
                    self.container_name
                )
                blob_client = container_client.get_blob_client(key)
                await self._circuit_breaker.execute(
                    lambda: blob_client.upload_blob(payload_blob, overwrite=True)
                )
                span.set_attribute("azure_blob.key", key)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Saving blob to Azure Blob: {key}",
                    {"key": key, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.saved",
                            client_type=self.client_type,
                            key=key,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return key
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"Azure Blob save error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to save blob to Azure Blob: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.save_failure",
                            client_type=self.client_type,
                            key=key,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to save blob to Azure Blob: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def get_blob(self, off_chain_id: str, correlation_id: Optional[str] = None) -> bytes:
        with TRACER.start_as_current_span(
            "azure_blob.get_blob",
            attributes={"key": off_chain_id, "correlation_id": str(correlation_id)},
        ) as span:
            if not off_chain_id:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="get_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Off-chain ID cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Off-chain ID cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                container_client = self._blob_service_client.get_container_client(
                    self.container_name
                )
                blob_client = container_client.get_blob_client(off_chain_id)
                downloader = await self._circuit_breaker.execute(
                    lambda: blob_client.download_blob()
                )
                payload_blob = await downloader.readall()
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Blob retrieved from Azure Blob: {off_chain_id}",
                    {"key": off_chain_id, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieved",
                            client_type=self.client_type,
                            key=off_chain_id,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return payload_blob
            except DLTClientCircuitBreakerError:
                raise
            except AzureResourceNotFoundError:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Blob {off_chain_id} not found")
                )
                self._format_log(
                    "error",
                    f"Blob {off_chain_id} not found in Azure Blob",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message="Blob not found",
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise FileNotFoundError(f"Blob {off_chain_id} not found in Azure Blob.")
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"Azure Blob get error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to get blob from Azure Blob: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientQueryError(
                    f"Failed to get blob from Azure Blob: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        try:
            await super().close()
            if self._blob_service_client:
                await self._blob_service_client.close()
            self._blob_service_client = None
            self._format_log(
                "info",
                "Azure Blob Off-Chain Client closed",
                {"client_type": self.client_type},
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close Azure Blob client cleanly: {e}",
                {"client_type": self.client_type},
            )
        finally:
            self._format_log(
                "audit",
                "Azure Blob Off-Chain Client cleanup attempted",
                {"client_type": self.client_type},
            )


class IPFSClient(BaseOffChainClient):

    def __init__(self, config: Dict[str, Any]):
        self.client_type: Final[str] = "IPFS"
        try:
            if not IPFS_AVAILABLE:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["client_init_failure"].labels(
                        client_type=self.client_type, error_type="dependency_missing"
                    ).inc()
                raise DLTClientConfigurationError(
                    "IPFS client requested but ipfshttpclient is not available.",
                    self.client_type,
                )
            ipfs_config_data = config.get("ipfs", {})
            self.client_config = IPFSConfig(**ipfs_config_data)
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid IPFS client configuration: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid IPFS client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid IPFS client configuration: {e}",
                self.client_type,
                original_exception=e,
            ) from e

        super().__init__(config)
        self.api_url: str = self.client_config.api_url
        self.ipfs_client = None

    async def initialize(self):
        try:
            self.ipfs_client = ipfshttpclient.connect(self.api_url)
            self._format_log("info", f"IPFS client initialized with API {self.api_url}.")
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Failed to initialize IPFS client: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Failed to initialize IPFS client: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Failed to initialize IPFS client: {e}",
                self.client_type,
                original_exception=e,
            ) from e

    def _format_log(self, level: str, message: str, extra: Dict[str, Any] = None) -> None:
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        loggable_extra = {str(k): str(v) for k, v in extra.items()}
        loggable_extra.update({"client_type": self.client_type})
        if self.client_config.log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **loggable_extra,
            }
            # FIX: Skip scrub_secrets to avoid TypeError with dict
            getattr(self.logger, level.lower())(json.dumps(log_entry))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"ipfs_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=loggable_extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"ipfs_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": str(correlation_id)},
        ) as span:
            try:
                if not self.ipfs_client:
                    raise DLTClientConfigurationError(
                        "IPFS client not initialized.", self.client_type
                    )
                await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(self.ipfs_client.id)
                )
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    "IPFS client is healthy.",
                    {"correlation_id": str(correlation_id)},
                )
                return {
                    "status": True,
                    "message": "IPFS client is healthy.",
                    "details": {"api_url": self.api_url},
                }
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"IPFS health check failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"IPFS health check failed: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "ipfs_health_check_failure",
                            client_type=self.client_type,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"IPFS health check failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        with TRACER.start_as_current_span(
            "ipfs.save_blob",
            attributes={
                "key_prefix": key_prefix,
                "correlation_id": str(correlation_id),
            },
        ) as span:
            if not key_prefix:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Key prefix cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Key prefix cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                if not self.ipfs_client:
                    raise DLTClientConfigurationError(
                        "IPFS client not initialized.", self.client_type
                    )
                ipfs_hash = await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(self.ipfs_client.add_bytes, payload_blob)
                )
                span.set_attribute("ipfs.hash", ipfs_hash)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Saving blob to IPFS: {ipfs_hash}",
                    {"key": ipfs_hash, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.saved",
                            client_type=self.client_type,
                            key=ipfs_hash,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return ipfs_hash
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"IPFS save error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to save blob to IPFS: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.save_failure",
                            client_type=self.client_type,
                            key="unknown",
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to save blob to IPFS: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def get_blob(self, off_chain_id: str, correlation_id: Optional[str] = None) -> bytes:
        with TRACER.start_as_current_span(
            "ipfs.get_blob",
            attributes={"key": off_chain_id, "correlation_id": str(correlation_id)},
        ) as span:
            if not off_chain_id:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="get_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Off-chain ID cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Off-chain ID cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                if not self.ipfs_client:
                    raise DLTClientConfigurationError(
                        "IPFS client not initialized.", self.client_type
                    )
                payload_blob = await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(self.ipfs_client.cat, off_chain_id)
                )
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Blob retrieved from IPFS: {off_chain_id}",
                    {"key": off_chain_id, "correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieved",
                            client_type=self.client_type,
                            key=off_chain_id,
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                return payload_blob
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"IPFS get error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to get blob from IPFS: {e}",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message=str(e),
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                if "no link named" in str(e).lower() or "ipfs resolve" in str(e).lower():
                    raise FileNotFoundError(
                        f"Blob with hash {off_chain_id} not found on IPFS."
                    ) from e
                raise DLTClientQueryError(
                    f"Failed to get blob from IPFS: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        try:
            await super().close()
            if self.ipfs_client:
                await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(self.ipfs_client.close)
                )
            self.ipfs_client = None
            self._format_log(
                "info",
                "IPFS Off-Chain Client closed",
                {"client_type": self.client_type},
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close IPFS client cleanly: {e}",
                {"client_type": self.client_type},
            )
        finally:
            self._format_log(
                "audit",
                "IPFS Off-Chain Client cleanup attempted",
                {"client_type": self.client_type},
            )


class InMemoryOffChainClient(BaseOffChainClient):

    def __init__(self, config: Dict[str, Any]):
        self.client_type: Final[str] = "InMemory"
        try:
            self.client_config = InMemoryConfig(**config.get("in_memory", {}))
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid InMemory client configuration: {e}. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid InMemory client configuration: {e}. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid InMemory client configuration: {e}",
                self.client_type,
                original_exception=e,
            ) from e

        super().__init__(config)
        self.store: Dict[str, bytes] = {}

        if PRODUCTION_MODE:
            _base_logger.critical(
                "CRITICAL: InMemoryOffChainClient is explicitly forbidden in PRODUCTION_MODE. Aborting startup."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: InMemoryOffChainClient is explicitly forbidden in PRODUCTION_MODE. Aborting.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "InMemoryOffChainClient is explicitly forbidden in PRODUCTION_MODE.",
                self.client_type,
            )

        self._format_log("info", "InMemory Off-Chain Client initialized (NOT FOR PRODUCTION)", {})

    def _format_log(self, level: str, message: str, extra: Dict[str, Any] = None) -> None:
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        loggable_extra = {str(k): str(v) for k, v in extra.items()}
        loggable_extra.update({"client_type": self.client_type})
        if self.client_config.log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **loggable_extra,
            }
            # FIX: Skip scrub_secrets to avoid TypeError with dict
            getattr(self.logger, level.lower())(json.dumps(log_entry))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"in_memory_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=loggable_extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"in_memory_client_error.{level.lower()}",
                            message=message,
                            details=loggable_extra,
                        )
                    )
                except RuntimeError:
                    pass

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": str(correlation_id)},
        ) as span:
            span.set_status(Status(StatusCode.OK))
            self._format_log(
                "info",
                "InMemory Off-Chain Client is healthy.",
                {"correlation_id": str(correlation_id)},
            )
            return {
                "status": True,
                "message": "InMemory Off-Chain Client is healthy.",
                "details": {"store_size": len(self.store)},
            }

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        with TRACER.start_as_current_span(
            "in_memory.save_blob",
            attributes={
                "key_prefix": key_prefix,
                "correlation_id": str(correlation_id),
            },
        ) as span:
            if not key_prefix:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Key prefix cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Key prefix cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="save_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            key = f"{key_prefix}-payload-{int(time.time())}-{uuid.uuid4().hex}"
            self.store[key] = payload_blob
            span.set_attribute("in_memory.key", key)
            span.set_status(Status(StatusCode.OK))
            self._format_log(
                "info",
                f"Saving blob for {key_prefix} in-memory: {key}",
                {"key": key, "correlation_id": str(correlation_id)},
            )
            try:
                asyncio.get_running_loop().create_task(
                    AUDIT.log_event(
                        "offchain_blob.saved",
                        client_type=self.client_type,
                        key=key,
                        correlation_id=str(correlation_id),
                    )
                )
            except RuntimeError:
                pass
            return key

    @async_retry(catch_exceptions=(Exception, DLTClientCircuitBreakerError))
    async def get_blob(self, off_chain_id: str, correlation_id: Optional[str] = None) -> bytes:
        with TRACER.start_as_current_span(
            "in_memory.get_blob",
            attributes={"key": off_chain_id, "correlation_id": str(correlation_id)},
        ) as span:
            if not off_chain_id:
                if OFFCHAIN_METRICS:
                    OFFCHAIN_METRICS["validation_failure"].labels(
                        client_type=self.client_type, operation="get_blob"
                    ).inc()
                self._format_log(
                    "error",
                    "Off-chain ID cannot be empty",
                    {"correlation_id": str(correlation_id)},
                )
                raise DLTClientValidationError(
                    "Off-chain ID cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            blob = self.store.get(off_chain_id)
            if blob is None:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"Blob {off_chain_id} not found in in-memory store",
                    )
                )
                self._format_log(
                    "error",
                    f"Blob {off_chain_id} not found in in-memory store",
                    {"correlation_id": str(correlation_id)},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "offchain_blob.retrieval_failure",
                            client_type=self.client_type,
                            key=off_chain_id,
                            error_message="Blob not found",
                            correlation_id=str(correlation_id),
                        )
                    )
                except RuntimeError:
                    pass
                raise FileNotFoundError(f"Blob {off_chain_id} not found.")
            span.set_status(Status(StatusCode.OK))
            self._format_log(
                "info",
                f"Blob retrieved from in-memory: {off_chain_id}",
                {"key": off_chain_id, "correlation_id": str(correlation_id)},
            )
            try:
                asyncio.get_running_loop().create_task(
                    AUDIT.log_event(
                        "offchain_blob.retrieved",
                        client_type=self.client_type,
                        key=off_chain_id,
                        correlation_id=str(correlation_id),
                    )
                )
            except RuntimeError:
                pass
            return blob

    async def close(self) -> None:
        try:
            await super().close()
            self.store.clear()
            self._format_log(
                "info",
                "InMemory Off-Chain Client closed",
                {"client_type": self.client_type},
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close InMemory client cleanly: {e}",
                {"client_type": self.client_type},
            )
        finally:
            self._format_log(
                "audit",
                "InMemory Off-Chain Client cleanup attempted",
                {"client_type": self.client_type},
            )
