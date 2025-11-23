import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from arbiter.otel_config import get_tracer
from opentelemetry.context import attach, detach
from opentelemetry.propagate import extract, inject
from pybreaker import CircuitBreaker, CircuitBreakerError

from .config_store import ConfigStore, TokenBucketRateLimiter
from .exceptions import (
    AuditChainTamperedError,
    CircuitBreakerOpenError,
    OperationQueueFullError,
    RateLimitError,
)
from .idempotency import IdempotencyStore
from .metrics import (
    GROWTH_ANOMALY_SCORE,
    GROWTH_CIRCUIT_BREAKER_TRIPS,
    GROWTH_EVENTS,
    GROWTH_OPERATION_EXECUTION_LATENCY,
    GROWTH_OPERATION_QUEUE_LATENCY,
    GROWTH_PENDING_QUEUE,
    GROWTH_SAVE_ERRORS,
    GROWTH_SKILL_IMPROVEMENT,
    GROWTH_SNAPSHOTS,
)
from .models import ArbiterState, GrowthEvent
from .storage_backends import StorageBackend

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


# --- Health Status Enum ---
class HealthStatus(Enum):
    """Health status states for the arbiter manager."""

    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"

    STOPPED = "stopped"
    ERROR = "error"


# --- Plugin System Protocol ---
class PluginHook(Protocol):
    """A protocol for plugins to hook into the growth event lifecycle."""

    async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
        """Called when a growth event is processed."""
        ...


# --- Circuit Breaker Listener ---
class CircuitBreakerListener:
    """Listener for circuit breaker state changes."""

    def __init__(self, arbiter_name: str):
        self.arbiter_name = arbiter_name

    def before_call(self, cb, func, *args, **kwargs):
        """Called before a circuit breaker protected call."""
        logger.debug(
            f"Circuit breaker '{cb.name}' checking call for arbiter '{self.arbiter_name}'"
        )

    def success(self, cb):
        """Called when a circuit breaker protected call succeeds."""
        logger.debug(
            f"Circuit breaker '{cb.name}' call succeeded for arbiter '{self.arbiter_name}'"
        )

    def failure(self, cb, exc):
        """Called when a circuit breaker protected call fails."""
        logger.warning(
            f"Circuit breaker '{cb.name}' recorded failure for arbiter '{self.arbiter_name}': {exc}"
        )

    def state_change(self, cb, old_state, new_state):
        """Called when the circuit breaker changes state."""
        logger.warning(
            f"Circuit breaker '{cb.name}' for arbiter '{self.arbiter_name}' changed state from {old_state} to {new_state}"
        )
        if str(new_state) == "open":
            GROWTH_CIRCUIT_BREAKER_TRIPS.labels(
                arbiter=self.arbiter_name, breaker_name=cb.name
            ).inc()


# --- Concrete Integrations (Replacing Mocks) ---
class Neo4jKnowledgeGraph:
    """
    A concrete implementation for interacting with a Neo4j Knowledge Graph.
    NOTE: This is a simplified example. A real implementation would use the
    official neo4j-driver and handle connection pooling, transactions, and errors.
    """

    def __init__(self, config_store: ConfigStore):
        self.uri = config_store.get("knowledge_graph.uri")
        self.user = config_store.get("knowledge_graph.user")
        self.password = config_store.get("knowledge_graph.password")
        logger.info("Initialized Neo4jKnowledgeGraph (simulation).")

    async def add_fact(
        self, arbiter_id: str, event_type: str, event_details: Dict[str, Any]
    ) -> None:
        """Simulates adding a fact to the knowledge graph."""
        # In a real implementation, this would execute a Cypher query.
        # Example: MERGE (a:Arbiter {id: $arbiter_id})
        #          MERGE (s:Skill {name: $skill_name})
        #          CREATE (a)-[:ACQUIRED]->(s)
        await asyncio.sleep(0.01)  # Simulate network latency
        logger.debug(
            f"[KnowledgeGraph] Added fact for {arbiter_id}: {event_type} -> {event_details.get('skill_name')}"
        )


class LoggingFeedbackManager:
    """
    A concrete implementation that logs feedback events.
    In a real system, this might write to a database, a message queue,
    or a dedicated feedback analysis service.
    """

    def __init__(self, config_store: ConfigStore):
        self.log_level = config_store.get("feedback_manager.log_level", "INFO")
        logger.info("Initialized LoggingFeedbackManager.")

    async def record_feedback(
        self, arbiter_id: str, event_type: str, event_details: Dict[str, Any]
    ) -> None:
        """Logs the feedback for analysis."""
        log_message = f"[Feedback] Arbiter: {arbiter_id}, Event: {event_type}, Details: {event_details}"
        logger.log(logging.getLevelName(self.log_level), log_message)
        await asyncio.sleep(0.005)  # Simulate I/O


# --- Helper Classes ---
class ContextAwareCallable:
    """Wraps an async callable to capture and restore OpenTelemetry context."""

    def __init__(
        self,
        coro: Callable[[], Awaitable[None]],
        context_carrier: Dict[str, str],
        arbiter_id: str,
    ):
        self._coro = coro
        self._context_carrier = context_carrier
        self._arbiter_id = arbiter_id
        self.queued_time = datetime.now(timezone.utc).timestamp()

    async def __call__(self):
        ctx = extract(self._context_carrier)
        token = attach(ctx)
        try:
            with tracer.start_as_current_span(
                "queued_operation", attributes={"arbiter.id": self._arbiter_id}
            ):
                GROWTH_OPERATION_QUEUE_LATENCY.labels(arbiter=self._arbiter_id).observe(
                    datetime.now(timezone.utc).timestamp() - self.queued_time
                )
                start_time = datetime.now(timezone.utc).timestamp()
                await self._coro()
                GROWTH_OPERATION_EXECUTION_LATENCY.labels(
                    arbiter=self._arbiter_id
                ).observe(datetime.now(timezone.utc).timestamp() - start_time)
        finally:
            detach(token)


# --- Main Manager Class ---
class ArbiterGrowthManager:
    """
    Manages the state, evolution, and event processing for a single arbiter.
    This class is the core logic engine, orchestrating storage, business logic,
    and integrations with external systems like knowledge graphs.
    """

    def __init__(
        self,
        arbiter_name: str,
        storage_backend: StorageBackend,
        knowledge_graph: Neo4jKnowledgeGraph,
        feedback_manager: LoggingFeedbackManager,
        config_store: ConfigStore,
        idempotency_store: IdempotencyStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.arbiter = arbiter_name
        self.storage_backend = storage_backend
        self.knowledge_graph = knowledge_graph
        self.feedback_manager = feedback_manager
        self.clock = clock
        self.config_store = config_store
        self.idempotency_store = idempotency_store

        # --- Sourced from Config ---
        self.SCHEMA_VERSION = self.config_store.get("global.schema_version", 1.0)
        self.MAX_PENDING_OPERATIONS = self.config_store.get(
            "arbiter.max_pending_operations", 1000
        )
        self._idempotency_salt = self.config_store.get(
            "security.idempotency_salt", os.urandom(16).hex()
        )

        self._state: ArbiterState = ArbiterState(arbiter_id=arbiter_name)
        self._dirty = False
        self._save_lock = asyncio.Lock()
        self._pending_operations: asyncio.Queue[ContextAwareCallable] = asyncio.Queue(
            maxsize=self.MAX_PENDING_OPERATIONS
        )
        self._before_hooks: List[PluginHook] = []
        self._after_hooks: List[PluginHook] = []
        self._running = True
        self._last_error: Optional[str] = None
        self._event_count_since_snapshot: int = 0
        self._rate_limiter = TokenBucketRateLimiter(self.config_store)

        self._load_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._evolution_task: Optional[asyncio.Task] = None

        # Initialize circuit breakers with proper listeners
        self._snapshot_breaker = CircuitBreaker(
            fail_max=5, reset_timeout=60, name=f"{self.arbiter}_snapshot"
        )
        self._push_event_breaker = CircuitBreaker(
            fail_max=10, reset_timeout=30, name=f"{self.arbiter}_push_event"
        )
        self._add_breaker_listeners()

    def _add_breaker_listeners(self):
        """Adds listeners to circuit breakers for logging and metrics."""
        # Create proper listener instances
        snapshot_listener = CircuitBreakerListener(self.arbiter)
        push_event_listener = CircuitBreakerListener(self.arbiter)

        # Add listeners to circuit breakers
        self._snapshot_breaker.add_listener(snapshot_listener)
        self._push_event_breaker.add_listener(push_event_listener)

    async def start(self) -> None:
        """
        Initializes the manager, loads state, and starts background tasks.
        """
        with tracer.start_as_current_span("arbiter_manager_start"):
            await self.storage_backend.start()
            await self.idempotency_store.start()
            await self._validate_audit_chain()
            self._load_task = asyncio.create_task(self._load_state_and_replay_events())
            self._start_time = self.clock()
            await self._load_task  # Wait for initial load to complete

            self._flush_task = asyncio.create_task(self._periodic_flush())
            self._evolution_task = asyncio.create_task(self._periodic_evolution_cycle())
            self._process_ops_task = asyncio.create_task(
                self._process_pending_operations()
            )
            logger.info(
                f"ArbiterGrowthManager for '{self.arbiter}' started successfully."
            )

    async def stop(self) -> None:
        """
        Gracefully shuts down the manager, cancels tasks, and saves final state.
        """
        if not self._running:
            return
        self._running = False
        logger.info(f"Shutting down ArbiterGrowthManager for '{self.arbiter}'...")

        # Cancel all background tasks
        tasks = [
            self._flush_task,
            self._evolution_task,
            self._load_task,
            self._process_ops_task,
        ]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in tasks if t], return_exceptions=True)

        # Process any remaining items in the queue
        logger.info(
            f"Processing {self._pending_operations.qsize()} remaining operations before shutdown..."
        )
        while not self._pending_operations.empty():
            op = await self._pending_operations.get()
            try:
                await op()
            except Exception as e:
                logger.error(f"Error processing pending operation during shutdown: {e}")

        await self._save_if_dirty(force=True)

        # Stop underlying services
        await self.idempotency_store.stop()
        await self.storage_backend.stop()
        logger.info(f"ArbiterGrowthManager for '{self.arbiter}' shut down completely.")

    async def _process_pending_operations(self):
        """Processes operations from the queue."""
        while self._running:
            try:
                operation = await self._pending_operations.get()
                await operation()
                self._pending_operations.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing operation: {e}")
                self._last_error = str(e)
            finally:
                GROWTH_PENDING_QUEUE.labels(arbiter=self.arbiter).set(
                    self._pending_operations.qsize()
                )

    async def _periodic_flush(self) -> None:
        """Periodically saves a snapshot of the state if it's dirty."""
        while self._running:
            try:
                min_interval = self.config_store.get("arbiter.flush_interval_min", 5)
                max_interval = self.config_store.get("arbiter.flush_interval_max", 60)

                logger.debug(
                    f"Arbiter '{self.arbiter}' queue size: {self._pending_operations.qsize()}"
                )
                GROWTH_PENDING_QUEUE.labels(arbiter=self.arbiter).set(
                    self._pending_operations.qsize()
                )

                sleep_interval = min_interval if self._dirty else max_interval
                await asyncio.sleep(sleep_interval)
                await self._save_if_dirty()
            except asyncio.CancelledError:
                logger.info(f"Periodic flush for '{self.arbiter}' cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"Error in periodic flush for '{self.arbiter}': {e}", exc_info=True
                )
                self._last_error = str(e)
                await asyncio.sleep(60)  # Wait longer after an error

    async def _periodic_evolution_cycle(self) -> None:
        """Periodically triggers a meta-learning and evolution cycle."""
        while self._running:
            try:
                interval = self.config_store.get(
                    "arbiter.evolution_cycle_interval_seconds", 3600
                )
                await asyncio.sleep(interval)
                await self._run_evolution_cycle()
            except asyncio.CancelledError:
                logger.info(f"Periodic evolution cycle for '{self.arbiter}' cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"Error in periodic evolution cycle for '{self.arbiter}': {e}",
                    exc_info=True,
                )
                self._last_error = str(e)
                await asyncio.sleep(300)  # Wait longer after an error

    async def _run_evolution_cycle(self) -> None:
        """Contains the logic for an arbiter's self-improvement cycle."""
        with tracer.start_as_current_span(
            "arbiter_evolution_cycle", attributes={"arbiter.id": self.arbiter}
        ):
            logger.info(f"Starting evolution cycle for arbiter: {self.arbiter}")
            # In a real system, this would trigger MLOps pipelines, etc.
            await self._audit_log("evolution_cycle_completed", {"status": "success"})
            logger.info(f"Evolution cycle for arbiter '{self.arbiter}' completed.")

    async def _validate_audit_chain(self) -> None:
        """
        Verifies the integrity of the entire audit log by checking hashes and timestamps.
        """
        with tracer.start_as_current_span("validate_audit_chain"):
            logger.info(
                f"Performing audit chain validation for arbiter: {self.arbiter}"
            )
            all_logs = await self.storage_backend.load_all_audit_logs(self.arbiter)
            if not all_logs:
                logger.info("No audit logs found. Chain is valid by default.")
                return

            last_hash = "genesis_hash"
            last_timestamp = datetime.min.replace(tzinfo=timezone.utc)

            for log in all_logs:
                # 1. Verify hash chain linkage
                if log["previous_log_hash"] != last_hash:
                    raise AuditChainTamperedError(
                        "Hash chain mismatch.",
                        details={"log_timestamp": log["timestamp"]},
                    )

                # 2. Verify integrity of the current log entry
                recalculated_hash = self._recalculate_log_hash(log)
                if log["log_hash"] != recalculated_hash:
                    raise AuditChainTamperedError(
                        "Log entry hash is corrupt.",
                        details={"log_timestamp": log["timestamp"]},
                    )

                # 3. Verify timestamps are monotonic
                current_timestamp = datetime.fromisoformat(log["timestamp"])
                if current_timestamp < last_timestamp:
                    raise AuditChainTamperedError(
                        "Timestamp out of order.",
                        details={"log_timestamp": log["timestamp"]},
                    )

                last_hash = log["log_hash"]
                last_timestamp = current_timestamp

            logger.info("Audit chain validation successful.")

    def _recalculate_log_hash(self, log: Dict[str, Any]) -> str:
        """Helper to recalculate a log's hash for validation."""
        from .storage_backends import _create_hmac_hash, _get_encryption_key_from_env

        key = _get_encryption_key_from_env()
        # Details are stored as a JSON string, so we use that directly
        details_str = (
            json.dumps(log["details"], sort_keys=True)
            if isinstance(log["details"], dict)
            else log["details"]
        )
        return _create_hmac_hash(
            key,
            log["arbiter_id"],
            log["operation"],
            log["timestamp"],
            details_str,
            log["previous_log_hash"],
        )

    async def _load_state_and_replay_events(self) -> None:
        """Loads the last snapshot and replays subsequent events to rebuild current state."""
        with tracer.start_as_current_span("load_state_and_replay_events"):
            logger.info(f"Loading state for arbiter: {self.arbiter}")
            snapshot_data = await self.storage_backend.load_snapshot(self.arbiter)
            if snapshot_data:
                self._state = ArbiterState(**snapshot_data)
                logger.info(
                    f"Loaded snapshot for '{self.arbiter}' at level {self._state.level}, event offset: {self._state.event_offset}"
                )
            else:
                self._state = ArbiterState(arbiter_id=self.arbiter)
                logger.info(f"No snapshot for '{self.arbiter}'. Starting fresh.")

            events_to_replay = await self.storage_backend.load_events(
                self.arbiter, from_offset=self._state.event_offset
            )
            logger.info(
                f"Replaying {len(events_to_replay)} events for '{self.arbiter}'..."
            )

            for event_dict in events_to_replay:
                try:
                    event = GrowthEvent(**event_dict)
                    await self._apply_event(event, is_replay=True)
                    self._state.event_offset = event_dict.get(
                        "canonical_offset", self._state.event_offset
                    )
                except Exception as e:
                    # Skip invalid or corrupt events during replay but log them.
                    logger.error(
                        f"Skipping invalid event during replay for '{self.arbiter}': {event_dict}. Error: {e}"
                    )

            self._dirty = False  # State is clean after replay
            logger.info(f"Finished replaying events for '{self.arbiter}'.")

    async def _apply_event(self, event: GrowthEvent, is_replay: bool = False) -> None:
        """
        Applies a single event to the arbiter's state. This is the core
        business logic dispatcher.
        """
        with tracer.start_as_current_span(f"apply_event_{event.type}"):
            # 1. Validate event details
            if not self._is_event_valid(event):
                raise ValueError(
                    f"Invalid event details for type {event.type}: {event.details}"
                )

            # Add logging for tests
            if not is_replay:
                logger.info(f"Processing growth event: {event.type} for {self.arbiter}")

            # Call before-hooks
            if not is_replay:
                for hook in self._before_hooks:
                    await hook.on_growth_event(event, self._state)

            # 2. Apply state change based on type
            if event.type == "skill_improved":
                skill_name = event.details["skill_name"]
                amount = event.details["improvement_amount"]
                current_score = self._state.skills.get(skill_name, 0.0)
                new_score = min(1.0, current_score + amount)
                self._state.set_skill_score(skill_name, new_score)
                logger.debug(
                    f"Applied event: skill '{skill_name}' improved to {new_score}"
                )
                if not is_replay:
                    GROWTH_SKILL_IMPROVEMENT.labels(
                        arbiter=self.arbiter, skill=skill_name
                    ).observe(amount)
                    # Anomaly Detection: Check for unusually large improvements
                    anomaly_threshold = self.config_store.get("anomaly_threshold", 0.95)
                    if amount > anomaly_threshold:
                        GROWTH_ANOMALY_SCORE.labels(
                            arbiter=self.arbiter, event_type=event.type
                        ).set(amount)
                        logger.warning(
                            f"Anomaly detected: Large skill improvement of {amount} for {skill_name}."
                        )
            elif event.type == "level_up":
                self._state.level = event.details["new_level"]
                logger.debug(f"Applied event: level up to {self._state.level}")
            # Add other event types here...
            else:
                logger.warning(f"Unknown event type received: {event.type}")

            self._dirty = True
            if not is_replay:
                self._event_count_since_snapshot += 1

            # Call after-hooks
            if not is_replay:
                for hook in self._after_hooks:
                    await hook.on_growth_event(event, self._state)

    def _is_event_valid(self, event: GrowthEvent) -> bool:
        """Validates the structure and content of an event."""
        if event.type == "skill_improved":
            return (
                "skill_name" in event.details and "improvement_amount" in event.details
            )
        if event.type == "level_up":
            return "new_level" in event.details
        # Add validation for other event types
        return True

    async def _save_if_dirty(self, force: bool = False) -> None:
        """Saves a snapshot if the state is dirty and conditions are met."""
        snapshot_interval = await self.config_store.get_config(
            "arbiter.snapshot_interval_events", 100
        )
        if self._dirty and (
            force or self._event_count_since_snapshot >= snapshot_interval
        ):
            async with self._save_lock:
                if self._dirty:  # Double-check after acquiring lock
                    logger.debug(f"Saving snapshot for '{self.arbiter}'...")
                    await self._save_snapshot_to_db()
                    self._dirty = False
                    self._event_count_since_snapshot = 0

    @tracer.start_as_current_span("save_snapshot_to_db")
    async def _save_snapshot_to_db(self) -> None:
        """Executes the actual database save operation for a snapshot."""
        try:
            await self._snapshot_breaker.call_async(
                self.storage_backend.save_snapshot,
                self.arbiter,
                self._state.model_dump(),
            )
            GROWTH_SNAPSHOTS.labels(arbiter=self.arbiter).inc()
            logger.info(f"Snapshot saved for '{self.arbiter}'.")
        except CircuitBreakerError:
            self._last_error = "Snapshot circuit breaker is open."
            logger.error(self._last_error)
            raise CircuitBreakerOpenError(self._last_error)
        except Exception as e:
            GROWTH_SAVE_ERRORS.labels(arbiter=self.arbiter).inc()
            self._last_error = str(e)
            logger.error(
                f"Failed to save snapshot for '{self.arbiter}': {e}", exc_info=True
            )
            raise

    async def _audit_log(self, operation: str, details: Dict[str, Any]) -> None:
        """Creates an immutable, chained audit log entry for a given operation."""
        previous_hash = await self.storage_backend.get_last_audit_hash(self.arbiter)
        await self.storage_backend.save_audit_log(
            self.arbiter, operation, details, previous_hash
        )

    def _generate_idempotency_key(self, event: GrowthEvent, service_name: str) -> str:
        """Creates a secure, salted hash to be used as an idempotency key."""
        details_json = json.dumps(event.details, sort_keys=True).encode()
        payload = f"{self._idempotency_salt}:{self.arbiter}:{event.type}:{event.timestamp}:{service_name}".encode()
        return hmac.new(payload, details_json, hashlib.sha256).hexdigest()

    def register_hook(self, hook: PluginHook, stage: str = "after") -> None:
        """
        Registers a plugin hook to be called during event processing.
        Hooks allow for extending functionality without modifying the core manager.

        Args:
            hook (PluginHook): The hook to register.
            stage (str): 'before' or 'after' the event is applied.
        """
        if stage == "before":
            self._before_hooks.append(hook)
        elif stage == "after":
            self._after_hooks.append(hook)
        else:
            raise ValueError("Hook stage must be either 'before' or 'after'")

    async def _push_events(self, events: List[GrowthEvent]) -> None:
        """Pushes a batch of events to external systems like KG and Feedback."""
        try:
            # In a real system with batch-capable SDKs, this would be a single call.
            async def _push():
                for event in events:
                    await self.knowledge_graph.add_fact(
                        self.arbiter, event.type, event.details
                    )
                    await self.feedback_manager.record_feedback(
                        self.arbiter, event.type, event.details
                    )

            await self._push_event_breaker.call_async(_push)
        except CircuitBreakerError:
            self._last_error = "Push event circuit breaker is open."
            logger.error(self._last_error)
            raise CircuitBreakerOpenError(self._last_error)

    async def _queue_operation(
        self, operation_coro: Callable[[], Awaitable[None]]
    ) -> None:
        """Safely queues an operation, applying rate limiting and backpressure."""
        if not await self._rate_limiter.acquire():
            raise RateLimitError("Rate limit exceeded for queuing operation.")

        if self._pending_operations.full():
            raise OperationQueueFullError(
                f"Operation queue for {self.arbiter} is full."
            )

        carrier = {}
        inject(carrier)
        context_aware_op = ContextAwareCallable(operation_coro, carrier, self.arbiter)

        await self._pending_operations.put(context_aware_op)

    async def record_growth_event(
        self, event_type: str, details: Dict[str, Any]
    ) -> None:
        """
        The primary API method for recording a new event for the arbiter.
        It queues the operation to ensure state consistency and order of operations.
        """
        with tracer.start_as_current_span("record_growth_event_api"):

            async def operation():
                timestamp = self.clock().isoformat(timespec="seconds")
                event = GrowthEvent(
                    type=event_type,
                    timestamp=timestamp,
                    details=details,
                    event_version=self.SCHEMA_VERSION,
                )

                # Check idempotency
                idempotency_key = self._generate_idempotency_key(
                    event, "growth_manager"
                )
                is_new_event = await self.idempotency_store.check_and_set(
                    idempotency_key
                )
                if not is_new_event:
                    logger.info(
                        f"Skipping duplicate event due to idempotency key: {idempotency_key}"
                    )
                    return

                await self._apply_event(event)
                await self.storage_backend.save_event(self.arbiter, event.model_dump())
                await self._push_events([event])  # Push as a batch of one

                GROWTH_EVENTS.labels(arbiter=self.arbiter).inc()
                await self._audit_log(f"event_recorded:{event_type}", details)
                await self._save_if_dirty()

            await self._queue_operation(operation)

    async def improve_skill(
        self, skill_name: str, improvement_amount: float = 0.01
    ) -> None:
        """A convenience API to record a skill improvement event."""
        await self.record_growth_event(
            "skill_improved",
            {"skill_name": skill_name, "improvement_amount": improvement_amount},
        )

    async def level_up(self) -> None:
        """A convenience API to record a level up event."""
        await self.record_growth_event(
            "level_up",
            {"new_level": self._state.level + 1, "old_level": self._state.level},
        )

    async def get_health_status(self) -> Dict[str, Any]:
        """
        Provides a detailed health check of the manager and its components.
        Useful for monitoring and diagnostics.
        """
        uptime = (
            (self.clock() - self._start_time).total_seconds()
            if hasattr(self, "_start_time")
            else 0
        )

        status = HealthStatus.HEALTHY
        if not self._running:
            status = HealthStatus.STOPPED
        elif self._last_error:
            status = HealthStatus.ERROR
        elif self._pending_operations.qsize() > self.MAX_PENDING_OPERATIONS * 0.8:
            status = HealthStatus.DEGRADED
        elif self._load_task and not self._load_task.done():
            status = HealthStatus.INITIALIZING

        return {
            "status": status.value,
            "arbiter_id": self.arbiter,
            "uptime_seconds": uptime,
            "last_error": self._last_error,
            "state": {
                "current_level": self._state.level,
                "is_dirty": self._dirty,
                "events_since_last_snapshot": self._event_count_since_snapshot,
            },
            "queue": {
                "size": self._pending_operations.qsize(),
                "max_size": self.MAX_PENDING_OPERATIONS,
            },
            "circuit_breakers": {
                "snapshot": str(self._snapshot_breaker.current_state),
                "push_event": str(self._push_event_breaker.current_state),
            },
            "last_audit_hash": await self.storage_backend.get_last_audit_hash(
                self.arbiter
            ),
        }

    def liveness_probe(self) -> bool:
        """
        A simple check to see if the manager's main loop is running.
        Failure indicates a fatal, unrecoverable error.
        """
        return self._running

    async def readiness_probe(self) -> bool:
        """
        A check to see if the manager is ready to accept new operations.
        Failure might indicate it's still starting up or a dependency is down.
        """
        if not self._running or (self._load_task and not self._load_task.done()):
            return False
        try:
            # Check connectivity to critical downstream services
            await self.idempotency_store.start()
            return True
        except Exception as e:
            logger.warning(f"Readiness check failed for '{self.arbiter}': {e}")
            return False
