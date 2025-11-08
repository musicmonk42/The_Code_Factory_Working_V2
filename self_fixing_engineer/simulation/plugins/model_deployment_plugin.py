# AI Model Deployment Plugin (Production-Ready Upgrade)

"""
AI Model Deployment Plugin

This plugin provides a flexible and extensible framework for deploying and undeploying
AI models to various targets, including local API services and cloud-based platforms.

Key upgrades in this version:
- Secrets redaction in logs (no plaintext credentials).
- Clear config validation semantics (AND with inner ORs, or OR-of-ANDs), deterministic errors.
- Deep-merge of global and per-invocation configs.
- Timeouts, retries with backoff, and cancellation propagation in async flows.
- Stable, idempotent deployment IDs derived from inputs (unless force override).
- Strong, consistent result schema with timestamps and correlation IDs.
- Optional OpenTelemetry spans for deploy/undeploy if OTEL is available.
- Plugin manifest and entrypoint registration for sim-runner integration.
- Safe logging formatter (example main) to avoid KeyError on correlation_id.
- Shared plugin singleton to broaden same-process locking scope.
- Safe async bridge that works even if a running event loop exists.

Behavioral notes:
- Direct use of the factory (deployer.deploy_model/undeploy_model) raises exceptions on failure
  (e.g., DeploymentError). The sim-runner entrypoint instead returns structured dicts
  like {"status": "error", "message": "..."} and does not raise.

Usage in sim-runner:
- This module registers an entrypoint "deployment" with a runner function that accepts
  keyword args (via --plugin-args key=value) such as:
    action=deploy|undeploy
    strategy_type=local_api|cloud_service
    model_path=/path/to/model
    model_version=1.2.3
    deployment_id=<id>  # for undeploy
    specific_config_json='{"endpoint_url":"http://...","direct_api_key":"..."}'
    timeout_seconds=60
    retries=2
    backoff_base=2.0
- When invoked without action, it performs NOOP and returns a status explaining the reason.

Security notes:
- Do not log or persist secrets. Prefer secret managers over inline config or env vars.
"""

from __future__ import annotations

import os
import json
import re
import logging
import hashlib
import asyncio
import contextlib  # used by _start_span fallback
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union, Type, Callable
from urllib.parse import urlparse
from datetime import datetime, timezone

# Optional OpenTelemetry (gracefully degraded if unavailable)
try:
    from opentelemetry import trace as otel_trace  # type: ignore
    from opentelemetry.trace import StatusCode as OtelStatusCode  # type: ignore
    _otel_available = True
except Exception:
    _otel_available = False

logger = logging.getLogger(__name__)

# ----------------- Utilities -----------------

_SENSITIVE_KEYS = {"api_key", "api_token", "direct_api_key", "password", "secret", "authorization", "access_key", "secret_key", "bearer"}

def _redact(obj: Any) -> Any:
    """
    Recursively redact sensitive keys in dicts/lists for safe logging.
    - Dict keys matching _SENSITIVE_KEYS (case-insensitive) are redacted.
    - Lists are processed element-wise.
    - Other types are returned as-is.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _stable_deployment_id(strategy: str, model_path: str, model_version: str, target_hint: str = "") -> str:
    """Create a stable, idempotent deployment ID from inputs."""
    base = f"{strategy}|{model_path}|{model_version}|{target_hint}"
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"{strategy}-{model_version}-{h[:12]}"

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dict b into a and return a new dict."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def _validate_semver(version: str) -> bool:
    # Accepts semver-like "x.y.z" or relaxed strings "latest", "staging", etc.
    if version.lower() in {"latest", "staging", "prod"}:
        return True
    return bool(re.fullmatch(r"[0-9]+(\.[0-9]+){1,2}([\-+][A-Za-z0-9\.-]+)?", version))

def _validate_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False

async def _sleep_with_timeout(seconds: float, timeout: Optional[float]) -> None:
    """Sleep with timeout using wait_for to exercise timeout semantics in stubs."""
    if timeout is None:
        await asyncio.sleep(seconds)
        return
    await asyncio.wait_for(asyncio.sleep(seconds), timeout=timeout)

async def _async_retry(coro_factory: Callable[[], "asyncio.Future[Any]"], retries: int, backoff_base: float) -> Any:
    """
    Retry helper for async operations.
    coro_factory must create and return a new coroutine Future per attempt.
    """
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt >= retries:
                raise
            sleep_time = backoff_base ** attempt
            logger.warning(f"Retry {attempt + 1}/{retries} after {sleep_time:.2f}s: {e}")
            await asyncio.sleep(sleep_time)
            attempt += 1

def _start_span(name: str):
    if _otel_available:
        return otel_trace.get_tracer(__name__).start_as_current_span(name)
    return contextlib.nullcontext()

# ----------------- Result Schema -----------------

def _result(
    *,
    status: str,
    deployment_id: Optional[str],
    endpoint_url: Optional[str],
    model_version: Optional[str],
    correlation_id: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "status": status,  # "success" | "pending" | "error" | "noop"
        "deployment_id": deployment_id,
        "endpoint_url": endpoint_url,
        "model_version": model_version,
        "correlation_id": correlation_id,
        "message": message,
        "metadata": metadata or {},
        "timestamps": {
            "started_at": started_at or _now_iso(),
            "completed_at": completed_at or _now_iso(),
        }
    }

# ----------------- Abstract Base Class -----------------

class DeploymentError(Exception):
    """Custom exception for deployment-related errors."""
    pass

class ModelDeploymentStrategy(ABC):
    """
    Abstract Base Class for different AI model deployment strategies.
    Concrete strategies must implement deploy and undeploy.
    """
    def __init__(self, config: Dict[str, Any], correlation_id: Optional[str] = None):
        self.config = config
        self.name = config.get("name", self.__class__.__name__)
        self.correlation_id = correlation_id or os.environ.get("CORRELATION_ID") or hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        self.logger = logging.LoggerAdapter(logger, {'correlation_id': self.correlation_id})
        # Redact sensitive fields in logs (deep)
        self.logger.info(f"Initializing deployment strategy: {self.name} with config: {_redact(self.config)}")

    @abstractmethod
    async def deploy(self, model_path: str, model_version: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Asynchronously deploy the AI model to the target service.
        Return a structured result dict via _result().
        """
        raise NotImplementedError

    @abstractmethod
    async def undeploy(self, deployment_id: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Asynchronously undeploy an existing AI model deployment.
        Return a structured result dict via _result().
        """
        raise NotImplementedError

    def _validate_config(self, required: List[Union[str, List[str]]]):
        """
        Validate configuration with two supported patterns:

        1) AND with inner ORs (default when 'required' contains any strings):
           Example: ['endpoint_url', ['api_key_env_var', 'direct_api_key']]
           Means: endpoint_url is required AND (api_key_env_var OR direct_api_key) is required.

        2) OR-of-ANDs groups (when 'required' is list of lists only):
           Example: [['a', 'b'], ['c', 'd']]
           Means: (a AND b) OR (c AND d) must be present.
        """
        if not isinstance(required, list) or not required:
            raise TypeError("required must be a non-empty list of strings or list of lists of strings.")

        all_lists = all(isinstance(item, list) for item in required)
        present = set(self.config.keys())

        if all_lists:
            # OR-of-ANDs
            groups: List[List[str]] = []
            for group in required:
                if not group or any(not isinstance(k, str) for k in group):
                    raise TypeError("All group keys must be non-empty strings.")
                groups.append(group)
            ok = any(all(k in present for k in group) for group in groups)
            if not ok:
                options = " OR ".join("(" + " AND ".join(g) + ")" for g in groups)
                raise ValueError(f"Config for {self.name} must include: {options}. Present: {sorted(present)}")
        else:
            # AND with inner ORs
            missing_parts: List[str] = []
            for item in required:
                if isinstance(item, str):
                    if item not in present:
                        missing_parts.append(item)
                elif isinstance(item, list):
                    if not item or any(not isinstance(k, str) for k in item):
                        raise TypeError("All keys inside OR-groups must be non-empty strings.")
                    if not any(k in present for k in item):
                        missing_parts.append("(" + " OR ".join(item) + ")")
                else:
                    raise TypeError("required must contain only strings or lists of strings.")
            if missing_parts:
                raise ValueError(f"Config for {self.name} must include: " + " AND ".join(missing_parts) + f". Present: {sorted(present)}")

# ----------------- Concrete Strategies -----------------

class LocalAPIDeploymentStrategy(ModelDeploymentStrategy):
    """
    Deploys a model to a local API endpoint (simulated example).
    In real usage, this might POST a model artifact to a local server.
    """
    def __init__(self, config: Dict[str, Any], correlation_id: Optional[str] = None):
        super().__init__(config, correlation_id)
        # Need 'endpoint_url' AND (api_key_env_var OR direct_api_key)
        self._validate_config(['endpoint_url', ['api_key_env_var', 'direct_api_key']])
        self.endpoint_url = self.config['endpoint_url']
        if not _validate_url(self.endpoint_url):
            raise ValueError(f"Invalid endpoint_url: {self.endpoint_url}")

        api_key_env_var = self.config.get('api_key_env_var')
        direct_api_key = self.config.get('direct_api_key')
        # Resolve API key (never log value)
        self.api_key = direct_api_key or (os.getenv(api_key_env_var) if api_key_env_var else None)
        if not self.api_key:
            env_var_msg = f" or environment variable '{api_key_env_var}'" if api_key_env_var else ""
            raise ValueError(f"No API key found. Either 'direct_api_key' in config{env_var_msg} must be set.")
        self.logger.info(f"Local API Deployment initialized for endpoint: {self.endpoint_url} (credential source: {'direct' if direct_api_key else 'env'})")

    async def deploy(self, model_path: str, model_version: str, **kwargs: Any) -> Dict[str, Any]:
        with _start_span("local_api.deploy") as span:
            started = _now_iso()
            timeout = kwargs.get("timeout_seconds")
            retries = int(kwargs.get("retries", 0))
            backoff_base = float(kwargs.get("backoff_base", 2.0))
            force_redeploy = bool(kwargs.get("force_redeploy", False))

            # Basic input validation
            if not _validate_semver(model_version):
                raise DeploymentError(f"Invalid model_version: {model_version}")
            if model_path and os.path.isabs(model_path) and not os.path.exists(model_path):
                raise DeploymentError(f"Model path does not exist: {model_path}")

            dep_id = _stable_deployment_id("local", model_path, model_version, self.endpoint_url)
            if force_redeploy:
                # include a changing suffix to indicate new rollout
                dep_id = dep_id + "-" + hashlib.sha1(os.urandom(8)).hexdigest()[:6]

            async def _do():
                # Simulate deployment (replace with real HTTP/process calls)
                await _sleep_with_timeout(0.5, timeout)
                return True

            try:
                await _async_retry(_do, retries=retries, backoff_base=backoff_base)
                msg = f"Model '{model_path}' (version: {model_version}) deployed to local API."
                if _otel_available and span:
                    span.set_attribute("deployment.id", dep_id)
                    span.set_status(OtelStatusCode.OK)
                return _result(
                    status="success",
                    deployment_id=dep_id,
                    endpoint_url=self.endpoint_url,
                    model_version=model_version,
                    correlation_id=self.correlation_id,
                    message=msg,
                    metadata={"strategy": "local_api"},
                    started_at=started,
                    completed_at=_now_iso()
                )
            except asyncio.CancelledError:
                if _otel_available and span:
                    span.set_status(OtelStatusCode.ERROR)
                raise
            except Exception as e:
                if _otel_available and span:
                    span.record_exception(e)
                    span.set_status(OtelStatusCode.ERROR)
                raise DeploymentError(f"Local API deployment failed: {e}")

    async def undeploy(self, deployment_id: str, **kwargs: Any) -> Dict[str, Any]:
        with _start_span("local_api.undeploy") as span:
            started = _now_iso()
            timeout = kwargs.get("timeout_seconds")
            retries = int(kwargs.get("retries", 0))
            backoff_base = float(kwargs.get("backoff_base", 2.0))

            async def _do():
                # Simulate undeployment
                await _sleep_with_timeout(0.2, timeout)
                return True

            try:
                await _async_retry(_do, retries=retries, backoff_base=backoff_base)
                msg = f"Undeployed local API deployment: {deployment_id}"
                if _otel_available and span:
                    span.set_attribute("deployment.id", deployment_id)
                    span.set_status(OtelStatusCode.OK)
                return _result(
                    status="success",
                    deployment_id=deployment_id,
                    endpoint_url=self.endpoint_url,
                    model_version=None,
                    correlation_id=self.correlation_id,
                    message=msg,
                    metadata={"strategy": "local_api"},
                    started_at=started,
                    completed_at=_now_iso()
                )
            except asyncio.CancelledError:
                if _otel_available and span:
                    span.set_status(OtelStatusCode.ERROR)
                raise
            except Exception as e:
                if _otel_available and span:
                    span.record_exception(e)
                    span.set_status(OtelStatusCode.ERROR)
                raise DeploymentError(f"Local API undeployment failed: {e}")

class CloudServiceDeploymentStrategy(ModelDeploymentStrategy):
    """
    Placeholder for a cloud-based deployment strategy (e.g., AWS SageMaker, Azure ML, GCP Vertex).
    Uses simulated async behavior here; real implementations should wrap blocking SDK calls via
    asyncio.to_thread or use async SDKs where available.
    """
    _allowed_services = {"aws_sagemaker", "azure_ml", "gcp_vertex"}

    def __init__(self, config: Dict[str, Any], correlation_id: Optional[str] = None):
        super().__init__(config, correlation_id)
        # Need 'service_name' AND ('region' OR 'endpoint_id')
        self._validate_config(['service_name', ['region', 'endpoint_id']])
        self.service_name = self.config['service_name']
        if self.service_name not in self._allowed_services:
            # Still allow for demo but warn
            logger.warning(f"Service '{self.service_name}' not in allowed set {self._allowed_services}; proceeding (demo).")
        self.region = self.config.get('region')
        self.endpoint_id = self.config.get('endpoint_id')
        self.logger.info(f"Cloud Service Deployment initialized for {self.service_name} (region={self.region}, endpoint_id={self.endpoint_id})")

    async def deploy(self, model_path: str, model_version: str, **kwargs: Any) -> Dict[str, Any]:
        with _start_span("cloud_service.deploy") as span:
            started = _now_iso()
            timeout = kwargs.get("timeout_seconds")
            retries = int(kwargs.get("retries", 1))
            backoff_base = float(kwargs.get("backoff_base", 2.0))
            force_redeploy = bool(kwargs.get("force_redeploy", False))

            if not _validate_semver(model_version):
                raise DeploymentError(f"Invalid model_version: {model_version}")
            if model_path and os.path.isabs(model_path) and not os.path.exists(model_path):
                # For cloud, models may be in remote storage; only warn if missing locally
                self.logger.warning(f"Model path does not exist locally: {model_path} (continuing for cloud demo)")

            target_hint = self.endpoint_id or self.region or "global"
            dep_id = _stable_deployment_id(self.service_name, model_path, model_version, target_hint)
            if force_redeploy:
                dep_id = dep_id + "-" + hashlib.sha1(os.urandom(8)).hexdigest()[:6]
            endpoint_url = f"https://{self.service_name}.{self.region or 'global'}.example.com/models/{dep_id}"

            async def _do():
                # Simulated cloud provisioning
                # In production, wrap blocking SDK calls with asyncio.to_thread(...)
                await _sleep_with_timeout(1.5, timeout)
                return True

            try:
                await _async_retry(_do, retries=retries, backoff_base=backoff_base)
                status = "pending" if self.config.get("async_deploy", False) else "success"
                msg = f"Deployment to {self.service_name} initiated."
                if _otel_available and span:
                    span.set_attribute("deployment.id", dep_id)
                    span.set_attribute("endpoint.url", endpoint_url)
                    span.set_status(OtelStatusCode.OK)
                return _result(
                    status=status,
                    deployment_id=dep_id,
                    endpoint_url=endpoint_url,
                    model_version=model_version,
                    correlation_id=self.correlation_id,
                    message=msg,
                    metadata={"strategy": "cloud_service", "region": self.region, "endpoint_id": self.endpoint_id},
                    started_at=started,
                    completed_at=_now_iso()
                )
            except asyncio.CancelledError:
                if _otel_available and span:
                    span.set_status(OtelStatusCode.ERROR)
                raise
            except Exception as e:
                if _otel_available and span:
                    span.record_exception(e)
                    span.set_status(OtelStatusCode.ERROR)
                raise DeploymentError(f"Cloud service deployment failed: {e}")

    async def undeploy(self, deployment_id: str, **kwargs: Any) -> Dict[str, Any]:
        with _start_span("cloud_service.undeploy") as span:
            started = _now_iso()
            timeout = kwargs.get("timeout_seconds")
            retries = int(kwargs.get("retries", 1))
            backoff_base = float(kwargs.get("backoff_base", 2.0))

            async def _do():
                # Simulated undeploy
                await _sleep_with_timeout(0.8, timeout)
                return True

            try:
                await _async_retry(_do, retries=retries, backoff_base=backoff_base)
                endpoint_url = f"https://{self.service_name}.{self.region or 'global'}.example.com/models/{deployment_id}"
                msg = f"Undeployed {self.service_name} deployment: {deployment_id}"
                if _otel_available and span:
                    span.set_attribute("deployment.id", deployment_id)
                    span.set_status(OtelStatusCode.OK)
                return _result(
                    status="success",
                    deployment_id=deployment_id,
                    endpoint_url=endpoint_url,
                    model_version=None,
                    correlation_id=self.correlation_id,
                    message=msg,
                    metadata={"strategy": "cloud_service", "region": self.region, "endpoint_id": self.endpoint_id},
                    started_at=started,
                    completed_at=_now_iso()
                )
            except asyncio.CancelledError:
                if _otel_available and span:
                    span.set_status(OtelStatusCode.ERROR)
                raise
            except Exception as e:
                if _otel_available and span:
                    span.record_exception(e)
                    span.set_status(OtelStatusCode.ERROR)
                raise DeploymentError(f"Cloud service undeployment failed: {e}")

# ----------------- Plugin Factory/Manager -----------------

class ModelDeploymentPlugin:
    """
    Factory for deployment strategies with global+specific config merging and basic locking.
    """

    _strategies: Dict[str, Type[ModelDeploymentStrategy]] = {
        "local_api": LocalAPIDeploymentStrategy,
        "cloud_service": CloudServiceDeploymentStrategy,
    }

    def __init__(self, global_config_path: Optional[str] = 'deployment_config.json'):
        self.global_config: Dict[str, Any] = {}
        # Per-target locks (strategy+target) to avoid concurrent conflicting operations
        self._locks: Dict[str, asyncio.Lock] = {}
        if global_config_path and os.path.exists(global_config_path):
            try:
                with open(global_config_path, 'r', encoding="utf-8") as f:
                    self.global_config = json.load(f)
                logger.info(f"Loaded global deployment configuration from: {global_config_path}")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding global config file '{global_config_path}': {e}", exc_info=True)
                raise
        else:
            logger.info(f"No global deployment configuration found at '{global_config_path}'. Using per-invocation configs only.")

    def _lock_key(self, strategy_type: str, specific_config: Dict[str, Any]) -> str:
        # Key on strategy + main target hint to avoid clashes (best-effort)
        if strategy_type == "local_api":
            endpoint = specific_config.get("endpoint_url", "")
            return f"{strategy_type}|{endpoint}"
        if strategy_type == "cloud_service":
            service = specific_config.get("service_name", "")
            region = specific_config.get("region", "")
            endpoint_id = specific_config.get("endpoint_id", "")
            return f"{strategy_type}|{service}|{region}|{endpoint_id}"
        return f"{strategy_type}|generic"

    def _get_lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def get_strategy(self, strategy_type: str, specific_config: Dict[str, Any], correlation_id: Optional[str] = None) -> ModelDeploymentStrategy:
        if strategy_type not in self._strategies:
            raise ValueError(f"Unknown deployment strategy type: {strategy_type}. Available: {list(self._strategies.keys())}")
        merged_config = _deep_merge(self.global_config.get(strategy_type, {}), specific_config)
        return self._strategies[strategy_type](merged_config, correlation_id=correlation_id)

    async def deploy_model(self, strategy_type: str, model_path: str, model_version: str, specific_config: Dict[str, Any],
                           correlation_id: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        current_correlation_id = correlation_id or hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        key = self._lock_key(strategy_type, specific_config)
        lock = self._get_lock(key)
        async with lock:
            with _start_span("plugin.deploy_model") as span:
                if _otel_available and span:
                    span.set_attribute("strategy.type", strategy_type)
                    span.set_attribute("model.version", model_version)
                logger.info(f"[{current_correlation_id}] Deploying model '{os.path.basename(model_path)}' version '{model_version}' via {strategy_type}")
                try:
                    strategy = self.get_strategy(strategy_type, specific_config, correlation_id=current_correlation_id)
                    res = await strategy.deploy(model_path, model_version, **kwargs)
                    return res
                except Exception as e:
                    if _otel_available and span:
                        span.record_exception(e)
                        span.set_status(OtelStatusCode.ERROR)
                    logger.error(f"[{current_correlation_id}] Deployment failed: {e}", exc_info=True)
                    raise

    async def undeploy_model(self, strategy_type: str, deployment_id: str, specific_config: Dict[str, Any],
                             correlation_id: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        current_correlation_id = correlation_id or hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        key = self._lock_key(strategy_type, specific_config)
        lock = self._get_lock(key)
        async with lock:
            with _start_span("plugin.undeploy_model") as span:
                if _otel_available and span:
                    span.set_attribute("strategy.type", strategy_type)
                    span.set_attribute("deployment.id", deployment_id)
                logger.info(f"[{current_correlation_id}] Undeploying ID '{deployment_id}' via {strategy_type}")
                try:
                    strategy = self.get_strategy(strategy_type, specific_config, correlation_id=current_correlation_id)
                    res = await strategy.undeploy(deployment_id, **kwargs)
                    return res
                except Exception as e:
                    if _otel_available and span:
                        span.record_exception(e)
                        span.set_status(OtelStatusCode.ERROR)
                    logger.error(f"[{current_correlation_id}] Undeployment failed: {e}", exc_info=True)
                    raise

# ----------------- Health Check -----------------

async def plugin_health() -> Dict[str, Any]:
    """
    Expanded health check:
    - Python version and OTEL availability.
    - SDK presence: 'requests' (for HTTP endpoints), 'boto3' (for AWS-based cloud strategies).
    - Optional endpoint reachability if MODEL_DEPLOYMENT_LOCAL_HEALTH_URL or LOCAL_API_HEALTHCHECK_URL is set.
    - Optional AWS credentials presence check if MODEL_DEPLOYMENT_CHECK_AWS=true (non-network).
    """
    details: List[str] = []
    status = "ok"
    try:
        # Python and OTEL
        details.append(f"Python: {os.sys.version.split()[0]}")
        details.append(f"OTEL available: {_otel_available}")

        # SDK presence
        requests_available = False
        boto3_available = False

        try:
            import requests  # type: ignore
            requests_available = True
            details.append("requests: available")
        except Exception:
            details.append("requests: missing")

        try:
            import boto3  # type: ignore
            boto3_available = True
            details.append("boto3: available")
        except Exception:
            details.append("boto3: missing")

        # Optional local endpoint reachability
        health_url = os.environ.get("MODEL_DEPLOYMENT_LOCAL_HEALTH_URL") or os.environ.get("LOCAL_API_HEALTHCHECK_URL")
        if health_url:
            if requests_available:
                try:
                    import requests  # type: ignore
                    resp = requests.get(health_url, timeout=2)
                    if 200 <= resp.status_code < 300:
                        details.append(f"local_api_health({health_url}): OK {resp.status_code}")
                    else:
                        details.append(f"local_api_health({health_url}): BAD {resp.status_code}")
                        status = "degraded"
                except Exception as he:
                    details.append(f"local_api_health({health_url}): ERROR {he}")
                    status = "degraded"
            else:
                details.append(f"local_api_health({health_url}): skipped (requests missing)")
                status = "degraded"

        # Optional AWS credentials presence (no network)
        check_aws = os.environ.get("MODEL_DEPLOYMENT_CHECK_AWS", "false").lower() in ("1", "true", "yes")
        if check_aws:
            if boto3_available:
                try:
                    import boto3  # type: ignore
                    region = os.environ.get("AWS_REGION")
                    session = boto3.Session(region_name=region) if region else boto3.Session()
                    creds = session.get_credentials()
                    if creds and creds.access_key:
                        details.append("aws_credentials: present")
                    else:
                        details.append("aws_credentials: missing")
                        status = "degraded"
                except Exception as ae:
                    details.append(f"aws_credentials: error {ae}")
                    status = "degraded"
            else:
                details.append("aws_credentials: skipped (boto3 missing)")
                status = "degraded"

    except Exception as e:
        status = "degraded"
        details.append(f"Health error: {e}")
    return {"status": status, "details": details}

# ----------------- Plugin Manifest and Registration -----------------

PLUGIN_MANIFEST = {
    "name": "ModelDeploymentPlugin",
    "version": "0.2.2",
    "description": "Deploy and undeploy AI models to local APIs and cloud services with retries, timeouts, and secure logging.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["model_deployment"],
    "permissions_required": ["network_access", "filesystem_read"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0"
    },
    "entry_points": {
        "model_deployment": {
            "description": "Deploys or undeploys a model using a specified strategy.",
            "parameters": [
                "action",  # deploy|undeploy
                "strategy_type",  # local_api|cloud_service
                "model_path",
                "model_version",
                "deployment_id",
                "specific_config_json",
                "timeout_seconds",
                "retries",
                "backoff_base",
                "force_redeploy"
            ]
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "",
    "tags": ["deployment", "mlops", "models"]
}

# Module-level singleton to broaden same-process locking scope across multiple invocations
_PLUGIN_SINGLETON: Optional[ModelDeploymentPlugin] = None

def _get_plugin_singleton() -> ModelDeploymentPlugin:
    global _PLUGIN_SINGLETON
    if _PLUGIN_SINGLETON is None:
        _PLUGIN_SINGLETON = ModelDeploymentPlugin()
    return _PLUGIN_SINGLETON

def register_plugin_entrypoints(register_func: Callable[[str, Dict[str, Any]], None]) -> None:
    """
    Registers this plugin's model deployment entrypoint with the sim-runner.
    The sim-runner adapter will call runner_function(**kwargs) when --plugin-args key=value are provided.
    This runner is tolerant of missing args and returns NOOP if 'action' is not provided.
    """
    async def _runner_async(
        action: Optional[str] = None,
        strategy_type: Optional[str] = None,
        model_path: Optional[str] = None,
        model_version: Optional[str] = None,
        deployment_id: Optional[str] = None,
        specific_config: Optional[Dict[str, Any]] = None,
        specific_config_json: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
        force_redeploy: Optional[bool] = None
    ) -> Dict[str, Any]:
        # NOOP if no action specified (prevents errors in bulk runs without plugin args)
        if not action:
            return _result(
                status="noop",
                deployment_id=None,
                endpoint_url=None,
                model_version=None,
                correlation_id="noop",
                message="No action provided; use action=deploy|undeploy and strategy_type=...",
                metadata={}
            )
        # Parse specific config
        cfg: Dict[str, Any] = {}
        if isinstance(specific_config, dict):
            cfg = specific_config
        elif specific_config_json:
            try:
                parsed = json.loads(specific_config_json)
                if isinstance(parsed, dict):
                    cfg = parsed
                else:
                    return _result(
                        status="error", deployment_id=None, endpoint_url=None, model_version=None,
                        correlation_id="parse", message="specific_config_json must parse to an object", metadata={}
                    )
            except Exception as e:
                return _result(
                    status="error", deployment_id=None, endpoint_url=None, model_version=None,
                    correlation_id="parse", message=f"Failed to parse specific_config_json: {e}", metadata={}
                )

        # Instantiate or reuse plugin factory
        plugin = _get_plugin_singleton()

        # Common kwargs for strategies
        kwargs: Dict[str, Any] = {}
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = int(timeout_seconds)
        if retries is not None:
            kwargs["retries"] = int(retries)
        if backoff_base is not None:
            kwargs["backoff_base"] = float(backoff_base)
        if force_redeploy is not None:
            kwargs["force_redeploy"] = bool(force_redeploy)

        try:
            if action == "deploy":
                if not strategy_type or not model_path or not model_version:
                    return _result(
                        status="error", deployment_id=None, endpoint_url=None, model_version=None,
                        correlation_id="validate", message="deploy requires strategy_type, model_path, model_version", metadata={}
                    )
                res = await plugin.deploy_model(strategy_type, model_path, model_version, cfg, **kwargs)
                # Ensure correlation_id is present for tracing
                res.setdefault("correlation_id", cfg.get("correlation_id", ""))
                return res
            elif action == "undeploy":
                if not strategy_type or not deployment_id:
                    return _result(
                        status="error", deployment_id=None, endpoint_url=None, model_version=None,
                        correlation_id="validate", message="undeploy requires strategy_type and deployment_id", metadata={}
                    )
                res = await plugin.undeploy_model(strategy_type, deployment_id, cfg, **kwargs)
                res.setdefault("correlation_id", cfg.get("correlation_id", ""))
                return res
            else:
                return _result(
                    status="error", deployment_id=None, endpoint_url=None, model_version=None,
                    correlation_id="validate", message=f"Unknown action: {action}", metadata={}
                )
        except asyncio.CancelledError:
            return _result(
                status="error", deployment_id=None, endpoint_url=None, model_version=None,
                correlation_id="cancel", message="Operation cancelled", metadata={}
            )
        except DeploymentError as e:
            return _result(
                status="error", deployment_id=None, endpoint_url=None, model_version=None,
                correlation_id="deploy_error", message=str(e), metadata={}
            )
        except Exception as e:
            logger.error(f"Unexpected error in model deployment runner: {e}", exc_info=True)
            return _result(
                status="error", deployment_id=None, endpoint_url=None, model_version=None,
                correlation_id="exception", message=str(e), metadata={}
            )

    def _runner_sync(**kwargs) -> Dict[str, Any]:
        """
        Bridge sync entrypoint to async implementation. If a running event loop exists
        (e.g., notebook), run the async function in a dedicated thread to avoid RuntimeError.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop: safe to use asyncio.run
            return asyncio.run(_runner_async(**kwargs))
        else:
            # Running loop detected: execute in a background thread
            from threading import Thread
            from queue import Queue
            q: Queue = Queue(maxsize=1)
            def worker():
                try:
                    res = asyncio.run(_runner_async(**kwargs))
                    q.put(res)
                except Exception as e:
                    q.put(_result(
                        status="error", deployment_id=None, endpoint_url=None, model_version=None,
                        correlation_id="bridge", message=f"runner bridge error: {e}", metadata={}
                    ))
            t = Thread(target=worker, daemon=True)
            t.start()
            t.join()
            return q.get()

    runner_info = {
        "version": PLUGIN_MANIFEST.get("version", "unknown"),
        "command": ["python", "-m", "simulation.plugins.model_deployment_plugin"],  # placeholder
        "extensions": [],
        "test_discovery": [],
        "runner_function": _runner_sync
    }
    register_func(language_or_framework="deployment", runner_info=runner_info)

# ----------------- Example Main (demo-only) -----------------

def _install_safe_log_record_factory():
    """
    Avoid KeyError when formatter expects correlation_id.
    This modifies the global record factory to always include correlation_id field (default '-').
    """
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        if not hasattr(record, "correlation_id"):
            setattr(record, "correlation_id", "-")
        return record
    logging.setLogRecordFactory(record_factory)

async def _demo_main():
    # Demo only: not used by sim-runner. Shows typical usage.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s')
    _install_safe_log_record_factory()

    # Prepare demo config file
    global_config_content = {
        "local_api": {
            "endpoint_url": "http://localhost:8000/predict",
            "api_key_env_var": "LOCAL_API_KEY"
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
            "instance_type": "ml.t2.medium"
        }
    }
    config_file_path = 'deployment_config.json'
    with open(config_file_path, 'w', encoding="utf-8") as f:
        json.dump(global_config_content, f, indent=2)

    os.environ['LOCAL_API_KEY'] = 'demo_key_value'  # demo only

    deployer = ModelDeploymentPlugin(global_config_path=config_file_path)

    # Local deploy/undeploy
    try:
        res_dep = await deployer.deploy_model("local_api", "/path/to/model.pkl", "1.0.0", {"direct_api_key": "override_key"})
        print("Local deploy:", res_dep)
        if res_dep.get("deployment_id"):
            res_und = await deployer.undeploy_model("local_api", res_dep["deployment_id"], {"direct_api_key": "override_key"})
            print("Local undeploy:", res_und)
    except Exception as e:
        print("Local error:", e)

    # Cloud deploy/undeploy
    try:
        res_dep_c = await deployer.deploy_model("cloud_service", "/path/to/model.pb", "2.1.5", {"instance_type": "ml.m5.large"})
        print("Cloud deploy:", res_dep_c)
        if res_dep_c.get("deployment_id"):
            res_und_c = await deployer.undeploy_model("cloud_service", res_dep_c["deployment_id"], {"instance_type": "ml.m5.large"})
            print("Cloud undeploy:", res_und_c)
    except Exception as e:
        print("Cloud error:", e)

    # Cleanup
    try:
        os.remove(config_file_path)
    except Exception:
        pass
    os.environ.pop("LOCAL_API_KEY", None)

if __name__ == "__main__":
    asyncio.run(_demo_main())