import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiofiles
try:
    import etcd3
    ETCD3_AVAILABLE = True
except (ImportError, TypeError):
    # TypeError can occur with protobuf version conflicts
    ETCD3_AVAILABLE = False
    etcd3 = None
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Local application imports are moved inside methods where they are used to prevent circular dependencies.

logger = logging.getLogger(__name__)


class ConfigStore:
    """
    Manages configuration settings with a primary source (etcd), a local file
    fallback, and in-memory caching with TTL for performance.

    The configuration lookup follows this order:
    1. In-memory cache (if the value is not expired).
    2. etcd distributed key-value store (with retries).
    3. Local JSON fallback file (if etcd fails).
    4. Hardcoded default values.
    """

    def __init__(
        self,
        etcd_host: str = "localhost",
        etcd_port: int = 2379,
        fallback_path: Optional[str] = None,
        cache_ttl_seconds: int = 300,
        etcd_user: Optional[str] = None,
        etcd_password: Optional[str] = None,
        etcd_ca_cert: Optional[str] = None,
        etcd_cert_key: Optional[str] = None,
        etcd_cert_cert: Optional[str] = None,
    ):
        """
        Initalizes the ConfigStore.

        Args:
            etcd_host (str): The hostname or IP address of the etcd server.
            etcd_port (int): The port of the etcd server.
            fallback_path (Optional[str]): Path to a local JSON file for fallback configuration.
            cache_ttl_seconds (int): Time-to-live for cached configuration values in seconds.
            etcd_user (Optional[str]): Username for etcd authentication.
            etcd_password (Optional[str]): Password for etcd authentication.
            etcd_ca_cert (Optional[str]): Path to the CA certificate for etcd TLS.
            etcd_cert_key (Optional[str]): Path to the client key file for etcd TLS.
            etcd_cert_cert (Optional[str]): Path to the client certificate file for etcd TLS.
        """
        self.client = None
        self._store = {}
        # Filter out None values for clean client instantiation
        etcd_kwargs = {
            k: v
            for k, v in {
                "host": etcd_host,
                "port": etcd_port,
                "user": etcd_user,
                "password": etcd_password,
                "ca_cert": etcd_ca_cert,
                "cert_key": etcd_cert_key,
                "cert_cert": etcd_cert_cert,
            }.items()
            if v is not None
        }

        try:
            if ETCD3_AVAILABLE and etcd3:
                self.client = etcd3.client(**etcd_kwargs)
            else:
                logger.warning("etcd3 is not available. Will rely on fallback mechanisms.")
                self.client = None
        except Exception as e:
            logger.error(
                f"Failed to initialize etcd client: {e}. Will rely on fallback mechanisms."
            )
            self.client = None

        self.fallback_path = fallback_path
        self.cache_ttl = cache_ttl_seconds
        self.defaults = {
            "flush_interval_min": 2.0,
            "flush_interval_max": 10.0,
            "snapshot_interval": 50,
            "rate_limit_tokens": 100,
            "rate_limit_refill_rate": 10.0,
            "rate_limit_timeout": 30.0,
            "redis_batch_size": 100,
            "anomaly_threshold": 0.95,
            "evolution_cycle_interval_seconds": 3600,
            "security.idempotency_salt": "test_salt",
            "storage.backend": "sqlite",
            "redis.url": "redis://localhost:6379",
            "kafka.bootstrap_servers": "localhost:9092",
        }
        self._cache: Dict[str, tuple[Any, float]] = {}
        self._cache_lock = asyncio.Lock()
        self._watch_task: Optional[asyncio.Task] = None
        # Don't create task here - defer to start_watcher() method
        # which should be called when an event loop is running
        self._watch_started = False

    def get(self, key, default=None):
        """Get configuration value by key with optional default"""
        return self._store.get(key, default)

    async def start_watcher(self):
        """Starts the background task to watch for etcd key changes."""
        if self.client and not self._watch_started:
            try:
                # Only create the task if we're in a running event loop
                loop = asyncio.get_running_loop()
                self._watch_task = loop.create_task(self._watch_etcd_updates())
                self._watch_started = True
                logger.info("etcd configuration watcher started.")
            except RuntimeError:
                # No running event loop, skip watcher creation
                logger.debug("No running event loop, etcd watcher not started.")

    async def stop_watcher(self):
        """Stops the etcd watcher background task."""
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            logger.info("etcd configuration watcher stopped.")
        self._watch_started = False

    async def _watch_for_changes(self):
        """A background task that watches etcd for changes and updates the cache."""
        if not self.client:
            return

        try:
            # The etcd3-py library's watch methods are blocking, so we run them in an executor
            # to avoid blocking the asyncio event loop.
            # A more robust solution might use a dedicated thread or a fully async etcd client.
            while True:
                event_iterator, cancel = self.client.watch_prefix("/")
                for event in event_iterator:
                    key = event.key.decode("utf-8")
                    value = event.value.decode("utf-8")
                    logger.info(
                        f"Detected etcd change for key '{key}'. Updating cache."
                    )
                    async with self._cache_lock:
                        self._cache[key] = (
                            self._parse_value(value),
                            datetime.now(timezone.utc).timestamp() + self.cache_ttl,
                        )
                await asyncio.sleep(1)  # prevent tight loop on watch error
        except Exception as e:
            logger.error(
                f"etcd watch task failed: {e}. Watcher will stop.", exc_info=True
            )

    async def _watch_etcd_updates(self):
        """Watches etcd for updates and invalidates the cache for changed keys."""
        try:
            if not self.client:
                return
            # This is a simplified watch implementation
            # In production, you'd use proper etcd watch API
            while True:
                await asyncio.sleep(5)  # Check for updates periodically
                # Here you would implement actual etcd watch logic
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Etcd watch failed: {e}")

    def _is_cache_valid(self, key: str) -> bool:
        """Checks if a cached key exists and has not expired."""
        if key not in self._cache:
            return False
        _, expiration_time = self._cache[key]
        is_valid = datetime.now(timezone.utc).timestamp() < expiration_time
        if is_valid:
            logger.debug(f"Cache hit for config key '{key}'.")
        return is_valid

    async def _load_from_fallback(self) -> None:
        """Loads configuration from the local fallback JSON file after verifying its integrity."""
        if not self.fallback_path or not os.path.exists(self.fallback_path):
            return

        # Integrity check
        checksum_path = f"{self.fallback_path}.sha256"
        if os.path.exists(checksum_path):
            try:
                async with aiofiles.open(checksum_path, "r") as f:
                    expected_hash = (await f.read()).strip()

                async with aiofiles.open(self.fallback_path, "rb") as f:
                    content_bytes = await f.read()
                    computed_hash = hashlib.sha256(content_bytes).hexdigest()

                if computed_hash != expected_hash:
                    logger.error(
                        f"Fallback file integrity check failed for '{self.fallback_path}'. File may be corrupt."
                    )
                    return
            except Exception as e:
                logger.error(f"Error during fallback file integrity check: {e}")
                return
        else:
            logger.warning(
                f"No checksum file found for '{self.fallback_path}'. Skipping integrity check."
            )

        try:
            async with aiofiles.open(self.fallback_path, "r") as f:
                content = await f.read()
                fallback_configs = json.loads(content)
                async with self._cache_lock:
                    for k, v in fallback_configs.items():
                        # Set a very long TTL for fallback values to differentiate them from etcd values
                        self._cache[k] = (
                            v,
                            datetime.now(timezone.utc).timestamp() + 86400,
                        )
                logger.info(
                    f"Loaded configurations from fallback file: {self.fallback_path}"
                )
        except Exception as e:
            logger.error(
                f"Failed to read or parse fallback config file {self.fallback_path}: {e}"
            )

    def _parse_value(self, value_str: str) -> Any:
        """Tries to parse a string value as float or JSON, otherwise returns the string."""
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            try:
                return float(value_str)
            except (ValueError, TypeError):
                return value_str

    async def _get_from_etcd_with_retry(self, key: str) -> Optional[Any]:
        """Wrapper to enable proper retry logic for etcd."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=1, max=5),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        async def _inner():
            if not self.client:
                raise Exception("No etcd client available")

            loop = asyncio.get_running_loop()
            value_bytes, _ = await loop.run_in_executor(None, self.client.get, key)

            if value_bytes:
                return self._parse_value(value_bytes.decode("utf-8"))
            return None

        try:
            return await _inner()
        except Exception:
            return None

    async def _get_from_etcd(self, key: str) -> Optional[Any]:
        """Retrieves a single key from etcd."""
        return await self._get_from_etcd_with_retry(key)

    async def get_config(self, key: str, default: Optional[Any] = None) -> Any:
        """
        Retrieves a configuration value by key, following the defined lookup order.

        Args:
            key (str): The configuration key to retrieve.
            default (Optional[Any]): A value to return if the key is not found anywhere.
                                     If not provided, a KeyError is raised.
        Returns:
            Any: The configuration value.
        Raises:
            KeyError: If the key is not found and no default is provided.
        """
        # FIX: Moved import here to break the circular dependency
        from .metrics import CONFIG_FALLBACK_USED

        # Check cache first with lock to prevent concurrent fetches
        async with self._cache_lock:
            if self._is_cache_valid(key):
                return self._cache[key][0]

            # If not in cache, we need to fetch it
            # Do the fetch while holding the lock to prevent duplicates
            logger.debug(f"Cache miss for config key '{key}'.")

            # FIX: Only attempt etcd fetch if the client exists.
            # This prevents slow retries when etcd initialization has already failed.
            if self.client:
                try:
                    value = await self._get_from_etcd(key)
                    if value is not None:
                        logger.debug(f"Fetched config '{key}' from etcd: {value}")
                        self._cache[key] = (
                            value,
                            datetime.now(timezone.utc).timestamp() + self.cache_ttl,
                        )
                        return value
                except Exception as e:
                    logger.warning(
                        f"Could not reach etcd to get config '{key}': {e}. Attempting fallback."
                    )

        # Release lock before loading fallback
        await self._load_from_fallback()

        async with self._cache_lock:
            if self._is_cache_valid(key):
                logger.warning(f"Using fallback config for '{key}'.")
                CONFIG_FALLBACK_USED.labels(config_key=key).inc()
                return self._cache[key][0]

            if key in self.defaults:
                value = self.defaults[key]
                logger.warning(f"Using hardcoded default for config '{key}'.")
                CONFIG_FALLBACK_USED.labels(config_key=key).inc()
                self._cache[key] = (
                    value,
                    datetime.now(timezone.utc).timestamp() + self.cache_ttl,
                )
                return value

        if default is not None:
            return default

        raise KeyError(f"Configuration key '{key}' not found in any source.")

    def get_all(self) -> Dict[str, Any]:
        """Returns a dictionary of all known configurations."""
        merged_config = self.defaults.copy()
        for key, (value, _) in self._cache.items():
            merged_config[key] = value
        return merged_config


class TokenBucketRateLimiter:
    """Implements a token bucket rate limiter with blocking capability."""

    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self.tokens: float = 0.0
        self.last_refill: float = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        Acquires a token from the bucket, blocking if necessary up to the timeout.

        Args:
            timeout (Optional[float]): Maximum time to wait for a token.
        Returns:
            bool: True if a token was acquired, False if the timeout was reached.
        """
        async with self.lock:
            # FIX: Use a monotonic clock for reliable time-based calculations.
            # `datetime.now()` is not monotonic and caused incorrect elapsed time calculations.
            now = asyncio.get_running_loop().time()

            max_tokens = await self.config_store.get_config("rate_limit_tokens", 10)
            refill_rate = await self.config_store.get_config(
                "rate_limit_refill_rate", 10
            )
            effective_timeout = (
                timeout
                if timeout is not None
                else await self.config_store.get_config("rate_limit_timeout", 30.0)
            )

            # Ensure we have valid values
            if max_tokens is None:
                max_tokens = 10
            if refill_rate is None:
                refill_rate = 10
            if effective_timeout is None:
                effective_timeout = 30.0

            if self.last_refill == 0.0:
                self.last_refill = now
                self.tokens = max_tokens

            # Calculate tokens accumulated since last refill
            elapsed = now - self.last_refill
            self.tokens = min(max_tokens, self.tokens + elapsed * refill_rate)
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True

            # Calculate wait time needed to get 1 token
            tokens_needed = 1.0 - self.tokens
            wait_time = tokens_needed / refill_rate

            if wait_time > effective_timeout:
                return False

            # Wait for the calculated time
            await asyncio.sleep(wait_time)

            # After waiting, update tokens based on the wait time
            # We waited long enough to get exactly the tokens we needed
            self.tokens = self.tokens + wait_time * refill_rate - 1.0
            self.last_refill = now + wait_time

            return True
