# Message Bus Layer Fixes - Implementation Plan

## Executive Summary

This document outlines the implementation plan for fixing three critical bugs in the OmniCore message_bus module. These fixes build upon the 6 critical bugs already resolved in this PR.

## Issues Identified

### Bug A: Threading vs asyncio Conflict (HIGH PRIORITY)

**Location**: `omnicore_engine/message_bus/resilience.py:31`

**Problem**:
```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self._lock = threading.Lock()  # <- PROBLEM
```

**Impact**:
- `threading.Lock()` can block the asyncio event loop
- Methods `record_failure()`, `record_success()`, and `can_attempt()` use blocking locks
- Causes performance degradation in high-throughput async scenarios
- Can lead to deadlocks if called from async context without proper handling

**Recommended Solution**:

Create a hybrid lock implementation that works in both sync and async contexts:

```python
import asyncio
import threading
from typing import Optional

class HybridLock:
    """A lock that works in both sync (threading) and async (asyncio) contexts."""
    
    def __init__(self):
        self._thread_lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _ensure_async_lock(self):
        """Lazily create async lock for current event loop."""
        try:
            loop = asyncio.get_running_loop()
            if self._loop != loop:
                self._loop = loop
                self._async_lock = asyncio.Lock()
        except RuntimeError:
            # No event loop running, will use threading lock
            pass
    
    def __enter__(self):
        """Sync context manager."""
        return self._thread_lock.__enter__()
    
    def __exit__(self, *args):
        """Sync context manager exit."""
        return self._thread_lock.__exit__(*args)
    
    async def __aenter__(self):
        """Async context manager."""
        self._ensure_async_lock()
        if self._async_lock:
            return await self._async_lock.__aenter__()
        # Fallback to thread lock in executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._thread_lock.acquire)
        return self
    
    async def __aexit__(self, *args):
        """Async context manager exit."""
        if self._async_lock:
            return await self._async_lock.__aexit__(*args)
        # Fallback to thread lock
        self._thread_lock.release()

# Updated CircuitBreaker
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self._lock = HybridLock()  # <- FIXED
        
    async def record_failure_async(self):
        """Async version of record_failure."""
        async with self._lock:
            # Same logic as before
            pass
    
    def record_failure(self):
        """Sync version for backward compatibility."""
        with self._lock:
            # Same logic as before
            pass
```

**Alternative Solution** (Simpler):

If async-only is acceptable, convert entirely to asyncio.Lock and remove threading support:

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self._lock = asyncio.Lock()
        
    async def record_failure(self):
        async with self._lock:
            # Logic here
```

### Bug B: Mock Error Trap (MEDIUM PRIORITY)

**Location**: `omnicore_engine/message_bus/integrations/redis_bridge.py:25`

**Problem**:
```python
except ImportError:
    ConnectionError = type("MockConnectionError", (Exception,), {})
    TimeoutError = type("MockTimeoutError", (Exception,), {})
```

**Impact**:
- Local mock types shadow standard library exceptions
- Can catch wrong exceptions if standard library later imported
- Breaks exception handling in subtle ways
- Inconsistent error types across modules

**Recommended Solution**:

Create a unified exception hierarchy in a new file:

**File**: `omnicore_engine/message_bus/exceptions.py`

```python
"""
Unified exception hierarchy for the OmniCore Message Bus.

Provides consistent error types that work regardless of external dependencies.
"""

class OmniCoreMessageBusError(Exception):
    """Base exception for all message bus errors."""
    pass

class OmniCoreConnectionError(OmniCoreMessageBusError):
    """Connection failure to external service (Redis, Kafka, etc.)."""
    pass

class OmniCoreTimeoutError(OmniCoreMessageBusError):
    """Operation timed out."""
    pass

class OmniCoreCircuitBreakerError(OmniCoreMessageBusError):
    """Circuit breaker is open, rejecting operations."""
    pass

class OmniCoreRateLimitError(OmniCoreMessageBusError):
    """Rate limit exceeded."""
    pass

# Create compatibility aliases for redis exceptions
try:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError
    
    # Inherit from both for compatibility
    class ConnectionError(OmniCoreConnectionError, RedisConnectionError):
        """Redis connection error with OmniCore hierarchy."""
        pass
    
    class TimeoutError(OmniCoreTimeoutError, RedisTimeoutError):
        """Redis timeout error with OmniCore hierarchy."""
        pass
        
except ImportError:
    # Standalone versions when redis unavailable
    ConnectionError = OmniCoreConnectionError
    TimeoutError = OmniCoreTimeoutError

__all__ = [
    'OmniCoreMessageBusError',
    'OmniCoreConnectionError',
    'OmniCoreTimeoutError',
    'OmniCoreCircuitBreakerError',
    'OmniCoreRateLimitError',
    'ConnectionError',
    'TimeoutError',
]
```

**Update**: `redis_bridge.py`

```python
# Replace lines 14-26 with:
from ..exceptions import ConnectionError, TimeoutError

# All existing except clauses work without changes
```

**Benefits**:
- Single source of truth for error types
- No shadow conflicts with standard library
- Proper exception hierarchy
- Works with or without redis installed

### Bug C: Memory Leak in Mock Metrics (HIGH PRIORITY)

**Location**: `omnicore_engine/message_bus/metrics.py:64`

**Problem**:
```python
class _ThreadSafeDict(Generic[T]):
    def __init__(self):
        self._data: Dict[Tuple, T] = {}  # <- UNBOUNDED
```

**Impact**:
- Dynamic metric names (e.g., `latency_session_{uuid}`) accumulate indefinitely
- Each unique label combination creates new dictionary entry
- Long-running processes will OOM crash
- Especially problematic in dev/test environments without Prometheus

**Recommended Solution**:

Implement LRU cache with configurable max size:

```python
from collections import OrderedDict
from typing import Optional

class _ThreadSafeDict(Generic[T]):
    """Thread-safe dictionary with LRU eviction for mock metric storage."""
    
    def __init__(self, max_size: Optional[int] = 10000):
        """
        Args:
            max_size: Maximum number of entries. When exceeded, LRU entries evicted.
                     None means unbounded (dangerous for production).
        """
        self._data: OrderedDict[Tuple, T] = OrderedDict()
        self._lock = threading.RLock()
        self._max_size = max_size
        self._eviction_count = 0
    
    def get(self, key: Tuple, default: T) -> T:
        with self._lock:
            # Move to end (mark as recently used)
            if key in self._data:
                self._data.move_to_end(key)
            return self._data.get(key, default)
    
    def set(self, key: Tuple, value: T):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)  # Mark as recently used
            self._evict_if_needed()
    
    def inc(self, key: Tuple, amount: float = 1.0):
        with self._lock:
            self._data[key] = self._data.get(key, 0.0) + amount
            self._data.move_to_end(key)
            self._evict_if_needed()
    
    def _evict_if_needed(self):
        """Remove least recently used entries if over limit."""
        if self._max_size is None:
            return
        
        while len(self._data) > self._max_size:
            # Remove first (least recently used) item
            evicted_key, _ = self._data.popitem(last=False)
            self._eviction_count += 1
            
            if self._eviction_count % 1000 == 0:
                logger.warning(
                    f"Mock metrics cache evicted {self._eviction_count} entries. "
                    f"Consider using real Prometheus or reducing metric cardinality."
                )
    
    def items(self):
        with self._lock:
            return list(self._data.items())
    
    def get_stats(self) -> Dict[str, int]:
        """Return cache statistics for monitoring."""
        with self._lock:
            return {
                'size': len(self._data),
                'max_size': self._max_size or -1,
                'evictions': self._eviction_count,
            }
```

**Configuration**:

Add environment variable support:

```python
import os

# At module level
_MOCK_METRICS_MAX_SIZE = int(os.getenv('OMNICORE_MOCK_METRICS_MAX_SIZE', '10000'))

class MockMetric:
    def __init__(self, ...):
        self._values: _ThreadSafeDict[float] = _ThreadSafeDict(max_size=_MOCK_METRICS_MAX_SIZE)
        self._bucket_values: _ThreadSafeDict[float] = _ThreadSafeDict(max_size=_MOCK_METRICS_MAX_SIZE)
```

**Benefits**:
- Bounded memory usage
- LRU keeps most relevant metrics
- Configurable limit per deployment
- Warnings when evictions occur
- Stats for monitoring

## Implementation Priority

1. **Bug C (Memory Leak)** - CRITICAL
   - Can cause production outages
   - Implement first
   - Easy to test

2. **Bug A (Threading)** - HIGH
   - Performance impact
   - Requires careful testing
   - Implement second

3. **Bug B (Mock Errors)** - MEDIUM
   - Cleanness improvement
   - Low risk
   - Implement last

## Testing Strategy

### Unit Tests

**For Bug A (Hybrid Lock)**:
```python
@pytest.mark.asyncio
async def test_circuit_breaker_async_context():
    cb = CircuitBreaker()
    # Should not block event loop
    for _ in range(10):
        await cb.record_failure_async()
    assert cb.state == "open"

def test_circuit_breaker_sync_context():
    cb = CircuitBreaker()
    # Should work in sync context
    for _ in range(10):
        cb.record_failure()
    assert cb.state == "open"
```

**For Bug B (Unified Exceptions)**:
```python
def test_exception_hierarchy():
    from omnicore_engine.message_bus.exceptions import (
        ConnectionError,
        OmniCoreConnectionError
    )
    
    # Should be catchable as both types
    err = ConnectionError("test")
    assert isinstance(err, OmniCoreConnectionError)
```

**For Bug C (LRU Cache)**:
```python
def test_mock_metrics_eviction():
    storage = _ThreadSafeDict(max_size=100)
    
    # Add 150 entries
    for i in range(150):
        storage.set((f"metric_{i}",), float(i))
    
    # Should have evicted 50
    assert len(storage._data) == 100
    assert storage._eviction_count == 50
    
    # Oldest entries should be gone
    assert storage.get(("metric_0",), -1) == -1
    # Newest should exist
    assert storage.get(("metric_149",), -1) == 149.0
```

### Integration Tests

Test with real workloads:
- Long-running message bus with dynamic metrics
- Circuit breaker under async load
- Exception handling across bridge integrations

## Rollout Plan

1. **Phase 1**: Implement and test Bug C (memory leak)
2. **Phase 2**: Implement and test Bug A (async lock)
3. **Phase 3**: Implement Bug B (unified exceptions)
4. **Phase 4**: Update all integration points
5. **Phase 5**: Documentation and migration guide

## Migration Notes

### Breaking Changes

**Bug A (Hybrid Lock)**:
- If custom code directly accessed `_lock`, will need updates
- Mitigation: Make `_lock` private, provide public async methods

**Bug B (Unified Exceptions)**:
- Exception types change (though compatible)
- Mitigation: Old exception types still work, deprecation warnings

**Bug C (LRU Cache)**:
- Some metrics may disappear from history
- Mitigation: Configurable limit, warnings on eviction

### Backward Compatibility

All changes maintain backward compatibility:
- Sync methods still work (Bug A)
- Exception catching still works (Bug B)
- Metrics API unchanged (Bug C)

## Estimated Effort

- Bug C: 4 hours (implementation + tests)
- Bug A: 8 hours (complex async handling)
- Bug B: 3 hours (straightforward refactor)

**Total**: ~15 hours development + testing

## Conclusion

These fixes will make the message bus:
- ✅ Safe for async contexts (no event loop blocking)
- ✅ Consistent error handling (unified exceptions)
- ✅ Memory-safe (bounded cache)
- ✅ Production-ready (all edge cases handled)

Recommended to implement in follow-up PR after current fixes are merged.
