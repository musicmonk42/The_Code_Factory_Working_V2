# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import datetime
import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import numpy as np

# --- Optional Dependency Stubs ---
# These are defined here to ensure the names exist in the global scope for
# testing and static analysis, even if the libraries are not installed.
Histogram, Counter, Gauge, CollectorRegistry = None, None, None, None
QuantumCircuit, transpile, AerSimulator = None, None, None
dwavebinarycsp, EmbeddingComposite, DWaveSampler = None, None, None
dual_annealing = None
base, creator, tools = None, None, None
torch, nn = None, None
boto3, ClientError = None, None
retry, stop_after_attempt, wait_exponential, reraise = None, None, None, None
DLTLogger = None


# --- Prometheus client for metrics ---
PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Prometheus client not available. Metrics will not be collected in quantum.py."
    )

# --- Pydantic for input validation ---
PYDANTIC_AVAILABLE = False
try:
    from pydantic import BaseModel, Field, ValidationError, field_validator

    PYDANTIC_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Pydantic not available. Input validation will be skipped in quantum.py."
    )

# --- aiofiles for fallback audit logging ---
AIOFILES_AVAILABLE = False
try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

quantum_logger = logging.getLogger("simulation.quantum")
quantum_logger.setLevel(logging.INFO)
if not quantum_logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    quantum_logger.addHandler(handler)

# --- Metrics (Idempotent and Thread-Safe Registration) ---
_metrics_registry = None
_metrics_lock = threading.Lock()

if PROMETHEUS_AVAILABLE:
    _metrics_registry = CollectorRegistry(auto_describe=True)

if PROMETHEUS_AVAILABLE:

    def get_or_create_metric(
        metric_type, name, documentation, labelnames=None, buckets=None
    ):
        if labelnames is None:
            labelnames = ()
        with _metrics_lock:
            try:
                existing_metric = _metrics_registry._names_to_collectors[name]
                # Check if metric_type is actually a type before using isinstance
                # Use isinstance for better metaclass handling
                if metric_type is not None and isinstance(metric_type, type):
                    if isinstance(existing_metric, metric_type):
                        return existing_metric
                    else:
                        quantum_logger.warning(
                            f"Metric '{name}' already registered with a different type. Reusing existing."
                        )
                        return existing_metric
                else:
                    # If metric_type is not a valid type, return existing metric
                    return existing_metric
            except KeyError:
                if metric_type == Histogram:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        buckets=buckets or Histogram.DEFAULT_BUCKETS,
                        registry=_metrics_registry,
                    )
                elif metric_type == Counter:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                elif metric_type == Gauge:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                else:
                    raise ValueError(f"Unsupported metric type: {metric_type}")
                return metric

    QUANTUM_METRICS = {
        "operation_total": get_or_create_metric(
            Counter,
            "quantum_operation_total",
            "Total quantum operations processed",
            ["operation_type", "backend", "status"],
        ),
        "backend_health": get_or_create_metric(
            Gauge,
            "quantum_backend_health",
            "Health status of quantum/classical backends (1=healthy, 0=unhealthy)",
            ["backend_name"],
        ),
        "input_validation_errors": get_or_create_metric(
            Counter,
            "quantum_input_validation_errors_total",
            "Total input validation errors for quantum operations",
            ["operation_type"],
        ),
        "operation_latency": get_or_create_metric(
            Histogram,
            "quantum_operation_latency_seconds",
            "Latency of quantum operations",
            ["operation_type", "backend"],
        ),
        "alerts_total": get_or_create_metric(
            Counter,
            "quantum_alerts_total",
            "Total alerts raised by the quantum module",
            ["level"],
        ),
    }
else:
    QUANTUM_METRICS = {}

# --- Optional Dependency Imports ---
QISKIT_AVAILABLE = False
try:
    from qiskit import QuantumCircuit, transpile
    from qiskit.providers.aer import AerSimulator

    QISKIT_AVAILABLE = True
    quantum_logger.info("Qiskit available.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="qiskit").set(1)
except ImportError:
    quantum_logger.warning(
        "Qiskit not available. Real quantum circuit backend disabled."
    )
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="qiskit").set(0)

DWAVE_AVAILABLE = False
try:
    import dwavebinarycsp
    from dwave.system import DWaveSampler, EmbeddingComposite

    DWAVE_AVAILABLE = True
    quantum_logger.info("D-Wave Ocean available.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="dwave").set(1)
except ImportError:
    quantum_logger.warning("D-Wave Ocean not available. D-Wave backend disabled.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="dwave").set(0)

SCIPY_AVAILABLE = False
try:
    from scipy.optimize import dual_annealing

    SCIPY_AVAILABLE = True
    quantum_logger.info("scipy available.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="scipy").set(1)
except ImportError:
    quantum_logger.warning(
        "scipy not available. Simulated Annealing backend will not function."
    )
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="scipy").set(0)

DEAP_AVAILABLE = False
try:
    from deap import base, creator, tools

    DEAP_AVAILABLE = True
    quantum_logger.info("deap available.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="ga").set(1)
except ImportError:
    quantum_logger.warning("deap not available. GA backend will not function.")
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(backend_name="ga").set(0)

TORCH_RL_AVAILABLE = False
try:
    import torch
    from torch import nn

    TORCH_RL_AVAILABLE = True
    quantum_logger.info("PyTorch available for QuantumRLAgent.")
except ImportError:
    quantum_logger.warning(
        "PyTorch not available for QuantumRLAgent. Classical RL agent will not function."
    )

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    ClientError = None

try:
    from tenacity import reraise, retry, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        def wrap(f):
            return f

        return wrap

    def stop_after_attempt(x):
        return None

    def wait_exponential(*args, **kwargs):
        return None


try:
    from test_generation.audit_log import AuditLogger as DLTLogger

    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False
    DLTLogger = None


# --- Robust Alerting System ---
async def send_pagerduty_alert(message, level):
    """
    Send alert to PagerDuty using Events API v2.

    Requires PAGERDUTY_ROUTING_KEY environment variable.
    Falls back to logging if not configured or on error.
    """
    routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY")

    if not routing_key:
        quantum_logger.info(
            f"PAGERDUTY ALERT ({level}): {message} [No routing key configured]"
        )
        return

    try:
        # Map severity levels to PagerDuty severity
        severity_map = {
            "CRITICAL": "critical",
            "ERROR": "error",
            "WARNING": "warning",
            "INFO": "info",
        }
        severity = severity_map.get(level, "error")

        # Prepare PagerDuty Events API v2 payload
        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": message[:1024],  # PagerDuty limit
                "severity": severity,
                "source": "quantum_module",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "custom_details": {"level": level, "module": "simulation.quantum"},
            },
        }

        # Send to PagerDuty with retry logic
        if TENACITY_AVAILABLE:

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                reraise=False,
            )
            async def _send_pd():
                async with asyncio.timeout(10):
                    # Use aiohttp if available, otherwise fallback
                    try:
                        import aiohttp

                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                "https://events.pagerduty.com/v2/enqueue",
                                json=payload,
                                headers={"Content-Type": "application/json"},
                            ) as response:
                                if response.status == 202:
                                    quantum_logger.info(
                                        f"PagerDuty alert sent successfully for: {message[:50]}..."
                                    )
                                    return True
                                else:
                                    error_text = await response.text()
                                    quantum_logger.error(
                                        f"PagerDuty API returned {response.status}: {error_text}"
                                    )
                                    return False
                    except ImportError:
                        quantum_logger.warning(
                            "aiohttp not available, PagerDuty alert not sent"
                        )
                        return False

            await _send_pd()
        else:
            # Without tenacity, make single attempt
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://events.pagerduty.com/v2/enqueue",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        if response.status == 202:
                            quantum_logger.info(
                                f"PagerDuty alert sent: {message[:50]}..."
                            )
                        else:
                            quantum_logger.error(
                                f"PagerDuty API error: {response.status}"
                            )
            except ImportError:
                quantum_logger.warning("aiohttp not available for PagerDuty")
            except Exception as e:
                quantum_logger.error(f"Failed to send PagerDuty alert: {e}")

    except Exception as e:
        quantum_logger.error(f"Failed to send PagerDuty alert: {e}")
        # Fallback to logging
        quantum_logger.info(f"PAGERDUTY ALERT (fallback) ({level}): {message}")


async def send_slack_alert(message, level):
    """
    Send alert to Slack using Incoming Webhook.

    Requires SLACK_WEBHOOK_URL environment variable.
    Falls back to logging if not configured or on error.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    if not webhook_url:
        quantum_logger.info(
            f"SLACK ALERT ({level}): {message} [No webhook URL configured]"
        )
        return

    try:
        # Map levels to Slack colors
        color_map = {
            "CRITICAL": "#FF0000",  # Red
            "ERROR": "#FF6B00",  # Orange
            "WARNING": "#FFD700",  # Gold
            "INFO": "#36A64F",  # Green
        }
        color = color_map.get(level, "#808080")

        # Prepare Slack message payload
        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"Quantum Module Alert - {level}",
                    "text": message,
                    "footer": "Quantum Plugin",
                    "ts": int(time.time()),
                }
            ]
        }

        # Send to Slack with retry logic
        if TENACITY_AVAILABLE:

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                reraise=False,
            )
            async def _send_slack():
                async with asyncio.timeout(10):
                    try:
                        import aiohttp

                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                webhook_url,
                                json=payload,
                                headers={"Content-Type": "application/json"},
                            ) as response:
                                if response.status == 200:
                                    quantum_logger.info(
                                        f"Slack alert sent successfully for: {message[:50]}..."
                                    )
                                    return True
                                else:
                                    error_text = await response.text()
                                    quantum_logger.error(
                                        f"Slack webhook returned {response.status}: {error_text}"
                                    )
                                    return False
                    except ImportError:
                        quantum_logger.warning(
                            "aiohttp not available, Slack alert not sent"
                        )
                        return False

            await _send_slack()
        else:
            # Without tenacity, make single attempt
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        if response.status == 200:
                            quantum_logger.info(f"Slack alert sent: {message[:50]}...")
                        else:
                            quantum_logger.error(
                                f"Slack webhook error: {response.status}"
                            )
            except ImportError:
                quantum_logger.warning("aiohttp not available for Slack")
            except Exception as e:
                quantum_logger.error(f"Failed to send Slack alert: {e}")

    except Exception as e:
        quantum_logger.error(f"Failed to send Slack alert: {e}")
        # Fallback to logging
        quantum_logger.info(f"SLACK ALERT (fallback) ({level}): {message}")


async def alert_operator(message: str, level: str = "CRITICAL"):
    """
    Alert operations team through multiple channels.
    Args:
        message: The alert message
        level: Alert level (INFO, WARNING, ERROR, CRITICAL)
    """
    log_level = getattr(logging, level, logging.CRITICAL)
    quantum_logger.log(log_level, f"[OPS ALERT] {message}")
    destinations = ["logs"]
    if level in ("WARNING", "ERROR", "CRITICAL"):
        destinations.append("metrics")
    if level in ("ERROR", "CRITICAL"):
        destinations.append("pagerduty")
    if level == "CRITICAL":
        destinations.append("slack")

    if PROMETHEUS_AVAILABLE and "metrics" in destinations:
        QUANTUM_METRICS.get("alerts_total").labels(level=level).inc()
    if "pagerduty" in destinations and os.environ.get("PAGERDUTY_SERVICE_KEY"):
        await send_pagerduty_alert(message, level)
    if "slack" in destinations and os.environ.get("SLACK_WEBHOOK_URL"):
        await send_slack_alert(message, level)


# --- Flexible Credential Management System ---
class CredentialProvider(ABC):
    """Abstract base class for credential providers."""

    @abstractmethod
    async def get_credentials(self, key: str) -> Dict[str, Any]:
        """Get credentials for the given key."""
        pass


class AWSCredentialProvider(CredentialProvider):
    """AWS Secrets Manager credential provider."""

    async def get_credentials(self, key: str) -> Dict[str, Any]:
        if not BOTO3_AVAILABLE:
            raise RuntimeError("Boto3 not available for AWS secrets")
        client = boto3.client("secretsmanager")
        try:
            response = await asyncio.to_thread(
                client.get_secret_value, SecretId=f"quantum/{key}"
            )
            return json.loads(response["SecretString"])
        except Exception as e:
            quantum_logger.error(f"Failed to load credentials from AWS: {e}")
            raise


class VaultCredentialProvider(CredentialProvider):
    """
    HashiCorp Vault credential provider with caching and TTL.

    Supports multiple authentication methods:
    - Token authentication
    - AppRole authentication
    - Kubernetes authentication

    Environment variables:
        VAULT_ADDR: Vault server address (required)
        VAULT_TOKEN: Direct token authentication
        VAULT_ROLE_ID: AppRole role ID
        VAULT_SECRET_ID: AppRole secret ID
        VAULT_MOUNT_POINT: Secret mount point (default: secret)
        VAULT_NAMESPACE: Vault namespace (optional)
        VAULT_CACERT: Path to CA certificate (optional)
    """

    def __init__(self):
        """Initialize Vault provider with configuration from environment."""
        self.vault_addr = os.environ.get("VAULT_ADDR")
        self.mount_point = os.environ.get("VAULT_MOUNT_POINT", "secret")
        self.namespace = os.environ.get("VAULT_NAMESPACE")
        self.cacert = os.environ.get("VAULT_CACERT")
        self._client = None
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = asyncio.Lock()
        self._default_ttl = 300  # 5 minutes default TTL

        if not self.vault_addr:
            quantum_logger.warning(
                "VAULT_ADDR not set. VaultCredentialProvider will not function."
            )

    async def _get_client(self):
        """Get or create Vault client with authentication."""
        if self._client:
            return self._client

        if not self.vault_addr:
            raise RuntimeError("VAULT_ADDR environment variable not set")

        try:
            import hvac

            # Create client
            client = hvac.Client(
                url=self.vault_addr,
                namespace=self.namespace,
                verify=self.cacert if self.cacert else True,
            )

            # Authenticate using available method
            if os.environ.get("VAULT_TOKEN"):
                # Token authentication
                client.token = os.environ["VAULT_TOKEN"]
                quantum_logger.info("Vault: Using token authentication")

            elif os.environ.get("VAULT_ROLE_ID") and os.environ.get("VAULT_SECRET_ID"):
                # AppRole authentication
                role_id = os.environ["VAULT_ROLE_ID"]
                secret_id = os.environ["VAULT_SECRET_ID"]

                auth_response = client.auth.approle.login(
                    role_id=role_id, secret_id=secret_id
                )
                client.token = auth_response["auth"]["client_token"]
                quantum_logger.info("Vault: Using AppRole authentication")

            elif os.environ.get("VAULT_K8S_ROLE"):
                # Kubernetes authentication
                k8s_role = os.environ["VAULT_K8S_ROLE"]
                jwt_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"

                if os.path.exists(jwt_path):
                    with open(jwt_path, "r") as f:
                        jwt = f.read()

                    auth_response = client.auth.kubernetes.login(role=k8s_role, jwt=jwt)
                    client.token = auth_response["auth"]["client_token"]
                    quantum_logger.info("Vault: Using Kubernetes authentication")
                else:
                    raise RuntimeError("Kubernetes service account token not found")
            else:
                raise RuntimeError(
                    "No Vault authentication method configured. "
                    "Set VAULT_TOKEN, VAULT_ROLE_ID/VAULT_SECRET_ID, or VAULT_K8S_ROLE"
                )

            # Verify authentication
            if not client.is_authenticated():
                raise RuntimeError("Vault authentication failed")

            self._client = client
            quantum_logger.info("Successfully authenticated with Vault")
            return client

        except ImportError:
            quantum_logger.error(
                "hvac (HashiCorp Vault client) not installed. "
                "Install with: pip install hvac"
            )
            raise RuntimeError("hvac package not available")
        except Exception as e:
            quantum_logger.error(f"Failed to initialize Vault client: {e}")
            raise

    async def get_credentials(self, key: str) -> Dict[str, Any]:
        """
        Get credentials from Vault with caching and TTL.

        Args:
            key: Secret path in Vault (e.g., "quantum/api-key")

        Returns:
            Dictionary containing the credentials

        Raises:
            RuntimeError: If Vault is not configured or authentication fails
            ValueError: If secret not found in Vault
        """
        async with self._cache_lock:
            # Check cache first
            if key in self._cache:
                cached_data = self._cache[key]
                if time.time() < cached_data["expiry"]:
                    quantum_logger.debug(f"Vault: Using cached credentials for {key}")
                    return cached_data["value"]
                else:
                    # Cache expired, remove it
                    del self._cache[key]
                    quantum_logger.debug(f"Vault: Cache expired for {key}")

            # Fetch from Vault
            try:
                client = await self._get_client()

                # Read secret from Vault
                # Try KV v2 first (default for newer Vault installations)
                try:
                    secret_response = await asyncio.to_thread(
                        client.secrets.kv.v2.read_secret_version,
                        path=key,
                        mount_point=self.mount_point,
                    )
                    secret_data = secret_response["data"]["data"]
                    quantum_logger.debug(f"Vault: Retrieved secret from KV v2: {key}")

                except Exception:
                    # Fallback to KV v1
                    try:
                        secret_response = await asyncio.to_thread(
                            client.secrets.kv.v1.read_secret,
                            path=key,
                            mount_point=self.mount_point,
                        )
                        secret_data = secret_response["data"]
                        quantum_logger.debug(
                            f"Vault: Retrieved secret from KV v1: {key}"
                        )
                    except Exception as kv1_error:
                        quantum_logger.error(
                            f"Failed to retrieve secret {key} from both KV v2 and v1: {kv1_error}"
                        )
                        raise ValueError(f"Secret not found in Vault: {key}")

                # Determine TTL
                lease_duration = secret_response.get(
                    "lease_duration", self._default_ttl
                )
                if lease_duration == 0:
                    lease_duration = self._default_ttl

                # Cache the credentials
                expiry_time = time.time() + lease_duration
                self._cache[key] = {"value": secret_data, "expiry": expiry_time}

                quantum_logger.info(
                    f"Vault: Cached credentials for {key} with TTL {lease_duration}s"
                )

                return secret_data

            except Exception as e:
                quantum_logger.error(
                    f"Failed to get credentials from Vault for {key}: {e}"
                )

                # Fallback: try to return expired cache if available
                if key in self._cache:
                    quantum_logger.warning(
                        f"Vault: Using expired cache as fallback for {key}"
                    )
                    return self._cache[key]["value"]

                raise

    async def invalidate_cache(self, key: Optional[str] = None):
        """
        Invalidate cached credentials.

        Args:
            key: Specific key to invalidate, or None to clear all cache
        """
        async with self._cache_lock:
            if key:
                if key in self._cache:
                    del self._cache[key]
                    quantum_logger.info(f"Vault: Invalidated cache for {key}")
            else:
                self._cache.clear()
                quantum_logger.info("Vault: Cleared all credential cache")

    async def close(self):
        """Close Vault client and cleanup resources."""
        if self._client:
            # hvac doesn't require explicit close, but we can clean up
            self._client = None
            quantum_logger.info("Vault: Client closed")


class EnvCredentialProvider(CredentialProvider):
    """Environment-based credential provider."""

    async def get_credentials(self, key: str) -> Dict[str, Any]:
        env_var = key.upper().replace("-", "_")
        creds = os.environ.get(f"QUANTUM_CREDS_{env_var}")
        if creds:
            return json.loads(creds)
        raise ValueError(f"Environment variable QUANTUM_CREDS_{env_var} not found")


class FileCredentialProvider(CredentialProvider):
    """File-based credential provider (for development)."""

    async def get_credentials(self, key: str) -> Dict[str, Any]:
        file_path = os.environ.get("QUANTUM_CRED_FILE_PATH", "~/.quantum/creds.json")
        path = os.path.expanduser(file_path)
        if os.path.exists(path):
            with open(path, "r") as f:
                all_creds = json.load(f)
                if key in all_creds:
                    return all_creds[key]
        raise FileNotFoundError(f"Credentials for {key} not found in {path}")


class CredentialManager:
    """Manages credentials from various providers."""

    def __init__(self):
        self.providers: List[CredentialProvider] = []
        self.credentials_cache: Dict[str, Any] = {}
        self.cache_expiry: Dict[str, float] = {}
        self._cache_lock = asyncio.Lock()
        self._register_providers()

    def _register_providers(self):
        """Register credential providers based on configuration."""
        if (
            os.environ.get("QUANTUM_CRED_PROVIDER_AWS", "true").lower() == "true"
            and BOTO3_AVAILABLE
        ):
            self.providers.append(AWSCredentialProvider())
        if os.environ.get("QUANTUM_CRED_PROVIDER_VAULT", "false").lower() == "true":
            self.providers.append(VaultCredentialProvider())
        if os.environ.get("QUANTUM_CRED_PROVIDER_ENV", "true").lower() == "true":
            self.providers.append(EnvCredentialProvider())
        if os.environ.get("QUANTUM_CRED_PROVIDER_FILE", "false").lower() == "true":
            self.providers.append(FileCredentialProvider())

    async def get_credentials(self, key: str) -> Dict[str, Any]:
        """Get credentials from the first successful provider."""
        async with self._cache_lock:
            if key in self.credentials_cache and time.time() < self.cache_expiry.get(
                key, 0
            ):
                return self.credentials_cache[key]
        for provider in self.providers:
            try:
                credentials = await provider.get_credentials(key)
                async with self._cache_lock:
                    self.credentials_cache[key] = credentials
                    self.cache_expiry[key] = time.time() + 3600
                return credentials
            except Exception as e:
                quantum_logger.warning(
                    f"Provider {provider.__class__.__name__} failed: {e}"
                )
                continue
        raise RuntimeError(f"Failed to get credentials for {key} from any provider")


credential_manager = CredentialManager()


async def load_quantum_credentials(backend: str) -> Dict[str, Any]:
    """Load credentials for the specified quantum backend."""
    try:
        return await credential_manager.get_credentials(f"{backend}-credentials")
    except Exception as e:
        quantum_logger.critical(f"Failed to load credentials for {backend}: {e}")
        await alert_operator(f"Failed to load credentials for {backend}", "CRITICAL")
        raise RuntimeError(f"Cannot load credentials for {backend}") from e


# --- Connection Pooling and Resource Management ---
class BackendClientPool:
    """Manages connections to quantum backends."""

    def __init__(self):
        self.clients: Dict[str, Any] = {}
        self.last_used: Dict[str, float] = {}
        self.creation_time: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self):
        """Initialize the client pool."""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        """Periodically clean up unused connections."""
        while True:
            try:
                await asyncio.sleep(300)
                await self._cleanup_unused_clients()
            except asyncio.CancelledError:
                break
            except Exception as e:
                quantum_logger.error(f"Error in client pool cleanup: {e}")

    async def _cleanup_unused_clients(self):
        """Clean up clients that haven't been used in a while."""
        async with self._lock:
            now = time.time()
            expired_keys = []
            for key, last_used_time in self.last_used.items():
                if (now - last_used_time > 1800) or (
                    now - self.creation_time.get(key, now) > 7200
                ):
                    expired_keys.append(key)
            for key in expired_keys:
                client = self.clients.pop(key, None)
                self.last_used.pop(key, None)
                self.creation_time.pop(key, None)
                await self._close_client(key, client)

    async def _close_client(self, key, client):
        """Close a client connection."""
        if client is None:
            return
        try:
            if hasattr(client, "close") and callable(client.close):
                await asyncio.to_thread(client.close)
            quantum_logger.info(f"Closed client connection: {key}")
        except Exception as e:
            quantum_logger.warning(f"Error closing client {key}: {e}")

    async def get_client(self, backend: str, **kwargs) -> Any:
        """Get a client for the specified backend."""
        client_key = f"{backend}:{self._hash_kwargs(kwargs)}"
        async with self._lock:
            if client_key in self.clients:
                self.last_used[client_key] = time.time()
                return self.clients[client_key]

            client = await self._create_client(backend, **kwargs)
            self.clients[client_key] = client
            self.last_used[client_key] = time.time()
            self.creation_time[client_key] = time.time()
            return client

    async def _create_client(self, backend: str, **kwargs) -> Any:
        """Create a new backend client."""
        quantum_logger.info(f"Creating new client for {backend}")
        if backend == "qiskit":
            return await asyncio.to_thread(self._create_qiskit_client, **kwargs)
        elif backend == "dwave":
            credentials = await load_quantum_credentials("dwave")
            token = credentials.get("token")
            return await asyncio.to_thread(self._create_dwave_client, token=token)
        raise ValueError(f"Unknown backend for client creation: {backend}")

    def _create_qiskit_client(self, **kwargs):
        if not QISKIT_AVAILABLE:
            raise RuntimeError("Qiskit not available")
        return AerSimulator()

    def _create_dwave_client(self, token: str):
        if not DWAVE_AVAILABLE:
            raise RuntimeError("D-Wave not available")
        return EmbeddingComposite(DWaveSampler(token=token))

    def _hash_kwargs(self, kwargs):
        """Create a hash of kwargs for client key."""
        # Security: Use SHA-256 instead of MD5 for hashing
        return hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()

    async def close(self):
        """Close all clients and stop cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for key, client in list(self.clients.items()):
                await self._close_client(key, client)
            self.clients.clear()
            self.last_used.clear()
            self.creation_time.clear()


backend_client_pool = BackendClientPool()


# --- Robust Audit Logging ---
class AuditLogger:
    """Handles audit logging with fallback mechanisms."""

    def __init__(self):
        self.dlt_logger: Optional[DLTLogger] = None
        self.fallback_file: Optional[str] = None
        self.initialized = False
        self.sequence = 0
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initialize the audit logger."""
        if self.initialized:
            return
        async with self._lock:
            if self.initialized:
                return
            if AUDIT_LOGGER_AVAILABLE:
                try:
                    self.dlt_logger = DLTLogger.from_environment()
                    quantum_logger.info("DLT audit logger initialized.")
                except Exception as e:
                    quantum_logger.warning(
                        f"Failed to initialize DLT logger: {e}, using fallbacks"
                    )

            if not self.dlt_logger and AIOFILES_AVAILABLE:
                try:
                    audit_dir = os.environ.get("QUANTUM_AUDIT_DIR", "./audit_logs")
                    os.makedirs(audit_dir, exist_ok=True)
                    self.fallback_file = os.path.join(
                        audit_dir, f"quantum_audit_{time.strftime('%Y%m%d')}.log"
                    )
                    quantum_logger.info(
                        f"Using file-based audit logging: {self.fallback_file}"
                    )
                except Exception as e:
                    quantum_logger.error(
                        f"Failed to set up fallback audit logging: {e}"
                    )
            self.initialized = True

    async def log_event(
        self,
        kind: str,
        name: str,
        details: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ):
        """Log an audit event."""
        if not self.initialized:
            await self.initialize()

        async with self._lock:
            self.sequence += 1
            if not correlation_id:
                correlation_id = f"quantum-{int(time.time())}-{self.sequence}"

        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "correlation_id": correlation_id,
            "kind": kind,
            "name": name,
            "details": details,
        }

        if self.dlt_logger:
            try:
                await self.dlt_logger.add_entry(
                    kind=kind,
                    name=name,
                    detail=details,
                    agent_id="quantum_plugin",
                    correlation_id=correlation_id,
                )
                return
            except Exception as e:
                quantum_logger.warning(f"DLT audit logging failed: {e}, using fallback")

        if self.fallback_file:
            try:
                async with aiofiles.open(self.fallback_file, "a") as f:
                    await f.write(json.dumps(log_entry) + "\n")
                return
            except Exception as e:
                quantum_logger.warning(f"File audit logging failed: {e}, using console")

        quantum_logger.info(f"AUDIT: {json.dumps(log_entry)}")


audit_logger = AuditLogger()


# --- Backend Enforcement (Critical Fix) ---
def check_any_backend_available():
    """Checks if at least one quantum or classical backend is available."""
    if not (QISKIT_AVAILABLE or DWAVE_AVAILABLE or SCIPY_AVAILABLE or DEAP_AVAILABLE):
        quantum_logger.critical(
            "CRITICAL: No quantum or classical backends are available. Aborting."
        )
        if PROMETHEUS_AVAILABLE:
            QUANTUM_METRICS["backend_health"].labels(
                backend_name="overall_quantum_module"
            ).set(0)
        raise RuntimeError(
            "No quantum or classical backends are available. Quantum features disabled."
        )
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["backend_health"].labels(
            backend_name="overall_quantum_module"
        ).set(1)


async def check_backend_health(backend: str) -> bool:
    """Performs a health check on a given quantum backend."""
    try:
        if backend == "qiskit" and QISKIT_AVAILABLE:
            client = await backend_client_pool.get_client("qiskit")
            client.status()
            if PROMETHEUS_AVAILABLE:
                QUANTUM_METRICS["backend_health"].labels(backend_name="qiskit").set(1)
            return True
        if backend == "dwave" and DWAVE_AVAILABLE:
            client = await backend_client_pool.get_client("dwave")
            client.sampler.client.is_solvent()
            if PROMETHEUS_AVAILABLE:
                QUANTUM_METRICS["backend_health"].labels(backend_name="dwave").set(1)
            return True
        if backend == "scipy" and SCIPY_AVAILABLE:
            if PROMETHEUS_AVAILABLE:
                QUANTUM_METRICS["backend_health"].labels(backend_name="scipy").set(1)
            return True
        if backend == "ga" and DEAP_AVAILABLE:
            if PROMETHEUS_AVAILABLE:
                QUANTUM_METRICS["backend_health"].labels(backend_name="ga").set(1)
            return True
    except Exception as e:
        quantum_logger.warning(f"Health check for backend '{backend}' failed: {e}")
        if PROMETHEUS_AVAILABLE:
            QUANTUM_METRICS["backend_health"].labels(backend_name=backend).set(0)
    return False


# --- Input Validation (Harden) ---
def _validate_secure_path_logic(v: str) -> str:
    """Shared logic for secure path validation."""
    if not isinstance(v, str) or not v:
        raise ValueError("code_file must be a non-empty string")

    v_norm = os.path.normpath(v)
    if ".." in v_norm.split(os.path.sep):
        raise ValueError("Path traversal detected in code_file")

    allowed_dirs_str = os.environ.get("QUANTUM_ALLOWED_DIRS", "")
    allowed_dirs = (
        allowed_dirs_str.split(":")
        if allowed_dirs_str
        else ["/opt/quantum/code", "./code", "./examples"]
    )

    abs_path = os.path.abspath(v_norm)
    allowed_abs_dirs = [os.path.abspath(d) for d in allowed_dirs]
    if not any(abs_path.startswith(allowed_dir) for allowed_dir in allowed_abs_dirs):
        raise ValueError(
            f"Code file must be in one of the allowed directories: {', '.join(allowed_dirs)}"
        )

    if not os.path.isfile(abs_path):
        raise ValueError(f"File does not exist: {v}")

    allowed_extensions = [".py", ".qasm", ".qiskit"]
    if not any(abs_path.endswith(ext) for ext in allowed_extensions):
        raise ValueError(
            f"File must have one of the following extensions: {', '.join(allowed_extensions)}"
        )

    if os.name != "nt":
        file_stat = os.stat(abs_path)
        if file_stat.st_mode & 0o777 & ~0o644:
            raise ValueError(
                f"File has unsafe permissions: {oct(file_stat.st_mode & 0o777)}"
            )
    return v_norm


def _create_run_mutation_params_class():
    """Factory function to create RunMutationCircuitParams class at runtime."""
    if PYDANTIC_AVAILABLE:
        class _RunMutationCircuitParams(BaseModel):
            code_file: str = Field(
                ...,
                min_length=1,
                max_length=255,
                description="Path to the code file for mutation.",
            )
            backend: str = Field(
                "auto",
                pattern="^(auto|qiskit|dwave|scipy)$",
                description="Backend to use for mutation.",
            )
            n_qubits: int = Field(
                5, ge=1, le=10, description="Number of qubits for Qiskit circuit."
            )
            n_vars: int = Field(
                5, ge=1, le=10, description="Number of variables for D-Wave problem."
            )
            backend_config: Dict[str, Any] = Field(
                default_factory=dict,
                description="Configuration specific to the chosen backend.",
            )

            @field_validator("code_file")
            @classmethod
            def validate_secure_path(cls, v: str) -> str:
                return _validate_secure_path_logic(v)
        return _RunMutationCircuitParams
    else:
        class _RunMutationCircuitParams:
            def __init__(self, **kwargs):
                self.code_file = kwargs.get("code_file", "N/A")
                self.backend = kwargs.get("backend", "auto")
                self.n_qubits = kwargs.get("n_qubits", 5)
                self.n_vars = kwargs.get("n_vars", 5)
                self.backend_config = kwargs.get("backend_config", {})
                self.validate()

            def validate(self):
                self.code_file = _validate_secure_path_logic(self.code_file)
                if self.backend not in ["auto", "qiskit", "dwave", "scipy"]:
                    raise ValueError("backend must be one of: auto, qiskit, dwave, scipy")
                if not (isinstance(self.n_qubits, int) and 1 <= self.n_qubits <= 10):
                    raise ValueError("n_qubits must be an integer between 1 and 10")
                if not (isinstance(self.n_vars, int) and 1 <= self.n_vars <= 10):
                    raise ValueError("n_vars must be an integer between 1 and 10")
                if not isinstance(self.backend_config, dict):
                    raise ValueError("backend_config must be a dictionary")
        return _RunMutationCircuitParams


def _create_forecast_params_class():
    """Factory function to create ForecastFailureTrendParams class at runtime."""
    if PYDANTIC_AVAILABLE:
        class _ForecastFailureTrendParams(BaseModel):
            trend_data: List[float] = Field(
                ..., min_length=2, description="List of historical trend data points."
            )

            @field_validator("trend_data")
            @classmethod
            def check_trend_data_values(cls, v: List[float]) -> List[float]:
                if not all(isinstance(x, (int, float)) for x in v):
                    raise ValueError("All trend_data elements must be numbers.")
                return v
        return _ForecastFailureTrendParams
    else:
        class _ForecastFailureTrendParams:
            def __init__(self, **kwargs):
                self.trend_data = kwargs.get("trend_data", [])
                self.validate()

            def validate(self):
                if not isinstance(self.trend_data, list) or len(self.trend_data) < 2:
                    raise ValueError(
                        "trend_data must be a list with at least two elements."
                    )
                if not all(isinstance(x, (int, float)) for x in self.trend_data):
                    raise ValueError("All trend_data elements must be numbers.")
        return _ForecastFailureTrendParams


# Create classes using factory functions - this prevents AST parsing issues with conditional definitions
RunMutationCircuitParams = _create_run_mutation_params_class()
ForecastFailureTrendParams = _create_forecast_params_class()


# --- Circuit Optimization ---
def optimize_quantum_circuit(
    circuit: QuantumCircuit, optimization_level: int = 1
) -> QuantumCircuit:
    if not QISKIT_AVAILABLE:
        return circuit
    try:
        return transpile(circuit, optimization_level=optimization_level)
    except Exception as e:
        quantum_logger.warning(
            f"Circuit optimization failed: {e}, using original circuit"
        )
        return circuit


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _execute_qiskit_job(qc, backend_sim, shots):
    optimized_qc = optimize_quantum_circuit(qc)
    transpiled = transpile(optimized_qc, backend_sim)
    job = backend_sim.run(transpiled, shots=shots)
    return job.result()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _execute_dwave_sampler(bqm, sampler, num_reads):
    return sampler.sample(bqm, num_reads=num_reads)


# --- Quantum Mutation using Qiskit, D-Wave, or Classical ---
async def run_quantum_mutation(
    code_file: str, backend: str = "auto", config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    correlation_id = f"quantum-mutation-{code_file}-{time.time()}"
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["operation_total"].labels(
            operation_type="run_mutation_circuit", backend=backend, status="attempt"
        ).inc()

    try:
        params_class = (
            RunMutationCircuitParams if PYDANTIC_AVAILABLE else RunMutationCircuitParams
        )
        params = params_class(code_file=code_file, backend=backend, **(config or {}))
        n_qubits = params.n_qubits
        n_vars = params.n_vars
    except (ValidationError, ValueError) as e:
        quantum_logger.error(f"Input validation failed for run_quantum_mutation: {e}")
        if PROMETHEUS_AVAILABLE:
            QUANTUM_METRICS["input_validation_errors"].labels(
                operation_type="run_mutation_circuit"
            ).inc()
        await audit_logger.log_event(
            "quantum",
            "mutation_error",
            {"code_file": code_file, "backend": backend, "error": str(e)},
            correlation_id,
        )
        return {"status": "ERROR", "reason": f"Input validation failed: {e}"}

    with (
        QUANTUM_METRICS["operation_latency"]
        .labels(operation_type="run_mutation_circuit", backend=backend)
        .time()
    ):
        # Qiskit Backend
        if backend in ("auto", "qiskit") and QISKIT_AVAILABLE:
            try:
                qc = QuantumCircuit(n_qubits)
                for i in range(n_qubits):
                    qc.h(i)
                for i in range(n_qubits - 1):
                    qc.cx(i, i + 1)
                qc.measure_all()
                backend_sim = await backend_client_pool.get_client("qiskit")
                result = await asyncio.to_thread(
                    _execute_qiskit_job, qc, backend_sim, 1024
                )
                counts = result.get_counts()
                bitstring = max(counts, key=counts.get)
                fitness = int(bitstring, 2)
                quantum_logger.info(
                    f"Qiskit mutation: best={bitstring} (fitness={fitness})"
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="qiskit",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation",
                    {"code_file": code_file, "backend": "qiskit", "result": fitness},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "quantum_result": fitness,
                    "mutated_state": bitstring,
                    "backend": "qiskit",
                }
            except Exception as e:
                quantum_logger.error(
                    f"Qiskit quantum mutation failed: {e}", exc_info=True
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="qiskit",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation_error",
                    {"code_file": code_file, "backend": "qiskit", "error": str(e)},
                    correlation_id,
                )
                if backend == "qiskit":
                    return {"status": "ERROR", "reason": f"Qiskit backend failed: {e}"}

        # D-Wave Backend
        if backend in ("auto", "dwave") and DWAVE_AVAILABLE:
            try:
                csp = dwavebinarycsp.ConstraintSatisfactionProblem(
                    dwavebinarycsp.BINARY
                )
                for i in range(n_vars):
                    csp.add_constraint(
                        lambda *args: sum(args) % 2 == 0, [f"x{i}", f"x{(i+1)%n_vars}"]
                    )
                bqm = dwavebinarycsp.stitch(csp)
                sampler = await backend_client_pool.get_client("dwave")
                sample = await asyncio.to_thread(
                    _execute_dwave_sampler, bqm, sampler, 10
                )
                best = next(iter(sample))
                fitness = sum(best.values())
                quantum_logger.info(
                    f"D-Wave mutation: result={best}, fitness={fitness}"
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="dwave",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation",
                    {"code_file": code_file, "backend": "dwave", "result": fitness},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "quantum_result": fitness,
                    "mutated_state": dict(best),
                    "backend": "dwave",
                }
            except Exception as e:
                quantum_logger.error(
                    f"D-Wave quantum mutation failed: {e}", exc_info=True
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="dwave",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation_error",
                    {"code_file": code_file, "backend": "dwave", "error": str(e)},
                    correlation_id,
                )
                if backend == "dwave":
                    return {"status": "ERROR", "reason": f"D-Wave backend failed: {e}"}

        # Scipy (Classical) Fallback
        if SCIPY_AVAILABLE:
            try:

                def fitness(x):
                    return np.sum(x**2)

                bounds = [(-5, 5)] * 10
                result = await asyncio.to_thread(dual_annealing, fitness, bounds)
                quantum_logger.info(f"SA mutation: best fitness={result.fun}")
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="scipy",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation",
                    {"code_file": code_file, "backend": "scipy", "result": result.fun},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "quantum_result": float(result.fun),
                    "mutated_state": result.x.tolist(),
                    "backend": "scipy",
                }
            except Exception as e:
                quantum_logger.error(f"Scipy SA mutation failed: {e}", exc_info=True)
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="run_mutation_circuit",
                        backend="scipy",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "mutation_error",
                    {"code_file": code_file, "backend": "scipy", "error": str(e)},
                    correlation_id,
                )
                if backend == "scipy":
                    return {"status": "ERROR", "reason": f"Scipy backend failed: {e}"}

        quantum_logger.error("No suitable backend available for mutation.")
        if PROMETHEUS_AVAILABLE:
            QUANTUM_METRICS["operation_total"].labels(
                operation_type="run_mutation_circuit", backend="none", status="failed"
            ).inc()
        await audit_logger.log_event(
            "quantum",
            "mutation_error",
            {"code_file": code_file, "backend": "none", "error": "No suitable backend"},
            correlation_id,
        )
        return {
            "status": "ERROR",
            "reason": "No suitable backend available for mutation.",
        }


# --- Quantum Forecast using Qiskit, D-Wave, or Classical GA ---
async def quantum_forecast_failure(trend_data: List[float]) -> Dict[str, Any]:
    correlation_id = f"quantum-forecast-{time.time()}"
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["operation_total"].labels(
            operation_type="forecast_failure_trend", backend="auto", status="attempt"
        ).inc()

    try:
        params = ForecastFailureTrendParams(trend_data=trend_data)
        validated_trend = params.trend_data
    except (ValidationError, ValueError) as e:
        quantum_logger.error(
            f"Input validation failed for quantum_forecast_failure: {e}"
        )
        if PROMETHEUS_AVAILABLE:
            QUANTUM_METRICS["input_validation_errors"].labels(
                operation_type="forecast_failure_trend"
            ).inc()
        await audit_logger.log_event(
            "quantum", "forecast_error", {"error": str(e)}, correlation_id
        )
        return {"status": "ERROR", "reason": f"Input validation failed: {e}"}

    with (
        QUANTUM_METRICS["operation_latency"]
        .labels(operation_type="forecast_failure_trend", backend="auto")
        .time()
    ):
        if QISKIT_AVAILABLE:
            try:
                n_qubits = min(5, len(validated_trend))
                qc = QuantumCircuit(n_qubits)
                for i in range(n_qubits):
                    qc.h(i)
                qc.measure_all()
                backend_sim = await backend_client_pool.get_client("qiskit")
                result = await asyncio.to_thread(
                    _execute_qiskit_job, qc, backend_sim, 256
                )
                counts = result.get_counts()
                bitstrings = [int(b, 2) for b in counts.keys()]
                weights = np.array(list(counts.values())) / sum(counts.values())
                forecast = np.average(bitstrings, weights=weights)
                quantum_logger.info(f"Qiskit forecast: {forecast}")
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="qiskit",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast",
                    {"backend": "qiskit", "result": forecast},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "forecast": float(forecast),
                    "backend": "qiskit",
                }
            except Exception as e:
                quantum_logger.error(
                    f"Qiskit quantum forecast failed: {e}", exc_info=True
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="qiskit",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast_error",
                    {"backend": "qiskit", "error": str(e)},
                    correlation_id,
                )

        if DWAVE_AVAILABLE:
            try:
                n_vars = min(5, len(validated_trend))
                csp = dwavebinarycsp.ConstraintSatisfactionProblem(
                    dwavebinarycsp.BINARY
                )
                for i in range(n_vars):
                    csp.add_constraint(
                        lambda *args: sum(args) % 2 == 1, [f"x{i}", f"x{(i+1)%n_vars}"]
                    )
                bqm = dwavebinarycsp.stitch(csp)
                sampler = await backend_client_pool.get_client("dwave")
                sample = await asyncio.to_thread(
                    _execute_dwave_sampler, bqm, sampler, 10
                )
                best = next(iter(sample))
                forecast = sum(best.values()) + random.uniform(-1, 1)
                quantum_logger.info(f"D-Wave forecast: {forecast}")
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="dwave",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast",
                    {"backend": "dwave", "result": forecast},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "forecast": float(forecast),
                    "backend": "dwave",
                }
            except Exception as e:
                quantum_logger.error(
                    f"D-Wave quantum forecast failed: {e}", exc_info=True
                )
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="dwave",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast_error",
                    {"backend": "dwave", "error": str(e)},
                    correlation_id,
                )

        if DEAP_AVAILABLE and len(validated_trend) >= 2:
            try:
                n = len(validated_trend)
                target = validated_trend[-1]
                if not hasattr(creator, "FitnessMin"):
                    creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
                if not hasattr(creator, "Individual"):
                    creator.create("Individual", list, fitness=creator.FitnessMin)
                toolbox = base.Toolbox()
                toolbox.register(
                    "individual",
                    tools.initRepeat,
                    creator.Individual,
                    lambda: random.uniform(-1, 1),
                    n=n,
                )
                toolbox.register(
                    "population", tools.initRepeat, list, toolbox.individual
                )
                toolbox.register("mate", tools.cxTwoPoint)
                toolbox.register("mutate", tools.mutGaussian, mu=0, sigma=1, indpb=0.2)
                toolbox.register("select", tools.selTournament, tournsize=3)

                def evaluate(ind):
                    return (np.sqrt(np.mean((np.array(ind) - target) ** 2)),)

                if "evaluate" in toolbox.__dict__:
                    del toolbox.evaluate
                toolbox.register("evaluate", evaluate)
                pop, NGEN, CXPB, MUTPB = toolbox.population(n=50), 10, 0.5, 0.2
                for gen in range(NGEN):
                    offspring = list(map(toolbox.clone, toolbox.select(pop, len(pop))))
                    for c1, c2 in zip(offspring[::2], offspring[1::2]):
                        if random.random() < CXPB:
                            toolbox.mate(c1, c2)
                            del c1.fitness.values
                            del c2.fitness.values
                    for m in offspring:
                        if random.random() < MUTPB:
                            toolbox.mutate(m)
                            del m.fitness.values
                    invalid = [ind for ind in offspring if not ind.fitness.valid]
                    fitnesses = map(toolbox.evaluate, invalid)
                    for ind, fit in zip(invalid, fitnesses):
                        ind.fitness.values = fit
                    pop[:] = offspring
                best = tools.selBest(pop, 1)[0]
                forecast = np.mean(best)
                quantum_logger.info(f"GA forecast: {forecast}")
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="ga",
                        status="completed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast",
                    {"backend": "ga", "result": forecast},
                    correlation_id,
                )
                return {
                    "status": "COMPLETED",
                    "forecast": float(forecast),
                    "backend": "ga",
                }
            except Exception as e:
                quantum_logger.error(f"DEAP GA forecast failed: {e}", exc_info=True)
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type="forecast_failure_trend",
                        backend="ga",
                        status="failed",
                    ).inc()
                await audit_logger.log_event(
                    "quantum",
                    "forecast_error",
                    {"backend": "ga", "error": str(e)},
                    correlation_id,
                )

    quantum_logger.warning(
        "No suitable backend for forecasting. Returning mean as fallback."
    )
    if PROMETHEUS_AVAILABLE:
        QUANTUM_METRICS["operation_total"].labels(
            operation_type="forecast_failure_trend", backend="none", status="fallback"
        ).inc()
    fallback_result = float(np.mean(validated_trend)) if validated_trend else 0.5
    await audit_logger.log_event(
        "quantum",
        "forecast",
        {"backend": "fallback_mean", "result": fallback_result},
        correlation_id,
    )
    return {
        "status": "COMPLETED",
        "forecast": fallback_result,
        "backend": "fallback_mean",
    }


# --- QuantumRLAgent ---
class QuantumRLAgent(nn.Module if TORCH_RL_AVAILABLE else object):
    """Hybrid Quantum-Classical Reinforcement Learning Agent."""

    def __init__(self, state_dim: int, action_dim: int):
        if not TORCH_RL_AVAILABLE:
            raise RuntimeError(
                "PyTorch not available! Cannot initialize QuantumRLAgent."
            )
        super().__init__()
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.use_quantum = QISKIT_AVAILABLE

        if self.use_quantum:
            quantum_logger.info("Initializing hybrid quantum-classical RL agent")
            self.pre_process = nn.Linear(state_dim, 4)
            self.post_process_actor = nn.Sequential(
                nn.Linear(4, action_dim), nn.Softmax(dim=-1)
            )
            self.critic = nn.Sequential(
                nn.Linear(state_dim, 64), nn.Tanh(), nn.Linear(64, 1)
            )
            try:
                self.simulator = AerSimulator()
                self._build_quantum_circuit()
            except Exception as e:
                quantum_logger.warning(
                    f"Failed to init quantum circuit: {e}, falling back to classical"
                )
                self.use_quantum = False
                self._fallback_to_classical()
        else:
            quantum_logger.info(
                "Initializing classical RL agent (quantum backend unavailable)"
            )
            self._fallback_to_classical()

    def _build_quantum_circuit(self):
        self.q_params = nn.Parameter(torch.randn(4 * 3))

    def _fallback_to_classical(self):
        self.actor = nn.Sequential(
            nn.Linear(self.state_dim, 64),
            nn.Tanh(),
            nn.Linear(64, self.action_dim),
            nn.Softmax(dim=-1),
        )
        self.critic = nn.Sequential(
            nn.Linear(self.state_dim, 64), nn.Tanh(), nn.Linear(64, 1)
        )

    def _execute_quantum_circuit(self, x):
        qc = QuantumCircuit(4, 4)
        params = self.pre_process(x)
        for i in range(4):
            qc.rx(params[i].item() + self.q_params[i].item(), i)
        for i in range(4):
            qc.rz(self.q_params[i + 4].item(), i)
        for i in range(3):
            qc.cx(i, i + 1)
        for i in range(4):
            qc.ry(self.q_params[i + 8].item(), i)
        qc.measure_all()
        result = self.simulator.run(qc, shots=100).result()
        counts = result.get_counts(qc)
        probs = torch.zeros(4)
        total = sum(counts.values())
        for bitstring, count in counts.items():
            idx = int(bitstring, 2) % self.action_dim
            probs[idx] += count / total
        return probs

    def forward(self, x):
        if not self.use_quantum:
            return self.actor(x), self.critic(x)
        try:
            with torch.no_grad():
                quantum_probs = self._execute_quantum_circuit(x)
            action_probs = self.post_process_actor(quantum_probs)
            value = self.critic(x)
            return action_probs, value
        except Exception as e:
            quantum_logger.warning(
                f"Quantum circuit execution failed: {e}, using classical fallback"
            )
            actor_output = (
                self.actor(x)
                if hasattr(self, "actor")
                else self.post_process_actor(torch.zeros(4))
            )
            return actor_output, self.critic(x)


# --- Lifecycle Management ---
async def initialize_quantum_module():
    quantum_logger.info("Initializing quantum module...")
    check_any_backend_available()
    await backend_client_pool.initialize()
    await audit_logger.initialize()
    quantum_logger.info("Quantum module initialization complete.")


async def shutdown_quantum_module():
    quantum_logger.info("Shutting down quantum module...")
    await backend_client_pool.close()
    quantum_logger.info("Quantum module shut down.")


# --- API for Plugins/Agents ---
class QuantumPluginAPI:
    def __init__(self):
        self._initialized = False
        quantum_logger.info("QuantumPluginAPI created (not yet initialized).")

    async def initialize(self):
        """Initialize the quantum API and its resources."""
        if not self._initialized:
            try:
                await initialize_quantum_module()
                self._initialized = True
                quantum_logger.info("QuantumPluginAPI initialized.")
            except RuntimeError as e:
                quantum_logger.critical(f"Failed to initialize QuantumPluginAPI: {e}")
                # Raising the exception here allows the caller to handle it gracefully
                raise e

    async def shutdown(self):
        """Shutdown the quantum API and release resources."""
        if self._initialized:
            await shutdown_quantum_module()
            self._initialized = False
            quantum_logger.info("QuantumPluginAPI shut down.")

    def get_available_backends(self) -> List[str]:
        return [
            b
            for b, available in [
                ("qiskit", QISKIT_AVAILABLE),
                ("dwave", DWAVE_AVAILABLE),
                ("scipy", SCIPY_AVAILABLE),
                ("ga", DEAP_AVAILABLE),
            ]
            if available
        ]

    async def perform_quantum_operation(
        self, operation_type: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        quantum_logger.info(
            f"Plugin requested quantum operation: {operation_type} with params: {params}"
        )
        if not self._initialized:
            return {
                "status": "ERROR",
                "reason": "API not initialized. Call initialize() first.",
            }
        try:
            if operation_type == "run_mutation_circuit":
                return await run_quantum_mutation(**params)
            elif operation_type == "forecast_failure_trend":
                if "trend_data" not in params:
                    if PROMETHEUS_AVAILABLE:
                        QUANTUM_METRICS["input_validation_errors"].labels(
                            operation_type="forecast_failure_trend"
                        ).inc()
                    return {
                        "status": "ERROR",
                        "reason": "Missing 'trend_data' parameter.",
                    }
                return await quantum_forecast_failure(params["trend_data"])
            else:
                if PROMETHEUS_AVAILABLE:
                    QUANTUM_METRICS["operation_total"].labels(
                        operation_type=operation_type,
                        backend="n/a",
                        status="unsupported",
                    ).inc()
                return {"status": "UNSUPPORTED_OPERATION", "operation": operation_type}
        except Exception as e:
            quantum_logger.error(
                f"Error performing quantum op '{operation_type}': {e}", exc_info=True
            )
            if PROMETHEUS_AVAILABLE:
                QUANTUM_METRICS["operation_total"].labels(
                    operation_type=operation_type, backend="n/a", status="error"
                ).inc()
            await alert_operator(
                f"Error in quantum operation '{operation_type}': {e}", level="ERROR"
            )
            return {"status": "ERROR", "reason": str(e)}

    async def check_all_backends_health(self) -> Dict[str, bool]:
        if not self._initialized:
            return {"error": "API not initialized"}
        results = {}
        for backend in ["qiskit", "dwave", "scipy", "ga"]:
            results[backend] = await check_backend_health(backend)
        quantum_logger.info(f"Backend health check results: {results}")
        return results

    async def execute_benchmark(
        self, backend: str = "auto", comprehensive: bool = False
    ) -> Dict[str, Any]:
        if not self._initialized:
            return {"status": "ERROR", "reason": "API not initialized."}
        quantum_logger.info(
            f"Running {'comprehensive' if comprehensive else 'basic'} benchmark on backend: {backend}"
        )

        backends_to_test = (
            self.get_available_backends() if backend == "auto" else [backend]
        )
        if backend != "auto" and backend not in self.get_available_backends():
            return {"status": "ERROR", "reason": f"Backend '{backend}' not available."}

        dummy_file = "./benchmark_test.py"
        with open(dummy_file, "w") as f:
            f.write("pass")

        benchmark_results = {}
        for test_backend in backends_to_test:
            start_time = time.time()
            small_result = await run_quantum_mutation(
                code_file=dummy_file,
                backend=test_backend,
                config={"n_qubits": 3, "n_vars": 3},
            )
            benchmark_results[test_backend] = {
                "basic": {
                    "status": small_result.get("status"),
                    "duration_seconds": time.time() - start_time,
                    "result": small_result.get("quantum_result"),
                }
            }

            if comprehensive:
                comp_results = {}
                for size in [5, 10]:
                    start_size = time.time()
                    res = await run_quantum_mutation(
                        code_file=dummy_file,
                        backend=test_backend,
                        config={"n_qubits": size, "n_vars": size},
                    )
                    comp_results[f"size_{size}"] = {
                        "status": res.get("status"),
                        "duration_seconds": time.time() - start_size,
                        "result": res.get("quantum_result"),
                    }

                start_forecast = time.time()
                forecast_res = await quantum_forecast_failure([1.0, 1.5, 2.0, 2.5, 3.0])
                comp_results["forecast"] = {
                    "status": forecast_res.get("status"),
                    "duration_seconds": time.time() - start_forecast,
                    "result": forecast_res.get("forecast"),
                }
                benchmark_results[test_backend]["comprehensive"] = comp_results

        os.remove(dummy_file)
        return {
            "status": "COMPLETED",
            "timestamp": time.time(),
            "results": benchmark_results,
        }


# --- Example Usage ---
if __name__ == "__main__":

    async def demo():
        api = QuantumPluginAPI()
        try:
            await api.initialize()
            print(f"Available backends: {api.get_available_backends()}")

            os.makedirs("./examples", exist_ok=True)
            dummy_code_file = "./examples/example_code.py"
            with open(dummy_code_file, "w") as f:
                f.write("# dummy code\n")

            mutation_result = await api.perform_quantum_operation(
                "run_mutation_circuit",
                {"code_file": dummy_code_file, "backend": "auto"},
            )
            print(f"\nQuantum mutation result: {mutation_result}")

            forecast_result = await api.perform_quantum_operation(
                "forecast_failure_trend", {"trend_data": [1.2, 1.5, 2.1, 2.7, 3.1, 3.8]}
            )
            print(f"\nQuantum forecast result: {forecast_result}")

            health_status = await api.check_all_backends_health()
            print(f"\nBackend health status: {health_status}")

            if api.get_available_backends():
                benchmark = await api.execute_benchmark(
                    backend="auto", comprehensive=True
                )
                print(f"\nBenchmark results: {json.dumps(benchmark, indent=2)}")

        except Exception as e:
            print(f"\nAn error occurred during the demo: {e}")
        finally:
            await api.shutdown()

    if sys.platform != "win32":
        asyncio.run(demo())
    else:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(demo())
