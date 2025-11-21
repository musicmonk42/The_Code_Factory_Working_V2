# audit_backends/audit_backend_streaming_utils.py
import logging
import re
import time
import asyncio
import json
import uuid
import os
import zlib
import aiofiles
import shutil
import functools
# --- START: Change B Import ---
from contextlib import asynccontextmanager
# --- END: Change B Import ---

from collections import deque
# --- START: Change D Import ---
from typing import Any, Dict, List, Optional, Set, Deque, Callable, Awaitable, AsyncIterator
# --- END: Change D Import ---

# --- START: Change C Import ---
from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry
from prometheus_client.registry import REGISTRY as DEFAULT_REGISTRY
# --- END: Change C Import ---

# --- FIX: Import send_alert from audit_backend_core ---
# Assuming audit_backend_core.py is in the same directory (audit_backends/)
# If it's one level up, it would be 'from ..audit_backend_core import send_alert'
# Based on the file path, it's likely in the same package.
from .audit_backend_core import send_alert
# --- END FIX ---


# --- Sensitive Data Redaction for Logging ---
class SensitiveDataFilter(logging.Filter):
    """
    A logging filter to redact sensitive information (like tokens, passwords)
    from log messages and exception traces.
    """
    # Regex patterns for common sensitive fields. Extend as needed.
    SENSITIVE_PATTERNS = [
        re.compile(r"hec_token: '[^']+'"),
        re.compile(r"password':\s*'[^']+'"), # Matches 'password': '...'
        re.compile(r"sasl_plain_password: '[^']+'"),
        re.compile(r"Authorization: Bearer [a-zA-Z0-9\-_.]+", re.IGNORECASE), # Matches Auth headers
        re.compile(r"api_key: '[^']+'"),
        re.compile(r"connection_string='[^']+'"), # Matches connection strings
        re.compile(r"secret='[^']+'"),
        # --- START: Change A ---
        re.compile(r"(password\s*:\s*)'[^']*'", re.IGNORECASE),  # redact password:'...'
        # --- START: FIX for test_sensitive_data_filter_redaction ---
        re.compile(r"(password\s*=\s*)'[^']*'", re.IGNORECASE),  # redact password='...'
        # --- END: FIX for test_sensitive_data_filter_redaction ---
        # --- END: Change A ---
    ]

    def filter(self, record):
        # Redact in the main message
        if hasattr(record, 'msg'):
            original_msg = str(record.msg)
            redacted_msg = original_msg
            for pattern in self.SENSITIVE_PATTERNS:
                redacted_msg = pattern.sub(self._get_redacted_replacement(pattern), redacted_msg)
            record.msg = redacted_msg

        # Redact in arguments (if structured logging is used with extra dict)
        if hasattr(record, 'args') and isinstance(record.args, dict):
            redacted_args = {}
            for k, v in record.args.items():
                if isinstance(v, str):
                    redacted_v = v
                    for pattern in self.SENSITIVE_PATTERNS:
                        redacted_v = pattern.sub(self._get_redacted_replacement(pattern), redacted_v)
                    redacted_args[k] = redacted_v
                else:
                    redacted_args[k] = v
            record.args = redacted_args

        # Redact in exception traceback
        if record.exc_info and record.exc_info[1]: # exc_info is (type, value, traceback)
            # Redact in the exception message itself
            redacted_exc_value = str(record.exc_info[1])
            for pattern in self.SENSITIVE_PATTERNS:
                redacted_exc_value = pattern.sub(self._get_redacted_replacement(pattern), redacted_exc_value)
            
            # The type of the exception value is preserved by creating a new instance of the original type
            record.exc_info = (record.exc_info[0], type(record.exc_info[1])(redacted_exc_value), record.exc_info[2])

            # Redact in the formatted traceback text
            if record.exc_text: # This is usually populated by the formatter
                record.exc_text = self.redact_sensitive_info_in_traceback(record.exc_text)
            
        return True

    def redact_sensitive_info_in_traceback(self, traceback_text):
        if not traceback_text:
            return traceback_text
        redacted_text = traceback_text
        for pattern in self.SENSITIVE_PATTERNS:
            redacted_text = pattern.sub(self._get_redacted_replacement(pattern), redacted_text)
        return redacted_text

    # --- START: FIX for test_sensitive_data_filter_redaction ---
    def _get_redacted_replacement(self, pattern: re.Pattern) -> str:
        """
        Returns the appropriate replacement string for a given compiled regex pattern.
        Handles backreferences for capturing groups.
        """
        # This map uses the raw regex string as the key.
        # This is more robust than introspection.
        REPLACEMENTS = {
            # Patterns that match a whole key-value pair (0 groups)
            r"hec_token: '[^']+'": "hec_token: '[REDACTED]'",
            r"password':\s*'[^']+'": "password': '[REDACTED]'",
            r"sasl_plain_password: '[^']+'": "sasl_plain_password: '[REDACTED]'",
            r"Authorization: Bearer [a-zA-Z0-9\-_.]+": "Authorization: Bearer [REDACTED]",
            r"api_key: '[^']+'": "api_key: '[REDACTED]'",
            r"connection_string='[^']+'": "connection_string='[REDACTED]'",
            r"secret='[^']+'": "secret='[REDACTED]'",
            
            # Patterns that use capturing groups (need backreferences)
            r"(password\s*:\s*)'[^']*'": r"\1'[REDACTED]'",
            r"(password\s*=\s*)'[^']*'": r"\1'[REDACTED]'", # New pattern
        }

        # Find the pattern string (ignoring flags) and return its replacement
        replacement = REPLACEMENTS.get(pattern.pattern)
        
        if replacement:
            return replacement
        
        # Fallback for patterns not in the map (e.g., dynamic)
        # Check if the pattern has one capturing group
        if pattern.groups == 1:
            return r"\1'[REDACTED]'"
        
        # Default fallback
        return "[REDACTED]"
    # --- END: FIX for test_sensitive_data_filter_redaction ---


# --- Circuit Breaker ---
class SimpleCircuitBreaker:
    GAUGE_CIRCUIT_BREAKER_STATE = Gauge("audit_backend_circuit_breaker_state", "Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)", ["backend"])
    COUNTER_CIRCUIT_BREAKER_TRIPS = Counter("audit_backend_circuit_breaker_trips_total", "Total times circuit breaker transitioned to OPEN", ["backend"])

    def __init__(self, backend_name: str, failure_threshold: int, recovery_timeout: int,
                 reset_timeout: int = 10,  # How long in HALF_OPEN before resetting to OPEN
                 initial_state: str = "CLOSED"):
        self.backend_name = backend_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.reset_timeout = reset_timeout
        
        # Store timestamps of failures within the recovery_timeout window
        self.current_failures: Deque[float] = deque() 
        self._state = initial_state
        self.last_state_change_time = time.time()
        
        self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(0 if self._state == "CLOSED" else (1 if self._state == "OPEN" else 2))

        self.logger = logging.getLogger(f"{__name__}.{backend_name}.CircuitBreaker")
        self.logger.info(f"Circuit Breaker initialized for {backend_name} with threshold={failure_threshold}, recovery_timeout={recovery_timeout}s.",
                         extra={"backend_type": backend_name, "operation": "cb_init"})

    @property
    def state(self) -> str:
        return self._state

    def _check_state(self):
        """Internal method to transition circuit breaker state based on time and failures."""
        now = time.time()
        
        # CLOSED -> OPEN
        if self._state == "CLOSED":
            # Remove old failures that are outside the recovery_timeout window
            while self.current_failures and (now - self.current_failures[0] > self.recovery_timeout):
                self.current_failures.popleft()
            
            if len(self.current_failures) >= self.failure_threshold:
                self._state = "OPEN"
                self.last_state_change_time = now
                self.COUNTER_CIRCUIT_BREAKER_TRIPS.labels(backend=self.backend_name).inc()
                self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(1)
                self.logger.warning(f"Circuit Breaker for {self.backend_name} tripped to OPEN due to {len(self.current_failures)} failures.",
                                    extra={"backend_type": self.backend_name, "operation": "cb_open"})
        
        # OPEN -> HALF_OPEN
        elif self._state == "OPEN":
            if now - self.last_state_change_time > self.recovery_timeout:
                self._state = "HALF_OPEN"
                self.last_state_change_time = now
                self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(2)
                self.logger.info(f"Circuit Breaker for {self.backend_name} transitioned to HALF_OPEN.",
                                 extra={"backend_type": self.backend_name, "operation": "cb_half_open"})
        
        # HALF_OPEN -> OPEN (Revert on timeout or failure)
        elif self._state == "HALF_OPEN":
            if now - self.last_state_change_time > self.reset_timeout:
                self._state = "OPEN"
                self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(1)
                self.logger.warning(f"Circuit Breaker for {self.backend_name} reverted to OPEN from HALF_OPEN (timeout).",
                                    extra={"backend_type": self.backend_name, "operation": "cb_half_open_revert"})


    def allow_request(self) -> bool:
        """Checks if a request is allowed by the circuit breaker."""
        self._check_state() # Update state based on time
        if self._state == "CLOSED":
            return True
        elif self._state == "HALF_OPEN":
            # In HALF_OPEN, allow one request and immediately reset timer so subsequent requests fail CB
            if time.time() - self.last_state_change_time < self.reset_timeout:
                self.last_state_change_time = time.time() # This effectively allows one request per reset_timeout window
                return True
            return False
        return False # OPEN state


    def record_failure(self, error: Exception):
        """Records a failure and potentially trips the circuit."""
        self.current_failures.append(time.time())
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
            self.last_state_change_time = time.time()
            self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(1)
            self.logger.warning(f"Circuit Breaker for {self.backend_name} transitioned from HALF_OPEN to OPEN (trial request failed).",
                                extra={"backend_type": self.backend_name, "operation": "cb_half_open_fail"})

        self._check_state() # Immediately check if state needs to change


    def record_success(self):
        """Records a success and potentially resets the circuit."""
        if self._state == "HALF_OPEN":
            self._state = "CLOSED"
            self.last_state_change_time = time.time()
            self.GAUGE_CIRCUIT_BREAKER_STATE.labels(backend=self.backend_name).set(0)
            self.current_failures.clear() # Clear all past failures
            self.logger.info(f"Circuit Breaker for {self.backend_name} successfully reset to CLOSED.",
                             extra={"backend_type": self.backend_name, "operation": "cb_reset_success"})
        elif self._state == "CLOSED":
            # Only remove stale failures if closed
            while self.current_failures and (time.time() - self.current_failures[0] > self.recovery_timeout):
                self.current_failures.popleft()

    # --- START: Change B ---
    async def __aenter__(self):
        if not self.allow_request():
            # Raise a consistent error type you already use
            raise RuntimeError(f"Circuit {self.backend_name} is OPEN")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc is None:
            self.record_success()
        else:
            # record failure with the same semantics your public API expects
            self.record_failure(exc)
        # don’t suppress exceptions
        return False
    
    @asynccontextmanager
    async def context(self):
        """Optional explicit context helper."""
        if not self.allow_request():
            raise RuntimeError(f"Circuit {self.backend_name} is OPEN")
        try:
            yield self
        except Exception as e:
            self.record_failure(e)
            raise
        else:
            self.record_success()
    # --- END: Change B ---


# --- START: Change C & D (DLQ Refactor) ---

# --- START: Change C ---
class _BaseRetryQueue:
    def __init__(
        self,
        backend_name: str,
        persistence_file: str,
        circuit_breaker: SimpleCircuitBreaker,
        max_queue_size: int,
        max_reprocess_attempts: int,
        metrics_registry: CollectorRegistry | None = None,
    ):
        self.backend_name = backend_name
        self.persistence_file = os.path.abspath(persistence_file)
        self.circuit_breaker = circuit_breaker
        self.max_queue_size = max_queue_size
        self.max_reprocess_attempts = max_reprocess_attempts
        self._registry = metrics_registry or DEFAULT_REGISTRY

        # Safe metric creation: try to reuse existing collector with same name
        self.DLQ_COUNT_GAUGE = self._get_or_create_gauge(
            "audit_backend_dlq_count",
            "Current number of items in Dead Letter Queue",
            ["backend"],
        )
        self.DLQ_REPROCESS_COUNTER = self._get_or_create_counter(
            "audit_backend_dlq_reprocess_total",
            "Total items reprocessed from DLQ",
            ["backend", "status"],
        )

    def _get_or_create_gauge(self, name, documentation, labelnames):
        try:
            return Gauge(name, documentation, labelnames, registry=self._registry)
        except ValueError:
            # Duplicated metric name; return existing one from registry
            existing = getattr(self._registry, "_names_to_collectors", {}).get(name)
            if existing:
                return existing
            raise

    def _get_or_create_counter(self, name, documentation, labelnames):
        try:
            return Counter(name, documentation, labelnames, registry=self._registry)
        except ValueError:
            existing = getattr(self._registry, "_names_to_collectors", {}).get(name)
            if existing:
                return existing
            raise
# --- END: Change C ---

# --- START: Change D ---
class PersistentRetryQueue(_BaseRetryQueue):
    def __init__(
        self, 
        backend_name: str, 
        persistence_file: str, 
        circuit_breaker: SimpleCircuitBreaker,
        max_queue_size: int = 10000, 
        max_reprocess_attempts: int = 5,
        metrics_registry: CollectorRegistry | None = None,
        **kwargs # Accept extra kwargs
    ):
        super().__init__(
            backend_name=backend_name,
            persistence_file=persistence_file,
            circuit_breaker=circuit_breaker,
            max_queue_size=max_queue_size,
            max_reprocess_attempts=max_reprocess_attempts,
            metrics_registry=metrics_registry
        )
        self._queue = asyncio.Queue()
        self.DLQ_COUNT_GAUGE.labels(self.backend_name).set(0) # Initialize gauge
        self.logger = logging.getLogger(f"{__name__}.{self.backend_name}.DLQProcessor") # Add logger


    def current_size(self) -> int:
        return self._queue.qsize()

    async def append(self, item: dict):
        if self.current_size() >= self.max_queue_size:
            raise OverflowError("DLQ is full")
        # normalize attempts
        item.setdefault("attempts", 0)
        await self._queue.put(item)
        # update gauge
        self.DLQ_COUNT_GAUGE.labels(self.backend_name).set(self.current_size())

    async def reprocess(self, handler: Callable[[dict], Awaitable[bool]]):
        # Drain into a buffer to avoid blocking new producers
        drained = []
        while not self._queue.empty():
            drained.append(await self._queue.get())

        success, fail = 0, 0
        for item in drained:
            try:
                ok = await handler(item)
            except Exception as e:
                self.logger.error(f"DLQ: Handler failed for item {item.get('dlq_item_id')}: {e}", exc_info=True)
                ok = False
            if ok:
                success += 1
            else:
                # bump attempts, requeue or drop + alert
                item["attempts"] = int(item.get("attempts", 0)) + 1
                if item["attempts"] < self.max_reprocess_attempts:
                    await self._queue.put(item)
                else:
                    # dead-dead-letter: alert
                    # from .audit_backend_core import send_alert # Already imported at top
                    self.logger.critical(f"DLQ: item exhausted retries: {item.get('dlq_item_id')}")
                    await send_alert(
                        f"{self.backend_name}: DLQ item exhausted retries", severity="error"
                    )
                fail += 1

        self.DLQ_REPROCESS_COUNTER.labels(self.backend_name, "success").inc(success)
        self.DLQ_REPROCESS_COUNTER.labels(self.backend_name, "failure").inc(fail)
        self.DLQ_COUNT_GAUGE.labels(self.backend_name).set(self.current_size())

    # --- Start/Stop/Enqueue methods from original file, simplified for new API ---
    # These methods provide compatibility with the backend's expectation of
    # .enqueue(), .start_processor(), and .stop_processor()

    async def enqueue(self, item_data: Any, failure_reason: str = "unknown_failure"):
        """Enqueues a failed item for retry."""
        dlq_item = {
            "original_item": item_data,
            "enqueue_time": time.time(),
            "attempts": 0,
            "last_failure_reason": failure_reason,
            "dlq_item_id": str(uuid.uuid4())
        }
        try:
            await self.append(dlq_item)
            self.logger.warning(f"DLQ: Enqueued failed item to retry queue for {self.backend_name}. Queue size: {self.current_size()}",
                                extra={"backend_type": self.backend_name, "operation": "dlq_enqueue", "dlq_size": self.current_size(),
                                       "item_id": dlq_item["dlq_item_id"], "reason": failure_reason})
        except OverflowError:
            self.logger.critical(f"DLQ: Retry queue for {self.backend_name} is full ({self.max_queue_size} items). Item dropped. Data loss occurred!",
                                 extra={"backend_type": self.backend_name, "operation": "dlq_drop_full", "dlq_size": self.current_size(),
                                        "item_id": dlq_item["dlq_item_id"], "reason": "queue_full"})
            await send_alert(f"DLQ for {self.backend_name} is full. Data dropped!", severity="emergency")

    async def start_processor(self, process_func: Callable[[Any], Awaitable[None]], interval: int = 30):
        """Starts the background processor for the DLQ."""
        # The new design uses an external caller to trigger reprocess()
        # This loop is just a shim to match the old API
        self.logger.info(f"DLQ: Started processor shim for {self.backend_name}. Reprocessing must be triggered externally via reprocess().",
                         extra={"backend_type": self.backend_name, "operation": "dlq_processor_start"})
        # We still need a handler function for the reprocess method
        async def handler_wrapper(item: dict) -> bool:
            # process_func expects the *original item*, not the DLQ wrapper
            await process_func(item.get("original_item"))
            return True # Assume success if no exception
        
        self._handler = handler_wrapper
        # The processor task is no longer a persistent loop
        self._processor_task = None 
        self._running = True


    async def stop_processor(self):
        """Stops the background processor for the DLQ."""
        self._running = False
        self.logger.info(f"DLQ: Stopped processor for {self.backend_name}.",
                         extra={"backend_type": self.backend_name, "operation": "dlq_processor_stop"})
        # In this new model, we might want to trigger persistence on stop
        if hasattr(self, '_persist_queue_state'):
             await self._persist_queue_state()


class FileBackedRetryQueue(PersistentRetryQueue):
    def __init__(self, backend_name: str, persistence_file: str, circuit_breaker: SimpleCircuitBreaker,
                 max_queue_size: int = 10000, max_reprocess_attempts: int = 5,
                 metrics_registry: CollectorRegistry | None = None, **kwargs):
        
        # Ensure the directory for the persistence file exists
        norm_persistence_file = os.path.normpath(persistence_file)
        os.makedirs(os.path.dirname(norm_persistence_file) or '.', exist_ok=True)
        
        super().__init__(
            backend_name=backend_name,
            persistence_file=norm_persistence_file, # Use the normed path
            circuit_breaker=circuit_breaker,
            max_queue_size=max_queue_size,
            max_reprocess_attempts=max_reprocess_attempts,
            metrics_registry=metrics_registry,
            **kwargs # Pass any other kwargs
        )
        self.logger = logging.getLogger(f"{__name__}.{backend_name}.FileBackedDLQ") # Specific logger for this implementation
        
        # --- Add reload logic with exception handling ---
        task = asyncio.create_task(self._reload_from_persistence())
        task.add_done_callback(self._handle_reload_task_completion)

    def _handle_reload_task_completion(self, task: asyncio.Task):
        """Handle completion of reload task and log any exceptions."""
        try:
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    self.logger.error(f"DLQ reload task failed with exception: {exc}",
                                    exc_info=exc,
                                    extra={"backend_type": self.backend_name, "operation": "dlq_reload_error"})
        except Exception as e:
            self.logger.error(f"Error handling reload task completion: {e}",
                            extra={"backend_type": self.backend_name})

    async def _reload_from_persistence(self):
        """Loads unprocessed items from the persistence file into the queue."""
        self.logger.info(f"DLQ: Reloading items from persistence file '{self.persistence_file}' for {self.backend_name}.",
                         extra={"backend_type": self.backend_name, "operation": "dlq_reload_start"})
        if not os.path.exists(self.persistence_file):
            self.logger.info(f"DLQ: Persistence file '{self.persistence_file}' not found. Starting with empty DLQ.",
                             extra={"backend_type": self.backend_name, "operation": "dlq_reload_no_file"})
            return

        reloaded_count = 0
        try:
            async with aiofiles.open(self.persistence_file, "r", encoding="utf-8") as f:
                content = await f.read()
                if not content:
                    self.logger.info(f"DLQ: Persistence file '{self.persistence_file}' is empty.",
                                     extra={"backend_type": self.backend_name, "operation": "dlq_reload_empty"})
                    return
                
                items = json.loads(content)
                for item in items:
                    if self.current_size() < self.max_queue_size:
                        # Ensure attempts key exists
                        item.setdefault("attempts", 0) 
                        await self._queue.put(item)
                        reloaded_count += 1
                    else:
                         self.logger.warning(f"DLQ: Queue full during reload for {self.backend_name}. Dropping reloaded item.",
                                            extra={"backend_type": self.backend_name, "operation": "dlq_reload_queue_full"})
            
            self.DLQ_COUNT_GAUGE.labels(self.backend_name).set(self.current_size())
            self.logger.info(f"DLQ: Reloaded {reloaded_count} items. Current size: {self.current_size()}",
                             extra={"backend_type": self.backend_name, "operation": "dlq_reload_end"})

        except json.JSONDecodeError as jde:
            self.logger.error(f"DLQ: Malformed persistence file '{self.persistence_file}', cannot reload. Error: {jde}.",
                              extra={"backend_type": self.backend_name, "operation": "dlq_reload_malformed"})
        except Exception as e:
            self.logger.error(f"DLQ: Failed to reload from persistence: {e}", exc_info=True,
                              extra={"backend_type": self.backend_name, "operation": "dlq_reload_fail"})


    async def _persist_queue_state(self):
        # snapshot queue contents
        items = []
        # non-destructive snapshot
        tmp = []
        while not self._queue.empty():
            item = await self._queue.get()
            items.append(item)
            tmp.append(item)
        for item in tmp:
            await self._queue.put(item)

        try:
            async with aiofiles.open(self.persistence_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(items, ensure_ascii=False))
        except Exception as e:
            # from .audit_backend_core import send_alert # Already imported at top
            await send_alert(f"{self.backend_name}: persist failed: {e}", severity="error")
            raise

    # --- Override append and reprocess to add persistence ---
    
    async def append(self, item: dict):
        await super().append(item)
        await self._persist_queue_state() # Persist after adding
    
    async def reprocess(self, handler: Callable[[dict], Awaitable[bool]]):
        await super().reprocess(handler)
        await self._persist_queue_state() # Persist after reprocessing

# --- END: Change D ---
# --- END: Change C & D (DLQ Refactor) ---