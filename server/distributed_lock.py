"""
Distributed Lock Utilities
==========================

This module provides distributed locking mechanisms using Redis to prevent
duplicate initialization and concurrent operations across multiple instances.

Key Features:
- Redis-based distributed locks using SET NX EX
- Automatic lock expiration for fault tolerance
- Context manager support for easy usage
- Startup lock for preventing duplicate container initialization
- Input validation and security checks
- Graceful degradation when Redis is unavailable

Usage:
    from server.distributed_lock import get_startup_lock, acquire_startup_lock
    
    # Using context manager (recommended)
    async with get_startup_lock() as acquired:
        if acquired:
            # Safe to initialize
            await initialize_agents()
    
    # Or manual acquisition
    lock = get_startup_lock()
    if await lock.acquire():
        try:
            await initialize_agents()
        finally:
            await lock.release()

Security Considerations:
    - REDIS_URL must use 'redis://' or 'rediss://' scheme
    - Lock names are sanitized and prefixed with 'lock:'
    - Unique lock values prevent accidental releases
    - Automatic expiration prevents deadlocks

Constants:
    MIN_LOCK_TIMEOUT: Minimum lock timeout (seconds)
    MAX_LOCK_TIMEOUT: Maximum lock timeout (seconds)
    MIN_RETRY_DELAY: Minimum retry delay (seconds)
    MAX_RETRY_DELAY: Maximum retry delay (seconds)
    MIN_MAX_RETRIES: Minimum retry attempts
    MAX_MAX_RETRIES: Maximum retry attempts

Examples:
    >>> # Create a lock with default settings
    >>> lock = DistributedLock("my_resource")
    >>> 
    >>> # Create a lock with custom settings
    >>> lock = DistributedLock(
    ...     "my_resource",
    ...     timeout=60,        # 60 second expiration
    ...     retry_delay=1.0,   # 1 second between retries
    ...     max_retries=5      # 5 retry attempts
    ... )
    >>> 
    >>> # Use with context manager
    >>> async with lock as acquired:
    ...     if acquired:
    ...         # Critical section
    ...         pass
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lock configuration constants
MIN_LOCK_TIMEOUT = 1  # Minimum lock timeout in seconds
MAX_LOCK_TIMEOUT = 3600  # Maximum lock timeout in seconds (1 hour)
MIN_RETRY_DELAY = 0.1  # Minimum retry delay in seconds
MAX_RETRY_DELAY = 60  # Maximum retry delay in seconds
MIN_MAX_RETRIES = 1  # Minimum number of retry attempts
MAX_MAX_RETRIES = 100  # Maximum number of retry attempts


class DistributedLock:
    """
    Distributed lock using Redis SET NX EX for atomic operations.
    
    This implementation uses Redis's atomic SET NX EX command to safely
    acquire locks across multiple processes/containers, with automatic
    expiration to prevent deadlocks from crashed processes.
    
    Attributes:
        lock_name: Name of the lock (Redis key)
        timeout: Lock expiration timeout in seconds
        retry_delay: Delay between lock acquisition attempts
        max_retries: Maximum number of acquisition attempts
    """
    
    def __init__(
        self,
        lock_name: str,
        timeout: int = 30,
        retry_delay: float = 0.5,
        max_retries: int = 3
    ) -> None:
        """
        Initialize a distributed lock.
        
        Args:
            lock_name: Name of the lock (will be prefixed with 'lock:')
            timeout: Lock expiration time in seconds (default: 30)
            retry_delay: Delay between acquisition attempts in seconds (default: 0.5)
            max_retries: Maximum number of acquisition attempts (default: 3)
            
        Raises:
            ValueError: If parameters are out of valid range
        """
        # Input validation
        if not lock_name or not lock_name.strip():
            raise ValueError("lock_name must be a non-empty string")
        if timeout <= MIN_LOCK_TIMEOUT - 1 or timeout > MAX_LOCK_TIMEOUT:
            raise ValueError(
                f"timeout must be between {MIN_LOCK_TIMEOUT} and {MAX_LOCK_TIMEOUT} seconds"
            )
        if retry_delay < MIN_RETRY_DELAY or retry_delay > MAX_RETRY_DELAY:
            raise ValueError(
                f"retry_delay must be between {MIN_RETRY_DELAY} and {MAX_RETRY_DELAY} seconds"
            )
        if max_retries < MIN_MAX_RETRIES or max_retries > MAX_MAX_RETRIES:
            raise ValueError(
                f"max_retries must be between {MIN_MAX_RETRIES} and {MAX_MAX_RETRIES}"
            )
        
        self.lock_name = f"lock:{lock_name.strip()}"
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.max_retries = max_retries
        self.lock_value = str(uuid.uuid4())  # Unique identifier for this lock holder
        self._acquired = False
        self._redis_client: Optional[Any] = None
    
    async def _get_redis_client(self) -> Optional[Any]:
        """
        Get or create Redis client.
        
        Returns:
            Redis client instance or None if Redis is not available
        """
        if self._redis_client is not None:
            return self._redis_client
        
        try:
            import redis.asyncio as redis
            
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            
            # Basic URL validation for security
            if not redis_url.startswith(("redis://", "rediss://")):
                logger.error(
                    f"Invalid REDIS_URL scheme. Must start with 'redis://' or 'rediss://'. "
                    f"Distributed locking disabled."
                )
                return None
            
            self._redis_client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=5,
                socket_timeout=5,
                decode_responses=True
            )
            
            # Test connection
            await self._redis_client.ping()
            logger.info(f"Connected to Redis for distributed lock: {self.lock_name}")
            
            return self._redis_client
            
        except ImportError:
            logger.warning("redis package not available - distributed locking disabled")
            return None
        except Exception as e:
            logger.warning(f"Failed to connect to Redis for distributed lock: {e}")
            logger.info("Continuing without distributed locking (single instance mode)")
            return None
    
    async def acquire(self, blocking: bool = False) -> bool:
        """
        Attempt to acquire the distributed lock.
        
        Args:
            blocking: If True, retry until lock is acquired or max_retries reached
        
        Returns:
            True if lock was acquired, False otherwise
        """
        client = await self._get_redis_client()
        
        # If Redis is not available, simulate successful lock acquisition
        # This allows the system to run in single-instance mode
        if client is None:
            logger.info(f"Lock '{self.lock_name}' acquired (no Redis, single instance mode)")
            self._acquired = True
            return True
        
        attempts = 0
        while attempts < self.max_retries:
            try:
                # Use Redis SET NX EX for atomic lock acquisition
                # SET key value NX EX seconds
                # - NX: Only set if key doesn't exist
                # - EX: Set expiration time
                acquired = await client.set(
                    self.lock_name,
                    self.lock_value,
                    nx=True,  # Only set if not exists
                    ex=self.timeout  # Expiration time
                )
                
                if acquired:
                    self._acquired = True
                    logger.info(
                        f"Distributed lock '{self.lock_name}' acquired "
                        f"(attempt {attempts + 1}/{self.max_retries}, "
                        f"expires in {self.timeout}s)"
                    )
                    return True
                
                # Lock is held by another instance
                if not blocking:
                    logger.info(
                        f"Lock '{self.lock_name}' is held by another instance "
                        f"(attempt {attempts + 1}/{self.max_retries})"
                    )
                    return False
                
                # Wait before retrying
                attempts += 1
                if attempts < self.max_retries:
                    logger.info(
                        f"Lock '{self.lock_name}' is busy, waiting {self.retry_delay}s "
                        f"before retry ({attempts}/{self.max_retries})"
                    )
                    await asyncio.sleep(self.retry_delay)
                
            except Exception as e:
                logger.error(f"Error acquiring lock '{self.lock_name}': {e}", exc_info=True)
                attempts += 1
                if not blocking or attempts >= self.max_retries:
                    return False
                await asyncio.sleep(self.retry_delay)
        
        logger.warning(
            f"Failed to acquire lock '{self.lock_name}' after {self.max_retries} attempts"
        )
        return False
    
    async def release(self) -> bool:
        """
        Release the distributed lock.
        
        Only releases the lock if this instance holds it (verified by lock_value).
        
        Returns:
            True if lock was released, False otherwise
        """
        if not self._acquired:
            return False
        
        client = await self._get_redis_client()
        
        # If Redis is not available, just mark as not acquired
        if client is None:
            self._acquired = False
            logger.info(f"Lock '{self.lock_name}' released (single instance mode)")
            return True
        
        try:
            # Use Lua script for atomic check-and-delete
            # This ensures we only delete the lock if we still own it
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            
            released = await client.eval(
                lua_script,
                1,  # Number of keys
                self.lock_name,  # KEYS[1]
                self.lock_value  # ARGV[1]
            )
            
            if released:
                self._acquired = False
                logger.info(f"Distributed lock '{self.lock_name}' released")
                return True
            else:
                logger.warning(
                    f"Lock '{self.lock_name}' could not be released "
                    "(may have expired or been acquired by another instance)"
                )
                self._acquired = False
                return False
                
        except Exception as e:
            logger.error(f"Error releasing lock '{self.lock_name}': {e}", exc_info=True)
            self._acquired = False
            return False
    
    async def __aenter__(self) -> bool:
        """Context manager entry: acquire the lock.
        
        Returns:
            True if lock was acquired, False otherwise
        """
        acquired = await self.acquire(blocking=True)
        return acquired
    
    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any]
    ) -> None:
        """Context manager exit: release the lock.
        
        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        await self.release()
    
    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis_client is not None:
            try:
                await self._redis_client.close()
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")


# Global startup lock instance
_startup_lock: Optional[DistributedLock] = None


def get_startup_lock() -> DistributedLock:
    """
    Get the global startup lock for preventing duplicate initialization.
    
    This lock is used to ensure only one container/instance initializes
    shared resources (agents, databases, etc.) at a time.
    
    Returns:
        DistributedLock instance for startup coordination
    """
    global _startup_lock
    
    if _startup_lock is None:
        # Create startup lock with appropriate timeout
        startup_timeout = int(os.getenv("STARTUP_TIMEOUT", "90"))
        _startup_lock = DistributedLock(
            lock_name="platform_startup",
            timeout=startup_timeout,
            retry_delay=1.0,
            max_retries=5
        )
    
    return _startup_lock


async def acquire_startup_lock(blocking: bool = True) -> bool:
    """
    Convenience function to acquire the startup lock.
    
    Args:
        blocking: If True, retry until lock is acquired
    
    Returns:
        True if lock was acquired, False otherwise
    """
    lock = get_startup_lock()
    return await lock.acquire(blocking=blocking)


async def release_startup_lock():
    """
    Convenience function to release the startup lock.
    """
    lock = get_startup_lock()
    await lock.release()
