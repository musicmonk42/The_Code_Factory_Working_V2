import logging
import uuid
import json
import hashlib
import threading
import asyncio
import time
from typing import Dict, Any, List, Optional, Coroutine, Union
from circuitbreaker import circuit
from omnicore_engine.retry_compat import retry
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime
from pydantic import BaseModel, Field, ValidationError, SecretStr

from arbiter.config import ArbiterConfig

settings = ArbiterConfig()
try:
    from omnicore_engine.plugin_registry import (
        PLUGIN_REGISTRY,
        PluginPerformanceTracker,
        ShadowDeployManager,
        PluginVersionManager,
    )
except ImportError:
    PLUGIN_REGISTRY = None
    PluginPerformanceTracker = None
    ShadowDeployManager = None
    PluginVersionManager = None
try:
    from omnicore_engine.database.database import Database
except ImportError:
    Database = None
from omnicore_engine.metrics import (
    AUDIT_RECORDS,
    AUDIT_ERRORS,
    AUDIT_RECORDS_PROCESSED_TOTAL,
    AUDIT_BUFFER_SIZE_CURRENT,
)

try:
    from omnicore_engine.core import KnowledgeGraph, safe_serialize
except ImportError:
    from omnicore_engine.core import safe_serialize

    KnowledgeGraph = None
import aiohttp

try:
    from confluent_kafka import Producer

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    Producer = None

try:
    from web3 import Web3

    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

try:
    # production import (if package present)
    from arbiter.feedback_manager import FeedbackManager, FeedbackType
except Exception:
    # Minimal fallback so tests and import-time code can patch/instantiate FeedbackManager.
    from enum import Enum

    class FeedbackType(Enum):
        BUG_REPORT = "bug_report"
        GENERAL = "general"
        # add other types used by tests if necessary

    class FeedbackManager:
        def __init__(self, *args, **kwargs):
            pass

        # tests expect record_feedback to be awaitable — keep a coroutine stub
        async def record_feedback(self, *args, **kwargs):
            return None


try:
    from omnicore_engine.policy import PolicyEngine
except Exception:
    # Minimal fallback so tests and import-time code can patch/instantiate PolicyEngine.
    class PolicyEngine:
        def __init__(self, *args, **kwargs):
            # accept both settings=... and arbiter_instance=... usage across the module
            pass

        async def should_auto_learn(self, *args, **kwargs):
            # default to allowing operations during tests / when real policy engine not present
            return True, ""


logger = logging.getLogger(__name__)


class ExplanationRationale(BaseModel):
    reason: str = Field(..., description="A concise reason for the audited event.")
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional detailed context or data supporting the reason.",
    )
    explanation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this specific explanation instance.",
    )


class SimulationOutcome(BaseModel):
    scenario_name: str = Field(
        ..., description="Name or identifier of the simulation scenario."
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict, description="Key metrics from the simulation outcome."
    )
    projected_impact: str = Field(
        ...,
        description="A summary of the projected impact (e.g., 'positive', 'negative', 'neutral').",
    )
    outcome_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this simulation outcome.",
    )


class ExplainRecord:
    def __init__(
        self,
        kind: str,
        name: str,
        detail: str,
        sim_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error: Optional[str] = None,
        context: Optional[str] = None,
        custom_attributes: Optional[str] = None,
        explanation_id: Optional[str] = None,
        root_merkle_hash: Optional[str] = None,
    ):
        self.data = {
            "kind": kind,
            "name": name,
            "detail": detail,
            "sim_id": sim_id,
            "agent_id": (
                hashlib.sha256(agent_id.encode()).hexdigest() if agent_id else None
            ),
            "error": error,
            "context": context,
            "custom_attributes": custom_attributes,
            "explanation_id": explanation_id,
            "root_merkle_hash": root_merkle_hash,
        }
        self.uuid = str(uuid.uuid4())
        self.ts = time.time()
        self.hash = self._hash_record()

    def _hash_record(self) -> str:
        data_to_hash = self.data.copy()
        for key in [
            "detail",
            "context",
            "custom_attributes",
            "explanation_id",
            "root_merkle_hash",
        ]:
            if data_to_hash.get(key) is None:
                data_to_hash[key] = ""
        return hashlib.sha256(
            json.dumps(data_to_hash, sort_keys=True, default=safe_serialize).encode(
                "utf-8"
            )
        ).hexdigest()

    def model_dump(self, exclude=None) -> Dict[str, Any]:
        return {
            "kind": self.data.get("kind"),
            "name": self.data.get("name"),
            "detail": self.data.get("detail"),
            "sim_id": self.data.get("sim_id"),
            "agent_id": self.data.get("agent_id"),
            "error": self.data.get("error"),
            "context": self.data.get("context"),
            "custom_attributes": self.data.get("custom_attributes"),
            "explanation_id": self.data.get("explanation_id"),
            "root_merkle_hash": self.data.get("root_merkle_hash"),
            "uuid": self.uuid,
            "ts": self.ts,
            "hash": self.hash,
        }


class EnhancedExplainRecord(ExplainRecord):
    def __init__(
        self,
        kind: str,
        name: str,
        detail: str,
        sim_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error: Optional[str] = None,
        context: Optional[str] = None,
        custom_attributes: Optional[str] = None,
        rationale: Optional[str] = None,
        simulation_outcomes: Optional[str] = None,
        tenant_id: Optional[str] = None,
        explanation_id: Optional[str] = None,
        root_merkle_hash: Optional[str] = None,
    ):
        super().__init__(
            kind,
            name,
            detail,
            sim_id,
            agent_id,
            error,
            context,
            custom_attributes,
            explanation_id,
            root_merkle_hash,
        )
        self.data["rationale"] = rationale
        self.data["simulation_outcomes"] = simulation_outcomes
        self.data["tenant_id"] = (
            hashlib.sha256(tenant_id.encode()).hexdigest() if tenant_id else None
        )
        self.hash = self._hash_record()

    def model_dump(self, exclude=None) -> Dict[str, Any]:
        base_dump = super().model_dump()
        base_dump["rationale"] = self.data.get("rationale")
        base_dump["simulation_outcomes"] = self.data.get("simulation_outcomes")
        base_dump["tenant_id"] = self.data.get("tenant_id")
        return base_dump


class AuditRecordSchema(BaseModel):
    kind: str = Field(..., description="The category or type of the audit event.")
    name: str = Field(..., description="The name or identifier of the entity involved.")
    detail: str = Field(
        ..., description="Encrypted string of detailed event information."
    )
    sim_id: Optional[str] = Field(
        None, description="Optional ID of a related simulation."
    )
    agent_id: Optional[str] = Field(
        None, description="Hashed identifier of the agent/component."
    )
    error: Optional[str] = Field(
        None, description="Brief error message if the event was a failure."
    )
    context: Optional[str] = Field(
        None, description="Encrypted string of additional contextual data."
    )
    custom_attributes: Optional[str] = Field(
        None, description="Encrypted string of custom, event-specific attributes."
    )
    rationale: Optional[str] = Field(
        None,
        description="Encrypted string of the rationale for the action (for enhanced records).",
    )
    simulation_outcomes: Optional[str] = Field(
        None,
        description="Encrypted string of simulated what-if outcomes (for enhanced records).",
    )
    tenant_id: Optional[str] = Field(
        None, description="Hashed tenant ID for multi-tenancy (for enhanced records)."
    )
    explanation_id: Optional[str] = Field(
        None, description="ID linking to an AI-generated explanation."
    )
    root_merkle_hash: Optional[str] = Field(
        None, description="Merkle root hash for integrity verification."
    )
    uuid: str = Field(..., description="Unique identifier for the audit record.")
    ts: float = Field(
        ..., description="Timestamp (epoch seconds) of when the record was created."
    )
    hash: str = Field(..., description="SHA256 hash of the record data for integrity.")


class CryptoValidator:
    def __init__(self):
        self.logger = logger
        self.is_key_valid = False

    def validate_fernet_key(self, key_b64_string: Union[SecretStr, str]) -> None:
        try:
            if isinstance(key_b64_string, SecretStr):
                key_str_val = key_b64_string.get_secret_value()
            elif isinstance(key_b64_string, str):
                key_str_val = key_b64_string
            else:
                raise TypeError("Key must be SecretStr or str (base64 encoded).")

            Fernet(key_str_val.encode("utf-8"))
            self.logger.info("Fernet key validated successfully")
            self.is_key_valid = True
        except Exception as e:
            self.logger.error(f"Invalid Fernet key: {e}")
            self.is_key_valid = False
            raise ValueError(f"Invalid ENCRYPTION_KEY: {e}")

    def encrypt_data(self, data: bytes) -> bytes:
        if not self.is_key_valid:
            self.logger.error(
                "Attempted to encrypt data with an invalid Fernet key. Encryption skipped."
            )
            raise ValueError("Encryption key is invalid.")
        return Fernet(
            settings.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
        ).encrypt(data)

    def decrypt_data(self, data: bytes) -> bytes:
        if not self.is_key_valid:
            self.logger.error(
                "Attempted to decrypt data with an invalid Fernet key. Decryption skipped."
            )
            raise ValueError("Encryption key is invalid.")
        return Fernet(
            settings.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
        ).decrypt(data)


class KafkaAuditStreamer:
    def __init__(self, bootstrap_servers: str, topic: str = "audit_events"):
        self.producer = None
        self.topic = topic
        self.logger = logger
        if KAFKA_AVAILABLE:
            try:
                self.producer = Producer({"bootstrap.servers": bootstrap_servers})
                self.logger.info("Kafka producer initialized")
            except Exception as e:
                self.logger.warning(
                    f"Kafka initialization failed: {e}; falling back to logging"
                )
        else:
            self.logger.warning(
                "confluent_kafka not available; Kafka streaming disabled."
            )

    async def stream_event(self, record: Dict[str, Any]):
        try:
            if self.producer:

                def delivery_report(err, msg):
                    if err is not None:
                        logger.error(f"Message delivery failed: {err}")
                        AUDIT_ERRORS.labels(operation="kafka_delivery").inc()
                    else:
                        logger.debug(
                            f"Message delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}"
                        )

                self.producer.produce(
                    self.topic,
                    json.dumps(record, default=safe_serialize).encode("utf-8"),
                    callback=delivery_report,
                )
                self.producer.poll(0)
                logger.info(f"Queued audit event for Kafka: {record.get('uuid')}")
            else:
                logger.info(
                    f"Kafka unavailable; audit event logged locally: {record.get('uuid')}"
                )
        except Exception as e:
            logger.error(f"Failed to stream audit event: {e}", exc_info=True)
            AUDIT_ERRORS.labels(operation="kafka_stream").inc()


class AuditProofExporter:
    def __init__(
        self, db: Database, encrypter: Optional[Fernet], merkle_tree: Optional[Any]
    ):
        self.db = db
        self.encrypter = encrypter
        self.merkle_tree = merkle_tree
        self.logger = logger

    async def export_proof_bundle(
        self, user_id: str, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            policy_engine = PolicyEngine(settings=settings)
            allowed, reason = await policy_engine.should_auto_learn(
                "Audit", "export_proof_bundle", user_id, {"tenant_id": tenant_id}
            )
            if not allowed:
                self.logger.warning(
                    f"Export proof bundle denied for user {user_id}: {reason}"
                )
                raise ValueError(f"Policy denied: {reason}")

            hashed_tenant_id = (
                hashlib.sha256(tenant_id.encode()).hexdigest() if tenant_id else None
            )
            filters = {"tenant_id": hashed_tenant_id} if hashed_tenant_id else {}

            records = await self.db.query_audit_records(filters=filters)

            merkle_root_hex = None
            if self.merkle_tree:
                merkle_root_hex = self.merkle_tree.get_merkle_root()

            decryption_key_value = (
                settings.ENCRYPTION_KEY.get_secret_value() if allowed else None
            )

            bundle = {
                "records": [
                    (
                        r.model_dump()
                        if isinstance(r, (ExplainRecord, EnhancedExplainRecord))
                        else r
                    )
                    for r in records
                ],
                "merkle_root": merkle_root_hex,
                "decryption_key": decryption_key_value,
                "timestamp": time.time(),
            }
            self.logger.info(
                f"Exported proof bundle for user {user_id}, tenant {tenant_id if tenant_id else 'N/A'}"
            )
            return bundle
        except Exception as e:
            self.logger.error(f"Failed to export proof bundle: {e}", exc_info=True)
            raise


class AuditSecurityGuard:
    def __init__(self):
        self.logger = logger

    def sanitize_data(self, data: Any) -> str:
        try:
            if isinstance(data, dict):
                sanitized_dict = {}
                for k, v in data.items():
                    if (
                        "password" in k.lower()
                        or "token" in k.lower()
                        or "key" in k.lower()
                    ):
                        sanitized_dict[k] = "[REDACTED]"
                    elif isinstance(v, (dict, list)):
                        sanitized_dict[k] = self.sanitize_data(v)
                    else:
                        sanitized_dict[k] = str(v)[:50] + (
                            "..." if len(str(v)) > 50 else ""
                        )
                return f"[Sanitized Dict: {json.dumps(sanitized_dict, default=safe_serialize)}]"
            elif isinstance(data, list):
                sanitized_list = [self.sanitize_data(item) for item in data[:5]]
                return f"[Sanitized List: {len(data)} items, first 5: {sanitized_list}]"
            elif isinstance(data, (str, bytes)):
                return f"[Sanitized Str: {str(data)[:100]}{'...' if len(str(data)) > 100 else ''}]"
            return f"[Sanitized Type: {type(data).__name__}]"
        except Exception as e:
            self.logger.error(f"Failed to sanitize data: {e}", exc_info=True)
            return "[Sanitization Failed]"


class AuditHookManager:
    def __init__(
        self,
        db: Database,
        performance_tracker: PluginPerformanceTracker,
        shadow_deploy_manager: ShadowDeployManager,
    ):
        self.db = db
        self.performance_tracker = performance_tracker
        self.shadow_deploy_manager = shadow_deploy_manager
        self.logger = logger

    async def capture_ab_shadow_results(
        self, kind: str, name: str, version: str
    ) -> Dict[str, Any]:
        try:
            active_plugin = PLUGIN_REGISTRY.get(kind, name)

            results = await self.performance_tracker.get_performance_history(
                kind, name, version
            )

            active_history = []
            if active_plugin:
                active_history = await self.performance_tracker.get_performance_history(
                    kind, name, active_plugin.meta.version
                )

            shadow_error_rate = sum(h.get("error_rate", 0) for h in results) / max(
                1, len(results)
            )
            shadow_exec_time_avg = sum(
                h.get("execution_time", 0) for h in results
            ) / max(1, len(results))

            active_error_rate = sum(
                h.get("error_rate", 0) for h in active_history
            ) / max(1, len(active_history))
            active_exec_time_avg = sum(
                h.get("execution_time", 0) for h in active_history
            ) / max(1, len(active_history))

            results_dict = {
                "plugin_id": f"{kind}:{name}:{version}",
                "active_version": active_plugin.meta.version if active_plugin else None,
                "shadow_version": version,
                "shadow_metrics": {
                    "error_rate": shadow_error_rate,
                    "execution_time_avg": shadow_exec_time_avg,
                },
                "active_metrics": {
                    "error_rate": active_error_rate,
                    "execution_time_avg": active_exec_time_avg,
                },
            }
            self.logger.info(
                f"Captured A/B and shadow results for {results_dict['plugin_id']}: {results_dict}"
            )
            return results_dict
        except Exception as e:
            self.logger.error(
                f"Failed to capture A/B and shadow results for {kind}:{name}:{version}: {e}",
                exc_info=True,
            )
            return {}


class ExplainAudit:
    def __init__(self, system_audit_merkle_tree: Optional[Any] = None):
        self.config = ArbiterConfig()
        self.entries: List[ExplainRecord] = []
        self.lock = threading.Lock()
        self.buffer: List[ExplainRecord] = []
        self.buffer_size = self.config.AUDIT_BUFFER_SIZE
        self.flush_interval = self.config.AUDIT_FLUSH_INTERVAL

        self.web3 = None
        self.encrypter: Optional[Fernet] = None
        self.crypto_validator = CryptoValidator()

        encryption_key_b64_string = self.config.ENCRYPTION_KEY.get_secret_value()

        try:
            self.crypto_validator.validate_fernet_key(encryption_key_b64_string)
            self.encrypter = Fernet(encryption_key_b64_string.encode("utf-8"))
        except ValueError as e:
            logger.critical(
                f"Invalid Fernet key provided to Audit system: {e}. Encryption features for audit will be unavailable or insecure.",
                exc_info=True,
            )
            self.encrypter = None
        except Exception as e:
            logger.critical(
                f"Error during Fernet key validation or initialization: {e}. Encryption features for audit will be unavailable or insecure.",
                exc_info=True,
            )
            self.encrypter = None

        self.security_guard = AuditSecurityGuard()

        self.feedback_manager = FeedbackManager(
            db_dsn=self.config.DATABASE_URL,
            redis_url=self.config.REDIS_URL,
            encryption_key=encryption_key_b64_string,
        )
        self._db_client = Database(
            self.config.DATABASE_URL, system_audit_merkle_tree=system_audit_merkle_tree
        )

        self.policy_engine = PolicyEngine(arbiter_instance=None)
        self.knowledge_graph = KnowledgeGraph()

        self.plugin_registry = PLUGIN_REGISTRY
        self.system_audit_merkle_tree = system_audit_merkle_tree

        self.performance_tracker = PluginPerformanceTracker(db=self._db_client)
        self.plugin_version_manager = PluginVersionManager(
            registry=self.plugin_registry, db=self._db_client
        )
        self.shadow_deploy_manager = ShadowDeployManager(
            registry=self.plugin_registry,
            version_manager=self.plugin_version_manager,
            performance_tracker=self.performance_tracker,
        )
        self.hook_manager = AuditHookManager(
            db=self._db_client,
            performance_tracker=self.performance_tracker,
            shadow_deploy_manager=self.shadow_deploy_manager,
        )
        self.kafka_streamer = KafkaAuditStreamer(
            bootstrap_servers=(
                self.config.KAFKA_BOOTSTRAP_SERVERS
                if hasattr(self.config, "KAFKA_BOOTSTRAP_SERVERS")
                else "localhost:9092"
            )
        )
        self.proof_exporter = AuditProofExporter(
            db=self._db_client,
            encrypter=self.encrypter,
            merkle_tree=system_audit_merkle_tree,
        )

        if WEB3_AVAILABLE:
            try:
                if self.config.WEB3_PROVIDER_URL:
                    self.web3 = Web3(
                        Web3.HTTPProvider(str(self.config.WEB3_PROVIDER_URL))
                    )
                    if not self.web3.is_connected():
                        logger.warning("Web3 provider not connected at init.")
                        self.safe_create_task(
                            self.feedback_manager.record_feedback(
                                user_id="system",
                                feedback_type=FeedbackType.BUG_REPORT,
                                details={
                                    "type": "web3_connection_error",
                                    "error": "Web3 provider not connected",
                                },
                            )
                        )
                else:
                    logger.info(
                        "WEB3_PROVIDER_URL not configured in settings. Web3 features disabled."
                    )
            except Exception as e:
                logger.error(f"Error initializing Web3: {e}", exc_info=True)
                self.safe_create_task(
                    self.feedback_manager.record_feedback(
                        user_id="system",
                        feedback_type=FeedbackType.BUG_REPORT,
                        details={"type": "web3_init_error", "error": str(e)},
                    )
                )
        else:
            logger.info("web3.py not installed, Web3 features disabled.")

        self._start_flush_task()
        logger.info("ExplainAudit initialized.")

    def encrypt_dict(self, data: Optional[Dict[str, Any]]) -> Optional[str]:
        try:
            if data is None:
                return None
            if self.encrypter is None:
                logger.warning(
                    "Attempted to encrypt data but Fernet encrypter is not initialized or invalid. Returning plaintext data."
                )
                return json.dumps(data, default=safe_serialize)

            json_string = json.dumps(data, default=safe_serialize)
            return self.encrypter.encrypt(json_string.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.error(
                f"Encryption failed for data: {self.security_guard.sanitize_data(data)}: {e}",
                exc_info=True,
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "encryption_error",
                        "operation": "encrypt_dict",
                        "error": str(e),
                    },
                )
            )
            return None

    def decrypt_str(self, encrypted_data_str: Optional[str]) -> Optional[Any]:
        try:
            if encrypted_data_str is None:
                return None
            if self.encrypter is None:
                logger.warning(
                    "Attempted to decrypt data but Fernet encrypter is not initialized or invalid. Returning None."
                )
                return None

            decrypted_bytes = self.encrypter.decrypt(encrypted_data_str.encode("utf-8"))
            return json.loads(decrypted_bytes.decode("utf-8"))
        except InvalidToken:
            logger.error(
                f"Decryption failed: Invalid token for data snippet: {encrypted_data_str[:50]}..."
            )
            return None
        except Exception as e:
            logger.error(
                f"Decryption failed for data snippet: {encrypted_data_str[:50]}...: {e}",
                exc_info=True,
            )
            return None

    def safe_create_task(self, coro: Coroutine):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(coro)
            else:
                logger.warning(
                    "Event loop not running, attempting to run coroutine directly. This might block."
                )
                try:
                    loop.run_until_complete(coro)
                except RuntimeError:
                    asyncio.run(coro)
        except RuntimeError as e:
            logger.warning(
                f"RuntimeError in safe_create_task (no event loop): {e}. Attempting asyncio.run()."
            )
            try:
                asyncio.run(coro)
            except Exception as ex:
                logger.error(
                    f"Failed to run task with asyncio.run: {ex}", exc_info=True
                )
        except Exception as e:
            logger.error(
                f"Unexpected error when trying to create/run task: {e}", exc_info=True
            )

    def add_entry(
        self,
        kind: str,
        name: str,
        detail: Dict[str, Any],
        sim_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        custom_attributes: Optional[Dict[str, Any]] = None,
        rationale: Optional[Dict[str, Any]] = None,
        simulation_outcomes: Optional[List[Dict]] = None,
        tenant_id: Optional[str] = None,
        explanation_id: Optional[str] = None,
        root_merkle_hash: Optional[str] = None,
    ):
        logger.warning(
            "Using deprecated add_entry; prefer add_entry_async for async contexts."
        )
        AUDIT_RECORDS.labels(operation="add_entry").inc()

        user_id_for_policy = agent_id if agent_id else "system"
        action_for_policy = "add_audit_entry_sync"
        metadata_for_policy = {
            "kind": kind,
            "name": name,
            "detail_summary": str(detail)[:100],
        }

        allowed = True
        reason = ""
        try:
            current_loop = None
            try:
                current_loop = asyncio.get_event_loop()
                if current_loop.is_running():
                    allowed, reason = current_loop.run_until_complete(
                        self.policy_engine.should_auto_learn(
                            user_id_for_policy, action_for_policy, metadata_for_policy
                        )
                    )
                else:
                    allowed, reason = asyncio.run(
                        self.policy_engine.should_auto_learn(
                            user_id_for_policy, action_for_policy, metadata_for_policy
                        )
                    )
            except RuntimeError:
                allowed, reason = asyncio.run(
                    self.policy_engine.should_auto_learn(
                        user_id_for_policy, action_for_policy, metadata_for_policy
                    )
                )

        except Exception as e:
            logger.error(
                f"Policy check failed for audit entry (deprecated sync call): {self.security_guard.sanitize_data(detail)}: {e}",
                exc_info=True,
            )

        if not allowed:
            logger.warning(
                f"Audit record denied by policy: {reason}. Record: {kind}:{name}."
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.GENERAL,
                    details={
                        "type": "policy_denial",
                        "record_kind": kind,
                        "reason": reason,
                    },
                )
            )
            return

        with self.lock:
            try:
                encrypted_detail = self.encrypt_dict(detail)
                encrypted_context = self.encrypt_dict(context)
                encrypted_custom_attributes = self.encrypt_dict(custom_attributes)
                encrypted_rationale = self.encrypt_dict(rationale)
                encrypted_simulation_outcomes = self.encrypt_dict(simulation_outcomes)

                current_merkle_root = None
                if self.system_audit_merkle_tree:
                    current_merkle_root = (
                        self.system_audit_merkle_tree.get_merkle_root()
                    )

                if kind in {
                    "plugin_hot_swap",
                    "plugin_rollback",
                    "plugin_shadow_deploy_predicted",
                    "promote_shadow_version",
                    "ab_shadow_audit",
                }:
                    record = EnhancedExplainRecord(
                        kind=kind,
                        name=name,
                        detail=encrypted_detail,
                        sim_id=sim_id,
                        agent_id=agent_id,
                        error=error,
                        context=encrypted_context,
                        custom_attributes=encrypted_custom_attributes,
                        rationale=encrypted_rationale,
                        simulation_outcomes=encrypted_simulation_outcomes,
                        tenant_id=tenant_id,
                        explanation_id=explanation_id,
                        root_merkle_hash=current_merkle_root,
                    )
                else:
                    record = ExplainRecord(
                        kind=kind,
                        name=name,
                        detail=encrypted_detail,
                        sim_id=sim_id,
                        agent_id=agent_id,
                        error=error,
                        context=encrypted_context,
                        custom_attributes=encrypted_custom_attributes,
                        explanation_id=explanation_id,
                        root_merkle_hash=current_merkle_root,
                    )

                try:
                    AuditRecordSchema(**record.model_dump())
                except ValidationError as e:
                    logger.error(
                        f"Audit record schema validation failed for record (UUID: {record.uuid}): {e}"
                    )
                    self.safe_create_task(
                        self.feedback_manager.record_feedback(
                            user_id="system",
                            feedback_type=FeedbackType.BUG_REPORT,
                            details={
                                "type": "audit_schema_validation_failed",
                                "record_uuid": record.uuid,
                                "error": str(e),
                                "record_data_sanitized": self.security_guard.sanitize_data(
                                    record.model_dump()
                                ),
                            },
                        )
                    )
                    return

                self.buffer.append(record)
                AUDIT_BUFFER_SIZE_CURRENT.set(len(self.buffer))

                self.safe_create_task(
                    self.kafka_streamer.stream_event(record.model_dump())
                )

                if len(self.buffer) >= self.buffer_size:
                    self.safe_create_task(self._flush_buffer())
            except Exception as e:
                logger.error(
                    f"Failed to process audit record data (sync call, UUID: {record.uuid if 'record' in locals() else 'N/A'}): {self.security_guard.sanitize_data(detail)}: {e}",
                    exc_info=True,
                )
                self.safe_create_task(
                    self.feedback_manager.record_feedback(
                        user_id="system",
                        feedback_type=FeedbackType.BUG_REPORT,
                        details={
                            "type": "audit_processing_error",
                            "operation": "add_entry_sync",
                            "error": str(e),
                            "record_kind": kind,
                            "record_name": name,
                        },
                    )
                )

    async def add_entry_async(
        self,
        kind: str,
        name: str,
        detail: Dict[str, Any],
        sim_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        custom_attributes: Optional[Dict[str, Any]] = None,
        rationale: Optional[Dict[str, Any]] = None,
        simulation_outcomes: Optional[List[Dict]] = None,
        tenant_id: Optional[str] = None,
        explanation_id: Optional[str] = None,
        root_merkle_hash: Optional[str] = None,
    ):
        AUDIT_RECORDS.labels(operation="add_entry_async").inc()

        record_data = {
            "kind": kind,
            "name": name,
            "detail": self.security_guard.sanitize_data(detail),
            "sim_id": sim_id,
            "agent_id": agent_id,
            "error": error,
            "context": context,
            "custom_attributes": custom_attributes,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if kind.startswith("arbiter"):
            async with aiohttp.ClientSession() as session:
                try:
                    await session.post(
                        self.config.ARBITTER_URL + "/audit", json=record_data
                    )
                    logger.info(f"Audit event sent to arbiter: {kind}")
                except Exception as e:
                    logger.error(f"Failed to send audit event to arbiter: {e}")

        user_id_for_policy = agent_id if agent_id else "system"
        action_for_policy = "add_audit_entry_async"
        metadata_for_policy = {
            "kind": kind,
            "name": name,
            "detail_summary": str(detail)[:100],
        }

        allowed, reason = await self.policy_engine.should_auto_learn(
            user_id_for_policy, action_for_policy, metadata_for_policy
        )
        if not allowed:
            logger.warning(
                f"Audit record denied by policy: {reason}. Record: {kind}:{name}."
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.GENERAL,
                    details={
                        "type": "policy_denial",
                        "record_kind": kind,
                        "reason": reason,
                        "record_name": name,
                    },
                )
            )
            return

        with self.lock:
            try:
                encrypted_detail = self.encrypt_dict(detail)
                encrypted_context = self.encrypt_dict(context)
                encrypted_custom_attributes = self.encrypt_dict(custom_attributes)
                encrypted_rationale = self.encrypt_dict(rationale)
                encrypted_simulation_outcomes = self.encrypt_dict(simulation_outcomes)

                current_merkle_root = None
                if self.system_audit_merkle_tree:
                    current_merkle_root = (
                        self.system_audit_merkle_tree.get_merkle_root()
                    )

                if kind in {
                    "plugin_hot_swap",
                    "plugin_rollback",
                    "plugin_shadow_deploy_predicted",
                    "promote_shadow_version",
                    "ab_shadow_audit",
                }:
                    record = EnhancedExplainRecord(
                        kind=kind,
                        name=name,
                        detail=encrypted_detail,
                        sim_id=sim_id,
                        agent_id=agent_id,
                        error=error,
                        context=encrypted_context,
                        custom_attributes=encrypted_custom_attributes,
                        rationale=encrypted_rationale,
                        simulation_outcomes=encrypted_simulation_outcomes,
                        tenant_id=tenant_id,
                        explanation_id=explanation_id,
                        root_merkle_hash=current_merkle_root,
                    )
                else:
                    record = ExplainRecord(
                        kind=kind,
                        name=name,
                        detail=encrypted_detail,
                        sim_id=sim_id,
                        agent_id=agent_id,
                        error=error,
                        context=encrypted_context,
                        custom_attributes=encrypted_custom_attributes,
                        explanation_id=explanation_id,
                        root_merkle_hash=current_merkle_root,
                    )

                try:
                    AuditRecordSchema(**record.model_dump())
                except ValidationError as e:
                    logger.error(
                        f"Audit record schema validation failed for record (UUID: {record.uuid}): {e}."
                    )
                    self.safe_create_task(
                        self.feedback_manager.record_feedback(
                            user_id="system",
                            feedback_type=FeedbackType.BUG_REPORT,
                            details={
                                "type": "audit_schema_validation_failed",
                                "record_uuid": record.uuid,
                                "error": str(e),
                                "record_data_sanitized": self.security_guard.sanitize_data(
                                    record.model_dump()
                                ),
                            },
                        )
                    )
                    return

                self.buffer.append(record)
                AUDIT_BUFFER_SIZE_CURRENT.set(len(self.buffer))

                await self.kafka_streamer.stream_event(record.model_dump())

                if len(self.buffer) >= self.buffer_size:
                    self.safe_create_task(self._flush_buffer())
            except Exception as e:
                logger.error(
                    f"Failed to process audit record data (async call, UUID: {record.uuid if 'record' in locals() else 'N/A'}): {self.security_guard.sanitize_data(detail)}: {e}",
                    exc_info=True,
                )
                self.safe_create_task(
                    self.feedback_manager.record_feedback(
                        user_id="system",
                        feedback_type=FeedbackType.BUG_REPORT,
                        details={
                            "type": "audit_processing_error",
                            "operation": "add_entry_async",
                            "error": str(e),
                            "record_kind": kind,
                            "record_name": name,
                        },
                    )
                )

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def _flush_buffer(self):
        AUDIT_RECORDS.labels(operation="flush_buffer").inc()
        start_time = time.time()
        records_to_flush = []

        with self.lock:
            if not self.buffer:
                AUDIT_BUFFER_SIZE_CURRENT.set(0)
                return
            records_to_flush = self.buffer[:]
            self.buffer.clear()
            AUDIT_BUFFER_SIZE_CURRENT.set(0)

        try:
            for record in records_to_flush:
                self.entries.append(record)

                if (
                    self.config.AUDIT_BLOCKCHAIN_ENABLED
                    and WEB3_AVAILABLE
                    and self.web3
                    and self.web3.is_connected()
                ):
                    try:
                        if self.web3.eth.accounts:
                            tx_hash = self.web3.eth.send_transaction(
                                {
                                    "from": self.web3.eth.accounts[0],
                                    "to": self.web3.eth.accounts[0],
                                    "value": 0,
                                    "data": record.hash.encode("utf-8"),
                                }
                            ).hex()
                            logger.debug(
                                f"Blockchain audit tx: {tx_hash} for record {record.uuid}."
                            )
                        else:
                            logger.warning(
                                "No Web3 accounts available for blockchain audit transaction for record %s.",
                                record.uuid,
                            )

                    except Exception as e:
                        logger.error(
                            f"Blockchain transaction failed for record {record.uuid}: {self.security_guard.sanitize_data(record.data)}: {e}",
                            exc_info=True,
                        )
                        self.safe_create_task(
                            self.feedback_manager.record_feedback(
                                user_id="system",
                                feedback_type=FeedbackType.BUG_REPORT,
                                details={
                                    "type": "blockchain_tx_failed",
                                    "record_id": record.uuid,
                                    "error": str(e),
                                },
                            )
                        )
                elif self.config.AUDIT_BLOCKCHAIN_ENABLED:
                    logger.warning(
                        "Blockchain audit enabled but Web3 not fully available or connected for record %s.",
                        record.uuid,
                    )

                if self.system_audit_merkle_tree:
                    audit_leaf_content = json.dumps(
                        record.model_dump(), sort_keys=True, default=safe_serialize
                    ).encode("utf-8")
                    self.system_audit_merkle_tree.add_leaf(audit_leaf_content)
                    self.system_audit_merkle_tree._recalculate_root()
                    logger.debug(
                        f"Merkle tree updated for record {record.uuid}. New root: {self.system_audit_merkle_tree.get_merkle_root()[:10]}..."
                    )

                await self._db_client.save_audit_record(record.model_dump())

                await self.knowledge_graph.add_fact(
                    "AuditRecords",
                    record.uuid,
                    record.model_dump(),
                    source="audit",
                    timestamp=datetime.utcnow().isoformat(),
                )

            logger.info(
                f"Flushed {len(records_to_flush)} audit records to DB/KG/Blockchain (if enabled)."
            )
            AUDIT_RECORDS_PROCESSED_TOTAL.labels(status="success").inc(
                len(records_to_flush)
            )
            AUDIT_RECORDS.labels(operation="flush_buffer_success").observe(
                time.time() - start_time
            )
        except Exception as e:
            AUDIT_RECORDS_PROCESSED_TOTAL.labels(status="failed").inc(
                len(records_to_flush)
            )
            AUDIT_ERRORS.labels(operation="flush_buffer").observe(
                time.time() - start_time
            )
            logger.error(
                f"Audit buffer flush failed: {self.security_guard.sanitize_data([r.model_dump() for r in records_to_flush])}: {e}",
                exc_info=True,
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "audit_flush_error",
                        "operation": "_flush_buffer",
                        "error": str(e),
                        "num_records_failed": len(records_to_flush),
                    },
                )
            )
            raise

    async def query_audit_records(
        self, filters: Optional[Dict[str, Any]] = None, use_dream_mode: bool = False
    ) -> List[Dict]:
        AUDIT_RECORDS.labels(operation="query_audit_records").inc()
        start_time_op = time.time()

        try:
            user_id_for_policy = "system"
            action_for_policy = "query_audit_records"
            metadata_for_policy = {"filters": filters}

            allowed, reason = await self.policy_engine.should_auto_learn(
                user_id_for_policy, action_for_policy, metadata_for_policy
            )
            if not allowed:
                logger.warning(
                    f"Audit query denied by policy: {reason}. Filters: {self.security_guard.sanitize_data(filters)}."
                )
                raise ValueError(f"Policy denied: {reason}")

            if filters and "tenant_id" in filters and filters["tenant_id"] is not None:
                filters["tenant_id"] = hashlib.sha256(
                    filters["tenant_id"].encode()
                ).hexdigest()

            result_from_db = await self._db_client.query_audit_records(
                filters=filters, use_dream_mode=False
            )
            validated_results = []
            for record_data in result_from_db:
                try:
                    record_to_process = record_data.copy()

                    record_to_process["detail"] = self.decrypt_str(
                        record_to_process.get("detail")
                    )
                    record_to_process["context"] = self.decrypt_str(
                        record_to_process.get("context")
                    )
                    record_to_process["custom_attributes"] = self.decrypt_str(
                        record_to_process.get("custom_attributes")
                    )
                    record_to_process["rationale"] = self.decrypt_str(
                        record_to_process.get("rationale")
                    )
                    record_to_process["simulation_outcomes"] = self.decrypt_str(
                        record_to_process.get("simulation_outcomes")
                    )

                    AuditRecordSchema(**record_to_process)
                    validated_results.append(record_data)
                except ValidationError as e:
                    logger.warning(
                        f"Invalid audit record encountered during replay (schema validation failed): {e}. Record UUID: {record_data.get('uuid')}. Data snippet: {self.security_guard.sanitize_data(record_data)}."
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing audit record during query for validation/decryption (UUID: {record_data.get('uuid')}): {e}. Data snippet: {self.security_guard.sanitize_data(record_data)}.",
                        exc_info=True,
                    )

            await self._log_audit(
                "query_audit_records",
                "query_" + str(uuid.uuid4()),
                "system",
                {"count": len(validated_results), "filters": filters},
            )
            return validated_results
        except Exception as e:
            AUDIT_ERRORS.labels(operation="query_audit_records").observe(
                time.time() - start_time_op
            )
            logger.error(
                f"Audit query failed: {self.security_guard.sanitize_data(filters)}: {e}",
                exc_info=True,
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "audit_error",
                        "operation": "query_audit_records",
                        "error": str(e),
                        "filters_sanitized": self.security_guard.sanitize_data(filters),
                    },
                )
            )
            raise

    async def replay_events(
        self, sim_id: str, start_time: float, end_time: float, user_id: str = "system"
    ) -> List[Dict]:
        AUDIT_RECORDS.labels(operation="replay_events").inc()
        start_time_op = time.time()
        try:
            action_for_policy = "replay_events"
            metadata_for_policy = {
                "sim_id": sim_id,
                "start_time": start_time,
                "end_time": end_time,
            }

            allowed, reason = await self.policy_engine.should_auto_learn(
                user_id, action_for_policy, metadata_for_policy
            )
            if not allowed:
                logger.warning(
                    f"Event replay denied for user {user_id}: {reason}. Sim ID: {sim_id}."
                )
                raise ValueError(f"Policy denied: {reason}")

            db_filters = {"sim_id": sim_id, "ts_start": start_time, "ts_end": end_time}
            records_to_replay_raw = await self._db_client.query_audit_records(
                db_filters
            )

            replayed_results = []

            for record_data in records_to_replay_raw:
                try:
                    record_to_process = record_data.copy()
                    record_to_process["detail"] = self.decrypt_str(
                        record_to_process.get("detail")
                    )
                    record_to_process["rationale"] = self.decrypt_str(
                        record_to_process.get("rationale")
                    )
                    record_to_process["simulation_outcomes"] = self.decrypt_str(
                        record_to_process.get("simulation_outcomes")
                    )
                    record_to_process["context"] = self.decrypt_str(
                        record_to_process.get("context")
                    )
                    record_to_process["custom_attributes"] = self.decrypt_str(
                        record_to_process.get("custom_attributes")
                    )

                    AuditRecordSchema(**record_to_process)

                    replayed_results.append(record_data)

                except ValidationError as e:
                    logger.warning(
                        f"Invalid audit record encountered during replay (schema validation failed): {e}. Record UUID: {record_data.get('uuid')}. Data snippet: {self.security_guard.sanitize_data(record_data)}."
                    )
                    AUDIT_ERRORS.labels(operation="replay_validation_failed").inc()
                except Exception as e:
                    logger.error(
                        f"Error processing audit record during replay for validation/decryption (UUID: {record_data.get('uuid')}): {e}. Data snippet: {self.security_guard.sanitize_data(record_data)}.",
                        exc_info=True,
                    )
                    self.safe_create_task(
                        self.feedback_manager.record_feedback(
                            user_id="system",
                            feedback_type=FeedbackType.BUG_REPORT,
                            details={
                                "type": "replay_error",
                                "record_id": record_data.get("uuid"),
                                "error": str(e),
                            },
                        )
                    )

            await self._log_audit(
                "replay_events",
                sim_id,
                user_id,
                {"count": len(replayed_results), "replay_filters": db_filters},
            )
            return replayed_results
        except Exception as e:
            AUDIT_ERRORS.labels(operation="replay_events").observe(
                time.time() - start_time_op
            )
            logger.error(
                f"Replay failed: {self.security_guard.sanitize_data(db_filters)}: {e}",
                exc_info=True,
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "audit_error",
                        "operation": "replay_events",
                        "error": str(e),
                        "filters_sanitized": self.security_guard.sanitize_data(
                            db_filters
                        ),
                    },
                )
            )
            raise

    async def snapshot_audit_state(self, user_id: str) -> str:
        AUDIT_RECORDS.labels(operation="snapshot_audit_state").inc()
        start_time_op = time.time()
        try:
            action_for_policy = "snapshot_audit_state"
            metadata_for_policy = {"user_id": user_id}

            allowed, reason = await self.policy_engine.should_auto_learn(
                user_id, action_for_policy, metadata_for_policy
            )
            if not allowed:
                logger.warning(f"Audit snapshot denied for user {user_id}: {reason}.")
                raise ValueError(f"Policy denied: {reason}")

            snapshot_id = str(uuid.uuid4())

            all_records_data = await self._db_client.query_audit_records(filters={})
            validated_records = []
            for record_data in all_records_data:
                try:
                    record_to_process = record_data.copy()
                    record_to_process["detail"] = self.decrypt_str(
                        record_to_process.get("detail")
                    )
                    record_to_process["rationale"] = self.decrypt_str(
                        record_to_process.get("rationale")
                    )
                    record_to_process["simulation_outcomes"] = self.decrypt_str(
                        record_to_process.get("simulation_outcomes")
                    )
                    record_to_process["context"] = self.decrypt_str(
                        record_to_process.get("context")
                    )
                    record_to_process["custom_attributes"] = self.decrypt_str(
                        record_to_process.get("custom_attributes")
                    )

                    AuditRecordSchema(**record_to_process)
                    validated_records.append(record_to_process)
                except ValidationError as e:
                    logger.warning(
                        f"Invalid audit record encountered during snapshot validation: {e}. Record UUID: {record_data.get('uuid')}. Data snippet: {self.security_guard.sanitize_data(record_data)}."
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing record for snapshot: {e}. Record UUID: {record_data.get('uuid')}.",
                        exc_info=True,
                    )

            if self.encrypter is None:
                raise RuntimeError(
                    "Audit system encrypter not initialized. Cannot create snapshot."
                )

            encrypted_records_snapshot = self.encrypter.encrypt(
                json.dumps(validated_records, default=safe_serialize).encode("utf-8")
            ).decode("utf-8")

            await self._db_client.snapshot_audit_state(
                snapshot_id, encrypted_records_snapshot, user_id
            )

            await self._log_audit(
                "snapshot_audit_state",
                snapshot_id,
                user_id,
                {
                    "records_count": len(validated_records),
                    "snapshot_size_bytes": len(
                        encrypted_records_snapshot.encode("utf-8")
                    ),
                },
            )
            return snapshot_id
        except Exception as e:
            AUDIT_ERRORS.labels(operation="snapshot_audit_state").observe(
                time.time() - start_time_op
            )
            logger.error(f"Audit state snapshot failed: {e}", exc_info=True)
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "audit_error",
                        "operation": "snapshot_audit_state",
                        "error": str(e),
                    },
                )
            )
            raise

    def _start_flush_task(self):
        async def periodic_flush_coro():
            while True:
                await asyncio.sleep(self.flush_interval)
                try:
                    await self._flush_buffer()
                except Exception as e:
                    logger.error(
                        f"Error during periodic audit flush: {e}", exc_info=True
                    )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(periodic_flush_coro())
                logger.info("Periodic audit flush task created.")
            else:
                logger.warning(
                    "No running event loop found; periodic audit flush task will not start automatically. Buffer relies on size threshold."
                )
        except RuntimeError:
            logger.warning(
                "No running event loop found at init; periodic audit flush task will not start automatically. Buffer relies on size threshold."
            )

    async def _log_audit(
        self, event: str, sim_id: str, user_id: str, details: Dict[str, Any]
    ):
        try:
            if self.system_audit_merkle_tree:
                AUDIT_RECORDS.labels(operation="log_audit_merkle").inc()

                hashed_user_id = hashlib.sha256(user_id.encode()).hexdigest()

                audit_leaf_content = json.dumps(
                    {
                        "timestamp": datetime.utcnow().isoformat(),
                        "event": event,
                        "sim_id": sim_id,
                        "user_id_hash": hashed_user_id,
                        "details_hash": hashlib.sha256(
                            json.dumps(
                                details, sort_keys=True, default=safe_serialize
                            ).encode("utf-8")
                        ).hexdigest(),
                    },
                    sort_keys=True,
                ).encode("utf-8")

                self.system_audit_merkle_tree.add_leaf(audit_leaf_content)
                self.system_audit_merkle_tree._recalculate_root()

                self.safe_create_task(
                    self.feedback_manager.record_feedback(
                        user_id="system",
                        feedback_type=FeedbackType.GENERAL,
                        details={
                            "type": "audit_event_merkle",
                            "event": event,
                            "sim_id": sim_id,
                            "details_hash": hashlib.sha256(
                                json.dumps(details, default=safe_serialize).encode()
                            ).hexdigest(),
                            "merkle_root": (
                                self.system_audit_merkle_tree.get_merkle_root()
                                if self.system_audit_merkle_tree
                                else "N/A"
                            ),
                        },
                    )
                )
            else:
                raise ValueError(
                    "system_audit_merkle_tree not available in ExplainAudit. Merkle tree logging for meta-event cannot be performed."
                )
        except Exception as e:
            logger.error(
                f"Error in _log_audit: {self.security_guard.sanitize_data(details)}: {e}",
                exc_info=True,
            )
            self.safe_create_task(
                self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "audit_log_error",
                        "event": event,
                        "error": str(e),
                    },
                )
            )
