# Dependency Management Guide

This guide explains the dependencies used in the Code Factory platform, their purposes, and how to handle missing dependencies gracefully.

## Core vs Optional Dependencies

### Core Dependencies (requirements.txt)
These dependencies are required for basic platform operation and are installed by default:

- **FastAPI, Uvicorn**: Web framework and ASGI server
- **SQLAlchemy, Alembic**: Database ORM and migrations  
- **Pydantic**: Data validation
- **Redis, aioredis**: Caching and message bus
- **Prometheus, OpenTelemetry**: Metrics and observability
- **LangChain, LangGraph**: Agent orchestration
- **nest-asyncio**: Async event loop compatibility

### Optional Dependencies (requirements-optional.txt)

These dependencies are NOT required for basic operation. Install only if you need the specific feature:

#### 1. Hardware Security Module (HSM) Support
```bash
pip install python-pkcs11
```
Only needed if `ENABLE_HSM=1`

#### 2. Virtualization (libvirt)
```bash
# System dependencies required FIRST:
apt-get install -y libvirt-dev pkg-config

# Then install Python package:
pip install libvirt-python
```
Only needed if `ENABLE_LIBVIRT=1`

#### 3. Feature Store (Feast)
```bash
pip install feast
```
Already included in main requirements.txt. Only needed if `ENABLE_FEATURE_STORE=1`

#### 4. Message Queue Systems

**Kafka** (Already in main requirements):
```bash
pip install aiokafka confluent-kafka
```

**Additional message queues** (Optional):
```bash
pip install pulsar-client  # Apache Pulsar
```

#### 5. Heavy ML Dependencies

These are already in main requirements but load lazily:
- **Presidio**: PII detection and anonymization
- **SpaCy**: Natural language processing
- **Transformers**: Hugging Face transformers
- **Torch**: Deep learning framework

Set `LAZY_LOAD_ML=1` (default) to defer loading until first use.

## Dependency Loading Strategy

### 1. Conditional Imports
All optional dependencies use try/except pattern:

```python
try:
    import feast
    FEAST_AVAILABLE = True
except ImportError:
    FEAST_AVAILABLE = False
    logger.warning("Feast library not found. Feature store disabled.")
```

### 2. Feature Flags
Control which features are enabled via environment variables:

```bash
ENABLE_FEATURE_STORE=0  # Disable even if installed
ENABLE_HSM=0           # Disable even if installed
ENABLE_LIBVIRT=0       # Disable even if installed
```

### 3. Lazy Loading
Heavy ML libraries are loaded on first use:

```bash
LAZY_LOAD_ML=1  # Default - load on first use
LAZY_LOAD_ML=0  # Load all at startup (slower)
```

### 4. Mock Implementations
In development mode, missing dependencies fall back to mock implementations:

```python
if not FEAST_AVAILABLE:
    class FeatureStore:
        """Mock implementation for development."""
        def get_online_features(self, *args, **kwargs):
            logger.warning("Using mock FeatureStore")
            return {}
```

**IMPORTANT**: Mock implementations are NOT used in production mode (`PRODUCTION_MODE=1`).

## Handling Missing Dependencies

### Development Mode
Missing optional dependencies will:
1. Log a warning message
2. Fall back to mock implementation (if available)
3. Disable the feature gracefully

### Production Mode
Set `PRODUCTION_MODE=1` to:
1. Fail fast on missing required dependencies
2. Prevent mock implementations from being used
3. Log clear error messages

### Testing Mode
Set `TESTING=1` to:
1. Skip heavy initialization
2. Use stubs for external services
3. Speed up test execution

## Common Issues and Solutions

### Issue: "RuntimeError: no running event loop"
**Cause**: ShardedMessageBus initialized outside async context

**Solution**: Already fixed! The code now:
- Uses `asyncio.get_running_loop()` instead of deprecated `get_event_loop()`
- Applies `nest_asyncio` for compatibility
- Handles initialization in both sync and async contexts

### Issue: "Feast library not found"
**Cause**: Feast not installed or `ENABLE_FEATURE_STORE=0`

**Solution**:
```bash
pip install feast
export ENABLE_FEATURE_STORE=1
```

### Issue: "PolicyEngine initialization failed"
**Cause**: ArbiterConfig import failure

**Solution**: This is expected in some environments. The system will:
1. Use fallback configuration
2. Log a warning
3. Continue with degraded functionality

In production, ensure all required configs are set.

### Issue: "numpy.core deprecated"
**Status**: ✅ Already fixed! No deprecated numpy imports found.

### Issue: "NLTK data not found"
**Solution**: NLTK data should be pre-downloaded in Dockerfile:

```dockerfile
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"
```

At runtime, data will be downloaded automatically if missing (with caching).

### Issue: "Missing configurations: WEB3_PROVIDER_URL, SENTRY_DSN"
**Solution**: These are optional. Set only if using the feature:

```bash
# Blockchain logging (optional)
export WEB3_PROVIDER_URL="https://mainnet.infura.io/v3/YOUR-PROJECT-ID"
export ENABLE_BLOCKCHAIN_LOGGING=true

# Error tracking (recommended for production)
export SENTRY_DSN="https://your-sentry-dsn"
export SENTRY_ENVIRONMENT="production"
```

## Installation Profiles

### Minimal Installation
Basic functionality only:
```bash
pip install -r requirements.txt
```

### Full Installation
All optional dependencies:
```bash
pip install -r requirements.txt
pip install -r requirements-optional.txt
```

### Production Installation
```bash
pip install -r requirements.txt
# Add only the optional dependencies you need
pip install feast  # If using feature store
pip install python-pkcs11  # If using HSM
```

## Dependency Verification

Run the dependency checker to verify your installation:

```bash
python -c "from omnicore_engine.config_validator import ensure_configuration_valid; ensure_configuration_valid()"
```

This will:
- Check for required dependencies
- Validate configuration
- Report missing optional dependencies
- Provide installation instructions

## Docker Considerations

### Build Time vs Runtime

**Build Time** (Dockerfile):
- Install system dependencies (libvirt-dev, etc.)
- Install Python packages
- Download NLTK data
- Pre-compile models

**Runtime**:
- Load dependencies based on feature flags
- Use lazy loading for heavy ML libraries
- Fail fast on missing required configs

### Multi-Stage Builds

```dockerfile
# Stage 1: Full build with all dependencies
FROM python:3.11 as builder
RUN apt-get update && apt-get install -y libvirt-dev pkg-config
COPY requirements.txt .
RUN pip install -r requirements.txt

# Stage 2: Minimal runtime (no dev dependencies)
FROM python:3.11-slim
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
```

## Troubleshooting

### Check Installed Packages
```bash
pip list | grep -E "feast|libvirt|pkcs11|confluent|avro"
```

### Check Feature Flags
```bash
env | grep ENABLE_
```

### Check Logs
```bash
# Look for dependency warnings
grep -i "not found\|not available\|disabled" logs/*.log
```

### Verify Configuration
```python
from omnicore_engine.config_validator import log_configuration_status
log_configuration_status()
```

## Best Practices

1. **Use Feature Flags**: Control dependencies via environment variables
2. **Lazy Load**: Set `LAZY_LOAD_ML=1` for faster startup
3. **Production Mode**: Set `PRODUCTION_MODE=1` to prevent mock implementations
4. **Secrets Management**: Use AWS Secrets Manager, Vault, or similar in production
5. **Health Checks**: Implement health checks that verify dependency availability
6. **Monitoring**: Monitor for dependency-related warnings in logs
7. **Documentation**: Keep this guide updated as dependencies change

## Migration Path

If you're seeing dependency warnings, follow this migration path:

1. **Identify**: Check logs for "not found" or "disabled" messages
2. **Evaluate**: Determine if you need the feature
3. **Install**: Install the dependency if needed
4. **Configure**: Set the appropriate environment variable
5. **Test**: Verify the feature works as expected
6. **Monitor**: Watch for related errors or warnings

## Support

For issues not covered in this guide:
1. Check the logs for specific error messages
2. Review the configuration examples in `.env.example`
3. See production configuration in `.env.production.template`
4. **Check [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for comprehensive troubleshooting guide**
5. Open an issue with dependency details and error messages

## CI/CD Dependency Handling

All GitHub Actions workflows now implement robust dependency handling:

### System Dependencies Installation

All workflows automatically install required system packages before Python dependencies:

```yaml
- name: Install system dependencies for Python packages
  run: |
    echo "Installing system dependencies for Python packages..."
    sudo apt-get update
    sudo apt-get install -y libvirt-dev pkg-config
```

This ensures that packages like `libvirt-python` (if uncommented) can be installed successfully.

### Poetry → pip Fallback Mechanism

All workflows implement automatic fallback from Poetry to pip:

```yaml
# Simplified example - actual workflows include additional flags and error handling
- name: Install dependencies
  run: |
    # Try Poetry first
    if [ -f pyproject.toml ] && command -v poetry &> /dev/null; then
      poetry install --no-cache --no-root || {
        # Fallback to pip if Poetry fails
        pip install --no-cache-dir -r requirements.txt -c .github/constraints.txt
      }
    else
      # Use pip directly if Poetry not available
      pip install --no-cache-dir -r requirements.txt -c .github/constraints.txt
    fi
```

**Note**: The actual implementation includes retry logic, error messages, and hints for troubleshooting. The `--no-cache-dir` flag is used consistently to avoid cache-related issues. See the workflow files for complete details.

**Benefits**:
- Handles Poetry cache corruption gracefully
- Ensures builds don't fail due to Poetry-specific issues
- Maintains compatibility with both installation methods

### Clear Error Messages

When dependency installation fails, workflows now provide actionable error messages:

```
ERROR: pip installation failed. Check dependency conflicts.
Hint: Ensure system packages (libvirt-dev, pkg-config) are installed.
Hint: Check if any dependencies require specific system libraries.
```

This helps developers quickly identify and fix issues.

### Handling Optional Dependencies

The platform handles optional dependencies gracefully:

1. **libvirt-python**: Commented out by default in `requirements.txt`
   - System dependencies (libvirt-dev, pkg-config) are always installed in CI
   - Uncomment in requirements.txt only if libvirt functionality is needed

2. **Heavy ML dependencies**: Already included but use lazy loading
   - Set `LAZY_LOAD_ML=1` to defer loading until first use
   - Reduces startup time and memory usage

3. **Feature-specific dependencies**: Controlled by environment variables
   - `ENABLE_FEATURE_STORE=0/1`
   - `ENABLE_HSM=0/1`
   - `ENABLE_LIBVIRT=0/1`

### CI Workflow Best Practices

1. **Always use constraints file** to prevent version conflicts:
   ```bash
   pip install -r requirements.txt -c .github/constraints.txt
   ```

2. **Check for dependency conflicts** after installation:
   ```bash
   pip check || echo "WARNING: Dependency conflicts detected"
   ```

3. **Install critical packages** (self_fixing_engineer) in editable mode:
   ```bash
   pip install -e ./self_fixing_engineer
   pip install -e ./omnicore_engine
   ```

4. **Verify critical imports** before running tests:
   ```bash
   python -c "import arbiter; print('arbiter:', arbiter.__file__)"
   ```

See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for detailed CI/CD troubleshooting guidance.
