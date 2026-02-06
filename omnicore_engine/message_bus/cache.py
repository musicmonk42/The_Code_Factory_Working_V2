# message_bus/cache.py

import threading
import time

import structlog

logger = structlog.get_logger(__name__)


class MessageCache:
    def __init__(self, maxsize=1000, ttl=3600):
        if maxsize <= 0:
            raise ValueError("maxsize must be positive.")
        if ttl <= 0:
            raise ValueError("ttl must be positive.")
        self.maxsize = maxsize
        self.ttl = ttl
        self.cache = {}
        self.access_times = {}
        self._lock = threading.Lock()
        logger.info("MessageCache initialized.", maxsize=maxsize, ttl=ttl)

    def __getstate__(self):
        """
        Prepare MessageCache for serialization (pickle protocol).

        This method is essential for multiprocessing and distributed testing scenarios,
        particularly with pytest-xdist's --forked mode. threading.Lock objects cannot
        be pickled as they are process-specific and bound to the parent process's
        memory space.

        Returns:
            dict: Object state without unpicklable synchronization primitives
        """
        state = self.__dict__.copy()
        # Remove the lock which cannot be pickled
        # It will be reconstructed in the target process via __setstate__
        state['_lock'] = None
        return state

    def __setstate__(self, state):
        """
        Restore MessageCache after deserialization in forked/spawned process.

        Reconstructs the threading.Lock that was excluded during pickling.
        This ensures the MessageCache is fully functional in the new process.

        Args:
            state: Pickled object state dictionary
        """
        self.__dict__.update(state)
        # Reconstruct the lock in the new process
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self.cache:
                if time.time() - self.access_times[key] > self.ttl:
                    self._remove(key)
                    return None
                self.access_times[key] = time.time()
                return self.cache[key]
            return None

    def put(self, key, value):
        with self._lock:
            if key in self.cache:
                self.cache[key] = value
                self.access_times[key] = time.time()
                return

            if len(self.cache) >= self.maxsize:
                self._evict()
            self.cache[key] = value
            self.access_times[key] = time.time()

    def _remove(self, key):
        if key in self.cache:
            del self.cache[key]
            del self.access_times[key]

    def _evict(self):
        now = time.time()
        expired_keys = [k for k, t in self.access_times.items() if (now - t) > self.ttl]

        if expired_keys:
            for key in expired_keys:
                self._remove(key)
            logger.debug(
                f"Evicted {len(expired_keys)} expired items from MessageCache."
            )
        elif self.cache:
            lru_key = min(self.access_times.items(), key=lambda item: item[1])[0]
            self._remove(lru_key)
            logger.debug(f"Evicted LRU item '{lru_key}' from MessageCache.")
