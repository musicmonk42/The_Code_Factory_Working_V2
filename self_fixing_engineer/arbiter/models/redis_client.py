import aioredis

class RedisClient:
    @staticmethod
    async def connect(url):
        # _maybe_await safeguard
        url = url if isinstance(url, str) else url()
        return await aioredis.from_url(url)
