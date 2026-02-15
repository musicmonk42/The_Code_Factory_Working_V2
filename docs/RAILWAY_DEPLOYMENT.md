<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Railway Deployment Guide

## ✅ Updated for All Critical Fixes

This guide has been updated to include all environment variables required for the critical startup and runtime fixes:
- Event loop management (Message Bus configuration)
- Config validation (Production mode settings)
- Audit logging (HMAC key requirements)
- Startup optimization

---

## ⚠️ Railway Deployment: No AWS KMS Required

**IMPORTANT:** Railway deployments do NOT need AWS KMS (Key Management Service). Railway has its own built-in secrets management that encrypts all environment variables at the platform level.

**What this means:**
- ✅ Generate an **UNENCRYPTED** base64 master key (see commands below)
- ✅ Set `USE_ENV_SECRETS=true` to use Railway's environment variables directly
- ❌ Do NOT encrypt the master key with AWS KMS
- ❌ Do NOT set up AWS credentials, KMS keys, or IAM roles
- ❌ You should NEVER see `InvalidCiphertextException` errors

If you see `InvalidCiphertextException` errors, this typically means you're using a KMS-encrypted key instead of a plaintext base64 key. Follow the secret generation commands below to fix this.

---

## Quick Start

### 1. Deploy to Railway
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

### 2. Add Required Plugins
In your Railway project dashboard:
1. Click **"+ New"** → **"Database"** → **"PostgreSQL"** (auto-injects `DATABASE_URL`)
2. Click **"+ New"** → **"Database"** → **"Redis"** (auto-injects `REDIS_URL`)

### 3. Set Required Secrets

⚠️ **CRITICAL**: All these secrets are REQUIRED for the application to start correctly.

| Variable | How to Generate | Required | Purpose |
|----------|----------------|----------|---------|
| `USE_ENV_SECRETS` | Set to `true` | ✅ Yes | Enable environment variable secret manager for Railway |
| `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` | Generate base64 key (see below) | ✅ Yes | Master encryption key for audit crypto |
| `AGENTIC_AUDIT_HMAC_KEY` | `openssl rand -hex 32` | ✅ Yes | Audit log signing (64 hex chars) |
| `ENCRYPTION_KEY` | Generate Fernet key (see below) | ✅ Yes | Data encryption at rest |
| `SECRET_KEY` | `${{secret()}}` or generate | ✅ Yes | App secret |
| `JWT_SECRET_KEY` | `${{secret()}}` or generate | ✅ Yes | JWT signing |
| `OPENAI_API_KEY` | From OpenAI dashboard | ✅ Yes | LLM access |

**Generate Commands:**

```bash
# USE_ENV_SECRETS (REQUIRED: tells system to use Railway's environment variables)
true

# AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 (UNENCRYPTED base64 key)
# ⚠️ IMPORTANT: This is NOT encrypted with KMS - Railway encrypts it for you
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"

# AGENTIC_AUDIT_HMAC_KEY (64 hex characters)
openssl rand -hex 32

# ENCRYPTION_KEY (Fernet key)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SECRET_KEY and JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **⚠️ CRITICAL**: 
> - `USE_ENV_SECRETS=true` is **REQUIRED** for Railway deployment. Without it, the system will try to use AWS KMS and fail.
> - `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` must be an **UNENCRYPTED** base64 key (generated with the command above). Do NOT use a KMS-encrypted key.
> - `AGENTIC_AUDIT_HMAC_KEY` is **required** for production audit logging. Without it, the application will fail at runtime when attempting to log security events. Must be exactly 64 hexadecimal characters.
> - Railway encrypts ALL environment variables at the platform level, so your secrets are protected.

---

## Environment Variables Configuration

### ✅ Core Application (Auto-configured in railway.toml)

These are already set in `railway.toml` but you can override them in Railway UI:

| Variable | Value | Purpose |
|----------|-------|---------|
| `APP_ENV` | `production` | Enable production mode |
| `DEV_MODE` | `0` | Disable development features |
| `PRODUCTION_MODE` | `1` | Enable production checks |
| `APP_STARTUP` | `1` | Optimize startup time |
| `SKIP_IMPORT_TIME_VALIDATION` | `1` | Skip import-time validation |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### ✅ Message Bus (Auto-configured for Event Loop Fix)

These ensure the event loop management fix works correctly:

| Variable | Value | Purpose |
|----------|-------|---------|
| `MESSAGE_BUS_SHARD_COUNT` | `8` | Number of message shards |
| `MESSAGE_BUS_WORKERS_PER_SHARD` | `4` | Workers per shard |
| `ENABLE_MESSAGE_BUS_GUARDIAN` | `1` | Enable health monitoring |
| `MESSAGE_BUS_GUARDIAN_INTERVAL` | `30` | Check interval (seconds) |

### ✅ Database (Auto-configured for Config Fix)

| Variable | Value | Purpose |
|----------|-------|---------|
| `DB_POOL_SIZE` | `50` | Connection pool size |
| `DB_POOL_MAX_OVERFLOW` | `20` | Max overflow connections |
| `DB_RETRY_ATTEMPTS` | `3` | Retry count |
| `DB_RETRY_DELAY` | `1.0` | Retry delay (seconds) |

### ✅ Feature Flags

| Variable | Value | Purpose |
|----------|-------|---------|
| `ENABLE_HSM` | `0` | Hardware security module |
| `ENABLE_REDIS` | `1` | Redis message bridge |
| `ENABLE_KAFKA` | `1` | Kafka message bridge (enabled for production event-driven orchestration) |
| `ENABLE_STRUCTURED_LOGGING` | `1` | JSON logging |

---

## Optional Configuration

### Additional LLM Providers
| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Claude |
| `GOOGLE_API_KEY` | Google Gemini |
| `GROK_API_KEY` | xAI Grok |

### Kafka (External Provider)
If using Kafka with an external managed service like [Upstash Kafka](https://upstash.com/kafka) or [Confluent Cloud](https://confluent.cloud), configure these variables:

```bash
ENABLE_KAFKA=1
USE_KAFKA_INGESTION=true
USE_KAFKA_AUDIT=true
KAFKA_BOOTSTRAP_SERVERS=your-cluster.upstash.io:9092
KAFKA_SASL_USERNAME=your-username
KAFKA_SASL_PASSWORD=your-password
KAFKA_SECURITY_PROTOCOL=SASL_SSL
```

> **Note**: When `ENABLE_KAFKA=1` is set in railway.toml and Kafka is available, the ShardedMessageBus will use Kafka for event-driven orchestration instead of local queue only.

### Neo4j (Knowledge Graph)
For knowledge graph features, use [Neo4j Aura](https://neo4j.com/cloud/aura/):

```bash
NEO4J_URL=bolt+s://your-instance.databases.neo4j.io:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

### Observability
```bash
SENTRY_DSN=https://your-sentry-dsn
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector:4317
PROMETHEUS_PORT=9090
```

---

## Deployment Steps

### Step 1: Fork and Connect Repository
1. Fork this repository to your GitHub account
2. Go to [Railway](https://railway.app)
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your forked repository

### Step 2: Add Database Plugins
1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"PostgreSQL"**
3. Click **"+ New"** again
4. Select **"Database"** → **"Redis"**

Railway will automatically inject `DATABASE_URL` and `REDIS_URL`.

### Step 3: Configure Secrets

In the Railway dashboard, go to your service → **Variables** tab:

1. **Enable environment variable secret manager:**
   - `USE_ENV_SECRETS` = `true`

2. **Generate and add security keys:**
   ```bash
   # Generate AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64
   python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
   # Copy output and add as Railway variable
   
   # Generate AGENTIC_AUDIT_HMAC_KEY
   openssl rand -hex 32
   # Copy output and add as Railway variable
   
   # Generate ENCRYPTION_KEY
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   # Copy output and add as Railway variable
   ```

3. **Add generated keys to Railway:**
   - `USE_ENV_SECRETS` = `true` (enables Railway environment variable secret manager)
   - `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` = (paste base64 key)
   - `AGENTIC_AUDIT_HMAC_KEY` = (paste 64-char hex key)
   - `ENCRYPTION_KEY` = (paste Fernet key)
   - `SECRET_KEY` = Use `${{secret()}}` or generate with Python
   - `JWT_SECRET_KEY` = Use `${{secret()}}` or generate with Python

4. **Add your OpenAI API key:**
   - `OPENAI_API_KEY` = (paste your OpenAI API key)

### Step 4: Deploy
Railway will automatically build and deploy using the Dockerfile. Monitor the build logs.

### Step 5: Verify Deployment

```bash
# Check health endpoint
curl https://your-app.up.railway.app/health

# Expected response:
{
  "status": "healthy",
  "timestamp": "2024-01-24T12:00:00Z",
  "version": "..."
}

# Check readiness
curl https://your-app.up.railway.app/ready

# Check API docs
# Visit: https://your-app.up.railway.app/docs
```

### Step 6: Validate Critical Fixes

Check the Railway logs for successful initialization:

```bash
# In Railway dashboard → Logs tab, look for:

✓ "ShardedMessageBus initialized"
  # Confirms event loop management fix is working

✓ "PolicyEngine initialized successfully"
  # Confirms config validation fix is working

✓ "API Server ready to accept connections"
  # Confirms overall startup is successful

# Should NOT see:
✗ "RuntimeError: no running event loop"
✗ "Config must be an instance of ArbiterConfig"
✗ "Circular import in clarifier"
```

---

## Audit Crypto Configuration (CRITICAL)

⚠️ **IMPORTANT**: Proper audit crypto configuration is essential for production deployments. The audit system provides cryptographic signatures for all security-relevant events.

### Understanding Audit Crypto

The audit crypto system uses a master encryption key to protect audit logs. This key can be:
- **Stored in environment variables** (recommended for Railway)
- **Encrypted with AWS KMS** (recommended for AWS deployments)

### Option 1: Without AWS KMS (Recommended for Railway)

This is the simplest approach for Railway deployments, where secrets are managed by Railway's environment variables.

**Step 1: Generate a new master key**
```bash
python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

**Step 2: Add to Railway environment variables**
- `USE_ENV_SECRETS` = `true` (enables environment variable secret manager)
- `AUDIT_CRYPTO_MODE` = `software` (default, can be omitted)
- `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` = (paste the base64 key from step 1)
- `AUDIT_CRYPTO_ALLOW_INIT_FAILURE` = `0` (fail fast in production if crypto cannot initialize)

**Step 3: Verify configuration**
```bash
# Run the validation script before deployment
python scripts/validate_secrets.py
```

### Option 2: With AWS KMS (Production AWS Deployments)

Use this approach when deploying on AWS infrastructure with KMS for enhanced security.

**Step 1: Generate a new master key**
```bash
python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

**Step 2: Encrypt it with your KMS key**
```bash
# Replace 'alias/your-kms-key' with your actual KMS key ID or alias
# Note: This uses bash process substitution. For other shells, save key to a file first.
aws kms encrypt --key-id alias/your-kms-key \
  --plaintext fileb://<(echo -n 'YOUR_MASTER_KEY_FROM_STEP_1' | base64 -d) \
  --query CiphertextBlob --output text

# Alternative using a temporary file (works in all shells):
# echo -n 'YOUR_MASTER_KEY_FROM_STEP_1' | base64 -d > /tmp/key.bin
# aws kms encrypt --key-id alias/your-kms-key \
#   --plaintext fileb:///tmp/key.bin \
#   --query CiphertextBlob --output text
# rm /tmp/key.bin
```

**Step 3: Add to Railway environment variables**
- `AWS_REGION` = `us-east-1` (or your AWS region)
- `AWS_ACCESS_KEY_ID` = (your AWS access key with KMS decrypt permissions)
- `AWS_SECRET_ACCESS_KEY` = (your AWS secret key)
- `KMS_KEY_ID` = `alias/your-kms-key` (the same KMS key ID used in step 2)
- `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` = (paste the ciphertext from step 2)
- `AUDIT_CRYPTO_MODE` = `software` (default)
- `AUDIT_CRYPTO_ALLOW_INIT_FAILURE` = `0` (fail fast in production)

### Common Issues and Solutions

#### KMS Key Mismatch Error

If you see this error:
```
InvalidCiphertextException: Master key encrypted with different KMS key
```

**Cause**: The encrypted master key was encrypted with a different KMS key than the one currently configured.

**Solution**:
1. Verify your `KMS_KEY_ID` matches the key used to encrypt the master key
2. If you've rotated KMS keys or migrated environments, regenerate the master key:
   - Follow "Option 2: With AWS KMS" steps above
   - Use your CURRENT KMS key ID
   - Update `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` with the new ciphertext

#### Switching Between KMS and Non-KMS

**From KMS to Environment Variables (e.g., moving to Railway):**
1. Generate a new plaintext master key (Option 1, Step 1)
2. Set `USE_ENV_SECRETS=true`
3. Remove AWS credentials if no longer needed
4. Update `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` with the plaintext base64 key

**From Environment Variables to KMS:**
1. Generate a new master key and encrypt with KMS (Option 2)
2. Remove `USE_ENV_SECRETS` or set to `false`
3. Update `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` with the KMS ciphertext

⚠️ **WARNING**: Changing the master key will invalidate existing encrypted audit data. Ensure backups are in place.

### Validation

Before deploying, validate your secrets configuration:

```bash
# Run validation script
python scripts/validate_secrets.py

# Expected output for successful validation:
✓ All secrets validated successfully
Application is ready to start
```

---

## Troubleshooting

### ❌ Critical: Secret Manager Error (NEW)

**Error**: 
```
generator.audit_log.audit_crypto.secrets.SecretNotFoundError: Dummy secret manager: secret 'AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64' not available.
```

**Cause**: The application is not configured to use the environment variable secret manager for Railway.

**Solution**:
1. Add `USE_ENV_SECRETS=true` to Railway environment variables
2. Generate and add the master encryption key:
   ```bash
   python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
   ```
3. Add the generated key as `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64`
4. Redeploy

**Note**: Railway uses environment variables for secrets, unlike AWS/GCP which have dedicated secret managers. Setting `USE_ENV_SECRETS=true` tells the application to read secrets from environment variables.

### ❌ Critical: Audit Logging Error

**Error**: 
```
FATAL: log_audit_event called for 'security_redact' but no signing key is configured
```

**Cause**: Missing `AGENTIC_AUDIT_HMAC_KEY`

**Solution**:
1. Generate: `openssl rand -hex 32`
2. Add to Railway Variables as `AGENTIC_AUDIT_HMAC_KEY`
3. Must be exactly 64 hex characters
4. Redeploy

### ❌ Event Loop Error

**Error**:
```
RuntimeError: no running event loop
```

**Cause**: Missing message bus configuration

**Solution**:
Variables should be auto-configured from `railway.toml`. Verify in Railway UI:
- `MESSAGE_BUS_SHARD_COUNT=8`
- `MESSAGE_BUS_WORKERS_PER_SHARD=4`
- `ENABLE_MESSAGE_BUS_GUARDIAN=1`

### ❌ Config Validation Error

**Error**:
```
Config must be an instance of ArbiterConfig
```

**Cause**: Missing production mode flags

**Solution**:
Verify in Railway UI:
- `APP_ENV=production`
- `PRODUCTION_MODE=1`
- `DEV_MODE=0`

### ❌ Health Check Failing

**Symptoms**: Deployment shows as unhealthy

**Solutions**:
1. Check logs for startup errors
2. Verify all required secrets are set
3. Ensure PostgreSQL and Redis plugins are added
4. Check `healthcheckTimeout` (default: 300s) is sufficient

### ❌ Database Connection Issues

**Error**: Connection timeout or refused

**Solutions**:
1. Verify PostgreSQL plugin is added and healthy
2. Check `DATABASE_URL` is injected (should happen automatically)
3. Restart the service if plugins were just added

### ❌ Kafka Initialization Error

**Error**:
```
AIOKafkaProducer.__init__() got an unexpected keyword argument 'retries'
```

**Cause**: The `aiokafka` library version in use does not support the `retries` parameter.

**Solution**:
This has been fixed in the codebase. The `retries` parameter has been removed from Kafka producer initialization as it's not supported in aiokafka 0.7.x. Retries are handled internally by aiokafka based on the `enable_idempotence` setting.

If you still see this error:
1. Ensure you're using the latest version of the code
2. Verify the fix is present in `omnicore_engine/message_bus/integrations/kafka_bridge.py`
3. Redeploy the application

**Note**: Kafka is optional. If Kafka fails to initialize, the application will fall back to local queue mode (Redis-only). To disable Kafka entirely, set `ENABLE_KAFKA=0`.

### ❌ LLM Errors

**Error**: OpenAI API errors

**Solutions**:
1. Verify `OPENAI_API_KEY` is set correctly (no extra spaces)
2. Check API key has sufficient credits
3. Test the key: `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`

---

## Monitoring and Metrics

### Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness check (is app running?) |
| `/ready` | Readiness check (can app serve traffic?) |
| `/metrics` | Prometheus metrics |
| `/docs` | OpenAPI documentation |

### Key Metrics to Monitor

```bash
# Message bus health
curl https://your-app.up.railway.app/metrics | grep message_bus

# Event loop status
curl https://your-app.up.railway.app/metrics | grep event_loop

# Audit log operations
curl https://your-app.up.railway.app/metrics | grep audit
```

---

## Security Checklist

- [x] `AGENTIC_AUDIT_HMAC_KEY` set to strong random key (64 hex chars)
- [x] `ENCRYPTION_KEY` set to Fernet key
- [x] `SECRET_KEY` and `JWT_SECRET_KEY` set to strong random values
- [x] `OPENAI_API_KEY` stored as Railway secret
- [x] Production mode enabled (`APP_ENV=production`)
- [x] Structured logging enabled
- [x] PostgreSQL used instead of SQLite
- [x] Regular key rotation schedule established
- [x] Monitoring and alerting configured

---

## Performance Optimization

### Resource Configuration

Railway automatically allocates resources, but you can optimize:

1. **Vertical Scaling**: Upgrade Railway plan for more CPU/memory
2. **Database**: Use Railway PostgreSQL for better performance than SQLite
3. **Redis**: Essential for message bus performance
4. **Workers**: Adjust `WORKER_COUNT` based on traffic (default: 4)

### Message Bus Tuning

For high-traffic deployments, adjust in Railway Variables:
```
MESSAGE_BUS_SHARD_COUNT=16  # More shards = better parallelism
MESSAGE_BUS_WORKERS_PER_SHARD=8  # More workers per shard
```

---

## Rolling Back

If deployment fails:

1. **Via Railway UI**: Deployments → Click previous successful deployment → "Redeploy"
2. **Via Git**: Push previous commit to trigger new deployment
3. **Quick fix**: Disable problematic feature flag in Variables

---

## Support and Documentation

- **Main Documentation**: See `STARTUP_RUNTIME_FIXES_IMPLEMENTATION.md`
- **Environment Variables**: See `ENVIRONMENT_VARIABLES.md`
- **Docker**: See `DOCKER_VALIDATION_FIXES.md`
- **Railway Docs**: https://docs.railway.app
- **Issue Tracker**: Create GitHub issue with logs

---

## Next Steps After Deployment

1. **Configure Monitoring**: Set up Sentry or other APM
2. **Set Up Alerts**: Monitor health endpoint failures
3. **Enable Backups**: Configure Railway automatic backups
4. **Load Testing**: Test with expected traffic patterns
5. **Documentation**: Update team runbooks with Railway specifics
