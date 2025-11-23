from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import random
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Type
from unittest.mock import MagicMock

# Conditional imports for cloud SDKs
AWS_AVAILABLE = False
GCP_AVAILABLE = False
AZURE_MONITOR_QUERY_AVAILABLE = False
AZURE_IDENTITY_AVAILABLE = False
AZURE_MONITOR_INGESTION_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError as AWSClientError

    AWS_AVAILABLE = True
except ImportError:
    logging.warning("boto3 not found. AWS CloudWatch Logs integration will be disabled.")

try:
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import logging as gcp_logging_sdk

    GCP_AVAILABLE = True
except ImportError:
    logging.warning(
        "google-cloud-logging not found. GCP Cloud Logging integration will be disabled."
    )

try:
    from azure.monitor.query.aio import LogsQueryClient

    AZURE_MONITOR_QUERY_AVAILABLE = True
except ImportError:
    logging.warning(
        "azure-monitor-query not found. Azure Monitor query functionality will be disabled."
    )

try:
    from azure.identity.aio import DefaultAzureCredential

    AZURE_IDENTITY_AVAILABLE = True
except ImportError:
    logging.warning("azure-identity not found. Azure authentication will be disabled.")

try:
    from azure.monitor.ingestion.aio import LogsIngestionClient

    AZURE_MONITOR_INGESTION_AVAILABLE = True
except ImportError:
    logging.warning(
        "azure-monitor-ingestion not found. Azure Monitor log ingestion will be disabled."
    )

logger = logging.getLogger(__name__)


# --- Async Retry Helper ---
async def _async_retry(
    func,
    *args,
    retries=3,
    min_delay=1,
    max_delay=5,
    jitter=True,
    exceptions=(Exception,),
    **kwargs,
):
    """
    An asynchronous retry helper with exponential backoff and jitter.
    """
    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            attempt += 1
            if attempt > retries:
                raise
            delay = min(max_delay, min_delay * (2 ** (attempt - 1)))
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)
            logger.warning(
                f"Retryable error: {e}. Retrying in {delay:.2f}s (attempt {attempt}/{retries})"
            )
            await asyncio.sleep(delay)


# --- Custom Exception Hierarchy ---
class CloudLoggingError(Exception):
    """Base exception for cloud logging integration errors."""

    def __init__(
        self,
        message: str,
        cloud_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.cloud_type = cloud_type
        self.original_exception = original_exception
        self.details = details or {}


class CloudLoggingConfigurationError(CloudLoggingError):
    """Raised when cloud logging client is misconfigured."""

    pass


class CloudLoggingConnectivityError(CloudLoggingError):
    """Raised when unable to connect to the cloud logging service."""

    pass


class CloudLoggingAuthError(CloudLoggingError):
    """Raised for authentication/authorization failures with cloud logging service."""

    pass


class CloudLoggingResponseError(CloudLoggingError):
    """Raised when cloud logging service returns an error response."""

    def __init__(
        self,
        message: str,
        cloud_type: str,
        status_code: int,
        response_text: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, cloud_type, original_exception, details)
        self.status_code = status_code
        self.response_text = response_text


class CloudLoggingQueryError(CloudLoggingError):
    """Raised when a cloud log query fails."""

    pass


# --- Base Cloud Logger Interface ---
class BaseCloudLogger:
    """
    Abstract base class for all cloud loggers.
    Implements a robust batching mechanism and async context management (`async with`).
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cloud_type = "BaseCloudLogger"
        self.timeout = config.get("default_timeout_seconds", 10)
        self.batch_size = config.get("batch_size", 100)
        self._log_buffer: Deque[Dict[str, Any]] = deque()

    async def __aenter__(self) -> BaseCloudLogger:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.flush()

    async def _to_thread(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(func, *args, **kwargs)

    def log_event(self, event: Dict[str, Any]):
        self._log_buffer.append(event)

    async def flush(self):
        raise NotImplementedError("Flush not implemented for this logger.")

    async def health_check(self) -> Tuple[bool, str]:
        raise NotImplementedError("Health check not implemented for this logger.")

    async def query_logs(
        self, query_string: str, time_range: str = "24h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("Query logs not implemented for this logger.")

    def _parse_relative_time_range_to_ms(self, time_range_str: str) -> int:
        if not time_range_str or len(time_range_str) < 2:
            return 24 * 3600 * 1000
        unit = time_range_str[-1].lower()
        try:
            value = int(time_range_str[:-1])
        except ValueError:
            return 24 * 3600 * 1000
        if unit == "s":
            return value * 1000
        elif unit == "m":
            return value * 60 * 1000
        elif unit == "h":
            return value * 3600 * 1000
        elif unit == "d":
            return value * 24 * 3600 * 1000
        else:
            return 24 * 3600 * 1000

    def _parse_relative_time_range_to_timedelta(self, time_range_str: str) -> datetime.timedelta:
        if not time_range_str or len(time_range_str) < 2:
            return datetime.timedelta(hours=24)
        unit = time_range_str[-1].lower()
        try:
            value = int(time_range_str[:-1])
        except ValueError:
            return datetime.timedelta(hours=24)
        if unit == "s":
            return datetime.timedelta(seconds=value)
        elif unit == "m":
            return datetime.timedelta(minutes=value)
        elif unit == "h":
            return datetime.timedelta(hours=value)
        elif unit == "d":
            return datetime.timedelta(days=value)
        else:
            return datetime.timedelta(hours=24)


# --- Cloud Logger Implementations ---


class CloudWatchLogger(BaseCloudLogger):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cloud_type = "AWSCloudWatch"
        aws_config = config.get("aws_cloudwatch", {})
        self.region_name = aws_config.get("region_name", os.getenv("AWS_REGION", "us-east-1"))
        self.log_group_name = aws_config.get("log_group_name", "sfe-audit-logs")
        self.log_stream_name = aws_config.get("log_stream_name", "default")
        self.batch_size = aws_config.get("batch_size", 100)
        self.max_query_wait_seconds = int(aws_config.get("max_query_wait_seconds", 90))

        if not AWS_AVAILABLE:
            raise CloudLoggingConfigurationError("boto3 is not installed for AWS.", self.cloud_type)
        if not self.log_group_name:
            raise CloudLoggingConfigurationError(
                "AWS Log Group Name must be configured.", self.cloud_type
            )

        self._cw_logs_client = None
        self._sequence_token = None

    def log_event(self, event: Dict[str, Any]):
        """Adds an event to the buffer after validating its size."""
        try:
            event_str = json.dumps(event)
            # AWS CloudWatch max event size is 256KB, minus a small overhead.
            if len(event_str.encode("utf-8")) > (256 * 1024 - 26):
                raise CloudLoggingError("Log event size exceeds 256KB limit", self.cloud_type)
            self._log_buffer.append(event)
        except TypeError as e:
            raise CloudLoggingError(f"Log event is not JSON serializable: {e}", self.cloud_type, e)

    async def _get_aws_client(self):
        if self._cw_logs_client is None:
            self._cw_logs_client = await self._to_thread(
                lambda: boto3.client("logs", region_name=self.region_name)
            )
        return self._cw_logs_client

    async def flush(self):
        """Flushes buffered logs to CloudWatch with retry logic for transient errors."""
        if not self._log_buffer:
            return
        client = await self._get_aws_client()
        if self._sequence_token is None:
            self._sequence_token = await self._get_latest_sequence_token(client)

        async def _put_events(put_args):
            return await self._to_thread(lambda: client.put_log_events(**put_args))

        while self._log_buffer:
            batch = [
                self._log_buffer.popleft()
                for _ in range(min(self.batch_size, len(self._log_buffer)))
            ]
            log_events = [
                {"timestamp": int(time.time() * 1000), "message": json.dumps(event)}
                for event in batch
            ]
            put_args = {
                "logGroupName": self.log_group_name,
                "logStreamName": self.log_stream_name,
                "logEvents": log_events,
            }
            if self._sequence_token:
                put_args["sequenceToken"] = self._sequence_token

            try:
                # Use retry logic for the API call
                response = await _async_retry(
                    _put_events,
                    put_args,
                    retries=3,
                    min_delay=1,
                    max_delay=5,
                    exceptions=(AWSClientError,),
                )
                self._sequence_token = response.get("nextSequenceToken")
                rejected_info = response.get("rejectedLogEventsInfo")
                if rejected_info and not isinstance(
                    rejected_info, MagicMock
                ):  # Avoid logging mocks
                    logger.error(f"CloudWatch rejected log events: {rejected_info}")
            except AWSClientError as e:
                # If retries fail, roll back the batch to the buffer
                for event in reversed(batch):
                    self._log_buffer.appendleft(event)
                logger.error(
                    f"Failed to flush logs to CloudWatch after retries: {e}",
                    exc_info=True,
                )
                self._sequence_token = None  # Invalidate token on failure
                raise CloudLoggingResponseError(
                    "AWS PutLogEvents failed", self.cloud_type, 500, str(e), e
                )

    async def _get_latest_sequence_token(self, client) -> Optional[str]:
        try:
            response = await self._to_thread(
                lambda: client.describe_log_streams(
                    logGroupName=self.log_group_name,
                    logStreamNamePrefix=self.log_stream_name,
                    limit=1,
                )
            )
            return response.get("logStreams", [{}])[0].get("uploadSequenceToken")
        except Exception:
            return None

    async def health_check(self) -> Tuple[bool, str]:
        """Performs a health check with robust error handling for mock compatibility."""
        try:
            client = await self._get_aws_client()
            await self._to_thread(
                lambda: client.describe_log_groups(logGroupNamePrefix=self.log_group_name, limit=1)
            )
            return True, "Successfully connected to AWS CloudWatch Logs."
        except AWSClientError as e:
            # It's a specific AWS error, let's inspect it
            if hasattr(e, "response") and isinstance(e.response, dict):
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "AccessDeniedException":
                    msg = e.response.get("Error", {}).get("Message", str(e))
                    raise CloudLoggingAuthError(
                        f"AWS authorization failed: {msg}", self.cloud_type, e
                    ) from e
            # For any other AWS error, wrap it in our custom generic error
            raise CloudLoggingError(
                f"Unexpected error during AWS health check: {e}", self.cloud_type, e
            ) from e
        except Exception as e:
            # It's a non-AWS error (e.g., configuration, network)
            raise CloudLoggingError(
                f"Unexpected error during AWS health check: {e}", self.cloud_type, e
            ) from e

    async def query_logs(
        self, query_string: str, time_range: str = "24h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        client = await self._get_aws_client()
        end_time_s = int(time.time())
        start_time_s = end_time_s - int(self._parse_relative_time_range_to_ms(time_range) / 1000)

        try:
            query_id = (
                await self._to_thread(
                    lambda: client.start_query(
                        logGroupName=self.log_group_name,
                        startTime=start_time_s,
                        endTime=end_time_s,
                        queryString=query_string,
                        limit=limit,
                    )
                )
            ).get("queryId")

            if not query_id:
                raise CloudLoggingQueryError(
                    "Failed to start CloudWatch Logs query.", self.cloud_type
                )

            started = time.time()
            while True:
                results_response = await self._to_thread(
                    lambda: client.get_query_results(queryId=query_id)
                )
                status = results_response.get("status")
                if status == "Complete":
                    return [
                        {field["field"]: field["value"] for field in row}
                        for row in results_response.get("results", [])
                    ]
                if status in ["Failed", "Cancelled"]:
                    raise CloudLoggingQueryError(f"Query {status.lower()}", self.cloud_type)
                if time.time() - started > self.max_query_wait_seconds:
                    await self._to_thread(lambda: client.stop_query(queryId=query_id))
                    raise CloudLoggingQueryError("Query timed out", self.cloud_type)
                await asyncio.sleep(1)
        except AWSClientError as e:
            raise CloudLoggingQueryError(f"AWS query error: {e}", self.cloud_type, e)


class GCPLogger(BaseCloudLogger):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cloud_type = "GCPLogging"
        gcp_config = config.get("gcp_logging", {})
        self.project_id = gcp_config.get("project_id", os.getenv("GCP_PROJECT_ID"))
        self.log_name = gcp_config.get("log_name", "sfe-audit-log")
        self.credentials_path = gcp_config.get("credentials_path")
        self.batch_size = gcp_config.get("batch_size", 1000)

        if not GCP_AVAILABLE:
            raise CloudLoggingConfigurationError(
                "google-cloud-logging not installed for GCP.", self.cloud_type
            )

        self._logging_client = None

    async def _get_gcp_client(self) -> gcp_logging_sdk.Client:
        if self._logging_client is None:
            if self.credentials_path:
                self._logging_client = await self._to_thread(
                    lambda: gcp_logging_sdk.Client.from_service_account_json(
                        self.credentials_path, project=self.project_id
                    )
                )
            else:
                self._logging_client = await self._to_thread(
                    lambda: gcp_logging_sdk.Client(project=self.project_id)
                )
        return self._logging_client

    async def flush(self):
        if not self._log_buffer:
            return

        client = await self._get_gcp_client()
        while self._log_buffer:
            batch = [
                self._log_buffer.popleft()
                for _ in range(min(self.batch_size, len(self._log_buffer)))
            ]
            try:
                with client.batch() as batch_client:
                    for event in batch:
                        severity = str(event.get("severity", "INFO")).upper()
                        batch_client.log_struct(event, severity=severity)
            except GoogleAPIError as e:
                for event in reversed(batch):
                    self._log_buffer.appendleft(event)
                logger.error(f"Failed to flush logs to GCP: {e}", exc_info=True)
                raise CloudLoggingResponseError(
                    "GCP API Error", self.cloud_type, getattr(e, "code", 500), str(e), e
                )

    async def health_check(self) -> Tuple[bool, str]:
        try:
            client = await self._get_gcp_client()
            await self._to_thread(lambda: next(client.list_entries(page_size=1), None))
            return True, "Successfully connected to GCP Cloud Logging."
        except GoogleAPIError as e:
            code = getattr(e, "code", 500)
            if code == 403:
                raise CloudLoggingAuthError(f"GCP permission denied: {e}", self.cloud_type, e)
            raise CloudLoggingConnectivityError(f"GCP API Error: {e}", self.cloud_type, e)
        except Exception as e:
            raise CloudLoggingError(
                f"Unexpected error during GCP health check: {e}", self.cloud_type, e
            )

    async def query_logs(
        self, query_string: str, time_range: str = "24h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        client = await self._get_gcp_client()
        end_time = datetime.datetime.utcnow()
        start_time = end_time - self._parse_relative_time_range_to_timedelta(time_range)

        filters = [
            f'logName="projects/{self.project_id}/logs/{self.log_name}"',
            f'timestamp >= "{start_time.isoformat()}Z"',
            f'timestamp <= "{end_time.isoformat()}Z"',
        ]
        if query_string:
            filters.append(query_string)

        try:

            def _collect():
                iterator = client.list_entries(
                    filter=" AND ".join(filters),
                    order_by=gcp_logging_sdk.DESCENDING,
                    page_size=limit,
                )
                return [entry.payload for entry in iterator]

            return await self._to_thread(_collect)
        except GoogleAPIError as e:
            raise CloudLoggingQueryError(f"GCP query failed: {e}", self.cloud_type, e)


class AzureMonitorLogger(BaseCloudLogger):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cloud_type = "AzureMonitor"
        azure_config = config.get("azure_monitor", {})
        self.data_collection_endpoint = azure_config.get(
            "data_collection_endpoint", os.getenv("AZURE_DCE")
        )
        self.dcr_immutable_id = azure_config.get("dcr_immutable_id", os.getenv("AZURE_DCR_ID"))
        self.stream_name = azure_config.get("stream_name", os.getenv("AZURE_STREAM_NAME"))
        self.workspace_id = azure_config.get("workspace_id", os.getenv("AZURE_WORKSPACE_ID"))
        self.batch_size = azure_config.get("batch_size", 500)

        if not all(
            [
                AZURE_IDENTITY_AVAILABLE,
                AZURE_MONITOR_INGESTION_AVAILABLE,
                AZURE_MONITOR_QUERY_AVAILABLE,
            ]
        ):
            raise CloudLoggingConfigurationError(
                "Required Azure libraries not installed.", self.cloud_type
            )
        if not all([self.data_collection_endpoint, self.dcr_immutable_id, self.stream_name]):
            raise CloudLoggingConfigurationError(
                "Azure config requires DCE, DCR_ID, and stream_name.", self.cloud_type
            )

        self._credential: Optional[DefaultAzureCredential] = None
        self._ingestion_client: Optional[LogsIngestionClient] = None
        self._query_client: Optional[LogsQueryClient] = None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)
        if self._ingestion_client:
            await self._ingestion_client.close()
        if self._query_client:
            await self._query_client.close()
        if self._credential:
            await self._credential.close()

    async def _get_credential(self) -> DefaultAzureCredential:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential

    async def _get_ingestion_client(self) -> LogsIngestionClient:
        if self._ingestion_client is None:
            creds = await self._get_credential()
            self._ingestion_client = LogsIngestionClient(
                endpoint=self.data_collection_endpoint, credential=creds
            )
        return self._ingestion_client

    async def _get_query_client(self) -> LogsQueryClient:
        if self._query_client is None:
            creds = await self._get_credential()
            self._query_client = LogsQueryClient(creds)
        return self._query_client

    async def flush(self):
        if not self._log_buffer:
            return
        client = await self._get_ingestion_client()
        while self._log_buffer:
            batch = [
                self._log_buffer.popleft()
                for _ in range(min(self.batch_size, len(self._log_buffer)))
            ]
            try:
                await client.upload(
                    rule_id=self.dcr_immutable_id,
                    stream_name=self.stream_name,
                    logs=batch,
                )
            except Exception as e:
                for event in reversed(batch):
                    self._log_buffer.appendleft(event)
                logger.error(f"Failed to flush logs to Azure: {e}", exc_info=True)
                raise CloudLoggingResponseError(
                    "Azure ingestion API call failed", self.cloud_type, 500, str(e), e
                )

    async def health_check(self) -> Tuple[bool, str]:
        try:
            await self._get_ingestion_client()
            return True, "Azure Monitor Ingestion client initialized successfully."
        except Exception as e:
            raise CloudLoggingAuthError(
                f"Azure authentication/configuration failed: {e}", self.cloud_type, e
            )

    async def query_logs(
        self, query_string: str, time_range: str = "24h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        client = await self._get_query_client()
        end_time = datetime.datetime.utcnow()
        start_time = end_time - self._parse_relative_time_range_to_timedelta(time_range)
        try:
            full_kql_query = f"{self.stream_name} | {query_string} | take {limit}"
            response = await client.query_workspace(
                workspace_id=self.workspace_id,
                query=full_kql_query,
                timespan=(start_time, end_time),
            )
            if response and response.tables:
                return [
                    dict(zip([col.name for col in table.columns], row))
                    for table in response.tables
                    for row in table.rows
                ]
            return []
        except Exception as e:
            raise CloudLoggingQueryError(
                f"Failed to query Azure Monitor Logs: {e}", self.cloud_type, e
            )


# --- Factory Function ---
CLOUD_LOGGER_REGISTRY: Dict[str, Type[BaseCloudLogger]] = {
    "aws_cloudwatch": CloudWatchLogger,
    "gcp_logging": GCPLogger,
    "azure_monitor": AzureMonitorLogger,
}


def get_cloud_logger(cloud_type: str, config: Dict[str, Any]) -> BaseCloudLogger:
    logger_class = CLOUD_LOGGER_REGISTRY.get(cloud_type)
    if not logger_class:
        raise CloudLoggingConfigurationError(f"Unknown logger type: {cloud_type}", cloud_type)
    return logger_class(config)
