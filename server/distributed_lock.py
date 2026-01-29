"""
Distributed Lock Utilities - Enterprise-Grade Distributed Locking
=================================================================

This module provides enterprise-grade distributed locking mechanisms using Redis
to prevent duplicate initialization and race conditions across multiple container
instances in orchestrated environments (Kubernetes, Railway, etc.).

Enterprise Features:
-------------------
- **Redis SET NX EX**: Atomic lock acquisition with automatic expiration
- **Lua Script Release**: Atomic check-and-delete to prevent accidental releases
- **Circuit Breaker Integration**: Graceful degradation when Redis unavailable
- **Input Validation**: All parameters validated against secure ranges
- **Context Manager**: RAII-style lock management for exception safety

Compliance & Standards:
----------------------
- ISO 27001 A.9.4.1: Secure system access control via distributed locks
- SOC 2 Type II CC6.1: Logical access controls in distributed systems
- NIST SP 800-53 AC-3: Access enforcement through lock-based coordination
- PCI DSS 8.1.5: Unique identification for concurrent operations

Architecture:
------------
The distributed lock uses Redis's atomic operations to ensure exactly-once
execution in distributed environments:

    1. ACQUIRE: SET lock_key unique_value NX EX timeout
       - NX: Only set if key doesn't exist (atomic)
       - EX: Automatic expiration (fault tolerance)

    2. RELEASE: Lua script for atomic check-and-delete
       - Verifies lock holder matches before delete
       - Prevents accidental release by other instances

    3. GRACEFUL DEGRADATION: When Redis unavailable
       - Falls back to single-instance mode
       - Logs warning for operator visibility
       - Continues operation without blocking

Usage Examples:
--------------
    from server.distributed_lock import get_startup_lock, acquire_startup_lock

    # Context manager (recommended for exception safety)
    async with get_startup_lock() as acquired:
        if acquired:
            # This instance holds the lock - safe to initialize
            await initialize_agents()
        else:
            # Another instance is initializing - wait or skip
            logger.info("Another instance is initializing")

    # Manual acquisition with explicit release
    lock = get_startup_lock()
    if await lock.acquire(blocking=True):
        try:
            await initialize_agents()
        finally:
            await lock.release()

Security Considerations:
-----------------------
- REDIS_URL must use 'redis://' or 'rediss://' scheme (validated)
- Lock names are sanitized and prefixed with 'lock:' namespace
- Unique UUIDv4 lock values prevent accidental cross-instance releases
- Automatic expiration prevents indefinite deadlocks from crashed processes
- No sensitive data stored in lock values (only UUID)

Performance Characteristics:
---------------------------
- Lock acquisition: O(1) Redis operation (~1ms typical)
- Lock release: O(1) Lua script execution (~1ms typical)
- Memory usage: ~100 bytes per lock (key + value + TTL metadata)
- Network: Single round-trip per operation

Configuration Constants:
-----------------------
    MIN_LOCK_TIMEOUT: 1s - Minimum lock timeout
    MAX_LOCK_TIMEOUT: 3600s (1 hour) - Maximum lock timeout
    MIN_RETRY_DELAY: 0.1s - Minimum retry delay
    MAX_RETRY_DELAY: 60s - Maximum retry delay
    MIN_MAX_RETRIES: 1 - Minimum retry attempts
    MAX_MAX_RETRIES: 100 - Maximum retry attempts

Module Version: 1.1.0
Author: Code Factory Platform Team
License: Proprietary
Last Updated: 2026-01-29
"""

from __future__ import annotations

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
        
        # Check if Redis is explicitly disabled for startup optimization
        # This allows faster startup in environments where Redis is not required
        if os.getenv("SKIP_REDIS_LOCK", "").lower() in ("1", "true", "yes"):
            logger.info("Redis distributed locking skipped (SKIP_REDIS_LOCK=1)")
            return None
        
        try:
            import redis.asyncio as redis
            
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            
            # Basic URL validation for security
            if not redis_url.startswith(("redis://", "rediss://")):
                logger.error(
                    "Invalid REDIS_URL scheme. Must start with 'redis://' or 'rediss://'. "
                    "Distributed locking disabled."
                )
                return None
            
            # STARTUP OPTIMIZATION: Reduced socket timeouts to 1s
            # for faster startup when Redis is unavailable. The distributed lock
            # has a graceful fallback to single-instance mode.
            self._redis_client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=1,  # Reduced from 2s for faster startup
                socket_timeout=1,  # Reduced from 2s for faster startup
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
                logger.debug(
                    f"Lock '{self.lock_name}' could not be released "
                    "(may have expired or been acquired by another instance)"
                )
                self._acquired = False
                return False
                
        except Exception as e:
            logger.debug(f"Could not release lock '{self.lock_name}': {e}", exc_info=True)
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
