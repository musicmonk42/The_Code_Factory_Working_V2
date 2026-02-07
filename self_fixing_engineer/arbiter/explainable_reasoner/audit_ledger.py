# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

# Structured logging
import structlog
import tenacity
from self_fixing_engineer.arbiter.explainable_reasoner.metrics import get_or_create_metric
from self_fixing_engineer.arbiter.explainable_reasoner.reasoner_config import SensitiveValue

# Real internal imports (enforce)
from self_fixing_engineer.arbiter.explainable_reasoner.reasoner_errors import (
    ReasonerError,
    ReasonerErrorCode,
)
from self_fixing_engineer.arbiter.explainable_reasoner.utils import redact_pii
from prometheus_client import Counter, Histogram
from pydantic import HttpUrl, ValidationError

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
_logger = structlog.get_logger(__name__)

# Optional breakers
try:
    import pybreaker

    BREAKER_AVAILABLE = True
except ImportError:
    BREAKER_AVAILABLE = False
    pybreaker = None
    _logger.warning("pybreaker missing; no circuit breakers")

# Optional OpenTelemetry
try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    _logger.warning("opentelemetry missing; tracing disabled")

# Define metrics
AUDIT_SEND_LATENCY = get_or_create_metric(
    Histogram, "audit_send_latency_seconds", "Audit event send latency", ("status",)
)
AUDIT_ERRORS = get_or_create_metric(
    Counter, "audit_errors_total", "Audit errors", ("code",)
)
AUDIT_BATCH_SIZE = get_or_create_metric(
    Histogram, "audit_batch_size_total", "Number of events in batch", ("status",)
)
AUDIT_RATE_LIMIT_HITS = get_or_create_metric(
    Counter, "audit_rate_limit_hits_total", "Rate limit hits", ("endpoint",)
)


def stop_after_attempt_from_self(retry_state: tenacity.RetryCallState) -> bool:
    """A stop condition that correctly pulls max_retries from the instance."""
    self_instance = retry_state.args[0]
    return retry_state.attempt_number > self_instance.max_retries


class AuditLedgerClient:
    """
    Client for an external, immutable audit ledger.
    This client uses `httpx` for asynchronous HTTP POST requests to the ledger endpoint.
    It includes retry logic with exponential backoff and robust error handling.
    """

    def __init__(
        self,
        ledger_url: str = os.getenv("AUDIT_LEDGER_URL", "https://localhost:8080/audit"),
        api_key: Optional[str] = os.getenv("AUDIT_API_KEY"),
        max_retries: int = int(os.getenv("AUDIT_MAX_RETRIES", "3")),
        initial_backoff_delay: float = float(os.getenv("AUDIT_BACKOFF_DELAY", "1.0")),
        timeout: float = float(os.getenv("AUDIT_TIMEOUT", "5.0")),
        health_endpoint: Optional[str] = os.getenv("AUDIT_HEALTH_ENDPOINT"),
    ):
        """
        Initializes the AuditLedgerClient.

        Args:
            ledger_url (str): The URL of the external audit ledger endpoint.
            api_key (Optional[str]): API key for authentication.
            max_retries (int): Maximum number of retries for sending an audit event.
            initial_backoff_delay (float): Initial delay in seconds for exponential backoff.
            timeout (float): Timeout in seconds for each HTTP request to the ledger.
            health_endpoint (Optional[str]): Optional specific endpoint for health checks (e.g., "/health").
        Raises:
            ValueError: If ledger_url is invalid or not HTTPS.
        """
        try:
            self.ledger_url = str(HttpUrl(ledger_url))
            if not self.ledger_url.startswith("https"):
                raise ValueError("Ledger URL must use HTTPS for security.")
        except ValidationError as e:
            raise ValueError(
                f"Invalid URL provided for ledger_url: {ledger_url}"
            ) from e

        self.ledger_url = self.ledger_url.rstrip("/")
        self.api_key = SensitiveValue(api_key) if api_key else None
        self.max_retries = max_retries
        self.initial_backoff_delay = initial_backoff_delay
        self.timeout = timeout
        self.health_endpoint = health_endpoint
        self._logger = _logger.bind(module="audit_ledger")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            verify=True,
        )
        if BREAKER_AVAILABLE:
            self._breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60)
        else:
            self._breaker = None
            self._logger.warning("No circuit breaker")
        self._logger.info(
            "audit_client_init",
            ledger_url=self.ledger_url,
            max_retries=self.max_retries,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initializes the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                verify=True,
            )
        return self._client

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=10)
        + tenacity.wait_random(0, 0.5),
        stop=stop_after_attempt_from_self,
        retry=tenacity.retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.TimeoutException, asyncio.TimeoutError)
        ),
        before_sleep=lambda retry_state: _logger.warning(
            "retry_send",
            attempt=retry_state.attempt_number,
            error=str(retry_state.outcome.exception()),
        ),
    )
    async def _send_event_with_retries(self, audit_record: Dict[str, Any]) -> bool:
        """
        Sends an audit event with retries and exponential backoff.

        Args:
            audit_record: The audit event dictionary.
        Returns:
            True if successful, False otherwise.
        Raises:
            ReasonerError: On unexpected errors after all retries.
        """
        start_time = time.monotonic()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key.get_actual_value()}"

        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None

        if tracer:
            with tracer.start_as_current_span(
                "audit_send_event", kind=SpanKind.CLIENT
            ) as span:
                span.set_attribute("event_type", audit_record["event_type"])
                span.set_attribute("record_hash", audit_record["record_hash"])
                try:
                    client = await self._get_client()
                    if self._breaker:
                        response = await self._breaker.call_async(
                            client.post,
                            self.ledger_url,
                            json=audit_record,
                            headers=headers,
                        )
                    else:
                        response = await client.post(
                            self.ledger_url, json=audit_record, headers=headers
                        )
                    response.raise_for_status()
                    self._logger.info(
                        "audit_event_sent_success",
                        event_type=audit_record["event_type"],
                    )
                    if AUDIT_SEND_LATENCY:
                        AUDIT_SEND_LATENCY.labels(status="success").observe(
                            time.monotonic() - start_time
                        )
                    if span:
                        span.set_attribute("status_code", response.status_code)
                        span.set_attribute("status", "success")
                    return True
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    if status_code == 429:
                        retry_after = float(
                            e.response.headers.get(
                                "Retry-After", self.initial_backoff_delay
                            )
                        )
                        remaining = e.response.headers.get(
                            "x-ratelimit-remaining", None
                        )
                        if remaining and int(remaining) < 5:
                            self._logger.warning(
                                "low_rate_limit",
                                endpoint=self.ledger_url,
                                remaining=remaining,
                                retry_after=retry_after,
                            )
                            AUDIT_RATE_LIMIT_HITS.labels(endpoint=self.ledger_url).inc()
                            raise ReasonerError(
                                f"Rate limit hit; retry after {retry_after}s",
                                ReasonerErrorCode.SERVICE_UNAVAILABLE,
                                e,
                            )
                    self._logger.warning(
                        "audit_send_http_error", status_code=status_code, error=str(e)
                    )
                    if AUDIT_ERRORS:
                        AUDIT_ERRORS.labels(code=f"http_{status_code}").inc()
                    if span:
                        span.set_attribute("status_code", status_code)
                        span.record_exception(e)
                        span.set_status(
                            trace.StatusCode.ERROR, f"HTTP Error: {status_code}"
                        )
                    raise
                except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                    self._logger.warning("audit_send_timeout", error=str(e))
                    if AUDIT_ERRORS:
                        AUDIT_ERRORS.labels(code="timeout").inc()
                    if span:
                        span.record_exception(e)
                        span.set_status(trace.StatusCode.ERROR, "Timeout")
                    raise ReasonerError(
                        "Audit log request timed out", ReasonerErrorCode.TIMEOUT, e
                    )
                except Exception as e:
                    self._logger.error("audit_send_failed", error=str(e), exc_info=True)
                    if AUDIT_ERRORS:
                        AUDIT_ERRORS.labels(code="unknown").inc()
                    if span:
                        span.record_exception(e)
                        span.set_status(trace.StatusCode.ERROR, "Unexpected Error")
                    raise ReasonerError(
                        "Audit log failed after retries",
                        ReasonerErrorCode.AUDIT_LOG_FAILED,
                        e,
                    )
        else:
            # Existing logic without tracing
            try:
                client = await self._get_client()
                if self._breaker:
                    response = await self._breaker.call_async(
                        client.post, self.ledger_url, json=audit_record, headers=headers
                    )
                else:
                    response = await client.post(
                        self.ledger_url, json=audit_record, headers=headers
                    )
                response.raise_for_status()
                self._logger.info(
                    "audit_event_sent_success", event_type=audit_record["event_type"]
                )
                if AUDIT_SEND_LATENCY:
                    AUDIT_SEND_LATENCY.labels(status="success").observe(
                        time.monotonic() - start_time
                    )
                return True
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                if status_code == 429:
                    retry_after = float(
                        e.response.headers.get(
                            "Retry-After", self.initial_backoff_delay
                        )
                    )
                    remaining = e.response.headers.get("x-ratelimit-remaining", None)
                    if remaining and int(remaining) < 5:
                        self._logger.warning(
                            "low_rate_limit",
                            endpoint=self.ledger_url,
                            remaining=remaining,
                            retry_after=retry_after,
                        )
                        AUDIT_RATE_LIMIT_HITS.labels(endpoint=self.ledger_url).inc()
                        raise ReasonerError(
                            f"Rate limit hit; retry after {retry_after}s",
                            ReasonerErrorCode.SERVICE_UNAVAILABLE,
                            e,
                        )
                self._logger.warning(
                    "audit_send_http_error", status_code=status_code, error=str(e)
                )
                if AUDIT_ERRORS:
                    AUDIT_ERRORS.labels(code=f"http_{status_code}").inc()
                raise
            except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                self._logger.warning("audit_send_timeout", error=str(e))
                if AUDIT_ERRORS:
                    AUDIT_ERRORS.labels(code="timeout").inc()
                raise ReasonerError(
                    "Audit log request timed out", ReasonerErrorCode.TIMEOUT, e
                )
            except Exception as e:
                self._logger.error("audit_send_failed", error=str(e), exc_info=True)
                if AUDIT_ERRORS:
                    AUDIT_ERRORS.labels(code="unknown").inc()
                raise ReasonerError(
                    "Audit log failed after retries",
                    ReasonerErrorCode.AUDIT_LOG_FAILED,
                    e,
                )

    async def log_event(
        self, event_type: str, details: Dict[str, Any], operator: str = "system"
    ) -> bool:
        """
        Logs an auditable event to the external ledger.

        Args:
            event_type (str): Type of audit event (e.g., "history_purge", "data_export").
            details (Dict[str, Any]): Specific details of the event.
            operator (str): The entity initiating the action (default: "system").
        Returns:
            bool: True if logging was successful, False otherwise.
        Raises:
            ValueError: If event_type or operator is invalid.
            ReasonerError: On unexpected errors during logging.
        """
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(operator, str) or not operator:
            raise ValueError("operator must be a non-empty string")
        if not isinstance(details, dict):
            raise ValueError("details must be a dictionary")

        redacted_details = redact_pii(details)
        audit_record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "operator": operator,
            "details": redacted_details,
            "record_hash": hashlib.sha256(
                json.dumps(redacted_details, sort_keys=True, default=str).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }
        self._logger.info(
            "auditing_event",
            event_type=event_type,
            operator=operator,
            record_hash=audit_record["record_hash"],
        )
        try:
            success = await self._send_event_with_retries(audit_record)
            if not success:
                self._logger.error(
                    "audit_event_send_failed_after_retries", event_type=event_type
                )
            return success
        except ReasonerError as e:
            self._logger.error(
                "audit_log_structured_error", event_type=event_type, message=e.message
            )
            return False
        except Exception as e:
            self._logger.critical(
                "audit_log_unhandled_exception",
                event_type=event_type,
                error=str(e),
                exc_info=True,
            )
            return False

    async def log_batch_events(self, events: List[Dict[str, Any]]) -> bool:
        """
        Logs a batch of audit events concurrently.

        Args:
            events: List of event dictionaries with event_type, details, and optional operator.
        Returns:
            True if all events were logged successfully, False otherwise.
        Raises:
            ValueError: If events list is invalid.
        """
        if not isinstance(events, list):
            raise ValueError("Events must be a list of dictionaries")
        if not events:
            self._logger.info("batch_empty", count=0)
            return True

        semaphore = asyncio.Semaphore(int(os.getenv("AUDIT_BATCH_CONCURRENCY", "10")))
        success = True

        async def log_single_event(event):
            async with semaphore:
                return await self.log_event(
                    event.get("event_type", "unknown"),
                    event.get("details", {}),
                    event.get("operator", "system"),
                )

        self._logger.info("batch_start", count=len(events))
        if AUDIT_BATCH_SIZE:
            AUDIT_BATCH_SIZE.labels(status="started").observe(len(events))

        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None

        if tracer:
            with tracer.start_as_current_span(
                "audit_log_batch", kind=SpanKind.CLIENT
            ) as span:
                span.set_attribute("batch_size", len(events))
                results = await asyncio.gather(
                    *[log_single_event(event) for event in events],
                    return_exceptions=True,
                )
        else:
            results = await asyncio.gather(
                *[log_single_event(event) for event in events], return_exceptions=True
            )

        for result, event in zip(results, events):
            if isinstance(result, Exception) or not result:
                success = False
                self._logger.warning(
                    "batch_event_failed",
                    event_type=event.get("event_type", "unknown"),
                    error=str(result) if isinstance(result, Exception) else "failed",
                )
        if AUDIT_BATCH_SIZE:
            AUDIT_BATCH_SIZE.labels(status="success" if success else "failure").observe(
                len(events)
            )
        return success

    async def health_check(self) -> bool:
        """
        Checks ledger connectivity with a minimal request.

        Returns:
            True if reachable, False otherwise.
        Raises:
            ReasonerError: On unexpected errors.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key.get_actual_value()}"

        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None
        if tracer:
            with tracer.start_as_current_span(
                "audit_health_check", kind=SpanKind.CLIENT
            ) as span:
                try:
                    client = await self._get_client()
                    if self.health_endpoint:
                        response = await client.get(
                            f"{self.ledger_url}/{self.health_endpoint.lstrip('/')}",
                            headers=headers,
                        )
                        span.set_attribute("http.method", "GET")
                        span.set_attribute(
                            "http.url",
                            f"{self.ledger_url}/{self.health_endpoint.lstrip('/')}",
                        )
                    else:
                        response = await client.post(
                            self.ledger_url, json={"ping": "true"}, headers=headers
                        )
                        span.set_attribute("http.method", "POST")
                        span.set_attribute("http.url", self.ledger_url)
                    response.raise_for_status()
                    self._logger.info("health_check_success")
                    span.set_attribute("status", "success")
                    return True
                except httpx.HTTPError as e:
                    self._logger.error("health_check_failed", error=str(e))
                    if AUDIT_ERRORS:
                        AUDIT_ERRORS.labels(code=type(e).__name__).inc()
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR)
                    return False
                except Exception as e:
                    self._logger.critical(
                        "health_check_unexpected", exc_info=True, error=str(e)
                    )
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR)
                    raise ReasonerError(
                        f"Health check failed: {str(e)}",
                        ReasonerErrorCode.SERVICE_UNAVAILABLE,
                        e,
                    )
        else:
            try:
                client = await self._get_client()
                if self.health_endpoint:
                    response = await client.get(
                        f"{self.ledger_url}/{self.health_endpoint.lstrip('/')}",
                        headers=headers,
                    )
                else:
                    response = await client.post(
                        self.ledger_url, json={"ping": "true"}, headers=headers
                    )
                response.raise_for_status()
                self._logger.info("health_check_success")
                return True
            except httpx.HTTPError as e:
                self._logger.error("health_check_failed", error=str(e))
                if AUDIT_ERRORS:
                    AUDIT_ERRORS.labels(code=type(e).__name__).inc()
                return False
            except Exception as e:
                self._logger.critical(
                    "health_check_unexpected", exc_info=True, error=str(e)
                )
                raise ReasonerError(
                    f"Health check failed: {str(e)}",
                    ReasonerErrorCode.SERVICE_UNAVAILABLE,
                    e,
                )

    async def rotate_key(self, new_key: str):
        """
        Rotates the API key securely.

        Args:
            new_key: The new API key string.
        Raises:
            ValueError: If new_key is empty or invalid.
        """
        if not new_key or not isinstance(new_key, str):
            raise ValueError("New key must be a non-empty string")
        self.api_key = SensitiveValue(new_key)
        if self._client:
            await self._client.aclose()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                verify=True,
            )
        self._logger.info("key_rotated")

    async def close(self):
        """
        Closes the underlying HTTP client session.

        Raises:
            ReasonerError: If closing the client fails unexpectedly.
        """
        if self._client and not self._client.is_closed:
            try:
                await self._client.aclose()
                self._logger.info("http_client_closed")
            except Exception as e:
                self._logger.critical("close_failed", exc_info=True, error=str(e))
                raise ReasonerError(
                    f"Failed to close client: {str(e)}",
                    ReasonerErrorCode.UNEXPECTED_ERROR,
                    e,
                )


if __name__ == "__main__":

    async def test_client():
        """
        Tests AuditLedgerClient functionality with mock setup.
        Requires a running ledger or mocked httpx client for full testing.
        """
        print("Starting standalone test...")
        client = AuditLedgerClient(
            ledger_url=os.getenv(
                "AUDIT_LEDGER_URL_TEST", "https://mock-ledger:8080/audit"
            ),
            api_key=os.getenv("AUDIT_API_KEY_TEST", "dummy_key"),
            health_endpoint=os.getenv("AUDIT_HEALTH_ENDPOINT_TEST", "/health"),
        )
        try:
            # Test health check
            print("\n--- Testing Health Check ---")
            is_healthy = await client.health_check()
            print(f"Health check: {is_healthy}")
            _logger.info("test_health_check", result=is_healthy)

            # Test single event
            print("\n--- Testing Single Event Logging ---")
            success = await client.log_event(
                "test_event", {"detail": "Test event", "email": "user@example.com"}
            )
            print(f"Single event logged: {success}")
            _logger.info("test_log_event", success=success)

            # Test batch events
            print("\n--- Testing Batch Event Logging ---")
            batch = [
                {"event_type": "batch_event_1", "details": {"data": "Batch 1"}},
                {
                    "event_type": "batch_event_2",
                    "details": {"data": "Batch 2", "email": "user2@example.com"},
                },
            ]
            batch_success = await client.log_batch_events(batch)
            print(f"Batch events logged: {batch_success}")
            _logger.info("test_batch_events", success=batch_success, count=len(batch))

            # Test key rotation
            print("\n--- Testing API Key Rotation ---")
            await client.rotate_key("new_dummy_key")
            print("Key rotated successfully")
            _logger.info("test_key_rotation")

        except Exception as e:
            _logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\nTest failed with an exception: {e}")
        finally:
            print("\n--- Closing Client ---")
            await client.close()
            print("Client closed. Test complete.")

    asyncio.run(test_client())
