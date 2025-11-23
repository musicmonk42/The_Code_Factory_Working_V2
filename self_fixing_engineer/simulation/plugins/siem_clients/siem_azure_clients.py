import os
import json
import datetime
import asyncio
import base64
import hmac
import hashlib
import uuid
import re
import aiohttp
from typing import Dict, Any, List, Tuple, Optional, Callable, Literal, Final
from abc import ABC, abstractmethod

# Import base classes and utilities from siem_base
from .siem_base import (
    BaseSIEMClient,
    AiohttpClientMixin,
    SIEMClientConfigurationError,
    SIEMClientAuthError,
    SIEMClientConnectivityError,
    SIEMClientQueryError,
    SIEMClientPublishError,
    SIEMClientResponseError,
    alert_operator,
    SECRETS_MANAGER,
    PRODUCTION_MODE,
    _base_logger,
)
from pydantic import BaseModel, Field, ValidationError, HttpUrl, validator

# --- Strict Dependency Checks for Azure SDKs ---
AZURE_EVENTGRID_AVAILABLE = False
try:
    from azure.eventgrid.models import CloudEvent
    from azure.eventgrid import EventGridPublisherClient
    from azure.core.credentials import AzureKeyCredential

    AZURE_EVENTGRID_AVAILABLE = True
except ImportError as e:
    # Let factory catch ImportError
    raise ImportError("azure-eventgrid not found. Azure Event Grid client is unavailable.") from e

AZURE_SERVICEBUS_AVAILABLE = False
try:
    from azure.servicebus.aio import ServiceBusClient
    from azure.servicebus import ServiceBusMessage
    from azure.identity.aio import DefaultAzureCredential as AioDefaultAzureCredential

    AZURE_SERVICEBUS_AVAILABLE = True
except ImportError as e:
    # Let factory catch ImportError
    raise ImportError(
        "azure-servicebus or azure-identity not found. Azure Service Bus client is unavailable."
    ) from e

AZURE_MONITOR_QUERY_AVAILABLE = False
try:
    from azure.monitor.query.aio import LogsQueryClient  # Async client
    from azure.monitor.query.models import QueryWorkspaceOptions
    from azure.identity.aio import DefaultAzureCredential as AzureMonitorCredential

    AZURE_MONITOR_QUERY_AVAILABLE = True
except ImportError as e:
    # Let factory catch ImportError
    raise ImportError(
        "azure-monitor-query not found. Azure Sentinel KQL querying is unavailable."
    ) from e

# Import for Azure Key Vault (optional, used when secrets_providers include 'azure')
try:
    from azure.keyvault.secrets.aio import SecretClient as AsyncSecretClient
    from azure.identity.aio import DefaultAzureCredential as KeyVaultCredential

    AZURE_KEYVAULT_AVAILABLE = True
except ImportError:
    AZURE_KEYVAULT_AVAILABLE = False
    _base_logger.warning(
        "azure-keyvault-secrets not found. Azure Key Vault secrets backend will not be available."
    )


# --- Alerting helper (do not rebind alert_operator to async) ---
async def _alert_operator_http(message: str, level: str = "CRITICAL"):
    """Sends a message to PagerDuty/Slack."""
    pagerduty_url = os.getenv("PAGERDUTY_URL")
    slack_url = os.getenv("SLACK_WEBHOOK_URL")

    if not pagerduty_url and not slack_url:
        return

    async with aiohttp.ClientSession() as session:
        if pagerduty_url:
            try:
                pagerduty_key = await SECRETS_MANAGER.get_secret("PAGERDUTY_TOKEN")
                headers = {
                    "Authorization": f"Token token={pagerduty_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "event_action": "trigger",
                    "routing_key": await SECRETS_MANAGER.get_secret("PAGERDUTY_ROUTING_KEY"),
                    "payload": {
                        "summary": f"[SIEM ALERT - {level}] {message}",
                        "source": "siem-azure-client",
                        "severity": "critical" if level == "CRITICAL" else "warning",
                    },
                }
                await session.post(pagerduty_url, json=payload, headers=headers)
            except Exception as e:
                _base_logger.error(f"Failed to send PagerDuty alert: {e}")

        if slack_url:
            try:
                payload = {"text": f"[SIEM ALERT - {level}] {message}"}
                await session.post(slack_url, json=payload)
            except Exception as e:
                _base_logger.error(f"Failed to send Slack alert: {e}")


def _notify_ops(message: str, level: str = "CRITICAL"):
    """Log synchronously; schedule HTTP alert best-effort."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_alert_operator_http(message, level))
    except RuntimeError:
        pass
    alert_operator(message, level=level)


# Secrets Backend Interface
class SecretsBackend(ABC):
    """Abstract base class for secrets backends."""

    @abstractmethod
    async def get_secret(self, secret_id: str) -> str:
        """
        Retrieves a secret by ID asynchronously.
        Raises:
            SIEMClientConfigurationError on failure.
        """
        raise NotImplementedError


class AzureKeyVaultBackend(SecretsBackend):
    """Azure Key Vault backend."""

    def __init__(self, vault_url: str):
        if not AZURE_KEYVAULT_AVAILABLE:
            raise SIEMClientConfigurationError("Azure Key Vault SDK not available.", "AzureClient")
        if not vault_url:
            raise SIEMClientConfigurationError("Azure Key Vault URL is required.", "AzureClient")
        self._credential = KeyVaultCredential()
        self.client = AsyncSecretClient(vault_url=vault_url, credential=self._credential)

    async def get_secret(self, secret_id: str) -> str:
        try:
            secret = await self.client.get_secret(secret_id)
            return secret.value
        except Exception as e:
            raise SIEMClientConfigurationError(
                f"Failed to fetch secret from Azure Key Vault: {e}",
                "AzureClient",
                original_exception=e,
            )

    async def close(self):
        try:
            await self.client.close()
        finally:
            # Some credential types expose close; ignore if not present
            if hasattr(self._credential, "close"):
                try:
                    await self._credential.close()
                except Exception:
                    pass


# --- Configuration Schemas for Azure Clients ---
class AzureSentinelConfig(BaseModel):
    """Configuration schema for Azure Sentinel client."""

    workspace_id: str = Field(..., min_length=1, description="Azure Log Analytics Workspace ID.")
    shared_key: Optional[str] = None  # Should come from secrets manager in prod
    shared_key_secret_id: Optional[str] = None  # Secret ID for Shared Key
    log_type: str = Field("SFE_Audit_CL", description="Custom Log Table Name.")
    api_version: str = Field("2016-04-01", description="API version for Data Collector API.")
    monitor_query_endpoint: Optional[HttpUrl] = Field(
        None, description="Endpoint for Azure Monitor Query API."
    )
    use_aad_for_query: bool = Field(
        True,
        description="Use Azure AD (Managed Identity/Service Principal) for KQL querying.",
    )
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None

    @validator("workspace_id")
    def validate_workspace_id_not_dummy(cls, v):
        if PRODUCTION_MODE and not re.match(
            r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", v
        ):
            raise ValueError(f"Invalid Workspace ID format: {v}. Not allowed in production.")
        return v

    @validator("log_type")
    def validate_log_type_format(cls, v):
        if not v.endswith("_CL"):
            raise ValueError("Custom Log Table Name must end with '_CL'.")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError(
                "Invalid Log Type format. Must contain only letters, numbers, and underscores."
            )
        return v

    @validator("shared_key", always=True)
    def validate_shared_key_source(cls, v, values):
        if PRODUCTION_MODE:
            if not values.get("shared_key_secret_id"):
                raise ValueError(
                    "In PRODUCTION_MODE, 'shared_key' must be loaded via 'shared_key_secret_id'. Direct key/ENV are forbidden."
                )
            if v and (
                "dummy" in v.lower()
                or "fake" in v.lower()
                or "test" in v.lower()
                or "mock" in v.lower()
                or not re.match(r"^[A-Za-z0-9+/=]+$", v)
            ):
                raise ValueError(
                    "Dummy/fake/invalid Shared Key format detected. Not allowed in production."
                )
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("shared_key_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if shared_key_secret_id is provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(
                        f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'."
                    )
        return v


class AzureEventGridConfig(BaseModel):
    """Configuration schema for Azure Event Grid client."""

    endpoint: HttpUrl = Field(..., description="Event Grid Topic endpoint.")
    key: Optional[str] = None
    key_secret_id: Optional[str] = None
    topic_name: str = Field("sfe-events", description="Name of the topic.")
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None

    @validator("endpoint")
    def validate_endpoint_not_dummy(cls, v):
        if PRODUCTION_MODE and (
            "dummy" in str(v).lower()
            or "test" in str(v).lower()
            or "mock" in str(v).lower()
            or "example.com" in str(v).lower()
        ):
            raise ValueError(f"Dummy/test Endpoint detected: {v}. Not allowed in production.")
        return v

    @validator("key", always=True)
    def validate_key_source(cls, v, values):
        if PRODUCTION_MODE:
            if not values.get("key_secret_id"):
                raise ValueError(
                    "In PRODUCTION_MODE, 'key' must be loaded via 'key_secret_id'. Direct key/ENV are forbidden."
                )
            if v and (
                "dummy" in v.lower()
                or "fake" in v.lower()
                or "test" in v.lower()
                or "mock" in v.lower()
            ):
                raise ValueError("Dummy/fake key detected. Not allowed in production.")
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("key_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if key_secret_id is provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(
                        f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'."
                    )
        return v

    @validator("topic_name")
    def validate_topic_name_format(cls, v):
        if PRODUCTION_MODE and not re.match(r"^[a-zA-Z0-9-]{3,50}$", v):
            raise ValueError(
                f"Invalid topic name format for production: {v}. Must be 3-50 characters, letters, numbers, and hyphens."
            )
        return v


class AzureServiceBusConfig(BaseModel):
    """Configuration schema for Azure Service Bus client."""

    connection_string: Optional[str] = None
    connection_string_secret_id: Optional[str] = None
    queue_name: Optional[str] = None
    topic_name: Optional[str] = None
    namespace_fqdn: Optional[str] = None
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
    secrets_provider_config: Optional[Dict[str, Any]] = None

    @validator("connection_string", always=True)
    def validate_connection_string_source(cls, v, values):
        if PRODUCTION_MODE:
            if not values.get("connection_string_secret_id") and not values.get("namespace_fqdn"):
                raise ValueError(
                    "In PRODUCTION_MODE, either 'connection_string_secret_id' or 'namespace_fqdn' must be provided. Direct string/ENV are forbidden."
                )
            if v and ("dummy" in v.lower() or "test" in v.lower() or "mock" in v.lower()):
                raise ValueError(
                    "Dummy/test Connection String detected. Not allowed in production."
                )
        return v

    @validator("queue_name", "topic_name", always=True)
    def validate_queue_or_topic(cls, v, values):
        if not values.get("queue_name") and not values.get("topic_name"):
            raise ValueError("Either 'queue_name' or 'topic_name' must be configured.")
        if values.get("queue_name") and values.get("topic_name"):
            raise ValueError("Only one of 'queue_name' or 'topic_name' can be configured.")
        return v

    @validator("namespace_fqdn")
    def validate_namespace_fqdn_not_dummy(cls, v):
        if (
            PRODUCTION_MODE
            and v
            and (
                "dummy" in v.lower()
                or "test" in v.lower()
                or "mock" in v.lower()
                or "example.com" in v.lower()
            )
        ):
            raise ValueError(f"Dummy/test Namespace FQDN detected: {v}. Not allowed in production.")
        return v

    @validator("queue_name", "topic_name")
    def validate_name_format(cls, v, field):
        if PRODUCTION_MODE and v and not re.match(r"^[a-zA-Z0-9-._]{1,260}$", v):
            raise ValueError(
                f"Invalid {field.name} format for production: {v}. Must be 1-260 characters, letters, numbers, hyphens, dots, or underscores."
            )
        return v


class AzureSentinelClient(AiohttpClientMixin, BaseSIEMClient):
    """
    Azure Sentinel (Log Analytics) client for sending logs via Data Collector API and querying via Azure Monitor Query SDK.
    """

    client_type: Final[str] = "AzureSentinel"
    MAX_BATCH_BYTES = 30 * 1024 * 1024  # 30 MB per POST

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        try:
            azure_sentinel_config_data = config.get(self.client_type.lower(), {})
            validated_config = AzureSentinelConfig(**azure_sentinel_config_data).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(f"CRITICAL: Invalid Azure Sentinel client configuration: {e}.")
            _notify_ops(
                f"CRITICAL: Invalid Azure Sentinel client configuration: {e}.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"Invalid Azure Sentinel client configuration: {e}",
                self.client_type,
                original_exception=e,
            )

        self.workspace_id = validated_config["workspace_id"]
        self.shared_key = validated_config.get("shared_key")
        self.shared_key_secret_id = validated_config.get("shared_key_secret_id")
        self.secrets_providers = validated_config.get("secrets_providers", [])
        self.secrets_provider_config = validated_config.get("secrets_provider_config", {}) or {}
        self.log_type = validated_config["log_type"]
        self.api_version = validated_config["api_version"]
        self.monitor_query_endpoint = validated_config.get("monitor_query_endpoint")
        self.use_aad_for_query = validated_config["use_aad_for_query"]

        self._logs_query_client: Optional[LogsQueryClient] = None
        self._azure_monitor_credential: Optional[AzureMonitorCredential] = None

        self.logger.extra.update({"workspace_id": self.workspace_id, "log_type": self.log_type})
        self.logger.info("AzureSentinelClient initialized.")

    async def _ensure_shared_key_loaded(self):
        if self.shared_key:
            return
        if not self.shared_key_secret_id:
            raise SIEMClientConfigurationError(
                "Shared key not configured; shared_key_secret_id is required.",
                self.client_type,
            )
        last_exc: Optional[Exception] = None
        for provider in self.secrets_providers:
            try:
                if provider == "azure":
                    vault_url = (self.secrets_provider_config.get("azure") or {}).get("vault_url")
                    if not vault_url:
                        raise ValueError(
                            "Azure secrets provider configured but vault_url is missing."
                        )
                    backend = AzureKeyVaultBackend(vault_url=vault_url)
                    try:
                        self.shared_key = await backend.get_secret(self.shared_key_secret_id)
                        return
                    finally:
                        await backend.close()
                else:
                    _base_logger.warning(
                        f"Secrets backend '{provider}' not supported for Azure Sentinel shared key.",
                        extra={"client_type": self.client_type},
                    )
            except Exception as e:
                last_exc = e
                _base_logger.warning(
                    f"Failed to fetch Azure Sentinel shared key from {provider}: {e}",
                    exc_info=True,
                    extra={"client_type": self.client_type},
                )
        _notify_ops(
            "CRITICAL: Failed to load Azure Sentinel shared key from secrets.",
            level="CRITICAL",
        )
        raise SIEMClientConfigurationError(
            "Failed to load Azure Sentinel shared key from any configured secrets backend.",
            self.client_type,
            original_exception=last_exc,
        )

    async def _get_logs_query_client(self) -> LogsQueryClient:
        """Lazily initializes and returns the Azure Monitor LogsQueryClient."""
        if self._logs_query_client is None:
            if not self.use_aad_for_query:
                _notify_ops(
                    "CRITICAL: Azure Sentinel KQL querying requires Azure AD.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    "KQL querying without Azure AD is not supported for security reasons.",
                    self.client_type,
                )
            self._azure_monitor_credential = AzureMonitorCredential()
            self._logs_query_client = LogsQueryClient(
                self._azure_monitor_credential, endpoint=self.monitor_query_endpoint
            )
            self.logger.info(
                "Azure Monitor Query client initialized with Azure AD credentials.",
                extra=self.logger.extra,
            )
        return self._logs_query_client

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for Azure Sentinel health check."""
        await self._ensure_shared_key_loaded()

        try:
            # Test Data Collector API connectivity (ingestion path)
            rfc1123date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
            content_type = "application/json"
            dummy_body = json.dumps([{"test_event": "health_check"}]).encode("utf-8")
            content_length = len(dummy_body)
            string_to_sign = (
                f"POST\n{content_length}\n{content_type}\nx-ms-date:{rfc1123date}\n/api/logs"
            )
            decoded_shared_key = base64.b64decode(self.shared_key)
            hashed_string = hmac.new(
                decoded_shared_key, string_to_sign.encode("utf-8"), hashlib.sha256
            ).digest()
            signature = base64.b64encode(hashed_string)
            headers = {
                "Content-Type": content_type,
                "Authorization": f"SharedKey {self.workspace_id}:{signature.decode('utf-8')}",
                "Log-Type": "HealthCheck_CL",
                "x-ms-date": rfc1123date,
            }
            api_url = f"https://{self.workspace_id}.ods.opinsights.azure.com/api/logs?api-version={self.api_version}"
            session = await self._get_session()
            async with asyncio.shield(
                session.post(api_url, headers=headers, data=dummy_body, timeout=self.timeout)
            ) as response:
                if response.status not in [200, 202]:
                    response_text = await response.text()
                    _notify_ops(
                        f"CRITICAL: Azure Sentinel Data Collector API health check failed: {response.status}: {response_text}",
                        level="CRITICAL",
                    )
                    raise SIEMClientResponseError(
                        f"Azure Sentinel Data Collector API responded with status {response.status}: {response_text}",
                        self.client_type,
                        response.status,
                        response_text,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
            self.logger.info(
                "Azure Sentinel Data Collector API is reachable.",
                extra=self.logger.extra,
            )

            # Test KQL client connectivity (query path)
            client = await self._get_logs_query_client()
            await asyncio.shield(
                client.query_workspace(
                    workspace_id=self.workspace_id,
                    query="Heartbeat | take 1",
                    timespan=(
                        datetime.datetime.utcnow() - datetime.timedelta(minutes=5),
                        datetime.datetime.utcnow(),
                    ),
                    options=QueryWorkspaceOptions(wait=True),
                )
            )
            self.logger.info("Azure Sentinel KQL client is functional.", extra=self.logger.extra)
            return True, "Azure Sentinel Data Collector API and KQL client are healthy."
        except Exception as e:
            _notify_ops(f"CRITICAL: Azure Sentinel health check failed: {e}", level="CRITICAL")
            raise SIEMClientConnectivityError(
                f"Azure Sentinel health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(self, log_entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Send a log to Azure Log Analytics via Data Collector API."""
        success, msg, failed_logs = await self._perform_send_logs_batch_logic([log_entry])
        if success:
            return True, "Log sent to Azure Sentinel/Log Analytics."
        raise SIEMClientPublishError(
            f"Failed to send log to Azure Sentinel: {failed_logs[0]['error']}",
            self.client_type,
            details=failed_logs[0],
            correlation_id=self.logger.extra.get("correlation_id"),
        )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Send multiple log entries to Azure Log Analytics via Data Collector API."""
        await self._ensure_shared_key_loaded()
        session = await self._get_session()

        rfc1123date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        content_type = "application/json"

        batches: List[List[str]] = []
        current_batch_events: List[str] = []
        current_batch_size = 0

        for log_entry in log_entries:
            event_with_meta = {
                "hostname": os.getenv("HOSTNAME", "unknown_host"),
                "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                **log_entry,
            }
            event_body = json.dumps(event_with_meta)
            event_size = len(event_body.encode("utf-8"))
            if current_batch_size + event_size > self.MAX_BATCH_BYTES:
                if current_batch_events:
                    batches.append(current_batch_events)
                current_batch_events = [event_body]
                current_batch_size = event_size
            else:
                current_batch_events.append(event_body)
                current_batch_size += event_size

        if current_batch_events:
            batches.append(current_batch_events)

        failed_logs: List[Dict[str, Any]] = []
        total_sent = 0

        for batch_events in batches:
            body = f"[{','.join(batch_events)}]"
            content_length = len(body.encode("utf-8"))
            string_to_sign = (
                f"POST\n{content_length}\n{content_type}\nx-ms-date:{rfc1123date}\n/api/logs"
            )
            try:
                decoded_shared_key = base64.b64decode(self.shared_key)
            except Exception as e:
                _notify_ops(
                    f"CRITICAL: Invalid Shared Key format during batch send: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientAuthError(
                    f"Invalid Shared Key format: {e}. Must be Base64 encoded.",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )

            hashed_string = hmac.new(
                decoded_shared_key, string_to_sign.encode("utf-8"), hashlib.sha256
            ).digest()
            signature = base64.b64encode(hashed_string)
            headers = {
                "Content-Type": content_type,
                "Authorization": f"SharedKey {self.workspace_id}:{signature.decode('utf-8')}",
                "Log-Type": self.log_type,
                "x-ms-date": rfc1123date,
                "time-generated-field": "timestamp_utc",
            }
            api_url = f"https://{self.workspace_id}.ods.opinsights.azure.com/api/logs?api-version={self.api_version}"

            try:
                async with asyncio.shield(
                    session.post(api_url, headers=headers, data=body, timeout=self.timeout)
                ) as response:
                    status_code = response.status
                    response_text = await response.text()
                    if status_code >= 400:
                        _notify_ops(
                            f"CRITICAL: Azure Sentinel Data Collector API rejected batch with status {status_code}: {response_text}",
                            level="CRITICAL",
                        )
                        failed_logs.extend(
                            [{"log": log, "error": response_text} for log in batch_events]
                        )
                    else:
                        total_sent += len(batch_events)
            except Exception as e:
                _notify_ops(
                    f"CRITICAL: Azure Sentinel batch log send failed: {e}",
                    level="CRITICAL",
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch_events])

        if failed_logs:
            return (
                False,
                f"Sent {total_sent} of {len(log_entries)} logs with errors.",
                failed_logs,
            )
        return (
            True,
            f"Batch of {len(log_entries)} logs sent to Azure Sentinel/Log Analytics.",
            [],
        )

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Query logs from Azure Log Analytics using KQL."""
        if not self.workspace_id:
            _notify_ops(
                "CRITICAL: Azure Workspace ID is required for KQL querying.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                "Azure Workspace ID is required for KQL querying.",
                self.client_type,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        if not self.use_aad_for_query:
            _notify_ops(
                "CRITICAL: KQL querying without Azure AD is not supported.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                "KQL querying without Azure AD is not supported for security reasons.",
                self.client_type,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

        client = await self._get_logs_query_client()

        end_time = datetime.datetime.utcnow()
        start_time = end_time - self._parse_relative_time_range_to_timedelta(time_range)

        try:
            full_kql_query = f"{self.log_type} | {query_string} | take {limit}"
            self.logger.debug(f"Executing KQL: {full_kql_query}", extra=self.logger.extra)
            response = await asyncio.shield(
                client.query_workspace(
                    workspace_id=self.workspace_id,
                    query=full_kql_query,
                    timespan=(start_time, end_time),
                    options=QueryWorkspaceOptions(wait=True),
                )
            )
            parsed_results: List[Dict[str, Any]] = []
            for table in response.tables:
                col_names = [col.name for col in table.columns]
                for row in table.rows:
                    parsed_results.append(dict(zip(col_names, row)))
            return parsed_results
        except Exception as e:
            if any(x in str(e) for x in ("AuthenticationFailed", "Unauthorized", "AADSTS")):
                _notify_ops(
                    f"CRITICAL: Azure Monitor Query authentication failed: {e}",
                    level="CRITICAL",
                )
                raise SIEMClientAuthError(
                    f"Azure Monitor Query authentication failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            elif any(
                x in str(e) for x in ("Connection refused", "Failed to establish a new connection")
            ):
                _notify_ops(
                    f"CRITICAL: Azure Monitor Query connection error: {e}",
                    level="CRITICAL",
                )
                raise SIEMClientConnectivityError(
                    f"Azure Monitor Query connection error: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            else:
                _notify_ops(f"CRITICAL: Failed to query Azure Sentinel: {e}", level="CRITICAL")
                raise SIEMClientQueryError(
                    f"Failed to query Azure Sentinel: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )

    async def close(self):
        """Closes the Azure Monitor LogsQueryClient session if it was initialized."""
        await super().close()
        if self._logs_query_client:
            try:
                await self._logs_query_client.close()  # Newer SDKs may support close; ignore if absent
            except Exception:
                pass
        if self._azure_monitor_credential and hasattr(self._azure_monitor_credential, "close"):
            try:
                await self._azure_monitor_credential.close()
            except Exception:
                pass
        if self._logs_query_client or self._azure_monitor_credential:
            self.logger.debug(
                f"{self.client_type} LogsQueryClient and credential closed.",
                extra=self.logger.extra,
            )
        self._logs_query_client = None
        self._azure_monitor_credential = None


class AzureEventGridClient(BaseSIEMClient):
    """
    Azure Event Grid client for publishing events. Does not support querying.
    """

    client_type: Final[str] = "AzureEventGrid"

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        try:
            eventgrid_config_data = config.get(self.client_type.lower(), {})
            validated_config = AzureEventGridConfig(**eventgrid_config_data).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(f"CRITICAL: Invalid Azure Event Grid client configuration: {e}.")
            _notify_ops(
                f"CRITICAL: Invalid Azure Event Grid client configuration: {e}.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"Invalid Azure Event Grid client configuration: {e}",
                self.client_type,
                original_exception=e,
            )

        self.endpoint = validated_config["endpoint"]
        self.key = validated_config.get("key")
        self.key_secret_id = validated_config.get("key_secret_id")
        self.secrets_providers = validated_config.get("secrets_providers", [])
        self.secrets_provider_config = validated_config.get("secrets_provider_config", {}) or {}

        self._event_grid_publisher_client = EventGridPublisherClient(
            endpoint=str(self.endpoint),
            credential=AzureKeyCredential(
                self.key or "DUMMY"
            ),  # placeholder; will set real key before send
        )
        self.logger.extra.update(
            {"endpoint": str(self.endpoint), "topic": validated_config["topic_name"]}
        )
        self.logger.info("AzureEventGridClient initialized.")

    async def _ensure_key_loaded(self):
        if self.key:
            return
        if not self.key_secret_id:
            raise SIEMClientConfigurationError(
                "Event Grid key not configured; key_secret_id is required.",
                self.client_type,
            )
        last_exc: Optional[Exception] = None
        for provider in self.secrets_providers:
            try:
                if provider == "azure":
                    vault_url = (self.secrets_provider_config.get("azure") or {}).get("vault_url")
                    if not vault_url:
                        raise ValueError(
                            "Azure secrets provider configured but vault_url is missing."
                        )
                    backend = AzureKeyVaultBackend(vault_url=vault_url)
                    try:
                        self.key = await backend.get_secret(self.key_secret_id)
                        # Update credential on the client
                        self._event_grid_publisher_client = EventGridPublisherClient(
                            endpoint=str(self.endpoint),
                            credential=AzureKeyCredential(self.key),
                        )
                        return
                    finally:
                        await backend.close()
                else:
                    _base_logger.warning(
                        f"Secrets backend '{provider}' not supported for Event Grid key.",
                        extra={"client_type": self.client_type},
                    )
            except Exception as e:
                last_exc = e
                _base_logger.warning(
                    f"Failed to fetch Event Grid key from {provider}: {e}",
                    exc_info=True,
                    extra={"client_type": self.client_type},
                )
        _notify_ops("CRITICAL: Failed to load Event Grid key from secrets.", level="CRITICAL")
        raise SIEMClientConfigurationError(
            "Failed to load Event Grid key from any configured secrets backend.",
            self.client_type,
            original_exception=last_exc,
        )

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """No direct health check API; attempt a dummy send."""
        try:
            await self._ensure_key_loaded()
            dummy_event = CloudEvent(
                source="/sfesystem/healthcheck",
                type="Azure.HealthCheckEvent",
                data={},
                time=datetime.datetime.utcnow().isoformat() + "Z",
                id=str(uuid.uuid4()),
            )
            await asyncio.shield(
                self._run_blocking_in_executor(
                    lambda: self._event_grid_publisher_client.send([dummy_event])
                )
            )
            self.logger.info(
                f"Successfully sent dummy CloudEvent for {self.client_type} health check.",
                extra=self.logger.extra,
            )
            return (
                True,
                "Azure Event Grid client initialized and connectivity tested via dummy send.",
            )
        except Exception as e:
            _notify_ops(f"CRITICAL: Azure Event Grid health check failed: {e}", level="CRITICAL")
            raise SIEMClientConnectivityError(
                f"Azure Event Grid health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(self, log_entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Publish a single event to Azure Event Grid."""
        await self._ensure_key_loaded()
        event_id = log_entry.get("event_id", str(uuid.uuid4()))
        event_type = log_entry.get("event_type", "GenericSFEEvent")
        subject = f"sfe/audit/{event_type}"
        event_time = datetime.datetime.utcnow().isoformat() + "Z"
        cloud_event = CloudEvent(
            id=event_id,
            source=f"/sfesystem/client/{self.client_type}",
            data=log_entry,
            type=event_type,
            time=event_time,
            specversion="1.0",
            subject=subject,
            datacontenttype="application/json",
        )
        try:
            await asyncio.shield(
                self._run_blocking_in_executor(
                    lambda: self._event_grid_publisher_client.send([cloud_event])
                )
            )
            return True, "Log published to Azure Event Grid."
        except Exception as e:
            _notify_ops(f"CRITICAL: Azure Event Grid log send failed: {e}", level="CRITICAL")
            if "AuthenticationFailed" in str(e) or "Unauthorized" in str(e):
                raise SIEMClientAuthError(
                    f"Azure Event Grid authentication failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            elif "Connection refused" in str(e) or "Failed to establish a new connection" in str(e):
                raise SIEMClientConnectivityError(
                    f"Azure Event Grid connection error: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            else:
                raise SIEMClientPublishError(
                    f"Failed to publish log to Azure Event Grid: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Publish multiple events to Azure Event Grid."""
        await self._ensure_key_loaded()
        batch_events: List[CloudEvent] = []
        for log_entry in log_entries:
            event_id = log_entry.get("event_id", str(uuid.uuid4()))
            event_type = log_entry.get("event_type", "GenericSFEEvent")
            subject = f"sfe/audit/{event_type}"
            event_time = datetime.datetime.utcnow().isoformat() + "Z"
            batch_events.append(
                CloudEvent(
                    id=event_id,
                    source=f"/sfesystem/client/{self.client_type}",
                    data=log_entry,
                    type=event_type,
                    time=event_time,
                    specversion="1.0",
                    subject=subject,
                    datacontenttype="application/json",
                )
            )
        try:
            await asyncio.shield(
                self._run_blocking_in_executor(
                    lambda: self._event_grid_publisher_client.send(batch_events)
                )
            )
            return (
                True,
                f"Batch of {len(log_entries)} logs published to Azure Event Grid.",
                [],
            )
        except Exception as e:
            _notify_ops(
                f"CRITICAL: Azure Event Grid batch log send failed: {e}",
                level="CRITICAL",
            )
            raise SIEMClientPublishError(
                f"Failed to publish Azure Event Grid batch: {e}",
                self.client_type,
                original_exception=e,
                details={"batch_size": len(log_entries)},
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def query_logs(
        self,
        query_string: str,
        time_range: str = "24h",
        limit: int = 100,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Querying is not supported by Azure Event Grid (it's an event bus)."""
        raise NotImplementedError(
            f"Querying is not supported by {self.client_type} client (event bus)."
        )


class AzureServiceBusClient(BaseSIEMClient):
    """
    Azure Service Bus client for sending messages to queues or topics. Does not support querying.
    """

    client_type: Final[str] = "AzureServiceBus"

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        try:
            servicebus_config_data = config.get(self.client_type.lower(), {})
            validated_config = AzureServiceBusConfig(**servicebus_config_data).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(f"CRITICAL: Invalid Azure Service Bus client configuration: {e}.")
            _notify_ops(
                f"CRITICAL: Invalid Azure Service Bus client configuration: {e}.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                f"Invalid Azure Service Bus client configuration: {e}",
                self.client_type,
                original_exception=e,
            )

        self.connection_string = validated_config.get("connection_string")
        self.connection_string_secret_id = validated_config.get("connection_string_secret_id")
        self.queue_name = validated_config.get("queue_name")
        self.topic_name = validated_config.get("topic_name")
        self.namespace_fqdn = validated_config.get("namespace_fqdn")
        self.secrets_providers = validated_config.get("secrets_providers", [])
        self.secrets_provider_config = validated_config.get("secrets_provider_config", {}) or {}

        self._service_bus_client: Optional[ServiceBusClient] = None
        self.logger.extra.update({"queue_name": self.queue_name, "topic_name": self.topic_name})
        self.logger.info("AzureServiceBusClient initialized.")

    async def _ensure_connection_string_loaded(self):
        if self.connection_string or self.namespace_fqdn:
            return
        if not self.connection_string_secret_id:
            raise SIEMClientConfigurationError(
                "Service Bus connection string not configured; connection_string_secret_id or namespace_fqdn required.",
                self.client_type,
            )
        last_exc: Optional[Exception] = None
        for provider in self.secrets_providers:
            try:
                if provider == "azure":
                    vault_url = (self.secrets_provider_config.get("azure") or {}).get("vault_url")
                    if not vault_url:
                        raise ValueError(
                            "Azure secrets provider configured but vault_url is missing."
                        )
                    backend = AzureKeyVaultBackend(vault_url=vault_url)
                    try:
                        self.connection_string = await backend.get_secret(
                            self.connection_string_secret_id
                        )
                        return
                    finally:
                        await backend.close()
                else:
                    _base_logger.warning(
                        f"Secrets backend '{provider}' not supported for Service Bus connection string.",
                        extra={"client_type": self.client_type},
                    )
            except Exception as e:
                last_exc = e
                _base_logger.warning(
                    f"Failed to fetch Service Bus connection string from {provider}: {e}",
                    exc_info=True,
                    extra={"client_type": self.client_type},
                )
        _notify_ops(
            "CRITICAL: Failed to load Service Bus connection string from secrets.",
            level="CRITICAL",
        )
        raise SIEMClientConfigurationError(
            "Failed to load Service Bus connection string from any configured secrets backend.",
            self.client_type,
            original_exception=last_exc,
        )

    async def _get_servicebus_client(self) -> ServiceBusClient:
        """Lazily initializes and returns the Azure Service Bus client."""
        if self._service_bus_client is None or self._service_bus_client.closed:
            await self._ensure_connection_string_loaded()
            if self.connection_string:
                self.logger.info(
                    "Initializing Azure Service Bus client with connection string.",
                    extra=self.logger.extra,
                )
                # Creation is cheap; do not offload to executor
                self._service_bus_client = ServiceBusClient.from_connection_string(
                    self.connection_string
                )
            elif self.namespace_fqdn:
                self.logger.info(
                    "Initializing Azure Service Bus client with Azure AD credentials (namespace FQDN).",
                    extra=self.logger.extra,
                )
                credential = AioDefaultAzureCredential()
                self._service_bus_client = ServiceBusClient(
                    fully_qualified_namespace=self.namespace_fqdn, credential=credential
                )
            else:
                _notify_ops(
                    "CRITICAL: Azure Service Bus client cannot be initialized: No connection string or namespace FQDN provided.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    "Azure Service Bus client cannot be initialized: No connection string or namespace FQDN provided.",
                    self.client_type,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
        return self._service_bus_client

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for Azure Service Bus health check."""
        try:
            client = await self._get_servicebus_client()
            if self.queue_name:
                receiver = client.get_queue_receiver(queue_name=self.queue_name)
                async with receiver:
                    await receiver.peek_messages(max_messages=1)
                self.logger.info(
                    f"Azure Service Bus Queue '{self.queue_name}' is reachable.",
                    extra=self.logger.extra,
                )
            elif self.topic_name:
                sender = client.get_topic_sender(topic_name=self.topic_name)
                async with sender:
                    # No direct operation required; opening/closing validates link setup
                    pass
                self.logger.info(
                    f"Azure Service Bus Topic '{self.topic_name}' is reachable.",
                    extra=self.logger.extra,
                )
            else:
                _notify_ops(
                    "CRITICAL: Azure Service Bus client configured without queue or topic name.",
                    level="CRITICAL",
                )
                return False, "No queue or topic configured."
            return True, "Azure Service Bus is reachable."
        except Exception as e:
            _notify_ops(
                f"CRITICAL: Azure Service Bus health check failed: {e}",
                level="CRITICAL",
            )
            raise SIEMClientConnectivityError(
                f"Failed to connect to Azure Service Bus: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(self, log_entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Send a log to Azure Service Bus."""
        client = await self._get_servicebus_client()
        body = json.dumps(log_entry).encode("utf-8")
        message = ServiceBusMessage(body)
        try:
            if self.queue_name:
                sender = client.get_queue_sender(queue_name=self.queue_name)
                async with sender:
                    await sender.send_messages(message)
                return True, f"Log sent to Azure Service Bus Queue '{self.queue_name}'."
            elif self.topic_name:
                sender = client.get_topic_sender(topic_name=self.topic_name)
                async with sender:
                    await sender.send_messages(message)
                return True, f"Log sent to Azure Service Bus Topic '{self.topic_name}'."
            else:
                _notify_ops(
                    "CRITICAL: Azure Service Bus client configured without queue or topic name for sending.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    "Neither queue_name nor topic_name is configured for Azure Service Bus.",
                    self.client_type,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
        except Exception as e:
            _notify_ops(f"CRITICAL: Azure Service Bus log send failed: {e}", level="CRITICAL")
            if any(x in str(e) for x in ("Authentication", "Unauthorized", "AADSTS")):
                raise SIEMClientAuthError(
                    f"Azure Service Bus authentication failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            elif any(x in str(e) for x in ("Connection", "Host")):
                raise SIEMClientConnectivityError(
                    f"Azure Service Bus connection error: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            else:
                raise SIEMClientPublishError(
                    f"Failed to send log to Azure Service Bus: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Send multiple logs to Azure Service Bus as a batch (multiple messages)."""
        client = await self._get_servicebus_client()
        try:
            if self.queue_name:
                sender = client.get_queue_sender(queue_name=self.queue_name)
            elif self.topic_name:
                sender = client.get_topic_sender(topic_name=self.topic_name)
            else:
                _notify_ops(
                    "CRITICAL: Azure Service Bus client configured without queue or topic name for batch sending.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    "Neither queue_name nor topic_name is configured for Azure Service Bus.",
                    self.client_type,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )

            async with sender:
                messages = [
                    ServiceBusMessage(json.dumps(log).encode("utf-8")) for log in log_entries
                ]
                await sender.send_messages(messages)
            return (
                True,
                f"Batch of {len(log_entries)} logs sent to Azure Service Bus.",
                [],
            )
        except Exception as e:
            _notify_ops(
                f"CRITICAL: Azure Service Bus batch log send failed: {e}",
                level="CRITICAL",
            )
            raise SIEMClientPublishError(
                f"Failed to send Azure Service Bus batch: {e}",
                self.client_type,
                original_exception=e,
                details={"batch_size": len(log_entries)},
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def query_logs(
        self,
        query_string: str,
        time_range: str = "24h",
        limit: int = 100,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Querying is not supported by Azure Service Bus (it's a message bus)."""
        raise NotImplementedError(
            f"Querying is not supported by {self.client_type} client (message bus)."
        )

    async def close(self):
        """Closes the Azure Service Bus client."""
        await super().close()
        if self._service_bus_client and not self._service_bus_client.closed:
            self.logger.info(
                f"Closing Service Bus client for {self.client_type}.",
                extra=self.logger.extra,
            )
            await self._service_bus_client.close()
            self._service_bus_client = None
