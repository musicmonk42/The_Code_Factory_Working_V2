# message_bus/cache.py

import threading
import time
import logging

logger = logging.getLogger(__name__)


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
