# Quick Fix Guide: OpenTelemetry TypeError with Elasticsearch

## Problem
If you see this error when running tests:
```
TypeError: TracerProvider.get_tracer() takes from 2 to 4 positional arguments but 5 were given
```

## Quick Solution

1. **Update your environment:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt --upgrade
   ```

2. **Verify the fix:**
   ```bash
   python3 -c "from elasticsearch import Elasticsearch; print('✓ Success')"
   ```

## Why This Happens
- You have an older version of `opentelemetry-api` (< 1.27.0) installed
- Elasticsearch 9.2.0 requires OpenTelemetry API >= 1.27.0
- The method signature changed to include an `attributes` parameter

## Detailed Documentation
See [docs/OPENTELEMETRY_FIX.md](./OPENTELEMETRY_FIX.md) for complete details.

## Still Having Issues?
If the above doesn't work, try:

1. **Clear pip cache:**
   ```bash
   pip cache purge
   ```

2. **Reinstall OpenTelemetry packages:**
   ```bash
   pip uninstall -y opentelemetry-api opentelemetry-sdk
   pip install "opentelemetry-api>=1.27.0,<2.0.0" "opentelemetry-sdk>=1.27.0,<2.0.0"
   ```

3. **Check installed versions:**
   ```bash
   pip show opentelemetry-api opentelemetry-sdk | grep Version
   ```
   
   You should see versions >= 1.27.0
