# Environment Variables Reference Guide

## Overview
This document provides a comprehensive reference for all environment variables used by The Code Factory platform. Variables are organized by category and include their purpose, default values, and recommended settings for different environments.

---

## Core Application Settings

### APP_ENV
- **Purpose:** Defines the application environment
- **Type:** String
- **Values:** `development`, `staging`, `production`
- **Default:** `development`
- **Production:** `production`
- **Example:** `APP_ENV=production`
- **Impact:** Changes error handling, logging verbosity, and fallback behavior

### DEV_MODE
- **Purpose:** Enables/disables development mode features
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `1` in development, `0` in production
- **Production:** `0`
- **Example:** `DEV_MODE=0`
- **Impact:** Controls debug features, hot reload, and verbose logging

### PRODUCTION_MODE
- **Purpose:** Legacy production mode flag (use APP_ENV instead)
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1`
- **Example:** `PRODUCTION_MODE=1`
- **Impact:** Same as APP_ENV=production

---

## Startup Configuration

### APP_STARTUP
- **Purpose:** Skips heavy initialization during startup
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `1`
- **Production:** `1`
- **Example:** `APP_STARTUP=1`
- **Impact:** Prevents plugin loading during import to speed up startup

### SKIP_IMPORT_TIME_VALIDATION
- **Purpose:** Skips validation during module imports
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `1`
- **Production:** `1`
- **Example:** `SKIP_IMPORT_TIME_VALIDATION=1`
- **Impact:** Reduces startup time by deferring validation

---

## Testing and CI/CD

### PYTEST_CURRENT_TEST
- **Purpose:** Set automatically by pytest during test execution
- **Type:** String (test node ID)
- **Values:** Varies (e.g., `tests/test_example.py::test_function`)
- **Default:** Not set
- **Production:** Should NEVER be set
- **Example:** `PYTEST_CURRENT_TEST=tests/test_startup.py::test_init`
- **Impact:** Causes conditional skipping of heavy initialization

### PYTEST_COLLECTING
- **Purpose:** Indicates pytest is collecting tests
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** Not set (pytest sets automatically)
- **Production:** Should NEVER be set
- **Example:** `PYTEST_COLLECTING=1`
- **Impact:** Skips event loop and async initialization during test collection

### CI
- **Purpose:** Indicates code is running in CI environment
- **Type:** String (usually "true" or "1")
- **Values:** `true`, `1`, etc.
- **Default:** Not set
- **Production:** Should NOT be set
- **Example:** `CI=true`
- **Impact:** May trigger CI-specific behaviors

---

## Security and Authentication

### AGENTIC_AUDIT_HMAC_KEY
- **Purpose:** HMAC key for audit log signing
- **Type:** String (hex-encoded key)
- **Values:** 64-character hexadecimal string
- **Default:** None (REQUIRED in production)
- **Production:** **REQUIRED** - Use strong random key
- **Example:** `AGENTIC_AUDIT_HMAC_KEY=7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a`
- **Impact:** Critical for audit log integrity
- **Security:** Store in secrets manager, rotate regularly

### ENCRYPTION_KEY
- **Purpose:** Fernet encryption key for data at rest
- **Type:** String (base64-encoded key)
- **Values:** Fernet-compatible key
- **Default:** None (generated if not provided)
- **Production:** **REQUIRED** - Use strong random key
- **Example:** `ENCRYPTION_KEY=abc123...`
- **Impact:** Encrypts sensitive data in database
- **Security:** Store in secrets manager, NEVER commit to git

### KMS_KEY_ID
- **Purpose:** AWS KMS key ID for encryption
- **Type:** String (ARN or alias)
- **Values:** AWS KMS key identifier
- **Default:** None
- **Production:** Recommended for AWS deployments
- **Example:** `KMS_KEY_ID=arn:aws:kms:us-east-1:123456789012:key/abc-123`
- **Impact:** Enables hardware-backed encryption
- **Security:** Use IAM roles, not hardcoded credentials

---

## Database Configuration

### DB_PATH / DATABASE_URL
- **Purpose:** Database connection string
- **Type:** String (database URL)
- **Values:** SQLAlchemy-compatible URL
- **Default:** `sqlite:///./omnicore.db`
- **Production:** PostgreSQL recommended: `postgresql+asyncpg://user:pass@host:5432/dbname`
- **Example:** `DB_PATH=postgresql+asyncpg://user:pass@localhost:5432/codefactory`
- **Impact:** Determines database backend
- **Security:** Use environment-specific databases

### DB_POOL_SIZE
- **Purpose:** Database connection pool size
- **Type:** Integer
- **Values:** 1-100
- **Default:** `50`
- **Production:** Tune based on load (50-100)
- **Example:** `DB_POOL_SIZE=50`
- **Impact:** Affects concurrent database operations

### DB_POOL_MAX_OVERFLOW
- **Purpose:** Maximum overflow connections beyond pool size
- **Type:** Integer
- **Values:** 0-50
- **Default:** `20`
- **Production:** `20-50`
- **Example:** `DB_POOL_MAX_OVERFLOW=20`
- **Impact:** Handles traffic spikes

### DB_RETRY_ATTEMPTS
- **Purpose:** Number of retry attempts for database operations
- **Type:** Integer
- **Values:** 1-10
- **Default:** `3`
- **Production:** `3-5`
- **Example:** `DB_RETRY_ATTEMPTS=3`
- **Impact:** Improves reliability with transient failures

### DB_RETRY_DELAY
- **Purpose:** Delay between retry attempts (seconds)
- **Type:** Float
- **Values:** 0.1-10.0
- **Default:** `1.0`
- **Production:** `1.0-2.0`
- **Example:** `DB_RETRY_DELAY=1.0`
- **Impact:** Prevents overwhelming failed systems

---

## Message Bus Configuration

### MESSAGE_BUS_SHARD_COUNT
- **Purpose:** Number of message bus shards
- **Type:** Integer
- **Values:** 1-100
- **Default:** `4`
- **Production:** Tune based on load (4-16)
- **Example:** `MESSAGE_BUS_SHARD_COUNT=8`
- **Impact:** Affects message processing parallelism

### MESSAGE_BUS_WORKERS_PER_SHARD
- **Purpose:** Worker threads per shard
- **Type:** Integer
- **Values:** 1-20
- **Default:** `4`
- **Production:** `4-8`
- **Example:** `MESSAGE_BUS_WORKERS_PER_SHARD=4`
- **Impact:** Controls concurrent message processing

### ENABLE_MESSAGE_BUS_GUARDIAN
- **Purpose:** Enables message bus health monitoring
- **Type:** Boolean (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1`
- **Example:** `ENABLE_MESSAGE_BUS_GUARDIAN=1`
- **Impact:** Monitors and restarts failed dispatchers

### MESSAGE_BUS_GUARDIAN_INTERVAL
- **Purpose:** Guardian check interval (seconds)
- **Type:** Integer
- **Values:** 10-300
- **Default:** `30`
- **Production:** `30-60`
- **Example:** `MESSAGE_BUS_GUARDIAN_INTERVAL=30`
- **Impact:** Frequency of health checks

---

## Feature Flags

### ENABLE_HSM
- **Purpose:** Enables Hardware Security Module support
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1` if HSM available
- **Example:** `ENABLE_HSM=1`
- **Impact:** Requires python-pkcs11 and HSM hardware
- **Dependencies:** `pip install python-pkcs11`

### ENABLE_LIBVIRT
- **Purpose:** Enables libvirt virtualization support
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1` if virtualization needed
- **Example:** `ENABLE_LIBVIRT=1`
- **Impact:** Requires libvirt-python and system packages
- **Dependencies:** `apt-get install libvirt-dev pkg-config && pip install libvirt-python`

### ENABLE_KAFKA
- **Purpose:** Enables Kafka message bridge
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1` if using Kafka
- **Example:** `ENABLE_KAFKA=1`
- **Impact:** Requires confluent-kafka
- **Dependencies:** `pip install confluent-kafka`

### ENABLE_REDIS
- **Purpose:** Enables Redis message bridge
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1` if using Redis
- **Example:** `ENABLE_REDIS=1`
- **Impact:** Requires redis-py
- **Dependencies:** Already in requirements.txt

---

## Logging and Monitoring

### LOG_LEVEL
- **Purpose:** Application logging level
- **Type:** String
- **Values:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Default:** `INFO`
- **Production:** `INFO` or `WARNING`
- **Example:** `LOG_LEVEL=INFO`
- **Impact:** Controls log verbosity

### SPACY_WARNING_IGNORE
- **Purpose:** Suppresses spaCy warnings
- **Type:** String (warning codes)
- **Values:** Comma-separated warning codes (e.g., `W007`)
- **Default:** None
- **Production:** `W007`
- **Example:** `SPACY_WARNING_IGNORE=W007`
- **Impact:** Reduces log noise

### ENABLE_STRUCTURED_LOGGING
- **Purpose:** Enables structured (JSON) logging
- **Type:** Integer (0 or 1)
- **Values:** `0` (disabled), `1` (enabled)
- **Default:** `0`
- **Production:** `1`
- **Example:** `ENABLE_STRUCTURED_LOGGING=1`
- **Impact:** Improves log parsing and analysis

---

## API Keys and External Services

### OPENAI_API_KEY
- **Purpose:** OpenAI API authentication
- **Type:** String (API key)
- **Values:** OpenAI API key
- **Default:** None
- **Production:** **REQUIRED** if using OpenAI
- **Example:** `OPENAI_API_KEY=sk-...`
- **Security:** Store in secrets manager

### ANTHROPIC_API_KEY
- **Purpose:** Anthropic (Claude) API authentication
- **Type:** String (API key)
- **Values:** Anthropic API key
- **Default:** None
- **Production:** **REQUIRED** if using Claude
- **Example:** `ANTHROPIC_API_KEY=sk-ant-...`
- **Security:** Store in secrets manager

### GOOGLE_API_KEY
- **Purpose:** Google Cloud API authentication
- **Type:** String (API key)
- **Values:** Google API key
- **Default:** None
- **Production:** Optional
- **Example:** `GOOGLE_API_KEY=AIza...`
- **Security:** Store in secrets manager

---

## Deployment-Specific

### PORT
- **Purpose:** HTTP server port
- **Type:** Integer
- **Values:** 1-65535
- **Default:** `8000`
- **Production:** `8000` (or as configured in load balancer)
- **Example:** `PORT=8000`
- **Impact:** Server listening port

### HOST
- **Purpose:** HTTP server bind address
- **Type:** String (IP address)
- **Values:** `0.0.0.0` (all interfaces), `127.0.0.1` (localhost)
- **Default:** `0.0.0.0`
- **Production:** `0.0.0.0`
- **Example:** `HOST=0.0.0.0`
- **Impact:** Network interface binding

### WORKERS
- **Purpose:** Number of Gunicorn/Uvicorn workers
- **Type:** Integer
- **Values:** 1-32
- **Default:** `4`
- **Production:** `(2 x CPU cores) + 1`
- **Example:** `WORKERS=9` (for 4-core machine)
- **Impact:** Concurrent request handling

---

## Complete Production Example

```bash
# Core Application
APP_ENV=production
DEV_MODE=0
PRODUCTION_MODE=1
APP_STARTUP=1
SKIP_IMPORT_TIME_VALIDATION=1

# Security
AGENTIC_AUDIT_HMAC_KEY=7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a
ENCRYPTION_KEY=<fernet-key>
KMS_KEY_ID=arn:aws:kms:us-east-1:123456789012:key/abc-123

# Database
DB_PATH=postgresql+asyncpg://user:password@db.example.com:5432/codefactory
DB_POOL_SIZE=50
DB_POOL_MAX_OVERFLOW=20
DB_RETRY_ATTEMPTS=3
DB_RETRY_DELAY=1.0

# Message Bus
MESSAGE_BUS_SHARD_COUNT=8
MESSAGE_BUS_WORKERS_PER_SHARD=4
ENABLE_MESSAGE_BUS_GUARDIAN=1
MESSAGE_BUS_GUARDIAN_INTERVAL=30

# Features
ENABLE_HSM=1
ENABLE_REDIS=1
ENABLE_KAFKA=0

# Logging
LOG_LEVEL=INFO
ENABLE_STRUCTURED_LOGGING=1
SPACY_WARNING_IGNORE=W007

# API Keys (use secrets manager!)
OPENAI_API_KEY=<from-secrets-manager>
ANTHROPIC_API_KEY=<from-secrets-manager>

# Deployment
PORT=8000
HOST=0.0.0.0
WORKERS=9
```

---

## Development Example

```bash
# Core Application
APP_ENV=development
DEV_MODE=1
PRODUCTION_MODE=0
APP_STARTUP=1
SKIP_IMPORT_TIME_VALIDATION=1

# Security (use test keys only!)
AGENTIC_AUDIT_HMAC_KEY=dev_key_not_for_production_use_only
ENCRYPTION_KEY=<dev-key>

# Database
DB_PATH=sqlite+aiosqlite:///./dev.db
DB_POOL_SIZE=10
DB_POOL_MAX_OVERFLOW=5

# Message Bus
MESSAGE_BUS_SHARD_COUNT=2
MESSAGE_BUS_WORKERS_PER_SHARD=2
ENABLE_MESSAGE_BUS_GUARDIAN=0

# Features
ENABLE_HSM=0
ENABLE_REDIS=0
ENABLE_KAFKA=0

# Logging
LOG_LEVEL=DEBUG
ENABLE_STRUCTURED_LOGGING=0
SPACY_WARNING_IGNORE=W007

# API Keys (use test keys!)
OPENAI_API_KEY=sk-test-...

# Deployment
PORT=8000
HOST=127.0.0.1
WORKERS=1
```

---

## Testing Example

```bash
# Core Application
APP_ENV=test
DEV_MODE=1
PRODUCTION_MODE=0
APP_STARTUP=1
SKIP_IMPORT_TIME_VALIDATION=1

# Testing Flags
PYTEST_CURRENT_TEST=<set-by-pytest>
PYTEST_COLLECTING=<set-by-pytest>
CI=true

# Security (use mock keys!)
AGENTIC_AUDIT_HMAC_KEY=test_key
ENCRYPTION_KEY=test_key

# Database (in-memory)
DB_PATH=sqlite+aiosqlite:///:memory:

# Message Bus (minimal)
MESSAGE_BUS_SHARD_COUNT=1
MESSAGE_BUS_WORKERS_PER_SHARD=1
ENABLE_MESSAGE_BUS_GUARDIAN=0

# Features (disabled for speed)
ENABLE_HSM=0
ENABLE_REDIS=0
ENABLE_KAFKA=0

# Logging
LOG_LEVEL=WARNING
ENABLE_STRUCTURED_LOGGING=0
```

---

## Security Best Practices

### DO NOT:
- ❌ Commit API keys or secrets to git
- ❌ Use production keys in development
- ❌ Share encryption keys via email/chat
- ❌ Set PYTEST_* variables in production
- ❌ Use weak HMAC keys

### DO:
- ✅ Use secrets management (AWS Secrets Manager, HashiCorp Vault)
- ✅ Rotate keys regularly
- ✅ Use different keys per environment
- ✅ Audit secrets access
- ✅ Use strong random keys (64+ hex chars for HMAC)

---

## Troubleshooting

### Issue: "RuntimeError: no running event loop"
**Check:** Is `PYTEST_COLLECTING=1` set incorrectly?
**Fix:** Unset testing variables in production

### Issue: "Config must be an instance of ArbiterConfig"
**Check:** Is `APP_ENV=production` set?
**Fix:** Verify ArbiterConfig is importable and properly configured

### Issue: "Circular import error in clarifier"
**Check:** Are you importing at module level?
**Fix:** Use lazy imports or import from `generator.clarifier`

### Issue: "No README content found"
**Check:** Are files uploaded? Check permissions?
**Fix:** Verify upload directory exists and is writable

---

## Related Documentation

- [Deployment Guide](DEPLOYMENT.md)
- [Security Guide](SECURITY_DEPLOYMENT_GUIDE.md)
- [Startup Fixes](STARTUP_RUNTIME_FIXES_IMPLEMENTATION.md)
- [Railway Deployment](RAILWAY_DEPLOYMENT.md)
