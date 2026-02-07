# Revert PR 860 - Fix GitHub Actions Workflow

## Problem

PR #860 broke the GitHub Actions workflow by adding invalid configuration to the Redis service container in `.github/workflows/pytest-all.yml`.

## Root Cause

PR #860 added the following to the Redis service:

```yaml
services:
  redis:
    image: redis:7-alpine
    options: >-
      --health-cmd "redis-cli ping"
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
      --memory="512m"
      --memory-swap="512m"
    ports:
      - 6379:6379
    # Configure Redis for low memory usage
    command: >
      redis-server
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
      --save ""
      --appendonly no
```

**The `command` field is NOT supported in GitHub Actions service containers!**

GitHub Actions services only support:
- `image`
- `options`
- `ports`
- `env`
- `volumes` (in some contexts)

The `command` field is docker-compose syntax and causes the workflow to fail.

## Solution

### Option 1: Remove the `command` field entirely (RECOMMENDED)

```yaml
services:
  redis:
    image: redis:7-alpine
    options: >-
      --health-cmd "redis-cli ping"
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
    ports:
      - 6379:6379
```

**Why this works**: The default Redis configuration is sufficient for tests. Memory limits can be handled at the OS level.

### Option 2: Use a custom Redis image with pre-configured settings

If Redis memory limits are critical, create a custom Docker image with the desired configuration baked in.

### Option 3: Pass Redis args via docker options (LIMITED)

```yaml
services:
  redis:
    image: redis:7-alpine
    options: >-
      --health-cmd "redis-cli ping"
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
      redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    ports:
      - 6379:6379
```

**Note**: This syntax may not work reliably in all GitHub Actions contexts.

## Changes to Revert

1. **Remove the `command` field** from Redis service
2. **Remove or fix `--memory` options** (these might work, but the command field definitely doesn't)
3. **Keep the good changes**:
   - Memory monitoring step (lines 571-590)
   - Timeout increase for Batch 2 (line 765, 786)
   - Test mocking in `test_arbiter_arbiter.py` (good for OOM prevention)
   - Cleanup fixture in `conftest.py` (good for memory management)

## Implementation

Edit `.github/workflows/pytest-all.yml` and remove lines 59-65 (the `command` field and comment).

Optionally, also remove the `--memory` and `--memory-swap` options from lines 55-56 if they cause issues.
