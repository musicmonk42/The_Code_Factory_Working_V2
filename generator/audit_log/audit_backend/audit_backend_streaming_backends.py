# audit_backends/audit_backend_streaming_backends.py
import asyncio
import datetime
import json
import uuid
import os
import zlib
import logging
import time
import ssl # Import ssl for http backend

from typing import Any, Dict, List, Optional, Set, AsyncIterator

import aiohttp
import aiokafka

# Import utilities from the new utils file
from .audit_backend_streaming_utils import (
    SensitiveDataFilter, SimpleCircuitBreaker, PersistentRetryQueue, FileBackedRetryQueue
)

# Import core backend components
from .audit_backend_core import (
    LogBackend, SCHEMA_VERSION, BACKEND_ERRORS, logger, send_alert, retry_operation,
    compute_hash, ENCRYPTER, COMPRESSION_ALGO, COMPRESSION_LEVEL, MigrationError,
    BACKEND_RETRY_ATTEMPTS, BACKEND_NETWORK_ERRORS, BACKEND_APPEND_LATENCY, BACKEND_QUERIES,
    BACKEND_WRITES, BACKEND_READS, BACKEND_BATCH_FLUSHES, BACKEND_THROUGHPUT_BYTES,
    BACKEND_HEALTH, BACKEND_TAMPER_DETECTION_FAILURES,
    _STATUS_OK, _STATUS_ERROR, BackendNotFoundError, CryptoInitializationError
)

# Apply sensitive data filter to this module's logger
logger.addFilter(SensitiveDataFilter())


# --- HTTP Backend ---
class HTTPBackend(LogBackend):
    """
    HTTP backend with batch uploads, enhanced error handling, and session management.
    Supports secure communication implicitly via aiohttp.
    """
    DEFAULT_MAX_HTTP_PAYLOAD_BYTES = 1 * 1024 * 1024 # 1 MB - Typical default max body size

    # Metrics specific to HTTP Backend
    HTTP_REQUEST_DURATION = Histogram("audit_backend_http_request_duration_seconds", "HTTP Request duration", ["backend", "operation", "status"])
    HTTP_REQUEST_RATE = Counter("audit_backend_http_request_total", "Total HTTP requests", ["backend", "operation", "status"])
    HTTP_QUEUE_SIZE = Gauge("audit_backend_http_queue_size", "Current size of internal HTTP retry queue", ["backend"])


    def _validate_params(self):
        if "endpoint" not in self.params:
            raise ValueError("endpoint parameter is required")
        if "query_endpoint" not in self.params:
            self.params["query_endpoint"] = self.params["endpoint"]
        
        self.endpoint = self.params["endpoint"]
        self.query_endpoint = self.params["query_endpoint"]
        self.headers = self.params.get("headers", {"Content-Type": "application/json"})
        self.timeout = self.params.get("timeout", 10)
        self.verify_ssl = self.params.get("verify_ssl", True)
        self.max_payload_bytes = self.params.get("max_http_payload_bytes", self.DEFAULT_MAX_HTTP_PAYLOAD_BYTES)
        
        # Circuit Breaker Configuration
        self._circuit_breaker = SimpleCircuitBreaker(
            backend_name=self.__class__.__name__,
            failure_threshold=self.params.get("cb_failure_threshold", 5),
            recovery_timeout=self.params.get("cb_recovery_timeout", 60)
        )
        # Persistent Retry Queue (DLQ) - Configurable via params
        # Default to FileBackedRetryQueue for persistence
        dlq_class = self.params.get("dlq_class", FileBackedRetryQueue)
        self.dlq_persistence_file = self.params.get("dlq_persistence_file", f"http_backend_dlq_{uuid.uuid4()}.jsonl")
        self._dlq = dlq_class(
            backend_name=self.__class__.__name__,
            persistence_file=self.dlq_persistence_file,
            circuit_breaker=self._circuit_breaker, # Pass circuit breaker to DLQ
            max_queue_size=self.params.get("dlq_max_size", 10000),
            max_reprocess_attempts=self.params.get("dlq_max_reprocess_attempts", 5)
        )
        
        # Async queue for internal retries / backpressure handling before DLQ
        self._internal_retry_queue = asyncio.Queue(maxsize=self.params.get("internal_retry_queue_max_size", 1000))
        # Set the gauge metric
        self.HTTP_QUEUE_SIZE.labels(backend=self.__class__.__name__).set(0)


    def __init__(self, params: Dict[str, Any]):
        self._background_tasks: Set[asyncio.Task] = set() # Init task set before super()
        super().__init__(params)
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Store all background tasks to ensure they are properly managed during shutdown.
        # super().__init__ also adds tasks to self._background_tasks (migrate, flush, health).
        self._init_task = asyncio.create_task(self._init_session())
        self._background_tasks.add(self._init_task)
        self._init_task.add_done_callback(self._background_tasks.discard)

        self._internal_retry_processor_task = asyncio.create_task(
            self._process_internal_retry_queue()
        )
        self._background_tasks.add(self._internal_retry_processor_task)
        self._internal_retry_processor_task.add_done_callback(self._background_tasks.discard)

        # Start DLQ processor
        self._dlq_processor_task = asyncio.create_task(self._dlq.start_processor(self._reprocess_failed_batch))
        self._background_tasks.add(self._dlq_processor_task)
        self._dlq_processor_task.add_done_callback(self._background_tasks.discard)


    async def _init_session(self):
        """Initializes HTTP session."""
        try:
            # Create SSL context based on verify_ssl
            # `True` (default): verify cert
            # `False`: do not verify cert (INSECURE)
            # `str`: path to CA bundle
            ssl_context = None
            if isinstance(self.verify_ssl, str):
                ssl_context = ssl.create_default_context(cafile=self.verify_ssl)
            elif self.verify_ssl is False:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                logger.warning(f"HTTPBackend: verify_ssl=False. SSL verification is DISABLED. This is insecure.",
                               extra={"backend_type": self.__class__.__name__, "operation": "init_session_insecure"})

            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(ssl=ssl_context if ssl_context is not None else self.verify_ssl)
            )
            logger.info(f"HTTPBackend initialized for endpoint {self.endpoint}",
                        extra={"backend_type": self.__class__.__name__, "operation": "init_session_success"})
        except Exception as e:
            logger.critical(f"HTTPBackend session initialization failed: {e}", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "init_session_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="InitError").inc()
            asyncio.create_task(send_alert(f"HTTPBackend session initialization failed: {e}", severity="critical"))
            raise


    async def _process_internal_retry_queue(self):
        """Processes items from the internal retry queue."""
        while True:
            try:
                item = await self._internal_retry_queue.get()
                self.HTTP_QUEUE_SIZE.labels(backend=self.__class__.__name__).set(self._internal_retry_queue.qsize())
                logger.info(f"HTTPBackend: Reprocessing batch from internal retry queue. Queue size: {self._internal_retry_queue.qsize()}",
                            extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_reprocess"})
                
                # We call _send_batch_chunks directly, which has its *own* retry logic.
                await self._send_batch_chunks(item, is_retry=True)
                
                logger.info(f"HTTPBackend: Successfully reprocessed batch from internal retry queue.",
                            extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_success"})
            except asyncio.CancelledError:
                logger.debug("HTTPBackend: Internal retry processor task cancelled.",
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cancelled"})
                break
            except Exception as e:
                logger.error(f"HTTPBackend: Failed to reprocess batch from internal retry queue after all retries: {e}. Enqueuing to DLQ.", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_fail"})
                await self._dlq.enqueue(item, failure_reason=str(e))


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op for HTTPBackend as the _atomic_context handles batch sending directly from the prepared_entries list.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """
        Queries HTTP endpoint with structured parameters.
        Note: Assumes the remote endpoint supports these query parameters and returns JSON.
        """
        if self.session is None:
            await retry_operation(self._init_session, backend_name=self.__class__.__name__, op_name="query_init_session")

        # Circuit breaker check for query operations
        if not self._circuit_breaker.allow_request():
            logger.warning("HTTPBackend: Circuit breaker is OPEN for queries. Query not executed.",
                           extra={"backend_type": self.__class__.__name__, "operation": "circuit_breaker_open_query"})
            raise ConnectionRefusedError("Circuit breaker is OPEN. Endpoint is deemed unhealthy for queries.")


        params = {"limit": str(limit)}
        # Input validation/sanitization should ideally happen at the API boundary,
        # but for untrusted inputs, ensure parameters are safely encoded.
        # Sensitive data in `filters` should be redacted from logs (handled by SensitiveDataFilter).
        sanitized_filters = {k: str(v) for k, v in filters.items()}
        logger.debug(f"HTTPBackend: Querying endpoint '{self.query_endpoint}' with filters: {sanitized_filters}",
                     extra={"backend_type": self.__class__.__name__, "operation": "query_single", "filters": sanitized_filters})

        start_req_time = time.perf_counter()
        request_status = "unknown"
        try:
            # We wrap the *individual* session.get() call in retry_operation
            async def http_get_op():
                async with self.session.get(self.query_endpoint, params=sanitized_filters, timeout=self.timeout) as response:
                    if response.status >= 400:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info, history=response.history, status=response.status,
                            message=f"Query failed with status {response.status}: {await response.text()}"
                        )
                    return await response.json() # Return JSON directly on success

            result_json = await retry_operation(
                http_get_op,
                backend_name=self.__class__.__name__, op_name="http_get_query"
            )
            
            request_status = "success"
            self._circuit_breaker.record_success()
            return result_json
                
        except aiohttp.ClientResponseError as e:
            request_status = f"error_{e.status}"
            logger.error(f"HTTPBackend query failed with status {e.status}: {e.message[:500]}...",
                         extra={"backend_type": self.__class__.__name__, "operation": "query_single_fail", "status_code": e.status})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type=f"HTTPError_{e.status}").inc()
            self._circuit_breaker.record_failure(e)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            request_status = "network_error"
            logger.error(f"HTTPBackend query failed: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "query_single_client_error"})
            BACKEND_NETWORK_ERRORS.labels(backend=self.__class__.__name__, operation="http_get_query").inc()
            self._circuit_breaker.record_failure(e)
            asyncio.create_task(send_alert(f"HTTPBackend query failed: {e}", severity="high"))
            raise
        except (json.JSONDecodeError, aiohttp.ContentTypeError) as parse_e:
            request_status = "parse_error"
            logger.error(f"HTTPBackend query received malformed JSON response: {parse_e}.",
                         extra={"backend_type": self.__class__.__name__, "operation": "query_single_parse"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MalformedJSONResponse").inc()
            self._circuit_breaker.record_failure(parse_e)
            raise
        except Exception as e:
            request_status = "internal_error"
            logger.error(f"HTTPBackend query failed unexpectedly: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "query_single_unexpected_error"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="QueryError").inc()
            self._circuit_breaker.record_failure(e)
            asyncio.create_task(send_alert(f"HTTPBackend query failed: {e}", severity="high"))
            raise
        finally:
            duration = time.perf_counter() - start_req_time
            self.HTTP_REQUEST_DURATION.labels(backend=self.__class__.__name__, operation="get_query", status=request_status).observe(duration)
            self.HTTP_REQUEST_RATE.labels(backend=self.__class__.__name__, operation="get_query", status=request_status).inc()


    async def _migrate_schema(self) -> None:
        """
        HTTP schema migration is dependent on the remote API's capabilities.
        """
        logger.info("HTTPBackend schema migration: This requires coordinating with the remote API provider for versioning or data transformation.",
                    extra={"backend_type": self.__class__.__name__, "operation": "migrate_schema"})
        pass


    async def _health_check(self) -> bool:
        """Checks HTTP endpoint health."""
        if self.session is None:
            try:
                await self._init_session()
            except Exception:
                logger.warning(f"HTTPBackend health check: Session could not be initialized.",
                               extra={"backend_type": self.__class__.__name__, "operation": "health_check_init_fail"})
                return False

        try:
            # We use a short timeout for the health check GET request
            async with self.session.get(self.query_endpoint, timeout=aiohttp.ClientTimeout(total=self.timeout / 2)) as response:
                if response.status < 500: # Allow 4xx (auth errors) but fail on 5xx (server errors)
                    return True
                logger.warning(f"HTTPBackend health check failed with status {response.status}.",
                               extra={"backend_type": self.__class__.__name__, "operation": "health_check_status_fail", "status_code": response.status})
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"HTTPBackend health check failed for {self.endpoint}: {e}",
                           extra={"backend_type": self.__class__.__name__, "operation": "health_check_client_error"})
            return False
        except Exception as e:
            logger.warning(f"HTTPBackend health check failed unexpectedly: {e}",
                           extra={"backend_type": self.__class__.__name__, "operation": "health_check_unexpected_error"})
            return False


    async def _get_current_schema_version(self) -> int:
        """For HTTP backend, schema version is implicitly handled by the remote API."""
        logger.info("HTTPBackend: Schema version is managed by the remote API. Assuming current schema version for local operations.",
                    extra={"backend_type": self.__class__.__name__, "operation": "get_schema_version"})
        return self.schema_version


    async def _send_batch_chunks(self, prepared_entries: List[Dict[str, Any]], is_retry: bool = False):
        """Internal method to send prepared entries in chunks, respecting payload size and retries."""
        
        # If this is not a retry, check the circuit breaker first.
        if not is_retry and not self._circuit_breaker.allow_request():
            logger.warning("HTTPBackend: Circuit breaker is OPEN. Batch not sent.",
                           extra={"backend_type": self.__class__.__name__, "operation": "circuit_breaker_open"})
            raise ConnectionRefusedError("Circuit breaker is OPEN. Endpoint is deemed unhealthy.")

        current_chunk_size = 0
        current_chunk_entries = []
        chunks_to_send = []

        for entry in prepared_entries:
            entry_json_str = json.dumps(entry, sort_keys=True)
            entry_bytes_approx = len(entry_json_str.encode('utf-8'))
            
            if current_chunk_size + entry_bytes_approx > self.max_payload_bytes and current_chunk_entries:
                chunks_to_send.append(current_chunk_entries)
                current_chunk_entries = []
                current_chunk_size = 0
            
            current_chunk_entries.append(entry)
            current_chunk_size += entry_bytes_approx
        
        if current_chunk_entries:
            chunks_to_send.append(current_chunk_entries)

        all_chunks_successful = True
        for i, chunk in enumerate(chunks_to_send):
            start_req_time = time.perf_counter()
            request_status = "unknown"
            
            async def http_post_op():
                nonlocal request_status
                # Generate a unique idempotency key for this chunk
                idempotency_key = str(uuid.uuid4())
                chunk_headers = self.headers.copy()
                chunk_headers["X-Idempotency-Key"] = idempotency_key
                
                async with self.session.post(self.endpoint, json=chunk, headers=chunk_headers, timeout=self.timeout) as response:
                    if response.status >= 400:
                        request_status = f"error_{response.status}"
                        error_detail = await response.text()
                        logger.error(f"HTTPBackend batch chunk failed with status {response.status}: {error_detail[:500]}...",
                                     extra={"backend_type": self.__class__.__name__, "operation": "send_chunk_fail",
                                            "status_code": response.status, "chunk_num": i+1, "idempotency_key": idempotency_key})
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info, history=response.history, status=response.status,
                            message=f"Batch upload failed: {error_detail}"
                        )
                    response.raise_for_status()
                request_status = "success"
                
            try:
                # Wrap the POST operation in the retry helper
                await retry_operation(
                    http_post_op,
                    backend_name=self.__class__.__name__,
                    op_name=f"http_post_chunk_{i}"
                )
                self._circuit_breaker.record_success() # Record success on this chunk
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                request_status = "network_error"
                logger.error(f"HTTPBackend batch chunk network error: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "send_chunk_network_error", "chunk_num": i+1})
                BACKEND_NETWORK_ERRORS.labels(backend=self.__class__.__name__, operation="send_chunk").inc()
                self._circuit_breaker.record_failure(e)
                all_chunks_successful = False
                raise # Re-raise to fail the whole batch
            except Exception as e:
                request_status = "internal_error"
                logger.error(f"HTTPBackend batch chunk unexpected error: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "send_chunk_unexpected_error", "chunk_num": i+1})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ChunkSendError").inc()
                self._circuit_breaker.record_failure(e)
                all_chunks_successful = False
                raise # Re-raise to fail the whole batch
            finally:
                duration = time.perf_counter() - start_req_time
                self.HTTP_REQUEST_DURATION.labels(backend=self.__class__.__name__, operation="post_chunk", status=request_status).observe(duration)
                self.HTTP_REQUEST_RATE.labels(backend=self.__class__.__name__, operation="post_chunk", status=request_status).inc()

        if not all_chunks_successful:
            raise RuntimeError("One or more chunks failed to send after retries.")


    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for HTTP batch. Attempts to send the batch via chunks.
        On persistent failure, enqueues to internal retry queue, then to DLQ.
        """
        if not prepared_entries:
            yield
            return

        if self.session is None:
            await self._init_session()

        try:
            await self._send_batch_chunks(prepared_entries, is_retry=False)
            logger.debug(f"HTTPBackend: Successfully sent {len(prepared_entries)} entries via HTTP.",
                         extra={"backend_type": self.__class__.__name__, "operation": "atomic_http_write_success"})
            yield
        except ConnectionRefusedError:
            # This is raised by _send_batch_chunks if the circuit breaker is open
            logger.warning(f"HTTPBackend: Batch not sent due to circuit breaker. Enqueuing to internal retry queue.",
                           extra={"backend_type": self.__class__.__name__, "operation": "atomic_write_cb_open"})
            try:
                await self._internal_retry_queue.put(prepared_entries)
                self.HTTP_QUEUE_SIZE.labels(backend=self.__class__.__name__).set(self._internal_retry_queue.qsize())
            except asyncio.QueueFull:
                 logger.critical(f"HTTPBackend: Internal retry queue full (CB Open). Enqueuing to DLQ.",
                                extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_queue_full_cb"})
                 await self._dlq.enqueue(prepared_entries, failure_reason="circuit_breaker_open_and_internal_queue_full")
            raise # Re-raise to signal failure up the chain
        except Exception as e:
            logger.error(f"HTTPBackend atomic batch upload failed after all retries: {e}. Attempting internal retry queue.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "atomic_http_write_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            
            try:
                await self._internal_retry_queue.put(prepared_entries)
                self.HTTP_QUEUE_SIZE.labels(backend=self.__class__.__name__).set(self._internal_retry_queue.qsize())
                logger.info(f"HTTPBackend: Enqueued batch for internal retry. Queue size: {self._internal_retry_queue.qsize()}",
                            extra={"backend_type": self.__class__.__name__, "operation": "enqueue_internal_retry"})
            except asyncio.QueueFull:
                logger.critical(f"HTTPBackend: Internal retry queue is full. Enqueuing to DLQ.",
                                extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_queue_full"})
                await self._dlq.enqueue(prepared_entries, failure_reason="internal_retry_queue_full")
            except Exception as enqueue_e:
                logger.error(f"HTTPBackend: Failed to enqueue to internal retry queue: {enqueue_e}. Enqueuing to DLQ.", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_enqueue_fail"})
                await self._dlq.enqueue(prepared_entries, failure_reason=str(enqueue_e))

            asyncio.create_task(send_alert(f"HTTPBackend atomic batch write failed. Data might be lost (DLQ/internal retry).", severity="critical"))
            raise


    async def _reprocess_failed_batch(self, batch_data: List[Dict[str, Any]]):
        """Callback for DLQ to reprocess a failed batch."""
        logger.info(f"HTTPBackend: Reprocessing failed batch of {len(batch_data)} entries from DLQ.",
                    extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_batch"})
        try:
            # Note: _send_batch_chunks has its own retry logic. If this fails, 
            # the DLQ processor will catch it and re-enqueue with increased attempt count.
            await self._send_batch_chunks(batch_data, is_retry=True)
            logger.info(f"HTTPBackend: Successfully reprocessed and sent batch from DLQ.",
                        extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_success"})
        except Exception as e:
            logger.error(f"HTTPBackend: Reprocessing failed for batch from DLQ: {e}.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_fail_persist"})
            raise # Re-raise error to let DLQ processor know it failed and must be re-queued


    async def close(self):
        """Closes the aiohttp client session and stops background tasks cleanly."""
        logger.info("HTTPBackend: Initiating graceful shutdown.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_start"})
        
        # Stop DLQ processor first to prevent new items during shutdown
        await self._dlq.stop_processor()

        if self._internal_retry_processor_task and not self._internal_retry_processor_task.done():
            self._internal_retry_processor_task.cancel()
            try:
                await self._internal_retry_processor_task
            except asyncio.CancelledError:
                logger.debug("HTTPBackend internal retry processor task was cancelled.",
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cancelled"})
            except Exception as e:
                logger.error(f"Error during HTTPBackend internal retry processor cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cleanup_error"})

        if self.session and not self.session.closed:
            logger.info("HTTPBackend: Closing aiohttp session...",
                        extra={"backend_type": self.__class__.__name__, "operation": "session_close_start"})
            await self.session.close()
            logger.info("HTTPBackend: aiohttp session closed.",
                        extra={"backend_type": self.__class__.__name__, "operation": "session_close_end"})
        
        # Cancel all other background tasks from the base class
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"HTTPBackend task was cancelled during shutdown.",
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cancelled"})
            except Exception as e:
                logger.error(f"Error during HTTPBackend task cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cleanup_error"})
        logger.info("HTTPBackend: Shutdown complete.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_end"})


# --- KafkaBackend ---
class KafkaBackend(LogBackend):
    """
    Kafka backend with transactional producers and conceptual Elasticsearch integration for querying.
    Provides robust lifecycle management, observability, and security considerations.
    """
    KAFKA_PRODUCER_BUFFER_FILL_RATIO = Gauge("audit_backend_kafka_producer_buffer_fill_ratio", "Ratio of producer buffer filled (0-1)", ["backend"])
    KAFKA_CONSUMER_LAG = Gauge("audit_backend_kafka_consumer_lag_messages", "Consumer lag in messages", ["backend", "topic", "partition"])
    KAFKA_TRANSACTION_COMMIT_DURATION = Histogram("audit_backend_kafka_transaction_commit_duration_seconds", "Kafka transaction commit duration", ["backend", "status"])
    KAFKA_PRODUCER_TRANSACTION_STATUS = Gauge("audit_backend_kafka_producer_transaction_status", "Producer transaction status (0=idle, 1=in_flight)", ["backend"])
    KAFKA_FENCED_PRODUCER_COUNTER = Counter("audit_backend_kafka_fenced_producer_total", "Count of times producer was fenced by broker", ["backend"])


    def _validate_params(self):
        if "bootstrap_servers" not in self.params or "topic" not in self.params:
            raise ValueError("bootstrap_servers and topic parameters are required")
        
        self.bootstrap_servers = self.params["bootstrap_servers"]
        self.topic = self.params["topic"]
        
        self.es_host = self.params.get("elasticsearch_host")
        self.es_index = self.params.get("elasticsearch_index")
        self.es_client: Optional[Any] = None

        self.schema_registry_url = self.params.get("schema_registry_url")
        self.security_protocol = self.params.get("security_protocol", "PLAINTEXT")
        self.sasl_mechanism = self.params.get("sasl_mechanism")
        self.sasl_username = self.params.get("sasl_username")
        self.sasl_password = self.params.get("sasl_password")
        self.ssl_cafile = self.params.get("ssl_cafile")
        self.ssl_certfile = self.params.get("ssl_certfile")
        self.ssl_keyfile = self.params.get("ssl_keyfile")

        # Circuit Breaker Configuration
        self._circuit_breaker = SimpleCircuitBreaker(
            backend_name=self.__class__.__name__,
            failure_threshold=self.params.get("cb_failure_threshold", 5),
            recovery_timeout=self.params.get("cb_recovery_timeout", 60)
        )
        # Persistent Retry Queue (DLQ) - Configurable to FileBackedRetryQueue
        dlq_class = self.params.get("dlq_class", FileBackedRetryQueue)
        self.dlq_persistence_file = self.params.get("dlq_persistence_file", f"kafka_backend_dlq_{uuid.uuid4()}.jsonl")
        self._dlq = dlq_class(
            backend_name=self.__class__.__name__,
            persistence_file=self.dlq_persistence_file,
            circuit_breaker=self._circuit_breaker, # Pass circuit breaker to DLQ
            max_queue_size=self.params.get("dlq_max_size", 10000),
            max_reprocess_attempts=self.params.get("dlq_max_reprocess_attempts", 5)
        )
        
        # Internal retry queue for backpressure/short-term failures
        self._internal_retry_queue = asyncio.Queue(maxsize=self.params.get("internal_retry_queue_max_size", 1000))
        self.KAFKA_PRODUCER_BUFFER_FILL_RATIO.labels(backend=self.__class__.__name__).set(0)


    def __init__(self, params: Dict[str, Any]):
        self._background_tasks: Set[asyncio.Task] = set() # Init task set before super()
        super().__init__(params)
        self.producer: Optional[aiokafka.AIOKafkaProducer] = None
        self._producer_init_task = asyncio.create_task(self._init_producer())
        self._background_tasks.add(self._producer_init_task)
        self._producer_init_task.add_done_callback(self._background_tasks.discard)

        self._es_init_task: Optional[asyncio.Task] = None
        if self.es_host and self.es_index:
            self._es_init_task = asyncio.create_task(self._init_elasticsearch_client())
            self._background_tasks.add(self._es_init_task)
            self._es_init_task.add_done_callback(self._background_tasks.discard)

        # Start internal retry queue processor
        self._internal_retry_processor_task = asyncio.create_task(
            self._process_internal_retry_queue()
        )
        self._background_tasks.add(self._internal_retry_processor_task)
        self._internal_retry_processor_task.add_done_callback(self._background_tasks.discard)

        # Start DLQ processor
        self._dlq_processor_task = asyncio.create_task(self._dlq.start_processor(self._reprocess_failed_batch))
        self._background_tasks.add(self._dlq_processor_task)
        self._dlq_processor_task.add_done_callback(self._background_tasks.discard)

        self.KAFKA_PRODUCER_TRANSACTION_STATUS.labels(backend=self.__class__.__name__).set(0)


    async def _init_producer(self):
        """Initializes Kafka producer with transactional support and optional security."""
        producer_configs = {
            "bootstrap_servers": self.bootstrap_servers,
            "transactional_id": f"audit_producer_{os.getpid()}_{uuid.uuid4()}",
            "acks": "all",
            "retries": RETRY_MAX_ATTEMPTS,
            "retry_backoff_ms": int(RETRY_BACKOFF_FACTOR * 1000),
            "max_block_ms": self.params.get("producer_max_block_ms", 60 * 1000),
            "buffer_memory": self.params.get("producer_buffer_memory", 32 * 1024 * 1024),
            "linger_ms": self.params.get("producer_linger_ms", 5)
        }
        
        if self.security_protocol != "PLAINTEXT":
            producer_configs["security_protocol"] = self.security_protocol
            if self.security_protocol.startswith("SASL"):
                if not self.sasl_mechanism or not self.sasl_username or not self.sasl_password:
                    raise ValueError("SASL parameters (mechanism, username, password) are required for SASL security protocol.")
                producer_configs["sasl_mechanism"] = self.sasl_mechanism
                producer_configs["sasl_plain_username"] = self.sasl_username
                producer_configs["sasl_plain_password"] = self.sasl_password
            if "SSL" in self.security_protocol:
                if self.ssl_cafile:
                    producer_configs["ssl_cafile"] = self.ssl_cafile
                if self.ssl_certfile and self.ssl_keyfile:
                    producer_configs["ssl_certfile"] = self.ssl_certfile
                    producer_configs["ssl_keyfile"] = self.ssl_keyfile
            
            logger.info(f"KafkaBackend: Initializing producer with security protocol: {self.security_protocol}",
                        extra={"backend_type": self.__class__.__name__, "operation": "init_producer", "protocol": self.security_protocol})

        try:
            self.producer = aiokafka.AIOKafkaProducer(**producer_configs)
            await self.producer.start()
            await self.producer.init_transactions()
            logger.info(f"KafkaBackend initialized producer for topic {self.topic}.",
                        extra={"backend_type": self.__class__.__name__, "operation": "init_producer_success"})
        except aiokafka.errors.KafkaError as kafka_e:
            logger.critical(f"Kafka producer initialization failed with KafkaError: {kafka_e}. Check broker connectivity/configs.", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "init_producer_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="KafkaInitError").inc()
            asyncio.create_task(send_alert(f"Kafka producer initialization failed: {kafka_e}", severity="critical"))
            raise
        except Exception as e:
            logger.critical(f"Kafka producer initialization failed unexpectedly: {e}.", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "init_producer_unexpected_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="KafkaInitError").inc()
            asyncio.create_task(send_alert(f"Kafka producer initialization failed: {e}", severity="critical"))
            raise


    async def _init_elasticsearch_client(self):
        """Conceptual: Initializes Elasticsearch client for querying."""
        try:
            from elasticsearch import AsyncElasticsearch, TransportError
            self.es_client = AsyncElasticsearch(
                hosts=[self.es_host],
                request_timeout=self.params.get("es_timeout", 30),
            )
            await self.es_client.ping()
            logger.info(f"KafkaBackend: Elasticsearch client initialized for {self.es_host} and index {self.es_index}.",
                        extra={"backend_type": self.__class__.__name__, "operation": "init_es_client_success"})
        except ImportError:
            logger.warning("Elasticsearch client library not found. Kafka querying via ES will be unavailable.",
                           extra={"backend_type": self.__class__.__name__, "operation": "init_es_client_import_fail"})
            self.es_client = None
        except TransportError as e:
            logger.error(f"KafkaBackend: Failed to connect to Elasticsearch at {self.es_host}: {e}. Check network/credentials.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "init_es_client_transport_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ElasticsearchConnectionError").inc()
            self.es_client = None
        except Exception as e:
            logger.error(f"KafkaBackend: Unexpected error initializing Elasticsearch client at {self.es_host}: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "init_es_client_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ElasticsearchInitError").inc()
            asyncio.create_task(send_alert(f"KafkaBackend failed to connect to Elasticsearch: {e}", severity="high"))
            self.es_client = None


    async def _process_internal_retry_queue(self):
        """Processes items from the internal retry queue."""
        while True:
            try:
                batch_data = await self._internal_retry_queue.get()
                self.KAFKA_PRODUCER_BUFFER_FILL_RATIO.labels(backend=self.__class__.__name__).set(self._internal_retry_queue.qsize() / self._internal_retry_queue.maxsize)
                
                logger.info(f"KafkaBackend: Reprocessing batch of {len(batch_data)} entries from internal retry queue.",
                            extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_reprocess"})
                # We call _atomic_context directly, which has its *own* retry logic.
                async with self._atomic_context(prepared_entries=batch_data):
                    pass
                logger.info(f"KafkaBackend: Successfully reprocessed batch from internal retry queue.",
                            extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_success"})
            except asyncio.CancelledError:
                logger.debug("KafkaBackend: Internal retry processor task cancelled.",
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cancelled"})
                break
            except Exception as e:
                logger.error(f"KafkaBackend: Failed to reprocess batch from internal retry queue after all retries: {e}. Enqueuing to DLQ.", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_fail"})
                await self._dlq.enqueue(batch_data, failure_reason=str(e))


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """Sends single prepared entry within the current transaction."""
        if self.producer is None:
            # If the producer failed initialization, retry it.
            await retry_operation(self._init_producer, backend_name=self.__class__.__name__, op_name="append_init_producer")

        value = json.dumps(prepared_entry, sort_keys=True).encode("utf-8")
        
        try:
            # send_and_wait ensures it's sent (or throws error) before moving to next entry in transaction
            await self.producer.send_and_wait(self.topic, value, key=prepared_entry["entry_id"].encode("utf-8"))
        except aiokafka.errors.ProducerFenced as e:
            self.KAFKA_FENCED_PRODUCER_COUNTER.labels(backend=self.__class__.__name__).inc()
            logger.critical(f"KafkaBackend: Producer fenced by broker. This producer instance is no longer valid. Error: {e}", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "producer_fenced"})
            asyncio.create_task(send_alert(f"Kafka producer for {self.__class__.__name__} was fenced. Restart service.", severity="emergency"))
            raise
        except aiokafka.errors.KafkaError as kafka_e:
            logger.error(f"KafkaBackend: Producer send failed for entry '{prepared_entry.get('entry_id')}': {kafka_e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "producer_send_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type=type(kafka_e).__name__).inc()
            raise
        except Exception as e:
            logger.error(f"KafkaBackend: Unexpected error during producer send for entry '{prepared_entry.get('entry_id')}': {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "producer_send_unexpected_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ProducerSendError").inc()
            raise


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries Kafka audit logs via Elasticsearch sink (conceptual)."""
        if not self.es_client:
            logger.warning("KafkaBackend: Elasticsearch client not initialized. Cannot query Kafka logs efficiently. Falling back to direct Kafka consumer query (inefficient).",
                           extra={"backend_type": self.__class__.__name__, "operation": "query_no_es"})
            return await self._query_kafka_directly(filters, limit)

        es_query_body = {
            "query": {"bool": {"must": []}},
            "size": limit,
            "sort": [{"timestamp": {"order": "desc"}}]
        }

        if "timestamp >=" in filters:
            es_query_body["query"]["bool"]["must"].append({"range": {"timestamp": {"gte": filters["timestamp >="]}}})
        if "timestamp <=" in filters:
            es_query_body["query"]["bool"]["must"].append({"range": {"timestamp": {"lte": filters["timestamp <="]}}})
        if "entry_id" in filters:
            es_query_body["query"]["bool"]["must"].append({"term": {"entry_id.keyword": filters["entry_id"]}})
        if "schema_version" in filters:
            es_query_body["query"]["bool"]["must"].append({"term": {"schema_version": filters["schema_version"]}})
        
        logger.debug(f"KafkaBackend: Executing Elasticsearch query: {json.dumps(es_query_body)}",
                     extra={"backend_type": self.__class__.__name__, "operation": "es_query_exec"})
        try:
            resp = await retry_operation(
                lambda: self.es_client.search(index=self.es_index, body=es_query_body),
                backend_name=self.__class__.__name__, op_name="elasticsearch_query"
            )

            parsed_results = []
            for hit in resp.body['hits']['hits']:
                source = hit['_source']
                parsed_results.append({
                    "encrypted_data": source.get("encrypted_data"),
                    "entry_id": source.get("entry_id"),
                    "timestamp": source.get("timestamp"),
                    "schema_version": source.get("schema_version"),
                    "_audit_hash": source.get("_audit_hash")
                })
            return parsed_results
        except Exception as e:
            logger.error(f"Kafka (Elasticsearch) query failed: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "es_query_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ElasticsearchQueryError").inc()
            asyncio.create_task(send_alert(f"KafkaBackend Elasticsearch query failed: {e}", severity="high"))
            raise

    async def _query_kafka_directly(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """
        Inefficient: Queries Kafka directly via consumer for basic retrieval from earliest offset.
        NOT suitable for production analytical queries on large topics. This is a fallback/diagnostic tool.
        """
        consumer_configs = {
            "bootstrap_servers": self.bootstrap_servers,
            "auto_offset_reset": "earliest",
            "enable_auto_commit": False,
            "group_id": f"audit_query_consumer_{uuid.uuid4()}",
            "request_timeout_ms": self.params.get("kafka_consumer_timeout_ms", 10000),
            "security_protocol": self.security_protocol,
            "sasl_mechanism": self.sasl_mechanism,
            "sasl_plain_username": self.sasl_username,
            "sasl_plain_password": self.sasl_password,
            "ssl_cafile": self.ssl_cafile,
            "ssl_certfile": self.ssl_certfile,
            "ssl_keyfile": self.ssl_keyfile,
        }
        consumer = aiokafka.AIOKafkaConsumer(**consumer_configs)

        entries = []
        try:
            await consumer.start()
            
            partitions = await consumer.partitions_for_topic(self.topic)
            if not partitions:
                logger.warning(f"KafkaBackend: No partitions found for topic {self.topic}.",
                               extra={"backend_type": self.__class__.__name__, "operation": "direct_query_no_partitions"})
                return []
            topic_partitions = [aiokafka.TopicPartition(self.topic, p) for p in partitions]
            consumer.assign(topic_partitions)
            await consumer.seek_to_beginning()
            
            consumed_count = 0
            while consumed_count < limit:
                # Poll for messages with a timeout
                messages_by_tp = await consumer.getmany(timeout_ms=1000, max_records=limit - consumed_count)
                if not messages_by_tp:
                    logger.debug("KafkaBackend: No more messages or timeout reached during direct query.",
                                 extra={"backend_type": self.__class__.__name__, "operation": "direct_query_end"})
                    break

                for tp, msgs in messages_by_tp.items():
                    for msg in msgs:
                        # (Consumer lag monitoring logic removed for brevity)
                        try:
                            prepared_entry = json.loads(msg.value.decode("utf-8"))
                            entries.append(prepared_entry)
                            consumed_count += 1
                            if consumed_count >= limit:
                                break
                        except json.JSONDecodeError:
                            logger.warning(f"KafkaBackend: Skipping malformed Kafka message from {tp} at offset {msg.offset}: {msg.value.decode('utf-8', errors='ignore')[:100]}...",
                                           extra={"backend_type": self.__class__.__name__, "operation": "direct_query_malformed_msg",
                                                  "topic": tp.topic, "partition": tp.partition, "offset": msg.offset})
                            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MalformedKafkaMessage").inc()
                        except Exception as parse_e:
                            logger.warning(f"KafkaBackend: Unexpected error parsing Kafka message from {tp} at offset {msg.offset}: {parse_e}", exc_info=True,
                                           extra={"backend_type": self.__class__.__name__, "operation": "direct_query_parse_error",
                                                  "topic": tp.topic, "partition": tp.partition, "offset": msg.offset})
                            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="KafkaParseError").inc()
                    if consumed_count >= limit:
                        break
        except aiokafka.errors.KafkaError as kafka_e:
            logger.error(f"KafkaBackend direct consumer query failed with Kafka error: {kafka_e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "direct_query_kafka_error"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type=type(kafka_e).__name__).inc()
            asyncio.create_task(send_alert(f"KafkaBackend direct consumer query failed: {kafka_e}", severity="medium"))
            raise
        except Exception as e:
            logger.error(f"KafkaBackend direct consumer query failed unexpectedly: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "direct_query_unexpected_error"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="KafkaDirectQueryError").inc()
            asyncio.create_task(send_alert(f"KafkaBackend direct consumer query failed: {e}", severity="medium"))
            raise
        finally:
            if consumer and not consumer.closed():
                await consumer.stop()
                logger.debug("KafkaBackend: Direct consumer stopped.",
                             extra={"backend_type": self.__class__.__name__, "operation": "direct_query_consumer_stop"})


    async def _migrate_schema(self) -> None:
        """
        Kafka schema migration: Requires external Schema Registry integration (e.g., Avro, Protobuf).
        """
        logger.info("KafkaBackend schema migration: This requires integration with a Schema Registry (e.g., Avro). JSON does not support strong schema evolution.",
                    extra={"backend_type": self.__class__.__name__, "operation": "migrate_schema"})
        pass


    async def _health_check(self) -> bool:
        """Checks Kafka producer and (optionally) Elasticsearch connectivity."""
        try:
            if self.producer is None:
                await retry_operation(self._init_producer, backend_name=self.__class__.__name__, op_name="health_check_init_producer")
            
            # Send a non-transactional message to check connectivity
            await retry_operation(
                lambda: self.producer.send_and_wait(self.topic, b"health_check_ping", timeout=1),
                backend_name=self.__class__.__name__, op_name="kafka_producer_health"
            )
            logger.debug("KafkaBackend: Producer health check successful.",
                         extra={"backend_type": self.__class__.__name__, "operation": "producer_health_ok"})

            if self.es_client:
                await retry_operation(
                    lambda: self.es_client.ping(),
                    backend_name=self.__class__.__name__, op_name="elasticsearch_ping"
                )
                logger.debug("KafkaBackend: Elasticsearch health check successful.",
                             extra={"backend_type": self.__class__.__name__, "operation": "es_health_ok"})
            return True
        except Exception as e:
            logger.warning(f"KafkaBackend health check failed: {e}", exc_info=True,
                           extra={"backend_type": self.__class__.__name__, "operation": "health_check_fail"})
            return False


    async def _get_current_schema_version(self) -> int:
        """
        For Kafka, schema version is typically managed by a Schema Registry.
        """
        logger.info("KafkaBackend: Schema version is managed by Schema Registry. Assuming current schema version for local operations.",
                    extra={"backend_type": self.__class__.__name__, "operation": "get_schema_version"})
        return self.schema_version


    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Manages Kafka transactions for a batch of messages.
        """
        if self.producer is None:
            await retry_operation(self._init_producer, backend_name=self.__class__.__name__, op_name="atomic_context_init_producer")

        # Circuit breaker check for producer operations
        if not self._circuit_breaker.allow_request():
            logger.warning("KafkaBackend: Circuit breaker is OPEN for producer. Batch not sent.",
                           extra={"backend_type": self.__class__.__name__, "operation": "circuit_breaker_open_producer"})
            raise ConnectionRefusedError("Circuit breaker is OPEN. Kafka endpoint deemed unhealthy.")

        self.KAFKA_PRODUCER_TRANSACTION_STATUS.labels(backend=self.__class__.__name__).set(1)
        commit_start_time = time.perf_counter()
        commit_status = "failed"
        try:
            await self.producer.begin_transaction()
            
            for prepared_entry in prepared_entries:
                await self._append_single(prepared_entry)
            
            await self.producer.commit_transaction()
            commit_status = "success"
            logger.debug(f"KafkaBackend: Transaction committed for batch of {len(prepared_entries)} entries.",
                         extra={"backend_type": self.__class__.__name__, "operation": "transaction_commit_success", "batch_size": len(prepared_entries)})
            self._circuit_breaker.record_success()
            yield
        except aiokafka.errors.ProducerFenced as e:
            self.KAFKA_FENCED_PRODUCER_COUNTER.labels(backend=self.__class__.__name__).inc()
            logger.critical(f"Kafka transaction failed: Producer fenced by broker. This producer instance is no longer valid. Error: {e}", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "transaction_fail_producer_fenced"})
            commit_status = "producer_fenced"
            await self.producer.abort_transaction()
            await asyncio.create_task(self._dlq.enqueue(prepared_entries, failure_reason=f"ProducerFenced: {str(e)}"))
            await asyncio.create_task(send_alert(f"Kafka producer for {self.__class__.__name__} was fenced. Service restart strongly recommended.", severity="emergency"))
            raise
        except aiokafka.errors.KafkaError as kafka_e:
            logger.error(f"Kafka transaction failed with KafkaError: {kafka_e}. Aborting transaction.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "transaction_fail_kafka_error"})
            commit_status = "kafka_error"
            await self.producer.abort_transaction()
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type=type(kafka_e).__name__).inc()
            self._circuit_breaker.record_failure(kafka_e)
            await asyncio.create_task(self._dlq.enqueue(prepared_entries, failure_reason=str(kafka_e)))
            await asyncio.create_task(send_alert(f"KafkaBackend transaction aborted. Batch failed: {kafka_e}. Enqueued to DLQ.", severity="critical"))
            raise
        except Exception as e:
            logger.error(f"Kafka transaction failed unexpectedly: {e}. Aborting transaction.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "transaction_fail_unexpected"})
            commit_status = "unexpected_error"
            await self.producer.abort_transaction()
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="TransactionAbort").inc()
            self._circuit_breaker.record_failure(e)
            await asyncio.create_task(self._dlq.enqueue(prepared_entries, failure_reason=str(e)))
            await asyncio.create_task(send_alert(f"KafkaBackend transaction aborted. Batch failed: {e}. Enqueued to DLQ.", severity="critical"))
            raise
        finally:
            self.KAFKA_PRODUCER_TRANSACTION_STATUS.labels(backend=self.__class__.__name__).set(0)
            commit_duration = time.perf_counter() - commit_start_time
            self.KAFKA_TRANSACTION_COMMIT_DURATION.labels(backend=self.__class__.__name__, status=commit_status).observe(commit_duration)


    async def _reprocess_failed_batch(self, batch_data: List[Dict[str, Any]]):
        """Callback for DLQ to reprocess a failed batch."""
        logger.info(f"KafkaBackend: Reprocessing failed batch of {len(batch_data)} entries from DLQ.",
                    extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_batch"})
        try:
            async with self._atomic_context(prepared_entries=batch_data):
                pass
            logger.info(f"KafkaBackend: Successfully reprocessed and sent batch from DLQ.",
                        extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_success"})
        except Exception as e:
            logger.error(f"KafkaBackend: Reprocessing failed for batch from DLQ: {e}.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_fail_persist"})
            raise # Re-raise error to let DLQ processor know it failed and must be re-queued


    async def close(self):
        """Closes the Kafka producer and Elasticsearch client cleanly."""
        logger.info("KafkaBackend: Initiating graceful shutdown.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_start"})
        
        await self._dlq.stop_processor()

        if self._internal_retry_processor_task and not self._internal_retry_processor_task.done():
            self._internal_retry_processor_task.cancel()
            try:
                await self._internal_retry_processor_task
            except asyncio.CancelledError:
                logger.debug("KafkaBackend internal retry processor task was cancelled.",
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cancelled"})
            except Exception as e:
                logger.error(f"Error during KafkaBackend internal retry processor cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "internal_retry_cleanup_error"})

        if self.producer and not self.producer.closed():
            logger.info("KafkaBackend: Stopping producer (flushing pending messages)...",
                        extra={"backend_type": self.__class__.__name__, "operation": "producer_stop_start"})
            try:
                await self.producer.stop()
                logger.info("KafkaBackend: Producer stopped successfully.",
                            extra={"backend_type": self.__class__.__name__, "operation": "producer_stop_success"})
            except Exception as e:
                logger.error(f"KafkaBackend: Error stopping producer: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "producer_stop_error"})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ProducerStopError").inc()

        if self.es_client:
            logger.info("KafkaBackend: Closing Elasticsearch client...",
                        extra={"backend_type": self.__class__.__name__, "operation": "es_close_start"})
            try:
                await self.es_client.close()
                logger.info("KafkaBackend: Elasticsearch client closed.",
                            extra={"backend_type": self.__class__.__name__, "operation": "es_close_success"})
            except Exception as e:
                logger.error(f"Error closing Elasticsearch client: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "es_close_error"})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="ESClientCloseError").inc()
        
        # Cancel all other background tasks from the base class
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"KafkaBackend task was cancelled during shutdown.",
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cancelled"})
            except Exception as e:
                logger.error(f"Error during KafkaBackend task cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cleanup_error"})
        logger.info("KafkaBackend: Shutdown complete.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_end"})


# --- SplunkBackend ---
class SplunkBackend(LogBackend):
    """
    Splunk backend with HEC batch uploads and Search API integration with pagination.
    Provides robust session management, chunking for HEC, and improved error handling.
    """
    MAX_HEC_PAYLOAD_BYTES = 10 * 1024 * 1024 # 10 MB - Splunk HEC recommended max payload size is 10MB

    # Metrics specific to Splunk Backend
    SPLUNK_HEC_CHUNK_DURATION = Histogram("audit_backend_splunk_hec_chunk_duration_seconds", "Splunk HEC chunk upload duration", ["backend", "status"])
    SPLUNK_HEC_CHUNK_RATE = Counter("audit_backend_splunk_hec_chunk_total", "Total Splunk HEC chunks sent", ["backend", "status"])
    SPLUNK_SEARCH_JOB_DURATION = Histogram("audit_backend_splunk_search_job_duration_seconds", "Splunk Search Job duration (creation to results)", ["backend", "status"])
    SPLUNK_SEARCH_JOB_COUNT = Counter("audit_backend_splunk_search_job_total", "Total Splunk Search Jobs created", ["backend", "status"])
    SPLUNK_SEARCH_RESULTS_COUNT = Counter("audit_backend_splunk_search_results_total", "Total results fetched from Splunk search", ["backend"])


    def _validate_params(self):
        if "hec_url" not in self.params or "hec_token" not in self.params:
            raise ValueError("hec_url and hec_token parameters are required")
        if "search_url" not in self.params:
            raise ValueError("search_url parameter is required for querying")

        self.hec_url = self.params["hec_url"]
        self.search_url = self.params["search_url"]
        self.hec_token = self.params["hec_token"]
        self.source = self.params.get("source", "audit_system")
        self.sourcetype = self.params.get("sourcetype", "_json")
        self.index = self.params.get("index", "main")
        self.timeout = self.params.get("timeout", 30)

        # Circuit Breaker Configuration
        self._circuit_breaker = SimpleCircuitBreaker(
            backend_name=self.__class__.__name__,
            failure_threshold=self.params.get("cb_failure_threshold", 5),
            recovery_timeout=self.params.get("cb_recovery_timeout", 60)
        )
        # Persistent Retry Queue (DLQ) - Configurable to FileBackedRetryQueue
        dlq_class = self.params.get("dlq_class", FileBackedRetryQueue)
        self.dlq_persistence_file = self.params.get("dlq_persistence_file", f"splunk_backend_dlq_{uuid.uuid4()}.jsonl")
        self._dlq = dlq_class(
            backend_name=self.__class__.__name__,
            persistence_file=self.dlq_persistence_file,
            circuit_breaker=self._circuit_breaker, # Pass circuit breaker to DLQ
            max_queue_size=self.params.get("dlq_max_size", 10000),
            max_reprocess_attempts=self.params.get("dlq_max_reprocess_attempts", 5)
        )


    def __init__(self, params: Dict[str, Any]):
        self._background_tasks: Set[asyncio.Task] = set() # Init task set before super()
        super().__init__(params)
        self.session: Optional[aiohttp.ClientSession] = None
        self._init_task = asyncio.create_task(self._init_session())
        self._background_tasks.add(self._init_task)
        self._init_task.add_done_callback(self._background_tasks.discard)

        # Start DLQ processor
        self._dlq_processor_task = asyncio.create_task(self._dlq.start_processor(self._reprocess_failed_batch))
        self._background_tasks.add(self._dlq_processor_task)
        self._dlq_processor_task.add_done_callback(self._background_tasks.discard)


    async def _init_session(self):
        """Initializes Splunk HEC and Search API aiohttp session."""
        try:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Splunk {self.hec_token}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            logger.info(f"SplunkBackend initialized for HEC {self.hec_url} and Search API {self.search_url}.",
                        extra={"backend_type": self.__class__.__name__, "operation": "init_session_success"})
        except Exception as e:
            logger.critical(f"SplunkBackend session initialization failed: {e}", exc_info=True,
                            extra={"backend_type": self.__class__.__name__, "operation": "init_session_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="InitError").inc()
            asyncio.create_task(send_alert(f"SplunkBackend session initialization failed: {e}", severity="critical"))
            raise

    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op for SplunkBackend as the _atomic_context handles batch sending directly from the prepared_entries list.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """
        Queries Splunk using Search API with robust pagination and error handling.
        """
        if self.session is None:
            await retry_operation(self._init_session, backend_name=self.__class__.__name__, op_name="query_init_session")

        # Circuit breaker check for query operations
        if not self._circuit_breaker.allow_request():
            logger.warning("SplunkBackend: Circuit breaker is OPEN for queries. Query not executed.",
                           extra={"backend_type": self.__class__.__name__, "operation": "circuit_breaker_open_query"})
            raise ConnectionRefusedError("Circuit breaker is OPEN. Splunk endpoint deemed unhealthy for queries.")

        search_query = f"search index={self.index} sourcetype={self.sourcetype}"
        
        if "timestamp >=" in filters:
            search_query += f" earliest=\"{filters['timestamp >=']}\""
        if "timestamp <=" in filters:
            search_query += f" latest=\"{filters['timestamp <=']}\""
        
        if "entry_id" in filters:
            search_query += f" entry_id=\"{filters['entry_id']}\""
        if "schema_version" in filters:
            search_query += f" schema_version={filters['schema_version']}"

        # Fields must match what is in the _atomic_context hec_event
        search_query += f" | table event.entry_id, event.encrypted_data, event.timestamp, event.schema_version, event._audit_hash"
        
        all_results = []
        offset = 0
        count_per_request = min(limit, 1000)

        job_id = None
        search_job_start_time = time.perf_counter()
        search_job_status = "failed"
        try:
            # 1. Create the search job
            response_post_job = await retry_operation(
                lambda: self.session.post(f"{self.search_url}/services/search/jobs",
                                          data={"search": search_query, "output_mode": "json", "exec_mode": "normal"},
                                          timeout=self.timeout),
                backend_name=self.__class__.__name__, op_name="splunk_start_job"
            )
            response_json_job = await response_post_job.json()
            job_id = response_json_job.get("sid")
            if not job_id:
                logger.error(f"Splunk search job creation failed: No SID returned. Response: {response_json_job}",
                             extra={"backend_type": self.__class__.__name__, "operation": "splunk_start_job_no_sid"})
                raise RuntimeError(f"Splunk search job creation failed: {response_json_job}")
            logger.debug(f"SplunkBackend: Started search job with SID: {job_id}",
                         extra={"backend_type": self.__class__.__name__, "operation": "splunk_start_job_success", "job_id": job_id})
            
            # 2. Poll for job completion and retrieve results in pages
            max_poll_duration = self.timeout * 2
            poll_start_time = time.perf_counter()
            while len(all_results) < limit:
                if time.perf_counter() - poll_start_time > max_poll_duration:
                    logger.warning(f"SplunkBackend: Search job {job_id} polling timed out after {max_poll_duration} seconds.",
                                   extra={"backend_type": self.__class__.__name__, "operation": "splunk_job_poll_timeout", "job_id": job_id})
                    raise TimeoutError(f"Splunk search job {job_id} did not complete within expected time.")

                # Get job status
                status_response = await retry_operation(
                    lambda: self.session.get(f"{self.search_url}/services/search/jobs/{job_id}", params={"output_mode": "json"}, timeout=self.timeout / 2),
                    backend_name=self.__class__.__name__, op_name="splunk_get_job_status"
                )
                job_status = await status_response.json()
                is_done = job_status["entry"][0]["content"].get("isDone") == "1"
                if not is_done:
                    logger.debug(f"SplunkBackend: Search job {job_id} still running (progress: {job_status['entry'][0]['content'].get('dispatchState')}). Polling again...",
                                 extra={"backend_type": self.__class__.__name__, "operation": "splunk_job_polling", "job_id": job_id, "dispatch_state": job_status['entry'][0]['content'].get('dispatchState')})
                    await asyncio.sleep(RETRY_BACKOFF_FACTOR * 1)
                    continue

                # Job is done, retrieve results page by page
                results_response = await retry_operation(
                    lambda: self.session.get(f"{self.search_url}/services/search/jobs/{job_id}/results",
                                             params={"output_mode": "json", "offset": offset, "count": count_per_request}),
                    backend_name=self.__class__.__name__, op_name="splunk_get_job_results_page"
                )
                results_json = await results_response.json()

                current_batch = []
                for result in results_json.get("results", []):
                    parsed_item = {
                        "encrypted_data": result.get("event.encrypted_data"),
                        "entry_id": result.get("event.entry_id"),
                        "timestamp": result.get("event.timestamp"),
                        "schema_version": int(result.get("event.schema_version")) if result.get("event.schema_version") else None,
                        "_audit_hash": result.get("event._audit_hash")
                    }
                    current_batch.append(parsed_item)

                all_results.extend(current_batch)
                self.SPLUNK_SEARCH_RESULTS_COUNT.labels(backend=self.__class__.__name__).inc(len(current_batch))
                offset += len(current_batch)
                
                if len(current_batch) < count_per_request:
                    logger.debug(f"SplunkBackend: Reached end of search results after {len(all_results)} entries.",
                                 extra={"backend_type": self.__class__.__name__, "operation": "splunk_query_end", "total_results": len(all_results)})
                    break

            search_job_status = "succeeded"
            self._circuit_breaker.record_success()
            return all_results[:limit]

        except aiohttp.ClientResponseError as cre:
            logger.error(f"Splunk query failed with HTTP status {cre.status}: {cre.message}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "splunk_query_http_fail", "status_code": cre.status})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type=f"SplunkHTTPError_{cre.status}").inc()
            self._circuit_breaker.record_failure(cre)
            asyncio.create_task(send_alert(f"SplunkBackend query failed due to HTTP error: {cre.message}", severity="high"))
            raise
        except Exception as e:
            logger.error(f"Splunk query failed: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "splunk_query_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SplunkQueryError").inc()
            self._circuit_breaker.record_failure(e)
            asyncio.create_task(send_alert(f"SplunkBackend query failed: {e}", severity="high"))
            raise
        finally:
            job_duration = time.perf_counter() - search_job_start_time
            self.SPLUNK_SEARCH_JOB_DURATION.labels(backend=self.__class__.__name__, status=search_job_status).observe(job_duration)
            self.SPLUNK_SEARCH_JOB_COUNT.labels(backend=self.__class__.__name__, status=search_job_status).inc()

            if job_id:
                try:
                    await retry_operation(
                        lambda: self.session.delete(f"{self.search_url}/services/search/jobs/{job_id}", timeout=self.timeout),
                        backend_name=self.__class__.__name__, op_name="splunk_delete_job"
                    )
                    logger.debug(f"SplunkBackend: Deleted search job {job_id}.",
                                 extra={"backend_type": self.__class__.__name__, "operation": "splunk_job_delete", "job_id": job_id})
                except Exception as cleanup_e:
                    logger.warning(f"SplunkBackend: Failed to delete search job {job_id}: {cleanup_e}. Manual cleanup may be required in Splunk.", exc_info=True,
                                   extra={"backend_type": self.__class__.__name__, "operation": "splunk_job_delete_fail", "job_id": job_id})
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SplunkJobCleanupError").inc()


    async def _migrate_schema(self) -> None:
        """
        Splunk schema migration is schema-on-read.
        """
        logger.info("SplunkBackend schema migration (schema-on-read). Data is indexed, but parsing rules can be updated.",
                    extra={"backend_type": self.__class__.__name__, "operation": "migrate_schema"})
        pass

    async def _health_check(self) -> bool:
        """Checks Splunk HEC connectivity."""
        if self.session is None:
            try:
                await self._init_session()
            except Exception:
                logger.warning(f"SplunkBackend health check: Session could not be initialized.",
                               extra={"backend_type": self.__class__.__name__, "operation": "health_check_init_fail"})
                return False

        try:
            test_event = {
                "event": {"message": "audit_backend_health_check", "test_id": str(uuid.uuid4())},
                "source": self.source,
                "sourcetype": self.sourcetype,
                "index": self.index,
                "time": time.time()
            }
            # Use a short timeout for the health check POST
            async with self.session.post(self.hec_url, json=test_event, timeout=aiohttp.ClientTimeout(total=self.timeout / 2)) as response:
                if response.status == 200:
                    logger.debug(f"Splunk HEC health check successful for index '{self.index}'.",
                                 extra={"backend_type": self.__class__.__name__, "operation": "hec_health_ok", "index": self.index})
                    return True
                logger.warning(f"Splunk HEC health check failed with status {response.status}.",
                               extra={"backend_type": self.__class__.__name__, "operation": "hec_health_status_fail", "status_code": response.status})
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Splunk HEC health check failed: {e}", exc_info=True,
                           extra={"backend_type": self.__class__.__name__, "operation": "hec_health_check_fail"})
            return False

    async def _get_current_schema_version(self) -> int:
        """For Splunk, schema version is implicitly handled by the schema-on-read model."""
        logger.info("SplunkBackend: Schema version is managed by schema-on-read and explicit field in event. Assuming current schema version for local operations.",
                    extra={"backend_type": self.__class__.__name__, "operation": "get_schema_version"})
        return self.schema_version

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for Splunk batch via one or more HEC requests for multiple events.
        Handles chunking for large payloads and reports errors for each chunk.
        """
        if not prepared_entries:
            yield
            return

        if self.session is None:
            await self._init_session()

        # Circuit breaker check for HEC operations
        if not self._circuit_breaker.allow_request():
            logger.warning("SplunkBackend: Circuit breaker is OPEN for HEC. Batch not sent.",
                           extra={"backend_type": self.__class__.__name__, "operation": "circuit_breaker_open_hec"})
            raise ConnectionRefusedError("Circuit breaker is OPEN. Splunk HEC endpoint deemed unhealthy.")

        current_chunk_bytes = 0
        current_chunk_events_json_bytes = []
        chunks_to_send: List[bytes] = []

        for entry in prepared_entries:
            hec_event = {
                "event": entry, # The entire prepared_entry is the event payload
                "source": self.source,
                "sourcetype": self.sourcetype,
                "index": self.index,
                "time": datetime.datetime.fromisoformat(entry["timestamp"].replace('Z', '+00:00')).timestamp()
            }
            event_json_bytes = json.dumps(hec_event, sort_keys=True).encode('utf-8')
            
            if current_chunk_bytes + len(event_json_bytes) + 1 > self.MAX_HEC_PAYLOAD_BYTES and current_chunk_events_json_bytes:
                chunks_to_send.append(b"\n".join(current_chunk_events_json_bytes))
                current_chunk_events_json_bytes = []
                current_chunk_bytes = 0
            
            current_chunk_events_json_bytes.append(event_json_bytes)
            current_chunk_bytes += len(event_json_bytes) + 1 # +1 for newline
        
        if current_chunk_events_json_bytes:
            chunks_to_send.append(b"\n".join(current_chunk_events_json_bytes))

        all_chunks_successful = True
        for i, payload_chunk in enumerate(chunks_to_send):
            chunk_send_status = "failed"
            start_chunk_time = time.perf_counter()
            
            async def http_post_op():
                nonlocal chunk_send_status
                async with self.session.post(self.hec_url, data=payload_chunk) as response:
                    if response.status >= 400:
                        chunk_send_status = f"error_{response.status}"
                        error_detail = await response.text()
                        logger.error(f"SplunkBackend HEC batch chunk failed with status {response.status}: {error_detail[:500]}...",
                                     extra={"backend_type": self.__class__.__name__, "operation": "hec_chunk_fail",
                                            "status_code": response.status, "chunk_num": i+1})
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info, history=response.history, status=response.status,
                            message=f"HEC batch upload failed: {error_detail}"
                        )
                    response.raise_for_status()
                chunk_send_status = "success"

            try:
                # Wrap the POST operation in the retry helper
                await retry_operation(
                    http_post_op,
                    backend_name=self.__class__.__name__,
                    op_name=f"splunk_hec_batch_send_chunk_{i}"
                )
                self._circuit_breaker.record_success() # Record success on this chunk
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                chunk_send_status = "network_error"
                logger.error(f"SplunkBackend HEC chunk network error: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "hec_chunk_network_error", "chunk_num": i+1})
                BACKEND_NETWORK_ERRORS.labels(backend=self.__class__.__name__, operation="send_hec_chunk").inc()
                self._circuit_breaker.record_failure(e)
                all_chunks_successful = False
                raise
            except Exception as e:
                chunk_send_status = "internal_error"
                logger.error(f"SplunkBackend HEC chunk unexpected error: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "hec_chunk_unexpected_error", "chunk_num": i+1})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="HECChunkSendError").inc()
                self._circuit_breaker.record_failure(e)
                all_chunks_successful = False
                raise
            finally:
                chunk_duration = time.perf_counter() - start_chunk_time
                self.SPLUNK_HEC_CHUNK_DURATION.labels(backend=self.__class__.__name__, status=chunk_send_status).observe(chunk_duration)
                self.SPLUNK_HEC_CHUNK_RATE.labels(backend=self.__class__.__name__, status=chunk_send_status).inc()

        if not all_chunks_successful:
            raise RuntimeError("One or more HEC chunks failed to send after retries.")

        logger.debug(f"SplunkBackend: Successfully sent {len(prepared_entries)} entries in {len(chunks_to_send)} HEC chunks.",
                     extra={"backend_type": self.__class__.__name__, "operation": "atomic_hec_write_success",
                            "entries_count": len(prepared_entries), "chunks_count": len(chunks_to_send)})
        try:
            yield
        except Exception as e:
            # This block captures errors *after* the yield, which is unlikely here, 
            # but if it did, we must enqueue to DLQ.
            logger.error(f"SplunkBackend atomic HEC batch failed post-yield: {e}. Attempting DLQ.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "atomic_hec_write_fail_post"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            await asyncio.create_task(self._dlq.enqueue(prepared_entries, failure_reason=str(e)))
            await asyncio.create_task(send_alert(f"SplunkBackend atomic HEC batch write failed post-yield. Data might be lost.", severity="critical"))
            raise


    async def _reprocess_failed_batch(self, batch_data: List[Dict[str, Any]]):
        """Callback for DLQ to reprocess a failed batch."""
        logger.info(f"SplunkBackend: Reprocessing failed batch of {len(batch_data)} entries from DLQ.",
                    extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_batch"})
        try:
            async with self._atomic_context(prepared_entries=batch_data):
                pass
            logger.info(f"SplunkBackend: Successfully reprocessed and sent batch from DLQ.",
                        extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_success"})
        except Exception as e:
            logger.error(f"SplunkBackend: Reprocessing failed for batch from DLQ: {e}.", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "dlq_reprocess_fail_persist"})
            raise # Re-raise error to let DLQ processor know it failed and must be re-queued


    async def close(self):
        """Closes the aiohttp client session and stops background tasks cleanly."""
        logger.info("SplunkBackend: Initiating graceful shutdown.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_start"})
        
        await self._dlq.stop_processor()

        if self.session and not self.session.closed:
            logger.info("SplunkBackend: Closing aiohttp session...",
                        extra={"backend_type": self.__class__.__name__, "operation": "session_close_start"})
            await self.session.close()
            logger.info("SplunkBackend: aiohttp session closed.",
                        extra={"backend_type": self.__class__.__name__, "operation": "session_close_end"})
        
        # Cancel all other background tasks from the base class
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"SplunkBackend task was cancelled during shutdown.",
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cancelled"})
            except Exception as e:
                logger.error(f"Error during SplunkBackend task cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cleanup_error"})
        logger.info("SplunkBackend: Shutdown complete.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_end"})


# --- InMemoryBackend ---
class InMemoryBackend(LogBackend):
    """
    In-memory backend for testing and development. Not suitable for production due to non-persistence.
    Provides optional memory limits, basic metrics, and a conceptual snapshot for dev/QA recovery.
    """
    def _validate_params(self):
        self.max_memory_entries = self.params.get("max_memory_entries", None)
        if self.max_memory_entries is not None and self.max_memory_entries <= 0:
            raise ValueError("max_memory_entries must be a positive integer or None.")
        
        self.max_memory_bytes = self.params.get("max_memory_bytes", None)
        if self.max_memory_bytes is not None and self.max_memory_bytes <= 0:
            raise ValueError("max_memory_bytes must be a positive integer or None.")
        
        self.snapshot_file = self.params.get("snapshot_file", None)

    def __init__(self, params: Dict[str, Any]):
        self._background_tasks: Set[asyncio.Task] = set() # Init task set before super()
        super().__init__(params)
        self.logs: List[Dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.current_memory_bytes = 0
        
        # Metrics for InMemoryBackend
        self.INMEMORY_SIZE_GAUGE = Gauge("audit_backend_inmemory_size_entries", "Current number of entries in InMemoryBackend", ["backend"])
        self.INMEMORY_MEMORY_BYTES_GAUGE = Gauge("audit_backend_inmemory_memory_bytes", "Approximate memory usage of InMemoryBackend (bytes)", ["backend"])
        self.INMEMORY_EVICTIONS_COUNTER = Counter("audit_backend_inmemory_evictions_total", "Total entries evicted from InMemoryBackend", ["backend"])
        self.INMEMORY_OOM_EVENTS_COUNTER = Counter("audit_backend_inmemory_oom_events_total", "Out of memory events in InMemoryBackend", ["backend"])
        self.INMEMORY_FLUSH_DURATION = Histogram("audit_backend_inmemory_flush_duration_seconds", "Duration of InMemoryBackend batch flush", ["backend"])
        
        self.INMEMORY_SIZE_GAUGE.labels(backend=self.__class__.__name__).set(0)
        self.INMEMORY_MEMORY_BYTES_GAUGE.labels(backend=self.__class__.__name__).set(0)

        self._load_snapshot_task = asyncio.create_task(self._load_snapshot())
        self._background_tasks.add(self._load_snapshot_task)
        self._load_snapshot_task.add_done_callback(self._background_tasks.discard)


    async def _load_snapshot(self):
        """Conceptual: Loads previously saved snapshot for dev/QA crash recovery."""
        if self.snapshot_file and os.path.exists(self.snapshot_file):
            logger.info(f"InMemoryBackend: Attempting to load snapshot from '{self.snapshot_file}'.",
                        extra={"backend_type": self.__class__.__name__, "operation": "load_snapshot"})
            try:
                async with self.lock:
                    async with aiofiles.open(self.snapshot_file, 'rb') as f:
                        compressed_data = await f.read()
                    decompressed_data = zlib.decompress(compressed_data)
                    loaded_entries = json.loads(decompressed_data.decode('utf-8'))
                    
                    for entry in loaded_entries:
                        if entry["entry_id"] not in {e["entry_id"] for e in self.logs}:
                            self.logs.append(entry)
                            self.current_memory_bytes += len(json.dumps(entry, sort_keys=True).encode('utf-8'))
                    
                    self.INMEMORY_SIZE_GAUGE.labels(backend=self.__class__.__name__).set(len(self.logs))
                    self.INMEMORY_MEMORY_BYTES_GAUGE.labels(backend=self.__class__.__name__).set(self.current_memory_bytes)
                logger.info(f"InMemoryBackend: Loaded {len(loaded_entries)} entries from snapshot.",
                            extra={"backend_type": self.__class__.__name__, "operation": "load_snapshot_success", "entries_loaded": len(loaded_entries)})
            except Exception as e:
                logger.error(f"InMemoryBackend: Failed to load snapshot from '{self.snapshot_file}': {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "load_snapshot_fail"})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SnapshotLoadError").inc()
        else:
            logger.info("InMemoryBackend: No snapshot file found or configured.",
                        extra={"backend_type": self.__class__.__name__, "operation": "no_snapshot"})


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op. All appending logic is within _atomic_context to ensure atomicity and proper locking/eviction.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries in-memory list with basic filtering on top-level stored fields."""
        async with self.lock:
            filtered_logs = []
            for stored_entry in reversed(self.logs):
                match = True
                if "entry_id" in filters and stored_entry.get("entry_id") != filters["entry_id"]:
                    match = False
                if "timestamp >=" in filters and stored_entry.get("timestamp", "") < filters["timestamp >="]:
                    match = False
                if "timestamp <=" in filters and stored_entry.get("timestamp", "") > filters["timestamp <="]:
                    match = False
                if "schema_version" in filters:
                    stored_schema_version = stored_entry.get("schema_version")
                    if stored_schema_version is None or stored_schema_version != filters["schema_version"]:
                        match = False
                
                if match:
                    filtered_logs.append(stored_entry)
                    if len(filtered_logs) >= limit:
                        break
            
            return filtered_logs[::-1]


    async def _migrate_schema(self) -> None:
        """No-op for in-memory backend, as data is non-persistent."""
        logger.info("InMemoryBackend schema migration (no-op). Data is not persisted.",
                    extra={"backend_type": self.__class__.__name__, "operation": "migrate_schema"})
        pass

    async def _health_check(self) -> bool:
        """Always healthy."""
        return True

    async def _get_current_schema_version(self) -> int:
        """Always returns current SCHEMA_VERSION for in-memory."""
        return self.schema_version

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for in-memory batch via a lock, with memory limits and eviction.
        """
        start_flush_time = time.perf_counter()
        try:
            async with self.lock:
                for entry in prepared_entries:
                    if entry["entry_id"] not in {e["entry_id"] for e in self.logs}:
                        entry_size_bytes = len(json.dumps(entry, sort_keys=True).encode('utf-8'))
                        
                        if self.max_memory_bytes is not None and (self.current_memory_bytes + entry_size_bytes) > self.max_memory_bytes:
                            while self.logs and (self.current_memory_bytes + entry_size_bytes) > self.max_memory_bytes:
                                evicted_entry = self.logs.pop(0)
                                evicted_size_bytes = len(json.dumps(evicted_entry, sort_keys=True).encode('utf-8'))
                                self.current_memory_bytes -= evicted_size_bytes
                                self.INMEMORY_EVICTIONS_COUNTER.labels(backend=self.__class__.__name__).inc()
                                logger.warning(f"InMemoryBackend: Evicted oldest entry '{evicted_entry.get('entry_id')}' due to byte limit during atomic flush.",
                                               extra={"backend_type": self.__class__.__name__, "operation": "eviction_byte_limit", "entry_id": evicted_entry.get('entry_id')})

                            if self.max_memory_bytes is not None and entry_size_bytes > self.max_memory_bytes:
                                logger.critical(f"InMemoryBackend: Single entry exceeds max_memory_bytes ({self.max_memory_bytes} bytes). Cannot store. Entry size: {entry_size_bytes} bytes. Data loss for this entry.",
                                                extra={"backend_type": self.__class__.__name__, "operation": "single_entry_too_large_atomic"})
                                self.INMEMORY_OOM_EVENTS_COUNTER.labels(backend=self.__class__.__name__).inc()
                                asyncio.create_task(send_alert(f"InMemoryBackend: Cannot store entry, single entry too large for configured memory limit. Data lost.", severity="critical"))
                                continue

                        self.logs.append(entry)
                        self.current_memory_bytes += entry_size_bytes
                        
                        while self.max_memory_entries is not None and len(self.logs) > self.max_memory_entries:
                            if not self.logs:
                                break
                            evicted_entry = self.logs.pop(0)
                            self.current_memory_bytes -= len(json.dumps(evicted_entry, sort_keys=True).encode('utf-8'))
                            self.INMEMORY_EVICTIONS_COUNTER.labels(backend=self.__class__.__name__).inc()
                            logger.warning(f"InMemoryBackend: Evicted oldest entry '{evicted_entry.get('entry_id')}' due to entry count limit during atomic flush.",
                                           extra={"backend_type": self.__class__.__name__, "operation": "eviction_entry_count_limit", "entry_id": evicted_entry.get('entry_id')})

                    else:
                        logger.debug(f"InMemoryBackend: Skipping duplicate entry_id '{entry['entry_id']}' in batch.",
                                     extra={"backend_type": self.__class__.__name__, "operation": "deduplication_skip", "entry_id": entry['entry_id']})

                self.INMEMORY_SIZE_GAUGE.labels(backend=self.__class__.__name__).set(len(self.logs))
                self.INMEMORY_MEMORY_BYTES_GAUGE.labels(backend=self.__class__.__name__).set(self.current_memory_bytes)
                logger.debug(f"InMemoryBackend: Atomically flushed {len(prepared_entries)} entries into memory. Current size: {len(self.logs)} entries, {self.current_memory_bytes} bytes.",
                             extra={"backend_type": self.__class__.__name__, "operation": "atomic_flush_success",
                                    "entries_flushed": len(prepared_entries), "current_entries": len(self.logs), "current_bytes": self.current_memory_bytes})
            
            yield
        except Exception as e:
            logger.error(f"InMemoryBackend atomic batch write failed: {e}", exc_info=True,
                         extra={"backend_type": self.__class__.__name__, "operation": "atomic_write_fail"})
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            asyncio.create_task(send_alert(f"InMemoryBackend atomic batch write failed.", severity="high"))
            raise
        finally:
            flush_duration = time.perf_counter() - start_flush_time
            self.INMEMORY_FLUSH_DURATION.labels(backend=self.__class__.__name__).observe(flush_duration)


    async def close(self):
        """
        Cleans up the InMemoryBackend. This involves optional snapshotting to disk
        and clearing all in-memory logs.
        """
        logger.info("InMemoryBackend: Initiating graceful shutdown.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_start"})
        
        if self.snapshot_file:
            logger.info(f"InMemoryBackend: Saving snapshot to '{self.snapshot_file}'.",
                        extra={"backend_type": self.__class__.__name__, "operation": "save_snapshot"})
            try:
                async with self.lock:
                    json_data = json.dumps(self.logs, sort_keys=True).encode('utf-8')
                    compressed_data = zlib.compress(json_data, level=COMPRESSION_LEVEL)
                    temp_snapshot_file = f"{self.snapshot_file}.tmp_{uuid.uuid4()}"
                    async with aiofiles.open(temp_snapshot_file, 'wb') as f:
                        await f.write(compressed_data)
                    await asyncio.to_thread(os.replace, temp_snapshot_file, self.snapshot_file)
                logger.info("InMemoryBackend: Snapshot saved successfully.",
                            extra={"backend_type": self.__class__.__name__, "operation": "save_snapshot_success"})
            except Exception as e:
                logger.error(f"InMemoryBackend: Failed to save snapshot to '{self.snapshot_file}': {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "save_snapshot_fail"})
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SnapshotSaveError").inc()
                asyncio.create_task(send_alert(f"InMemoryBackend: Failed to save snapshot. Data not persisted.", severity="critical"))

        logger.info("InMemoryBackend: Clearing all in-memory logs.",
                    extra={"backend_type": self.__class__.__name__, "operation": "clear_logs"})
        async with self.lock:
            self.logs.clear()
            self.current_memory_bytes = 0
            self.INMEMORY_SIZE_GAUGE.labels(backend=self.__class__.__name__).set(0)
            self.INMEMORY_MEMORY_BYTES_GAUGE.labels(backend=self.__class__.__name__).set(0)
        
        # Cancel all other background tasks from the base class
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"InMemoryBackend task was cancelled during shutdown.",
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cancelled"})
            except Exception as e:
                logger.error(f"Error during InMemoryBackend task cleanup: {e}", exc_info=True,
                             extra={"backend_type": self.__class__.__name__, "operation": "task_cleanup_error"})
        logger.info("InMemoryBackend: Shutdown complete.",
                    extra={"backend_type": self.__class__.__name__, "operation": "close_end"})
}
Do not shorten for brevity, truncate, abbreviate, or any of the lazy thing ai tries to do. Fix and return the entire untruncated full file with all logic and function fully intact.s

Backends: audit_backend_streaming
audit_backend_streaming_backends.py
Needs imports:


from .audit_backend_core import register_backend, get_backend
At end, register:


register_backend("http", HTTPBackend)
register_backend("kafka", KafkaBackend)
register_backend("splunk", SplunkBackend)
register_backend("inmemory", InMemoryBackend)
...