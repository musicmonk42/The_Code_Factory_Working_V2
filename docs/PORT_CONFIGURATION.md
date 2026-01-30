# Port Configuration Guide - Fixed for Railway Deployment

## Summary of Changes

✅ **Fixed port configuration in Docker and docker-compose to support Railway's dynamic PORT assignment**

### What Was Wrong
- `Dockerfile` had hardcoded port `8000` in CMD
- `docker-compose.yml` had hardcoded port `8000` in command
- When Railway builds the Docker image, it sets `PORT` env var (e.g., 8080), but the container ignored it

### What Was Fixed
1. **Dockerfile** - Now uses `${PORT:-8000}` pattern
2. **docker-compose.yml** - Now uses `$${PORT:-8000}` pattern (with YAML escaping)

## Configuration Status

| File | Status | Port Behavior |
|------|--------|---------------|
| `railway.toml` | ✅ Already correct | Uses `${PORT:-8000}` |
| `Procfile` | ✅ Already correct | Uses `${PORT:-8000}` |
| `server/run.py` | ✅ Already correct | Uses `os.environ.get("PORT", 8000)` |
| `Dockerfile` | ✅ **FIXED** | Now uses `${PORT:-8000}` |
| `docker-compose.yml` | ✅ **FIXED** | Now uses `$${PORT:-8000}` |

## How It Works

### Railway Deployment
```bash
# Railway sets PORT environment variable (e.g., PORT=8080)
# The container CMD will use that value:
CMD sh -c "python -m uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"

# Result: App binds to port 8080 (Railway's assigned port)
```

### Local Development
```bash
# No PORT env var set, defaults to 8000
docker run -p 8000:8000 code-factory:latest

# Result: App binds to port 8000
```

### Custom Port
```bash
# Explicitly set PORT
docker run -e PORT=8080 -p 8080:8080 code-factory:latest

# Result: App binds to port 8080
```

## Testing

### Verified Docker Build
```bash
✓ Docker image builds successfully
✓ Default port (8000) works when PORT not set
✓ Custom port works when PORT=8080 set
✓ docker-compose.yml syntax is valid
```

### Test Commands
```bash
# Test default port
docker run --rm test-port-config:latest sh -c 'echo "Port: ${PORT:-8000}"'
# Output: Port: 8000

# Test with PORT set
docker run --rm -e PORT=8080 test-port-config:latest sh -c 'echo "Port: ${PORT:-8000}"'
# Output: Port: 8080
```

## Answer to Your Question

> "the port was 8080. do I need to change it in railway to 8000?"

**No, you don't need to change anything in Railway!** 

The configuration is now correct. Railway will:
1. Inject its own `PORT` environment variable (could be 8080, 3000, or any other port)
2. The app will automatically bind to that port
3. Everything will work without any manual configuration in Railway

The `8000` is just a **fallback** for local development when Railway's PORT isn't set.

## Related Files Changed

1. `Dockerfile` - Line 144: Changed CMD to use `${PORT:-8000}`
2. `docker-compose.yml` - Line 64: Changed command to use `$${PORT:-8000}`
3. `PORT_CONFIGURATION.md` - This documentation file
