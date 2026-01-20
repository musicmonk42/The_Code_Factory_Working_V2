# Audit Logger Configuration for Containerized Deployments

## Problem Solved

This fix resolves the permission error that occurred when deploying to containerized environments (Railway, Docker, etc.) where the application runs as a non-root user and cannot write to `/var/log/`.

## Changes Made

### 1. Dockerfile Updates

The Dockerfile now creates audit log directories with proper ownership **before** switching to the non-root `appuser`:

```dockerfile
RUN mkdir -p /opt/venv /app /var/log/analyzer_audit /app/logs/analyzer_audit && \
    chown appuser:appuser /opt/venv /app /var/log/analyzer_audit /app/logs/analyzer_audit
```

This ensures that both `/var/log/analyzer_audit` and `/app/logs/analyzer_audit` are writable by the application.

### 2. Fallback Directory Logic

The `RegulatoryAuditLogger` class now includes intelligent fallback logic that tries directories in priority order:

1. **`/var/log/analyzer_audit`** - Production default (when running with proper permissions)
2. **`/app/logs/analyzer_audit`** - Container-friendly fallback
3. **`/tmp/analyzer_audit`** - Last resort for maximum compatibility

The logger tests each directory for writability and automatically selects the first one that works.

### 3. Environment Variable Support

You can now configure the audit log directory using the `AUDIT_LOG_DIR` environment variable:

```bash
# In your deployment configuration
AUDIT_LOG_DIR=/custom/path/to/audit/logs
```

This takes precedence over all other settings (except in testing mode).

## Configuration Priority

The audit directory is determined in this order:

1. **`TESTING` mode** - Always uses `/tmp/analyzer_audit` (highest priority for CI/CD)
2. **`AUDIT_LOG_DIR` environment variable** - Custom location
3. **Config dictionary** - Programmatic configuration
4. **Automatic fallback** - Tries `/var/log/analyzer_audit`, `/app/logs/analyzer_audit`, `/tmp/analyzer_audit`

## Usage Examples

### Railway Deployment

No configuration needed! The application will automatically use `/app/logs/analyzer_audit` when `/var/log/` is not writable.

### Docker Deployment

**Option 1: Use the built-in directories (recommended)**

```dockerfile
# The Dockerfile already creates these directories
# No additional configuration needed
```

**Option 2: Mount a volume**

```yaml
# docker-compose.yml
services:
  app:
    volumes:
      - audit-logs:/var/log/analyzer_audit
volumes:
  audit-logs:
```

**Option 3: Use environment variable**

```yaml
# docker-compose.yml
services:
  app:
    environment:
      - AUDIT_LOG_DIR=/app/custom-audit-logs
    volumes:
      - ./audit-logs:/app/custom-audit-logs
```

### Kubernetes Deployment

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: app
    env:
    - name: AUDIT_LOG_DIR
      value: /var/log/audit
    volumeMounts:
    - name: audit-logs
      mountPath: /var/log/audit
  volumes:
  - name: audit-logs
    persistentVolumeClaim:
      claimName: audit-logs-pvc
```

### Local Development

```bash
# Use a local directory
export AUDIT_LOG_DIR=/tmp/my-audit-logs
python -m uvicorn server.main:app
```

## Testing

The changes include comprehensive tests for all scenarios:

```bash
# Run the containerized deployment test suite
python /tmp/test_containerized_audit.py
```

Test scenarios include:
- Custom directory via `AUDIT_LOG_DIR`
- Automatic fallback mechanism
- Testing mode for CI/CD

## Compliance Notes

**Important**: This change maintains full regulatory compliance:

- ✅ All audit entries are still cryptographically signed with HMAC-SHA256
- ✅ Log integrity verification still runs continuously
- ✅ The fallback mechanism only activates when the primary directory is not writable
- ✅ A warning is logged when using fallback directories
- ✅ Production environments with proper permissions will still use `/var/log/analyzer_audit`

The fallback mechanism ensures the application can start in containerized environments while maintaining the same security guarantees.

## Troubleshooting

### Issue: Audit logger still failing with permission error

**Solution**: Ensure the Dockerfile changes are applied and rebuild your container:

```bash
docker build -t your-app:latest .
```

### Issue: Want to verify which directory is being used

**Solution**: Check the application logs. When using a fallback directory, you'll see:

```
WARNING: Using fallback audit directory: /app/logs/analyzer_audit (preferred /var/log/analyzer_audit not writable)
```

### Issue: Need to use a specific directory

**Solution**: Set the `AUDIT_LOG_DIR` environment variable:

```bash
export AUDIT_LOG_DIR=/your/custom/path
```

## Migration Guide

No migration is required. Existing deployments will continue to work:

- **Existing logs**: If you have existing logs in `/var/log/analyzer_audit`, they will continue to be used
- **New deployments**: Will automatically select the best available directory
- **Containerized deployments**: Will now work out of the box

## Related Files

- `Dockerfile` - Creates audit directories with proper ownership
- `self_fixing_engineer/self_healing_import_fixer/analyzer/core_audit.py` - Implements fallback logic
- `/tmp/test_containerized_audit.py` - Comprehensive test suite
