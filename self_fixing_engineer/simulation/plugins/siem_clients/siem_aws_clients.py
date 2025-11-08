# plugins/siem_aws_clients.py

import os
import json
import time
import datetime
import asyncio
import sys # For sys.exit to fail fast
import re
import aiohttp
import base64
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple, Optional, Callable, Literal, Final

# Import base classes and utilities from siem_base
from .siem_base import (
    BaseSIEMClient, SIEMClientConfigurationError, SIEMClientError,
    SIEMClientAuthError, SIEMClientConnectivityError, SIEMClientQueryError,
    SIEMClientPublishError, SIEMClientResponseError,
    alert_operator, SECRETS_MANAGER, PRODUCTION_MODE, AUDIT,
    PYDANTIC_AVAILABLE, GenericLogEvent, scrub_secrets, _global_secret_patterns,
    _base_logger
)
from pydantic import BaseModel, Field, ValidationError, validator # Re-import for local schemas


# --- Strict Dependency Check for boto3 ---
AWS_AVAILABLE = False
try:
    import boto3
    from botocore.exceptions import ClientError as AWSClientError
    AWS_AVAILABLE = True
except ImportError:
    _base_logger.critical("CRITICAL: boto3 not found. AWS CloudWatch Logs client will be disabled. Aborting startup.")
    alert_operator("CRITICAL: boto3 not found for AWS CloudWatch client. Aborting.", level="CRITICAL")
    sys.exit(1)


# --- Configuration Schema for AWS CloudWatch Client ---
class AwsCloudWatchConfig(BaseModel):
    """Configuration schema for AWS CloudWatch Logs client."""
    region_name: str = Field(..., description="AWS region.")
    log_group_name: str = Field(..., min_length=1, description="CloudWatch Log Group name.")
    log_stream_name: str = Field("default", description="CloudWatch Log Stream name.")
    # New: Explicit control for resource creation in production
    auto_create_log_group: bool = Field(False, description="Allow auto-creation of log group.")
    auto_create_log_stream: bool = Field(False, description="Allow auto-creation of log stream.")
    # Secrets for explicit AWS credentials (if not using IAM roles)
    aws_access_key_id: Optional[str] = None # Should come from secrets manager/IAM role in prod
    aws_secret_access_key: Optional[str] = None # Should come from secrets manager/IAM role in prod
    aws_credentials_secret_id: Optional[str] = None # Secret ID for AWS credentials (JSON string)
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list) # Prioritized list of secrets backends
    secrets_provider_config: Optional[Dict[str, Any]] = None # Config for secrets backends (e.g., vault_url, project_id)

    @validator("region_name", "log_group_name", "log_stream_name")
    def validate_non_empty(cls, v):
        if not v:
            raise ValueError("Field must not be empty.")
        return v
    
    @validator("log_group_name", "log_stream_name")
    def validate_arn_compliance(cls, v, field):
        if PRODUCTION_MODE:
            # CloudWatch Log Group and Stream names have specific allowed characters
            # Log Group: letters, numbers, '.', '_', '-', '/'
            # Log Stream: letters, numbers, '.', '_', '-', '/'
            # Check for a more specific pattern for production
            # (Note: The provided example ARN pattern for log groups is not a standard Boto3 input.
            # Boto3 client methods typically take the name, not the full ARN.)
            # Boto3's `describe_log_groups` and `describe_log_streams` APIs accept the log group/stream name as a string.
            log_name_pattern = r'^[a-zA-Z0-9_./-]+$'
            if not re.match(log_name_pattern, v):
                raise ValueError(f"Invalid {field.name} format for production: {v}. Must contain only letters, numbers, '.', '_', '-', '/'.")
        return v

    @validator("auto_create_log_group", "auto_create_log_stream")
    def validate_auto_create_in_prod(cls, v, field):
        if PRODUCTION_MODE and v:
            raise ValueError(f"In PRODUCTION_MODE, '{field.name}' must be False. Log groups/streams must be pre-created or explicitly approved by operator.")
        return v

    @validator("aws_access_key_id", "aws_secret_access_key", always=True)
    def validate_aws_credentials_source(cls, v, values):
        if PRODUCTION_MODE:
            # BLOCKER: Require KMS/IAM role-based credentials only. Abort if ENV or plaintext creds are present.
            # This validation ensures that if explicit keys are provided, they must come from a secrets_id.
            # If no secrets_id, it implies IAM role or default chain, which is preferred.
            if values.get("aws_access_key_id") or values.get("aws_secret_access_key"):
                if not values.get("aws_credentials_secret_id"):
                    raise ValueError("In PRODUCTION_MODE, explicit AWS credentials must be loaded via 'aws_credentials_secret_id'. Direct keys/ENV are forbidden.")
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("aws_credentials_secret_id"):
            if not v:
                raise ValueError("secrets_providers list must not be empty if aws_credentials_secret_id is provided.")
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'.")
        return v

# --- Alerting function for PagerDuty/Slack (Integration Completion) ---
# NOTE: This is a simplified example. In a real-world scenario, you would
# likely have a more robust alerting service and aiohttp session management.
async def alert_operator_http(message: str, level: str = "CRITICAL"):
    """Sends a message to PagerDuty/Slack."""
    pagerduty_url = os.getenv("PAGERDUTY_URL")
    slack_url = os.getenv("SLACK_WEBHOOK_URL")

    async with aiohttp.ClientSession() as session:
        if pagerduty_url:
            try:
                # Assuming PagerDuty API requires an Authorization header with a token
                # The payload may vary depending on the PagerDuty event API version
                pagerduty_key = await SECRETS_MANAGER.get_secret("PAGERDUTY_TOKEN")
                headers = {"Authorization": f"Token token={pagerduty_key}", "Content-Type": "application/json"}
                payload = {
                    "event_action": "trigger",
                    "routing_key": await SECRETS_MANAGER.get_secret("PAGERDUTY_ROUTING_KEY"),
                    "payload": {
                        "summary": f"[SIEM ALERT - {level}] {message}",
                        "source": "siem-aws-client",
                        "severity": "critical" if level == "CRITICAL" else "warning"
                    }
                }
                await session.post(pagerduty_url, json=payload, headers=headers)
            except Exception as e:
                _base_logger.error(f"Failed to send PagerDuty alert: {e}")
        
        if slack_url:
            try:
                # Basic Slack webhook payload
                payload = {"text": f"[SIEM ALERT - {level}] {message}"}
                await session.post(slack_url, json=payload)
            except Exception as e:
                _base_logger.error(f"Failed to send Slack alert: {e}")
    _base_logger.critical(f"[OPS ALERT - {level}] {message}")

# Redirect the global alert_operator to our new HTTP-based function if needed
# This part depends on how `siem_base` is structured. For this fix, we assume
# a global replacement.
if PRODUCTION_MODE:
    alert_operator = alert_operator_http


# Secrets Backend Interface (copied from dlt_base, assuming it's not globally available)
class SecretsBackend(ABC):
    """Abstract base class for secrets backends."""
    @abstractmethod
    async def get_secret(self, secret_id: str) -> str:
        """
        Retrieves a secret by ID asynchronously.
        Args:
            secret_id: The identifier of the secret.
        Returns:
            str: The secret value.
        Raises:
            SIEMClientConfigurationError: If the secret cannot be retrieved.
        """
        pass

class AWSSecretsBackend(SecretsBackend):
    """AWS Secrets Manager backend."""
    def __init__(self, region_name: str = None):
        if not AWS_AVAILABLE:
            raise SIEMClientConfigurationError("AWS Secrets Manager backend requested but boto3 is not available.", "AWSCloudWatch")
        self.client = boto3.client('secretsmanager', region_name=region_name)

    async def get_secret(self, secret_id: str) -> str:
        try:
            # boto3 client methods are typically blocking, run in executor
            response = await asyncio.to_thread(self.client.get_secret_value, SecretId=secret_id)
            return response['SecretString']
        except AWSClientError as e:
            raise SIEMClientConfigurationError(f"Failed to fetch secret from AWS Secrets Manager: {e}", "AWSCloudWatch", original_exception=e)

class AwsKmsBackend(SecretsBackend):
    """AWS KMS backend for decrypting secrets."""
    def __init__(self, region_name: str = None):
        if not AWS_AVAILABLE:
            raise SIEMClientConfigurationError("AWS KMS backend requested but boto3 is not available.", "AWSCloudWatch")
        self.client = boto3.client('kms', region_name=region_name)

    async def get_secret(self, secret_id: str) -> str:
        try:
            # Decrypts a Base64-encoded ciphertext blob.
            encrypted_data = base64.b64decode(secret_id)
            response = await asyncio.to_thread(self.client.decrypt, CiphertextBlob=encrypted_data)
            return response['Plaintext'].decode('utf-8')
        except AWSClientError as e:
            raise SIEMClientConfigurationError(f"Failed to decrypt secret with AWS KMS: {e}", "AWSCloudWatch", original_exception=e)

# Assuming Azure/GCP secrets backends are defined similarly in dlt_base or elsewhere
# For this file, we only need AWSSecretsBackend and AwsKmsBackend for AWS credentials.


class AwsCloudWatchClient(BaseSIEMClient):
    """
    AWS CloudWatch Logs client for sending logs via `PutLogEvents` and querying via Logs Insights.
    """
    client_type: Final[str] = "AWSCloudWatch"
    MAX_BATCH_SIZE = 10000
    MAX_BATCH_BYTES = 1048576 # 1 MB in bytes

    def __init__(self, config: Dict[str, Any], metrics_hook: Optional[Callable] = None, paranoid_mode: bool = False):
        super().__init__(config, metrics_hook, paranoid_mode)
        
        # Secrets from Vault Only & Config Validation at Startup
        try:
            aws_config_data = config.get(self.client_type.lower(), {})
            
            # Load credentials from Secrets Manager if specified
            aws_credentials_secret_id = aws_config_data.get("aws_credentials_secret_id")
            secrets_providers = aws_config_data.get("secrets_providers", [])
            secrets_provider_config = aws_config_data.get("secrets_provider_config", {})

            if aws_credentials_secret_id:
                for provider_name in secrets_providers:
                    try:
                        if provider_name == "aws": # Only AWS Secrets Manager for AWS creds
                            backend = AWSSecretsBackend(region_name=aws_config_data.get("region_name"))
                            credentials_json = await backend.get_secret(aws_credentials_secret_id)
                        # Add logic for other providers if they can store AWS creds
                        # For now, we assume this is not the case
                        else:
                            _base_logger.warning(f"Secrets backend '{provider_name}' not supported for AWS credentials.", extra={'client_type': self.client_type})
                            continue
                        
                        credentials = json.loads(credentials_json)
                        aws_config_data["aws_access_key_id"] = credentials.get("aws_access_key_id")
                        aws_config_data["aws_secret_access_key"] = credentials.get("aws_secret_access_key")
                        if not aws_config_data["aws_access_key_id"] or not aws_config_data["aws_secret_access_key"]:
                            raise ValueError("AWS credentials JSON missing 'aws_access_key_id' or 'aws_secret_access_key'.")
                        self.logger.info(f"AWS credentials loaded from secrets backend: {provider_name}.")
                        break
                    except Exception as e:
                        _base_logger.warning(f"Failed to fetch AWS credentials from {provider_name}: {e}", exc_info=True, extra={'client_type': self.client_type})
                        continue
                else:
                    # If secrets_id was provided but all providers failed
                    _base_logger.critical("CRITICAL: Failed to load AWS credentials from any configured secrets backend. Aborting startup.", extra={'client_type': self.client_type})
                    alert_operator("CRITICAL: Failed to load AWS credentials from secrets. Aborting.", level="CRITICAL")
                    sys.exit(1)
            
            validated_config = AwsCloudWatchConfig(**aws_config_data).dict(exclude_unset=True)
        except ValidationError as e:
            _base_logger.critical(f"CRITICAL: Invalid AWS CloudWatch client configuration: {e}. Aborting startup.", extra={'client_type': self.client_type})
            alert_operator(f"CRITICAL: Invalid AWS CloudWatch client configuration: {e}. Aborting.", level="CRITICAL")
            sys.exit(1) # Fail fast on configuration errors
        except Exception as e:
            _base_logger.critical(f"CRITICAL: Failed to load AWS CloudWatch client secrets or configuration: {e}. Aborting startup.", exc_info=True, extra={'client_type': self.client_type})
            alert_operator(f"CRITICAL: Failed to load AWS CloudWatch client secrets or configuration: {e}. Aborting.", level="CRITICAL")
            sys.exit(1) # Fail fast on general config/secrets loading errors

        self.region_name = validated_config["region_name"]
        self.log_group_name = validated_config["log_group_name"]
        self.log_stream_name = validated_config["log_stream_name"]
        self.auto_create_log_group = validated_config["auto_create_log_group"]
        self.auto_create_log_stream = validated_config["auto_create_log_stream"]
        self.aws_access_key_id = validated_config.get("aws_access_key_id")
        self.aws_secret_access_key = validated_config.get("aws_secret_access_key")

        self._cw_logs_client = None # Boto3 client, lazily initialized
        self._kms_client = None # Boto3 KMS client, lazily initialized

        self.logger.extra.update({'region': self.region_name, 'log_group': self.log_group_name, 'log_stream': self.log_stream_name})
        self.logger.info("AwsCloudWatchClient initialized.")

    async def _get_aws_client(self):
        """Lazily initializes and returns the boto3 CloudWatch Logs client."""
        if self._cw_logs_client is None:
            # If aws_access_key_id/secret_access_key are None, boto3 will use its default credential chain (IAM roles, etc.)
            if self.aws_access_key_id and self.aws_secret_access_key:
                # SECURITY ENHANCEMENT: Decrypt credentials with KMS if in production
                if PRODUCTION_MODE:
                    self.logger.debug("Attempting to decrypt credentials with KMS.", extra=self.logger.extra)
                    try:
                        kms_backend = AwsKmsBackend(region_name=self.region_name)
                        self.aws_access_key_id = await kms_backend.get_secret(self.aws_access_key_id)
                        self.aws_secret_access_key = await kms_backend.get_secret(self.aws_secret_access_key)
                        self.logger.info("Credentials successfully decrypted via KMS.", extra=self.logger.extra)
                    except Exception as e:
                        _base_logger.critical(f"CRITICAL: KMS decryption failed: {e}. Aborting startup.", exc_info=True)
                        alert_operator(f"CRITICAL: KMS decryption failed: {e}. Aborting.", level="CRITICAL")
                        sys.exit(1)

                self._cw_logs_client = await asyncio.shield(self._run_blocking_in_executor(
                    lambda: boto3.client(
                        'logs',
                        region_name=self.region_name,
                        aws_access_key_id=self.aws_access_key_id,
                        aws_secret_access_key=self.aws_secret_access_key
                    )
                ))
                self.logger.debug("Initialized AWS client with explicit credentials.", extra=self.logger.extra)
            else:
                self._cw_logs_client = await asyncio.shield(self._run_blocking_in_executor(
                    lambda: boto3.client('logs', region_name=self.region_name)
                ))
                self.logger.debug("Initialized AWS client using default credential chain (IAM roles/config).", extra=self.logger.extra)
        return self._cw_logs_client

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        """Internal logic for AWS CloudWatch Logs health check."""
        try:
            client = await self._get_aws_client()
            # Attempt to describe log groups to verify connectivity and permissions
            response = await asyncio.shield(self._run_blocking_in_executor(
                lambda: client.describe_log_groups(logGroupNamePrefix=self.log_group_name, limit=1)
            ))
            if 'logGroups' in response:
                return True, "Successfully connected to AWS CloudWatch Logs."
            else:
                alert_operator(f"CRITICAL: AWS CloudWatch health check failed: Unexpected response from describe_log_groups.", level="CRITICAL")
                raise SIEMClientResponseError("Failed to list log groups in AWS CloudWatch (unexpected response).", self.client_type, 500, str(response), correlation_id=self.logger.extra.get('correlation_id'))
        except AWSClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            if error_code == 'AccessDeniedException':
                 alert_operator(f"CRITICAL: AWS CloudWatch authorization failed: {error_message}", level="CRITICAL")
                 raise SIEMClientAuthError(f"AWS CloudWatch authorization failed: {error_message}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
            alert_operator(f"CRITICAL: AWS CloudWatch connectivity error: {error_code}: {error_message}", level="CRITICAL")
            raise SIEMClientConnectivityError(f"AWS Error: {error_code}: {error_message}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
        except Exception as e:
            alert_operator(f"CRITICAL: Unexpected error during AWS CloudWatch health check: {e}", level="CRITICAL")
            raise SIEMClientError(f"Unexpected error during AWS CloudWatch health check: {e}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))

    async def _ensure_log_group_and_stream(self):
        """Ensures the log group and stream exist, respecting auto_create policies."""
        client = await self._get_aws_client()
        
        # Ensure log group exists
        try:
            await asyncio.shield(self._run_blocking_in_executor(lambda: client.describe_log_groups(logGroupNamePrefix=self.log_group_name, limit=1)))
            self.logger.debug(f"Log group {self.log_group_name} exists.", extra=self.logger.extra)
        except AWSClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                if self.auto_create_log_group:
                    self.logger.info(f"Log group {self.log_group_name} not found, attempting to create as auto_create_log_group is True.", extra=self.logger.extra)
                    await asyncio.shield(self._run_blocking_in_executor(lambda: client.create_log_group(logGroupName=self.log_group_name)))
                    self.logger.info(f"Log group {self.log_group_name} created.", extra=self.logger.extra)
                else:
                    alert_operator(f"CRITICAL: Log group {self.log_group_name} not found and auto_create_log_group is False. Aborting log submission.", level="CRITICAL")
                    raise SIEMClientConfigurationError(f"Log group {self.log_group_name} not found and auto-creation is disabled.", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
            else:
                alert_operator(f"CRITICAL: Failed to check/create log group {self.log_group_name}: {e.response.get('Error', {}).get('Message', str(e))}", level="CRITICAL")
                raise SIEMClientPublishError(f"Failed to check/create log group {self.log_group_name}: {e.response.get('Error', {}).get('Message', str(e))}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
        
        # Ensure log stream exists
        try:
            await asyncio.shield(self._run_blocking_in_executor(lambda: client.describe_log_streams(logGroupName=self.log_group_name, logStreamNamePrefix=self.log_stream_name, limit=1)))
            self.logger.debug(f"Log stream {self.log_stream_name} exists.", extra=self.logger.extra)
        except AWSClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                if self.auto_create_log_stream:
                    self.logger.info(f"Log stream {self.log_stream_name} not found, attempting to create as auto_create_log_stream is True.", extra=self.logger.extra)
                    await asyncio.shield(self._run_blocking_in_executor(lambda: client.create_log_stream(logGroupName=self.log_group_name, logStreamName=self.log_stream_name)))
                    self.logger.info(f"Log stream {self.log_stream_name} created.", extra=self.logger.extra)
                else:
                    alert_operator(f"CRITICAL: Log stream {self.log_stream_name} not found and auto_create_log_stream is False. Aborting log submission.", level="CRITICAL")
                    raise SIEMClientConfigurationError(f"Log stream {self.log_stream_name} not found and auto-creation is disabled.", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
            else:
                alert_operator(f"CRITICAL: Failed to check/create log stream {self.log_stream_name}: {e.response.get('Error', {}).get('Message', str(e))}", level="CRITICAL")
                raise SIEMClientPublishError(f"Failed to check/create log stream {self.log_stream_name}: {e.response.get('Error', {}).get('Message', str(e))}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))


    async def _perform_send_log_logic(self, log_entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Internal logic for sending a log to AWS CloudWatch Logs."""
        # Use the batch logic for a single log to avoid code duplication
        success, msg, failed_logs = await self._perform_send_logs_batch_logic([log_entry])
        if success:
            return True, "Log sent to AWS CloudWatch Logs."
        else:
            raise SIEMClientPublishError(f"Failed to send log to AWS CloudWatch: {failed_logs[0]['error']}", self.client_type, details=failed_logs[0], correlation_id=self.logger.extra.get('correlation_id'))

    async def _perform_send_logs_batch_logic(self, log_entries: List[Dict[str, Any]]) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Sends multiple log entries to AWS CloudWatch Logs as a batch."""
        client = await self._get_aws_client()
        await self._ensure_log_group_and_stream()

        # Sort logs by timestamp
        # The `PutLogEvents` API requires events to be in chronological order.
        sorted_log_events = []
        for entry in log_entries:
            ts_iso = entry.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")
            try:
                ts_dt = datetime.datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
                ts_ms = int(ts_dt.timestamp() * 1000)
            except ValueError:
                ts_ms = int(time.time() * 1000) # Fallback to current time
                self.logger.warning(f"Could not parse timestamp_utc '{ts_iso}'. Using current time for batch ordering.", extra=self.logger.extra)
            
            # Message must be a string and is often a JSON dump of the entry
            message = json.dumps(entry)
            
            # The PutLogEvents API has size limits, so we check this
            if sys.getsizeof(message.encode('utf-8')) > self.MAX_BATCH_BYTES:
                self.logger.warning(f"Log event exceeds maximum size limit and will be dropped. Size: {sys.getsizeof(message.encode('utf-8'))} bytes.", extra=self.logger.extra)
                continue

            sorted_log_events.append({
                'timestamp': ts_ms,
                'message': message
            })

        sorted_log_events.sort(key=lambda x: x['timestamp'])

        # Split into smaller batches to respect API limits (10,000 events, 1MB payload)
        # The `PutLogEvents` API has a limit of 10,000 log events per call and a total size limit of 1 MB.
        batches = []
        current_batch = []
        current_size = 0
        for event in sorted_log_events:
            event_size = len(event['message'].encode('utf-8')) + 26 # 26 bytes for timestamp and other overhead
            if len(current_batch) >= self.MAX_BATCH_SIZE or (current_size + event_size) > self.MAX_BATCH_BYTES:
                batches.append(current_batch)
                current_batch = [event]
                current_size = event_size
            else:
                current_batch.append(event)
                current_size += event_size
        if current_batch:
            batches.append(current_batch)
        
        failed_logs = []
        total_sent = 0
        
        for batch in batches:
            next_sequence_token = None
            try:
                response = await asyncio.shield(self._run_blocking_in_executor(lambda: client.describe_log_streams(
                    logGroupName=self.log_group_name,
                    logStreamNamePrefix=self.log_stream_name,
                    limit=1
                )))
                if response and response['logStreams']:
                    next_sequence_token = response['logStreams'][0].get('uploadSequenceToken')
            except AWSClientError as e:
                self.logger.warning(f"Could not get sequence token for {self.log_stream_name}: {e}. Attempting without it.", exc_info=True, extra=self.logger.extra)
                alert_operator(f"WARNING: Could not get sequence token for CloudWatch log stream {self.log_stream_name} during batch send: {e}. Log ingestion might be impacted.", level="WARNING")

            put_log_events_args = {
                'logGroupName': self.log_group_name,
                'logStreamName': self.log_stream_name,
                'logEvents': batch
            }
            if next_sequence_token:
                put_log_events_args['sequenceToken'] = next_sequence_token

            try:
                response = await asyncio.shield(self._run_blocking_in_executor(lambda: client.put_log_events(**put_log_events_args)))
                if response.get('rejectedLogEventsInfo'):
                    rejected_info = response['rejectedLogEventsInfo']
                    self.logger.warning(f"CloudWatch Logs rejected some events in batch: {rejected_info}", extra=self.logger.extra)
                    alert_operator(f"CRITICAL: CloudWatch Logs rejected some events in batch: {rejected_info}. Possible ingestion issue.", level="CRITICAL")
                    # Note: It's hard to map rejected events back to original log_entries
                    # without more info from AWS, so we report the entire batch as failed
                    # for simplicity here.
                    failed_logs.extend([{"error": "Some logs rejected by CloudWatch", "details": rejected_info}])
                else:
                    total_sent += len(batch)

            except Exception as e:
                alert_operator(f"CRITICAL: AWS CloudWatch batch log send failed: {e}", level="CRITICAL")
                # Add all logs from this batch to the failed list
                failed_logs.extend([{"error": str(e), "details": "Failed to send batch."} for _ in batch])
        
        if failed_logs:
            return False, f"Sent {total_sent} of {len(log_entries)} logs with errors.", failed_logs
        
        return True, f"Batch of {len(log_entries)} logs sent to AWS CloudWatch Logs.", []


    async def _perform_query_logs_logic(self, query_string: str, time_range: str, limit: int) -> List[Dict[str, Any]]:
        """Internal logic for querying logs from AWS CloudWatch Logs Insights."""
        client = await self._get_aws_client()

        end_time_ms = int(datetime.datetime.utcnow().timestamp() * 1000) # Use UTC timestamp
        start_time_ms = end_time_ms - self._parse_relative_time_range_to_ms(time_range)

        try:
            query_id_response = await asyncio.shield(self._run_blocking_in_executor(lambda: client.start_query(
                logGroupName=self.log_group_name,
                startTime=start_time_ms,
                endTime=end_time_ms,
                queryString=query_string,
                limit=limit
            )))
            query_id = query_id_response.get('queryId')

            if not query_id:
                alert_operator(f"CRITICAL: Failed to start CloudWatch Logs query. No queryId returned.", level="CRITICAL")
                raise SIEMClientQueryError("Failed to start CloudWatch Logs query.", self.client_type, correlation_id=self.logger.extra.get('correlation_id'))

            status = ''
            results = []
            poll_interval = 1
            max_poll_attempts = int(self.timeout * 2) # Max polling attempts based on client timeout
            attempts = 0
            while status not in ['Complete', 'Failed', 'Cancelled'] and attempts < max_poll_attempts:
                await asyncio.sleep(poll_interval)
                query_results_response = await asyncio.shield(self._run_blocking_in_executor(lambda: client.get_query_results(queryId=query_id)))
                status = query_results_response.get('status')
                attempts += 1
                if status == 'Complete':
                    for result_row in query_results_response.get('results', []):
                        parsed_row = {}
                        for field in result_row:
                            parsed_row[field['field']] = field['value']
                        results.append(parsed_row)
                    break
            
            if status == 'Failed':
                alert_operator(f"CRITICAL: CloudWatch Logs query failed: {query_results_response.get('statusReason', 'No reason provided')}", level="CRITICAL")
                raise SIEMClientQueryError(f"CloudWatch Logs query failed: {query_results_response.get('statusReason', 'No reason provided')}", self.client_type, correlation_id=self.logger.extra.get('correlation_id'))
            
            # The query can be cancelled or timeout. We handle the timeout here.
            # Boto3's `get_query_results` will eventually return a status of `Failed` or `Cancelled`.
            # We explicitly check for a timeout by seeing if we reached max attempts without a final status.
            if attempts >= max_poll_attempts:
                # Attempt to stop the query to clean up resources
                try:
                    await asyncio.shield(self._run_blocking_in_executor(lambda: client.stop_query(queryId=query_id)))
                    self.logger.warning(f"CloudWatch Logs query timed out and was stopped. Query ID: {query_id}", extra=self.logger.extra)
                except AWSClientError as e:
                    self.logger.error(f"Failed to stop timed-out CloudWatch query {query_id}: {e}", exc_info=True, extra=self.logger.extra)
                
                alert_operator(f"CRITICAL: CloudWatch Logs query timed out after {attempts} attempts.", level="CRITICAL")
                raise SIEMClientQueryError(f"CloudWatch Logs query timed out after {attempts} attempts.", self.client_type, correlation_id=self.logger.extra.get('correlation_id'))
            
            return results
        except AWSClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            if error_code == 'MalformedQueryException':
                alert_operator(f"CRITICAL: Invalid CloudWatch Logs query string: {error_message}", level="CRITICAL")
                raise SIEMClientQueryError(f"Invalid CloudWatch Logs query string: {error_message}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
            else:
                alert_operator(f"CRITICAL: AWS CloudWatch Logs query error: {error_code}: {error_message}", level="CRITICAL")
                raise SIEMClientQueryError(f"AWS CloudWatch Logs query error: {error_message}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))
        except SIEMClientError:
            raise # Re-raise custom errors
        except Exception as e:
            alert_operator(f"CRITICAL: Unexpected error during AWS CloudWatch Logs query: {e}", level="CRITICAL")
            raise SIEMClientQueryError(f"Unexpected error during AWS CloudWatch Logs query: {e}", self.client_type, original_exception=e, correlation_id=self.logger.extra.get('correlation_id'))