# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import datetime
import json
import os
import re
import time
from typing import Any, Callable, Dict, Final, List, Optional, Tuple

from aiohttp import BasicAuth, ClientError
from pydantic import (  # Re-import for local schemas
    BaseModel,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
)

# Import base classes and utilities from siem_base
from .siem_base import (
    PRODUCTION_MODE,
    SECRETS_MANAGER,
    AiohttpClientMixin,
    BaseSIEMClient,
    SIEMClientConfigurationError,
    SIEMClientConnectivityError,
    SIEMClientPublishError,
    SIEMClientQueryError,
    SIEMClientResponseError,
    _base_logger,
    alert_operator,
)


# Helpers
def _is_transient_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


async def _get_secret(key: str, default: Any = None, *, required: bool = False) -> Any:
    # Supports both sync and async SECRETS_MANAGER.get_secret implementations
    val = (
        SECRETS_MANAGER.get_secret(key, default, required=required)
        if default is not None
        else SECRETS_MANAGER.get_secret(key, required=required)
    )
    return await _maybe_await(val)


# --- Configuration Schemas for Generic Clients ---
class SplunkConfig(BaseModel):
    """Configuration schema for Splunk client."""

    url: HttpUrl = Field(
        ...,
        description="Splunk HEC endpoint URL (usually .../services/collector/event).",
    )
    token: str = Field(
        ..., min_length=1, description="Splunk HEC authentication token."
    )
    source: str = Field("sfe_audit", description="Event source.")
    sourcetype: str = Field("_json", description="Event sourcetype.")
    index: Optional[str] = Field(None, description="Splunk index to send data to.")

    @field_validator("url")
    @classmethod
    def validate_url_security_and_dummy(cls, v):
        if PRODUCTION_MODE:
            v_str = str(v).lower()
            if not v_str.startswith("https"):
                raise ValueError("Splunk URL must use HTTPS in PRODUCTION_MODE.")
            if any(s in v_str for s in ("dummy", "mock", "test", "example.com")):
                raise ValueError(
                    f"Dummy/test URL detected: {v}. Not allowed in production."
                )
        return v

    @field_validator("token")
    @classmethod
    def validate_token_not_dummy(cls, v):
        if PRODUCTION_MODE and any(s in v.lower() for s in ("dummy", "mock", "test")):
            raise ValueError("Dummy/test token detected. Not allowed in production.")
        return v


class ElasticConfig(BaseModel):
    """Configuration schema for Elasticsearch client."""

    url: HttpUrl = Field(..., description="Elasticsearch cluster URL.")
    api_key: Optional[str] = Field(None, description="API Key for authentication.")
    username: Optional[str] = Field(None, description="Username for Basic Auth.")
    password: Optional[str] = Field(None, description="Password for Basic Auth.")
    index: str = Field("sfe-logs", description="Default index name.")

    @field_validator("url")
    @classmethod
    def validate_url_security_and_dummy(cls, v):
        if PRODUCTION_MODE:
            v_str = str(v).lower()
            if not v_str.startswith("https"):
                raise ValueError("Elasticsearch URL must use HTTPS in PRODUCTION_MODE.")
            if any(s in v_str for s in ("dummy", "mock", "test", "example.com")):
                raise ValueError(
                    f"Dummy/test URL detected: {v}. Not allowed in production."
                )
        return v

    @field_validator("api_key", "password")
    @classmethod
    def validate_credentials_not_dummy(cls, v, field):
        if (
            PRODUCTION_MODE
            and v
            and any(s in v.lower() for s in ("dummy", "mock", "test"))
        ):
            raise ValueError(
                f"Dummy/test credential detected for {field.name}. Not allowed in production."
            )
        return v

    @field_validator("api_key", "username", "password", mode='before')
    @classmethod
    def validate_auth_method_presence(cls, v, values):
        if not values.get("api_key") and not (
            values.get("username") and values.get("password")
        ):
            raise ValueError(
                "Either 'api_key' or both 'username' and 'password' must be provided for authentication."
            )
        return v


class DatadogConfig(BaseModel):
    """Configuration schema for Datadog client."""

    url: HttpUrl = Field(
        "https://http-intake.logs.datadoghq.com/api/v2/logs",
        description="Datadog Logs intake URL.",
    )
    query_url: HttpUrl = Field(
        "https://api.datadoghq.com/api/v1/logs-queries",
        description="Datadog Logs query API URL.",
    )
    api_key: str = Field(..., min_length=1, description="Datadog API Key.")
    application_key: str = Field(
        ...,
        min_length=1,
        description="Datadog Application Key (required for querying).",
    )
    service: str = Field("sfe-agent", description="Service name for logs.")
    source: str = Field("sfe-audit-plugin", description="Log source.")
    tags: List[str] = Field(
        default_factory=list, description="List of global tags for logs."
    )

    @field_validator("url", "query_url")
    @classmethod
    def validate_urls_security_and_dummy(cls, v):
        if PRODUCTION_MODE:
            v_str = str(v).lower()
            if not v_str.startswith("https"):
                raise ValueError("Datadog URLs must use HTTPS in PRODUCTION_MODE.")
            if any(s in v_str for s in ("dummy", "mock", "test", "example.com")):
                raise ValueError(
                    f"Dummy/test URL detected: {v}. Not allowed in production."
                )
        return v

    @field_validator("api_key", "application_key")
    @classmethod
    def validate_keys_not_dummy(cls, v, field):
        if PRODUCTION_MODE and any(s in v.lower() for s in ("dummy", "mock", "test")):
            raise ValueError(
                f"Dummy/test key detected for {field.name}. Not allowed in production."
            )
        return v


class SplunkClient(AiohttpClientMixin, BaseSIEMClient):
    """
    Splunk HEC (HTTP Event Collector) client for sending and querying logs.
    """

    client_type: Final[str] = "Splunk"

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        self._config_loaded = False
        self._config_lock = asyncio.Lock()

        # Placeholders; real values loaded in _ensure_config_loaded
        self.url: Optional[str] = None
        self.token: Optional[str] = None
        self.source: str = "sfe_audit"
        self.sourcetype: str = "_json"
        self.index: Optional[str] = None
        self.search_url_base: Optional[str] = None

        self.logger.info("SplunkClient initialized.")

    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            try:
                splunk_config_data = (
                    self.config.get(self.client_type.lower(), {})
                    if isinstance(self.config, dict)
                    else {}
                )

                # Retrieve secrets from SECRETS_MANAGER (supports sync/async)
                splunk_config_data["url"] = await _get_secret(
                    "SIEM_SPLUNK_HEC_URL", required=True
                )
                splunk_config_data["token"] = await _get_secret(
                    "SIEM_SPLUNK_HEC_TOKEN", required=True
                )

                validated_config = SplunkConfig(**splunk_config_data).dict(
                    exclude_unset=True
                )
            except ValidationError as e:
                _base_logger.critical(
                    f"CRITICAL: Invalid Splunk client configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Invalid Splunk client configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Invalid Splunk client configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )
            except Exception as e:
                _base_logger.critical(
                    f"CRITICAL: Failed to load Splunk client secrets or configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Failed to load Splunk client secrets or configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Failed to load Splunk client secrets or configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )

            self.url = str(validated_config["url"])
            self.token = validated_config["token"]
            self.source = validated_config["source"]
            self.sourcetype = validated_config["sourcetype"]
            self.index = validated_config.get("index")

            # Derive Search API base robustly
            # Replace /services/collector[/event] with /services/search
            self.search_url_base = re.sub(
                r"/services/collector(?:/event)?/?$", "/services/search", self.url
            )
            if not self.search_url_base.startswith("http"):
                msg = (
                    f"Derived Splunk Search API URL is invalid: {self.search_url_base}"
                )
                _base_logger.critical(f"CRITICAL: {msg}")
                alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
                raise SIEMClientConfigurationError(msg, self.client_type)

            self.logger.extra.update({"url": self.url})
            self._config_loaded = True

    def _hec_health_url(self) -> str:
        # Base HEC collector path
        hec_base = re.sub(
            r"/services/collector(?:/event)?/?$", "/services/collector", self.url or ""
        )
        return f"{hec_base.rstrip('/')}/health/1.0"

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for Splunk HEC health check."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {"Authorization": f"Splunk {self.token}"}
        health_url = self._hec_health_url()
        try:
            async with asyncio.shield(
                session.get(health_url, headers=headers, timeout=self.timeout)
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code == 200:
                    return True, "Splunk HEC is healthy."
                if _is_transient_status(status_code):
                    alert_operator(
                        f"ERROR: Splunk HEC transient health status {status_code}.",
                        level="ERROR",
                    )
                    raise SIEMClientConnectivityError(
                        f"Splunk HEC health transient error {status_code}",
                        self.client_type,
                    )
                # Non-transient failure
                alert_operator(
                    f"CRITICAL: Splunk HEC health check failed with status {status_code}.",
                    level="CRITICAL",
                )
                raise SIEMClientResponseError(
                    f"Splunk HEC responded with status {status_code}",
                    self.client_type,
                    status_code,
                    response_text,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"CRITICAL: Splunk health check connectivity error: {e}",
                level="CRITICAL",
            )
            raise SIEMClientConnectivityError(
                f"Splunk HEC health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(
                f"CRITICAL: Splunk health check failed: {e}", level="CRITICAL"
            )
            raise SIEMClientConnectivityError(
                f"Splunk HEC health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(
        self, log_entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Internal logic for sending a log to Splunk HEC."""
        success, msg, failed_logs = await self._perform_send_logs_batch_logic(
            [log_entry]
        )
        if success:
            return True, "Log sent to Splunk HEC."
        raise SIEMClientPublishError(
            f"Failed to send log to Splunk: {failed_logs[0]['error']}",
            self.client_type,
            details=failed_logs[0],
            correlation_id=self.logger.extra.get("correlation_id"),
        )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Sends multiple log entries to Splunk HEC as a batch (NDJSON)."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {
            "Authorization": f"Splunk {self.token}",
            "Content-Type": "application/json",
        }

        max_batch_size = 1000
        batches = [
            log_entries[i : i + max_batch_size]
            for i in range(0, len(log_entries), max_batch_size)
        ]

        failed_logs: List[Dict[str, Any]] = []
        total_sent = 0

        for batch in batches:
            batch_body_parts = []
            now = time.time()
            host = os.getenv("HOSTNAME", "unknown_host")
            for log_entry in batch:
                hec_event = {
                    "event": log_entry,
                    "sourcetype": self.sourcetype,
                    "source": self.source,
                    "host": host,
                    "time": now,
                }
                if self.index:
                    hec_event["index"] = self.index
                batch_body_parts.append(json.dumps(hec_event))

            full_body = "\n".join(batch_body_parts)

            try:
                async with asyncio.shield(
                    session.post(
                        self.url, headers=headers, data=full_body, timeout=self.timeout
                    )
                ) as response:
                    status_code = response.status
                    response_text = await response.text()
                    if status_code >= 400:
                        if _is_transient_status(status_code):
                            alert_operator(
                                f"ERROR: Splunk HEC transient error {status_code}. Will retry.",
                                level="ERROR",
                            )
                            raise SIEMClientConnectivityError(
                                f"Splunk HEC transient error {status_code}",
                                self.client_type,
                            )
                        alert_operator(
                            f"CRITICAL: Splunk HEC rejected batch with {status_code}.",
                            level="CRITICAL",
                        )
                        failed_logs.extend(
                            [
                                {
                                    "log": log,
                                    "error": f"HTTP {status_code}: {response_text}",
                                }
                                for log in batch
                            ]
                        )
                    else:
                        response.raise_for_status()
                        total_sent += len(batch)
                        if response_text and "success" not in response_text.lower():
                            self.logger.warning(
                                f"Splunk HEC responded with non-success text but status {status_code}: {response_text}.",
                                extra=self.logger.extra,
                            )
            except (ClientError, asyncio.TimeoutError) as e:
                alert_operator(
                    f"ERROR: Splunk HEC connectivity error. Will retry: {e}",
                    level="ERROR",
                )
                raise SIEMClientConnectivityError(
                    f"Splunk HEC connectivity error: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            except Exception as e:
                alert_operator(
                    f"CRITICAL: Splunk batch send failed: {e}", level="CRITICAL"
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])

        if failed_logs:
            return (
                False,
                f"Sent {total_sent} of {len(log_entries)} logs with errors.",
                failed_logs,
            )
        return True, f"Batch of {len(log_entries)} logs sent to Splunk HEC.", []

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Internal logic for querying logs from Splunk."""
        await self._ensure_config_loaded()
        if not self.search_url_base:
            raise SIEMClientConfigurationError(
                "Splunk Search API URL not configured.", self.client_type
            )

        session = await self._get_session()
        headers = {
            "Authorization": f"Splunk {self.token}",
            "Content-Type": "application/json",
        }
        search_url = f"{self.search_url_base.rstrip('/')}/jobs/export"

        earliest_time = (
            f"now-{time_range}"
            if time_range and time_range.endswith(("h", "m", "d", "s"))
            else time_range
        )
        search_query = (
            f"search index={self.index} {query_string}"
            if self.index
            else f"search {query_string}"
        )

        data = {
            "search": search_query,
            "output_mode": "json",
            "count": limit,
            "earliest_time": earliest_time,
        }

        try:
            async with asyncio.shield(
                session.post(
                    search_url, headers=headers, json=data, timeout=self.timeout
                )
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code >= 400:
                    if _is_transient_status(status_code):
                        alert_operator(
                            f"ERROR: Splunk query transient HTTP {status_code}. Will retry.",
                            level="ERROR",
                        )
                        raise SIEMClientConnectivityError(
                            f"Splunk query transient error {status_code}",
                            self.client_type,
                        )
                    alert_operator(
                        f"CRITICAL: Splunk rejected query with status {status_code}.",
                        level="CRITICAL",
                    )
                    raise SIEMClientResponseError(
                        f"HTTP Error {status_code}: {response_text}",
                        self.client_type,
                        status_code,
                        response_text,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
                response.raise_for_status()

                parsed_results: List[Dict[str, Any]] = []
                for line in response_text.strip().split("\n"):
                    if not line:
                        continue
                    try:
                        parsed_results.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        self.logger.warning(
                            f"Failed to parse Splunk search result line: {line}. Error: {e}",
                            extra=self.logger.extra,
                        )
                return parsed_results[:limit] if limit else parsed_results
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"ERROR: Splunk query connectivity error. Will retry: {e}",
                level="ERROR",
            )
            raise SIEMClientConnectivityError(
                f"Failed to query logs from Splunk: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(f"CRITICAL: Splunk query failed: {e}", level="CRITICAL")
            raise SIEMClientQueryError(
                f"Failed to query logs from Splunk: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )


class ElasticClient(AiohttpClientMixin, BaseSIEMClient):
    """
    Elasticsearch client for sending and querying logs using HTTP APIs.
    """

    client_type: Final[str] = "Elasticsearch"

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        self._config_loaded = False
        self._config_lock = asyncio.Lock()

        # Placeholders
        self.url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.index: str = "sfe-logs"

        self.logger.info("ElasticClient initialized.")

    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            try:
                elastic_config_data = (
                    self.config.get(self.client_type.lower(), {})
                    if isinstance(self.config, dict)
                    else {}
                )

                elastic_config_data["url"] = await _get_secret(
                    "SIEM_ELASTIC_URL", required=True
                )
                elastic_config_data["api_key"] = await _get_secret(
                    "SIEM_ELASTIC_API_KEY", required=False
                )
                elastic_config_data["username"] = await _get_secret(
                    "SIEM_ELASTIC_USERNAME", required=False
                )
                elastic_config_data["password"] = await _get_secret(
                    "SIEM_ELASTIC_PASSWORD", required=False
                )

                validated_config = ElasticConfig(**elastic_config_data).dict(
                    exclude_unset=True
                )
            except ValidationError as e:
                _base_logger.critical(
                    f"CRITICAL: Invalid Elasticsearch client configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Invalid Elasticsearch client configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Invalid Elasticsearch client configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )
            except Exception as e:
                _base_logger.critical(
                    f"CRITICAL: Failed to load Elasticsearch client secrets or configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Failed to load Elasticsearch client secrets or configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Failed to load Elasticsearch client secrets or configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )

            self.url = str(validated_config["url"])
            self.api_key = validated_config.get("api_key")
            self.username = validated_config.get("username")
            self.password = validated_config.get("password")
            self.index = validated_config["index"]

            self.logger.extra.update({"url": self.url, "index": self.index})
            self._config_loaded = True

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for Elasticsearch cluster health check."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"

        health_url = f"{self.url.rstrip('/')}/_cluster/health"
        try:
            async with asyncio.shield(
                session.get(
                    health_url,
                    headers=headers,
                    auth=(
                        BasicAuth(self.username, self.password)
                        if self.username and self.password
                        else None
                    ),
                    timeout=self.timeout,
                )
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code == 200:
                    health_status = await response.json()
                    state = health_status.get("status")
                    if state in ["green", "yellow"]:
                        return (
                            True,
                            f"Elasticsearch cluster is healthy ({state} status).",
                        )
                    alert_operator(
                        f"CRITICAL: Elasticsearch cluster health is {state}: {health_status}",
                        level="CRITICAL",
                    )
                    raise SIEMClientResponseError(
                        f"Elasticsearch cluster health is {state}",
                        self.client_type,
                        status_code,
                        response_text,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
                if _is_transient_status(status_code):
                    alert_operator(
                        f"ERROR: Elasticsearch health transient status {status_code}.",
                        level="ERROR",
                    )
                    raise SIEMClientConnectivityError(
                        f"Elasticsearch health transient error {status_code}",
                        self.client_type,
                    )
                alert_operator(
                    f"CRITICAL: Elasticsearch responded with status {status_code}: {response_text}",
                    level="CRITICAL",
                )
                raise SIEMClientResponseError(
                    f"Elasticsearch responded with status {status_code}",
                    self.client_type,
                    status_code,
                    response_text,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"ERROR: Elasticsearch health connectivity error. Will retry: {e}",
                level="ERROR",
            )
            raise SIEMClientConnectivityError(
                f"Elasticsearch health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(
                f"CRITICAL: Elasticsearch health check failed: {e}", level="CRITICAL"
            )
            raise SIEMClientConnectivityError(
                f"Elasticsearch health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(
        self, log_entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Internal logic for sending a log to Elasticsearch."""
        success, msg, failed_logs = await self._perform_send_logs_batch_logic(
            [log_entry]
        )
        if success:
            return True, "Log sent to Elasticsearch."
        raise SIEMClientPublishError(
            f"Failed to send log to Elasticsearch: {failed_logs[0]['error']}",
            self.client_type,
            details=failed_logs[0],
            correlation_id=self.logger.extra.get("correlation_id"),
        )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Sends multiple log entries to Elasticsearch using the _bulk API."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {"Content-Type": "application/x-ndjson"}  # NDJSON for bulk API
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"

        max_bulk_size = 1000
        batches = [
            log_entries[i : i + max_bulk_size]
            for i in range(0, len(log_entries), max_bulk_size)
        ]

        failed_logs: List[Dict[str, Any]] = []
        total_sent = 0

        for batch in batches:
            body_lines = []
            ts = datetime.datetime.utcnow().isoformat() + "Z"
            host = os.getenv("HOSTNAME", "unknown_host")
            for log_entry in batch:
                body_lines.append(json.dumps({"index": {"_index": self.index}}))
                event_with_meta = {
                    "@timestamp": ts,
                    "host": host,
                    "service.name": "sfe-agent",
                    "event": log_entry,
                }
                body_lines.append(json.dumps(event_with_meta))

            full_body = "\n".join(body_lines) + "\n"  # Must end with a newline

            try:
                bulk_url = f"{self.url.rstrip('/')}/_bulk"
                async with asyncio.shield(
                    session.post(
                        bulk_url,
                        headers=headers,
                        data=full_body,
                        auth=(
                            BasicAuth(self.username, self.password)
                            if self.username and self.password
                            else None
                        ),
                        timeout=self.timeout,
                    )
                ) as response:
                    status_code = response.status
                    response_text = await response.text()
                    if status_code >= 400:
                        if _is_transient_status(status_code):
                            alert_operator(
                                f"ERROR: Elasticsearch bulk transient HTTP {status_code}. Will retry.",
                                level="ERROR",
                            )
                            raise SIEMClientConnectivityError(
                                f"Elasticsearch bulk transient error {status_code}",
                                self.client_type,
                            )
                        alert_operator(
                            f"CRITICAL: Elasticsearch rejected bulk with status {status_code}.",
                            level="CRITICAL",
                        )
                        raise SIEMClientResponseError(
                            f"HTTP Error {status_code}: {response_text}",
                            self.client_type,
                            status_code,
                            response_text,
                            correlation_id=self.logger.extra.get("correlation_id"),
                        )

                    response.raise_for_status()
                    bulk_response = await response.json()

                    failed_in_batch = 0
                    items = bulk_response.get("items", [])
                    for i, item in enumerate(items):
                        status = item.get("index", {}).get("status", 200)
                        if status >= 400:
                            reason = (
                                item.get("index", {})
                                .get("error", {})
                                .get("reason", "Unknown error")
                            )
                            failed_logs.append(
                                {
                                    "log": batch[i] if i < len(batch) else {},
                                    "error": reason,
                                    "reason": f"bulk_api_failure_status_{status}",
                                }
                            )
                            failed_in_batch += 1
                    total_sent += max(0, len(batch) - failed_in_batch)
            except (ClientError, asyncio.TimeoutError) as e:
                alert_operator(
                    f"ERROR: Elasticsearch bulk connectivity error. Will retry: {e}",
                    level="ERROR",
                )
                raise SIEMClientConnectivityError(
                    f"Elasticsearch bulk send failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            except Exception as e:
                alert_operator(
                    f"CRITICAL: Elasticsearch bulk log send failed: {e}",
                    level="CRITICAL",
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])

        if failed_logs:
            return False, f"Batch sent with {len(failed_logs)} failures.", failed_logs
        return True, f"Batch of {len(log_entries)} logs sent to Elasticsearch.", []

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Internal logic for querying logs from Elasticsearch."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"

        search_url = f"{self.url.rstrip('/')}/{self.index}/_search"

        query_body: Dict[str, Any] = {
            "size": limit,
            "query": {"query_string": {"query": query_string or "*"}},
            "sort": [{"@timestamp": "desc"}],
        }

        if time_range:
            query_body["query"] = {
                "bool": {
                    "must": [query_body["query"]],
                    "filter": [{"range": {"@timestamp": {"gte": f"now-{time_range}"}}}],
                }
            }

        try:
            async with asyncio.shield(
                session.post(
                    search_url, headers=headers, json=query_body, timeout=self.timeout
                )
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code >= 400:
                    if _is_transient_status(status_code):
                        alert_operator(
                            f"ERROR: Elasticsearch query transient HTTP {status_code}. Will retry.",
                            level="ERROR",
                        )
                        raise SIEMClientConnectivityError(
                            f"Elasticsearch query transient error {status_code}",
                            self.client_type,
                        )
                    alert_operator(
                        f"CRITICAL: Elasticsearch rejected query with status {status_code}.",
                        level="CRITICAL",
                    )
                    raise SIEMClientResponseError(
                        f"HTTP Error {status_code}: {response_text}",
                        self.client_type,
                        status_code,
                        response_text,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
                response.raise_for_status()
                search_results = await response.json()
                hits = search_results.get("hits", {}).get("hits", [])
                return [hit.get("_source", {}) for hit in hits]
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"ERROR: Elasticsearch query connectivity error. Will retry: {e}",
                level="ERROR",
            )
            raise SIEMClientConnectivityError(
                f"Failed to query logs from Elasticsearch: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(
                f"CRITICAL: Elasticsearch query failed: {e}", level="CRITICAL"
            )
            raise SIEMClientQueryError(
                f"Failed to query logs from Elasticsearch: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )


class DatadogClient(AiohttpClientMixin, BaseSIEMClient):
    """
    Datadog client for sending logs to intake and querying via logs-queries API.
    """

    client_type: Final[str] = "Datadog"

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Callable] = None,
        paranoid_mode: bool = False,
    ):
        super().__init__(config, metrics_hook, paranoid_mode)
        self._config_loaded = False
        self._config_lock = asyncio.Lock()

        # Placeholders
        self.url: Optional[str] = None
        self.query_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.application_key: Optional[str] = None
        self.service: str = "sfe-agent"
        self.source: str = "sfe-audit-plugin"
        self.tags: List[str] = []

        self.logger.info("DatadogClient initialized.")

    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            try:
                datadog_config_data = (
                    self.config.get(self.client_type.lower(), {})
                    if isinstance(self.config, dict)
                    else {}
                )

                datadog_config_data["url"] = await _get_secret(
                    "SIEM_DATADOG_API_URL",
                    "https://http-intake.logs.datadoghq.com/api/v2/logs",
                    required=True,
                )
                datadog_config_data["query_url"] = await _get_secret(
                    "SIEM_DATADOG_QUERY_URL",
                    "https://api.datadoghq.com/api/v1/logs-queries",
                    required=True,
                )
                datadog_config_data["api_key"] = await _get_secret(
                    "SIEM_DATADOG_API_KEY", required=True
                )
                datadog_config_data["application_key"] = await _get_secret(
                    "SIEM_DATADOG_APPLICATION_KEY", required=True
                )

                validated_config = DatadogConfig(**datadog_config_data).dict(
                    exclude_unset=True
                )
            except ValidationError as e:
                _base_logger.critical(
                    f"CRITICAL: Invalid Datadog client configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Invalid Datadog client configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Invalid Datadog client configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )
            except Exception as e:
                _base_logger.critical(
                    f"CRITICAL: Failed to load Datadog client secrets or configuration: {e}."
                )
                alert_operator(
                    f"CRITICAL: Failed to load Datadog client secrets or configuration: {e}.",
                    level="CRITICAL",
                )
                raise SIEMClientConfigurationError(
                    f"Failed to load Datadog client secrets or configuration: {e}",
                    self.client_type,
                    original_exception=e,
                )

            self.url = str(validated_config["url"])
            self.query_url = str(validated_config["query_url"])
            self.api_key = validated_config["api_key"]
            self.application_key = validated_config["application_key"]
            self.service = validated_config["service"]
            self.source = validated_config["source"]
            self.tags = validated_config["tags"]

            self.logger.extra.update({"url": self.url, "service": self.service})
            self._config_loaded = True

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for Datadog Logs intake URL reachability."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {"DD-API-KEY": self.api_key, "Content-Type": "application/json"}
        try:
            async with asyncio.shield(
                session.get(self.url, headers=headers, timeout=self.timeout)
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code in [
                    200,
                    202,
                    400,
                ]:  # 400 = bad request, but endpoint is reachable
                    return (
                        True,
                        "Datadog Logs intake URL is reachable (status 2xx/400 suggests service is up).",
                    )
                if _is_transient_status(status_code):
                    alert_operator(
                        f"ERROR: Datadog intake transient status {status_code}.",
                        level="ERROR",
                    )
                    raise SIEMClientConnectivityError(
                        f"Datadog intake transient error {status_code}",
                        self.client_type,
                    )
                alert_operator(
                    f"CRITICAL: Datadog Logs intake responded with unexpected status {status_code}.",
                    level="CRITICAL",
                )
                raise SIEMClientResponseError(
                    f"Datadog Logs intake responded with status {status_code}",
                    self.client_type,
                    status_code,
                    response_text,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"ERROR: Datadog health connectivity error. Will retry: {e}",
                level="ERROR",
            )
            raise SIEMClientConnectivityError(
                f"Datadog Logs intake health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(
                f"CRITICAL: Datadog health check failed: {e}", level="CRITICAL"
            )
            raise SIEMClientConnectivityError(
                f"Datadog Logs intake health check failed: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )

    async def _perform_send_log_logic(
        self, log_entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Internal logic for sending a log to Datadog Logs intake."""
        success, msg, failed_logs = await self._perform_send_logs_batch_logic(
            [log_entry]
        )
        if success:
            return True, "Log sent to Datadog Logs."
        raise SIEMClientPublishError(
            f"Failed to send log to Datadog: {failed_logs[0]['error']}",
            self.client_type,
            details=failed_logs[0],
            correlation_id=self.logger.extra.get("correlation_id"),
        )

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Sends multiple log entries to Datadog Logs intake as a batch."""
        await self._ensure_config_loaded()
        session = await self._get_session()
        headers = {"DD-API-KEY": self.api_key, "Content-Type": "application/json"}

        max_batch_size = 1000
        batches = [
            log_entries[i : i + max_batch_size]
            for i in range(0, len(log_entries), max_batch_size)
        ]

        failed_logs: List[Dict[str, Any]] = []
        total_sent = 0

        for batch in batches:
            host = os.getenv("HOSTNAME", "unknown_host")
            now_ms = int(time.time() * 1000)
            batch_payload = []
            for log_entry in batch:
                batch_payload.append(
                    {
                        "ddsource": self.source,
                        "ddtags": ",".join(self.tags),
                        "hostname": host,
                        "service": self.service,
                        "message": json.dumps(log_entry),
                        "timestamp": now_ms,
                    }
                )

            try:
                async with asyncio.shield(
                    session.post(
                        self.url,
                        headers=headers,
                        json=batch_payload,
                        timeout=self.timeout,
                    )
                ) as response:
                    status_code = response.status
                    response_text = await response.text()
                    if status_code >= 400:
                        if _is_transient_status(status_code):
                            alert_operator(
                                f"ERROR: Datadog intake transient HTTP {status_code}. Will retry.",
                                level="ERROR",
                            )
                            raise SIEMClientConnectivityError(
                                f"Datadog intake transient error {status_code}",
                                self.client_type,
                            )
                        alert_operator(
                            f"CRITICAL: Datadog intake rejected batch with status {status_code}.",
                            level="CRITICAL",
                        )
                        failed_logs.extend(
                            [
                                {
                                    "log": log,
                                    "error": f"HTTP {status_code}: {response_text}",
                                }
                                for log in batch
                            ]
                        )
                    else:
                        response.raise_for_status()
                        total_sent += len(batch)
            except (ClientError, asyncio.TimeoutError) as e:
                alert_operator(
                    f"ERROR: Datadog connectivity error. Will retry: {e}", level="ERROR"
                )
                raise SIEMClientConnectivityError(
                    f"Datadog batch send failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=self.logger.extra.get("correlation_id"),
                )
            except Exception as e:
                alert_operator(
                    f"CRITICAL: Datadog batch send failed: {e}", level="CRITICAL"
                )
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])

        if failed_logs:
            return False, f"Batch sent with {len(failed_logs)} failures.", failed_logs
        return True, f"Batch of {len(log_entries)} logs sent to Datadog Logs.", []

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Internal logic for querying logs from Datadog."""
        await self._ensure_config_loaded()
        if not self.application_key:
            raise SIEMClientConfigurationError(
                "Datadog Application Key is required for querying.", self.client_type
            )

        session = await self._get_session()
        headers = {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.application_key,
            "Content-Type": "application/json",
        }

        now_ms = int(time.time() * 1000)
        from_ms = now_ms - self._parse_relative_time_range_to_ms(time_range)

        query_body = {
            "query": query_string,
            "time": {
                "from": from_ms,
                "to": now_ms,
            },
            "limit": limit,
            "sort": "desc",
        }

        try:
            async with asyncio.shield(
                session.post(
                    self.query_url,
                    headers=headers,
                    json=query_body,
                    timeout=self.timeout,
                )
            ) as response:
                status_code = response.status
                response_text = await response.text()
                if status_code >= 400:
                    if _is_transient_status(status_code):
                        alert_operator(
                            f"ERROR: Datadog query transient HTTP {status_code}. Will retry.",
                            level="ERROR",
                        )
                        raise SIEMClientConnectivityError(
                            f"Datadog query transient error {status_code}",
                            self.client_type,
                        )
                    alert_operator(
                        f"CRITICAL: Datadog rejected query with status {status_code}.",
                        level="CRITICAL",
                    )
                    raise SIEMClientResponseError(
                        f"HTTP Error {status_code}: {response_text}",
                        self.client_type,
                        status_code,
                        response_text,
                        correlation_id=self.logger.extra.get("correlation_id"),
                    )
                response.raise_for_status()
                query_results = await response.json()
                return (
                    [log.get("content", {}) for log in query_results.get("data", [])][
                        :limit
                    ]
                    if limit
                    else [
                        log.get("content", {}) for log in query_results.get("data", [])
                    ]
                )
        except (ClientError, asyncio.TimeoutError) as e:
            alert_operator(
                f"ERROR: Datadog query connectivity error. Will retry: {e}",
                level="ERROR",
            )
            raise SIEMClientConnectivityError(
                f"Failed to query logs from Datadog: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
        except Exception as e:
            alert_operator(f"CRITICAL: Datadog query failed: {e}", level="CRITICAL")
            raise SIEMClientQueryError(
                f"Failed to query logs from Datadog: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=self.logger.extra.get("correlation_id"),
            )
