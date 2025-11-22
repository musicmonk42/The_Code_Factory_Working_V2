import asyncio
import json
import logging
import os
import re
import sys
import time
from typing import Dict, Any, Optional
from functools import wraps

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from opentelemetry import trace
from prometheus_client import Counter, Histogram
from aiohttp_client_cache import CachedSession, SQLiteBackend
from aiohttp import (
    ClientTimeout,
    TCPConnector,
    ClientResponseError,
    ClientConnectorError,
)

# --- PII Redaction Setup ---
try:
    from .logging_utils import PIIRedactorFilter
except ImportError:

    class PIIRedactorFilter:
        def _redact_dict(
            self, data: Dict[str, Any], seen=None, depth=0
        ) -> Dict[str, Any]:
            """Fallback PII redactor with matching signature"""
            if seen is None:
                seen = set()
            if depth > 10:  # Prevent infinite recursion
                return data

            redacted_data = data.copy()
            sensitive_keys = [
                "email",
                "phone",
                "address",
                "ssn",
                "credit_card",
                "ip_address",
                "password",
                "api_key",
                "token",
            ]
            pii_patterns = [
                re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
                re.compile(r"\b(?:\d{3}[-.\s]?){2}\d{4}\b"),
                re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            ]

            def redact_value(val):
                if isinstance(val, str):
                    for pattern in pii_patterns:
                        val = pattern.sub("[REDACTED]", val)
                return val

            for key, value in redacted_data.items():
                if key in sensitive_keys:
                    redacted_data[key] = "[REDACTED]"
                elif isinstance(value, dict):
                    redacted_data[key] = self._redact_dict(value, seen, depth + 1)
                elif isinstance(value, str):
                    redacted_data[key] = redact_value(value)
            return redacted_data

    logging.warning(
        "logging_utils.py not found. Using enhanced placeholder PII redaction."
    )

# Create a module-level instance to avoid recreating on every request
_pii_filter = PIIRedactorFilter()

logger = logging.getLogger(__name__)

# --- Prometheus Metrics ---
HTTP_CALLS_TOTAL = Counter(
    "http_calls_total",
    "Total number of HTTP calls made by clients",
    ["client_name", "method", "status"],
)
HTTP_CALL_LATENCY_SECONDS = Histogram(
    "http_call_latency_seconds",
    "Latency of HTTP calls in seconds",
    ["client_name", "method", "status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
)


# --- Decorator for Centralized Logging, Tracing, and Metrics ---
def _with_client_logging_and_metrics(span_name: str, span_attributes: Dict[str, Any]):
    """
    Decorator to centralize logging, tracing, exception handling, and metrics for client methods.
    Now with manual timing for reliable Prometheus Histogram metrics.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            tracer = trace.get_tracer(__name__)
            method_name = func.__name__
            client_name = self.__class__.__name__

            with tracer.start_as_current_span(span_name) as span:
                for k, v in span_attributes.items():
                    span.set_attribute(k, v)

                status_label = "failure"
                start_time = time.time()  # Manual start for latency timing
                try:
                    logger.info(
                        f"Calling client method {method_name} for endpoint: {self.endpoint}"
                    )
                    result = await func(self, *args, **kwargs)
                    span.set_attribute("status", "success")
                    logger.info(f"Client method {method_name} completed successfully.")
                    status_label = "success"
                    return result
                except (ClientResponseError, ClientConnectorError) as e:
                    span.set_attribute("status", "http_error")
                    span.record_exception(e)
                    logger.error(f"HTTP client error in {method_name}: {e}")
                    status_label = "http_error"
                    raise
                except asyncio.TimeoutError:
                    span.set_attribute("status", "timeout")
                    span.record_exception(TimeoutError("Client request timed out"))
                    logger.error(
                        f"Timeout in {method_name} for endpoint: {self.endpoint}"
                    )
                    status_label = "timeout"
                    raise
                except Exception as e:
                    span.set_attribute("status", "error")
                    span.record_exception(e)
                    logger.error(
                        f"Unexpected error in {method_name}: {e}", exc_info=True
                    )
                    status_label = "error"
                    raise
                finally:
                    duration = time.time() - start_time  # Calculate duration manually
                    HTTP_CALL_LATENCY_SECONDS.labels(
                        client_name=client_name, method=method_name, status=status_label
                    ).observe(duration)
                    HTTP_CALLS_TOTAL.labels(
                        client_name=client_name, method=method_name, status=status_label
                    ).inc()

        return wrapper

    return decorator


# --- Base Class for HTTP Clients ---
class _BaseHTTPClient:
    """
    Abstract base class to reduce duplication between clients.
    Now with timeouts, concurrency limits, and enhanced PII for production.
    """

    def __init__(self, endpoint: str, session: Optional[aiohttp.ClientSession] = None):
        self.endpoint = endpoint
        self._session_managed_externally = session is not None

        api_key = os.getenv("ML_PLATFORM_API_KEY")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if not api_key:
            logger.warning(
                "ML_PLATFORM_API_KEY environment variable not set. API calls might fail."
            )

        if session:
            self.session = session
        else:
            # Check if we should disable cache for tests
            disable_cache = os.getenv("DISABLE_HTTP_CACHE", "false").lower() == "true"
            timeout_secs = float(os.getenv("HTTP_TIMEOUT_SECS", 30.0))
            conn_limit = int(os.getenv("HTTP_CONN_LIMIT", 100))
            conn_limit_per_host = int(os.getenv("HTTP_CONN_LIMIT_PER_HOST", 20))
            ssl_verify = os.getenv("HTTP_SSL_VERIFY", "true").lower() == "true"

            connector = TCPConnector(
                limit=conn_limit, limit_per_host=conn_limit_per_host, ssl=ssl_verify
            )

            python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
            user_agent = f"MetaLearningOrchestrator/1.0 (Python/{python_version}; aiohttp/{aiohttp.__version__})"

            if disable_cache:
                # Use regular ClientSession for tests
                self.session = aiohttp.ClientSession(
                    headers={**self.headers, "User-Agent": user_agent},
                    timeout=ClientTimeout(
                        total=timeout_secs,
                        connect=5,
                        sock_connect=5,
                        sock_read=timeout_secs - 10,
                    ),
                    connector=connector,
                )
            else:
                # Use CachedSession for production
                self.session = CachedSession(
                    cache=SQLiteBackend("api_cache.sqlite", expire_after=300),
                    headers={**self.headers, "User-Agent": user_agent},
                    timeout=ClientTimeout(
                        total=timeout_secs,
                        connect=5,
                        sock_connect=5,
                        sock_read=timeout_secs - 10,
                    ),
                    connector=connector,
                )
        logger.info(f"Client initialized for endpoint: {self.endpoint}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Explicitly close the session if managed internally."""
        if not self._session_managed_externally and hasattr(self.session, "close"):
            if not getattr(self.session, "closed", True):
                await self.session.close()
                logger.info(f"{self.__class__.__name__} aiohttp session closed.")

    async def _request_with_redaction(
        self, method: str, url: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Performs an HTTP request, redacting PII from the payload before sending.
        Handles response parsing with JSON fallback and content-type checks.
        """
        # Fix: Use module-level _pii_filter instance to avoid recreation
        redacted_data = (
            _pii_filter._redact_dict(data, seen=set(), depth=0) if data else None
        )

        try:
            async with self.session.request(
                method, url, json=redacted_data, headers=self.headers
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/json" in content_type:
                    return await response.json()
                else:
                    text = await response.text()
                    logger.warning(
                        f"Non-JSON response from {url} with Content-Type '{content_type}': {text[:200]}..."
                    )
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {
                            "success": response.ok,
                            "status_code": response.status,
                            "content": text,
                        }
        except ClientResponseError as e:
            logger.error(f"HTTP error {e.status} from {url}: {e.message}")
            raise
        except (ClientConnectorError, asyncio.TimeoutError) as e:
            logger.error(f"Connection/Timeout error for {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in request to {url}: {e}", exc_info=True)
            raise


# --- Real External Service Clients ---
class MLPlatformClient(_BaseHTTPClient):
    """
    Client for interacting with the ML Platform service via HTTP.
    Handles training, evaluation, deployment, and status checks.
    """

    # Legacy method names for backward compatibility
    async def trigger_training_job(
        self, training_data_path: str, params: Dict[str, Any]
    ) -> str:
        """Legacy method name - redirects to train_model."""
        return await self.train_model(
            {"data_path": training_data_path, "params": params}
        )

    async def get_training_job_status(self, job_id: str) -> Dict[str, Any]:
        """Legacy method name - redirects to get_training_status."""
        return await self.get_training_status(job_id)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("train_ml_model", {"ml.action": "train"})
    async def train_model(self, training_data: Dict[str, Any]) -> str:
        """Triggers an ML model training job via HTTP POST."""
        span = trace.get_current_span()
        span.set_attribute("ml.training_params", str(training_data.get("params", {})))

        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/train", data=training_data
        )
        job_id = response_data.get("job_id")
        if not job_id:
            raise ValueError("ML Platform did not return a valid job_id")
        logger.info(f"ML training job triggered: {job_id}")
        return job_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("get_training_status", {"ml.action": "status"})
    async def get_training_status(self, job_id: str) -> Dict[str, Any]:
        """Gets the status of an ML training job via HTTP GET."""
        span = trace.get_current_span()
        span.set_attribute("ml.job_id", job_id)

        response_data = await self._request_with_redaction(
            "GET", f"{self.endpoint}/training/{job_id}"
        )
        status = response_data.get("status")
        span.set_attribute("ml.status", status)
        return response_data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("evaluate_ml_model", {"ml.action": "evaluate"})
    async def evaluate_model(
        self, model_id: str, eval_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Evaluates an ML model via HTTP POST."""
        span = trace.get_current_span()
        span.set_attribute("ml.model_id", model_id)

        payload = {"model_id": model_id, "eval_data": eval_data}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/evaluate", data=payload
        )
        metrics = response_data.get("metrics", {})
        span.set_attribute("ml.accuracy", metrics.get("accuracy", 0))
        return response_data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("deploy_ml_model", {"ml.action": "deploy"})
    async def deploy_model(self, model_id: str, version: str) -> bool:
        """Deploys an ML model to production via HTTP POST."""
        span = trace.get_current_span()
        span.set_attribute("ml.model_id", model_id)
        span.set_attribute("ml.version", version)

        payload = {"model_id": model_id, "version": version}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/deploy", data=payload
        )
        return response_data.get("success", False)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("delete_ml_model", {"ml.action": "delete"})
    async def delete_model(self, model_id: str) -> bool:
        """Deletes an ML model via HTTP DELETE."""
        span = trace.get_current_span()
        span.set_attribute("ml.model_id", model_id)

        response_data = await self._request_with_redaction(
            "DELETE", f"{self.endpoint}/models/{model_id}"
        )
        success = response_data.get("success", False)
        if success:
            logger.info(f"ML model {model_id} deleted successfully.")
        else:
            logger.warning(f"Failed to delete ML model {model_id}.")
        return success

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics(
        "get_evaluation_metrics", {"ml.action": "metrics"}
    )
    async def get_evaluation_metrics(self, model_id: str) -> Dict[str, Any]:
        """Gets evaluation metrics for a model via HTTP GET."""
        span = trace.get_current_span()
        span.set_attribute("ml.model_id", model_id)

        response_data = await self._request_with_redaction(
            "GET", f"{self.endpoint}/models/{model_id}/metrics"
        )
        return response_data.get("metrics", {})


class AgentConfigurationService(_BaseHTTPClient):
    """
    Client for a service that updates configurations for agents
    (e.g., DecisionOptimizer, PolicyEngine) via HTTP.
    """

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics(
        "update_prioritization_weights", {"config.type": "prioritization_weights"}
    )
    async def update_prioritization_weights(
        self, weights: Dict[str, float], version: str
    ) -> bool:
        """Updates prioritization weights for DecisionOptimizer via HTTP."""
        span = trace.get_current_span()
        span.set_attribute("config.version", version)

        payload = {"weights": weights, "version": version}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/prioritization-weights", data=payload
        )
        span.set_attribute("config.status", "success")
        return response_data.get("success", True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics(
        "update_policy_rules", {"config.type": "policy_rules"}
    )
    async def update_policy_rules(self, rules: Dict[str, Any], version: str) -> bool:
        """Updates policy rules for PolicyEngine via HTTP."""
        span = trace.get_current_span()
        span.set_attribute("config.version", version)

        payload = {"rules": rules, "version": version}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/policy-rules", data=payload
        )
        span.set_attribute("config.status", "success")
        return response_data.get("success", True)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics("update_rl_policy", {"config.type": "rl_policy"})
    async def update_rl_policy(self, policy_model_id: str, version: str) -> bool:
        """Deploys a new RL policy model to relevant agents via HTTP."""
        span = trace.get_current_span()
        span.set_attribute("config.model_id", policy_model_id)
        span.set_attribute("config.version", version)

        payload = {"model_id": policy_model_id, "version": version}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/rl-policy", data=payload
        )
        span.set_attribute("config.status", "success")
        return response_data.get("success", True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics(
        "delete_agent_config", {"config.action": "delete"}
    )
    async def delete_config(self, config_type: str, config_id: str) -> bool:
        """Deletes a specific configuration by type and ID."""
        span = trace.get_current_span()
        span.set_attribute("config.type", config_type)
        span.set_attribute("config.id", config_id)

        response_data = await self._request_with_redaction(
            "DELETE", f"{self.endpoint}/{config_type}/{config_id}"
        )
        logger.info(
            f"Agent configuration {config_id} of type {config_type} deleted. Response: {response_data}"
        )
        return response_data.get("success", False)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    @_with_client_logging_and_metrics(
        "rollback_agent_config", {"config.action": "rollback"}
    )
    async def rollback_config(
        self, config_type: str, config_id: str, version: str
    ) -> bool:
        """Rolls back a specific configuration to a previous version."""
        span = trace.get_current_span()
        span.set_attribute("config.type", config_type)
        span.set_attribute("config.id", config_id)
        span.set_attribute("config.rollback_version", version)

        payload = {"version": version}
        response_data = await self._request_with_redaction(
            "POST", f"{self.endpoint}/{config_type}/{config_id}/rollback", data=payload
        )
        logger.info(
            f"Agent configuration {config_id} of type {config_type} rolled back to version {version}. Response: {response_data}"
        )
        return response_data.get("success", False)
