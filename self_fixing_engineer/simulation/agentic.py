import os
import sys
import asyncio
import logging
import uuid
import json
import pickle
import functools
import io
import argparse
import hmac
import hashlib
import threading
import random
import time
import requests
import atexit
from contextlib import suppress

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Callable, List, Optional, Tuple

import numpy as np

# Pydantic for configuration and input validation
try:
    from pydantic import BaseModel, Field, ValidationError, validator
    from pydantic.functional_validators import model_validator
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.getLogger(__name__).warning("Pydantic not available. Configuration and input validation will be skipped in agentic.py.")

# --- Logging Setup ---
agentic_logger = logging.getLogger("simulation.agentic")
agentic_logger.setLevel(logging.INFO)
if not agentic_logger.hasHandlers():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s'))
    agentic_logger.addHandler(handler)

def alert_operator(message: str, level: str = "CRITICAL"):
    agentic_logger.critical(f"[OPS ALERT - {level}] {message}")

# --- Dependency Management & Self-Healing (Production Fixes) ---
import importlib

def check_and_import(package_name: str, module_name: Optional[str] = None, critical: bool = False):
    try:
        return importlib.import_module(module_name or package_name)
    except ImportError:
        msg = f"Dependency '{package_name}' not found."
        if critical:
            agentic_logger.critical(f"CRITICAL: Required {msg}. Aborting startup.")
            alert_operator(f"CRITICAL: Missing required dependency '{package_name}'. Agentic service cannot start.")
            sys.exit(1)
        else:
            agentic_logger.warning(f"Optional {msg}. Some features will be disabled. Please run 'pip install {package_name}'.")
        return None

httpx = check_and_import("httpx", critical=True)
boto3 = check_and_import("boto3")
minio = check_and_import("minio")
google_cloud_storage = check_and_import("google.cloud", "google.cloud.storage")
web3 = check_and_import("web3")

gym = check_and_import("gymnasium")
sentry_sdk = check_and_import("sentry_sdk")
stable_baselines3 = check_and_import("stable_baselines3")
deap = check_and_import("deap")
ray = check_and_import("ray")
aioredis = check_and_import("aioredis")
nats = check_and_import("nats")
aiokafka = check_and_import("aiokafka")
opentelemetry = check_and_import("opentelemetry")

try:
    from test_generation.audit_log import AuditLogger as DLTLogger
    from test_generation.agentic import SecretsManager as GlobalSecretsManager
    DLT_LOGGER_AVAILABLE = True
except ImportError:
    DLT_LOGGER_AVAILABLE = False
    DLTLogger = None
    GlobalSecretsManager = None
    agentic_logger.warning("DLTLogger or SecretsManager not available. Audit logging will be disabled.")

if opentelemetry:
    from opentelemetry import trace
    from arbiter.otel_config import get_tracer
    tracer = get_tracer(__name__)
else:
    class MockSpan:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
    class MockTracer:
        def start_as_current_span(self, *args, **kwargs):
            return MockSpan()
    tracer = MockTracer()

def async_span(name):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(name):
                return await func(*args, **kwargs)
        return wrapper
    return decorator

SENTRY_ENABLED = False
if sentry_sdk and os.getenv("SENTRY_DSN"):
    try:
        sentry_sdk.init(dsn=os.environ.get("SENTRY_DSN"), traces_sample_rate=1.0)
        SENTRY_ENABLED = True
    except Exception as e:
        agentic_logger.warning(f"Sentry SDK failed to initialize: {e}. Sentry disabled.")

def log_exception_with_sentry(exc: Exception):
    agentic_logger.error(f"Uncaught Exception: {exc}", exc_info=True)
    if SENTRY_ENABLED:
        sentry_sdk.capture_exception(exc)

class SecretsManager:
    def __init__(self):
        self.cache = {}
    def get_secret(self, key: str, default: Optional[str] = None, required: bool = True) -> Optional[str]:
        if key in self.cache:
            return self.cache[key]
        secret_value = os.getenv(key)
        if not secret_value:
            if required:
                msg = f"Missing required secret: {key}. Please configure your secret manager or environment variable."
                agentic_logger.critical(msg)
                if SENTRY_ENABLED: sentry_sdk.capture_message(f"Critical: Missing secret {key}")
                alert_operator(f"Critical: Missing required secret '{key}'.", level="CRITICAL")
                # Raise exception instead of sys.exit() to allow proper error handling
                raise RuntimeError(f"Missing required secret: {key}")
            else:
                self.cache[key] = default
                return default
        self.cache[key] = secret_value
        return secret_value

SECRETS_MANAGER = SecretsManager()

AUDIT_HMAC_KEY_ENV = "AGENTIC_AUDIT_HMAC_KEY"
_audit_hmac_key: Optional[bytes] = None

def _get_audit_hmac_key_agentic() -> bytes:
    global _audit_hmac_key
    if _audit_hmac_key is None:
        key_str = SECRETS_MANAGER.get_secret(AUDIT_HMAC_KEY_ENV, required=False)
        if key_str:
            _audit_hmac_key = key_str.encode('utf-8')
        else:
            _audit_hmac_key = os.urandom(32)
            agentic_logger.warning("AGENTIC_AUDIT_HMAC_KEY_ENV not set. Generated a random key for audit log signing. THIS IS INSECURE FOR PRODUCTION.")
    return _audit_hmac_key

_ = _get_audit_hmac_key_agentic()

class AuditLogger:
    DLQ_PATH = os.getenv("AUDIT_DLQ_PATH", "audit_dlq.jsonl")
    AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "agentic_audit.jsonl")
    AUDIT_INTEGRITY_FILE = os.getenv("AUDIT_INTEGRITY_FILE", "agentic_audit_integrity.json")

    def __init__(self):
        os.makedirs(os.path.dirname(self.AUDIT_LOG_PATH) or '.', exist_ok=True)
        os.makedirs(os.path.dirname(self.DLQ_PATH) or '.', exist_ok=True)
        os.makedirs(os.path.dirname(self.AUDIT_INTEGRITY_FILE) or '.', exist_ok=True)

        self.backend = SECRETS_MANAGER.get_secret("AUDIT_BACKEND", "file", required=False) or "file"
        self.url, self.token, self.api_key, self.bc_url, self.bc_key = None, None, None, None, None

        if self.backend == "splunk":
            self.url = SECRETS_MANAGER.get_secret("SPLUNK_HEC_URL", required=True)
            self.token = SECRETS_MANAGER.get_secret("SPLUNK_HEC_TOKEN", required=True)
        elif self.backend == "elk":
            self.url = SECRETS_MANAGER.get_secret("ELK_LOGSTASH_URL", required=True)
        elif self.backend == "datadog":
            self.url = SECRETS_MANAGER.get_secret("DATADOG_LOG_URL", required=True)
            self.api_key = SECRETS_MANAGER.get_secret("DATADOG_API_KEY", required=True)
        elif self.backend == "blockchain":
            if not web3:
                agentic_logger.critical("Blockchain audit backend selected but web3 not available. Aborting.")
                alert_operator("CRITICAL: Blockchain audit backend selected but web3 not available.", level="CRITICAL")
                sys.exit(1)
            self.bc_url = SECRETS_MANAGER.get_secret("BLOCKCHAIN_NODE_URL", required=True)
            self.bc_key = SECRETS_MANAGER.get_secret("BLOCKCHAIN_SIGN_KEY", required=True)
        elif self.backend == "file":
            agentic_logger.info("Using file-based audit logging.")
        else:
            agentic_logger.critical(f"Unsupported audit backend '{self.backend}'. Aborting.")
            alert_operator(f"CRITICAL: Unsupported audit backend '{self.backend}'. Aborting.", level="CRITICAL")
            sys.exit(1)

        self.max_retries, self.retry_delay_base = 5, 1
        self._dlq_lock = asyncio.Lock()
        self._audit_log_lock = asyncio.Lock()
        self._bg_tasks: list[asyncio.Task] = []
        self._under_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        if not self._under_pytest:
            if not os.path.exists(self.AUDIT_INTEGRITY_FILE):
                with open(self.AUDIT_INTEGRITY_FILE, "w") as f:
                    json.dump(
                        {
                            "last_verified_entry_count": 0,
                            "last_verification_time": datetime.utcnow().isoformat(),
                        },
                        f,
                    )
            try:
                self._loop = asyncio.get_event_loop()
                if self._loop.is_running():
                    self._bg_tasks.append(self._loop.create_task(self.replay_dlq()))
                    self._bg_tasks.append(self._loop.create_task(self._periodic_audit_integrity_check()))
            except RuntimeError:
                self._loop = None
        
        atexit.register(self._sync_shutdown)

    async def shutdown(self):
        """Cancel and await all background tasks on the loop that owns them."""
        if not self._bg_tasks:
            return

        for t in self._bg_tasks:
            if not t.done():
                t.cancel()

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if self._loop and self._loop.is_running() and running is self._loop:
            for t in list(self._bg_tasks):
                with suppress(asyncio.CancelledError):
                    await t
        elif self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._await_tasks(self._bg_tasks), self._loop)
            with suppress(Exception):
                fut.result(timeout=0.75)
        else:
            pass

        self._bg_tasks.clear()

    async def _await_tasks(self, tasks: list[asyncio.Task]):
        for t in tasks:
            with suppress(asyncio.CancelledError):
                await t

    def _sync_shutdown(self):
        """Called by atexit: try to cleanly stop tasks on their home loop."""
        if not self._bg_tasks:
            return
        try:
            if self._loop and self._loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)
                with suppress(Exception):
                    # Increased timeout from 0.75s to 5s to allow tasks to complete
                    fut.result(timeout=5.0)
        except Exception:
            pass

    async def _send_to_backend(self, event: dict):
        event_json_str = json.dumps(event, sort_keys=True, ensure_ascii=False)
        h = hmac.new(_get_audit_hmac_key_agentic(), event_json_str.encode('utf-8'), hashlib.sha256)
        signed_event = {"event": event, "signature": h.hexdigest()}

        if self.backend == "file":
            async with self._audit_log_lock:
                with open(self.AUDIT_LOG_PATH, "a") as f:
                    f.write(json.dumps(signed_event) + "\n")
            agentic_logger.info(f"[AuditLogger] Event written to file: {event.get('event_type')}")
            return

        if self.backend in {"splunk", "elk", "datadog"} and httpx and self.url:
            headers = {}
            if self.backend == "splunk": headers = {"Authorization": f"Splunk {self.token}"}
            elif self.backend == "datadog": headers = {"DD-API-KEY": self.api_key}
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(self.url, headers=headers, json=event)
                resp.raise_for_status()
            agentic_logger.info(f"[AuditLogger] Event sent to {self.backend}: {event.get('event_type')}")
        elif self.backend == "blockchain" and web3 and self.bc_url:
            from web3 import AsyncWeb3, HTTPProvider
            w3 = AsyncWeb3(HTTPProvider(self.bc_url))
            acct = w3.eth.account.from_key(self.bc_key)
            tx = {'from': acct.address, 'value': 0, 'data': w3.to_bytes(text=json.dumps(event)), 'gas': 200_000, 'gasPrice': await w3.eth.gas_price, 'nonce': await w3.eth.get_transaction_count(acct.address)}
            signed = acct.sign_transaction(tx)
            tx_hash = await w3.eth.send_raw_transaction(signed.rawTransaction)
            await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            agentic_logger.info(f"[AuditLogger] Event sent to blockchain: {event.get('event_type')}")
        else:
            raise RuntimeError(f"Audit backend '{self.backend}' is not configured or supported for sending.")

    async def _send_with_retries(self, event: dict):
        for attempt in range(self.max_retries):
            try:
                await self._send_to_backend(event)
                return
            except Exception as e:
                agentic_logger.warning(f"[AuditLogger] Send attempt {attempt+1} failed for event {event.get('event_type')}: {e}")
                log_exception_with_sentry(e)
                await asyncio.sleep(self.retry_delay_base * (2 ** attempt))
        agentic_logger.error(f"[AuditLogger] Event permanently lost after {self.max_retries} retries: {event.get('event_type')}")
        await self.write_to_dlq(event)
        alert_operator(f"Audit event permanently lost for {event.get('event_type')}. Check DLQ.", level="ERROR")

    async def write_to_dlq(self, event: dict):
        try:
            async with self._dlq_lock:
                with open(self.DLQ_PATH, "a") as f:
                    f.write(json.dumps(event) + "\n")
            agentic_logger.warning(f"AuditLogger: Event written to DLQ: {self.DLQ_PATH}")
        except Exception as e:
            agentic_logger.critical(f"AuditLogger: Failed to write event to DLQ: {e}")
            log_exception_with_sentry(e)
            alert_operator(f"CRITICAL: Failed to write event to DLQ: {e}", level="CRITICAL")

    async def replay_dlq(self):
        if not os.path.exists(self.DLQ_PATH): return
        try:
            agentic_logger.info(f"AuditLogger: Replaying DLQ from {self.DLQ_PATH}")
            temp_dlq_path = self.DLQ_PATH + ".tmp"
            
            async with self._dlq_lock:
                with open(self.DLQ_PATH, "r") as f_read:
                    lines = f_read.readlines()
                
                remaining_events = []
                for line in lines:
                    try:
                        event = json.loads(line)
                        await self._send_with_retries(event)
                    except Exception as e:
                        agentic_logger.warning(f"AuditLogger: Failed to replay DLQ event: {e}. Keeping in DLQ.")
                        remaining_events.append(line)
                
                with open(temp_dlq_path, "w") as f_write:
                    f_write.writelines(remaining_events)
                os.replace(temp_dlq_path, self.DLQ_PATH)
            
            agentic_logger.info(f"AuditLogger: DLQ replay complete. {len(remaining_events)} events remain in DLQ.")
            if len(remaining_events) > 0:
                alert_operator(f"Audit DLQ has {len(remaining_events)} unsendable events after replay. Manual inspection needed.", level="WARNING")
        except Exception as e:
            agentic_logger.critical(f"AuditLogger: DLQ replay failed: {e}")
            log_exception_with_sentry(e)
            alert_operator(f"CRITICAL: Audit DLQ replay failed: {e}", level="CRITICAL")

    async def verify_audit_log_integrity(self, max_age_hours: int = 24) -> bool:
        try:
            async with self._audit_log_lock:
                with open(self.AUDIT_INTEGRITY_FILE, "r") as f:
                    integrity_meta = json.load(f)
            
            last_verified_time_str = integrity_meta.get("last_verification_time")
            if last_verified_time_str:
                last_verified_time = datetime.fromisoformat(last_verified_time_str)
                if datetime.utcnow() - last_verified_time < timedelta(hours=max_age_hours):
                    agentic_logger.info("Audit log integrity recently verified. Skipping full check.")
                    return True

            current_entry_count = 0
            mismatched_signatures = 0
            with open(self.AUDIT_LOG_PATH, "r") as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        signed_event = json.loads(line)
                        event_data = signed_event.get("event")
                        signature = signed_event.get("signature")

                        if not event_data or not signature:
                            agentic_logger.error(f"Audit log line {line_num} is malformed (missing event/signature).")
                            mismatched_signatures += 1
                            continue
                        
                        event_json_recreated = json.dumps(event_data, sort_keys=True, ensure_ascii=False)
                        expected_signature = hmac.new(_get_audit_hmac_key_agentic(), event_json_recreated.encode('utf-8'), hashlib.sha256).hexdigest()

                        if signature != expected_signature:
                            agentic_logger.error(f"Audit log integrity compromised: Signature mismatch on line {line_num}. Event: {event_data}")
                            mismatched_signatures += 1
                        current_entry_count += 1
                    except json.JSONDecodeError as e:
                        agentic_logger.error(f"Audit log line {line_num} is not valid JSON: {e}. Line: {line.strip()}")
                        mismatched_signatures += 1
                    except Exception as e:
                        agentic_logger.error(f"Unexpected error during audit log verification on line {line_num}: {e}", exc_info=True)
                        mismatched_signatures += 1

            if mismatched_signatures > 0:
                alert_operator(f"CRITICAL: Agentic Audit log integrity check failed. {mismatched_signatures} signature mismatches found.", level="CRITICAL")
                agentic_logger.critical(f"Agentic Audit log integrity check FAILED. {mismatched_signatures} signature mismatches found.")
                return False
            else:
                agentic_logger.info(f"Agentic Audit log integrity check PASSED. {current_entry_count} entries verified.")
                async with self._audit_log_lock:
                    with open(self.AUDIT_INTEGRITY_FILE, "w") as f:
                        json.dump({"last_verified_entry_count": current_entry_count, "last_verification_time": datetime.utcnow().isoformat()}, f)
                return True

        except FileNotFoundError:
            agentic_logger.warning("Audit log file or integrity meta file not found. Cannot verify integrity.")
            return False
        except Exception as e:
            agentic_logger.critical(f"CRITICAL: Error during audit log integrity verification: {e}", exc_info=True)
            alert_operator(f"CRITICAL: Error during audit log integrity verification: {e}", level="CRITICAL")
            return False

    async def _periodic_audit_integrity_check(self, interval_seconds: int = 3600):
        while True:
            await asyncio.sleep(interval_seconds)
            await self.verify_audit_log_integrity()

    @async_span("log_audit_event")
    async def log_event(self, event_type: str, **kwargs):
        payload = kwargs.get('payload', kwargs)
        event = {
            "event_type": event_type,
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        if self._under_pytest:
            await self._send_with_retries(event)
        else:
            asyncio.create_task(self._send_with_retries(event))
        agentic_logger.debug(f"Audit event '{event_type}' dispatched.")

AUDIT_LOGGER = AuditLogger()

class ObjectStorageClient:
    def __init__(self):
        self.backend = SECRETS_MANAGER.get_secret("OBJ_STORE_BACKEND", "minio", required=False) or "minio"
        self.bucket_name = SECRETS_MANAGER.get_secret("OBJ_BUCKET", "agentic")
        self.max_retries, self.retry_delay_base = 5, 1
        self.client = None
        self.is_connected = False

        try:
            if self.backend == "s3" and boto3:
                self.client = boto3.client("s3")
                self.is_connected = True
            elif self.backend == "minio" and minio:
                from minio import Minio
                self.client = Minio(
                    SECRETS_MANAGER.get_secret("MINIO_ENDPOINT", required=True),
                    access_key=SECRETS_MANAGER.get_secret("MINIO_ACCESS_KEY", required=True),
                    secret_key=SECRETS_MANAGER.get_secret("MINIO_SECRET_KEY", required=True),
                    secure=SECRETS_MANAGER.get_secret("MINIO_SECURE", "False").lower() == "true"
                )
                if not self.client.bucket_exists(self.bucket_name):
                    self.client.make_bucket(self.bucket_name)
                self.is_connected = True
            elif self.backend == "gcs" and google_cloud_storage:
                self.client = google_cloud_storage.Client()
                self.is_connected = True
            else:
                raise ImportError(f"Unsupported or uninstalled object storage backend: {self.backend}")
            
            if not self.is_connected:
                raise RuntimeError(f"Object storage backend '{self.backend}' failed to initialize.")

            agentic_logger.info(f"ObjectStorageClient initialized with backend: {self.backend}")
        except Exception as e:
            agentic_logger.critical(f"CRITICAL: Failed to initialize ObjectStorageClient: {e}. Aborting startup.")
            alert_operator(f"CRITICAL: Object storage backend '{self.backend}' failed to initialize: {e}. Aborting.", level="CRITICAL")
            sys.exit(1)

    async def save_object(self, key: str, data: bytes):
        if not self.is_connected:
            agentic_logger.error("ObjectStorageClient is not connected. Save operation aborted.")
            return
        for attempt in range(self.max_retries):
            try:
                if self.backend == "s3":
                    await asyncio.to_thread(self.client.upload_fileobj, io.BytesIO(data), self.bucket_name, key, ExtraArgs={"ServerSideEncryption": "AES256"})
                elif self.backend == "minio":
                    await asyncio.to_thread(self.client.put_object, self.bucket_name, key, io.BytesIO(data), len(data))
                elif self.backend == "gcs":
                    await asyncio.to_thread(self.client.bucket(self.bucket_name).blob(key).upload_from_string, data)
                agentic_logger.info(f"ObjectStorageClient: Saved '{key}' to {self.backend}.")
                return
            except Exception as e:
                agentic_logger.warning(f"ObjectStorageClient: Failed to save '{key}' (attempt {attempt+1}): {e}")
                log_exception_with_sentry(e)
                if attempt == self.max_retries - 1:
                    agentic_logger.error(f"ObjectStorageClient: Failed to save '{key}' after {self.max_retries} retries.")
                    alert_operator(f"Object storage save failed for key '{key}' after retries: {e}", level="ERROR")
                    raise
                await asyncio.sleep(self.retry_delay_base * (2 ** attempt))

    async def load_object(self, key: str) -> Optional[bytes]:
        if not self.is_connected:
            agentic_logger.error("ObjectStorageClient is not connected. Load operation aborted.")
            return None
        for attempt in range(self.max_retries):
            try:
                if self.backend == "s3":
                    buf = io.BytesIO()
                    await asyncio.to_thread(self.client.download_fileobj, self.bucket_name, key, buf)
                    return buf.getvalue()
                elif self.backend == "minio":
                    return await asyncio.to_thread(lambda: self.client.get_object(self.bucket_name, key).read())
                elif self.backend == "gcs":
                    return await asyncio.to_thread(lambda: self.client.bucket(self.bucket_name).blob(key).download_as_bytes())
            except Exception as e:
                agentic_logger.warning(f"ObjectStorageClient: Failed to load '{key}' (attempt {attempt+1}): {e}")
                log_exception_with_sentry(e)
                if attempt == self.max_retries - 1:
                    agentic_logger.error(f"ObjectStorageClient: Failed to load '{key}' after {self.max_retries} retries.")
                    alert_operator(f"Object storage load failed for key '{key}' after retries: {e}", level="ERROR")
                    return None
                await asyncio.sleep(self.retry_delay_base * (2 ** attempt))
        return None

_OBJECT_STORAGE: Optional[ObjectStorageClient] = None
def get_object_storage() -> ObjectStorageClient:
    global _OBJECT_STORAGE
    if _OBJECT_STORAGE is None:
        _OBJECT_STORAGE = ObjectStorageClient()
    return _OBJECT_STORAGE

class MeshNotifier:
    def __init__(self):
        self.slack_token = SECRETS_MANAGER.get_secret("SLACK_TOKEN", required=False)
        self.teams_webhook = SECRETS_MANAGER.get_secret("TEAMS_WEBHOOK", required=False)
        self.is_configured = bool(self.slack_token or self.teams_webhook)
        if not self.is_configured:
            agentic_logger.warning("MeshNotifier: No Slack or Teams webhook configured. Notifications will be disabled.")

    async def _send_notification(self, url: str, headers: Dict = None, json: Dict = None):
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, headers=headers, json=json)
                    resp.raise_for_status()
                return
            except httpx.HTTPStatusError as e:
                agentic_logger.error(f"[MeshNotifier] HTTP error sending notification (attempt {attempt+1}): {e.response.status_code} - {e.response.text}")
                log_exception_with_sentry(e)
                if attempt == 2: raise
                await asyncio.sleep(1 * (2 ** attempt))
            except httpx.RequestError as e:
                agentic_logger.error(f"[MeshNotifier] Network error sending notification (attempt {attempt+1}): {e}")
                log_exception_with_sentry(e)
                if attempt == 2: raise
                await asyncio.sleep(1 * (2 ** attempt))
            except Exception as e:
                agentic_logger.error(f"[MeshNotifier] Unexpected error sending notification (attempt {attempt+1}): {e}")
                log_exception_with_sentry(e)
                if attempt == 2: raise
                await asyncio.sleep(1 * (2 ** attempt))

    async def notify(self, msg: str, channel: str = "default", urgency: str = "info"):
        if not self.is_configured:
            agentic_logger.debug("MeshNotifier is not configured. Notification skipped.")
            return
        if not httpx:
            agentic_logger.warning("httpx not available. Cannot send notifications.")
            return
        try:
            tasks = []
            if self.slack_token:
                tasks.append(self._send_notification(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {self.slack_token}"},
                    json={"channel": channel, "text": f"[{urgency.upper()}] {msg}"}
                ))
            if self.teams_webhook:
                tasks.append(self._send_notification(
                    self.teams_webhook,
                    json={"text": f"[{urgency.upper()}] {msg}"}
                ))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            agentic_logger.info(f"Notification sent: {msg[:50]}...")
        except Exception as e:
            agentic_logger.error(f"[MeshNotifier] Failed to send notification (outer catch): {e}")
            log_exception_with_sentry(e)

MESH_NOTIFIER = MeshNotifier()

class EventBus:
    def __init__(self):
        self.backend = SECRETS_MANAGER.get_secret("EVENT_BUS_BACKEND", "memory", required=False) or "memory"
        self.client = None
        self.producer = None
        self.consumer = None
        self.is_connected = False
        self._memory_listeners = {}

        if self.backend == "redis":
            if not aioredis:
                agentic_logger.critical("Redis event bus selected but aioredis not available. Aborting.")
                alert_operator("CRITICAL: Redis event bus selected but aioredis not available.", level="CRITICAL")
                sys.exit(1)
            self.redis_url = SECRETS_MANAGER.get_secret("REDIS_URL", "redis://localhost")
        elif self.backend == "nats":
            if not nats:
                agentic_logger.critical("NATS event bus selected but nats-py not available. Aborting.")
                alert_operator("CRITICAL: NATS event bus selected but nats-py not available.", level="CRITICAL")
                sys.exit(1)
            self.nats_url = SECRETS_MANAGER.get_secret("NATS_URL", "nats://localhost:4222")
        elif self.backend == "kafka":
            if not aiokafka:
                agentic_logger.critical("Kafka event bus selected but aiokafka not available. Aborting.")
                alert_operator("CRITICAL: Kafka event bus selected but aiokafka not available.", level="CRITICAL")
                sys.exit(1)
            self.kafka_servers = SECRETS_MANAGER.get_secret("KAFKA_SERVERS", "localhost:9092")
        elif self.backend == "memory":
            agentic_logger.warning("Using memory event bus. This is not suitable for distributed production environments.")
        else:
            agentic_logger.critical(f"Unsupported event bus backend '{self.backend}'. Aborting.")
            alert_operator(f"CRITICAL: Unsupported event bus backend '{self.backend}'. Aborting.", level="CRITICAL")
            sys.exit(1)

    async def connect(self):
        if self.is_connected: return
        agentic_logger.info(f"Connecting to {self.backend} event bus...")
        for attempt in range(5):
            try:
                if self.backend == "memory":
                    self.is_connected = True
                    agentic_logger.info("Memory event bus 'connected'.")
                    return
                elif self.backend == "redis":
                    self.client = aioredis.from_url(self.redis_url)
                    await self.client.ping()
                elif self.backend == "nats":
                    self.client = await nats.connect(self.nats_url)
                elif self.backend == "kafka":
                    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
                    self.producer = AIOKafkaProducer(bootstrap_servers=self.kafka_servers)
                    self.consumer = AIOKafkaConsumer('agentic_events', bootstrap_servers=self.kafka_servers, group_id="agentic_group")
                    await self.producer.start(); await self.consumer.start()
                self.is_connected = True
                agentic_logger.info(f"Successfully connected to {self.backend} event bus.")
                return
            except Exception as e:
                agentic_logger.error(f"Failed to connect to {self.backend} event bus (attempt {attempt+1}): {e}")
                log_exception_with_sentry(e)
                if attempt == 4:
                    agentic_logger.critical(f"CRITICAL: Failed to connect to {self.backend} event bus after multiple retries. Event bus disabled.")
                    alert_operator(f"CRITICAL: Failed to connect to {self.backend} event bus after multiple retries. Event bus disabled.", level="CRITICAL")
                    self.backend = "memory"
                    self.is_connected = True
                    agentic_logger.info("Falling back to memory event bus.")
                    return
                await asyncio.sleep(1 * (2 ** attempt))

    async def disconnect(self):
        if not self.is_connected: return
        agentic_logger.info(f"Disconnecting from {self.backend} event bus...")
        try:
            if self.backend == "memory":
                self.is_connected = False
                self._memory_listeners.clear()
                return
            elif self.backend == "nats" and self.client: await self.client.close()
            elif self.backend == "redis" and self.client: await self.client.close()
            elif self.backend == "kafka":
                if self.producer: await self.producer.stop()
                if self.consumer: await self.consumer.stop()
            self.is_connected = False
        except Exception as e:
            agentic_logger.error(f"Error during event bus disconnection: {e}")
            log_exception_with_sentry(e)

    async def publish(self, topic: str, msg: dict):
        if not self.is_connected:
            agentic_logger.warning("EventBus is not connected. Publish call ignored.")
            return
        encoded_msg = json.dumps(msg).encode('utf-8')
        try:
            if self.backend == "memory":
                for handler in self._memory_listeners.get(topic, []):
                    asyncio.create_task(handler(json.loads(encoded_msg)))
                agentic_logger.info(f"Published event to topic '{topic}' (memory)")
                return
            elif self.backend == "redis": await self.client.publish(topic, encoded_msg)
            elif self.backend == "nats": await self.client.publish(topic, encoded_msg)
            elif self.backend == "kafka": await self.producer.send_and_wait(topic, encoded_msg)
            agentic_logger.info(f"Published event to topic '{topic}'")
        except Exception as e:
            agentic_logger.error(f"Failed to publish event to topic '{topic}': {e}")
            log_exception_with_sentry(e)
            alert_operator(f"Event bus publish failed for topic '{topic}': {e}", level="ERROR")

    async def subscribe(self, topic: str, handler: Callable):
        if not self.is_connected:
            agentic_logger.warning("EventBus is not connected. Subscribe call ignored.")
            return
        agentic_logger.info(f"Subscribing to topic '{topic}'...")
        try:
            if self.backend == "memory":
                self._memory_listeners.setdefault(topic, []).append(handler)
                agentic_logger.info(f"Subscribed to topic '{topic}' (memory)")
                await asyncio.Event().wait()
                return
            elif self.backend == "redis":
                async with self.client.pubsub() as pubsub:
                    await pubsub.subscribe(topic)
                    async for message in pubsub.listen():
                        if message['type'] == 'message':
                            try: await handler(json.loads(message['data'].decode("utf-8")))
                            except Exception as e: agentic_logger.error(f"Error in Redis subscriber handler for topic '{topic}': {e}"); log_exception_with_sentry(e)
            elif self.backend == "nats":
                async def nats_handler(msg):
                    try: await handler(json.loads(msg.data.decode("utf-8")))
                    except Exception as e: agentic_logger.error(f"Error in NATS subscriber handler for topic '{topic}': {e}"); log_exception_with_sentry(e)
                await self.client.subscribe(topic, cb=nats_handler)
                await asyncio.Event().wait()
            elif self.backend == "kafka":
                async for msg in self.consumer:
                    try: await handler(json.loads(msg.value.decode("utf-8")))
                    except Exception as e: agentic_logger.error(f"Error in Kafka subscriber handler for topic '{topic}': {e}"); log_exception_with_sentry(e)
                await asyncio.Event().wait()
        except Exception as e:
            agentic_logger.error(f"Error during subscription to topic '{topic}': {e}")
            log_exception_with_sentry(e)
            alert_operator(f"Event bus subscription failed for topic '{topic}': {e}", level="ERROR")

class PolicyManager:
    def __init__(self):
        self.opa_url = SECRETS_MANAGER.get_secret("OPA_URL", required=False)
        if not self.opa_url:
            agentic_logger.warning("OPA_URL not configured. RBAC will be disabled.")
    def has_permission(self, agent: str, action: str, resource: str) -> bool:
        if not self.opa_url or not httpx:
            agentic_logger.warning("OPA or httpx not available. RBAC check bypassed.")
            return True
        try:
            resp = httpx.post(self.opa_url.rstrip("/") + "/v1/data/agentic/allow", json={"input": {"agent": agent, "action": action, "resource": resource}}, timeout=3)
            resp.raise_for_status()
            return resp.json().get("result", False)
        except Exception as e:
            agentic_logger.error(f"RBAC/OPA check failed: {e}. Defaulting to deny (fail closed).")
            log_exception_with_sentry(e)
            alert_operator(f"RBAC/OPA check failed: {e}", level="ERROR")
            return False

policy_manager = PolicyManager()

def rbac_enforce(agent: str, action: str, resource: str):
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not policy_manager.has_permission(agent, action, resource):
                raise PermissionError(f"Agent '{agent}' not permitted to {action} on {resource}")
            return await fn(*args, **kwargs)
        return wrapper
    return decorator

if PYDANTIC_AVAILABLE:
    class SwarmConfig(BaseModel):
        swarm_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", description="Unique ID for the agent swarm.")
        agents: List[Dict[str, Any]] = Field(..., min_items=1, description="List of configurations for each agent in the swarm.")
        max_concurrency: int = Field(5, ge=1, description="Maximum number of agents to run concurrently.")
else:
    class SwarmConfig:
        def __init__(self, **data):
            self.swarm_id = data.get("swarm_id")
            self.agents = data.get("agents", [])
            self.max_concurrency = int(data.get("max_concurrency", 5))
            if not self.swarm_id or not isinstance(self.agents, list) or len(self.agents) < 1:
                raise ValueError("Invalid swarm config: swarm_id must be a string, agents must be a non-empty list.")
            if not isinstance(self.max_concurrency, int) or self.max_concurrency < 1:
                raise ValueError("Invalid swarm config: max_concurrency must be a positive integer.")

class BaseWorkloadAdapter:
    async def evaluate(self, individual):
        return (0.0,)

class GAOptimizer:
    def __init__(self, n_params: int = 3):
        if not deap:
            agentic_logger.critical("DEAP not available. GAOptimizer cannot be initialized. Aborting.")
            alert_operator("CRITICAL: DEAP not available for GAOptimizer. Aborting.", level="CRITICAL")
            sys.exit(1)
        from deap import base, creator, tools
        if not hasattr(creator, "FitnessMin"): creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        if not hasattr(creator, "Individual"): creator.create("Individual", list, fitness=creator.FitnessMin)
        self.toolbox = base.Toolbox(); self.toolbox.register("attr_float", np.random.uniform, 0, 10); self.toolbox.register("individual", tools.initRepeat, creator.Individual, self.toolbox.attr_float, n=n_params)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual); self.toolbox.register("mate", tools.cxBlend, alpha=0.5); self.toolbox.register("mutate", tools.mutGaussian, mu=0, sigma=1, indpb=0.2)
        self.toolbox.register("select", tools.selTournament, tournsize=3); self.tools = tools
    @async_span("ga_evolve_cycle")
    async def evolve(self, workload_adapter: BaseWorkloadAdapter) -> List[float]:
        from deap import algorithms
        self.toolbox.register("evaluate", workload_adapter.evaluate); self.toolbox.register("map", map)
        if ray:
            try:
                if not ray.is_initialized(): ray.init(logging_level=logging.ERROR, ignore_reinit_error=True)
                self.toolbox.register("map", ray.util.multiprocessing.Pool().map); agentic_logger.info("Using Ray for parallel GA evaluation.")
            except Exception as e:
                agentic_logger.warning(f"Failed to use Ray for GA map: {e}. Falling back to sequential map.")
        pop = self.toolbox.population(n=50); hof = self.tools.HallOfFame(1)
        try:
            pop, log = algorithms.eaSimple(pop, self.toolbox, cxpb=0.7, mutpb=0.2, ngen=20, halloffame=hof, verbose=False)
            agentic_logger.info(f"GA evolved best params: {hof[0]}"); return list(hof[0])
        except Exception as e:
            agentic_logger.error(f"Error during GA evolution: {e}", exc_info=True); log_exception_with_sentry(e)
            alert_operator(f"GA evolution failed: {e}", level="ERROR")
            return [5.0, 2.0, 0.7]

async def run_simulation_swarm(config: Dict[str, Any]) -> Dict[str, Any]:
    # Placeholder metric with a working context manager
    class _NoopTimer:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def time(self): return self
    agentic_swarm_latency = type("Noop", (), {"labels": staticmethod(lambda **kwargs: _NoopTimer())})()

    dlt_logger = None
    with agentic_swarm_latency.labels(swarm_id=config.get("swarm_id")).time():
        try:
            validated_config = SwarmConfig(**config)
            swarm_id = validated_config.swarm_id
            agents_configs = validated_config.agents
            max_concurrency = validated_config.max_concurrency

            if DLT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(kind="agentic", name="swarm_start", detail={"swarm_id": swarm_id, "agent_count": len(agents_configs)}, agent_id="agentic_swarm")
            
            semaphore = asyncio.Semaphore(max_concurrency)
            async def run_single_agent(agent_config):
                async with semaphore:
                    agent_id = agent_config.get("id", str(uuid.uuid4()))
                    agentic_logger.info(f"Starting agent {agent_id} in swarm {swarm_id}...")
                    
                    if dlt_logger: await dlt_logger.add_entry(kind="agentic", name="agent_start", detail={"swarm_id": swarm_id, "agent_id": agent_id}, agent_id=agent_id)
                    
                    await asyncio.sleep(random.uniform(1, 5)) # Simulate agent execution
                    
                    result = {"status": "success", "result": "mock_result"}
                    
                    if dlt_logger: await dlt_logger.add_entry(kind="agentic", name="agent_end", detail={"swarm_id": swarm_id, "agent_id": agent_id, "result": result}, agent_id=agent_id)
                    return result

            tasks = [run_single_agent(cfg) for cfg in agents_configs]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successes = sum(1 for r in results if not isinstance(r, Exception))
            failures = len(results) - successes

            if dlt_logger: await dlt_logger.add_entry(kind="agentic", name="swarm_end", detail={"swarm_id": swarm_id, "successes": successes, "failures": failures}, agent_id="agentic_swarm")

            return {"status": "completed", "swarm_id": swarm_id, "results": results}
        except Exception as e:
            if dlt_logger: await dlt_logger.add_entry(kind="agentic", name="swarm_error", detail={"swarm_id": config.get("swarm_id"), "error": str(e)}, agent_id="agentic_swarm")
            raise

# --- Stubs for undefined components in the main loop ---
class OPERATOR_API:
    @staticmethod
    async def get_health_status():
        agentic_logger.info("Health status stub: Agentic core is running.")
        return {"status": "healthy", "components": {"swarm": "ready", "events": "ready"}}
    @staticmethod
    async def inspect_dlq():
        return [{"event": "mock_event", "reason": "mock_reason"}]
    @staticmethod
    async def clear_dlq():
        return {"status": "success", "message": "DLQ cleared"}

async def demo_run():
    agentic_logger.info("Demo run stub.")
    return {"status": "success", "message": "Demo completed"}

class ImportFixerAutoTuningAdapter(BaseWorkloadAdapter):
    async def evaluate(self, individual):
        agentic_logger.info(f"Evaluating individual: {individual}")
        return (0.0,)

class SelfEvolutionEngine:
    async def start(self, cycles=3):
        agentic_logger.info(f"Self-evolution engine stub: cycles={cycles}")
        return {"status": "success", "message": "Evolution completed"}

# ... other classes and functions ...

async def main_async():
    parser = argparse.ArgumentParser(description="Agentic RL+GA+MetaLearning Engine")
    parser.add_argument("--mode", choices=["evolve", "ga", "health", "demo", "publish", "subscribe", "dlq_inspect", "dlq_clear", "swarm"], default="health")
    parser.add_argument("--topic", help="Topic for publish/subscribe commands")
    parser.add_argument("--message", help="JSON message string for publish command")
    parser.add_argument("--cycles", type=int, default=3, help="Number of evolution cycles to run.")
    args = parser.parse_args()

    # The AuditLogger now handles its own background task scheduling.
    # No need to manually schedule here.

    try:
        if args.mode == "swarm":
            swarm_config = {
                "swarm_id": "example_swarm_123",
                "agents": [
                    {"id": "agent1"},
                    {"id": "agent2"},
                    {"id": "agent3"},
                ]
            }
            results = await run_simulation_swarm(swarm_config)
            print(json.dumps(results, indent=2))
        elif args.mode == "health":
            await OPERATOR_API.get_health_status()
        elif args.mode == "demo":
            await demo_run()
        elif args.mode == "ga":
            adapter = ImportFixerAutoTuningAdapter()
            optimizer = GAOptimizer()
            await optimizer.evolve(adapter)
        elif args.mode == "evolve":
            engine = SelfEvolutionEngine()
            await engine.start(cycles=args.cycles)
        elif args.mode == "publish":
            if not args.topic or not args.message:
                print("Error: --topic and --message are required for publish mode.")
                sys.exit(1)
            bus = EventBus()
            await bus.connect()
            await bus.publish(args.topic, json.loads(args.message))
            await bus.disconnect()
        elif args.mode == "subscribe":
            if not args.topic:
                print("Error: --topic is required for subscribe mode.")
                sys.exit(1)
            bus = EventBus()
            await bus.connect()
            async def handler(msg):
                print(f"Received Message: {msg}")
            await bus.subscribe(args.topic, handler)
        elif args.mode == "dlq_inspect":
            dlq_contents = await OPERATOR_API.inspect_dlq()
            print(json.dumps(dlq_contents, indent=2))
            if not dlq_contents:
                print("DLQ is empty.")
        elif args.mode == "dlq_clear":
            result = await OPERATOR_API.clear_dlq()
            print(json.dumps(result, indent=2))
    except Exception as exc:
        log_exception_with_sentry(exc)
        sys.exit(1)

if __name__ == "__main__":
    if ray:
        try:
            if not ray.is_initialized():
                ray.init(logging_level=logging.ERROR, ignore_reinit_error=True)
        except Exception as e:
            agentic_logger.warning(f"Failed to initialize Ray: {e}. Ray-dependent features may be limited.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        agentic_logger.info("Agentic engine shut down by user.")
    except Exception as e:
        agentic_logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
        log_exception_with_sentry(e)
        sys.exit(1)