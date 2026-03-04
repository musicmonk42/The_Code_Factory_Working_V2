import aioredis
import asyncio

class RedisClient:
    def __init__(self, url):
        self.url = url

    async def connect(self):
        return await aioredis.from_url(self.url)

    async def await_if_awaitable(self, value):
        if asyncio.iscoroutine(value):
            return await value
        return value

    async def health_check(self):
        try:
            connection = await self.await_if_awaitable(self.connect())
            # Assume some health check logic here
        except Exception as e:
            print(f'Health check failed: {e}')  
            return False
        return True