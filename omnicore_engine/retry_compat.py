"""
Compatibility layer for retry decorator.
Provides a retry decorator interface compatible with the 'retry' package,
but implemented using tenacity which is more actively maintained.
"""

from functools import wraps

try:
    from tenacity import retry as tenacity_retry
    from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential
    _HAS_TENACITY = True
except ImportError:
    _HAS_TENACITY = False
    tenacity_retry = None
    retry_if_exception_type = None
    stop_after_attempt = None
    wait_exponential = None


def retry(tries=3, delay=1, backoff=2, exceptions=(Exception,)):
    """
    Retry decorator compatible with the 'retry' package interface.

    Args:
        tries: Maximum number of attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay between retries
        exceptions: Tuple of exception types to catch

    Returns:
        Decorated function with retry logic
    """
    # If tenacity is not available, return a no-op decorator
    if not _HAS_TENACITY:
        def noop_decorator(func):
            return func
        return noop_decorator

    def decorator(func):
        @wraps(func)
        @tenacity_retry(
            stop=stop_after_attempt(tries),
            wait=wait_exponential(
                multiplier=delay, min=delay, max=delay * (backoff ** (tries - 1))
            ),
            retry=retry_if_exception_type(exceptions),
            reraise=True,
        )
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        @wraps(func)
        @tenacity_retry(
            stop=stop_after_attempt(tries),
            wait=wait_exponential(
                multiplier=delay, min=delay, max=delay * (backoff ** (tries - 1))
            ),
            retry=retry_if_exception_type(exceptions),
            reraise=True,
        )
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # Return appropriate wrapper based on whether function is async
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
