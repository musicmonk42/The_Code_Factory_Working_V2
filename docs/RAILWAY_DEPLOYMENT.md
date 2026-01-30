# Railway Deployment Guide

## ✅ Updated for All Critical Fixes

This guide has been updated to include all environment variables required for the critical startup and runtime fixes:
- Event loop management (Message Bus configuration)
- Config validation (Production mode settings)
- Audit logging (HMAC key requirements)
- Startup optimization

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
# USE_ENV_SECRETS (enable environment variable secret manager for Railway)
true

# AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 (base64-encoded 32-byte key)
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"

# AGENTIC_AUDIT_HMAC_KEY (64 hex characters)
openssl rand -hex 32

# ENCRYPTION_KEY (Fernet key)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SECRET_KEY and JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **⚠️ CRITICAL**: 
> - `USE_ENV_SECRETS=true` enables the environment variable secret manager for Railway deployment
> - `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` is required for audit encryption to work
> - `AGENTIC_AUDIT_HMAC_KEY` is **required** for production audit logging. Without it, the application will fail at runtime when attempting to log security events. Must be exactly 64 hexadecimal characters.

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
| `ENABLE_KAFKA` | `0` | Kafka message bridge |
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
If using Kafka instead of Redis Streams, use a managed service like [Upstash Kafka](https://upstash.com/kafka) or [Confluent Cloud](https://confluent.cloud):

```bash
USE_KAFKA_INGESTION=true
USE_KAFKA_AUDIT=true
KAFKA_BOOTSTRAP_SERVERS=your-cluster.upstash.io:9092
KAFKA_SASL_USERNAME=your-username
KAFKA_SASL_PASSWORD=your-password
KAFKA_SECURITY_PROTOCOL=SASL_SSL
```

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
