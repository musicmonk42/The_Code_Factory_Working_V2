import os
import json
import logging
import hmac
import hashlib
import asyncio
from typing import Dict, Any, List, Optional, Union, Protocol, Type
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from opentelemetry import trace
from prometheus_client import Histogram, REGISTRY
from cryptography.fernet import Fernet, InvalidToken
import redis.asyncio as redis
from redis.exceptions import RedisError
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaError
from aiokafka.structs import TopicPartition
from pybreaker import CircuitBreaker, CircuitBreakerError

# Local application imports - assuming they exist in the project structure
from .models import Base, GrowthEventRecord, AuditLog, GrowthSnapshot
from .config_store import ConfigStore
from .exceptions import ArbiterGrowthError, AuditChainTamperedError

# --- Configuration ---
logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# --- Observability ---
# Clear any existing metric with this name
try:
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        if 'storage_latency_seconds' in REGISTRY._collector_to_names.get(collector, []):
            REGISTRY.unregister(collector)
except Exception:
    pass

# Then define the metric
STORAGE_LATENCY_SECONDS = Histogram(
    "storage_latency_seconds",
    "Latency of storage backend operations",
    ["backend", "operation"]
)

# --- Circuit Breaker Configuration ---
REDIS_BREAKER = CircuitBreaker(fail_max=5, reset_timeout=60, name="Redis")
KAFKA_BREAKER = CircuitBreaker(fail_max=5, reset_timeout=60, name="Kafka")
SQL_BREAKER = CircuitBreaker(fail_max=5, reset_timeout=60, name="SQL")


# --- Protocols and Interfaces ---
class StorageBackend(Protocol):
    """
    Defines the interface for all storage backend implementations.

    This protocol ensures that any class acting as a storage backend will have a
    consistent set of methods for saving, loading, and managing data.
    """
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc_val, exc_tb): ...

    async def load_snapshot(self, arbiter_id: str) -> Optional[Dict[str, Any]]: ...
    async def save_snapshot(self, arbiter_id: str, data: Dict[str, Any]) -> None: ...
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None: ...
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]: ...
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str: ...
    async def get_last_audit_hash(self, arbiter_id: str) -> str: ...
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]: ...


# --- Utility Functions ---
def _get_encryption_key_from_env() -> bytes:
    """Retrieves and validates the encryption key from environment variables."""
    key = os.environ.get("ARBITER_ENCRYPTION_KEY")
    if not key:
        raise ArbiterGrowthError("ARBITER_ENCRYPTION_KEY environment variable not set.")
    return key.encode('utf-8')

def _create_hmac_hash(key: bytes, *parts: str) -> str:
    """Creates a HMAC-SHA256 hash for audit log integrity."""
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for part in parts:
        mac.update(part.encode('utf-8'))
    return mac.hexdigest()

def _wrap_exception(backend_name: str):
    """Decorator to wrap backend specific exceptions into a standard format."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except (SQLAlchemyError, RedisError, KafkaError, CircuitBreakerError) as e:
                err_msg = f"{backend_name} operation '{func.__name__}' failed."
                logger.error("%s: %s", err_msg, e, exc_info=True)
                raise ArbiterGrowthError(err_msg, details={"error": str(e)}) from e
        return wrapper
    return decorator

def _normalize_event_offset(offset: Union[int, str]) -> Union[int, str]:
    """Normalizes event offset to appropriate type based on value."""
    if isinstance(offset, str):
        # Try to convert numeric strings to int
        if offset.isdigit():
            return int(offset)
        # Keep Redis stream IDs and other non-numeric strings as-is
        return offset
    return offset


# --- Backend Implementations ---

class SQLiteStorageBackend:
    """
    A storage backend using SQLite for persistent storage.
    Suitable for single-node deployments and development.
    """
    def __init__(self, config: ConfigStore):
        self.config = config
        db_url = self.config.get("sqlite.database_url", "sqlite+aiosqlite:///arbiter.db")
        self.engine = create_async_engine(db_url)
        self.session_factory = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.encryption_key = _get_encryption_key_from_env()
        self.cipher = Fernet(self.encryption_key)
        self._hash_cache = {}

    @asynccontextmanager
    async def _get_session(self) -> AsyncSession:
        session = self.session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @_wrap_exception("SQLite")
    async def start(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("SQLite backend started and schema initialized.")

    @_wrap_exception("SQLite")
    async def stop(self):
        await self.engine.dispose()
        logger.info("SQLite backend stopped.")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="load_snapshot").time()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(SQLAlchemyError))
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def load_snapshot(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        async with self._get_session() as session:
            record = await session.get(GrowthSnapshot, arbiter_id)
            if not record: return None
            try:
                # Normalize event_offset to int for SQLite
                event_offset = _normalize_event_offset(record.event_offset)
                return {
                    "arbiter_id": arbiter_id,
                    "level": record.level,
                    "skills": json.loads(self.cipher.decrypt(record.skills_encrypted)),
                    "user_preferences": json.loads(self.cipher.decrypt(record.user_preferences_encrypted)),
                    "schema_version": record.schema_version,
                    "event_offset": event_offset,
                    "experience_points": record.experience_points
                }
            except InvalidToken as e:
                logger.error("Decryption failed for snapshot of arbiter '%s'.", arbiter_id)
                raise AuditChainTamperedError("Snapshot decryption failed", details={"arbiter_id": arbiter_id}) from e

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="save_snapshot").time()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(SQLAlchemyError))
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def save_snapshot(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        async with self._get_session() as session:
            record = await session.get(GrowthSnapshot, arbiter_id) or GrowthSnapshot(arbiter_id=arbiter_id)
            record.level = data["level"]
            record.skills_encrypted = self.cipher.encrypt(json.dumps(data["skills"]).encode('utf-8'))
            record.user_preferences_encrypted = self.cipher.encrypt(json.dumps(data.get("user_preferences", {})).encode('utf-8'))
            record.schema_version = data.get("schema_version", 1.0)
            record.event_offset = str(data.get("event_offset", "0"))
            record.experience_points = data.get("experience_points", 0.0)
            session.add(record)

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="save_event").time()
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        async with self._get_session() as session:
            session.add(GrowthEventRecord(
                arbiter_id=arbiter_id,
                event_type=event["type"],
                timestamp=event["timestamp"],
                details_encrypted=self.cipher.encrypt(json.dumps(event["details"]).encode('utf-8')),
                event_version=event.get("event_version", 1.0)
            ))

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="load_events").time()
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]:
        """
        Fixed: Use ID-based filtering instead of OFFSET clause to correctly load events.
        """
        events = []
        # Fixed: Track by actual ID instead of skip count
        offset_id = int(from_offset) if isinstance(from_offset, (int, str)) and str(from_offset).isdigit() else 0
        batch_size = self.config.get("sqlite.batch_size", 1000)
        
        async with self._get_session() as session:
            while True:
                # Fixed: Filter by ID > offset_id instead of using OFFSET
                stmt = (select(GrowthEventRecord)
                       .filter_by(arbiter_id=arbiter_id)
                       .filter(GrowthEventRecord.id > offset_id)
                       .order_by(GrowthEventRecord.id)
                       .limit(batch_size))
                result = await session.execute(stmt)
                records = result.scalars().all()
                if not records: break
                for r in records:
                    try:
                        events.append({
                            "type": r.event_type,
                            "timestamp": r.timestamp,
                            "details": json.loads(self.cipher.decrypt(r.details_encrypted)),
                            "event_version": r.event_version,
                            "canonical_offset": r.id
                        })
                        offset_id = r.id  # Fixed: Update to last processed ID
                    except InvalidToken:
                        logger.warning("Skipping undecryptable event ID %d for arbiter '%s'.", r.id, arbiter_id)
                        offset_id = r.id  # Skip to next even on error
                if len(records) < batch_size: break
        return events

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="save_audit_log").time()
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        async with self._get_session() as session:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            details_str = json.dumps(details, sort_keys=True)
            current_hash = _create_hmac_hash(self.encryption_key, arbiter_id, operation, timestamp, details_str, previous_hash)
            session.add(AuditLog(
                arbiter_id=arbiter_id,
                operation=operation,
                timestamp=timestamp,
                details=details_str,
                previous_log_hash=previous_hash,
                log_hash=current_hash
            ))
            self._hash_cache[arbiter_id] = (current_hash, asyncio.get_event_loop().time() + 60)
            return current_hash

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="get_last_audit_hash").time()
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        cached = self._hash_cache.get(arbiter_id)
        if cached and cached[1] > asyncio.get_event_loop().time():
            return cached[0]
        
        async with self._get_session() as session:
            stmt = select(AuditLog.log_hash).filter_by(arbiter_id=arbiter_id).order_by(AuditLog.id.desc()).limit(1)
            last_hash = (await session.execute(stmt)).scalar_one_or_none() or "genesis_hash"
            self._hash_cache[arbiter_id] = (last_hash, asyncio.get_event_loop().time() + 60)
            return last_hash

    @STORAGE_LATENCY_SECONDS.labels(backend="sqlite", operation="load_all_audit_logs").time()
    @_wrap_exception("SQLite")
    @SQL_BREAKER
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        async with self._get_session() as session:
            stmt = select(AuditLog).filter_by(arbiter_id=arbiter_id).order_by(AuditLog.id.asc())
            return [{
                "arbiter_id": r.arbiter_id,
                "operation": r.operation,
                "timestamp": r.timestamp,
                "details": json.loads(r.details) if isinstance(r.details, str) else r.details,
                "previous_log_hash": r.previous_log_hash,
                "log_hash": r.log_hash
            } for r in (await session.execute(stmt)).scalars().all()]


class RedisStreamsStorageBackend:
    """
    A storage backend using Redis Streams for event sourcing.
    Snapshots and audit logs are stored in Redis Hashes and Lists respectively.
    """
    def __init__(self, config: ConfigStore):
        self.config = config
        self.redis_url = self.config.get("redis.url")
        if not self.redis_url:
            raise ArbiterGrowthError("Redis URL not configured.")
        self.redis = redis.from_url(self.redis_url, decode_responses=False, encoding='utf-8')
        self.encryption_key = _get_encryption_key_from_env()
        self.cipher = Fernet(self.encryption_key)
        self._hash_cache = {}

    def _key(self, arbiter_id: str, key_type: str) -> str:
        return f"arbiter:{arbiter_id}:{key_type}"

    @_wrap_exception("Redis")
    async def start(self):
        await self.redis.ping()
        logger.info("Redis backend connected successfully.")

    @_wrap_exception("Redis")
    async def stop(self):
        await self.redis.close()
        logger.info("Redis backend connection closed.")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="load_snapshot").time()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(RedisError))
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def load_snapshot(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        snapshot_key = self._key(arbiter_id, "snapshot")
        data = await self.redis.hgetall(snapshot_key)
        if not data:
            return None
        try:
            # Keep event_offset as string for Redis (stream IDs)
            event_offset = data[b'event_offset'].decode('utf-8')
            return {
                "arbiter_id": arbiter_id,
                "level": int(data[b'level']),
                "skills": json.loads(self.cipher.decrypt(data[b'skills_encrypted'])),
                "user_preferences": json.loads(self.cipher.decrypt(data[b'user_preferences_encrypted'])),
                "schema_version": float(data[b'schema_version']),
                "event_offset": event_offset,
                "experience_points": float(data.get(b'experience_points', b'0'))
            }
        except (InvalidToken, KeyError) as e:
            logger.error("Decryption or data error for Redis snapshot '%s'.", snapshot_key)
            raise AuditChainTamperedError("Snapshot is corrupt", details={"key": snapshot_key}) from e

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="save_snapshot").time()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(RedisError))
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def save_snapshot(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        snapshot_key = self._key(arbiter_id, "snapshot")
        # Use transaction to save all fields atomically
        pipe = self.redis.pipeline(transaction=True)
        await pipe.hset(snapshot_key, mapping={
            b'level': data["level"],
            b'skills_encrypted': self.cipher.encrypt(json.dumps(data["skills"]).encode('utf-8')),
            b'user_preferences_encrypted': self.cipher.encrypt(json.dumps(data.get("user_preferences", {})).encode('utf-8')),
            b'schema_version': data.get("schema_version", 1.0),
            b'event_offset': str(data.get("event_offset", "0")),
            b'experience_points': data.get("experience_points", 0.0)
        })
        await pipe.execute()

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="save_event").time()
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        stream_key = self._key(arbiter_id, "events")
        event_data = {
            b'type': event["type"].encode('utf-8'),
            b'timestamp': event["timestamp"].encode('utf-8'),
            b'details_encrypted': self.cipher.encrypt(json.dumps(event["details"]).encode('utf-8')),
            b'event_version': str(event.get("event_version", 1.0)).encode('utf-8')
        }
        await self.redis.xadd(stream_key, event_data)

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="load_events").time()
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = '0-0') -> List[Dict[str, Any]]:
        """
        Fixed: Improved loop termination condition and reduced block time to avoid hanging.
        """
        events = []
        stream_key = self._key(arbiter_id, "events")
        last_id = from_offset if isinstance(from_offset, str) else '0-0'
        
        while True:
            # Fixed: Reduced block time from 2000ms to 100ms to avoid hanging
            response = await self.redis.xread({stream_key: last_id}, count=1000, block=100)
            # Fixed: Check if we got any messages properly
            if not response or not response[0][1]:
                break
            for msg_id, msg_data in response[0][1]:
                try:
                    events.append({
                        "type": msg_data[b'type'].decode('utf-8'),
                        "timestamp": msg_data[b'timestamp'].decode('utf-8'),
                        "details": json.loads(self.cipher.decrypt(msg_data[b'details_encrypted'])),
                        "event_version": float(msg_data[b'event_version']),
                        "canonical_offset": msg_id.decode('utf-8')
                    })
                except InvalidToken:
                    logger.warning("Skipping undecryptable event ID %s in stream '%s'.", msg_id.decode('utf-8'), stream_key)
            # Removed redundant check that would never trigger
            last_id = response[0][1][-1][0]
        return events

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="save_audit_log").time()
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        details_str = json.dumps(details, sort_keys=True)
        current_hash = _create_hmac_hash(self.encryption_key, arbiter_id, operation, timestamp, details_str, previous_hash)
        log_entry = json.dumps({
            "arbiter_id": arbiter_id,
            "operation": operation,
            "timestamp": timestamp,
            "details": details_str,
            "previous_hash": previous_hash,
            "log_hash": current_hash
        }).encode('utf-8')
        await self.redis.rpush(self._key(arbiter_id, "audit"), log_entry)
        self._hash_cache[arbiter_id] = (current_hash, asyncio.get_event_loop().time() + 60)
        return current_hash

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="get_last_audit_hash").time()
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        cached = self._hash_cache.get(arbiter_id)
        if cached and cached[1] > asyncio.get_event_loop().time():
            return cached[0]
        
        last_log_json = await self.redis.lindex(self._key(arbiter_id, "audit"), -1)
        if not last_log_json:
            return "genesis_hash"
        last_hash = json.loads(last_log_json).get("log_hash", "genesis_hash")
        self._hash_cache[arbiter_id] = (last_hash, asyncio.get_event_loop().time() + 60)
        return last_hash

    @STORAGE_LATENCY_SECONDS.labels(backend="redis", operation="load_all_audit_logs").time()
    @_wrap_exception("Redis")
    @REDIS_BREAKER
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        logs_json = await self.redis.lrange(self._key(arbiter_id, "audit"), 0, -1)
        logs = []
        for log_bytes in logs_json:
            log_dict = json.loads(log_bytes)
            # Parse the details field if it's a JSON string
            if isinstance(log_dict.get("details"), str):
                log_dict["details"] = json.loads(log_dict["details"])
            logs.append(log_dict)
        return logs


class KafkaStorageBackend:
    """
    A storage backend using Kafka for event sourcing and audit logging.
    This backend is highly scalable but has performance trade-offs for
    snapshot loading and retrieving the last audit hash.
    """
    def __init__(self, config: ConfigStore):
        self.config = config
        self.producer: Optional[AIOKafkaProducer] = None
        self.encryption_key = _get_encryption_key_from_env()
        self.cipher = Fernet(self.encryption_key)
        self._hash_cache = {}
        
        # Extract Kafka connection settings from config
        self.bootstrap_servers = self.config.get("kafka.bootstrap_servers")
        if not self.bootstrap_servers:
            raise ArbiterGrowthError("Kafka bootstrap_servers not configured.")
        
        self.kafka_security_configs = {
            k.replace("kafka.", ""): v for k, v in self.config.get_all().items() if k.startswith("kafka.")
        }

    def _topic(self, arbiter_id: str, topic_type: str) -> str:
        return f"arbiter.{arbiter_id}.{topic_type}"

    @_wrap_exception("Kafka")
    async def start(self):
        self.producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers, **self.kafka_security_configs)
        await self.producer.start()
        logger.info("Kafka producer started.")

    @_wrap_exception("Kafka")
    async def stop(self):
        if self.producer:
            await self.producer.stop()
        logger.info("Kafka producer stopped.")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="load_snapshot").time()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(KafkaError))
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def load_snapshot(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        topic = self._topic(arbiter_id, "snapshots")
        consumer = AIOKafkaConsumer(
            bootstrap_servers=self.bootstrap_servers, 
            auto_offset_reset='earliest',
            group_id=f"snapshot-loader-{arbiter_id}-{os.urandom(4).hex()}",
            **self.kafka_security_configs
        )
        await consumer.start()
        try:
            partitions = consumer.partitions_for_topic(topic)
            if not partitions:
                return None
            
            tp_list = [TopicPartition(topic, p) for p in partitions]
            end_offsets = await consumer.end_offsets(tp_list)
            
            latest_message = None
            for tp in tp_list:
                offset = end_offsets.get(tp)
                if offset and offset > 0:
                    consumer.seek(tp, offset - 1)
                    msg = await consumer.getone(tp)
                    if not latest_message or msg.offset > latest_message.offset:
                        latest_message = msg
            
            if not latest_message:
                return None
            
            decrypted = self.cipher.decrypt(latest_message.value)
            snapshot_data = json.loads(decrypted)
            # Ensure consistent offset handling
            if "event_offset" in snapshot_data:
                snapshot_data["event_offset"] = _normalize_event_offset(snapshot_data["event_offset"])
            return snapshot_data
        except (InvalidToken, KeyError) as e:
            logger.error("Decryption or data error for Kafka snapshot in topic '%s'.", topic)
            raise AuditChainTamperedError("Snapshot is corrupt", details={"topic": topic}) from e
        finally:
            await consumer.stop()

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="save_snapshot").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def save_snapshot(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        if not self.producer:
            raise ArbiterGrowthError("Kafka producer not started.")
        topic = self._topic(arbiter_id, "snapshots")
        # Ensure event_offset is stored as string
        save_data = data.copy()
        save_data["event_offset"] = str(save_data.get("event_offset", "0"))
        payload = self.cipher.encrypt(json.dumps(save_data).encode('utf-8'))
        await self.producer.send_and_wait(topic, payload, key=arbiter_id.encode('utf-8'))

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="save_event").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        if not self.producer:
            raise ArbiterGrowthError("Kafka producer not started.")
        topic = self._topic(arbiter_id, "events")
        payload = self.cipher.encrypt(json.dumps(event).encode('utf-8'))
        await self.producer.send_and_wait(topic, payload, key=arbiter_id.encode('utf-8'))

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="load_events").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]:
        # NOTE: Kafka from_offset is complex. This implementation assumes a simple integer
        # offset from the beginning of partition 0, which is not robust for multi-partition topics.
        # A production system would need a mapping like {"0": 123, "1": 456}.
        topic = self._topic(arbiter_id, "events")
        events = []
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap_servers,
            auto_offset_reset='earliest',
            group_id=f"event-loader-{arbiter_id}-{os.urandom(4).hex()}",
            **self.kafka_security_configs
        )
        await consumer.start()
        try:
            # Simple offset handling for demonstration
            if isinstance(from_offset, int) and from_offset > 0:
                tp = TopicPartition(topic, 0)
                consumer.seek(tp, from_offset)

            while True:
                batch = await consumer.getmany(timeout_ms=1000, max_records=500)
                if not batch:
                    break
                for tp, messages in batch.items():
                    for msg in messages:
                        try:
                            decrypted = self.cipher.decrypt(msg.value)
                            event_data = json.loads(decrypted)
                            event_data["canonical_offset"] = f"{msg.partition}:{msg.offset}"
                            events.append(event_data)
                        except InvalidToken:
                            logger.warning("Skipping undecryptable event at offset %s in topic '%s'.", 
                                         f"{msg.partition}:{msg.offset}", topic)
                if not any(batch.values()):
                    break
        finally:
            await consumer.stop()
        return events

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="save_audit_log").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        if not self.producer:
            raise ArbiterGrowthError("Kafka producer not started.")
        topic = self._topic(arbiter_id, "audit")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        details_str = json.dumps(details, sort_keys=True)
        current_hash = _create_hmac_hash(self.encryption_key, arbiter_id, operation, timestamp, details_str, previous_hash)
        log_entry = {
            "arbiter_id": arbiter_id,
            "operation": operation,
            "timestamp": timestamp,
            "details": details_str,
            "previous_hash": previous_hash,
            "log_hash": current_hash
        }
        payload = json.dumps(log_entry).encode('utf-8')
        await self.producer.send_and_wait(topic, payload, key=arbiter_id.encode('utf-8'))
        self._hash_cache[arbiter_id] = (current_hash, asyncio.get_event_loop().time() + 60)
        return current_hash

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="get_last_audit_hash").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        cached = self._hash_cache.get(arbiter_id)
        if cached and cached[1] > asyncio.get_event_loop().time():
            return cached[0]
        
        # This is inefficient in Kafka. For production, the hash should be stored
        # in a fast K/V store like Redis or a database.
        topic = self._topic(arbiter_id, "audit")
        consumer = AIOKafkaConsumer(
            bootstrap_servers=self.bootstrap_servers,
            group_id=f"hash-loader-{arbiter_id}-{os.urandom(4).hex()}",
            **self.kafka_security_configs
        )
        await consumer.start()
        try:
            partitions = consumer.partitions_for_topic(topic)
            if not partitions:
                return "genesis_hash"
            
            tp_list = [TopicPartition(topic, p) for p in partitions]
            end_offsets = await consumer.end_offsets(tp_list)
            
            latest_message = None
            for tp in tp_list:
                offset = end_offsets.get(tp)
                if offset and offset > 0:
                    consumer.seek(tp, offset - 1)
                    msg = await consumer.getone(tp)
                    if not latest_message or msg.offset > latest_message.offset:
                        latest_message = msg
            
            if not latest_message:
                return "genesis_hash"
            
            log_entry = json.loads(latest_message.value)
            last_hash = log_entry.get("log_hash", "genesis_hash")
            self._hash_cache[arbiter_id] = (last_hash, asyncio.get_event_loop().time() + 60)
            return last_hash
        finally:
            await consumer.stop()

    @STORAGE_LATENCY_SECONDS.labels(backend="kafka", operation="load_all_audit_logs").time()
    @_wrap_exception("Kafka")
    @KAFKA_BREAKER
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        topic = self._topic(arbiter_id, "audit")
        logs = []
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap_servers,
            auto_offset_reset='earliest',
            group_id=f"audit-loader-{arbiter_id}-{os.urandom(4).hex()}",
            **self.kafka_security_configs
        )
        await consumer.start()
        try:
            while True:
                batch = await consumer.getmany(timeout_ms=2000, max_records=500)
                if not any(batch.values()):
                    break
                for tp, messages in batch.items():
                    for msg in messages:
                        log_entry = json.loads(msg.value)
                        # Parse details if it's a string
                        if isinstance(log_entry.get("details"), str):
                            log_entry["details"] = json.loads(log_entry["details"])
                        logs.append(log_entry)
        finally:
            await consumer.stop()
        return logs


def storage_backend_factory(config: ConfigStore) -> StorageBackend:
    """
    Factory function to create a storage backend based on configuration.
    """
    backend_type = config.get("storage.backend", "sqlite").lower()
    
    if backend_type == "sqlite":
        return SQLiteStorageBackend(config)
    elif backend_type == "redis":
        return RedisStreamsStorageBackend(config)
    elif backend_type == "kafka":
        return KafkaStorageBackend(config)
    else:
        raise ValueError(f"Unknown storage backend type: {backend_type}")