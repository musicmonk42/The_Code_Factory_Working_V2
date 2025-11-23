"""
Array backend for the Arbiter/SFE platform.

- Type-safe array operations (append, get, update, delete, query)
- Thread-safe with concurrent access handling
- Persistent storage with JSON, SQLite, or Redis backend
- Auditable with structured logging
- Observable with Prometheus metrics and OpenTelemetry tracing
- Secure with encryption and file permission checks
- Extensible via arbiter_plugin_registry integration

USAGE:
    from arbiter_plugin_registry import registry, PlugInKind
    from arbiter_array_backend import ArrayBackend

    # Register backend as a plugin
    @registry.register(kind=PlugInKind.CORE_SERVICE, name="ArrayBackend", version="1.0.0", author="Arbiter Team")
    class MyArrayBackend(ArrayBackend):
        pass

    # In an async function:
    backend = MyArrayBackend(name="my_array", storage_path="arrays.json")
    await backend.initialize()
    await backend.append([1, 2, 3])
    data = await backend.get()
"""

import asyncio
import json
import logging
import os
import threading
import time
from abc import ABCMeta, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import aiofiles

# ---------- FIX: Added missing critical imports ----------
import numpy as np
from arbiter.otel_config import get_tracer
from cryptography.fernet import Fernet, InvalidToken
from prometheus_client import REGISTRY
from prometheus_client import Counter as PCounter
from prometheus_client import Gauge as PGauge
from prometheus_client import Histogram as PHistogram
from prometheus_client import Summary as PSummary
from prometheus_client.metrics import Counter as _Counter
from prometheus_client.metrics import Gauge as _Gauge
from prometheus_client.metrics import Histogram as _Histogram
from prometheus_client.metrics import Summary as _Summary
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

# ---------- end FIX ----------


# ---------- Optional dependency shims (test/dev safe) ----------

# aiolimiter
try:
    from aiolimiter import AsyncLimiter  # type: ignore
except Exception:

    class AsyncLimiter:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False


# aiosqlite (ensure Error exists for retry tuples)
try:
    import aiosqlite  # type: ignore
except Exception:

    class aiosqlite:  # noqa: N801
        class Error(Exception): ...


# redis (ensure RedisError exists for retry tuples)
try:
    import redis.asyncio as aioredis  # type: ignore
    from redis.exceptions import RedisError as RedisError  # noqa: F401
except Exception:

    class aioredis:  # noqa: N801
        class RedisError(Exception): ...

        @staticmethod
        def from_url(*args, **kwargs):
            raise ImportError("redis.asyncio not installed")

    RedisError = aioredis.RedisError


# gym / stable_baselines3 are optional; silence import errors if they're referenced
try:
    import gym  # type: ignore
except Exception:

    class gym:  # noqa: N801
        pass


try:
    import stable_baselines3  # type: ignore
except Exception:

    class stable_baselines3:  # noqa: N801
        class common:
            pass


# ---------- end optional shims ----------

# Mock imports for a self-contained fix
try:
    from .arbiter_plugin_registry import PluginBase, PlugInKind, registry
    from .logging_utils import PIIRedactorFilter
except ImportError:
    # Create a combined metaclass to handle both PluginBase and ABC
    class PluginMeta(ABCMeta):
        """Combined metaclass for plugins."""

        pass

    class PluginBase(metaclass=PluginMeta):
        """Base class for plugins with ABC support."""

        def on_reload(self):
            pass

    class PlugInKind:
        CORE_SERVICE = "CORE_SERVICE"

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

        @staticmethod
        def get(kind, name):
            raise KeyError(f"Plugin {name} not found")

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True


try:
    from .config import ArbiterConfig
    from .postgres_client import PostgresClient
except ImportError:

    class ArbiterConfig:
        REDIS_URL = "redis://localhost:6379"
        REDIS_MAX_CONNECTIONS = 50
        ENCRYPTION_KEY = ""
        DATABASE_URL = "postgresql://user:password@host:port/database"

        class MockSecret:
            def get_secret_value(self):
                return os.getenv("SFE_ENCRYPTION_KEY", Fernet.generate_key().decode())

        ENCRYPTION_KEY = MockSecret()

    class PostgresClient:
        def __init__(self, db_url):
            self.db_url = db_url
            self._is_connected = False

        async def connect(self):
            self._is_connected = True
            await asyncio.sleep(0.01)  # Simulate async connection

        async def disconnect(self):
            self._is_connected = False
            await asyncio.sleep(0.01)  # Simulate async disconnection

        async def execute(self, query, params=None):
            if not self._is_connected:
                raise Exception("Not connected to database")
            await asyncio.sleep(0.01)  # Simulate async query
            if "SELECT" in query:
                return []  # Return empty list for select queries
            return None

        async def ping(self):
            return self._is_connected


# Logging setup
logger = logging.getLogger("arbiter.array_backend")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)

# OpenTelemetry Setup
tracer = get_tracer(__name__)

# Prometheus Metrics
_metrics_lock = threading.Lock()
VALID_METRIC_TYPES = (_Counter, _Histogram, _Summary, _Gauge)


def get_or_create_metric(metric_cls, name, desc, labelnames=(), buckets=None):
    with _metrics_lock:
        try:
            existing = REGISTRY._names_to_collectors.get(name)
            if existing and isinstance(existing, metric_cls):
                return existing
            if existing:
                REGISTRY.unregister(existing)
        except Exception:
            pass

        kwargs = {"name": name, "documentation": desc, "labelnames": labelnames}

        if metric_cls is PHistogram and buckets:
            kwargs["buckets"] = buckets

        if metric_cls in (PCounter, PHistogram, PSummary, PGauge):
            return metric_cls(**kwargs)

        # tolerant fallback by metric name
        lname = name.lower()
        if "counter" in lname:
            return PCounter(**kwargs)
        if "histogram" in lname:
            return PHistogram(**kwargs)
        if "gauge" in lname:
            return PGauge(**kwargs)
        return PSummary(**kwargs)


array_ops_total = get_or_create_metric(
    PCounter, "array_ops_total", "Total array operations", ("backend_name", "operation")
)
array_op_time = get_or_create_metric(
    PHistogram,
    "array_op_time_seconds",
    "Time taken for array operations",
    ("backend_name", "operation"),
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)
array_size = get_or_create_metric(
    PGauge, "array_size", "Current size of the array", ("backend_name",)
)
array_errors_total = get_or_create_metric(
    PCounter,
    "array_errors_total",
    "Total errors in array operations",
    ("backend_name", "error_type", "backend"),
)


# Custom Exceptions
class ArrayBackendError(Exception):
    """Base exception for ArrayBackend errors."""

    pass


class StorageError(ArrayBackendError):
    """Raised for storage-related errors."""

    pass


class ArraySizeLimitError(ArrayBackendError):
    """Raised when the array exceeds the maximum size."""

    pass


@dataclass
class ArrayMeta:
    """Metadata for the array."""

    name: str
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)
    size_limit: int = 100000
    encryption_enabled: bool = False


class ArrayBackend(PluginBase):
    """Abstract base class for array backend implementations."""

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def append(self, item: Any) -> None:
        pass

    @abstractmethod
    async def get(self, index: Optional[int] = None) -> Union[Any, List[Any]]:
        pass

    @abstractmethod
    async def update(self, index: int, item: Any) -> None:
        pass

    @abstractmethod
    async def delete(self, index: Optional[int] = None) -> None:
        pass

    @abstractmethod
    async def query(self, condition: Callable[[Any], bool]) -> List[Any]:
        pass

    @abstractmethod
    def meta(self) -> ArrayMeta:
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def rotate_encryption_key(self, new_key: bytes) -> None:
        pass


# Mock PermissionManager for local testing
class PermissionManager:
    def __init__(self, config):
        self.config = config

    def check_permission(self, role, permission):
        # Dummy logic: always grant permission if role and permission are non-empty
        return bool(role) and bool(permission)


class ConcreteArrayBackend(ArrayBackend):
    """Concrete implementation of the array backend with JSON, SQLite, Redis, or PostgreSQL storage."""

    def __init__(
        self,
        name: str,
        storage_path: str = "arrays.json",
        storage_type: str = None,
        config: ArbiterConfig = None,
    ):
        super().__init__()
        self.name = name
        self.storage_path = storage_path
        self.storage_type = storage_type or os.getenv("ARRAY_STORAGE_TYPE", "json")
        self.config = config or ArbiterConfig()
        self._data: List[Any] = []
        self._page_size = int(os.getenv("ARRAY_PAGE_SIZE", 1000))
        self._current_page = 0
        self._lock = asyncio.Lock()
        self._meta = ArrayMeta(
            name=name,
            size_limit=int(os.getenv("ARRAY_MAX_SIZE", 100000)),
            encryption_enabled=os.getenv("ARRAY_ENCRYPTION_ENABLED", "false").lower()
            == "true",
        )
        self._fernet = (
            Fernet(self.config.ENCRYPTION_KEY.get_secret_value().encode())
            if self._meta.encryption_enabled
            else None
        )
        self._redis = None
        self._sqlite_pool = None
        self._postgres_client = None
        self._tracer = tracer

        # ---------- FIX: Initialize ThreadPoolExecutor and Dask flag ----------
        self.executor = ThreadPoolExecutor(max_workers=os.cpu_count())
        self.use_dask = os.getenv("USE_DASK", "false").lower() == "true"
        # ---------- end FIX ----------

        logger.info(
            {
                "event": "array_backend_init",
                "name": self.name,
                "storage_type": self.storage_type,
            }
        )

    # ---------- FIX: Add a __del__ method for safe garbage collection ----------
    def __del__(self):
        """Ensure resources are cleaned up when the object is destroyed."""
        # Fixed: Only clean up synchronous resources in __del__
        # Async cleanup should be done via __aexit__ or explicit close() calls
        if self.executor:
            try:
                self.executor.shutdown(wait=False)
            except Exception:
                pass  # Best effort cleanup in __del__

    # ---------- end FIX ----------

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type((IOError, aiosqlite.Error, RedisError)),
    )
    async def initialize(self) -> None:
        """Initializes the array backend, setting up storage and loading data."""
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_initialize_{self.name}"):
                try:
                    if self.storage_type == "redis":
                        try:
                            self._redis = aioredis.from_url(
                                self.config.REDIS_URL,
                                max_connections=max(
                                    self.config.REDIS_MAX_CONNECTIONS, 50
                                ),
                            )
                            await self._redis.ping()
                            logger.info(
                                {
                                    "event": "redis_connection_established",
                                    "name": self.name,
                                }
                            )
                        except RedisError:
                            logger.warning(
                                {
                                    "event": "redis_init_failed",
                                    "name": self.name,
                                    "fallback": "sqlite",
                                }
                            )
                            self.storage_type = "sqlite"
                            self.storage_path = "arrays.db"

                    if self.storage_type == "postgres":
                        self._postgres_client = PostgresClient(self.config.DATABASE_URL)
                        await self._postgres_client.connect()
                        await self._postgres_client.execute(
                            "CREATE TABLE IF NOT EXISTS array_data (id SERIAL PRIMARY KEY, data TEXT)"
                        )
                        logger.info(
                            {
                                "event": "postgres_connection_established",
                                "name": self.name,
                            }
                        )

                    if self.storage_type == "sqlite":
                        self._sqlite_pool = await aiosqlite.connect(
                            self.storage_path, isolation_level=None
                        )
                        await self._sqlite_pool.execute("PRAGMA journal_mode=WAL")
                        await self._sqlite_pool.execute(
                            "CREATE TABLE IF NOT EXISTS array_data (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)"
                        )
                        await self._sqlite_pool.commit()
                        logger.info(
                            {
                                "event": "sqlite_connection_established",
                                "name": self.name,
                            }
                        )

                    await self._load_from_storage()
                    array_ops_total.labels(
                        backend_name=self.name, operation="initialize"
                    ).inc()
                    array_op_time.labels(
                        backend_name=self.name, operation="initialize"
                    ).observe(time.time() - start_time)
                    array_size.labels(backend_name=self.name).set(len(self._data))
                except Exception as e:
                    logger.error(
                        {
                            "event": "array_initialize_error",
                            "name": self.name,
                            "error": str(e),
                        },
                        exc_info=True,
                    )
                    array_errors_total.labels(
                        backend_name=self.name,
                        error_type="initialize",
                        backend=self.storage_type,
                    ).inc()
                    raise StorageError(
                        f"Failed to initialize array backend: {e}"
                    ) from e

    async def close(self) -> None:
        """Closes storage connections."""
        start_time = time.time()
        with self._tracer.start_as_current_span(f"array_close_{self.name}"):
            try:
                if self._redis:
                    await self._redis.close()
                    self._redis = None
                    logger.info({"event": "redis_connection_closed", "name": self.name})
                if self._sqlite_pool:
                    await self._sqlite_pool.close()
                    self._sqlite_pool = None
                    logger.info(
                        {"event": "sqlite_connection_closed", "name": self.name}
                    )
                if self._postgres_client:
                    await self._postgres_client.disconnect()
                    self._postgres_client = None
                    logger.info(
                        {"event": "postgres_connection_closed", "name": self.name}
                    )
                array_ops_total.labels(backend_name=self.name, operation="close").inc()
                array_op_time.labels(backend_name=self.name, operation="close").observe(
                    time.time() - start_time
                )
            except Exception as e:
                logger.error(
                    {"event": "array_close_error", "name": self.name, "error": str(e)},
                    exc_info=True,
                )
                array_errors_total.labels(
                    backend_name=self.name,
                    error_type="close",
                    backend=self.storage_type,
                ).inc()
                raise StorageError(f"Failed to close array backend: {e}") from e

    # ---------- FIX: Add missing asnumpy and array methods ----------
    def array(self, data: Any) -> Any:
        """Converts data to the backend's array format (NumPy array)."""
        return np.array(data)

    def asnumpy(self, data: Any) -> np.ndarray:
        """Ensures data is a NumPy array."""
        if isinstance(data, np.ndarray):
            return data
        return np.array(data)

    # ---------- end FIX ----------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type((IOError, aiosqlite.Error, RedisError)),
    )
    async def _save_to_storage(self) -> None:
        """Saves the array data to the configured storage backend. Assumes lock is held."""
        try:
            data_to_save = self._data
            if self._meta.encryption_enabled and self._fernet:
                data_to_save = [
                    self._fernet.encrypt(json.dumps(item).encode()).decode()
                    for item in self._data
                ]
            if self.storage_type == "redis" and self._redis:
                await self._redis.set(f"array:{self.name}", json.dumps(data_to_save))
            elif self.storage_type == "sqlite":
                await self._sqlite_pool.execute("DELETE FROM array_data")
                for item in data_to_save:
                    await self._sqlite_pool.execute(
                        "INSERT INTO array_data (data) VALUES (?)", (json.dumps(item),)
                    )
                await self._sqlite_pool.commit()
            elif self.storage_type == "postgres":
                await self._postgres_client.execute("DELETE FROM array_data")
                for item in data_to_save:
                    await self._postgres_client.execute(
                        "INSERT INTO array_data (data) VALUES (%s)", (json.dumps(item),)
                    )
            else:
                os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
                os.chmod(
                    os.path.dirname(self.storage_path), 0o700
                )  # Restrict directory permissions
                tmp_path = Path(self.storage_path).with_suffix(".tmp")
                async with aiofiles.open(tmp_path, "w") as f:
                    await f.write(json.dumps(data_to_save))
                    await f.flush()
                    await asyncio.get_running_loop().run_in_executor(
                        None, os.fsync, f.fileno()
                    )
                os.replace(tmp_path, self.storage_path)
                os.chmod(self.storage_path, 0o600)  # Restrict file permissions
            logger.info(
                {"event": "array_save", "name": self.name, "size": len(self._data)}
            )
        except Exception as e:
            logger.error(
                {"event": "array_save_error", "name": self.name, "error": str(e)},
                exc_info=True,
            )
            array_errors_total.labels(
                backend_name=self.name, error_type="save", backend=self.storage_type
            ).inc()
            raise StorageError(f"Failed to save array data: {e}") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(
            (
                IOError,
                aiosqlite.Error,
                redis.RedisError,
                json.JSONDecodeError,
                InvalidToken,
            )
        ),
    )
    async def _load_from_storage(self) -> None:
        """Loads array data from the configured storage backend. Assumes lock is held."""
        try:
            loaded_data = []
            if self.storage_type == "redis" and self._redis:
                data = await self._redis.get(f"array:{self.name}")
                loaded_data = json.loads(data) if data else []
            elif self.storage_type == "sqlite":
                async with self._sqlite_pool.execute(
                    "SELECT data FROM array_data ORDER BY id"
                ) as cursor:
                    rows = await cursor.fetchall()
                    loaded_data = [row[0] for row in rows]
            elif self.storage_type == "postgres":
                rows = await self._postgres_client.execute(
                    "SELECT data FROM array_data ORDER BY id"
                )
                loaded_data = [row[0] for row in rows]
            else:
                storage_path = Path(self.storage_path)
                if not storage_path.exists():
                    self._data = []
                    return
                else:
                    async with aiofiles.open(storage_path, "r") as f:
                        content = await f.read()
                        try:
                            loaded_data = json.loads(content) if content else []
                        except json.JSONDecodeError:
                            logger.warning(
                                {
                                    "event": "array_load_corrupted",
                                    "name": self.name,
                                    "error": "Invalid JSON, recovering to empty array",
                                }
                            )
                            array_errors_total.labels(
                                backend_name=self.name,
                                error_type="json_corrupted",
                                backend="json",
                            ).inc()
                            loaded_data = []

            if self._meta.encryption_enabled and self._fernet:
                self._data = [
                    json.loads(self._fernet.decrypt(item.encode()).decode())
                    for item in loaded_data
                ]
            else:
                self._data = [
                    json.loads(item) if isinstance(item, str) else item
                    for item in loaded_data
                ]

            self._meta.modified_at = time.time()
            logger.info(
                {"event": "array_load", "name": self.name, "size": len(self._data)}
            )
        except Exception as e:
            logger.error(
                {"event": "array_load_error", "name": self.name, "error": str(e)},
                exc_info=True,
            )
            array_errors_total.labels(
                backend_name=self.name, error_type="load", backend=self.storage_type
            ).inc()
            raise StorageError(f"Failed to load array data: {e}") from e

    async def append(self, item: Any) -> None:
        """
        Appends an item to the array.

        Args:
            item: The item to append (must be JSON-serializable).

        Raises:
            ArraySizeLimitError: If the array exceeds the maximum size.
            StorageError: If saving to storage fails.
        """
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_append_{self.name}"):
                if len(self._data) >= self._meta.size_limit:
                    array_errors_total.labels(
                        backend_name=self.name,
                        error_type="size_limit",
                        backend=self.storage_type,
                    ).inc()
                    raise ArraySizeLimitError(
                        f"Array size limit ({self._meta.size_limit}) exceeded"
                    )
                self._data.append(item)
                self._meta.modified_at = time.time()
                await self._save_to_storage()
                array_ops_total.labels(backend_name=self.name, operation="append").inc()
                array_op_time.labels(
                    backend_name=self.name, operation="append"
                ).observe(time.time() - start_time)
                array_size.labels(backend_name=self.name).set(len(self._data))
                logger.info(
                    {
                        "event": "array_append",
                        "name": self.name,
                        "item": str(item)[:100],
                    }
                )

    async def get(self, index: Optional[int] = None) -> Union[Any, List[Any]]:
        """
        Retrieves an item at a specific index or the entire array (paginated).

        Args:
            index: Optional index of the item to retrieve.

        Returns:
            The item at the specified index or the current page of the array.

        Raises:
            IndexError: If the index is out of range.
        """
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_get_{self.name}"):
                if index is not None:
                    if not (0 <= index < len(self._data)):
                        array_errors_total.labels(
                            backend_name=self.name,
                            error_type="index_out_of_range",
                            backend=self.storage_type,
                        ).inc()
                        raise IndexError(
                            f"Index {index} out of range for array of size {len(self._data)}"
                        )
                    return self._data[index]

                # Paginated retrieval
                start = self._current_page * self._page_size
                end = start + self._page_size

                array_ops_total.labels(backend_name=self.name, operation="get").inc()
                array_op_time.labels(backend_name=self.name, operation="get").observe(
                    time.time() - start_time
                )

                return self._data[start:end]

    async def update(self, index: int, item: Any) -> None:
        """
        Updates an item at a specific index.

        Args:
            index: The index of the item to update.
            item: The new item value.

        Raises:
            IndexError: If the index is out of range.
            StorageError: If saving to storage fails.
        """
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_update_{self.name}"):
                if not (0 <= index < len(self._data)):
                    array_errors_total.labels(
                        backend_name=self.name,
                        error_type="index_out_of_range",
                        backend=self.storage_type,
                    ).inc()
                    raise IndexError(
                        f"Index {index} out of range for array of size {len(self._data)}"
                    )
                self._data[index] = item
                self._meta.modified_at = time.time()
                await self._save_to_storage()
                array_ops_total.labels(backend_name=self.name, operation="update").inc()
                array_op_time.labels(
                    backend_name=self.name, operation="update"
                ).observe(time.time() - start_time)
                logger.info(
                    {
                        "event": "array_update",
                        "name": self.name,
                        "index": index,
                        "item": str(item)[:100],
                    }
                )

    async def delete(self, index: Optional[int] = None) -> None:
        """
        Delete an item at a specific index or clear the array.

        Args:
            index: Optional index of the item to delete. If None, the entire array is cleared.

        Raises:
            IndexError: If the index is out of range.
            StorageError: If saving to storage fails.
        """
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_delete_{self.name}"):
                if index is not None:
                    if not (0 <= index < len(self._data)):
                        array_errors_total.labels(
                            backend_name=self.name,
                            error_type="index_out_of_range",
                            backend=self.storage_type,
                        ).inc()
                        raise IndexError(
                            f"Index {index} out of range for array of size {len(self._data)}"
                        )
                    del self._data[index]
                    array_ops_total.labels(
                        backend_name=self.name, operation="delete"
                    ).inc()
                    logger.info(
                        {"event": "array_delete", "name": self.name, "index": index}
                    )
                else:
                    self._data.clear()
                    array_ops_total.labels(
                        backend_name=self.name, operation="clear"
                    ).inc()
                    logger.info({"event": "array_clear", "name": self.name})
                self._meta.modified_at = time.time()
                await self._save_to_storage()
                array_op_time.labels(
                    backend_name=self.name, operation="delete"
                ).observe(time.time() - start_time)
                array_size.labels(backend_name=self.name).set(len(self._data))

    async def query(self, condition: Callable[[Any], bool]) -> List[Any]:
        """
        Query the array with a condition function.

        Args:
            condition: A callable that takes an item and returns a boolean.

        Returns:
            A list of items that match the condition.
        """
        start_time = time.time()
        async with self._lock:
            with self._tracer.start_as_current_span(f"array_query_{self.name}"):
                results = [item for item in self._data if condition(item)]
                array_ops_total.labels(backend_name=self.name, operation="query").inc()
                array_op_time.labels(backend_name=self.name, operation="query").observe(
                    time.time() - start_time
                )
                logger.info(
                    {
                        "event": "array_query",
                        "name": self.name,
                        "result_count": len(results),
                    }
                )
                return results

    def meta(self) -> ArrayMeta:
        """
        Get the array metadata.

        Returns:
            An ArrayMeta dataclass instance.
        """
        # No lock needed as _meta is only mutated inside locked methods
        return self._meta

    async def rotate_encryption_key(self, new_key: bytes) -> None:
        """
        Rotates the encryption key for stored data. All stored data will be decrypted with the old key and re-encrypted with the new key.

        Args:
            new_key: The new Fernet key to use for encryption.

        Raises:
            StorageError: If key rotation fails at any point.
        """
        async with self._lock:
            if not self._meta.encryption_enabled:
                logger.warning(
                    {
                        "event": "key_rotation_skipped",
                        "reason": "Encryption not enabled",
                    }
                )
                return
            try:
                old_fernet = self._fernet
                self._fernet = Fernet(new_key)

                raw_data = await self._load_raw_from_storage()
                # 1. Decrypt all data in memory with old key
                decrypted_data = [
                    json.loads(old_fernet.decrypt(item.encode()).decode())
                    for item in raw_data
                ]

                # 2. Update in-memory data to decrypted state
                self._data = decrypted_data

                # 3. Save the newly encrypted data to storage (save will handle re-encryption)
                await self._save_to_storage()

                array_ops_total.labels(
                    backend_name=self.name, operation="key_rotation"
                ).inc()
                logger.info({"event": "array_key_rotation", "name": self.name})
            except Exception as e:
                logger.error(
                    {
                        "event": "array_key_rotation_error",
                        "name": self.name,
                        "error": str(e),
                    },
                    exc_info=True,
                )
                array_errors_total.labels(
                    backend_name=self.name,
                    error_type="key_rotation",
                    backend=self.storage_type,
                ).inc()
                raise StorageError(f"Key rotation failed: {e}") from e

    async def _load_raw_from_storage(self) -> List[str]:
        """Loads raw encrypted/unencrypted data from storage without decryption. Assumes lock is held."""
        if self.storage_type == "redis" and self._redis:
            data = await self._redis.get(f"array:{self.name}")
            return json.loads(data) if data else []
        elif self.storage_type == "sqlite":
            async with self._sqlite_pool.execute(
                "SELECT data FROM array_data ORDER BY id"
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
        elif self.storage_type == "postgres":
            rows = await self._postgres_client.execute(
                "SELECT data FROM array_data ORDER BY id"
            )
            return [row[0] for row in rows]
        else:
            storage_path = Path(self.storage_path)
            if not storage_path.exists():
                return []
            else:
                async with aiofiles.open(storage_path, "r") as f:
                    content = await f.read()
                    return json.loads(content) if content else []

    async def health_check(self) -> Dict[str, Any]:
        """Performs a health check on the backend."""
        # This operation is safe to do outside the lock unless inspecting in-memory state
        try:
            if self.storage_type == "redis" and self._redis:
                await self._redis.ping()
                return {"status": "healthy", "backend": "redis"}
            elif self.storage_type == "sqlite" and self._sqlite_pool:
                async with self._sqlite_pool.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
                return {"status": "healthy", "backend": "sqlite"}
            elif self.storage_type == "postgres" and self._postgres_client:
                await self._postgres_client.ping()
                return {"status": "healthy", "backend": "postgres"}
            else:
                if os.path.exists(self.storage_path):
                    return {"status": "healthy", "backend": "json"}
                return {
                    "status": "unhealthy",
                    "backend": "json",
                    "error": "Storage file missing",
                }
        except Exception as e:
            array_errors_total.labels(
                backend_name=self.name,
                error_type="health_check",
                backend=self.storage_type,
            ).inc()
            logger.error(
                {
                    "event": "array_health_check_error",
                    "name": self.name,
                    "error": str(e),
                },
                exc_info=True,
            )
            return {
                "status": "unhealthy",
                "backend": self.storage_type,
                "error": str(e),
            }

    def on_reload(self) -> None:
        """Handle reload event by refreshing storage."""

        async def _reload_task():
            async with self._lock:
                await self._load_from_storage()

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_reload_task())
        except RuntimeError:
            asyncio.run(_reload_task())
        logger.info({"event": "array_backend_reloaded", "name": self.name})

    async def start(self) -> None:
        """Start the array backend service."""
        await self.initialize()
        logger.info(f"ArrayBackend {self.name} started")

    def stop(self) -> None:
        """Stop the array backend service."""
        if self.executor:
            self.executor.shutdown(wait=False)
            self.executor = None

        # Closing async resources should be done in an event loop
        async def _async_stop():
            await self.close()

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(_async_stop())
            else:
                loop.run_until_complete(_async_stop())
        except RuntimeError:  # No running event loop
            asyncio.run(_async_stop())

        logger.info(f"ArrayBackend {self.name} stopped")

    def get_capabilities(self) -> Dict[str, Any]:
        """Get the capabilities of this array backend."""
        return {
            "name": self.name,
            "storage_type": self.storage_type,
            "supports_encryption": self._meta.encryption_enabled,
            "max_size": self._meta.size_limit,
            "page_size": self._page_size,
            "supports_query": True,
            "supports_pagination": True,
            "supports_persistence": True,
            "supported_storage_types": ["json", "sqlite", "redis", "postgres"],
        }


# Register with plugin registry only if not already registered
try:
    existing = registry.get(PlugInKind.CORE_SERVICE, "ArrayBackend")
    # If we get here without an exception, the plugin is already registered
    logger.debug(
        f"ArrayBackend already registered with version {existing.version if hasattr(existing, 'version') else 'unknown'}"
    )
except (AttributeError, KeyError):
    # Plugin not registered yet, so register it now
    try:
        registry.register(
            kind=PlugInKind.CORE_SERVICE,
            name="ArrayBackend",
            version="1.0.0",
            author="Arbiter Team",
        )(ConcreteArrayBackend)
        logger.info("ArrayBackend registered successfully")
    except ValueError as e:
        if "not newer than existing version" in str(e):
            # Already registered, which is fine
            logger.debug("ArrayBackend already registered")
        else:
            raise
