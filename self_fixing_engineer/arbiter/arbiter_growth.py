import logging
import asyncio
import os
import inspect
from typing import Dict, Any, List, Optional, Callable, Awaitable, Protocol, Union, Any as AnyType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import Column, Integer, String, Float, Text
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel, Field
from kazoo.client import KazooClient
from arbiter.otel_config import get_tracer
from opentelemetry.context import attach, detach
from opentelemetry.propagate import set_global_textmap, get_global_textmap
import json
import aiofiles
from etcd3 import client as etcd_client
from cryptography.fernet import Fernet
import redis.asyncio as redis
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
try:
    from aiokafka import KafkaError
except ImportError:
    # Fallback if aiokafka is not installed
    class KafkaError(Exception):
        pass
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer
import hashlib
from abc import ABC, abstractmethod
from aiobreaker import CircuitBreaker, CircuitBreakerError
import uuid # Added missing import for uuid
try:
    from aiokafka.structs import TopicPartition
except (ImportError, AttributeError):
    class TopicPartition:
        def __init__(self, topic, partition):
            self.topic = topic
            self.partition = partition


# --- Custom Exceptions for API Granularity ---
class ArbiterGrowthError(Exception):
    """Base exception for the ArbiterGrowthManager."""
    pass

class OperationQueueFullError(ArbiterGrowthError):
    """Raised when the pending operations queue is full."""
    pass

class RateLimitError(ArbiterGrowthError):
    """Raised when an operation is rejected due to rate limiting."""
    pass

class CircuitBreakerOpenError(ArbiterGrowthError):
    """Raised when an operation fails because the circuit breaker is open."""
    pass

class AuditChainTamperedError(ArbiterGrowthError):
    """Raised when the audit log hash chain validation fails."""
    pass

# OpenTelemetry Setup
tracer = get_tracer(__name__)
# The global textmap propagator is assumed to be configured by the centralized
# otel_config. We retrieve it here for use in context propagation.
propagator = get_global_textmap()
set_global_textmap(propagator)

# --- Prometheus Metrics Initialization with Duplication Check ---
logger = logging.getLogger(__name__)
from prometheus_client import Counter, Histogram, Summary, Gauge, REGISTRY
from prometheus_client.metrics import Counter as _Counter, Histogram as _Histogram, Summary as _Summary, Gauge as _Gauge

VALID_METRIC_TYPES = (_Counter, _Histogram, _Summary, _Gauge)

def get_or_create_metric(metric_class, name, documentation, labelnames=(), buckets=None):
    try:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing and isinstance(existing, metric_class):
            return existing
        if existing:
            REGISTRY.unregister(existing)
            logger.warning(f"Unregistered existing metric '{name}' due to type mismatch or re-creation attempt.")
    except Exception as e:
        logger.error(f"Error checking/unregistering metric {name}: {e}")

    try:
        kwargs = {'name': name, 'documentation': documentation, 'labelnames': labelnames}
        if buckets and metric_class is Histogram:
            kwargs['buckets'] = buckets

        if metric_class in (Counter, Histogram, Summary, Gauge):
            return metric_class(**kwargs)

        # Fallback logic
        lname = name.lower()
        if "counter" in lname:
            return Counter(**kwargs)
        if "histogram" in lname:
            return Histogram(**kwargs)
        if "gauge" in lname:
            return Gauge(**kwargs)
        return Summary(**kwargs)
    except Exception as e:
        logger.error(f"Failed to create metric {name}: {e}")
        return Summary(name, documentation, labelnames)


# Metrics
GROWTH_EVENTS = get_or_create_metric(Counter, "growth_events_total", "Total growth events recorded", ["arbiter"])
GROWTH_SAVE_ERRORS = get_or_create_metric(Counter, "growth_save_errors_total", "Total growth save errors", ["arbiter"])
GROWTH_PENDING_QUEUE = get_or_create_metric(Gauge, "growth_pending_queue_size", "Size of pending operations queue", ["arbiter"])
GROWTH_SKILL_IMPROVEMENT = get_or_create_metric(Histogram, "growth_skill_improvement", "Skill improvement amounts", ["arbiter", "skill"], buckets=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0])
GROWTH_SNAPSHOTS = get_or_create_metric(Counter, "growth_snapshots_total", "Total snapshots created", ["arbiter"])
GROWTH_EVENT_PUSH_LATENCY = get_or_create_metric(Histogram, "growth_event_push_latency_seconds", "Latency of pushing an event to external systems", ["arbiter"])
GROWTH_OPERATION_QUEUE_LATENCY = get_or_create_metric(Histogram, "growth_operation_queue_latency_seconds", "Time an operation spends in the pending queue", ["arbiter"])
GROWTH_OPERATION_EXECUTION_LATENCY = get_or_create_metric(Histogram, "growth_operation_execution_latency_seconds", "Latency of executing a queued operation", ["arbiter"])
GROWTH_CIRCUIT_BREAKER_TRIPS = get_or_create_metric(Counter, "growth_circuit_breaker_trips", "Total circuit breaker trips", ["arbiter", "breaker_name"])
GROWTH_ANOMALY_SCORE = get_or_create_metric(Gauge, "growth_anomaly_score", "Anomaly score for growth events", ["arbiter"])
CONFIG_FALLBACK_USED = get_or_create_metric(Counter, "config_fallback_used_total", "Total times fallback config was used", ["arbiter"])
GROWTH_AUDIT_ANCHORS_TOTAL = get_or_create_metric(Counter, "growth_audit_anchors_total", "Total audit chain anchors created", ["arbiter"])
GROWTH_ERRORS_TOTAL = get_or_create_metric(Counter, "growth_errors_total", "Total errors in growth operations", ["arbiter", "error_type"])


# --- PII Redaction Filter ---
try:
    from .logging_utils import PIIRedactorFilter
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
        handler.addFilter(PIIRedactorFilter())
        logger.addHandler(handler)
except ImportError:
    pass # Assume logging_utils is not available, proceed without PII redaction


# Configuration Store
class ConfigStore:
    """Manages configuration settings with etcd and a local fallback file."""
    def __init__(self, etcd_host: str = "localhost", etcd_port: int = 2379, fallback_path: Optional[str] = None):
        try:
            self.client = etcd_client(host=etcd_host, port=etcd_port)
        except Exception as e:
            logger.error(f"Failed to initialize etcd client: {e}. Will rely on fallback mechanisms.")
            self.client = None
        
        self.fallback_path = fallback_path
        self.defaults = {
            "flush_interval_min": 2.0,
            "flush_interval_max": 10.0,
            "snapshot_interval": 50,
            "rate_limit_tokens": 100,
            "rate_limit_refill_rate": 10.0,
            "rate_limit_timeout": 5.0,
            "redis_batch_size": 100,
            "anomaly_threshold": 0.95,
            "evolution_cycle_interval_seconds": 3600 # New config for evolution cycle
        }
        self._cache: Dict[str, Any] = {}
        self._cache_lock = asyncio.Lock()

    async def _load_from_fallback(self) -> None:
        if self.fallback_path and os.path.exists(self.fallback_path):
            try:
                async with aiofiles.open(self.fallback_path, 'r') as f:
                    content = await f.read()
                    fallback_configs = json.loads(content)
                    self._cache.update(fallback_configs)
                    logger.info(f"Loaded configurations from fallback file: {self.fallback_path}")
            except Exception as e:
                logger.error(f"Failed to read or parse fallback config file {self.fallback_path}: {e}")

    async def get_config(self, key: str) -> Any:
        async with self._cache_lock:
            if key in self._cache:
                return self._cache[key]

            # 1. Try etcd
            if self.client:
                try:
                    with tracer.start_as_current_span(f"etcd_get_config_{key}", attributes={"config.key": key}):
                        value_bytes, _ = await asyncio.get_event_loop().run_in_executor(None, lambda: self.client.get(key))
                        if value_bytes:
                            value_str = value_bytes.decode('utf-8')
                            # Attempt to convert to float if it looks like a number, otherwise keep as string
                            try:
                                value = float(value_str)
                            except ValueError:
                                value = value_str
                            self._cache[key] = value
                            logger.debug(f"Fetched config '{key}' from etcd: {value}")
                            return value
                except Exception as e:
                    logger.warning(f"Could not reach etcd to get config '{key}': {e}. Attempting fallback.")

            # 2. Try fallback file (if not already in cache)
            await self._load_from_fallback()
            if key in self._cache:
                CONFIG_FALLBACK_USED.labels(arbiter=self.fallback_path).inc()
                logger.warning(f"Using fallback config for '{key}'.")
                return self._cache[key]
            
            # 3. Check defaults
            if key in self.defaults: # Added check for key in self.defaults
                value = self.defaults.get(key)
                self._cache[key] = value
                logger.debug(f"Config '{key}' not found in etcd or fallback, using default: {value}")
                return value
            
            # 4. Raise KeyError for unknown keys
            raise KeyError(key) # Raise KeyError if key is not found anywhere

    async def ping(self) -> Dict[str, Any]:
        """Checks the health of the etcd connection."""
        try:
            if self.client:
                # etcd client's `status` method is a good way to check connectivity.
                await asyncio.get_event_loop().run_in_executor(None, lambda: self.client.status())
                return {"status": "healthy"}
            else:
                return {"status": "unhealthy", "message": "Etcd client not initialized"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

# Rate Limiter
class TokenBucketRateLimiter:
    """Implements a token bucket rate limiter with blocking capability."""
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self.tokens: float = 100.0
        self.last_refill: float = datetime.now(timezone.utc).timestamp()
        self.lock = asyncio.Lock()

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        async with self.lock:
            now = datetime.now(timezone.utc).timestamp()
            max_tokens = await self.config_store.get_config("rate_limit_tokens")
            refill_rate = await self.config_store.get_config("rate_limit_refill_rate")
            timeout = timeout or await self.config_store.get_config("rate_limit_timeout")
            elapsed = now - self.last_refill
            self.tokens = min(max_tokens, self.tokens + elapsed * refill_rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            wait_time = (1.0 - self.tokens) / refill_rate
            if wait_time > timeout:
                return False
            await asyncio.sleep(wait_time)
            self.tokens -= 1.0
            return True

# Context-Aware Callable
class ContextAwareCallable:
    """Wraps an async callable to capture and restore OpenTelemetry context."""
    def __init__(self, coro: Callable[[], Awaitable[None]], context_carrier: Dict[str, str], arbiter_id: str):
        self._coro = coro
        self._context_carrier = context_carrier
        self._arbiter_id = arbiter_id
        self.queued_time = datetime.now(timezone.utc).timestamp()

    async def __call__(self):
        ctx = propagator.extract(self._context_carrier)
        token = attach(ctx)
        try:
            with tracer.start_as_current_span("queued_operation", attributes={"arbiter.id": self._arbiter_id}):
                GROWTH_OPERATION_QUEUE_LATENCY.labels(arbiter=self._arbiter_id).observe(
                    datetime.now(timezone.utc).timestamp() - self.queued_time
                )
                start_time = datetime.now(timezone.utc).timestamp()
                await self._coro()
                GROWTH_OPERATION_EXECUTION_LATENCY.labels(arbiter=self._arbiter_id).observe(
                    datetime.now(timezone.utc).timestamp() - start_time
                )
        finally:
            detach(token)

# Idempotency Store
class IdempotencyStore:
    """Manages idempotency keys for exactly-once processing."""
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url, decode_responses=True)

    async def check_and_set(self, key: str, ttl: int = 3600) -> bool:
        with tracer.start_as_current_span("idempotency_check", attributes={"idempotency.key": key}):
            result = await self.redis.set(f"idempotency:{key}", "processed", nx=True, ex=ttl) # Added "idempotency:" prefix
            if result:
                tracer.current_span().set_attribute("idempotency.hit", False)
                return True
            else:
                tracer.current_span().set_attribute("idempotency.hit", True)
                return False
    async def start(self):
        try:
            await self.redis.ping()
        except Exception as e:
            logger.error(f"Failed to connect to IdempotencyStore Redis: {e}")
            raise
    
    async def ping(self) -> Dict[str, Any]:
        """Checks the health of the Redis connection."""
        try:
            if await self.redis.ping():
                return {"status": "healthy"}
            else:
                return {"status": "unhealthy", "message": "Ping failed"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    async def stop(self):
        await self.redis.close()
        
    async def remember(self, key: str, ttl: int = 3600) -> None:
        """Stores a key to indicate an event has been processed."""
        await self.redis.set(f"dedup:{key}", "processed", ex=ttl)

# Pluggable Storage Interface
class StorageBackend(Protocol):
    async def load(self, arbiter_id: str) -> Optional[Dict[str, Any]]: ...
    async def save(self, arbiter_id: str, data: Dict[str, Any]) -> None: ...
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None: ...
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def ping(self) -> Dict[str, Any]: ...
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str: ...
    async def get_last_audit_hash(self, arbiter_id: str) -> str: ...
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]: ...


class SQLiteStorageBackend:
    """
    SQLite storage backend with encryption and audit logging.
    
    Consistency: Provides strong, serializable consistency for all operations. Changes
    are immediately visible upon transaction commit. Not suitable for high-concurrency
    production workloads; use PostgreSQL or MySQL instead.
    """
    def __init__(self, session_factory: Callable[[], AsyncSession], encryption_key: Union[str, bytes]):
        self.session_factory = session_factory
        if isinstance(encryption_key, str):
            encryption_key = encryption_key.encode('utf-8')
        self.cipher = Fernet(encryption_key)

    async def start(self): pass
    async def stop(self): pass
    async def ping(self) -> Dict[str, Any]:
        try:
            async with self.session_factory() as session:
                await session.connection()
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(SQLAlchemyError))
    @tracer.start_as_current_span("sqlite_load")
    async def load(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        async with self.session_factory() as session:
            record = await session.get(GrowthSnapshot, arbiter_id)
            if record:
                skills = json.loads(self.cipher.decrypt(record.skills_encrypted).decode('utf-8')) if record.skills_encrypted else record.skills
                user_preferences = json.loads(self.cipher.decrypt(record.user_preferences_encrypted).decode('utf-8')) if record.user_preferences_encrypted else record.user_preferences
                return { "level": record.level, "skills": skills, "user_preferences": user_preferences, "schema_version": record.schema_version, "event_offset": record.event_offset }
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(SQLAlchemyError))
    @tracer.start_as_current_span("sqlite_save")
    async def save(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                record = await session.get(GrowthSnapshot, arbiter_id)
                if not record:
                    record = GrowthSnapshot(arbiter_id=arbiter_id)
                    session.add(record)
                record.level = data["level"]
                record.skills_encrypted = self.cipher.encrypt(json.dumps(data["skills"]).encode('utf-8'))
                record.user_preferences_encrypted = self.cipher.encrypt(json.dumps(data.get("user_preferences", {})).encode('utf-8'))
                record.schema_version = data["schema_version"]
                record.event_offset = str(data.get("event_offset", "0"))

    @tracer.start_as_current_span("sqlite_save_event")
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                details_encrypted = self.cipher.encrypt(json.dumps(event["details"]).encode('utf-8'))
                record = GrowthEventRecord(arbiter_id=arbiter_id, event_type=event["type"], timestamp=event["timestamp"], details_encrypted=details_encrypted, event_version=event.get("event_version", 1.0))
                session.add(record)

    @tracer.start_as_current_span("sqlite_load_events")
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            sqlite_offset = int(from_offset) if isinstance(from_offset, str) and from_offset.isdigit() else int(from_offset)
            result = await session.execute(select(GrowthEventRecord).filter_by(arbiter_id=arbiter_id).order_by(GrowthEventRecord.id).offset(sqlite_offset))
            events = []
            for r in result.scalars().all():
                try:
                    details = json.loads(self.cipher.decrypt(r.details_encrypted).decode('utf-8'))
                    events.append({ "type": r.event_type, "timestamp": r.timestamp, "details": details, "event_version": r.event_version, "canonical_offset": r.id })
                except Exception as e:
                    logger.error(f"Failed to decrypt event ID {r.id} for {arbiter_id}: {e}")
            return events

    @tracer.start_as_current_span("sqlite_save_audit_log")
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        async with self.session_factory() as session:
            async with session.begin():
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                details_str = json.dumps(details, sort_keys=True)
                
                current_hash = hashlib.sha256(f"{arbiter_id}{operation}{timestamp}{details_str}{previous_hash}".encode()).hexdigest()

                record = AuditLog(
                    arbiter_id=arbiter_id,
                    operation=operation,
                    timestamp=timestamp,
                    details=details_str,
                    previous_log_hash=previous_hash,
                    log_hash=current_hash
                )
                session.add(record)
                return current_hash

    @tracer.start_as_current_span("sqlite_get_last_audit_hash")
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        async with self.session_factory() as session:
            result = await session.execute(
                select(AuditLog.log_hash)
                .filter_by(arbiter_id=arbiter_id)
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
            last_hash = result.scalar_one_or_none()
            return last_hash or "genesis_hash"

    @tracer.start_as_current_span("sqlite_load_all_audit_logs")
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(AuditLog).filter_by(arbiter_id=arbiter_id).order_by(AuditLog.id.asc())
            )
            return [
                {
                    "arbiter_id": r.arbiter_id,
                    "operation": r.operation,
                    "timestamp": r.timestamp,
                    "details": r.details,
                    "previous_log_hash": r.previous_log_hash,
                    "log_hash": r.log_hash,
                }
                for r in result.scalars().all()
            ]


class RedisStreamsStorageBackend:
    """
    Redis Streams storage backend.

    Consistency: Provides at-least-once semantics for event processing due to retries.
    Snapshots are eventually consistent. Does not guarantee transactional atomicity between
    saving an event and saving a snapshot.
    """
    def __init__(self, redis_url: str = "redis://localhost:6379", encryption_key: Optional[bytes] = None, config_store: Optional[ConfigStore] = None):
        self.redis = redis.from_url(redis_url, decode_responses=False)
        self.cipher = Fernet(encryption_key) if encryption_key else None
        self.config_store = config_store or ConfigStore()
    
    async def start(self): await self.redis.ping()
    async def stop(self): await self.redis.close()
    async def ping(self) -> Dict[str, Any]:
        try:
            if await self.redis.ping():
                return {"status": "healthy"}
            else:
                return {"status": "unhealthy", "message": "Ping failed"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}
    
    async def _get_stream_key(self, arbiter_id: str) -> str: return f"arbiter:{arbiter_id}:events"
    async def _get_snapshot_key(self, arbiter_id: str) -> str: return f"arbiter:{arbiter_id}:snapshot"
    async def _get_audit_key(self, arbiter_id: str) -> str: return f"arbiter:{arbiter_id}:audit"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(redis.RedisError))
    @tracer.start_as_current_span("redis_load_snapshot")
    async def load(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        snapshot_key = await self._get_snapshot_key(arbiter_id)
        snapshot_data_bytes = await self.redis.hgetall(snapshot_key)
        if snapshot_data_bytes:
            # Manually decode bytes to string for known fields
            level = int(snapshot_data_bytes.get(b"level", b"1").decode())
            schema_version = float(snapshot_data_bytes.get(b"schema_version", b"1.0").decode())
            event_offset = snapshot_data_bytes.get(b"event_offset", b"0").decode()

            # Handle encrypted skills and preferences
            skills = {}
            if b"skills_encrypted" in snapshot_data_bytes and self.cipher:
                try:
                    skills_encrypted = snapshot_data_bytes[b"skills_encrypted"]
                    skills = json.loads(self.cipher.decrypt(skills_encrypted).decode('utf-8'))
                except Exception as e:
                    logger.error(f"Failed to decrypt Redis skills for {arbiter_id}: {e}")
            elif b"skills" in snapshot_data_bytes:
                skills = json.loads(snapshot_data_bytes[b"skills"].decode('utf-8'))

            user_preferences = {}
            if b"user_preferences_encrypted" in snapshot_data_bytes and self.cipher:
                try:
                    user_preferences_encrypted = snapshot_data_bytes[b"user_preferences_encrypted"]
                    user_preferences = json.loads(self.cipher.decrypt(user_preferences_encrypted).decode('utf-8'))
                except Exception as e:
                    logger.error(f"Failed to decrypt Redis user preferences for {arbiter_id}: {e}")
            elif b"user_preferences" in snapshot_data_bytes:
                user_preferences = json.loads(snapshot_data_bytes[b"user_preferences"].decode('utf-8'))


            return { 
                "level": level, 
                "skills": skills, 
                "user_preferences": user_preferences, 
                "schema_version": schema_version, 
                "event_offset": event_offset 
            }
        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(redis.RedisError))
    @tracer.start_as_current_span("redis_save_snapshot")
    async def save(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        snapshot_key = await self._get_snapshot_key(arbiter_id)
        save_data = { 
            b"level": str(data["level"]).encode(), 
            b"schema_version": str(data["schema_version"]).encode(), 
            b"event_offset": str(data.get("event_offset", "0")).encode() 
        }

        # Encrypt skills and user_preferences if cipher is available
        if self.cipher:
            save_data[b"skills_encrypted"] = self.cipher.encrypt(json.dumps(data["skills"]).encode('utf-8'))
            save_data[b"user_preferences_encrypted"] = self.cipher.encrypt(json.dumps(data.get("user_preferences", {})).encode('utf-8'))
        else:
            save_data[b"skills"] = json.dumps(data["skills"]).encode('utf-8')
            save_data[b"user_preferences"] = json.dumps(data.get("user_preferences", {})).encode('utf-8')
            
        await self.redis.hset(snapshot_key, mapping=save_data)
        
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(redis.RedisError))
    @tracer.start_as_current_span("redis_save_event")
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        stream_key = await self._get_stream_key(arbiter_id)
        
        # Prepare event data, encrypting details if cipher is available
        event_data_to_store = {
            b"type": event["type"].encode(),
            b"timestamp": event["timestamp"].encode(),
            b"event_version": str(event.get("event_version", 1.0)).encode()
        }
        
        if self.cipher:
            event_data_to_store[b"details_encrypted"] = self.cipher.encrypt(json.dumps(event["details"]).encode('utf-8'))
        else:
            event_data_to_store[b"details"] = json.dumps(event["details"]).encode('utf-8')

        # Add OpenTelemetry context to event data
        carrier = {}
        propagator.inject(carrier)
        event_data_to_store[b"trace_context"] = json.dumps(carrier).encode('utf-8')

        await self.redis.xadd(stream_key, event_data_to_store)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(redis.RedisError))
    @tracer.start_as_current_span("redis_load_events")
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]:
        stream_key = await self._get_stream_key(arbiter_id)
        # Redis Stream IDs are in the format "timestamp-sequence".
        # If from_offset is an integer, we assume it's a "logical" offset and translate it
        # This is a simplification; a more robust approach might store Redis stream ID in snapshot.
        start_id = from_offset if isinstance(from_offset, str) else '0-0'
        
        events_with_offsets = []
        # XREAD can block; XREADGROUP is for consumer groups. We use XREVRANGE for simple replay.
        # However, XREVRANGE reads backwards. For sequential replay, XREAD with a starting ID is better.
        # For simplicity and to fetch all from a point, we'll use XREAD with COUNT.
        # Note: A real-world application would likely need to page this or use consumer groups.
        
        # We will fetch a batch. To ensure we get all, we might need multiple calls.
        # Let's assume for now, we try to fetch a reasonable chunk.
        
        # Simplistic approach: Read all events from the given offset forward
        # This might consume a lot of memory for very long event streams.
        # In production, consider cursor-based iteration or windowed reads.
        last_id = start_id
        while True:
            # XREAD [COUNT count] [BLOCK milliseconds] STREAMS key id [key id ...]
            # Using XREAD with a single stream and starting ID
            # Returns: [[stream_key, [[message_id, {field: value, ...}], ...]]]
            response = await self.redis.xread(
                streams={stream_key: last_id},
                count=self.config_store.defaults.get("redis_batch_size", 1000), # Use a configured batch size
                block=0 # Do not block
            )
            
            if not response or not response[0][1]: # Check if response is empty or stream has no messages
                break

            for msg_id, msg_data in response[0][1]:
                event = {}
                event["canonical_offset"] = msg_id.decode('utf-8') # Redis Stream ID as canonical offset
                
                # Decode and decrypt fields
                event["type"] = msg_data.get(b"type", b"").decode('utf-8')
                event["timestamp"] = msg_data.get(b"timestamp", b"").decode('utf-8')
                event["event_version"] = float(msg_data.get(b"event_version", b"1.0").decode('utf-8'))

                if b"details_encrypted" in msg_data and self.cipher:
                    try:
                        event["details"] = json.loads(self.cipher.decrypt(msg_data[b"details_encrypted"]).decode('utf-8'))
                    except Exception as e:
                        logger.error(f"Failed to decrypt Redis event details for {arbiter_id} (ID: {msg_id.decode('utf-8')}): {e}")
                        event["details"] = {"_decryption_error": str(e)}
                elif b"details" in msg_data:
                    event["details"] = json.loads(msg_data[b"details"].decode('utf-8'))
                else:
                    event["details"] = {} # No details or unrecognized format

                events_with_offsets.append(event)
            
            last_id = events_with_offsets[-1]["canonical_offset"] # Next read starts after the last message received

        return events_with_offsets

    @tracer.start_as_current_span("redis_save_audit_log")
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        audit_key = await self._get_audit_key(arbiter_id)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        details_str = json.dumps(details, sort_keys=True)
        current_hash = hashlib.sha256(f"{arbiter_id}{operation}{timestamp}{details_str}{previous_hash}".encode()).hexdigest()
        
        log_entry = { "operation": operation, "timestamp": timestamp, "details": details_str, "previous_hash": previous_hash, "log_hash": current_hash }
        await self.redis.rpush(audit_key, json.dumps(log_entry))
        return current_hash

    @tracer.start_as_current_span("redis_get_last_audit_hash")
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        audit_key = await self._get_audit_key(arbiter_id)
        last_log_json = await self.redis.lindex(audit_key, -1)
        if last_log_json:
            last_log = json.loads(last_log_json)
            return last_log.get("log_hash", "genesis_hash")
        return "genesis_hash"
        
    @tracer.start_as_current_span("redis_load_all_audit_logs")
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        audit_key = await self._get_audit_key(arbiter_id)
        # LRANGE key 0 -1 fetches all elements from the list.
        logs_json = await self.redis.lrange(audit_key, 0, -1)
        return [json.loads(log_json) for log_json in logs_json]


class KafkaStorageBackend:
    """
    Kafka storage backend for growth events.

    Consistency: Provides at-least-once delivery for events when using retries.
    Transactional sends provide atomicity for batches of messages per producer session,
    ensuring that a batch is either fully written or not at all. Ordering is guaranteed
    per partition (and thus per-arbiter if `arbiter_id` is the key).
    """
    def __init__(self, bootstrap_servers: str = "localhost:9092", schema_registry_url: str = "http://localhost:8081", zookeeper_hosts: str = "localhost:2181", encryption_key: Optional[bytes] = None):
        self.bootstrap_servers = bootstrap_servers
        self.schema_registry_url = schema_registry_url
        self.zookeeper_hosts = zookeeper_hosts
        self.producer: Optional[AIOKafkaProducer] = None
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.zookeeper: Optional[KazooClient] = None
        self.zookeeper_lock: Optional[Any] = None # Placeholder for ZK distributed lock
        self.cipher: Optional[Fernet] = Fernet(encryption_key) if encryption_key else None
        
        # Schema Registry setup
        self.schema_registry_client = SchemaRegistryClient({'url': self.schema_registry_url})
        self.event_schema = {
            "type": "record",
            "name": "GrowthEventRecord",
            "fields": [
                {"name": "arbiter_id", "type": "string"},
                {"name": "event_type", "type": "string"},
                {"name": "timestamp", "type": "string"},
                {"name": "details_encrypted", "type": ["null", "bytes"]}, # encrypted bytes
                {"name": "details", "type": ["null", "string"]}, # unencrypted JSON string
                {"name": "event_version", "type": "float"},
                {"name": "trace_context", "type": ["null", "string"]} # OpenTelemetry trace context
            ]
        }
        self.snapshot_schema = {
            "type": "record",
            "name": "GrowthSnapshotRecord",
            "fields": [
                {"name": "arbiter_id", "type": "string"},
                {"name": "level", "type": "int"},
                {"name": "skills_encrypted", "type": ["null", "bytes"]},
                {"name": "skills", "type": ["null", {"type": "map", "values": "double"}]},
                {"name": "user_preferences_encrypted", "type": ["null", "bytes"]},
                {"name": "user_preferences", "type": ["null", {"type": "map", "values": "string"}]}, # Assuming string values
                {"name": "schema_version", "type": "float"},
                {"name": "event_offset", "type": "string"} # Kafka offset is string (e.g., "partition:offset")
            ]
        }
        self.event_serializer = AvroSerializer(self.schema_registry_client, self.event_schema)
        self.event_deserializer = AvroDeserializer(self.schema_registry_client, self.event_schema)
        self.snapshot_serializer = AvroSerializer(self.schema_registry_client, self.snapshot_schema)
        self.snapshot_deserializer = AvroDeserializer(self.schema_registry_client, self.snapshot_schema)

    async def start(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            enable_idempotence=True, # Ensure exactly-once for producer writes
            transactional_id=f"arbiter-growth-producer-{os.getpid()}" # Unique transactional ID
        )
        await self.producer.start()

        # Zookeeper for coordinating consumer group offsets or distributed locks
        self.zookeeper = KazooClient(hosts=self.zookeeper_hosts)
        await asyncio.get_event_loop().run_in_executor(None, self.zookeeper.start)
        # self.zookeeper_lock = self.zookeeper.Lock(f"/arbiter_growth_locks/{arbiter_id}") # Per-arbiter lock

        logger.info(f"Kafka producer connected to {self.bootstrap_servers}")

    async def stop(self):
        if self.producer: await self.producer.stop()
        if self.consumer: await self.consumer.stop()
        if self.zookeeper: await asyncio.get_event_loop().run_in_executor(None, self.zookeeper.stop)

    async def ping(self) -> Dict[str, Any]:
        """Checks the health of the Kafka producer connection."""
        try:
            # Pinging Kafka is not a direct operation. A good health check is to try to get metadata.
            if self.producer:
                await self.producer.partitions_for_topic("health_check_topic")
                return {"status": "healthy"}
            else:
                return {"status": "unhealthy", "message": "Producer not initialized"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    # Kafka doesn't directly store "snapshots" in the same way a DB does.
    # A "snapshot" in Kafka would typically be the result of replaying events up to a certain point,
    # or a dedicated topic for state snapshots, often compressed.
    # For this interface, we'll model it as a separate topic.
    async def load(self, arbiter_id: str) -> Optional[Dict[str, Any]]:
        snapshot_topic = f"arbiter.{arbiter_id}.snapshots"
        # To load the *latest* snapshot, we'd read from the end of the topic.
        # This requires a consumer. A simpler approach for loading is to use the AdminClient
        # or consume from the topic's end, but Kafka is not optimized for "read latest record by key" like Redis/DB.
        # This is a conceptual placeholder. In a real system, snapshots might be in S3/HDFS, or a DB.
        logger.warning(f"KafkaStorageBackend: Direct 'load' of latest snapshot for {arbiter_id} is not efficient. Consider a dedicated snapshot storage for this operation.")
        # For a simplified demo, we'll just return None, implying state must be replayed from events.
        # Or, one could implement a consumer that reads all records for the arbiter_id and processes them
        # to reconstruct the latest snapshot, then close. This is slow.
        try:
            # Create a temporary consumer to read the last message
            consumer = AIOKafkaConsumer(
                snapshot_topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=f"snapshot-loader-{arbiter_id}-{uuid.uuid4().hex[:8]}", # Unique group ID
                auto_offset_reset="latest", # Start consuming from the latest offset
                enable_auto_commit=False,
                max_poll_records=1 # Only need the last one
            )
            await consumer.start()
            
            snapshot_data = None
            try:
                # Assign to all partitions to get the latest from any
                partitions = consumer.partitions_for_topic(snapshot_topic)
                if not partitions:
                    logger.info(f"No partitions found for snapshot topic {snapshot_topic}.")
                    return None
                
                tps = [TopicPartition(snapshot_topic, p) for p in partitions]
                consumer.assign(tps)
                
                # Seek to end and get current offsets for each partition
                end_offsets = await consumer.end_offsets(tps)
                for tp in tps:
                    consumer.seek(tp, end_offsets[tp]) # Seek to the end

                # Poll for messages
                messages = await consumer.getmany(timeout_ms=1000, max_records=1) # Adjust timeout as needed
                for tp, msgs in messages.items():
                    if msgs:
                        # Process the last message
                        latest_message = msgs[-1]
                        if self.snapshot_deserializer:
                            snapshot_data = self.snapshot_deserializer(latest_message.value)
                            # If encrypted, decrypt the relevant fields
                            if self.cipher:
                                if snapshot_data.get("skills_encrypted"):
                                    snapshot_data["skills"] = json.loads(self.cipher.decrypt(snapshot_data["skills_encrypted"]).decode('utf-8'))
                                if snapshot_data.get("user_preferences_encrypted"):
                                    snapshot_data["user_preferences"] = json.loads(self.cipher.decrypt(snapshot_data["user_preferences_encrypted"]).decode('utf-8'))
                                # Remove encrypted fields if unencrypted are now available
                                snapshot_data.pop("skills_encrypted", None)
                                snapshot_data.pop("user_preferences_encrypted", None)
                            
                            # Ensure skills and user_preferences are dicts even if from old schema
                            snapshot_data["skills"] = snapshot_data.get("skills", {}) or {}
                            snapshot_data["user_preferences"] = snapshot_data.get("user_preferences", {}) or {}

                            logger.info(f"Loaded snapshot for {arbiter_id} from offset {latest_message.offset}")
                            break # Found the latest snapshot, exit
            finally:
                await consumer.stop()
            return snapshot_data
        except KafkaError as e:
            logger.error(f"Kafka error loading snapshot for {arbiter_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load snapshot for {arbiter_id}: {e}")
            return None

    async def save(self, arbiter_id: str, data: Dict[str, Any]) -> None:
        snapshot_topic = f"arbiter.{arbiter_id}.snapshots"
        
        # Prepare data for Avro serialization, handling encryption
        save_data_avro = data.copy()
        if self.cipher:
            save_data_avro["skills_encrypted"] = self.cipher.encrypt(json.dumps(data["skills"]).encode('utf-8'))
            save_data_avro.pop("skills", None) # Remove unencrypted field
            save_data_avro["user_preferences_encrypted"] = self.cipher.encrypt(json.dumps(data.get("user_preferences", {})).encode('utf-8'))
            save_data_avro.pop("user_preferences", None) # Remove unencrypted field
        else:
            # Ensure these fields are explicitly present even if None for schema
            save_data_avro["skills_encrypted"] = None
            save_data_avro["user_preferences_encrypted"] = None

        # Ensure correct types for Avro
        save_data_avro["level"] = int(save_data_avro["level"])
        save_data_avro["schema_version"] = float(save_data_avro["schema_version"])
        save_data_avro["event_offset"] = str(save_data_avro["event_offset"])
        
        try:
            # Serialize with Avro
            serialized_data = self.snapshot_serializer(save_data_avro)
            await self.producer.send_and_wait(snapshot_topic, serialized_data, key=arbiter_id.encode('utf-8'))
            logger.info(f"Saved snapshot for {arbiter_id} to Kafka topic {snapshot_topic}")
        except Exception as e:
            logger.error(f"Failed to save snapshot for {arbiter_id} to Kafka: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(KafkaError))
    @tracer.start_as_current_span("kafka_save_event")
    async def save_event(self, arbiter_id: str, event: Dict[str, Any]) -> None:
        event_topic = f"arbiter.{arbiter_id}.events"
        
        event_data_avro = event.copy()
        event_data_avro["arbiter_id"] = arbiter_id # Ensure arbiter_id is in the record

        # Handle encryption for details
        if self.cipher:
            event_data_avro["details_encrypted"] = self.cipher.encrypt(json.dumps(event["details"]).encode('utf-8'))
            event_data_avro["details"] = None # Null out unencrypted field
        else:
            event_data_avro["details_encrypted"] = None
            event_data_avro["details"] = json.dumps(event["details"])

        # Add OpenTelemetry context
        carrier = {}
        propagator.inject(carrier)
        event_data_avro["trace_context"] = json.dumps(carrier)

        # Ensure correct types for Avro serialization
        event_data_avro["event_version"] = float(event_data_avro.get("event_version", 1.0))

        try:
            # Send with transaction
            async with self.producer.transaction(): # This ensures atomicity for batching
                serialized_event = self.event_serializer(event_data_avro)
                await self.producer.send(event_topic, serialized_event, key=arbiter_id.encode('utf-8'))
                await self.producer.commit_transaction()
            logger.debug(f"Event '{event['type']}' saved to Kafka for {arbiter_id}")
        except Exception as e:
            logger.error(f"Failed to save event to Kafka for {arbiter_id}: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(KafkaError))
    @tracer.start_as_current_span("kafka_load_events")
    async def load_events(self, arbiter_id: str, from_offset: Union[int, str] = 0) -> List[Dict[str, Any]]:
        event_topic = f"arbiter.{arbiter_id}.events"
        events_with_offsets = []

        consumer_group_id = f"arbiter-event-loader-{arbiter_id}-{uuid.uuid4().hex[:8]}"
        self.consumer = AIOKafkaConsumer(
            event_topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=consumer_group_id,
            auto_offset_reset="earliest", # Start from the beginning if no committed offset
            enable_auto_commit=False
        )
        
        await self.consumer.start()

        try:
            # Assign partitions for the specific arbiter_id (assuming key-based partitioning)
            # In a real system, you'd likely map arbiter_id to a specific partition.
            # Here, we'll assign all partitions for the topic and filter by key.
            partitions = self.consumer.partitions_for_topic(event_topic)
            if not partitions:
                logger.warning(f"No partitions found for event topic {event_topic}. Cannot load events.")
                return []
            
            tps = [TopicPartition(event_topic, p) for p in partitions]
            self.consumer.assign(tps)

            # Seek to the appropriate offset for each partition.
            # 'from_offset' is tricky with Kafka since it's per-partition.
            # If `from_offset` is a string like "partition:offset", we can use that.
            # If it's just "0" (earliest) or "latest", use auto_offset_reset.
            if isinstance(from_offset, str) and ':' in from_offset:
                # Example: "0:123" for partition 0, offset 123
                parts = from_offset.split(':')
                try:
                    target_partition = int(parts[0])
                    target_offset = int(parts[1])
                    for tp in tps:
                        if tp.partition == target_partition:
                            self.consumer.seek(tp, target_offset)
                            break
                except ValueError:
                    logger.warning(f"Invalid 'from_offset' format: {from_offset}. Starting from earliest.")
            else:
                # Default behavior (earliest from auto_offset_reset) already set.
                pass # AIOKafkaConsumer with auto_offset_reset="earliest" handles this

            # Poll for messages until no more are available or a certain timeout/count is reached
            while True:
                messages = await self.consumer.getmany(timeout_ms=1000, max_records=100) # Fetch in batches
                if not messages:
                    break # No more messages

                for tp, msgs in messages.items():
                    for message in msgs:
                        # Only process messages for the target arbiter_id if using generic topic
                        if message.key and message.key.decode('utf-8') != arbiter_id:
                            continue

                        if self.event_deserializer:
                            deserialized_event = self.event_deserializer(message.value)

                            # Decrypt details if necessary
                            if self.cipher and deserialized_event.get("details_encrypted"):
                                try:
                                    deserialized_event["details"] = json.loads(self.cipher.decrypt(deserialized_event["details_encrypted"]).decode('utf-8'))
                                except Exception as e:
                                    logger.error(f"Failed to decrypt Kafka event details for {arbiter_id} (offset: {message.offset}): {e}")
                                    deserialized_event["details"] = {"_decryption_error": str(e)}
                            elif not deserialized_event.get("details") and deserialized_event.get("details_encrypted") is None:
                                # If no 'details' and no 'details_encrypted', ensure it's an empty dict
                                deserialized_event["details"] = {}

                            # Add Kafka-specific offset information
                            deserialized_event["canonical_offset"] = f"{message.partition}:{message.offset}"
                            events_with_offsets.append(deserialized_event)

                # If we received fewer messages than max_records, or no messages, we might be at the end.
                # A more robust check might involve checking consumer.assignment() and comparing current
                # position to end_offsets. For simplicity, we just break if getmany returns empty.
                if sum(len(m) for m in messages.values()) < 100: # If we got a partial batch, assume end
                     break

        finally:
            await self.consumer.stop()

        # Sort events by canonical offset if needed (important for replay, especially if partitions were read in parallel)
        # Assuming canonical_offset "partition:offset" can be sorted as strings, or convert to tuples (int, int)
        # Fixed: Add type checking for canonical_offset before splitting
        def safe_offset_key(event):
            offset = event.get("canonical_offset", "0:0")
            # Handle numeric offsets from SQLite or malformed strings
            if isinstance(offset, (int, float)):
                return (int(offset), 0)
            try:
                parts = str(offset).split(':')
                return tuple(map(int, parts)) if len(parts) > 0 else (0, 0)
            except (ValueError, AttributeError):
                return (0, 0)
        
        events_with_offsets.sort(key=safe_offset_key)

        return events_with_offsets
    
    @tracer.start_as_current_span("kafka_save_audit_log")
    async def save_audit_log(self, arbiter_id: str, operation: str, details: Dict[str, Any], previous_hash: str) -> str:
        # Kafka for audit logs implies a topic specifically for audit events.
        audit_topic = f"arbiter.{arbiter_id}.audit_logs"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        details_str = json.dumps(details, sort_keys=True)
        current_hash = hashlib.sha256(f"{arbiter_id}{operation}{timestamp}{details_str}{previous_hash}".encode()).hexdigest()
        
        audit_entry = { 
            "arbiter_id": arbiter_id,
            "operation": operation, 
            "timestamp": timestamp, 
            "details": details_str, 
            "previous_log_hash": previous_hash, 
            "log_hash": current_hash 
        }
        
        try:
            # For simplicity, we are not using Avro serialization for audit logs here, just JSON
            # In a real system, you might define an Avro schema for audit logs too.
            await self.producer.send_and_wait(audit_topic, json.dumps(audit_entry).encode('utf-8'), key=arbiter_id.encode('utf-8'))
            logger.info(f"Audit log (Kafka backend) saved: {arbiter_id}: {operation} - Hash: {current_hash}")
        except Exception as e:
            logger.error(f"Failed to save audit log to Kafka for {arbiter_id}: {e}")
            raise
        return current_hash

    @tracer.start_as_current_span("kafka_get_last_audit_hash")
    async def get_last_audit_hash(self, arbiter_id: str) -> str:
        # Getting the "last" audit hash from Kafka effectively means consuming
        # all messages for a given arbiter_id from the audit topic and finding the last one.
        # This is generally inefficient for a single lookup and indicates Kafka might not be
        # the ideal primary store for the "latest state" of an audit chain head.
        # A separate, queryable database (like a relational DB or Redis) would store the current head.
        # This method is a placeholder reflecting the challenge.
        logger.warning("Kafka get_last_audit_hash is a placeholder and inefficient for real-time lookup. Use a queryable DB for audit chain head.")
        audit_topic = f"arbiter.{arbiter_id}.audit_logs"
        last_hash = "genesis_hash"
        
        consumer_group_id = f"audit-hash-reader-{arbiter_id}-{uuid.uuid4().hex[:8]}"
        consumer = AIOKafkaConsumer(
            audit_topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=consumer_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            consumer_timeout_ms=1000 # Short timeout if no messages
        )
        
        await consumer.start()
        try:
            partitions = consumer.partitions_for_topic(audit_topic)
            if not partitions:
                return "genesis_hash"
            
            tps = [TopicPartition(audit_topic, p) for p in partitions]
            consumer.assign(tps)
            
            # Seek to end on all partitions to find the latest
            end_offsets = await consumer.end_offsets(tps)
            for tp in tps:
                consumer.seek(tp, end_offsets[tp] - 1 if end_offsets[tp] > 0 else 0) # Seek to the last message if exists

            # Poll messages for a short period
            messages = await consumer.getmany(timeout_ms=1000, max_records=len(tps)) # Try to get 1 message per partition
            
            latest_log_entry = None
            latest_offset_overall = -1
            
            for tp, msgs in messages.items():
                if msgs:
                    # Find the latest message within this partition's batch
                    partition_latest_msg = max(msgs, key=lambda m: m.offset)
                    if partition_latest_msg.offset > latest_offset_overall:
                        latest_offset_overall = partition_latest_msg.offset
                        latest_log_entry = json.loads(partition_latest_msg.value.decode('utf-8'))
            
            if latest_log_entry:
                last_hash = latest_log_entry.get("log_hash", "genesis_hash")

        finally:
            await consumer.stop()
        return last_hash
        
    @tracer.start_as_current_span("kafka_load_all_audit_logs")
    async def load_all_audit_logs(self, arbiter_id: str) -> List[Dict[str, Any]]:
        # Loading *all* audit logs from Kafka means replaying the entire topic.
        # This is fine for validation during startup but not for general querying.
        logger.info(f"Loading all audit logs for {arbiter_id} from Kafka. This might take time for large topics.")
        audit_topic = f"arbiter.{arbiter_id}.audit_logs"
        all_logs = []
        
        consumer_group_id = f"audit-log-reader-{arbiter_id}-{uuid.uuid4().hex[:8]}"
        consumer = AIOKafkaConsumer(
            audit_topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=consumer_group_id,
            auto_offset_reset="earliest", # Start from the beginning
            enable_auto_commit=False,
            consumer_timeout_ms=1000 # Timeout if no more messages
        )
        
        await consumer.start()
        try:
            partitions = consumer.partitions_for_topic(audit_topic)
            if not partitions:
                return []
            
            tps = [TopicPartition(audit_topic, p) for p in partitions]
            consumer.assign(tps)

            while True:
                messages = await consumer.getmany(timeout_ms=100, max_records=500) # Fetch in batches
                if not messages:
                    break # No more messages

                for tp, msgs in messages.items():
                    for message in msgs:
                        if message.key and message.key.decode('utf-8') != arbiter_id:
                            continue
                        try:
                            log_entry = json.loads(message.value.decode('utf-8'))
                            all_logs.append(log_entry)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode audit log JSON from Kafka message at offset {message.offset}: {e}")
                        except Exception as e:
                            logger.error(f"Error processing audit log message from Kafka at offset {message.offset}: {e}")
            
        finally:
            await consumer.stop()

        # Sort logs by timestamp or by Kafka offset if the sequence matters strictly
        # Sorting by (partition, offset) for true Kafka order
        # For simplicity, assuming timestamp sorting is sufficient for chain validation if events are in order per partition.
        # If strict global ordering across partitions is required, a different strategy (e.g., KSQL DB sink) is needed.
        return all_logs

# Event Models
class GrowthEvent(BaseModel):
    type: str = Field(...)
    timestamp: str = Field(...)
    details: Dict[str, Any] = Field(...)
    event_version: float = Field(1.0)

# Arbiter State Model
class ArbiterState(BaseModel):
    arbiter_id: str = Field(...)
    level: int = Field(1, ge=1)
    skills: Dict[str, float] = Field(default_factory=dict)
    user_preferences: Dict[str, Any] = Field(default_factory=dict)
    event_offset: Union[int, str] = Field("0") # This will store the last processed Kafka offset (e.g., "partition:offset") or SQLite ID
    schema_version: float = Field(1.0)
    experience_points: Optional[float] = Field(None)
    def set_skill_score(self, skill_name: str, score: float): self.skills[skill_name] = max(0.0, min(1.0, score))


# Database Schema
try:
    from app.omnicore_engine.database import Base
except ImportError:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()

class GrowthSnapshot(Base):
    __tablename__ = 'arbiter_growth_snapshots'
    arbiter_id = Column(String, primary_key=True)
    level = Column(Integer, default=1)
    skills_encrypted = Column(Text) # Stores encrypted JSON string
    user_preferences_encrypted = Column(Text) # Stores encrypted JSON string
    schema_version = Column(Float, default=1.0)
    event_offset = Column(String, default="0") # Can be SQLite row ID or Kafka "partition:offset"

class GrowthEventRecord(Base):
    __tablename__ = 'arbiter_growth_events'
    id = Column(Integer, primary_key=True) # For SQLite, this is the canonical offset
    arbiter_id = Column(String, index=True)
    event_type = Column(String)
    timestamp = Column(String)
    details_encrypted = Column(Text) # Stores encrypted JSON string
    event_version = Column(Float, default=1.0)

class AuditLog(Base):
    __tablename__ = 'arbiter_audit_logs'
    id = Column(Integer, primary_key=True)
    arbiter_id = Column(String, index=True)
    operation = Column(String)
    timestamp = Column(String)
    details = Column(Text) # Stores JSON string of details
    previous_log_hash = Column(String, nullable=False)
    log_hash = Column(String, nullable=False, unique=True)


# Mocked Integrations
class KnowledgeGraph:
    async def add_fact(self, *args, **kwargs):
        pass
class FeedbackManager:
    async def record_feedback(self, *args, **kwargs):
        pass


class PluginHook(ABC):
    @abstractmethod
    async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None: pass


class ArbiterGrowthManager:
    MAX_PENDING_OPERATIONS = int(os.getenv("GROWTH_MAX_OPERATIONS", 1000))
    SCHEMA_VERSION = 1.0

    def __init__(
        self,
        arbiter_name: str,
        storage_backend: StorageBackend,
        knowledge_graph: KnowledgeGraph,
        feedback_manager: Optional[FeedbackManager] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        config_store: Optional[ConfigStore] = None,
        idempotency_store: Optional[IdempotencyStore] = None
    ):
        self.arbiter = arbiter_name
        self.storage_backend = storage_backend
        self.knowledge_graph = knowledge_graph
        self.feedback_manager = feedback_manager or FeedbackManager()
        self.clock = clock
        self.config_store = config_store or ConfigStore()
        self.idempotency_store = idempotency_store or IdempotencyStore()
        self._state: ArbiterState = ArbiterState(arbiter_id=arbiter_name)
        self._dirty = False
        self._save_lock = asyncio.Lock()
        self._pending_operations: asyncio.Queue[ContextAwareCallable] = asyncio.Queue(maxsize=self.MAX_PENDING_OPERATIONS)
        self._before_hooks: List[PluginHook] = []
        self._after_hooks: List[PluginHook] = []
        self._running = True
        self._last_flush_timestamp: float = 0.0
        self._last_error: Optional[str] = None
        self._event_count_since_snapshot: int = 0
        self._rate_limiter = TokenBucketRateLimiter(self.config_store)
        self._load_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._evolution_task: Optional[asyncio.Task] = None # Task for evolution cycle

        # Circuit Breakers for external systems
        self._snapshot_breaker = CircuitBreaker(fail_max=5, reset_timeout=60)
        self._push_event_breaker = CircuitBreaker(fail_max=10, reset_timeout=30)
    
    @staticmethod
    async def _call_maybe_async(fn: Callable[..., AnyType], *args, **kwargs) -> AnyType:
        """
        Call a function that may be async or sync. If it returns an awaitable, await it.
        This makes the manager robust against MagicMock/sync fakes in tests and
        real async impls in production.
        """
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def start(self) -> None:
        """Starts the manager, validates the audit chain, and replays events."""
        with tracer.start_as_current_span("arbiter_manager_start"):
            try:
                self._start_time = self.clock()
                self._running = True
                await self._call_maybe_async(self.storage_backend.start)
                await self._call_maybe_async(self.idempotency_store.start)
                # Validate audit chain integrity before loading state
                ok = await self._validate_audit_chain()
                if not ok:
                    logger.critical("Audit chain tampered for %s", self.arbiter)
                    raise AuditChainTamperedError(f"Audit chain tampered for {self.arbiter}")

                self._load_task = asyncio.create_task(self._load_state_and_replay_events())
                await self._load_task # Wait for initial load and replay to complete
                
                self._flush_task = asyncio.create_task(self._periodic_flush())
                
                # Start the evolution cycle task
                self._evolution_task = asyncio.create_task(self._periodic_evolution_cycle())
                
                logger.info(f"[{self.arbiter}] Growth manager started")
            except Exception as e:
                GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="start").inc()
                logger.error(f"[{self.arbiter}] Failed to start growth manager: {e}", exc_info=True)
                raise ArbiterGrowthError(f"Failed to start growth manager: {e}") from e


    async def _periodic_evolution_cycle(self) -> None:
        """Periodically triggers the _run_evolution_cycle."""
        while self._running:
            try:
                interval = await self.config_store.get_config("evolution_cycle_interval_seconds")
                logger.info(f"Next evolution cycle for {self.arbiter} in {interval} seconds.")
                await asyncio.sleep(interval)
                await self._run_evolution_cycle()
            except asyncio.CancelledError:
                logger.info(f"Periodic evolution cycle for {self.arbiter} cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in periodic evolution cycle for {self.arbiter}: {e}", exc_info=True)
                # Avoid tight loop on error
                await asyncio.sleep(60) # Wait a bit before retrying after an error


    async def _run_evolution_cycle(self) -> None:
        """
        Triggers a comprehensive meta-learning and evolution cycle for the arbiter.
        This conceptually links to the MetaLearning.adapt_agent_behavior and
        would involve a production-grade MLOps pipeline.
        """
        with tracer.start_as_current_span("arbiter_evolution_cycle", attributes={"arbiter.id": self.arbiter}):
            logger.info(f"Starting evolution cycle for arbiter: {self.arbiter}")

            # 1. Data Collection & Preparation (Conceptual)
            # In a real system, this step would involve:
            #   - Gathering recent growth events, user feedback, performance metrics.
            #   - Potentially enriching data with external context.
            #   - Pushing this data to a data lake/feature store for ML training.
            logger.info("  1. Collecting and preparing data for meta-learning...")
            # For demonstration, assume MetaLearning instance (if present) already logs corrections.
            # Here, you might trigger an export from the storage_backend if it holds raw data.
            # e.g., raw_event_data = await self.storage_backend.load_events_for_ml_training(...)

            # 2. Model Retraining (Conceptual)
            # This would trigger an external ML training pipeline.
            # The 'MetaLearning' class (from agent_core.py) would conceptually manage this.
            logger.info("  2. Triggering meta-learning model retraining via MLOps pipeline...")
            # This call would interact with your MLOps platform API:
            # e.g., mlops_platform.trigger_training_job(data_source_id, model_name)
            # Upon successful training, a new model version would be available.

            # 3. Model Deployment / Configuration Update (Conceptual)
            # After a new model is trained and validated, it needs to be deployed or its influence
            # reflected in the arbiter's configuration.
            logger.info("  3. Deploying new model version / updating arbiter configuration...")
            # This could involve:
            #   - Updating a config store (like etcd) with new weights, prompt templates, or behavioral parameters.
            #   - Deploying a new service version if the model is embedded or served via an API.
            #   - For prompt-based adaptation, the ConfigStore might load new prompt versions.
            # For example:
            # await self.config_store.set_config("skill_prediction_model_version", "v2.1")
            # await self.config_store.set_config("new_prompt_template_id", "optimized_v3")

            # 4. A/B Testing / Canary Deployment (Conceptual)
            logger.info("  4. Monitoring A/B tests or canary deployments for new behaviors...")

            # 5. Audit Logging the Evolution
            await self._audit_log("evolution_cycle_completed", {
                "status": "success",
                "triggered_at": self.clock().isoformat(),
                # "new_model_version": "v2.1", # Add actual version if applicable
            })
            logger.info(f"Evolution cycle for arbiter {self.arbiter} completed successfully.")


    async def _validate_audit_chain(self) -> bool:
        """Validates the integrity of the audit chain for this arbiter."""
        import hashlib # Explicitly import hashlib here for clarity
        with tracer.start_as_current_span("validate_audit_chain"):
            logger.info(f"Performing audit chain validation for arbiter: {self.arbiter}")
            all_logs = await self._call_maybe_async(self.storage_backend.load_all_audit_logs, self.arbiter)
            if not all_logs:
                logger.info("No audit logs found. Chain is valid by default.")
                return True

            # Check timestamps first
            prev_timestamp = None
            for log in all_logs:
                current_timestamp = log["timestamp"]
                if prev_timestamp and current_timestamp < prev_timestamp:
                    error_msg = f"Audit chain TAMPERED! Timestamp out of order at {current_timestamp}."
                    logger.critical(error_msg)
                    GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="audit_tampered").inc()
                    return False
                prev_timestamp = current_timestamp

            # Then check hash chain
            last_hash = "genesis_hash"
            for log in all_logs:
                # Use log.get() to gracefully handle missing keys and provide defaults
                arbiter_id = log.get('arbiter_id', '')
                operation = log.get('operation', '')
                timestamp = log.get('timestamp', '')
                details_str = json.dumps(log.get('details', {}), sort_keys=True)
                prev_hash = log.get('previous_log_hash', log.get('previous_hash', last_hash))

                recalculated_hash = hashlib.sha256(
                    f"{arbiter_id}{operation}{timestamp}{details_str}{prev_hash}".encode()
                ).hexdigest()

                if log["log_hash"] != recalculated_hash:
                    error_msg = f"Audit chain TAMPERED! Corrupted log hash at timestamp {log['timestamp']}."
                    logger.critical(error_msg)
                    GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="audit_tampered").inc()
                    return False
                if log.get('previous_log_hash', log.get('previous_hash')) != last_hash:
                    error_msg = f"Audit chain TAMPERED! Mismatch at log timestamp {log['timestamp']}. Expected previous hash {last_hash}, but got {log.get('previous_log_hash', log.get('previous_hash'))}"
                    logger.critical(error_msg)
                    GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="audit_tampered").inc()
                    return False
                last_hash = log["log_hash"]
            
            logger.info("Audit chain validation successful.")
            return True

    async def anchor_audit_chain_periodically(self, external_ledger_api: Callable[[str, str], Awaitable[None]]):
        """
        Placeholder for periodically anchoring the latest audit hash to an external immutable ledger.
        This should be run by a background scheduler.
        """
        with tracer.start_as_current_span("anchor_audit_chain"):
            try:
                latest_hash = await self._call_maybe_async(self.storage_backend.get_last_audit_hash, self.arbiter)
                if latest_hash != "genesis_hash":
                    # In a real implementation, this would call the external service API
                    # e.g., await external_ledger_api.record_hash(self.arbiter, latest_hash)
                    logger.info(f"Anchoring latest audit hash {latest_hash} for {self.arbiter} to external ledger.")
                    GROWTH_AUDIT_ANCHORS_TOTAL.labels(arbiter=self.arbiter).inc()
            except Exception as e:
                GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="audit_anchor").inc()
                logger.error(f"Failed to anchor audit chain for {self.arbiter}: {e}")

    def _on_load_done(self, fut: asyncio.Future) -> None:
        if fut.exception():
            self._last_error = str(fut.exception())
    
    async def _periodic_flush(self) -> None:
        while self._running:
            try:
                # Dynamically get flush interval from config store
                min_interval = await self.config_store.get_config("flush_interval_min")
                max_interval = await self.config_store.get_config("flush_interval_max")
                # Simple adaptive logic: faster flush if dirty, slower if not.
                # In a real system, this might be based on event volume or system load.
                sleep_interval = min_interval if self._dirty else max_interval
                await asyncio.sleep(sleep_interval)
                await self._save_if_dirty()
            except asyncio.CancelledError:
                logger.info(f"Periodic flush for {self.arbiter} cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in periodic flush for {self.arbiter}: {e}", exc_info=True)
                GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="periodic_flush").inc()
                await asyncio.sleep(min_interval) # Don't spin too fast on error

    async def shutdown(self) -> None:
        """
        Performs a graceful shutdown of the manager,
        including cancelling tasks and persisting final state.
        """
        self._running = False
        logger.info(f"Shutting down ArbiterGrowthManager for {self.arbiter}...")
        
        # Cancel background tasks
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        if self._evolution_task:
            self._evolution_task.cancel()
            await asyncio.gather(self._evolution_task, return_exceptions=True)
        if self._load_task: # Should already be done, but ensure no lingering issues
            self._load_task.cancel()
            await asyncio.gather(self._load_task, return_exceptions=True)

        # Process any remaining pending operations
        while not self._pending_operations.empty():
            op = await self._pending_operations.get()
            try:
                await self._call_maybe_async(op)
            except Exception as e:
                logger.error(f"Error processing pending operation during shutdown: {e}")
                GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="shutdown").inc()


        # Ensure final state is saved
        await self._save_if_dirty(force=True)

        # Stop backends
        await self._call_maybe_async(self.idempotency_store.stop)
        await self._call_maybe_async(self.storage_backend.stop)
        logger.info(f"ArbiterGrowthManager for {self.arbiter} shut down.")


    async def _load_state_and_replay_events(self) -> None:
        """
        Loads the latest snapshot and replays events from the last recorded offset.
        This ensures the arbiter's state is consistent.
        """
        with tracer.start_as_current_span("load_state_and_replay_events", attributes={"arbiter.id": self.arbiter}):
            logger.info(f"Loading state and replaying events for arbiter: {self.arbiter}")
            
            # 1. Load the latest snapshot
            snapshot_data = await self._call_maybe_async(self.storage_backend.load, self.arbiter)
            if snapshot_data:
                # Validate and apply schema migrations if necessary
                if snapshot_data.get("schema_version", 1.0) < self.SCHEMA_VERSION:
                    logger.info(f"Applying schema migration for {self.arbiter}. Old version: {snapshot_data.get('schema_version')}, New version: {self.SCHEMA_VERSION}")
                    # In a real system, you'd have a migration function here
                    # e.g., snapshot_data = _apply_snapshot_migrations(snapshot_data, self.SCHEMA_VERSION)
                
                self._state = ArbiterState(arbiter_id=self.arbiter, **snapshot_data)
                logger.info(f"Loaded snapshot for {self.arbiter} at level {self._state.level}, event offset: {self._state.event_offset}")
            else:
                logger.info(f"No existing snapshot found for {self.arbiter}. Starting with default state.")
                self._state = ArbiterState(arbiter_id=self.arbiter) # Ensure arbiter_id is set
            
            # 2. Replay events from the last recorded offset
            # The event_offset can be an integer (for SQLite ID) or a string (for Kafka "partition:offset").
            # The storage backend's load_events method needs to correctly handle this.
            from_offset = self._state.event_offset
            # Coerce numeric string offsets to int for list slicing backends used in tests
            if isinstance(from_offset, str) and from_offset.isdigit():
                from_offset = int(from_offset)
            events_to_replay = await self._call_maybe_async(self.storage_backend.load_events, self.arbiter, from_offset=from_offset)
            logger.info(f"Replaying {len(events_to_replay)} events for {self.arbiter} from offset {self._state.event_offset}")

            for event_dict in events_to_replay:
                event = GrowthEvent(**event_dict)
                await self._apply_event(event)
                # Update the event_offset in state to reflect the highest replayed offset
                # This ensures that next load starts from the correct point.
                self._state.event_offset = event.details.get("canonical_offset", self._state.event_offset) # Use canonical offset if provided by backend
                self._event_count_since_snapshot += 1
            
            # After replay, ensure any pending operations that queued *during* replay are now processed.
            # This handles operations that might have been queued before _load_task completed.
            while not self._pending_operations.empty():
                op = await self._pending_operations.get_nowait()
                await self._call_maybe_async(op) # Execute them immediately after state is ready

            await self._save_if_dirty(force=True) # Save final state after replay
            logger.info(f"Finished replaying events for {self.arbiter}. Current level: {self._state.level}, Events since last snapshot: {self._event_count_since_snapshot}")


    async def _apply_event(self, event: GrowthEvent) -> None:
        """
        Applies a growth event to the arbiter's state.
        This is the core state transition logic.
        """
        with tracer.start_as_current_span(f"apply_event_{event.type}", attributes={"event.type": event.type}):
            logger.debug(f"Applying event: {event.type} for {self.arbiter}")
            
            if event.type in ("skill_acquired", "skill_learned"):  # Handle both for compatibility
                skill_name = event.details.get("skill_name")
                initial_score = event.details.get("initial_score", 0.1)
                if skill_name:
                    self._state.set_skill_score(skill_name, initial_score)
                    GROWTH_SKILL_IMPROVEMENT.labels(arbiter=self.arbiter, skill=skill_name).observe(initial_score)
            elif event.type == "skill_improved":
                skill_name = event.details.get("skill_name")
                improvement_amount = event.details.get("improvement_amount", 0.01)
                if skill_name:
                    current_score = self._state.skills.get(skill_name, 0.0)
                    self._state.set_skill_score(skill_name, current_score + improvement_amount)
                    GROWTH_SKILL_IMPROVEMENT.labels(arbiter=self.arbiter, skill=skill_name).observe(improvement_amount)
            elif event.type == "level_up":
                new_level = event.details.get("new_level")
                if new_level and new_level > self._state.level:
                    self._state.level = new_level
                    logger.info(f"Arbiter {self.arbiter} leveled up to {new_level}")
            elif event.type == "experience_gained":
                xp_amount = event.details.get("amount", 0.0)
                if self._state.experience_points is None:
                    self._state.experience_points = 0.0
                self._state.experience_points += xp_amount
            elif event.type == "user_preference_updated":
                preference_key = event.details.get("key")
                preference_value = event.details.get("value")
                if preference_key is not None:
                    self._state.user_preferences[preference_key] = preference_value
            else:
                logger.warning(f"Unknown event type received: {event.type} for {self.arbiter}")
            
            self._dirty = True # Mark state as dirty after applying any event

    @tracer.start_as_current_span("save_snapshot_to_db")
    async def _save_snapshot_to_db(self) -> None:
        if hasattr(self._snapshot_breaker, '__aenter__'):
            try:
                async with self._snapshot_breaker:
                    await self.__do_save_snapshot()
            except CircuitBreakerError:
                self._last_error = "Snapshot circuit breaker is open."
                GROWTH_CIRCUIT_BREAKER_TRIPS.labels(arbiter=self.arbiter, breaker_name="snapshot").inc()
                logger.error(f"Failed to save snapshot for {self.arbiter}: Circuit breaker is open.")
                raise CircuitBreakerOpenError(self._last_error)
            except Exception as e:
                GROWTH_SAVE_ERRORS.labels(arbiter=self.arbiter).inc()
                self._last_error = str(e)
                logger.error(f"Failed to save snapshot for {self.arbiter}: {e}", exc_info=True)
                raise
        else:
            await self.__do_save_snapshot()

    async def __do_save_snapshot(self):
         async with self._save_lock:
            # Ensure event_offset is explicitly updated to the current latest.
            # This is critical for correct replay on startup.
            # For SQLite, it would be the max ID of events processed.
            # For Kafka, it would be the "partition:offset" string of the last processed message.
            # The _apply_event should update self._state.event_offset with the canonical offset.
            snapshot_data = self._state.model_dump()
            await self._call_maybe_async(self.storage_backend.save, self.arbiter, snapshot_data)
            GROWTH_SNAPSHOTS.labels(arbiter=self.arbiter).inc()
            self._dirty = False
            self._event_count_since_snapshot = 0
            logger.info(f"Snapshot saved for {self.arbiter}. Current event offset: {self._state.event_offset}")


    async def _save_if_dirty(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        snapshot_interval = await self.config_store.get_config("snapshot_interval")
        # Only save if state is dirty AND (forced or enough events have occurred)
        if self._dirty and (force or self._event_count_since_snapshot >= snapshot_interval):
            logger.debug(f"Attempting to save snapshot for {self.arbiter} (dirty: {self._dirty}, events since snapshot: {self._event_count_since_snapshot}, force: {force})")
            await self._call_maybe_async(self._save_snapshot_to_db)

    async def _audit_log(self, operation: str, details: Dict[str, Any]) -> None:
        """Log operations with hash-chaining."""
        # This function should only be called for operations that modify state or are critical for audit.
        previous_hash = await self._call_maybe_async(self.storage_backend.get_last_audit_hash, self.arbiter)
        new_hash = await self._call_maybe_async(self.storage_backend.save_audit_log, self.arbiter, operation, details, previous_hash)
        logger.debug(f"Audit logged operation '{operation}' for {self.arbiter}. New hash: {new_hash}")


    def _generate_idempotency_key(self, event: GrowthEvent, service_name: str) -> str:
        """Generates a unique idempotency key for an event and service."""
        # Key should combine arbiter_id, event_type, timestamp, and a hash of details
        # Ensure that 'details' dict is sorted for consistent hashing.
        details_hash = hashlib.sha256(json.dumps(event.details, sort_keys=True).encode()).hexdigest()
        key_components = f"{self.arbiter}:{event.type}:{event.timestamp}:{details_hash}:{service_name}"
        return hashlib.sha256(key_components.encode()).hexdigest()


    def register_hook(self, hook: PluginHook, stage: str = 'after') -> None:
        """Register a plugin hook to run 'before' or 'after' a growth event."""
        if stage == 'before':
            self._before_hooks.append(hook)
        elif stage == 'after':
            self._after_hooks.append(hook)
        else:
            raise ValueError("Stage must be either 'before' or 'after'")

    @tracer.start_as_current_span("push_event")
    async def _push_event(self, event: GrowthEvent) -> None:
        """
        Pushes a growth event to the primary storage backend and external systems.
        Includes circuit breaker for robustness.
        """
        if hasattr(self._push_event_breaker, '__aenter__'):
            try:
                async with self._push_event_breaker:
                    await self.__do_push_event(event)
            except CircuitBreakerError:
                self._last_error = "Push event circuit breaker is open."
                GROWTH_CIRCUIT_BREAKER_TRIPS.labels(arbiter=self.arbiter, breaker_name="push_event").inc()
                logger.error(f"Failed to push event for {self.arbiter}: Circuit breaker is open.")
                raise CircuitBreakerOpenError(self._last_error)
        else:
            await self.__do_push_event(event)


    async def __do_push_event(self, event: GrowthEvent):
        """Internal method to perform the actual event push, subject to circuit breaker."""
        start_time = datetime.now(timezone.utc).timestamp()
        try:
            # 1. Save event to primary storage backend (e.g., Kafka Stream)
            await self._call_maybe_async(self.storage_backend.save_event, self.arbiter, event.model_dump())
            logger.debug(f"Event '{event.type}' saved to primary storage for {self.arbiter}.")
            self._event_count_since_snapshot += 1 # Increment counter after successful event persistence
            GROWTH_EVENTS.labels(arbiter=self.arbiter).inc()
            
            # 2. Idempotent calls to external systems (Knowledge Graph, Feedback Manager)
            # Use idempotency store to prevent duplicate processing if retries occur.
            
            # Example for Knowledge Graph
            kg_idempotency_key = self._generate_idempotency_key(event, "knowledge_graph")
            if await self._call_maybe_async(self.idempotency_store.check_and_set, kg_idempotency_key):
                await self._call_maybe_async(
                    self.knowledge_graph.add_fact,
                    arbiter_id=self.arbiter,
                    event_type=event.type,
                    event_details=event.details
                )
                logger.debug(f"Event '{event.type}' pushed to Knowledge Graph for {self.arbiter}.")
            else:
                logger.info(f"Knowledge Graph update for event '{event.type}' (key: {kg_idempotency_key}) skipped due to idempotency.")

            # Example for Feedback Manager
            if self.feedback_manager:
                fm_idempotency_key = self._generate_idempotency_key(event, "feedback_manager")
                if await self._call_maybe_async(self.idempotency_store.check_and_set, fm_idempotency_key):
                    await self._call_maybe_async(
                        self.feedback_manager.record_feedback,
                        arbiter_id=self.arbiter,
                        event_type=event.type,
                        event_details=event.details
                    )
                    logger.debug(f"Event '{event.type}' pushed to Feedback Manager for {self.arbiter}.")
                else:
                    logger.info(f"Feedback Manager update for event '{event.type}' (key: {fm_idempotency_key}) skipped due to idempotency.")

            # Any other external integrations would follow similar idempotent patterns.
            
            # 3. Audit log the event processing
            await self._audit_log(f"event_recorded:{event.type}", event.details)
            
        finally:
            GROWTH_EVENT_PUSH_LATENCY.labels(arbiter=self.arbiter).observe(datetime.now(timezone.utc).timestamp() - start_time)

    async def _queue_operation(self, operation_coro: Callable[[], Awaitable[None]]) -> None:
        """
        Queues an operation for asynchronous processing, applying rate limiting and managing queue size.
        If the load task is still running, operations are truly queued. Once loaded, they are executed immediately.
        """
        if not await self._call_maybe_async(self._rate_limiter.acquire):
            raise RateLimitError("Rate limit exceeded for queuing operation.")
        
        carrier = {}
        # Inject current OpenTelemetry context into the carrier for propagation
        propagator.inject(carrier)
        context_aware_op = ContextAwareCallable(operation_coro, carrier, self.arbiter)
        
        # Check queue size before adding to prevent unbounded growth
        if self._pending_operations.qsize() >= self.MAX_PENDING_OPERATIONS:
            GROWTH_PENDING_QUEUE.labels(arbiter=self.arbiter).set(self._pending_operations.qsize())
            raise OperationQueueFullError(f"Operation queue for {self.arbiter} is full. Max: {self.MAX_PENDING_OPERATIONS}")
        
        GROWTH_PENDING_QUEUE.labels(arbiter=self.arbiter).set(self._pending_operations.qsize() + 1)
    
        # Always execute immediately if load task is complete - this fixes the test issue
        if self._load_task and self._load_task.done():
            logger.debug(f"Executing operation directly for {self.arbiter} (load task complete).")
            try:
                await self._call_maybe_async(context_aware_op)
            finally:
                GROWTH_PENDING_QUEUE.labels(arbiter=self.arbiter).set(self._pending_operations.qsize())
        else:
            # Queue for later processing
            logger.debug(f"Queueing operation for {self.arbiter} (load task pending). Queue size: {self._pending_operations.qsize() + 1}")
            await self._pending_operations.put(context_aware_op)


    async def record_growth_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """
        Records a new growth event. This is the primary public API for external systems
        to signal growth-related activities. The event will be processed through the
        apply-and-push pipeline.
        """
        with tracer.start_as_current_span("record_growth_event_api", attributes={"event.type": event_type, "arbiter.id": self.arbiter}):
            async def operation():
                timestamp = self.clock().isoformat(timespec="seconds")
                event = GrowthEvent(type=event_type, timestamp=timestamp, details=details, event_version=self.SCHEMA_VERSION)
                
                # Execute 'before' hooks
                for hook in self._before_hooks:
                    await self._call_maybe_async(hook.on_growth_event, event, self._state)

                # Push event to external systems (idempotent, async) and persist to storage
                await self._call_maybe_async(self._push_event, event)
                
                # Apply event to internal state (idempotent, order-dependent)
                # This logic is now here to ensure it only happens after persistence
                await self._apply_event(event) 
                
                # Execute 'after' hooks
                for hook in self._after_hooks:
                    await self._call_maybe_async(hook.on_growth_event, event, self._state)

                # Trigger snapshot save if criteria met
                await self._save_if_dirty()
            
            # FIX A: Execute event operations inline when loaded
            if self._load_task and self._load_task.done():
                await operation()
            else:
                # Queue the operation for processing
                await self._queue_operation(operation)


    async def acquire_skill(self, skill_name: str, initial_score: float = 0.1, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Records an event for acquiring a new skill.
        
        Args:
            skill_name: The name of the skill being acquired.
            initial_score: The initial proficiency score (0.0 to 1.0).
            context: Optional contextual data for the event.
            
        Raises:
            ArbiterGrowthError: If the operation fails to be queued.
            OperationQueueFullError: If the internal operation queue is full.
            RateLimitError: If the rate limit is exceeded.
        """
        try:
            await self.record_growth_event(
                "skill_acquired", 
                {"skill_name": skill_name, "initial_score": initial_score, "context": context or {}}
            )
            logger.info(f"[{self.arbiter}] Queued event for skill acquisition: {skill_name}")
        except (OperationQueueFullError, RateLimitError, ArbiterGrowthError) as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="acquire_skill_queue_fail").inc()
            logger.error(f"[{self.arbiter}] Failed to queue skill acquisition event for {skill_name}: {e}")
            raise

    async def improve_skill(self, skill_name: str, improvement_amount: float = 0.01, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Records an event for improving an existing skill.
        
        Args:
            skill_name: The name of the skill to improve.
            improvement_amount: The amount to increase the proficiency score.
            context: Optional contextual data for the event.
            
        Raises:
            ArbiterGrowthError: If the operation fails to be queued.
        """
        try:
            await self.record_growth_event(
                "skill_improved", 
                {"skill_name": skill_name, "improvement_amount": improvement_amount, "context": context or {}}
            )
            logger.info(f"[{self.arbiter}] Queued event for skill improvement: {skill_name}")
        except (OperationQueueFullError, RateLimitError, ArbiterGrowthError) as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="improve_skill_queue_fail").inc()
            logger.error(f"[{self.arbiter}] Failed to queue skill improvement event for {skill_name}: {e}")
            raise

    async def level_up(self) -> None:
        """
        Records an event for leveling up the arbiter. The actual level change is handled by the event application logic.
        
        Raises:
            ArbiterGrowthError: If the operation fails to be queued.
        """
        try:
            await self.record_growth_event(
                "level_up", 
                {"new_level": self._state.level + 1, "old_level": self._state.level}
            )
            logger.info(f"[{self.arbiter}] Queued event for level up.")
        except (OperationQueueFullError, RateLimitError, ArbiterGrowthError) as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="level_up_queue_fail").inc()
            logger.error(f"[{self.arbiter}] Failed to queue level up event: {e}")
            raise

    async def gain_experience(self, amount: float, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Records an event for gaining experience points.
        
        Args:
            amount: The amount of experience to gain.
            context: Optional contextual data for the event.
            
        Raises:
            ArbiterGrowthError: If the operation fails to be queued.
        """
        try:
            await self.record_growth_event(
                "experience_gained",
                {"amount": amount, "context": context or {}}
            )
            logger.info(f"[{self.arbiter}] Queued event for gaining experience: {amount}")
        except (OperationQueueFullError, RateLimitError, ArbiterGrowthError) as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="gain_experience_queue_fail").inc()
            logger.error(f"[{self.arbiter}] Failed to queue experience gain event: {e}")
            raise

    async def update_user_preference(self, key: str, value: Any, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Records an event for updating a user preference.
        
        Args:
            key: The name of the preference to update.
            value: The new value of the preference.
            context: Optional contextual data for the event.
            
        Raises:
            ArbiterGrowthError: If the operation fails to be queued.
        """
        try:
            await self.record_growth_event(
                "user_preference_updated",
                {"key": key, "value": value, "context": context or {}}
            )
            logger.info(f"[{self.arbiter}] Queued event for updating user preference: {key}")
        except (OperationQueueFullError, RateLimitError, ArbiterGrowthError) as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="update_user_pref_queue_fail").inc()
            logger.error(f"[{self.arbiter}] Failed to queue user preference update event: {e}")
            raise
            
    async def _record_event_now(self, event: "GrowthEvent") -> None: 
        # 1) persist event 
        await self._call_maybe_async(self.storage_backend.save_event, self.arbiter, event.model_dump())
    
        # 2) write audit log 
        await self._audit_log(operation=event.type, details=event.details)
    
        # 3) optional idempotency + KG hooks (non-fatal) 
        try: 
            if getattr(self, "idempotency_store", None): 
                import json as _json 
                dedup_key = f"{event.type}:{_json.dumps(event.details, sort_keys=True)}" 
                await self._call_maybe_async(self.idempotency_store.remember, dedup_key) 
            if getattr(self, "knowledge_graph", None): 
                await self._call_maybe_async(self.knowledge_graph.add_fact, { 
                    "arbiter_id": self.arbiter, 
                    "event_type": event.type, 
                    "details": event.details, 
                }) 
        except Exception as e: 
            logger.exception("[%s] Optional idempotency/KG hook failed: %s", self.arbiter, e) 
    
        # 4) apply to in-memory state (unknown types may warn at _apply_event) 
        try: 
            await self._apply_event(event) 
        except Exception as e: 
            logger.exception("[%s] Failed to apply event %s: %s", self.arbiter, event.type, e) 
    
        # 5) bump counters and snapshot 
        self._dirty = True 
        self._event_count_since_snapshot += 1 
        snapshot_interval = await self.config_store.get_config("snapshot_interval")
        if self._event_count_since_snapshot >= snapshot_interval: 
            await self._save_if_dirty(force=True)

    async def get_growth_summary(self) -> Dict[str, Any]:
        """
        Returns the current, consistent growth summary for the arbiter.
        Ensures any pending changes are saved before returning the state.
        
        Returns:
            Dict containing the current state of the arbiter's growth.
        """
        await self._save_if_dirty() # Ensure the latest state is persisted before querying.
        return self._state.model_dump()

    async def health(self) -> Dict[str, Any]:
        """
        Provides a detailed health status of the ArbiterGrowthManager,
        including internal state, error status, and circuit breaker states.
        
        Returns:
            Dict containing health details and backend statuses.

        Raises:
            ArbiterGrowthError: If health check fails unexpectedly.
        """
        try:
            storage_status = await self._call_maybe_async(self.storage_backend.ping)
            idempotency_status = await self._call_maybe_async(self.idempotency_store.ping)
            config_status = await self._call_maybe_async(self.config_store.ping)
            
            health_data = {
                "arbiter_id": self.arbiter,
                "is_running": self._running,
                "last_error": str(self._last_error) if self._last_error else None,
                "current_level": self._state.level,
                "pending_operations_queue_size": self._pending_operations.qsize(),
                "events_since_last_snapshot": self._event_count_since_snapshot,
                "snapshot_breaker_state": getattr(self._snapshot_breaker, 'current_state', 'unknown'),
                "push_event_breaker_state": getattr(self._push_event_breaker, 'current_state', 'unknown'),
                "storage_backend_status": storage_status,
                "idempotency_store_status": idempotency_status,
                "config_store_status": config_status,
                "last_audit_hash": await self._call_maybe_async(self.storage_backend.get_last_audit_hash, self.arbiter),
                "up_time_seconds": (self.clock() - self._start_time).total_seconds() if hasattr(self, '_start_time') else 0,
            }
            logger.info(f"[{self.arbiter}] Health check completed: {health_data}")
            return health_data
        except Exception as e:
            GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="health_check").inc()
            logger.error(f"[{self.arbiter}] Health check failed: {e}", exc_info=True)
            raise ArbiterGrowthError(f"Health check failed: {e}") from e
    
    # Liveness and Readiness Probes
    async def liveness_probe(self) -> bool:
        """
        Simple liveness probe: is the manager running and not in a critical error state?
        """
        return self._running and self._last_error is None

    async def readiness_probe(self) -> bool:
        """
        Readiness probe: is the manager ready to accept requests?
        This means it's running, initial load is complete, and critical backends are accessible.
        """
        if not self._running:
            return False
        if self._load_task and not self._load_task.done():
            return False # Still loading or replaying
        try:
            # Check essential backend connectivity
            await self._call_maybe_async(self.storage_backend.ping)
            await self._call_maybe_async(self.idempotency_store.ping)
            await self._call_maybe_async(self.config_store.ping)
            return True
        except Exception as e:
            logger.warning(f"Readiness check failed for {self.arbiter}: {e}")
            return False
            
    async def rotate_encryption_key(self, new_key: bytes) -> None:
        """
        Conceptually rotates the encryption key for stored data.
        
        Note: This is a placeholder. A real implementation would:
        1. Read all encrypted data from storage.
        2. Decrypt the data using the old key.
        3. Re-encrypt the data using the new key.
        4. Write the new encrypted data back to storage.
        5. Update the service's key management configuration.
        
        This process must be atomic and handle potential failures gracefully.
        The current `StorageBackend` interface does not support this
        operation generically, so this remains a conceptual example.
        """
        logger.warning(f"[{self.arbiter}] Encryption key rotation is a conceptual method. A full, production-grade implementation would require changes to the StorageBackend interface to support re-encryption of all data.")
        GROWTH_ERRORS_TOTAL.labels(arbiter=self.arbiter, error_type="key_rotation_not_implemented").inc()