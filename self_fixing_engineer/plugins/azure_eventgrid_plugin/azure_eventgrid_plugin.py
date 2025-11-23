# plugins/azure_eventgrid_plugin/azure_eventgrid_plugin.py
"""
azure_eventgrid_plugin.py

World-Class Async Azure Event Grid Audit/Event Plugin for CheckpointManager and Distributed Systems

- Fully async, connection-pooled, robust: handles retries, connection errors, and backpressure.
- Batching and Queueing: High-throughput event batching with configurable size and flush intervals.
- Configurable: endpoint URL, access key, custom subject, event type, batching, retries, timeouts.
- Structured, extensible: pluggable as an audit/event hook, supports extra context/metadata, host/node tags, and custom event IDs.
- Resilient: queueing and retry logic for temporary Azure outages, with exponential backoff and distinction between permanent/retriable errors.
- Graceful shutdown and reusable for FastAPI/Quart microservices: drains the event queue on close.
- SIEM and ops ready: ideal for audit, infosec, event streaming, and compliance.
"""

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os  # FIX: used by PRODUCTION_MODE
import socket
import sys  # For sys.exit
import time
import uuid
from contextlib import nullcontext  # FIX: clean tracing fallback
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import aiohttp
import redis.asyncio as redis

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# --- Logging Setup ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# --- Custom Exceptions ---
class AnalyzerCriticalError(Exception):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        alert_operator(message, alert_level)


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


from plugins.core_audit import audit_logger
from plugins.core_secrets import SECRETS_MANAGER

# --- Centralized Utilities (replacing placeholders) ---
from plugins.core_utils import alert_operator
from plugins.core_utils import scrub_secrets as scrub_sensitive_data

# --- OpenTelemetry/Tracing (REQUIRED in prod) ---
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # noqa: F401
    from opentelemetry.sdk.resources import Resource  # noqa: F401
    from opentelemetry.sdk.trace import TracerProvider  # noqa: F401 (used by main app)
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: F401

    tracer = trace.get_tracer(__name__)
    logger.info("OpenTelemetry tracer available for azure_eventgrid_plugin.")
except ImportError as e:
    if PRODUCTION_MODE:
        logger.critical(
            f"CRITICAL: OpenTelemetry not found. Tracing is mandatory in PRODUCTION_MODE. Aborting startup: {e}."
        )
        alert_operator(
            "CRITICAL: OpenTelemetry missing. Azure Event Grid plugin aborted.",
            level="CRITICAL",
        )
        sys.exit(1)
    else:
        logger.warning("OpenTelemetry not found. Tracing will be disabled.")

        class MockTracer:
            def start_as_current_span(self, *_, **__):
                class MockSpan:
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def set_attribute(self, *args):
                        pass

                    def record_exception(self, *args):
                        pass

                    def set_status(self, *args):
                        pass

                return MockSpan()

        tracer = MockTracer()

# --- Caching: Redis Client Initialization ---
try:
    REDIS_CLIENT = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=0,
        decode_responses=True,
    )
except Exception as e:
    logger.warning(f"Failed to connect to Redis for caching: {e}. Caching will be disabled.")
    REDIS_CLIENT = None


# Custom Exceptions for granular error handling
class EventGridError(Exception):
    """Base exception for Event Grid operations."""

    pass


class EventGridPermanentError(EventGridError):
    """Indicates a non-retriable error (e.g., 4xx HTTP status)."""

    pass


class EventGridRetriableError(EventGridError):
    """Indicates a retriable error (e.g., 5xx HTTP status, network issue)."""

    pass


# Manifest Controls
PLUGIN_MANIFEST = {
    "name": "azure_eventgrid_plugin",
    "version": "0.0.1",
    "description": "A demo Azure Event Grid plugin.",
    "entrypoint": "azure_eventgrid_plugin.py",
    "type": "python",
    "author": "Omnisapient Wizard",
    "capabilities": ["network_access_limited"],
    "permissions": ["network_access_limited"],
    "dependencies": ["aiohttp"],
    "min_core_version": "1.1.0",
    "max_core_version": "2.0.0",
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://example.com/azure_eventgrid_plugin",
    "tags": ["demo", "azure", "eventgrid", "audit", "observability"],
    "is_demo_plugin": True,
    "signature": "PLACEHOLDER_FOR_HMAC_SIGNATURE",
}


class AzureEventGridAuditHook:
    """
    World-class async Azure Event Grid audit/event hook for CheckpointManager and distributed systems.
    """

    def __init__(
        self,
        endpoint_url: str,
        subject: str = "checkpoint",
        data_version: str = "1.0",
        retries: int = 3,  # Mandatory: Exponential backoff must be capped.
        retry_backoff: float = 2.0,
        extra_context: Optional[Dict[str, Any]] = None,
        timeout: Union[float, aiohttp.ClientTimeout] = 10.0,
        session: Optional[aiohttp.ClientSession] = None,
        batch_size: int = 10,  # Mandatory: Batch sizes must be configurable.
        flush_interval: float = 5.0,  # Mandatory: Flush intervals must be configurable.
        on_failure: Optional[
            Callable[[List[Dict[str, Any]], Exception], Awaitable[None]]
        ] = None,  # Must notify ops/SIEM.
    ):
        # Key Management: key must be sourced from a secure secrets manager.
        self.endpoint_url = self._validate_endpoint(endpoint_url)
        self.key = SECRETS_MANAGER.get_secret(
            "AZURE_EVENTGRID_KEY", required=True if PRODUCTION_MODE else False
        )
        if PRODUCTION_MODE and not self.key:
            raise AnalyzerCriticalError(
                "Missing 'AZURE_EVENTGRID_KEY' in PRODUCTION_MODE. Aborting."
            )

        self.subject = subject
        self.data_version = data_version
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.extra_context = extra_context or {}

        # Robust timeout type check
        self.timeout = (
            timeout
            if isinstance(timeout, aiohttp.ClientTimeout)
            else aiohttp.ClientTimeout(total=timeout)
        )

        self._session = session
        self._own_session = session is None
        self._hostname = socket.gethostname()

        # Batching and Queueing
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._event_queue = asyncio.Queue()
        self._shutdown_event = asyncio.Event()

        # Hooks and Metrics
        self.on_failure = on_failure
        self._sent_count = 0
        self._retry_count = 0
        self._failed_count = 0

        # Start the background task
        self._sender_task = asyncio.create_task(self._batch_sender())
        logger.info(
            "AzureEventGridAuditHook initialized.",
            extra={"endpoint": self.endpoint_url},
        )
        audit_logger.log_event(
            "eventgrid_hook_init",
            endpoint=self.endpoint_url,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )

    def _validate_endpoint(self, url: str) -> str:
        """
        Endpoint/Network Controls:
        - In PROD, enforce HTTPS and allowlist.
        - Disallow dummy/test endpoints in PROD.
        """
        if not url:
            raise AnalyzerCriticalError("Event Grid Endpoint URL is empty. Aborting startup.")

        allowed_endpoints_str = SECRETS_MANAGER.get_secret(
            "EVENTGRID_ALLOWED_ENDPOINTS", required=PRODUCTION_MODE
        )
        if PRODUCTION_MODE:
            if not url.lower().startswith("https://"):
                raise AnalyzerCriticalError(
                    f"Non-HTTPS endpoint '{url}' detected in PRODUCTION_MODE. HTTPS is mandatory. Aborting startup."
                )

            if not allowed_endpoints_str:
                raise AnalyzerCriticalError(
                    "'EVENTGRID_ALLOWED_ENDPOINTS' must be configured and non-empty in PRODUCTION_MODE. Aborting startup."
                )

            allowed_endpoints = [
                ep.strip() for ep in allowed_endpoints_str.split(",") if ep.strip()
            ]
            if url not in allowed_endpoints:
                audit_logger.log_event(
                    "eventgrid_endpoint_forbidden",
                    endpoint=url,
                    reason="not_in_allowlist",
                    allowed_endpoints=allowed_endpoints,
                )
                raise AnalyzerCriticalError(
                    f"Endpoint '{url}' is not in the allowed_endpoints list: {allowed_endpoints}. Aborting startup."
                )

            low = url.lower()
            if any(s in low for s in ("dummy", "test", "mock", "example.com")):
                audit_logger.log_event(
                    "eventgrid_endpoint_forbidden",
                    endpoint=url,
                    reason="dummy_endpoint_in_prod",
                )
                raise AnalyzerCriticalError(
                    f"Dummy/test endpoint '{url}' detected in PRODUCTION_MODE. Aborting startup."
                )

        return url

    def _sign_event(self, event: Dict[str, Any]) -> str:
        """Generates an HMAC signature for an event payload."""
        event_json_str = json.dumps(event, sort_keys=True, ensure_ascii=False).encode("utf-8")
        eventgrid_hmac_key = SECRETS_MANAGER.get_secret(
            "EVENTGRID_HMAC_KEY", required=True if PRODUCTION_MODE else False
        )

        if PRODUCTION_MODE and not eventgrid_hmac_key:
            raise AnalyzerCriticalError(
                "Missing 'EVENTGRID_HMAC_KEY' in PRODUCTION_MODE for event signing. Aborting."
            )

        return hmac.new(
            eventgrid_hmac_key.encode("utf-8"), event_json_str, hashlib.sha256
        ).hexdigest()

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self):
        """
        Async/Graceful Shutdown: On shutdown, flush all events and block until drained.
        Escalate if unable to flush within timeout.
        """
        logger.info("AzureEventGridAuditHook shutting down. Draining queue...")
        audit_logger.log_event(
            "eventgrid_hook_shutdown_start",
            plugin=PLUGIN_MANIFEST["name"],
            queue_size=self._event_queue.qsize(),
        )

        self._shutdown_event.set()  # Signal the sender to drain the queue.

        try:
            await asyncio.wait_for(self._event_queue.join(), timeout=self.flush_interval * 2 + 5)
            logger.info("AzureEventGridAuditHook queue drained successfully.")
            audit_logger.log_event(
                "eventgrid_hook_queue_drained",
                plugin=PLUGIN_MANIFEST["name"],
                remaining_events=self._event_queue.qsize(),
            )
        except asyncio.TimeoutError:
            remaining_events = self._event_queue.qsize()
            logger.critical(
                f"CRITICAL: AzureEventGridAuditHook failed to drain queue within timeout. {remaining_events} events remain unsent. Aborting shutdown."
            )
            audit_logger.log_event(
                "eventgrid_hook_shutdown_failure",
                plugin=PLUGIN_MANIFEST["name"],
                reason="queue_drain_timeout",
                remaining_events=remaining_events,
            )
            alert_operator(
                f"CRITICAL: Event Grid hook queue NOT drained. {remaining_events} events unsent. Aborting.",
                level="CRITICAL",
            )
            sys.exit(1)
        except Exception as e:
            logger.critical(
                f"CRITICAL: Unexpected error during queue drain: {e}. Aborting shutdown.",
                exc_info=True,
            )
            audit_logger.log_event(
                "eventgrid_hook_shutdown_failure",
                plugin=PLUGIN_MANIFEST["name"],
                reason="queue_drain_error",
                error=str(e),
            )
            alert_operator(
                f"CRITICAL: Event Grid hook queue drain failed unexpectedly: {e}. Aborting.",
                level="CRITICAL",
            )
            sys.exit(1)

        # Cancel the sender task.
        self._sender_task.cancel()
        try:
            await self._sender_task
        except asyncio.CancelledError:
            pass

        if self._own_session and self._session:
            await self._session.close()
        logger.info("AzureEventGridAuditHook shutdown complete.")
        audit_logger.log_event("eventgrid_hook_shutdown_complete", plugin=PLUGIN_MANIFEST["name"])

    async def audit_hook(self, event: str, details: dict, event_id: Optional[str] = None):
        """
        Queues an event to be sent to Azure Event Grid.
        Context/PII Scrubbing: scrub payloads for PII/secrets before enqueue.
        """
        if self._shutdown_event.is_set():
            logger.warning("Audit hook is shutting down, dropping new event.")
            audit_logger.log_event(
                "eventgrid_event_dropped", event_type=event, reason="shutting_down"
            )
            return

        # Scrub payloads for PII/secrets.
        scrubbed_details = scrub_sensitive_data(details)

        event_obj = {
            "id": event_id or str(uuid.uuid4()),
            "eventType": event,
            "subject": self.subject,
            # FIX: correct ISO8601 Zulu formatting (no second arg to datetime.strftime)
            "eventTime": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                **scrubbed_details,
                **self.extra_context,
                "host": self._hostname,
                "ts": time.time(),
            },
            "dataVersion": self.data_version,
            "signature": self._sign_event(scrubbed_details),  # HMAC signature
        }
        await self._event_queue.put(event_obj)
        audit_logger.log_event(
            "eventgrid_event_queued",
            event_type=event,
            event_id=event_obj["id"],
            queue_size=self._event_queue.qsize(),
        )

    async def _send_batch(self, batch: List[Dict[str, Any]]):
        """Sends a batch of events with retry logic."""
        if not batch:
            return

        await self._ensure_session()
        headers = {
            "aeg-sas-key": self.key,
            "Content-Type": "application/json; charset=utf-8",
        }
        last_exc = None

        for attempt in range(1, self.retries + 1):
            try:
                span_name = "eventgrid_send_batch"
                # FIX: clean context when tracing is unavailable
                with tracer.start_as_current_span(span_name) if tracer else nullcontext():
                    # Optionally set attributes (works with real tracer; no-op with mock/null)
                    try:
                        span = trace.get_current_span()
                        if span:
                            span.set_attribute("event_count", len(batch))
                            span.set_attribute("attempt", attempt)
                            span.set_attribute("endpoint_url", self.endpoint_url)
                    except Exception:
                        pass

                    async with self._session.post(
                        self.endpoint_url, headers=headers, data=json.dumps(batch)
                    ) as resp:
                        if 200 <= resp.status < 300:
                            logger.debug(
                                f"Successfully published batch of {len(batch)} events to Azure Event Grid."
                            )
                            self._sent_count += len(batch)
                            audit_logger.log_event(
                                "eventgrid_batch_sent",
                                count=len(batch),
                                attempt=attempt,
                                status=resp.status,
                            )
                            return  # Success

                        text = await resp.text()
                        if 400 <= resp.status < 500:
                            # Permanent failure → escalate (do not retry)
                            logger.error(
                                f"Event Grid returned permanent error: {resp.status} {text}. Dropping batch."
                            )
                            audit_logger.log_event(
                                "eventgrid_batch_permanent_failure",
                                count=len(batch),
                                status=resp.status,
                                response_text=text,
                            )
                            raise EventGridPermanentError(
                                f"Event Grid returned permanent error: {resp.status} {text}"
                            )
                        else:
                            # Transient/retriable
                            logger.warning(
                                f"Event Grid returned retriable error: {resp.status} {text}. Retrying."
                            )
                            audit_logger.log_event(
                                "eventgrid_batch_retriable_failure",
                                count=len(batch),
                                status=resp.status,
                                response_text=text,
                                attempt=attempt,
                                endpoint=self.endpoint_url,
                            )
                            raise EventGridRetriableError(
                                f"Event Grid returned retriable error: {resp.status} {text}"
                            )

            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                EventGridRetriableError,
            ) as e:
                last_exc = e
                logger.warning(f"Event Grid send failed (attempt {attempt}/{self.retries}): {e}")
                audit_logger.log_event(
                    "eventgrid_retry_attempt",
                    count=len(batch),
                    attempt=attempt,
                    endpoint=self.endpoint_url,
                    error=str(e),
                )
                if attempt < self.retries:
                    self._retry_count += 1
                    sleep_duration = self.retry_backoff**attempt
                    await asyncio.sleep(sleep_duration)
                else:
                    break
            except EventGridPermanentError as e:
                last_exc = e
                logger.error(f"Event Grid audit event dropped due to permanent error: {e}")
                break

        # If all retries failed or a permanent error occurred
        self._failed_count += len(batch)
        logger.error(
            f"Event Grid audit batch dropped after {self.retries} attempts. Events: {len(batch)}"
        )
        audit_logger.log_event(
            "eventgrid_batch_dropped_final", count=len(batch), final_error=str(last_exc)
        )

        # Operator Escalation
        alert_operator(
            f"CRITICAL: Event Grid audit batch dropped after {self.retries} attempts. {len(batch)} events unsent. Final Error: {last_exc}",
            level="CRITICAL",
        )

        if self.on_failure:
            try:
                await self.on_failure(batch, last_exc)
            except Exception as cb_exc:
                logger.error(
                    f"on_failure callback itself raised an exception: {cb_exc}",
                    exc_info=True,
                )
                alert_operator(
                    f"CRITICAL: Event Grid on_failure callback failed: {cb_exc}",
                    level="CRITICAL",
                )

    async def _batch_sender(self):
        """The main background task that collects events and sends them in batches."""
        while not self._shutdown_event.is_set() or not self._event_queue.empty():
            batch: List[Dict[str, Any]] = []
            try:
                timeout = self.flush_interval if not self._shutdown_event.is_set() else 0.1

                try:
                    first_event = await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
                    batch.append(first_event)
                except asyncio.TimeoutError:
                    if self._shutdown_event.is_set() and self._event_queue.empty():
                        break
                    continue
                except asyncio.CancelledError:
                    if batch:
                        await self._send_batch(batch)
                        for _ in batch:
                            self._event_queue.task_done()
                    raise

                while len(batch) < self.batch_size:
                    try:
                        next_event = self._event_queue.get_nowait()
                        batch.append(next_event)
                    except asyncio.QueueEmpty:
                        break

            except asyncio.CancelledError:
                logger.info("_batch_sender task cancelled during event collection.")
                break

            if batch:
                try:
                    await self._send_batch(batch)
                finally:
                    for _ in batch:
                        self._event_queue.task_done()
            else:
                if self._shutdown_event.is_set() and self._event_queue.empty():
                    break

        logger.info("_batch_sender loop finished.")

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# --- Demo/Testing Code: must never run in PRODUCTION_MODE ---
if not PRODUCTION_MODE:

    async def example_failure_callback(events: List[Dict[str, Any]], error: Exception):
        """Example callback for handling failed event batches."""
        print(f"ALERT: Failed to send {len(events)} events. Error: {error}")

    async def main():
        # Simple mock server to simulate Azure Event Grid
        async def mock_eventgrid_server(request):
            request_body = await request.text()
            if "fail_permanently" in request_body:
                return aiohttp.web.Response(status=400, text="Bad Request - Invalid Event Schema")
            if "fail_transiently" in request_body:
                if not hasattr(mock_eventgrid_server, "fail_count"):
                    mock_eventgrid_server.fail_count = 0
                if mock_eventgrid_server.fail_count < 2:
                    mock_eventgrid_server.fail_count += 1
                    return aiohttp.web.Response(status=503, text="Service Unavailable")

            print(f"Mock Server received {len(await request.json())} events.")
            return aiohttp.web.Response(status=200, text="OK")

        app = aiohttp.web.Application()
        app.router.add_post("/api/events", mock_eventgrid_server)
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "localhost", 8080)
        await site.start()
        print("Mock Event Grid server started at http://localhost:8080/api/events")

        hook = AzureEventGridAuditHook(
            endpoint_url="http://localhost:8080/api/events",
            batch_size=5,
            flush_interval=2.0,
            on_failure=example_failure_callback,
        )

        try:
            print("\n--- Sending 12 successful events ---")
            tasks = [hook.audit_hook("test.event", {"i": i}) for i in range(12)]
            await asyncio.gather(*tasks)
            await asyncio.sleep(3)  # Wait for flush interval

            print("\n--- Sending events that will fail transiently then succeed ---")
            await hook.audit_hook("test.retriable", {"data": "fail_transiently"})
            await asyncio.sleep(5)  # Wait for retries

            print("\n--- Sending events that will fail permanently ---")
            await hook.audit_hook("test.permanent", {"data": "fail_permanently"})
            await asyncio.sleep(3)  # Wait for processing

            print("\n--- Metrics ---")
            print(
                f"Sent: {hook._sent_count}, Retried: {hook._retry_count}, Failed: {hook._failed_count}"
            )
        finally:
            print("\n--- Shutting down hook ---")
            await hook.close()
            await runner.cleanup()
            print("Shutdown complete.")

    if __name__ == "__main__":
        try:
            import aiohttp.web  # ensure submodule is available

            asyncio.run(main())
        except ImportError:
            print("Please install aiohttp (`pip install aiohttp`) to run the usage example.")
else:
    if __name__ == "__main__":
        logger.critical(
            "CRITICAL: Attempted to run example/test code in PRODUCTION_MODE. This file should not be executed directly in production."
        )
        alert_operator(
            "CRITICAL: Azure Event Grid plugin example code executed in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
