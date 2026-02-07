# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import datetime
import json
import os
import re
import tempfile
import urllib.parse
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Final, List, Literal, Optional, Tuple

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    validator,
)  # Re-import for local schemas

# Import base classes and utilities from siem_base
from .siem_base import (
    PRODUCTION_MODE,
    BaseSIEMClient,
    SIEMClientAuthError,
    SIEMClientConfigurationError,
    SIEMClientConnectivityError,
    SIEMClientError,
    SIEMClientPublishError,
    SIEMClientQueryError,
    _base_logger,
    alert_operator,
)

# --- Strict Dependency Checks for GCP SDKs ---
GCP_AVAILABLE = False
try:
    from google.cloud import logging as gcp_logging_sdk

    # Prefer stable v1 API
    try:
        from google.cloud import secretmanager as gcp_secretmanager  # v1 (preferred)
    except Exception:
        from google.cloud import secretmanager_v1beta1 as gcp_secretmanager  # fallback
    from google.api_core.exceptions import (
        Forbidden,
        GoogleAPICallError,
        GoogleAPIError,
        NotFound,
    )
    from google.oauth2 import service_account

    GCP_AVAILABLE = True
except ImportError as e:
    # Let the factory decide how to handle unavailable clients
    raise ImportError(
        "google-cloud-logging and/or google-cloud-secret-manager not found. GCP Logging client is unavailable."
    ) from e


# Helper to classify transient GCP errors for retry semantics
_TRANSIENT_GCP_ERROR_NAMES = {
    "ServiceUnavailable",
    "DeadlineExceeded",
    "TooManyRequests",
    "Unavailable",
    "ResourceExhausted",
    "Aborted",
    "InternalServerError",
    "GatewayTimeout",
    "BadGateway",
}


def _is_transient_gcp_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in _TRANSIENT_GCP_ERROR_NAMES


# Secrets Backend Interface
class SecretsBackend(ABC):
    """Abstract base class for secrets backends."""

    @abstractmethod
    async def get_secret(self, secret_id: str) -> str:
        """
        Retrieves a secret by ID asynchronously.
        Returns:
            str: The secret value.
        Raises:
            SIEMClientConfigurationError: If the secret cannot be retrieved.
        """
        raise NotImplementedError


class GCPSecretManagerBackend(SecretsBackend):
    """GCP Secret Manager backend."""

    def __init__(self, project_id: str):
        if not GCP_AVAILABLE:
            raise SIEMClientConfigurationError(
                "GCP Secret Manager backend requested but Google Cloud SDK is not available.",
                "GCPLogging",
            )
        if not project_id:
            raise SIEMClientConfigurationError(
                "GCP Project ID is required for GCP Secret Manager.", "GCPLogging"
            )
        self.client = gcp_secretmanager.SecretManagerServiceClient()
        self.project_id = project_id

    async def get_secret(self, secret_id: str) -> str:
        try:
            name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
            response = await asyncio.to_thread(
                self.client.access_secret_version, name=name
            )
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            raise SIEMClientConfigurationError(
                f"Failed to fetch secret from GCP Secret Manager: {e}",
                "GCPLogging",
                original_exception=e,
            )

    async def close(self):
        """Close underlying transport to avoid open channel leaks."""
        try:
            transport = getattr(self.client, "transport", None)
            if transport and hasattr(transport, "close"):
                await asyncio.to_thread(transport.close)
        except Exception:
            pass


# --- Configuration Schema for GCP Logging Client ---
class GcpLoggingConfig(BaseModel):
    """Configuration schema for GCP Cloud Logging client."""

    project_id: str = Field(..., min_length=1, description="GCP Project ID.")
    log_name: str = Field("sfe-audit-log", description="Log name.")
    credentials_path: Optional[str] = (
        None  # Path to service account key file (optional; prefer vault)
    )
    credentials_secret_id: Optional[str] = (
        None  # Secret ID for service account key JSON in secrets backend
    )
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(
        default_factory=list
    )  # Prioritized list of secrets backends
    secrets_provider_config: Optional[Dict[str, Any]] = (
        None  # Config for secrets backends (e.g., vault_url, project_id)
    )

    @validator("project_id")
    def validate_project_id_format(cls, v):
        if PRODUCTION_MODE and not re.match(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", v):
            raise ValueError(
                f"Invalid GCP Project ID format for production: {v}. Must be 6-30 characters, lowercase letters, numbers, and hyphens, and start with a letter."
            )
        return v

    @validator("log_name")
    def validate_log_name_format(cls, v):
        if PRODUCTION_MODE and not re.match(r"^[a-zA-Z0-9-._/]+$", v):
            raise ValueError(
                f"Invalid GCP Log Name format for production: {v}. Must contain letters, numbers, hyphens, underscores, dots, or slashes."
            )
        return v

    @validator("credentials_path", always=True)
    def validate_credentials_source(cls, v, values):
        if PRODUCTION_MODE:
            if not values.get("credentials_secret_id"):
                raise ValueError(
                    "In PRODUCTION_MODE, 'credentials_secret_id' must be provided for GCP credentials. Direct path/ENV are forbidden."
                )
            if v and any(s in v.lower() for s in ("dummy", "test", "mock")):
                raise ValueError(
                    "Dummy/test credentials path detected. Not allowed in production."
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
                    raise ValueError(
                        f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'."
                    )
        return v


class GcpLoggingClient(BaseSIEMClient):
    """
    GCP Cloud Logging client for sending logs via `logger.log_struct` and querying via `list_entries`.
    """

    client_type: Final[str] = "GCPLogging"
    MAX_BATCH_SIZE = 1000

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)

        try:
            gcp_config_data = config.get(self.client_type.lower(), {})
            validated_config = GcpLoggingConfig(**gcp_config_data).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid GCP Logging client configuration: {e}.",
                extra={"client_type": self.client_type},
            )
            alert_operator(
                f"CRITICAL: Invalid GCP Logging client configuration: {e}.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"Invalid GCP Logging client configuration: {e}",
                self.client_type,
                original_exception=e,
            )

        self.project_id = validated_config["project_id"]
        self.log_name = validated_config["log_name"]
        self.credentials_path = validated_config.get("credentials_path")
        self.credentials_secret_id = validated_config.get("credentials_secret_id")
        self.secrets_providers = validated_config.get("secrets_providers", [])
        self.secrets_provider_config = (
            validated_config.get("secrets_provider_config", {}) or {}
        )

        self._logging_client: Optional[gcp_logging_sdk.Client] = None
        self._credentials: Optional[service_account.Credentials] = None
        self._temp_credentials_path: Optional[str] = None  # track temp file for cleanup
        self._creds_lock = asyncio.Lock()

        self.logger.extra.update(
            {"project_id": self.project_id, "log_name": self.log_name}
        )
        self.logger.info("GcpLoggingClient initialized.")

    async def _ensure_credentials_loaded(self):
        """Loads service account credentials from secret manager to a temp file if configured."""
        if self.credentials_path or not self.credentials_secret_id:
            return
        async with self._creds_lock:
            if self.credentials_path:
                return

            last_exc: Optional[Exception] = None
            for provider_name in self.secrets_providers:
                backend: Optional[GCPSecretManagerBackend] = None
                try:
                    if provider_name == "gcp":
                        provider_proj = (
                            self.secrets_provider_config.get("gcp") or {}
                        ).get("project_id") or self.project_id
                        backend = GCPSecretManagerBackend(provider_proj)
                    else:
                        _base_logger.warning(
                            f"Secrets backend '{provider_name}' not supported for GCP credentials.",
                            extra={"client_type": self.client_type},
                        )
                        continue

                    credentials_json = await backend.get_secret(
                        self.credentials_secret_id
                    )
                    self._temp_credentials_path = os.path.join(
                        tempfile.gettempdir(), f"gcp_sa_key_{uuid.uuid4().hex}.json"
                    )
                    with open(self._temp_credentials_path, "w") as f:
                        f.write(credentials_json)
                    os.chmod(self._temp_credentials_path, 0o600)
                    self.credentials_path = self._temp_credentials_path
                    self.logger.info(
                        f"GCP credentials loaded from secrets backend: {provider_name} into temporary file.",
                        extra=self.logger.extra,
                    )
                    return
                except Exception as e:
                    last_exc = e
                    _base_logger.warning(
                        f"Failed to fetch GCP credentials from {provider_name}: {e}",
                        exc_info=True,
                        extra={"client_type": self.client_type},
                    )
                finally:
                    if backend:
                        try:
                            await backend.close()
                        except Exception:
                            pass

            alert_operator(
                "CRITICAL: Failed to load GCP credentials from secrets.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                "Failed to load GCP credentials from any configured secrets backend.",
                self.client_type,
                original_exception=last_exc,
            )

    def _encoded_log_id(self) -> str:
        """URL-encode log_id for use in logName filter (Cloud Logging API requirement)."""
        return urllib.parse.quote(self.log_name, safe="")

    async def _get_gcp_client(self) -> gcp_logging_sdk.Client:
        """Lazily initializes and returns the GCP Cloud Logging client."""
        if self._logging_client is None:
            await self._ensure_credentials_loaded()
            if self.credentials_path:
                self.logger.debug(
                    f"Initializing GCP client with credentials from {self.credentials_path}",
                    extra=self.logger.extra,
                )
                self._credentials = await asyncio.shield(
                    self._run_blocking_in_executor(
                        lambda: service_account.Credentials.from_service_account_file(
                            self.credentials_path
                        )
                    )
                )
                self._logging_client = await asyncio.shield(
                    self._run_blocking_in_executor(
                        lambda: gcp_logging_sdk.Client(
                            project=self.project_id, credentials=self._credentials
                        )
                    )
                )
            else:
                self.logger.debug(
                    "Initializing GCP client using default application credentials.",
                    extra=self.logger.extra,
                )
                self._logging_client = await asyncio.shield(
                    self._run_blocking_in_executor(
                        lambda: gcp_logging_sdk.Client(project=self.project_id)
                    )
                )
        return self._logging_client

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for GCP Cloud Logging health check."""
        try:
            client = await self._get_gcp_client()
            encoded_log = self._encoded_log_id()

            def _probe():
                it = client.list_entries(
                    page_size=1,
                    filter=f'logName="projects/{self.project_id}/logs/{encoded_log}"',
                )
                for _ in it.pages:
                    break
                return True

            await asyncio.shield(self._run_blocking_in_executor(_probe))
            self.logger.info(
                f"GCP project '{self.project_id}' and log '{self.log_name}' are accessible.",
                extra=self.logger.extra,
            )
            return True, "Successfully connected to GCP Cloud Logging."
        except Forbidden as e:
            alert_operator(
                f"CRITICAL: GCP permission denied during health check: {e}",
                level="CRITICAL",
            )
            raise SIEMClientAuthError(
                f"GCP permission denied: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except NotFound as e:
            alert_operator(
                f"CRITICAL: GCP Project '{self.project_id}' or Log '{self.log_name}' not found during health check: {e}",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"GCP project or log not found: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except GoogleAPICallError as e:
            alert_operator(
                f"CRITICAL: GCP API error during health check: {e}", level="CRITICAL"
            )
            raise SIEMClientConnectivityError(
                f"GCP API error: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(
                f"CRITICAL: Unexpected error during GCP Cloud Logging health check: {e}",
                level="CRITICAL",
            )
            raise SIEMClientError(
                f"Unexpected error during GCP Cloud Logging health check: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(
        self, log_entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Internal logic for sending a log to GCP Cloud Logging."""
        success, msg, failed_logs = await self._perform_send_logs_batch_logic(
            [log_entry]
        )
        if success:
            return True, "Log sent to GCP Cloud Logging."
        raise SIEMClientPublishError(
            f"Failed to send log to GCP Cloud Logging: {failed_logs[0]['error']}",
            self.client_type,
            details=failed_logs[0],
            correlation_id=self.logger.extra.get("correlation_id"),
        )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Sends multiple log entries to GCP Cloud Logging as a batch."""
        client = await self._get_gcp_client()
        logger = client.logger(self.log_name)

        batches = [
            log_entries[i : i + self.MAX_BATCH_SIZE]
            for i in range(0, len(log_entries), self.MAX_BATCH_SIZE)
        ]
        failed_logs: List[Dict[str, Any]] = []
        total_sent = 0

        for batch in batches:

            def _send_batch():
                with logger.batch() as b:
                    for entry in batch:
                        sev = str(entry.get("severity", "INFO")).upper()
                        b.log_struct(entry, severity=sev)
                return len(batch)

            try:
                sent = await asyncio.shield(self._run_blocking_in_executor(_send_batch))
                total_sent += sent
            except Forbidden as e:
                alert_operator(
                    f"CRITICAL: GCP permission denied while sending batch: {e}",
                    level="CRITICAL",
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])
            except GoogleAPICallError as e:
                if _is_transient_gcp_error(e):
                    alert_operator(
                        f"ERROR: Transient GCP API error while sending batch. Will retry: {e}",
                        level="ERROR",
                    )
                    raise SIEMClientConnectivityError(
                        f"Transient GCP API error while sending batch: {e}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
                alert_operator(
                    f"CRITICAL: GCP API error while sending batch: {e}",
                    level="CRITICAL",
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])
            except Exception as e:
                alert_operator(
                    f"CRITICAL: GCP Cloud Logging batch send failed: {e}",
                    level="CRITICAL",
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])

        if failed_logs:
            return (
                False,
                f"Sent {total_sent} of {len(log_entries)} logs with errors.",
                failed_logs,
            )
        return True, f"Batch of {len(log_entries)} logs sent to GCP Cloud Logging.", []

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Internal logic for querying logs from GCP Cloud Logging."""
        client = await self._get_gcp_client()

        end_time = datetime.datetime.utcnow()
        start_time = end_time - self._parse_relative_time_range_to_timedelta(time_range)
        encoded_log = self._encoded_log_id()

        filters = [
            f'logName="projects/{self.project_id}/logs/{encoded_log}"',
            f'timestamp >= "{start_time.isoformat()}Z"',
            f'timestamp <= "{end_time.isoformat()}Z"',
        ]
        if query_string:
            filters.append(query_string)

        full_filter = " AND ".join(filters)
        self.logger.debug(f"GCP Query Filter: {full_filter}", extra=self.logger.extra)

        def _fetch_entries():
            page_size = min(max(limit, 1), 1000)
            iterator = client.list_entries(
                filter=full_filter, order_by="timestamp desc", page_size=page_size
            )
            results = []
            for entry in iterator:
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

        try:
            entries = await asyncio.shield(
                self._run_blocking_in_executor(_fetch_entries)
            )
            results: List[Dict[str, Any]] = []
            for entry in entries:
                payload = entry.payload
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {"raw_payload": payload}
                elif not isinstance(payload, dict):
                    payload = {"raw_payload": str(payload)}
                payload.setdefault("severity", str(getattr(entry, "severity", "INFO")))
                ts = getattr(entry, "timestamp", None)
                if ts:
                    try:
                        payload["timestamp"] = ts.isoformat()
                    except Exception:
                        payload["timestamp"] = str(ts)
                results.append(payload)
            return results
        except Forbidden as e:
            alert_operator(
                f"CRITICAL: GCP permission denied during query: {e}", level="CRITICAL"
            )
            raise SIEMClientAuthError(
                f"GCP permission denied during query: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except NotFound as e:
            alert_operator(
                f"CRITICAL: GCP Project '{self.project_id}' or Log '{self.log_name}' not found during query: {e}",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"GCP project or log not found during query: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except GoogleAPICallError as e:
            if _is_transient_gcp_error(e):
                alert_operator(
                    f"ERROR: Transient GCP API error during query. Will retry: {e}",
                    level="ERROR",
                )
                raise SIEMClientConnectivityError(
                    f"Transient GCP API error during query: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            alert_operator(
                f"CRITICAL: GCP API error during query: {e}", level="CRITICAL"
            )
            raise SIEMClientQueryError(
                f"GCP API error during query: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except SIEMClientError:
            raise
        except Exception as e:
            alert_operator(
                f"CRITICAL: Unexpected error during GCP Cloud Logging query: {e}",
                level="CRITICAL",
            )
            raise SIEMClientQueryError(
                f"Unexpected error during GCP Cloud Logging query: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def close(self):
        """Cleans up the temporary credentials file if it was created."""
        await super().close()
        # Clean up temp credentials file
        if self._temp_credentials_path and os.path.exists(self._temp_credentials_path):
            try:
                self.logger.info(
                    f"Cleaning up temporary GCP credentials file: {self._temp_credentials_path}",
                    extra=self.logger.extra,
                )
                os.remove(self._temp_credentials_path)
            except Exception as e:
                _base_logger.warning(
                    f"Failed to remove temporary GCP credentials file: {e}",
                    extra=self.logger.extra,
                )
            finally:
                self._temp_credentials_path = None
        # Attempt to close underlying Cloud Logging transport to prevent open channel leaks
        try:
            if self._logging_client:
                transport = None
                if hasattr(self._logging_client, "_gapic_api"):
                    transport = getattr(
                        self._logging_client._gapic_api, "transport", None
                    )
                if not transport:
                    transport = getattr(self._logging_client, "transport", None)
                if transport and hasattr(transport, "close"):
                    await asyncio.to_thread(transport.close)
        except Exception:
            pass
