# plugins/gremlin_chaos_plugin.py
#
# Production-grade Gremlin Chaos Plugin (HTTP-based)
# - Async, resilient HTTP client with retries, timeouts, SSL enforcement
# - Strict input validation (Pydantic v2 with v1 compatibility)
# - Low-cardinality Prometheus metrics with safe registration
# - Structured, secret-scrubbed logging with correlation IDs
# - Health check and clean entrypoint registration
# - Concurrency limiting and safe shutdown hook
# - Optional extras: base URL allowlist, custom CA bundle, tunable poll cadence, extra metrics
# - API contract overrides via environment (paths, header names, auth scheme)
#
# Notes:
# - Default API shape:
#   POST   {BASE_URL}/v1/attacks           -> create attack
#   GET    {BASE_URL}/v1/attacks/{id}      -> get attack status
#   POST   {BASE_URL}/v1/attacks/{id}/halt -> halt attack
#   GET    {BASE_URL}/v1/attacks?limit=1   -> light auth/connectivity check
# - Adjust endpoints/headers/payloads to match your actual Gremlin API with env overrides below.
# - Do NOT include user-provided secrets in logs. This plugin scrubs known keys.

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

import aiohttp

# Tenacity (optional) for retries
try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    TENACITY_AVAILABLE = True
except Exception:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        def _wrap(f):
            return f

        return _wrap

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(e):
        return lambda x: False


# Pydantic v2 with v1 compatibility
try:
    from pydantic import (  # type: ignore
        BaseModel,
        Field,
        ValidationError,
        model_validator,
        root_validator,
    )

    PYDANTIC_V2 = True
except Exception:
    from pydantic import BaseModel, Field, ValidationError, root_validator  # type: ignore

    PYDANTIC_V2 = False

# Logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    import sys

    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
    )
    logger.addHandler(h)

if not TENACITY_AVAILABLE:
    logger.warning(
        "Tenacity not installed; API retries disabled. Install 'tenacity' for production resilience."
    )

# Prometheus (optional) safe creators
PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except Exception as _e:
    logger.warning(
        f"Prometheus client not available; Gremlin chaos metrics disabled: {_e}"
    )

_METRICS: Dict[str, Any] = {}


def _noop_counter():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def dec(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

    return _Noop()


def _noop_hist():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    return _Noop()


def _safe_counter(name: str, doc: str, labelnames: tuple = ()):
    if not PROMETHEUS_AVAILABLE:
        return _noop_counter()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Counter(name, doc, labelnames=labelnames)
        _METRICS[name] = m
        return m
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        m = _noop_counter()
        _METRICS[name] = m
        return m


def _safe_hist(name: str, doc: str, labelnames: tuple = (), buckets=None):
    if not PROMETHEUS_AVAILABLE:
        return _noop_hist()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Histogram(name, doc, labelnames=labelnames, buckets=buckets or Histogram.DEFAULT_BUCKETS)  # type: ignore
        _METRICS[name] = m
        return m
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        m = _noop_hist()
        _METRICS[name] = m
        return m


def _safe_gauge(name: str, doc: str, labelnames: tuple = ()):
    if not PROMETHEUS_AVAILABLE:
        return _noop_counter()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Gauge(name, doc, labelnames=labelnames)
        _METRICS[name] = m
        return m
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        m = _noop_counter()
        _METRICS[name] = m
        return m


# Metrics (low-cardinality)
CHAOS_ATTACKS_TOTAL = _safe_counter(
    "gremlin_chaos_attacks_total",
    "Total chaos attacks events",
    ("experiment_type", "status"),
)
CHAOS_ATTACK_ERRORS_TOTAL = _safe_counter(
    "gremlin_chaos_attack_errors_total",
    "Total chaos attack errors",
    ("experiment_type", "error_type"),
)
CHAOS_ATTACK_DURATION_SECONDS = _safe_hist(
    "gremlin_chaos_attack_duration_seconds",
    "Duration of chaos attacks",
    ("experiment_type",),
)
GREMLIN_API_LATENCY_SECONDS = _safe_hist(
    "gremlin_api_latency_seconds", "Latency of Gremlin API calls", ("operation",)
)
GREMLIN_API_RETRIES_TOTAL = _safe_counter(
    "gremlin_api_retries_total", "Total API retries", ("operation",)
)
GREMLIN_HALTS_TOTAL = _safe_counter(
    "gremlin_chaos_halts_total", "Total halt attempts for chaos attacks", ("status",)
)
GREMLIN_INFLIGHT_ATTACKS = _safe_gauge(
    "gremlin_chaos_inflight_attacks", "Number of in-flight chaos attacks"
)
GREMLIN_CREATE_TOTAL = _safe_counter(
    "gremlin_create_attack_total", "Create-attack attempts and outcomes", ("status",)
)
GREMLIN_STATUS_POLLS_TOTAL = _safe_counter(
    "gremlin_status_polls_total", "Total status polls", ("state",)
)
GREMLIN_HTTP_RESPONSES_TOTAL = _safe_counter(
    "gremlin_http_responses_total", "HTTP responses by status class", ("class",)
)
GREMLIN_SUBMISSION_SUCCESS_TOTAL = _safe_counter(
    "gremlin_submission_success_total", "Successful submissions to Gremlin API"
)

# Manifest
PLUGIN_MANIFEST = {
    "name": "GremlinChaosPlugin",
    "version": "2.3.0",
    "description": "Integrates with Gremlin-compatible Chaos API for programmable chaos experiments.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["chaos_injection", "fault_injection", "resilience_testing"],
    "permissions_required": ["network_access_external"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "run_chaos_experiment": {
            "description": "Executes a specified chaos experiment.",
            "parameters": [
                "experiment_type",
                "target_type",
                "target_value",
                "duration_seconds",
                "intensity",
                "labels",
                "tags",
            ],
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://www.gremlin.com/",
    "tags": ["chaos_engineering", "gremlin", "fault_injection", "resilience"],
}

# Configuration (env)
GREMLIN_BASE_URL = os.getenv("GREMLIN_API_BASE_URL", "https://api.gremlin.com").rstrip(
    "/"
)
GREMLIN_TEAM_ID = os.getenv("GREMLIN_TEAM_ID", "")
GREMLIN_API_KEY = os.getenv("GREMLIN_API_KEY", "")
GREMLIN_TIMEOUT_SECONDS = int(os.getenv("GREMLIN_API_TIMEOUT_SECONDS", "30"))
GREMLIN_RETRY_ATTEMPTS = int(os.getenv("GREMLIN_API_RETRY_ATTEMPTS", "3"))
GREMLIN_RETRY_BACKOFF_FACTOR = float(
    os.getenv("GREMLIN_API_RETRY_BACKOFF_FACTOR", "2.0")
)
GREMLIN_VERIFY_SSL = os.getenv("GREMLIN_VERIFY_SSL", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
GREMLIN_CA_BUNDLE = os.getenv(
    "GREMLIN_CA_BUNDLE", ""
).strip()  # optional custom CA bundle path
GREMLIN_ALLOW_ALL_TARGETS = os.getenv(
    "GREMLIN_ALLOW_ALL_TARGETS", "false"
).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
GREMLIN_MAX_CONCURRENCY = max(1, int(os.getenv("GREMLIN_MAX_CONCURRENCY", "5")))
GREMLIN_POLL_INTERVAL_SECONDS = max(
    1, int(os.getenv("GREMLIN_POLL_INTERVAL_SECONDS", "10"))
)
GREMLIN_POLL_JITTER_SECONDS = max(0, int(os.getenv("GREMLIN_POLL_JITTER_SECONDS", "5")))
# Optional base URL allowlist (comma-separated hostnames, e.g., "api.gremlin.com,gremlin-staging.example.com")
GREMLIN_ALLOWED_BASE_URLS = {
    h.strip().lower()
    for h in os.getenv("GREMLIN_ALLOWED_BASE_URLS", "").split(",")
    if h.strip()
}
# Optional Kubernetes allowlist (comma-separated namespaces)
GREMLIN_K8S_ALLOWED_NAMESPACES = {
    ns.strip()
    for ns in os.getenv("GREMLIN_K8S_ALLOWED_NAMESPACES", "").split(",")
    if ns.strip()
}
# API contract overrides (paths, headers, auth scheme)
GREMLIN_API_CREATE_PATH = os.getenv("GREMLIN_API_CREATE_PATH", "/v1/attacks")
GREMLIN_API_STATUS_PATH_FORMAT = os.getenv(
    "GREMLIN_API_STATUS_PATH_FORMAT", "/v1/attacks/{id}"
)
GREMLIN_API_HALT_PATH_FORMAT = os.getenv(
    "GREMLIN_API_HALT_PATH_FORMAT", "/v1/attacks/{id}/halt"
)
GREMLIN_API_QUICK_CHECK_PATH = os.getenv(
    "GREMLIN_API_QUICK_CHECK_PATH", "/v1/attacks?limit=1"
)
GREMLIN_AUTH_HEADER_NAME = os.getenv("GREMLIN_AUTH_HEADER_NAME", "Authorization")
GREMLIN_TEAM_HEADER_NAME = os.getenv("GREMLIN_TEAM_HEADER_NAME", "X-Gremlin-Team-ID")
GREMLIN_AUTH_SCHEME = os.getenv("GREMLIN_AUTH_SCHEME", "Key").strip()

# Concurrency limiter for outbound API calls
_api_semaphore = asyncio.Semaphore(GREMLIN_MAX_CONCURRENCY)

# Simple secret scrubber
_SCRUB_PAT = re.compile(
    r"(?i)\b(api_key|authorization|x-gremlin-team-id|team_id|bearer|token|secret|access[-_ ]?token|apikey)\b\s*[:=]?\s*([^\s\"']+)"
)


def _scrub(s: str) -> str:
    try:
        return _SCRUB_PAT.sub(lambda m: f"{m.group(1)}=[REDACTED]", s)
    except Exception:
        return s


# Optional Audit Logger integration
try:
    from simulation.audit_log import AuditLogger as SFE_AuditLogger

    _audit_logger = SFE_AuditLogger.from_environment()
except Exception:

    class _MockAudit:
        async def log(self, event_type: str, details: Dict[str, Any], **kwargs: Any):
            logger.info(
                f"[AUDIT] {event_type}: {_scrub(json.dumps(details, ensure_ascii=False))}"
            )

    _audit_logger = _MockAudit()


async def _audit_event(event_type: str, details: Dict[str, Any]):
    try:
        await _audit_logger.log(event_type, details)
    except Exception as e:
        logger.debug(f"Audit log failed: {e}")


# Input validation models
ExperimentType = Literal[
    "cpu_hog", "network_latency", "process_kill", "disk_io", "blackhole", "shutdown"
]
TargetType = Literal["Host", "Container", "Kubernetes", "Random", "All"]


class TargetSpec(BaseModel):
    target_type: TargetType = Field(..., description="Type of target.")
    # Host/Container
    names: Optional[List[str]] = Field(None, description="Host or container names/IDs.")
    # Kubernetes
    namespace: Optional[str] = None
    cluster_name: Optional[str] = None
    labels: Optional[Dict[str, str]] = None
    field_selectors: Optional[Dict[str, str]] = None
    name_regex: Optional[str] = None
    # Random
    count: Optional[int] = Field(None, ge=1)

    if PYDANTIC_V2:

        @model_validator(mode="after")
        def _validate(self) -> "TargetSpec":
            t = self.target_type
            if t in ("Host", "Container"):
                if (
                    not self.names
                    or not isinstance(self.names, list)
                    or not all(isinstance(x, str) and x for x in self.names)
                ):
                    raise ValueError(f"{t} target requires non-empty 'names' list.")
            elif t == "Kubernetes":
                if (
                    GREMLIN_K8S_ALLOWED_NAMESPACES
                    and self.namespace
                    and self.namespace not in GREMLIN_K8S_ALLOWED_NAMESPACES
                ):
                    raise ValueError(
                        f"Kubernetes namespace '{self.namespace}' not allowed by policy."
                    )
                if not (self.namespace or self.labels or self.name_regex):
                    raise ValueError(
                        "Kubernetes target requires at least one of: namespace, labels, name_regex."
                    )
            elif t == "Random":
                if not self.count or self.count < 1:
                    self.count = 1
            elif t == "All":
                if not GREMLIN_ALLOW_ALL_TARGETS:
                    raise ValueError(
                        "Target type 'All' requires GREMLIN_ALLOW_ALL_TARGETS=true."
                    )
            return self

    else:

        @root_validator
        def _validate_v1(cls, values):
            t = values.get("target_type")
            if t in ("Host", "Container"):
                names = values.get("names")
                if (
                    not names
                    or not isinstance(names, list)
                    or not all(isinstance(x, str) and x for x in names)
                ):
                    raise ValueError(f"{t} target requires non-empty 'names' list.")
            elif t == "Kubernetes":
                ns = values.get("namespace")
                if (
                    GREMLIN_K8S_ALLOWED_NAMESPACES
                    and ns
                    and ns not in GREMLIN_K8S_ALLOWED_NAMESPACES
                ):
                    raise ValueError(
                        f"Kubernetes namespace '{ns}' not allowed by policy."
                    )
                if not (
                    values.get("namespace")
                    or values.get("labels")
                    or values.get("name_regex")
                ):
                    raise ValueError(
                        "Kubernetes target requires at least one of: namespace, labels, name_regex."
                    )
            elif t == "Random":
                c = values.get("count")
                if not c or c < 1:
                    values["count"] = 1
            elif t == "All":
                if not GREMLIN_ALLOW_ALL_TARGETS:
                    raise ValueError(
                        "Target type 'All' requires GREMLIN_ALLOW_ALL_TARGETS=true."
                    )
            return values


class AttackSpec(BaseModel):
    experiment_type: ExperimentType
    duration_seconds: int = Field(
        60, ge=1, le=int(os.getenv("GREMLIN_MAX_DURATION_SECONDS", "3600"))
    )
    intensity: Optional[float] = Field(None, ge=0)

    # Specific params
    # process_kill
    process_name: Optional[str] = None
    # network_latency
    delay_milliseconds: Optional[int] = Field(None, ge=0)
    packet_loss_percent: Optional[float] = Field(None, ge=0, le=100)
    protocol: Optional[str] = Field(None, description="e.g., tcp/udp")
    destination_hosts: Optional[List[str]] = None
    # disk_io
    read_bytes_per_sec: Optional[int] = Field(None, ge=0)
    write_bytes_per_sec: Optional[int] = Field(None, ge=0)

    labels: Optional[Dict[str, str]] = None
    tags: Optional[List[str]] = None

    if PYDANTIC_V2:

        @model_validator(mode="after")
        def _validate(self) -> "AttackSpec":
            et = self.experiment_type
            if et == "process_kill":
                if not self.process_name:
                    raise ValueError("process_kill requires 'process_name'.")
            if et == "network_latency":
                if self.delay_milliseconds is None and self.intensity is None:
                    raise ValueError(
                        "network_latency requires delay_milliseconds or intensity (ms)."
                    )
                if self.protocol and self.protocol.lower() not in ("tcp", "udp"):
                    raise ValueError("network_latency protocol must be 'tcp' or 'udp'.")
            if et == "disk_io":
                if self.read_bytes_per_sec is None and self.intensity is None:
                    raise ValueError(
                        "disk_io requires read_bytes_per_sec or intensity (MB/s)."
                    )
            if et == "cpu_hog":
                if self.intensity is None:
                    self.intensity = 100.0
                self.intensity = max(0.0, min(100.0, float(self.intensity)))
            return self

    else:

        @root_validator
        def _validate_v1(cls, values):
            et = values.get("experiment_type")
            if et == "process_kill" and not values.get("process_name"):
                raise ValueError("process_kill requires 'process_name'.")
            if et == "network_latency":
                if (
                    values.get("delay_milliseconds") is None
                    and values.get("intensity") is None
                ):
                    raise ValueError(
                        "network_latency requires delay_milliseconds or intensity (ms)."
                    )
                proto = values.get("protocol")
                if proto and str(proto).lower() not in ("tcp", "udp"):
                    raise ValueError("network_latency protocol must be 'tcp' or 'udp'.")
            if et == "disk_io":
                if (
                    values.get("read_bytes_per_sec") is None
                    and values.get("intensity") is None
                ):
                    raise ValueError(
                        "disk_io requires read_bytes_per_sec or intensity (MB/s)."
                    )
            if et == "cpu_hog":
                intensity = values.get("intensity")
                if intensity is None:
                    values["intensity"] = 100.0
                else:
                    try:
                        values["intensity"] = max(0.0, min(100.0, float(intensity)))
                    except Exception:
                        raise ValueError("cpu_hog intensity must be numeric.")
            return values


# HTTP Client for Gremlin API
@dataclass
class _AuthHeaders:
    authorization: str
    team_id: str


class GremlinApiError(Exception):
    def __init__(
        self, message: str, status: Optional[int] = None, body: Optional[Any] = None
    ):
        super().__init__(message)
        self.status = status
        self.body = body


class GremlinApiRetryableError(GremlinApiError):
    """Error type for retryable HTTP conditions (5xx, 408, 429)."""

    pass


class GremlinApiClient:
    def __init__(
        self,
        base_url: str,
        team_id: str,
        api_key: str,
        timeout: int,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        auth_value = (
            f"{GREMLIN_AUTH_SCHEME} {api_key}".strip()
            if GREMLIN_AUTH_SCHEME
            else api_key
        )
        self._auth = _AuthHeaders(authorization=auth_value, team_id=team_id)
        # More granular timeouts
        connect_timeout = min(10, max(1, timeout // 2))
        self._timeout = aiohttp.ClientTimeout(
            total=timeout,
            connect=connect_timeout,
            sock_connect=connect_timeout,
            sock_read=timeout,
        )
        self._verify_ssl = verify_ssl
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._ssl_context: Optional[ssl.SSLContext] = None
        if self._verify_ssl and GREMLIN_CA_BUNDLE:
            try:
                ctx = ssl.create_default_context(
                    cafile=(
                        GREMLIN_CA_BUNDLE if os.path.exists(GREMLIN_CA_BUNDLE) else None
                    )
                )
                if not os.path.exists(GREMLIN_CA_BUNDLE):
                    logger.warning(
                        f"GREMLIN_CA_BUNDLE path not found: {GREMLIN_CA_BUNDLE}. Using system CAs."
                    )
                self._ssl_context = ctx
            except Exception as e:
                logger.warning(
                    f"Failed to load GREMLIN_CA_BUNDLE '{GREMLIN_CA_BUNDLE}': {e}. Using system CAs."
                )
                self._ssl_context = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                if not self._verify_ssl:
                    logger.warning(
                        "GREMLIN_VERIFY_SSL=false; TLS verification disabled. Do not use in production."
                    )
                connector = aiohttp.TCPConnector(
                    ssl=(self._ssl_context if self._verify_ssl else False),
                    limit=GREMLIN_MAX_CONCURRENCY,
                )
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout, connector=connector
                )
            return self._session

    def _headers(self) -> Dict[str, str]:
        return {
            GREMLIN_AUTH_HEADER_NAME: self._auth.authorization,
            GREMLIN_TEAM_HEADER_NAME: self._auth.team_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"SFE-GremlinChaosPlugin/{PLUGIN_MANIFEST['version']}",
        }

    @staticmethod
    def _build_attack_payload(attack: AttackSpec, target: TargetSpec) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": attack.experiment_type,
            "duration_ms": attack.duration_seconds * 1000,
            "labels": attack.labels or {},
            "args": {},
            "target": {"type": target.target_type},
        }
        et = attack.experiment_type
        if et == "cpu_hog":
            payload["args"]["cpu_percent"] = int(attack.intensity or 100)
        elif et == "network_latency":
            payload["args"]["delay_ms"] = int(
                attack.delay_milliseconds or int(attack.intensity or 100)
            )
            if attack.packet_loss_percent is not None:
                payload["args"]["packet_loss_percent"] = float(
                    attack.packet_loss_percent
                )
            if attack.protocol:
                payload["args"]["protocol"] = attack.protocol.lower()
            if attack.destination_hosts:
                payload["args"]["destination_hosts"] = attack.destination_hosts
        elif et == "process_kill":
            payload["args"]["process_name"] = attack.process_name
        elif et == "disk_io":
            payload["args"]["read_bps"] = int(
                attack.read_bytes_per_sec or int((attack.intensity or 10) * 1024 * 1024)
            )
            if attack.write_bytes_per_sec is not None:
                payload["args"]["write_bps"] = int(attack.write_bytes_per_sec)
        elif et == "blackhole":
            if attack.destination_hosts:
                payload["args"]["destination_hosts"] = attack.destination_hosts
        elif et == "shutdown":
            payload["args"] = {}

        tt = target.target_type
        if tt in ("Host", "Container"):
            payload["target"]["names"] = target.names
        elif tt == "Kubernetes":
            if target.namespace:
                payload["target"]["namespace"] = target.namespace
            if target.cluster_name:
                payload["target"]["cluster_name"] = target.cluster_name
            if target.labels:
                payload["target"]["labels"] = target.labels
            if target.field_selectors:
                payload["target"]["field_selectors"] = target.field_selectors
            if target.name_regex:
                payload["target"]["name_regex"] = target.name_regex
        elif tt == "Random":
            payload["target"]["count"] = target.count
        elif tt == "All":
            pass
        return payload

    def _retry_decorator(self, op_name: str):
        if not TENACITY_AVAILABLE:
            return lambda f: f

        def after_sleep(_state):
            GREMLIN_API_RETRIES_TOTAL.labels(operation=op_name).inc()

        return retry(
            stop=stop_after_attempt(GREMLIN_RETRY_ATTEMPTS),
            wait=wait_exponential(
                multiplier=GREMLIN_RETRY_BACKOFF_FACTOR, min=1, max=10
            ),
            retry=retry_if_exception_type(
                (aiohttp.ClientError, asyncio.TimeoutError, GremlinApiRetryableError)
            ),
            after=after_sleep,
        )

    async def close(self):
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None

    async def _request(
        self,
        op: str,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        start = time.monotonic()
        async with _api_semaphore:
            try:
                async with session.request(
                    method=method, url=url, headers=self._headers(), json=json_body
                ) as resp:
                    text = await resp.text()
                    elapsed = time.monotonic() - start
                    GREMLIN_API_LATENCY_SECONDS.labels(operation=op).observe(elapsed)
                    cls = f"{resp.status//100}xx"
                    GREMLIN_HTTP_RESPONSES_TOTAL.labels(**{"class": cls}).inc()
                    if 200 <= resp.status < 300:
                        if not text:
                            return None
                        try:
                            return json.loads(text)
                        except Exception:
                            return text
                    if 500 <= resp.status < 600 or resp.status in (408, 429):
                        raise GremlinApiRetryableError(
                            f"HTTP {resp.status} for {op}",
                            status=resp.status,
                            body=_scrub(text),
                        )
                    raise GremlinApiError(
                        f"HTTP {resp.status} for {op}",
                        status=resp.status,
                        body=_scrub(text),
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError):
                GREMLIN_API_LATENCY_SECONDS.labels(operation=op).observe(
                    time.monotonic() - start
                )
                raise

    # API operations (with retries)
    async def create_attack(self, attack: AttackSpec, target: TargetSpec) -> str:
        GREMLIN_CREATE_TOTAL.labels(status="attempt").inc()
        payload = self._build_attack_payload(attack, target)
        op = "create_attack"
        decorated = self._retry_decorator(op)(self._request)
        try:
            data = await decorated(op, "POST", GREMLIN_API_CREATE_PATH, payload)
        except GremlinApiRetryableError:
            GREMLIN_CREATE_TOTAL.labels(status="retryable_error").inc()
            raise
        except GremlinApiError:
            GREMLIN_CREATE_TOTAL.labels(status="failure").inc()
            raise
        if isinstance(data, dict):
            attack_id = data.get("id") or data.get("guid") or data.get("attack_id")
            if attack_id:
                GREMLIN_CREATE_TOTAL.labels(status="success").inc()
                GREMLIN_SUBMISSION_SUCCESS_TOTAL.inc()
                return str(attack_id)
        GREMLIN_CREATE_TOTAL.labels(status="no_id").inc()
        raise GremlinApiError(
            "Create attack did not return an attack id",
            body=_scrub(json.dumps(data, ensure_ascii=False)),
        )

    async def get_attack_status(self, attack_id: str) -> Dict[str, Any]:
        op = "get_attack_status"
        decorated = self._retry_decorator(op)(self._request)
        path = GREMLIN_API_STATUS_PATH_FORMAT.format(id=attack_id)
        data = await decorated(op, "GET", path)
        if isinstance(data, dict):
            st = str(data.get("state") or data.get("status") or "UNKNOWN")
            GREMLIN_STATUS_POLLS_TOTAL.labels(state=st).inc()
        return data if isinstance(data, dict) else {"raw": data}

    async def halt_attack(self, attack_id: str) -> None:
        op = "halt_attack"
        decorated = self._retry_decorator(op)(self._request)
        path = GREMLIN_API_HALT_PATH_FORMAT.format(id=attack_id)
        await decorated(op, "POST", path)

    async def quick_check(self) -> bool:
        try:
            await self._request("quick_check", "GET", GREMLIN_API_QUICK_CHECK_PATH)
            return True
        except Exception as e:
            logger.debug(f"Gremlin quick_check failed: {e}")
            return False


# Singleton client (do not close per experiment)
_client_lock = asyncio.Lock()
_client: Optional[GremlinApiClient] = None


def _hostname_from_url(u: str) -> str:
    try:
        return urlparse(u).hostname or ""
    except Exception:
        return ""


async def _get_client() -> GremlinApiClient:
    global _client
    async with _client_lock:
        if _client is None:
            if not GREMLIN_BASE_URL.lower().startswith("https://"):
                logger.warning(
                    "GREMLIN_API_BASE_URL is not HTTPS; refusing to operate."
                )
                raise GremlinApiError("Insecure GREMLIN_API_BASE_URL (HTTPS required).")
            base_host = _hostname_from_url(GREMLIN_BASE_URL).lower()
            if GREMLIN_ALLOWED_BASE_URLS and base_host not in GREMLIN_ALLOWED_BASE_URLS:
                raise GremlinApiError(
                    f"Base URL host '{base_host}' not in GREMLIN_ALLOWED_BASE_URLS policy."
                )
            if not GREMLIN_TEAM_ID or not GREMLIN_API_KEY:
                raise GremlinApiError("Missing GREMLIN_TEAM_ID or GREMLIN_API_KEY.")
            _client = GremlinApiClient(
                base_url=GREMLIN_BASE_URL,
                team_id=GREMLIN_TEAM_ID,
                api_key=GREMLIN_API_KEY,
                timeout=GREMLIN_TIMEOUT_SECONDS,
                verify_ssl=GREMLIN_VERIFY_SSL,
            )
        return _client


# Health check
async def plugin_health() -> Dict[str, Any]:
    status = "ok"
    details: List[str] = []
    if not TENACITY_AVAILABLE:
        status = "degraded"
        details.append("Tenacity not installed; API retries disabled.")
    base_host = _hostname_from_url(GREMLIN_BASE_URL).lower()
    if GREMLIN_ALLOWED_BASE_URLS and base_host not in GREMLIN_ALLOWED_BASE_URLS:
        status = "error"
        details.append(
            f"Base URL host '{base_host}' not in GREMLIN_ALLOWED_BASE_URLS policy."
        )
        return {"status": status, "details": details}
    try:
        c = await _get_client()
        ok = await c.quick_check()
        if ok:
            details.append("Gremlin API connectivity/authentication OK.")
        else:
            status = "degraded"
            details.append("Gremlin API quick check failed (see debug logs).")
    except GremlinApiError as e:
        status = "error"
        details.append(
            f"Gremlin API error: status={e.status}, message={_scrub(str(e))}"
        )
    except Exception as e:
        status = "error"
        details.append(f"Initialization error: {e}")
    logger.info(f"Gremlin plugin health: {status} | details={details}")
    return {"status": status, "details": details}


# Public entrypoint
async def run_chaos_experiment(
    experiment_type: ExperimentType,
    target_type: TargetType,
    target_value: Union[str, Dict[str, Any], List[str], None],
    duration_seconds: int = 60,
    intensity: Optional[Union[float, int]] = None,
    labels: Optional[Dict[str, str]] = None,
    tags: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Execute a chaos experiment via Gremlin-compatible API.
    Returns a dict with:
      - success (bool)
      - reason (str)
      - attack_id (str or None)
      - experiment_type (str)
      - target (any)
      - final_state (str)
      - error (str or None)
      - audit_tags (list)
      - duration_seconds (float)
      - correlation_id (str)
    """
    started = time.monotonic()
    correlation_id = f"sfe-{uuid.uuid4().hex[:8]}"

    # Build TargetSpec from inputs
    try:
        target_payload: Dict[str, Any] = {"target_type": target_type}
        if target_type in ("Host", "Container"):
            if isinstance(target_value, str):
                target_payload["names"] = [target_value]
            elif isinstance(target_value, list):
                target_payload["names"] = target_value
        elif target_type == "Kubernetes":
            if not isinstance(target_value, dict):
                raise ValueError("Expected dict for Kubernetes target")
            target_payload.update(
                {
                    k: v
                    for k, v in target_value.items()
                    if k
                    in (
                        "namespace",
                        "cluster_name",
                        "labels",
                        "field_selectors",
                        "name_regex",
                    )
                }
            )
        elif target_type == "Random":
            if isinstance(target_value, dict) and "count" in target_value:
                target_payload["count"] = int(target_value["count"])
            else:
                target_payload["count"] = int(intensity or 1)
        elif target_type == "All":
            pass
        target = TargetSpec(**target_payload)
    except (ValidationError, ValueError) as e:
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="validation_error"
        ).inc()
        msg = f"[cid={correlation_id}] Target validation failed: {e}"
        logger.error(_scrub(msg))
        return {
            "success": False,
            "reason": "Target validation error",
            "attack_id": None,
            "experiment_type": experiment_type,
            "target": target_value,
            "final_state": "VALIDATION_ERROR",
            "error": _scrub(str(e)),
            "audit_tags": tags or [],
            "duration_seconds": 0.0,
            "correlation_id": correlation_id,
        }

    # Build AttackSpec
    try:
        attack = AttackSpec(
            experiment_type=experiment_type,
            duration_seconds=int(duration_seconds),
            intensity=float(intensity) if intensity is not None else None,
            labels=labels,
            tags=tags,
            process_name=kwargs.get("process_name"),
            delay_milliseconds=kwargs.get("delay_milliseconds"),
            packet_loss_percent=kwargs.get("packet_loss_percent"),
            protocol=kwargs.get("protocol"),
            destination_hosts=kwargs.get("destination_hosts"),
            read_bytes_per_sec=kwargs.get("read_bytes_per_sec"),
            write_bytes_per_sec=kwargs.get("write_bytes_per_sec"),
        )
    except (ValidationError, ValueError) as e:
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="validation_error"
        ).inc()
        msg = f"[cid={correlation_id}] Attack validation failed: {e}"
        logger.error(_scrub(msg))
        return {
            "success": False,
            "reason": "Attack validation error",
            "attack_id": None,
            "experiment_type": experiment_type,
            "target": target_value,
            "final_state": "VALIDATION_ERROR",
            "error": _scrub(str(e)),
            "audit_tags": tags or [],
            "duration_seconds": 0.0,
            "correlation_id": correlation_id,
        }

    attack_id: Optional[str] = None
    final_state: str = "UNKNOWN"
    inflight_inc = False

    try:
        client = await _get_client()
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="attempt"
        ).inc()
        GREMLIN_INFLIGHT_ATTACKS.inc()
        inflight_inc = True

        await _audit_event(
            "chaos_experiment_attempt",
            {
                "experiment_type": experiment_type,
                "target": target.model_dump() if hasattr(target, "model_dump") else target.dict(),  # type: ignore
                "duration": attack.duration_seconds,
                "intensity": attack.intensity,
                "labels": attack.labels,
                "tags": attack.tags,
                "correlation_id": correlation_id,
            },
        )

        attack_id = await client.create_attack(attack, target)
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="initiated"
        ).inc()
        await _audit_event(
            "chaos_experiment_initiated",
            {
                "experiment_type": experiment_type,
                "attack_id": attack_id,
                "correlation_id": correlation_id,
            },
        )
        logger.info(
            f"[cid={correlation_id}] Initiated chaos attack: {attack_id} (type={experiment_type})"
        )

        # Monitor until terminal state or timeout window
        terminal_states = {"HALTED", "SUCCEEDED", "FAILED", "ROLLED_BACK"}
        poll_start = time.monotonic()
        max_wait = attack.duration_seconds + GREMLIN_TIMEOUT_SECONDS + 60
        state = "PENDING"
        status_reason = None

        while (
            state not in terminal_states and (time.monotonic() - poll_start) < max_wait
        ):
            await asyncio.sleep(
                GREMLIN_POLL_INTERVAL_SECONDS
                + random.uniform(0, GREMLIN_POLL_JITTER_SECONDS)
            )
            status = await client.get_attack_status(attack_id)
            state = str(status.get("state") or status.get("status") or "UNKNOWN")
            status_reason = status.get("statusReason") or status.get("reason")
            logger.info(
                f"[cid={correlation_id}] Attack {attack_id} status={state} reason={status_reason}"
            )
            if state in terminal_states:
                final_state = state
                break

        if final_state not in terminal_states:
            final_state = "MONITORING_TIMED_OUT"
            CHAOS_ATTACKS_TOTAL.labels(
                experiment_type=experiment_type, status="monitoring_timeout"
            ).inc()
            await _audit_event(
                "chaos_experiment_monitoring_timeout",
                {
                    "experiment_type": experiment_type,
                    "attack_id": attack_id,
                    "duration": round(time.monotonic() - started, 3),
                    "correlation_id": correlation_id,
                },
            )
            GREMLIN_HALTS_TOTAL.labels(status="attempt").inc()
            try:
                await client.halt_attack(attack_id)
                GREMLIN_HALTS_TOTAL.labels(status="success").inc()
                logger.info(
                    f"[cid={correlation_id}] Halted timed-out attack {attack_id}"
                )
            except Exception as e:
                GREMLIN_HALTS_TOTAL.labels(status="failure").inc()
                logger.warning(
                    f"[cid={correlation_id}] Failed to halt timed-out attack {attack_id}: {e}"
                )

        duration = time.monotonic() - started
        if final_state == "SUCCEEDED":
            CHAOS_ATTACKS_TOTAL.labels(
                experiment_type=experiment_type, status="succeeded"
            ).inc()
            CHAOS_ATTACK_DURATION_SECONDS.labels(
                experiment_type=experiment_type
            ).observe(duration)
            await _audit_event(
                "chaos_experiment_succeeded",
                {
                    "experiment_type": experiment_type,
                    "attack_id": attack_id,
                    "duration": round(duration, 3),
                    "correlation_id": correlation_id,
                },
            )
            return {
                "success": True,
                "reason": "Attack succeeded",
                "attack_id": attack_id,
                "experiment_type": experiment_type,
                "target": target_value,
                "final_state": final_state,
                "error": None,
                "audit_tags": tags or [],
                "duration_seconds": round(duration, 3),
                "correlation_id": correlation_id,
            }
        else:
            CHAOS_ATTACKS_TOTAL.labels(
                experiment_type=experiment_type, status="failed"
            ).inc()
            CHAOS_ATTACK_ERRORS_TOTAL.labels(
                experiment_type=experiment_type, error_type=final_state
            ).inc()
            await _audit_event(
                "chaos_experiment_failed",
                {
                    "experiment_type": experiment_type,
                    "attack_id": attack_id,
                    "final_state": final_state,
                    "reason": status_reason,
                    "duration": round(duration, 3),
                    "correlation_id": correlation_id,
                },
            )
            return {
                "success": False,
                "reason": f"Attack finished with state: {final_state} ({status_reason})",
                "attack_id": attack_id,
                "experiment_type": experiment_type,
                "target": target_value,
                "final_state": final_state,
                "error": status_reason,
                "audit_tags": tags or [],
                "duration_seconds": round(duration, 3),
                "correlation_id": correlation_id,
            }

    except asyncio.CancelledError:
        raise
    except GremlinApiError as e:
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="api_error"
        ).inc()
        CHAOS_ATTACK_ERRORS_TOTAL.labels(
            experiment_type=experiment_type, error_type="api_error"
        ).inc()
        msg = f"[cid={correlation_id}] Gremlin API error: status={e.status}, body={e.body}"
        logger.error(_scrub(msg))
        await _audit_event(
            "chaos_experiment_api_error",
            {
                "experiment_type": experiment_type,
                "target": target_value,
                "error": _scrub(str(e)),
                "correlation_id": correlation_id,
            },
        )
        return {
            "success": False,
            "reason": "Gremlin API error",
            "attack_id": attack_id,
            "experiment_type": experiment_type,
            "target": target_value,
            "final_state": "API_ERROR",
            "error": _scrub(str(e)),
            "audit_tags": tags or [],
            "duration_seconds": round(time.monotonic() - started, 3),
            "correlation_id": correlation_id,
        }
    except Exception as e:
        CHAOS_ATTACKS_TOTAL.labels(
            experiment_type=experiment_type, status="unexpected_error"
        ).inc()
        CHAOS_ATTACK_ERRORS_TOTAL.labels(
            experiment_type=experiment_type, error_type="unexpected_exception"
        ).inc()
        logger.error(
            f"[cid={correlation_id}] Unexpected error in run_chaos_experiment: {e}",
            exc_info=True,
        )
        await _audit_event(
            "chaos_experiment_unexpected_error",
            {
                "experiment_type": experiment_type,
                "target": target_value,
                "error": str(e),
                "correlation_id": correlation_id,
            },
        )
        return {
            "success": False,
            "reason": "Unexpected error",
            "attack_id": attack_id,
            "experiment_type": experiment_type,
            "target": target_value,
            "final_state": "UNEXPECTED_ERROR",
            "error": str(e),
            "audit_tags": tags or [],
            "duration_seconds": round(time.monotonic() - started, 3),
            "correlation_id": correlation_id,
        }
    finally:
        if inflight_inc:
            try:
                GREMLIN_INFLIGHT_ATTACKS.dec()
            except Exception:
                pass


# Entry point registration
def register_plugin_entrypoints(register_func: Callable):
    logger.info("Registering GremlinChaosPlugin entrypoints...")
    register_func(
        name="gremlin_chaos",
        executor_func=run_chaos_experiment,
        capabilities=["chaos_injection"],
    )


# Optional shutdown hook (call from your host when stopping the process)
async def shutdown_plugin():
    try:
        c = await _get_client()
        await c.close()
    except Exception:
        pass
