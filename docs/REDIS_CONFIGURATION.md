<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Redis Configuration for Railway Deployment

## Railway Redis URL

The platform is configured to use Railway's internal Redis instance.

**IMPORTANT**: The actual Redis credentials are stored securely in Railway's environment variables. Never commit passwords to version control.

Example format:
```
redis://default:<REDIS_PASSWORD>@redis.railway.internal:6379
```

## Environment Variable

Set this in your Railway deployment environment variables:

```bash
REDIS_URL=redis://default:<your-redis-password>@redis.railway.internal:6379
```

Replace `<your-redis-password>` with the actual password from Railway's Redis service configuration.

## Fallback Behavior

The platform includes robust error handling for Redis connectivity:

1. **CacheManager**: Falls back to in-memory caching when Redis is unavailable
2. **DistributedRateLimiter**: Disables rate limiting when Redis is unavailable (fail-open)

This ensures the platform continues to function even when Redis is not accessible.

## Testing

To test Redis connectivity:

```python
import asyncio
import redis.asyncio as aioredis
import os

async def test():
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    client = aioredis.from_url(redis_url)
    await client.ping()
    print("Redis connected!")
    await client.close()

asyncio.run(test())
```

## Security Note

The Redis password is stored in Railway's environment variables for production deployments:
- Encrypted at rest in Railway
- Never commit to version control
- Rotate periodically for security
- Access via `os.environ.get("REDIS_URL")` in code
