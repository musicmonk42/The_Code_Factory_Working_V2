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

from collections import deque
from typing import Any, Dict, List, Optional, Set, Deque, Callable, Awaitable, AsyncIterator

from prometheus_client import Counter, Gauge, Histogram

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
        re.compile(r"secret='[^']+'")
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

    def _get_redacted_replacement(self, pattern):
        # Extract the field name from the regex, e.g., "password" from "password': '[^']+'"
        match = re.match(r"(\w+):", pattern.pattern)
        if match:
            # We explicitly replace with '[REDACTED]' to make the field obvious
            return f"{match.group(1)}: '[REDACTED]'"
        return "[REDACTED]" # Generic if no clear field name


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


# --- Persistent Retry Queue (Platinum-grade DLQ) ---
class PersistentRetryQueue:
    """
    A durable Dead Letter Queue (DLQ) using a local file for persistence.
    Supports graceful restart, poison message handling, and introspection.
    """
    DLQ_OLDEST_ITEM_AGE_SECONDS_GAUGE = Gauge("audit_backend_dlq_oldest_item_age_seconds", "Age of the oldest item in DLQ", ["backend"])
    DLQ_ITEM_ATTEMPTS_GAUGE = Gauge("audit_backend_dlq_item_attempts_total", "Reprocess attempts for items in DLQ", ["backend"])
    DLQ_POISON_MESSAGE_COUNTER = Counter("audit_backend_dlq_poison_messages_total", "Total poison messages identified in DLQ", ["backend"])
    DLQ_QUEUE_FULL_DROPS_COUNTER = Counter("audit_backend_dlq_queue_full_drops_total", "Total items dropped from DLQ because queue was full", ["backend"])
    DLQ_LOAD_FAILURES_COUNTER = Counter("audit_backend_dlq_load_failures_total", "Total failures when loading DLQ from persistence", ["backend"])
    DLQ_PERSIST_FAILURES_COUNTER = Counter("audit_backend_dlq_persist_failures_total", "Total failures when persisting DLQ to disk", ["backend"])


    def __init__(self, backend_name: str, persistence_file: str, circuit_breaker: SimpleCircuitBreaker,
                 max_queue_size: int = 10000, max_reprocess_attempts: int = 5):
        self.backend_name = backend_name
        self.persistence_file = persistence_file
        self.max_queue_size = max_queue_size
        self.max_reprocess_attempts = max_reprocess_attempts
        self._circuit_breaker = circuit_breaker # Mandatory Circuit Breaker dependency
        
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=self.max_queue_size)
        self._processor_task: Optional[asyncio.Task] = None
        self._running = False
        self._dlq_lock = asyncio.Lock() # Protect persistence file operations

        # Metrics for DLQ
        self.DLQ_COUNT_GAUGE = Gauge("audit_backend_dlq_count", "Current number of items in Dead Letter Queue", ["backend"])
        self.DLQ_ENQUEUED_COUNT = Counter("audit_backend_dlq_enqueued_total", "Total items enqueued to DLQ", ["backend"])
        self.DLQ_REPROCESSED_COUNT = Counter("audit_backend_dlq_reprocessed_total", "Total items reprocessed from DLQ", ["backend"])
        self.DLQ_DROPPED_COUNT = Counter("audit_backend_dlq_dropped_total", "Total items dropped from DLQ (e.g., max attempts, queue full)", ["backend"])

        self.DLQ_COUNT_GAUGE.labels(backend=self.backend_name).set(0)
        self.DLQ_OLDEST_ITEM_AGE_SECONDS_GAUGE.labels(backend=self.backend_name).set(0)
        
        self.logger = logging.getLogger(f"{__name__}.{self.backend_name}.DLQProcessor")
        # Reload items from persistence on init
        asyncio.create_task(self._reload_from_persistence())


    async def _reload_from_persistence(self):
        """Loads unprocessed items from the persistence file into the queue."""
        self.logger.info(f"DLQ: Reloading items from persistence file '{self.persistence_file}' for {self.backend_name}.",
                         extra={"backend_type": self.backend_name, "operation": "dlq_reload_start"})
        if not os.path.exists(self.persistence_file):
            self.logger.info(f"DLQ: Persistence file '{self.persistence_file}' not found. Starting with empty DLQ.",
                             extra={"backend_type": self.backend_name, "operation": "dlq_reload_no_file"})
            return

        reloaded_count = 0
        temp_loaded_items = [] # Load into temporary list first
        
        async with self._dlq_lock:
            try:
                # Use os.path.normpath for cross-platform path safety
                async with aiofiles.open(os.path.normpath(self.persistence_file), "r") as f:
                    async for line in f:
                        try:
                            item = json.loads(line.strip())
                            item.setdefault('reprocess_attempts', 0)
                            item.setdefault('last_attempt_time', 0)
                            item.setdefault('enqueue_time', time.time()) # Set enqueue time if missing (for old data)
                            item.setdefault('last_failure_reason', 'reloaded_from_persistence')
                            item.setdefault('dlq_item_id', str(uuid.uuid4())) # Ensure unique ID for reloaded items

                            temp_loaded_items.append(item)
                        except json.JSONDecodeError as jde:
                            self.logger.error(f"DLQ: Malformed entry in persistence file '{self.persistence_file}', skipping. Error: {jde}. Line: '{line.strip()[:100]}...'",
                                              extra={"backend_type": self.backend_name, "operation": "dlq_reload_malformed"})
                            self.DLQ_LOAD_FAILURES_COUNTER.labels(backend=self.backend_name).inc()
                        except Exception as e:
                            self.logger.error(f"DLQ: Unexpected error parsing reloaded item from persistence file: {e}", exc_info=True,
                                              extra={"backend_type": self.backend_name, "operation": "dlq_reload_parse_error"})
                            self.DLQ_LOAD_FAILURES_COUNTER.labels(backend=self.backend_name).inc()

                # Now, enqueue reloaded items from temp_loaded_items into the actual queue
                for item in temp_loaded_items:
                    if self._queue.qsize() < self._queue.maxsize:
                        await self._queue.put(item)
                        reloaded_count += 1
                        self.DLQ_COUNT_GAUGE.labels(backend=self.backend_name).inc()
                    else:
                        self.logger.warning(f"DLQ: Queue full during reload for {self.backend_name}. Dropping reloaded item.",
                                            extra={"backend_type": self.backend_name, "operation": "dlq_reload_queue_full"})
                        self.DLQ_DROPPED_COUNT.labels(backend=self.backend_name).inc()
                        self.DLQ_QUEUE_FULL_DROPS_COUNTER.labels(backend=self.backend_name).inc()

                # After successful reload, atomically rewrite the persistence file with current queue state.
                # This removes any malformed/unloaded entries from the original file.
                await self._persist_queue_state()
                self.logger.info(f"DLQ: Cleared old persistence file '{self.persistence_file}' after successful reload/rewrite.",
                                 extra={"backend_type": self.backend_name, "operation": "dlq_reload_clear_file"})

            except Exception as e:
                self.logger.error(f"DLQ: Failed to reload from persistence for {self.backend_name}: {e}", exc_info=True)
                self.DLQ_LOAD_FAILURES_COUNTER.labels(backend=self.backend_name).inc()
                asyncio.create_task(send_alert(f"DLQ reload failed for {self.backend_name}. Items may be lost if not manually recovered.", severity="emergency"))

        self.logger.info(f"DLQ: Reloaded {reloaded_count} items from persistence for {self.backend_name}. Current queue size: {self._queue.qsize()}",
                         extra={"backend_type": self.backend_name, "operation": "dlq_reload_end", "reloaded_count": reloaded_count})


    async def _persist_queue_state(self):
        """Persists the current queue state to disk (atomic rewrite)."""
        temp_file = os.path.normpath(f"{self.persistence_file}.tmp_{uuid.uuid4()}")
        persistence_file_norm = os.path.normpath(self.persistence_file)
        
        # We must acquire the lock BEFORE opening the temp file to protect the file operations
        async with self._dlq_lock:
            try:
                # 1. Write current queue state to a temporary file
                async with aiofiles.open(temp_file, "w") as f:
                    for item in list(self._queue._queue): # Iterate over internal deque (safe for read)
                        await f.write(json.dumps(item) + "\n")
                
                # 2. Ensure data is synced to disk before renaming for durability
                # Use run_in_executor for synchronous I/O operations
                await asyncio.get_event_loop().run_in_executor(None, functools.partial(os.fsync, os.open(temp_file, os.O_RDONLY)))
                
                # 3. Atomically replace the persistence file
                await asyncio.to_thread(os.replace, temp_file, persistence_file_norm)
                
                self.logger.debug(f"DLQ: Persisted queue state for {self.backend_name}.",
                                  extra={"backend_type": self.backend_name, "operation": "dlq_persist_success", "queue_size": self._queue.qsize()})
            except Exception as e:
                self.logger.error(f"DLQ: Failed to persist queue state for {self.backend_name}: {e}. Data loss on crash possible.", exc_info=True,
                                  extra={"backend_type": self.backend_name, "operation": "dlq_persist_fail"})
                self.DLQ_PERSIST_FAILURES_COUNTER.labels(backend=self.backend_name).inc()
                asyncio.create_task(send_alert(f"DLQ persistence failed for {self.backend_name}. Data loss possible on crash.", severity="critical"))
                # Clean up the partial temp file on failure
                if os.path.exists(temp_file):
                    await asyncio.to_thread(os.remove, temp_file)


    async def enqueue(self, item_data: Any, failure_reason: str = "unknown_failure"):
        """
        Enqueues a failed item for retry. Adds metadata for introspection and poison message handling.
        `item_data` is the original item to be reprocessed (e.g., prepared_entries batch).
        """
        dlq_item = {
            "original_item": item_data,
            "enqueue_time": time.time(),
            "reprocess_attempts": 0,
            "last_attempt_time": 0,
            "last_failure_reason": failure_reason,
            "dlq_item_id": str(uuid.uuid4()) # Unique ID for DLQ item for tracking
        }
        try:
            await self._queue.put(dlq_item)
            self.DLQ_ENQUEUED_COUNT.labels(backend=self.backend_name).inc()
            self.DLQ_COUNT_GAUGE.labels(backend=self.backend_name).set(self._queue.qsize())
            self.logger.warning(f"DLQ: Enqueued failed item to retry queue for {self.backend_name}. Queue size: {self._queue.qsize()}",
                                extra={"backend_type": self.backend_name, "operation": "dlq_enqueue", "dlq_size": self._queue.qsize(),
                                       "item_id": dlq_item["dlq_item_id"], "reason": failure_reason})
            await self._persist_queue_state() # Persist state after enqueue
        except asyncio.QueueFull:
            self.DLQ_DROPPED_COUNT.labels(backend=self.backend_name).inc()
            self.DLQ_QUEUE_FULL_DROPS_COUNTER.labels(backend=self.backend_name).inc()
            self.logger.critical(f"DLQ: Retry queue for {self.backend_name} is full ({self._queue.maxsize} items). Item dropped. Data loss occurred!",
                                 extra={"backend_type": self.backend_name, "operation": "dlq_drop_full", "dlq_size": self._queue.qsize(),
                                        "item_id": dlq_item["dlq_item_id"], "reason": "queue_full"})
            asyncio.create_task(send_alert(f"DLQ for {self.backend_name} is full. Data dropped!", severity="emergency"))


    async def start_processor(self, process_func: Callable[[Any], Awaitable[None]], interval: int = 30):
        """Starts the background processor for the DLQ."""
        self._running = True
        self._processor_task = asyncio.create_task(self._processor_loop(process_func, interval))
        self.logger.info(f"DLQ: Started processor for {self.backend_name} with interval {interval}s.",
                         extra={"backend_type": self.backend_name, "operation": "dlq_processor_start"})

    async def _processor_loop(self, process_func: Callable[[Any], Awaitable[None]], interval: int):
        """Main loop for processing items from the DLQ."""
        while self._running:
            try:
                # Update oldest item age metric
                if not self._queue.empty():
                    oldest_item = self._queue._queue[0] # Access internal deque for oldest item without removing
                    self.DLQ_OLDEST_ITEM_AGE_SECONDS_GAUGE.labels(backend=self.backend_name).set(time.time() - oldest_item['enqueue_time'])
                else:
                    self.DLQ_OLDEST_ITEM_AGE_SECONDS_GAUGE.labels(backend=self.backend_name).set(0)

                # Process items, but only if circuit breaker allows
                if not self._circuit_breaker.allow_request():
                    self.logger.debug(f"DLQ Processor: Circuit breaker for {self.backend_name} is {self._circuit_breaker.state}. Waiting for recovery.",
                                      extra={"backend_type": self.backend_name, "operation": "dlq_processor_cb_wait", "cb_state": self._circuit_breaker.state})
                    await asyncio.sleep(interval) # Wait for the next check interval
                    continue 

                # Wait for next item with a timeout, allowing for periodic checks even if queue is empty
                try:
                    dlq_item = await asyncio.wait_for(self._queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    continue # Check again after timeout/interval

                self.DLQ_COUNT_GAUGE.labels(backend=self.backend_name).set(self._queue.qsize()) # Decrement count as item is taken

                # Handle poison messages (check *after* retrieving but *before* processing)
                if dlq_item['reprocess_attempts'] >= self.max_reprocess_attempts:
                    self.DLQ_POISON_MESSAGE_COUNTER.labels(backend=self.backend_name).inc()
                    self.DLQ_DROPPED_COUNT.labels(backend=self.backend_name).inc() # Dropped due to poison
                    self.logger.critical(f"DLQ: Poison message identified for {self.backend_name} (ID: {dlq_item.get('dlq_item_id')}). Max reprocess attempts ({self.max_reprocess_attempts}) exceeded. Item dropped.",
                                         extra={"backend_type": self.backend_name, "operation": "dlq_poison_message", "item_id": dlq_item.get('dlq_item_id'),
                                                "attempts": dlq_item['reprocess_attempts'], "reason": dlq_item['last_failure_reason']})
                    asyncio.create_task(send_alert(f"Poison message for {self.backend_name}. ID: {dlq_item.get('dlq_item_id')}. Manual intervention required.", severity="emergency"))
                    await self._persist_queue_state() # Persist state to remove poison message
                    continue # Skip processing this poison message

                try:
                    self.logger.info(f"DLQ: Reprocessing item (ID: {dlq_item.get('dlq_item_id')}, Attempts: {dlq_item['reprocess_attempts']}) for {self.backend_name}.",
                                     extra={"backend_type": self.backend_name, "operation": "dlq_reprocess_attempt",
                                            "item_id": dlq_item.get('dlq_item_id'), "attempts": dlq_item['reprocess_attempts']})
                    
                    await process_func(dlq_item["original_item"]) # Attempt to re-process the original item
                    self.DLQ_REPROCESSED_COUNT.labels(backend=self.backend_name).inc()
                    self._circuit_breaker.record_success() # Report success to circuit breaker

                    # After successful reprocessing, update persistence
                    await self._persist_queue_state()
                    self.logger.info(f"DLQ: Successfully reprocessed item (ID: {dlq_item.get('dlq_item_id')}) for {self.backend_name}.",
                                     extra={"backend_type": self.backend_name, "operation": "dlq_reprocess_success", "item_id": dlq_item.get('dlq_item_id')})

                except Exception as e:
                    dlq_item['reprocess_attempts'] += 1
                    dlq_item['last_attempt_time'] = time.time()
                    dlq_item['last_failure_reason'] = str(e)[:255] # Store a truncated reason
                    await self._queue.put(dlq_item) # Re-enqueue for another attempt
                    self.DLQ_COUNT_GAUGE.labels(backend=self.backend_name).set(self._queue.qsize()) # Update count
                    
                    self._circuit_breaker.record_failure(e) # Report failure to circuit breaker

                    self.logger.error(f"DLQ: Failed to reprocess item (ID: {dlq_item.get('dlq_item_id')}) for {self.backend_name}: {e}. Attempts: {dlq_item['reprocess_attempts']}. Re-enqueued.", exc_info=True,
                                      extra={"backend_type": self.backend_name, "operation": "dlq_reprocess_fail", "item_id": dlq_item.get('dlq_item_id'),
                                             "attempts": dlq_item['reprocess_attempts'], "reason": dlq_item['last_failure_reason']})
                    # Persist state after re-enqueue with updated attempts
                    await self._persist_queue_state()
            
            except asyncio.CancelledError:
                self.logger.info(f"DLQ Processor: Loop cancelled for {self.backend_name}.",
                                 extra={"backend_type": self.backend_name, "operation": "dlq_processor_cancelled"})
                break # Exit loop on cancellation
            except Exception as e:
                self.logger.critical(f"DLQ Processor: Critical error in processor loop for {self.backend_name}: {e}", exc_info=True,
                                     extra={"backend_type": self.backend_name, "operation": "dlq_processor_critical"})
                # BACKEND_ERRORS is in core
                asyncio.create_task(send_alert(f"DLQ processor critical error for {self.backend_name}. Manual investigation needed.", severity="emergency"))
                await asyncio.sleep(interval * 2) # Longer pause before next attempt if critical failure


    async def stop_processor(self):
        """Stops the background processor for the DLQ and ensures persistence."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        # Ensure all remaining items in the queue are persisted before shutdown
        await self._persist_queue_state()
        self.logger.info(f"DLQ: Stopped processor for {self.backend_name} and persisted remaining items.",
                         extra={"backend_type": self.backend_name, "operation": "dlq_processor_stop"})


# --- File-Backed Retry Queue (Example of PersistentRetryQueue implementation) ---
class FileBackedRetryQueue(PersistentRetryQueue):
    """
    An implementation of PersistentRetryQueue that uses a local file for persistence.
    Designed for crash recovery in development/QA or for low-volume production DLQs.
    """
    def __init__(self, backend_name: str, persistence_file: str, circuit_breaker: SimpleCircuitBreaker,
                 max_queue_size: int = 10000, max_reprocess_attempts: int = 5):
        # Ensure the directory for the persistence file exists
        # Use os.path.normpath for cross-platform safety
        norm_persistence_file = os.path.normpath(persistence_file)
        os.makedirs(os.path.dirname(norm_persistence_file) or '.', exist_ok=True)
        super().__init__(backend_name, norm_persistence_file, circuit_breaker, max_queue_size, max_reprocess_attempts)
        self.logger = logging.getLogger(f"{__name__}.{backend_name}.FileBackedDLQ") # Specific logger for this implementation