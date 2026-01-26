# Redis Configuration for Railway Deployment

## Railway Redis URL

The platform is configured to use Railway's internal Redis instance:

```
redis://default:XVzVgcZtDkrcPOlBwuTHdDLXKzoVmjsI@redis.railway.internal:6379
```

## Environment Variable

Set this in your Railway deployment environment variables:

```bash
REDIS_URL=redis://default:XVzVgcZtDkrcPOlBwuTHdDLXKzoVmjsI@redis.railway.internal:6379
```

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

async def test():
    client = aioredis.from_url("redis://default:XVzVgcZtDkrcPOlBwuTHdDLXKzoVmjsI@redis.railway.internal:6379")
    await client.ping()
    print("Redis connected!")
    await client.close()

asyncio.run(test())
```

## Security Note

The Redis password is stored in the URL for Railway deployments. In production:
- Store in Railway's environment variables (encrypted at rest)
- Never commit to version control
- Rotate periodically
