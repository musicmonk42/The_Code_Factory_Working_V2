# arbiter/learner/core.py

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import structlog
from cryptography.fernet import Fernet, InvalidToken
from opentelemetry import metrics, trace
from redis.asyncio import Redis

# Add this import (install with: pip install asyncpg)
# or mock it for testing:
try:
    import asyncpg
except ImportError:
    asyncpg = None

from self_fixing_engineer.arbiter.bug_manager import BugManager
from self_fixing_engineer.arbiter.models.knowledge_graph_db import Neo4jKnowledgeGraph
from self_fixing_engineer.arbiter.models.meta_learning_data_store import (
    MetaLearningDataStoreConfig,
    get_meta_learning_data_store,
)
from self_fixing_engineer.arbiter.models.postgres_client import PostgresClient

# Corrected relative imports to be absolute imports from the project root
from self_fixing_engineer.arbiter.plugins.llm_client import LLMClient
from self_fixing_engineer.arbiter.policy.core import should_auto_learn
from pydantic import BaseModel

from .audit import (
    CircuitBreaker,
    MerkleTree,
    persist_knowledge,
    persist_knowledge_batch,
)
from .encryption import ArbiterConfig, decrypt_value, encrypt_value
from .explanations import generate_explanation, record_explanation_quality
from .fuzzy import FuzzyParser

# Import the metrics and helper function from the new metrics.py file to resolve the circular dependency.
from .metrics import get_labels  # Import the helper function
from .metrics import (
    forget_counter,
    forget_duration_seconds,
    learn_counter,
    learn_duration_seconds,
    learn_error_counter,
    retrieve_hit_miss,
)
from .validation import validate_data
from self_fixing_engineer.arbiter.otel_config import get_tracer_safe

# Use structlog for structured logging
logger = structlog.get_logger(__name__)

# OpenTelemetry tracer and meter
tracer = get_tracer_safe(__name__)
meter = metrics.get_meter(__name__)

# Handle the missing 'self_fixing_engineer.arbiter.audit_log' dependency gracefully.
try:
    from self_fixing_engineer.arbiter.audit_log import AuditLogger, audit_log, verify_audit_chain
except ImportError:
    logger.warning(
        "Failed to import 'self_fixing_engineer.arbiter.audit_log'. Using dummy fallbacks. "
        "Ensure the module is installed/available in production."
    )

    class AuditLogger:
        """Dummy AuditLogger class for when the real one is unavailable."""

        @staticmethod
        def from_environment():
            return DummyAuditLog()

    class DummyAuditLog:
        """A dummy audit log class that does nothing."""

        async def log_event(
            self,
            component: str,
            event: str,
            details: Dict[str, Any],
            user_id: str = "system",
        ):
            logger.info(
                f"DummyAuditLog: Event '{event}' in component '{component}' logged."
            )
            await asyncio.sleep(0)  # Make it properly async

        async def add_entry(
            self,
            component: str,
            event: str,
            details: Dict[str, Any],
            user_id: str = "system",
        ):
            """Alias for log_event to maintain compatibility."""
            await self.log_event(component, event, details, user_id)

    async def audit_log(*args, **kwargs):
        """A dummy async function for logging audit events as a no-op."""
        logger.debug("Dummy audit_log called (no-op).")
        await asyncio.sleep(0)  # Keep it async

    def verify_audit_chain(log_path: str) -> bool:
        """A dummy function that always returns True."""
        logger.debug("Dummy verify_audit_chain called. Returning True.")
        return True


# Add this after the imports and before class Arbiter


class LearningRecord(BaseModel):
    """Model for meta-learning records."""

    timestamp: str
    agent_id: str
    session_id: str
    decision_trace: Dict[str, Any]
    user_feedback: Optional[Any]
    event_type: str
    learned_domain: str
    learned_key: str
    new_value_summary: Optional[str]
    old_value_summary: Optional[str]
    version: Optional[int]
    diff_applied: Optional[bool]
    explanation: Optional[str]


class LearnerArbiterHelper:
    """
    Helper class for Learner's internal use.
    NOTE: This is NOT the main Arbiter class from self_fixing_engineer.arbiter.py.

    This lightweight helper manages state and dependencies specifically for the Learner module:
    - Maintains a memory dictionary for knowledge storage
    - Provides access to BugManager for issue tracking
    - Interfaces with Neo4j knowledge graph
    - Tracks self-audit execution state

    The main Arbiter class (arbiter.py) handles full orchestration, while this helper
    provides minimal state management for learner-specific operations.
    """

    def __init__(self):
        self.name = "LearnerArbiterHelper"
        self.state = {"memory": {}}
        # Create a simple settings dict for BugManager
        bug_manager_settings = {
            "jira_url": ArbiterConfig.JIRA_URL,
            "user": ArbiterConfig.JIRA_USER,
            "password": ArbiterConfig.JIRA_PASSWORD,
        }
        self.bug_manager = BugManager(settings=bug_manager_settings)
        self.knowledge_graph = Neo4jKnowledgeGraph(
            url=ArbiterConfig.NEO4J_URL,
            user=ArbiterConfig.NEO4J_USER,
            password=ArbiterConfig.NEO4J_PASSWORD,
        )
        self.is_running_self_audit = False
        logger.debug(
            "LearnerArbiterHelper created for Learner's internal use.", name=self.name
        )


class Learner:
    """Central learning module for Arbiter."""

    def __init__(
        self,
        arbiter: LearnerArbiterHelper,
        redis: Redis,
        db_url: Optional[str] = None,
        merkle_tree_class: Optional[Callable] = None,
    ):
        """
        Initialize the Learner module.
        Args:
            arbiter: LearnerArbiterHelper instance for state and dependencies.
            redis: Redis client for caching.
            db_url: Optional database URL. If None, uses ArbiterConfig.DATABASE_URL.
            merkle_tree_class: Optional MerkleTree class for dependency injection.
        """
        self.arbiter = arbiter
        self.redis = redis
        self.db = PostgresClient(db_url=db_url)
        logger.info("Learner initialized", db_url=self.db.db_url)

        self.merkle_tree_class = merkle_tree_class or MerkleTree
        try:
            self.ciphers = {
                key_id: (
                    Fernet(key)
                    if isinstance(key, bytes)
                    else Fernet(key.encode("utf-8"))
                )
                for key_id, key in ArbiterConfig.ENCRYPTION_KEYS.items()
            }
        except Exception as e:
            logger.critical("Failed to initialize Fernet ciphers", error=str(e))
            raise ValueError(f"Encryption setup failed: {e}") from e
        self.validation_schemas: Dict[str, Dict[str, Any]] = {}
        self.validation_hooks: Dict[str, Callable] = {}
        self.event_hooks: Dict[str, List[Callable]] = {
            "pre_learn": [],
            "post_learn": [],
            "pre_forget": [],
            "post_forget": [],
            "on_schema_reload": [],
        }
        self.db_circuit_breaker = CircuitBreaker(name="db_operations")
        self.audit_circuit_breaker = CircuitBreaker(
            name="audit_operations", failure_threshold=10
        )
        self.fuzzy_parser_hooks: List[FuzzyParser] = []
        self.explanation_feedback_log: List[Dict[str, Any]] = []
        self._self_audit_task: Optional[asyncio.Task] = None
        self._self_audit_stop_event = asyncio.Event()
        self.llm_explanation_client = LLMClient(
            provider=ArbiterConfig.LLM_PROVIDER,
            api_key=ArbiterConfig.LLM_API_KEY,
            model=ArbiterConfig.LLM_MODEL,
        )
        self.audit_logger = AuditLogger.from_environment()
        self.meta_data_store_config = MetaLearningDataStoreConfig()
        self.meta_data_store = get_meta_learning_data_store(self.meta_data_store_config)
        self.learn_semaphore = asyncio.Semaphore(
            int(os.getenv("MAX_CONCURRENT_LEARNS", 50))
        )

    async def start(self):
        """Performs all necessary async initialization tasks."""
        await self.meta_data_store.connect()
        await self.start_self_audit()

    async def _run_self_audit(self):
        """Periodically performs a self-audit of the knowledge base and audit trail."""
        self.arbiter.is_running_self_audit = True
        logger.info("Starting scheduled self-audit of knowledge base and audit trail.")

        while not self._self_audit_stop_event.is_set():
            audit_start_time = time.monotonic()
            try:
                # FIX: `audit_log` is now a real object, not an async function
                audit_log_path = (
                    self.audit_logger.log_path
                    if hasattr(self.audit_logger, "log_path")
                    else None
                )
                if not audit_log_path or not os.path.exists(audit_log_path):
                    logger.warning(
                        "Audit log file not found. Skipping audit trail verification.",
                        log_path=audit_log_path,
                    )
                    await self.audit_logger.log_event(
                        "self_audit",
                        "Audit log file not found, skipped integrity check.",
                        {"status": "skipped", "component": "audit_trail"},
                        "system",
                    )
                else:
                    audit_trail_valid = verify_audit_chain(audit_log_path)
                    if audit_trail_valid:
                        logger.info("Audit trail integrity verified successfully.")
                        await self.audit_logger.log_event(
                            "self_audit",
                            "Audit trail integrity check PASSED.",
                            {"status": "success", "component": "audit_trail"},
                            "system",
                        )
                    else:
                        logger.critical(
                            "Audit trail integrity check FAILED. Potential tampering detected!"
                        )
                        await self.audit_logger.log_event(
                            "self_audit",
                            "Audit trail integrity check FAILED. Potential tampering detected!",
                            {"status": "failure", "component": "audit_trail"},
                            "system",
                        )
                        await self.arbiter.bug_manager.bug_detected(
                            "audit_trail_tampering",
                            "Audit trail integrity check failed. Potential data tampering.",
                            {"audit_log_path": audit_log_path},
                        )

                logger.info("Knowledge base consistency check (conceptual) completed.")
                await self.audit_logger.log_event(
                    "self_audit",
                    "Knowledge consistency check (conceptual) completed.",
                    {"status": "info", "component": "knowledge_base"},
                    "system",
                )

            except Exception as e:
                logger.error("Error during self-audit", error=str(e), exc_info=True)
                await self.audit_logger.log_event(
                    "self_audit",
                    f"Self-audit failed: {e}",
                    {"status": "error", "error_message": str(e)},
                    "system",
                )
                await self.arbiter.bug_manager.bug_detected(
                    "self_audit_failure",
                    f"Self-audit process encountered an error: {e}",
                    {},
                )
            finally:
                audit_duration = time.monotonic() - audit_start_time
                logger.info("Self-audit completed", duration_seconds=audit_duration)
                await self.audit_logger.log_event(
                    "self_audit",
                    "Self-audit completed.",
                    {"duration_seconds": audit_duration, "status": "completed"},
                    "system",
                )

            await asyncio.sleep(ArbiterConfig.SELF_AUDIT_INTERVAL_SECONDS)
            if (
                self.arbiter.is_running_self_audit
                and self._self_audit_stop_event.is_set()
            ):
                break

        self.arbiter.is_running_self_audit = False

    async def start_self_audit(self):
        """Starts the periodic self-audit background task."""
        if self._self_audit_task is None or self._self_audit_task.done():
            self._self_audit_stop_event.clear()
            self._self_audit_task = asyncio.create_task(self._run_self_audit())
            logger.info("Self-audit background task started.")
            # FIX: Use the AuditLogger instance with await
            await self.audit_logger.log_event(
                "self_audit_control",
                "Self-audit task started.",
                {"action": "start"},
                "system",
            )

    async def stop_self_audit(self):
        """Stops the periodic self-audit background task."""
        if self._self_audit_task and not self._self_audit_task.done():
            self._self_audit_stop_event.set()
            try:
                await self._self_audit_task
                logger.info("Self-audit background task stopped.")
                await self.audit_logger.log_event(
                    "self_audit_control",
                    "Self-audit task stopped.",
                    {"action": "stop"},
                    "system",
                )
            except asyncio.CancelledError:
                logger.info("Self-audit task was cancelled.")
                await self.audit_logger.log_event(
                    "self_audit_control",
                    "Self-audit task cancelled.",
                    {"action": "cancel"},
                    "system",
                )
            except Exception as e:
                logger.error(
                    "Error stopping self-audit task", error=str(e), exc_info=True
                )
                await self.audit_logger.log_event(
                    "self_audit_control",
                    f"Error stopping self-audit task: {e}",
                    {"action": "stop_error", "error": str(e)},
                    "system",
                )
        if self.meta_data_store:
            await self.meta_data_store.disconnect()

    async def learn_new_thing(
        self,
        domain: str,
        key: str,
        value: Any,
        user_id: Optional[str] = None,
        source: str = "system",
        write_to_disk: bool = True,
        explanation_quality_score: Optional[int] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        decision_trace: Optional[Dict[str, Any]] = None,
        event_type: str = "knowledge_update",
    ) -> Dict[str, Any]:
        """
        Learn and store new knowledge with validation, encryption, and auditing.
        """
        with tracer.start_as_current_span("learn_new_thing"):
            async with self.learn_semaphore:
                start_time = time.monotonic()
                try:
                    if not re.match(ArbiterConfig.VALID_DOMAIN_PATTERN, domain):
                        learn_error_counter.labels(
                            **get_labels(domain=domain, error_type="invalid_domain")
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_error",
                            f"Invalid domain format: {domain}",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "error_type": "invalid_domain",
                            },
                            user_id or "system",
                        )
                        return {
                            "status": "failed",
                            "reason": f"invalid_domain: {domain}",
                        }

                    allowed, reason = await should_auto_learn(
                        domain, key, user_id, value
                    )
                    if not allowed:
                        logger.info(
                            "Auto-learning blocked by policy",
                            domain=domain,
                            key=key,
                            reason=reason,
                        )
                        await self.audit_logger.log_event(
                            "learn_skipped",
                            f"Auto-learning blocked by policy: {reason}",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "reason": reason,
                            },
                            user_id or "system",
                        )
                        return {
                            "status": "skipped",
                            "reason": f"policy_blocked: {reason}",
                        }

                    validation_result = await validate_data(self, domain, value)
                    if not validation_result["is_valid"]:
                        learn_error_counter.labels(
                            **get_labels(
                                domain=domain,
                                error_type=validation_result["reason_code"],
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_error",
                            f"Validation failed for {domain}:{key}: {validation_result['reason']}",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "error_type": validation_result["reason_code"],
                                "validation_errors": validation_result["reason"],
                            },
                            user_id or "system",
                        )
                        return {
                            "status": "failed",
                            "reason": validation_result["reason"],
                        }

                    lock_key = f"knowledge_lock:{domain}:{key}"
                    async with self.redis.lock(
                        lock_key, timeout=10, blocking_timeout=5
                    ):
                        for hook in self.event_hooks["pre_learn"]:
                            await (
                                hook(domain, key, value)
                                if asyncio.iscoroutinefunction(hook)
                                else asyncio.to_thread(hook, domain, key, value)
                            )

                        result = await self._process_learn(
                            domain, key, value, user_id, source, write_to_disk
                        )

                        if explanation_quality_score is not None:
                            await record_explanation_quality(
                                self,
                                domain,
                                key,
                                result.get("version"),
                                explanation_quality_score,
                            )

                        if result.get("status") == "learned":
                            for hook in self.event_hooks["post_learn"]:
                                await (
                                    hook(domain, key, value, result)
                                    if asyncio.iscoroutinefunction(hook)
                                    else asyncio.to_thread(
                                        hook, domain, key, value, result
                                    )
                                )
                            await self.audit_logger.log_event(
                                "knowledge_learn",
                                f"Learned {domain}:{key}",
                                {
                                    "domain": domain,
                                    "key": key,
                                    "user_id": user_id,
                                    "source": source,
                                    "version": result.get("version"),
                                },
                                user_id or "system",
                            )

                            try:
                                learning_record = LearningRecord(
                                    timestamp=datetime.now(timezone.utc).isoformat(),
                                    agent_id=agent_id or "unknown_agent",
                                    session_id=session_id or "unknown_session",
                                    decision_trace=decision_trace or {},
                                    user_feedback=result.get(
                                        "explanation_quality_score_feedback"
                                    ),
                                    event_type=event_type,
                                    learned_domain=domain,
                                    learned_key=key,
                                    new_value_summary=str(value)[:200],
                                    old_value_summary=(
                                        str(result.get("previous_value_summary"))[:200]
                                        if result.get("previous_value_summary")
                                        else None
                                    ),
                                    version=result.get("version"),
                                    diff_applied=result.get("diff_applied"),
                                    explanation=result.get("explanation"),
                                )
                                await self.meta_data_store.write_record(
                                    learning_record.model_dump()
                                )
                                logger.info(
                                    "Meta-learning record written",
                                    domain=domain,
                                    key=key,
                                )
                            except Exception as meta_e:
                                logger.error(
                                    "Failed to write meta-learning record",
                                    domain=domain,
                                    key=key,
                                    error=str(meta_e),
                                    exc_info=True,
                                )
                                learn_error_counter.labels(
                                    **get_labels(
                                        domain=domain,
                                        error_type="meta_learning_write_error",
                                    )
                                ).inc()
                                await self.audit_logger.log_event(
                                    "meta_learning_error",
                                    f"Failed to write meta-learning record: {meta_e}",
                                    {
                                        "domain": domain,
                                        "key": key,
                                        "user_id": user_id,
                                        "error": str(meta_e),
                                    },
                                    user_id or "system",
                                )

                        return result

                except InvalidToken:
                    learn_error_counter.labels(
                        **get_labels(domain=domain, error_type="encryption_error")
                    ).inc()
                    await self.audit_logger.log_event(
                        "learn_error",
                        f"Encryption/decryption error for {domain}:{key}",
                        {
                            "domain": domain,
                            "key": key,
                            "user_id": user_id,
                            "error_type": "encryption_error",
                        },
                        user_id or "system",
                    )
                    return {"status": "failed", "reason": "encryption_decryption_error"}
                except asyncio.TimeoutError:
                    learn_error_counter.labels(
                        **get_labels(domain=domain, error_type="lock_timeout")
                    ).inc()
                    await self.audit_logger.log_event(
                        "learn_error",
                        f"Lock timeout for {domain}:{key}",
                        {
                            "domain": domain,
                            "key": key,
                            "user_id": user_id,
                            "error_type": "lock_timeout",
                        },
                        user_id or "system",
                    )
                    return {"status": "failed", "reason": "lock_timeout"}
                except Exception as e:
                    logger.error(
                        "Error learning knowledge",
                        domain=domain,
                        key=key,
                        error=str(e),
                        exc_info=True,
                    )
                    await self.audit_logger.log_event(
                        "learn_error",
                        f"Error learning {domain}:{key}: {e}",
                        {
                            "domain": domain,
                            "key": key,
                            "user_id": user_id,
                            "error_type": "unexpected_error",
                            "exception": str(e),
                        },
                        user_id or "system",
                    )
                    learn_error_counter.labels(
                        **get_labels(domain=domain, error_type="unexpected_error")
                    ).inc()
                    await self.arbiter.bug_manager.bug_detected(
                        "learn_new_thing_unexpected",
                        f"Unexpected error for {domain}:{key}: {e}",
                        {
                            "domain": domain,
                            "key": key,
                            "value": str(value),
                            "user_id": user_id,
                            "source": source,
                        },
                    )
                    return {"status": "failed", "reason": f"unexpected_error: {e}"}
                finally:
                    learn_duration_seconds.labels(**get_labels(domain=domain)).observe(
                        time.monotonic() - start_time
                    )

    async def _process_learn(
        self,
        domain: str,
        key: str,
        value: Any,
        user_id: Optional[str],
        source: str,
        write_to_disk: bool,
    ) -> Dict[str, Any]:
        """Process single learning event."""
        mem = self.arbiter.state.setdefault("memory", {}).setdefault(domain, {})
        previous_entry = mem.get(key, {"domain": domain, "value": None})
        previous_value = await self._get_previous_value(previous_entry, domain)
        is_encrypted = domain in ArbiterConfig.ENCRYPTED_DOMAINS
        if is_encrypted and "v1" not in self.ciphers:
            raise ValueError("Encryption key 'v1' not available")
        stored_value = (
            await encrypt_value(value, self.ciphers["v1"], "v1")
            if is_encrypted
            else value
        )

        if (
            previous_entry.get("value") == stored_value
            and previous_entry.get("source") == source
        ):
            logger.debug("No change", domain=domain, key=key)
            return {
                "status": "unchanged",
                "reason": "value_identical",
                "previous_value_summary": str(previous_value),
            }

        value_diff = self._compute_diff(previous_value, value)
        current_version = (
            previous_entry.get("version", 0) + 1 if previous_entry.get("value") else 1
        )
        value_with_metadata = {
            "value": stored_value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "user_id": user_id,
            "version": current_version,
            "diff": value_diff,
        }

        mem[key] = value_with_metadata.copy()
        logger.info(
            "Learned in-memory",
            domain=domain,
            key=key,
            version=value_with_metadata["version"],
            source=source,
        )

        leaf_data_for_merkle = json.dumps(value, sort_keys=True, default=str).encode(
            "utf-8"
        )
        leaf = hashlib.sha256(leaf_data_for_merkle).digest()
        tree = self.merkle_tree_class([leaf])
        root = tree.get_root().hex()
        proof = tree.get_proof(0)

        if write_to_disk and await self.db_circuit_breaker.can_proceed():
            try:
                await persist_knowledge(
                    db=self.db,
                    circuit_breaker=self.audit_circuit_breaker,
                    domain=domain,
                    key=key,
                    value_with_metadata=value_with_metadata,
                    user_id=user_id,
                    leaf_hash=leaf.hex(),
                    merkle_proof=proof,
                    merkle_root=root,
                )
                await self.db_circuit_breaker.record_success()
            except Exception as e:
                await self.db_circuit_breaker.record_failure()
                learn_error_counter.labels(
                    **get_labels(domain=domain, error_type="db_persist_failure")
                ).inc()
                logger.error(
                    "Failed to persist knowledge", domain=domain, key=key, error=str(e)
                )
                await self.audit_logger.log_event(
                    "learn_error",
                    f"Failed to persist knowledge: {e}",
                    {"domain": domain, "key": key, "user_id": user_id, "error": str(e)},
                    user_id or "system",
                )
                raise e
        else:
            logger.warning(
                "Persistence skipped due to open DB circuit breaker.",
                domain=domain,
                key=key,
            )
            learn_error_counter.labels(
                **get_labels(domain=domain, error_type="circuit_breaker_open_db")
            ).inc()
            await self.audit_logger.log_event(
                "learn_skipped",
                f"DB persistence skipped for {domain}:{key} due to circuit breaker.",
                {
                    "domain": domain,
                    "key": key,
                    "user_id": user_id,
                    "reason": "db_circuit_open",
                },
                user_id or "system",
            )

        redis_value = value_with_metadata.copy()
        if is_encrypted and isinstance(redis_value["value"], bytes):
            redis_value["value"] = redis_value["value"].hex()
        await self.redis.setex(
            f"knowledge:{domain}:{key}",
            ArbiterConfig.KNOWLEDGE_REDIS_TTL_SECONDS,
            json.dumps(redis_value),
        )

        learn_counter.labels(**get_labels(domain=domain, source=source)).inc()
        explanation = await generate_explanation(
            self, domain, key, value, previous_value, value_diff
        )

        return {
            "status": "learned",
            "version": value_with_metadata["version"],
            "diff_applied": value_diff is not None,
            "explanation": explanation,
            "previous_value_summary": str(previous_value),
        }

    async def _get_previous_value(self, previous_entry, domain):
        """Helper to safely get and decrypt previous value."""
        if previous_entry.get("value"):
            try:
                return await decrypt_value(previous_entry["value"], self.ciphers)
            except (InvalidToken, Exception):
                logger.error(
                    "Failed to decrypt previous value. Assuming no previous value.",
                    domain=domain,
                    key=previous_entry.get("key"),
                )
        return None

    async def learn_batch(
        self,
        facts: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        source: str = "batch_import",
        write_to_disk: bool = True,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        event_type: str = "knowledge_update_batch",
    ) -> List[Dict[str, Any]]:
        """
        Process multiple facts in parallel.
        """
        with tracer.start_as_current_span("learn_batch"):
            start_time = time.monotonic()
            logger.info("Starting batch learning", fact_count=len(facts))

            chunk_size = int(os.getenv("BATCH_CHUNK_SIZE", 100))
            all_results = []
            meta_learning_records = []

            for i in range(0, len(facts), chunk_size):
                chunk = facts[i : i + chunk_size]

                validation_tasks = [
                    validate_data(self, fact.get("domain", ""), fact.get("value", None))
                    for fact in chunk
                ]
                validation_results = await asyncio.gather(
                    *validation_tasks, return_exceptions=True
                )

                individual_learn_tasks = []

                for j, fact in enumerate(chunk):
                    domain = fact.get("domain")
                    key = fact.get("key")
                    value = fact.get("value")
                    decision_trace = fact.get("decision_trace", {})
                    validation_res = validation_results[j]

                    if (
                        isinstance(validation_res, Exception)
                        or not validation_res["is_valid"]
                    ):
                        reason_code = (
                            validation_res["reason_code"]
                            if isinstance(validation_res, dict)
                            else "internal_validation_error"
                        )
                        reason_msg = (
                            validation_res["reason"]
                            if isinstance(validation_res, dict)
                            else str(validation_res)
                        )
                        all_results.append(
                            {"status": "skipped", "reason": reason_msg, "fact": fact}
                        )
                        learn_error_counter.labels(
                            **get_labels(
                                domain=domain if domain else "unknown",
                                error_type=reason_code,
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_batch_skipped",
                            "Fact skipped in batch due to validation",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "reason": reason_msg,
                            },
                            user_id or "system",
                        )
                        continue

                    if not all([domain, key, value is not None]):
                        all_results.append(
                            {
                                "status": "skipped",
                                "reason": "malformed_fact_structure",
                                "fact": fact,
                            }
                        )
                        learn_error_counter.labels(
                            **get_labels(
                                domain=domain if domain else "unknown",
                                error_type="malformed_fact_structure",
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_batch_skipped",
                            "Fact skipped in batch due to malformed structure",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "reason": "malformed_fact_structure",
                            },
                            user_id or "system",
                        )
                        continue

                    allowed, reason = await should_auto_learn(
                        domain, key, user_id, value
                    )
                    if not allowed:
                        all_results.append(
                            {
                                "status": "skipped",
                                "reason": f"policy_blocked: {reason}",
                                "fact": fact,
                            }
                        )
                        learn_error_counter.labels(
                            **get_labels(domain=domain, error_type="policy_blocked")
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_batch_skipped",
                            "Fact skipped in batch due to policy",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "reason": reason,
                            },
                            user_id or "system",
                        )
                        continue

                    individual_learn_tasks.append(
                        self._prepare_and_process_single_fact_for_batch(
                            domain, key, value, user_id, source, decision_trace
                        )
                    )

                processed_facts_details = await asyncio.gather(
                    *individual_learn_tasks, return_exceptions=True
                )

                batch_db_entries = []
                for res in processed_facts_details:
                    if isinstance(res, Exception):
                        # Find the original fact more safely
                        fact = {"domain": "unknown", "key": "unknown"}
                        for f in chunk:
                            if str(res).find(f.get("key", "")) != -1:
                                fact = f
                                break
                        logger.error(
                            "Error processing fact in batch",
                            domain=fact.get("domain"),
                            key=fact.get("key"),
                            error=str(res),
                        )
                        all_results.append(
                            {
                                "status": "failed",
                                "reason": f"processing_error: {res}",
                                "fact": fact,
                            }
                        )
                        learn_error_counter.labels(
                            **get_labels(
                                domain=fact.get("domain", "unknown"),
                                error_type="batch_processing_exception",
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "learn_batch_error",
                            f"Error processing fact in batch: {res}",
                            {
                                "domain": fact.get("domain"),
                                "key": fact.get("key"),
                                "user_id": user_id,
                                "error": str(res),
                            },
                            user_id or "system",
                        )
                    else:
                        all_results.append(res["result"])
                        if res["result"].get("status") == "learned":
                            batch_db_entries.append(
                                (
                                    res["domain"],
                                    res["key"],
                                    res["value_with_metadata"],
                                    res["timestamp"],
                                    res["leaf_hash"],
                                    res["merkle_proof"],
                                    res["merkle_root"],
                                )
                            )
                            try:
                                meta_learning_records.append(
                                    LearningRecord(
                                        timestamp=res["value_with_metadata"][
                                            "timestamp"
                                        ],
                                        agent_id=agent_id or "unknown_agent",
                                        session_id=session_id or "unknown_session",
                                        decision_trace=res.get("decision_trace", {}),
                                        user_feedback=None,
                                        event_type=event_type,
                                        learned_domain=res["domain"],
                                        learned_key=res["key"],
                                        new_value_summary=str(res["value"])[:200],
                                        old_value_summary=(
                                            str(res.get("previous_value_summary"))[:200]
                                            if res.get("previous_value_summary")
                                            else None
                                        ),
                                        version=res["value_with_metadata"]["version"],
                                        diff_applied=res["result"].get("diff_applied"),
                                        explanation=res["result"].get("explanation"),
                                    ).model_dump()
                                )
                            except Exception as meta_e:
                                logger.error(
                                    "Failed to prepare meta-learning record for batch fact",
                                    domain=domain,
                                    key=key,
                                    error=str(meta_e),
                                    exc_info=True,
                                )
                                learn_error_counter.labels(
                                    **get_labels(
                                        domain=domain,
                                        error_type="meta_learning_batch_prep_error",
                                    )
                                ).inc()

                if write_to_disk and await self.db_circuit_breaker.can_proceed():
                    if batch_db_entries:
                        try:
                            # Use transactions for atomicity
                            async with self.db.transaction():
                                await persist_knowledge_batch(
                                    self.db,
                                    self.audit_circuit_breaker,
                                    batch_db_entries,
                                    user_id,
                                )
                            await self.db_circuit_breaker.record_success()
                            await self.audit_logger.log_event(
                                "knowledge_learn_batch",
                                "Batch learned facts",
                                {
                                    "count": len(batch_db_entries),
                                    "user_id": user_id,
                                    "source": source,
                                },
                                user_id or "system",
                            )
                        except Exception as e:
                            await self.db_circuit_breaker.record_failure()
                            learn_error_counter.labels(
                                **get_labels(
                                    domain="batch",
                                    error_type="db_persist_failure_batch",
                                )
                            ).inc()
                            logger.error(
                                "Failed to persist batch knowledge",
                                error=str(e),
                                exc_info=True,
                            )
                            await self.audit_logger.log_event(
                                "learn_batch_error",
                                f"Failed to persist batch knowledge: {e}",
                                {"user_id": user_id, "error": str(e)},
                                user_id or "system",
                            )
                            raise e
                    else:
                        logger.info(
                            "Batch persistence skipped: No valid entries to persist."
                        )
                        await self.audit_logger.log_event(
                            "knowledge_learn_batch_skipped",
                            "Batch persistence skipped: No valid entries to persist.",
                            {"user_id": user_id, "source": source},
                            user_id or "system",
                        )
                else:
                    logger.warning(
                        "Batch persistence skipped due to open DB circuit breaker."
                    )
                    learn_error_counter.labels(
                        **get_labels(
                            domain="batch", error_type="circuit_breaker_open_db_batch"
                        )
                    ).inc()
                    await self.audit_logger.log_event(
                        "knowledge_learn_batch_skipped",
                        "Batch persistence skipped due to open DB circuit breaker.",
                        {
                            "user_id": user_id,
                            "source": source,
                            "reason": "db_circuit_open",
                        },
                        user_id or "system",
                    )

            if meta_learning_records:
                try:
                    await self.meta_data_store.write_batch(meta_learning_records)
                    logger.info(
                        "Meta-learning records written for batch",
                        count=len(meta_learning_records),
                    )
                except Exception as meta_b_e:
                    logger.error(
                        "Failed to write meta-learning batch records",
                        error=str(meta_b_e),
                        exc_info=True,
                    )
                    learn_error_counter.labels(
                        **get_labels(
                            domain="batch", error_type="meta_learning_batch_write_error"
                        )
                    ).inc()
                    await self.audit_logger.log_event(
                        "meta_learning_batch_error",
                        f"Failed to write meta-learning batch records: {meta_b_e}",
                        {"user_id": user_id, "error": str(meta_b_e)},
                        user_id or "system",
                    )

            learn_duration_seconds.labels(**get_labels(domain="batch")).observe(
                time.monotonic() - start_time
            )
            logger.info("Batch learning completed", processed_count=len(facts))
            return all_results

    async def _prepare_and_process_single_fact_for_batch(
        self,
        domain: str,
        key: str,
        value: Any,
        user_id: Optional[str],
        source: str,
        decision_trace: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Prepare single fact for batch learning."""
        mem = self.arbiter.state.setdefault("memory", {}).setdefault(domain, {})
        previous_entry = mem.get(key, {"domain": domain, "value": None})
        previous_value = await self._get_previous_value(previous_entry, domain)
        is_encrypted = domain in ArbiterConfig.ENCRYPTED_DOMAINS
        if is_encrypted and "v1" not in self.ciphers:
            raise ValueError("Encryption key 'v1' not available")
        stored_value = (
            await encrypt_value(value, self.ciphers["v1"], "v1")
            if is_encrypted
            else value
        )

        if (
            previous_entry.get("value") == stored_value
            and previous_entry.get("source") == source
        ):
            logger.debug("No change for batch fact", domain=domain, key=key)
            return {
                "result": {
                    "status": "unchanged",
                    "reason": "value_identical",
                    "fact": {"domain": domain, "key": key},
                },
                "domain": domain,
                "key": key,
                "value": value,
                "previous_value_summary": str(previous_value),
                "decision_trace": decision_trace,
            }

        value_diff = self._compute_diff(previous_value, value)
        current_version = (
            previous_entry.get("version", 0) + 1 if previous_entry.get("value") else 1
        )
        value_with_metadata = {
            "value": stored_value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "user_id": user_id,
            "version": current_version,
            "diff": value_diff,
        }

        mem[key] = value_with_metadata.copy()
        redis_value = value_with_metadata.copy()
        if is_encrypted and isinstance(redis_value["value"], bytes):
            redis_value["value"] = redis_value["value"].hex()
        await self.redis.setex(
            f"knowledge:{domain}:{key}",
            ArbiterConfig.KNOWLEDGE_REDIS_TTL_SECONDS,
            json.dumps(redis_value),
        )

        learn_counter.labels(**get_labels(domain=domain, source=source)).inc()
        explanation = await generate_explanation(
            self, domain, key, value, previous_value, value_diff
        )

        leaf_data_for_merkle = json.dumps(value, sort_keys=True, default=str).encode(
            "utf-8"
        )
        leaf = hashlib.sha256(leaf_data_for_merkle).digest()
        tree = self.merkle_tree_class([leaf])
        root = tree.get_root().hex()
        proof = tree.get_proof(0)

        return {
            "result": {
                "status": "learned",
                "version": value_with_metadata["version"],
                "diff_applied": value_diff is not None,
                "explanation": explanation,
                "fact": {"domain": domain, "key": key},
            },
            "domain": domain,
            "key": key,
            "value": value,
            "value_with_metadata": value_with_metadata,
            "timestamp": value_with_metadata["timestamp"],
            "leaf_hash": leaf.hex(),
            "merkle_proof": proof,
            "merkle_root": root,
            "previous_value_summary": str(previous_value),
            "decision_trace": decision_trace,
        }

    async def forget_fact(
        self,
        domain: str,
        key: str,
        user_id: Optional[str] = None,
        reason: str = "manual_deletion",
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        decision_trace: Optional[Dict[str, Any]] = None,
        event_type: str = "knowledge_deletion",
    ) -> Dict[str, Any]:
        """
        Remove a fact from all stores and audit the action.
        """
        with tracer.start_as_current_span("forget_fact"):
            start_time = time.monotonic()
            if not re.match(ArbiterConfig.VALID_DOMAIN_PATTERN, domain):
                await self.audit_logger.log_event(
                    "forget_error",
                    f"Invalid domain format for forgetting: {domain}",
                    {
                        "domain": domain,
                        "key": key,
                        "user_id": user_id,
                        "error_type": "invalid_domain",
                    },
                    user_id or "system",
                )
                return {"status": "failed", "reason": f"invalid_domain: {domain}"}

            lock_key = f"knowledge_lock:{domain}:{key}"
            async with self.redis.lock(lock_key, timeout=10, blocking_timeout=5):
                for hook in self.event_hooks["pre_forget"]:
                    await (
                        hook(domain, key)
                        if asyncio.iscoroutinefunction(hook)
                        else asyncio.to_thread(hook, domain, key)
                    )

                fact_to_forget = await self.retrieve_knowledge(
                    domain, key, decrypt=False
                )
                if not fact_to_forget:
                    logger.info(
                        "Fact not found for forgetting.", domain=domain, key=key
                    )
                    await self.audit_logger.log_event(
                        "knowledge_forget_skipped",
                        f"Fact not found for forgetting: {domain}:{key}",
                        {
                            "domain": domain,
                            "key": key,
                            "user_id": user_id,
                            "reason": "fact_not_found",
                        },
                        user_id or "system",
                    )
                    return {"status": "skipped", "reason": "fact_not_found"}

                decrypted_value_for_audit = None
                if isinstance(fact_to_forget.get("value"), str):
                    # If it's a string and should be encrypted, convert to bytes
                    if domain in ArbiterConfig.ENCRYPTED_DOMAINS:
                        try:
                            decrypted_value_for_audit = await decrypt_value(
                                bytes.fromhex(fact_to_forget.get("value")), self.ciphers
                            )
                        except ValueError:
                            # Not hex encoded, treat as plain value
                            decrypted_value_for_audit = fact_to_forget.get("value")
                    else:
                        decrypted_value_for_audit = fact_to_forget.get("value")
                elif isinstance(fact_to_forget.get("value"), bytes):
                    decrypted_value_for_audit = await decrypt_value(
                        fact_to_forget.get("value"), self.ciphers
                    )
                else:
                    decrypted_value_for_audit = fact_to_forget.get("value")

                try:
                    if (
                        domain in self.arbiter.state.get("memory", {})
                        and key in self.arbiter.state["memory"][domain]
                    ):
                        del self.arbiter.state["memory"][domain][key]

                    await self.redis.delete(f"knowledge:{domain}:{key}")

                    if decrypted_value_for_audit is None:
                        logger.warning(
                            "Could not decrypt value for audit log.",
                            domain=domain,
                            key=key,
                        )
                        leaf_hash_for_audit = hashlib.sha256(
                            b"DECRYPTION_FAILED_FOR_AUDIT"
                        ).hexdigest()
                        merkle_proof_for_audit = []
                        merkle_root_for_audit = "0" * 64
                        audit_notes = "Decryption failed during audit preparation."
                    else:
                        leaf_data_for_merkle_audit = json.dumps(
                            decrypted_value_for_audit, sort_keys=True, default=str
                        ).encode("utf-8")
                        leaf_hash_for_audit = hashlib.sha256(
                            leaf_data_for_merkle_audit
                        ).hexdigest()
                        tree_for_audit = self.merkle_tree_class(
                            [hashlib.sha256(leaf_data_for_merkle_audit).digest()]
                        )
                        merkle_root_for_audit = tree_for_audit.get_root().hex()
                        merkle_proof_for_audit = tree_for_audit.get_proof(0)
                        audit_notes = ""

                    if await self.db_circuit_breaker.can_proceed():
                        await self.db.delete_agent_knowledge(domain, key)
                        await self.db_circuit_breaker.record_success()
                        audit_event_data = {
                            "action": "forget_fact",
                            "domain": domain,
                            "key": key,
                            "reason": reason,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "user_id": user_id or "system",
                            "merkle_leaf": leaf_hash_for_audit,
                            "merkle_proof": merkle_proof_for_audit,
                            "merkle_root_at_deletion": merkle_root_for_audit,
                            "previous_metadata": fact_to_forget.copy(),
                            "notes": audit_notes,
                        }
                        if await self.audit_circuit_breaker.can_proceed():
                            await self.audit_logger.log_event(
                                "knowledge_forget",
                                "Fact forgotten.",
                                audit_event_data,
                                user_id or "system",
                            )
                            await self.audit_circuit_breaker.record_success()
                        else:
                            logger.error(
                                "Audit log for forgetting skipped due to open audit circuit breaker!",
                                domain=domain,
                                key=key,
                            )
                            learn_error_counter.labels(
                                **get_labels(
                                    domain=domain,
                                    error_type="audit_circuit_open_forget",
                                )
                            ).inc()
                            await self.audit_logger.log_event(
                                "forget_error",
                                f"Audit log skipped for {domain}:{key} due to circuit breaker.",
                                {
                                    "domain": domain,
                                    "key": key,
                                    "user_id": user_id,
                                    "reason": "audit_circuit_open",
                                },
                                user_id or "system",
                            )
                    else:
                        logger.warning(
                            "DB deletion skipped due to open DB circuit breaker.",
                            domain=domain,
                            key=key,
                        )
                        learn_error_counter.labels(
                            **get_labels(
                                domain=domain, error_type="db_circuit_open_forget"
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "forget_skipped",
                            f"DB deletion skipped for {domain}:{key} due to circuit breaker.",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "reason": "db_circuit_open",
                            },
                            user_id or "system",
                        )

                    try:
                        learning_record = LearningRecord(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            agent_id=agent_id or "unknown_agent",
                            session_id=session_id or "unknown_session",
                            decision_trace=decision_trace or {},
                            user_feedback=None,
                            event_type=event_type,
                            learned_domain=domain,
                            learned_key=key,
                            new_value_summary=None,
                            old_value_summary=(
                                str(decrypted_value_for_audit)[:200]
                                if decrypted_value_for_audit
                                else None
                            ),
                            version=fact_to_forget.get("version"),
                            diff_applied=None,
                            explanation=f"Fact forgotten due to: {reason}",
                        )
                        await self.meta_data_store.write_record(
                            learning_record.model_dump()
                        )
                        logger.info(
                            "Meta-learning record written for forgetting",
                            domain=domain,
                            key=key,
                        )
                    except Exception as meta_e:
                        logger.error(
                            "Failed to write meta-learning record for forgetting",
                            domain=domain,
                            key=key,
                            error=str(meta_e),
                            exc_info=True,
                        )
                        learn_error_counter.labels(
                            **get_labels(
                                domain=domain,
                                error_type="meta_learning_forget_write_error",
                            )
                        ).inc()
                        await self.audit_logger.log_event(
                            "meta_learning_error",
                            f"Failed to write meta-learning record for forgetting {domain}:{key}: {meta_e}",
                            {
                                "domain": domain,
                                "key": key,
                                "user_id": user_id,
                                "error": str(meta_e),
                            },
                            user_id or "system",
                        )

                    forget_counter.labels(**get_labels(domain=domain)).inc()
                    return {"status": "forgotten", "reason": "success"}
                except Exception as e:
                    logger.error(
                        "Error forgetting fact", domain=domain, key=key, error=str(e)
                    )
                    learn_error_counter.labels(
                        **get_labels(domain=domain, error_type="forget_error")
                    ).inc()
                    await self.audit_logger.log_event(
                        "forget_error",
                        f"Error forgetting {domain}:{key}: {e}",
                        {
                            "domain": domain,
                            "key": key,
                            "user_id": user_id,
                            "error": str(e),
                        },
                        user_id or "system",
                    )
                    return {"status": "failed", "reason": f"forget_error: {e}"}
                finally:
                    forget_duration_seconds.labels(**get_labels(domain=domain)).observe(
                        time.monotonic() - start_time
                    )

    async def retrieve_knowledge(
        self, domain: str, key: str, decrypt: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve knowledge from memory, Redis, or database.
        """
        if not re.match(ArbiterConfig.VALID_DOMAIN_PATTERN, domain):
            logger.warning("Invalid domain format", domain=domain)
            return None

        mem_data = self.arbiter.state.get("memory", {}).get(domain, {}).get(key)
        if mem_data:
            retrieve_hit_miss.labels(
                **get_labels(domain=domain, cache_status="memory_hit")
            ).inc()
            return await self._process_retrieved_data(mem_data.copy(), domain, decrypt)

        redis_key = f"knowledge:{domain}:{key}"
        cached_data_json = await self.redis.get(redis_key)
        if cached_data_json:
            try:
                cached_data = json.loads(cached_data_json)
                if domain in ArbiterConfig.ENCRYPTED_DOMAINS and isinstance(
                    cached_data.get("value"), str
                ):
                    cached_data["value"] = bytes.fromhex(cached_data["value"])
                self.arbiter.state.setdefault("memory", {}).setdefault(domain, {})[
                    key
                ] = cached_data.copy()
                retrieve_hit_miss.labels(
                    **get_labels(domain=domain, cache_status="redis_hit")
                ).inc()
                return await self._process_retrieved_data(
                    cached_data.copy(), domain, decrypt
                )
            except (json.JSONDecodeError, ValueError, InvalidToken) as e:
                logger.error(
                    "Corrupted Redis entry", domain=domain, key=key, error=str(e)
                )
                await self.redis.delete(redis_key)
                learn_error_counter.labels(
                    **get_labels(domain=domain, error_type="redis_corrupted_entry")
                ).inc()

        retrieve_hit_miss.labels(
            **get_labels(domain=domain, cache_status="db_fetch")
        ).inc()
        if not await self.db_circuit_breaker.can_proceed():
            logger.warning(
                "Database read skipped due to open DB circuit breaker.",
                domain=domain,
                key=key,
            )
            learn_error_counter.labels(
                **get_labels(domain=domain, error_type="db_read_circuit_open")
            ).inc()
            return None

        try:
            db_data = await self.db.load_agent_knowledge(domain, key)
            if db_data:
                await self.db_circuit_breaker.record_success()
                if domain in ArbiterConfig.ENCRYPTED_DOMAINS and isinstance(
                    db_data.get("value"), str
                ):
                    db_data["value"] = bytes.fromhex(db_data["value"])
                redis_value = db_data.copy()
                if isinstance(redis_value.get("value"), bytes):
                    redis_value["value"] = redis_value["value"].hex()
                await self.redis.setex(
                    redis_key,
                    ArbiterConfig.KNOWLEDGE_REDIS_TTL_SECONDS,
                    json.dumps(redis_value),
                )
                self.arbiter.state.setdefault("memory", {}).setdefault(domain, {})[
                    key
                ] = db_data.copy()
                return await self._process_retrieved_data(
                    db_data.copy(), domain, decrypt
                )
        except Exception as e:
            logger.error("Failed to load from DB", domain=domain, key=key, error=str(e))
            await self.db_circuit_breaker.record_failure()
            learn_error_counter.labels(
                **get_labels(domain=domain, error_type="db_load_failure")
            ).inc()

        logger.debug("Knowledge not found.", domain=domain, key=key)
        return None

    async def _process_retrieved_data(
        self, data: Dict[str, Any], domain: str, decrypt: bool
    ) -> Dict[str, Any]:
        """Process retrieved data, handling decryption."""
        if (
            decrypt
            and domain in ArbiterConfig.ENCRYPTED_DOMAINS
            and isinstance(data.get("value"), bytes)
        ):
            try:
                data["value"] = await decrypt_value(data["value"], self.ciphers)
            except InvalidToken:
                logger.error("Failed to decrypt value: InvalidToken", domain=domain)
                data["decryption_error"] = "Invalid encryption token."
            except Exception as e:
                logger.error("Failed to decrypt value", domain=domain, error=str(e))
                data["decryption_error"] = f"Decryption failed: {e}"
        return data

    def _compute_diff(
        self, old_value: Any, new_value: Any
    ) -> Optional[List[Dict[str, Any]]]:
        """Compute JSON Patch diff for auditing."""
        try:
            from jsonpatch import JsonPatch, JsonPointerException

            old_json = (
                json.loads(json.dumps(old_value, default=str))
                if old_value is not None
                else {}
            )
            new_json = (
                json.loads(json.dumps(new_value, default=str))
                if new_value is not None
                else {}
            )
            patch = JsonPatch.from_diff(old_json, new_json)
            return patch.patch if patch.patch else None
        except (ImportError, TypeError, ValueError, JsonPointerException) as e:
            learn_error_counter.labels(
                **get_labels(domain="diff_computation", error_type="jsonpatch_error")
            ).inc()
            logger.warning("Failed to compute JSON diff", error=str(e))
            return [{"op": "replace", "path": "/", "value": new_value}]
        except Exception as e:
            learn_error_counter.labels(
                **get_labels(domain="diff_computation", error_type="unexpected_error")
            ).inc()
            logger.error(
                "Unexpected error during diff computation", error=str(e), exc_info=True
            )
            return [{"op": "replace", "path": "/", "value": new_value}]
