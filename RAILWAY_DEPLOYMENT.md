# Railway Deployment Guide

## Quick Start

### 1. Deploy to Railway
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

### 2. Add Required Plugins
In your Railway project dashboard:
1. Click **"+ New"** → **"Database"** → **"PostgreSQL"** (auto-injects `DATABASE_URL`)
2. Click **"+ New"** → **"Database"** → **"Redis"** (auto-injects `REDIS_URL`)

### 3. Set Required Secrets
In the **Variables** tab, add:

| Variable | How to Set | Required |
|----------|-----------|----------|
| `OPENAI_API_KEY` | Your OpenAI API key | ✅ Yes |
| `SECRET_KEY` | `${{secret()}}` or generate manually | ✅ Yes |
| `JWT_SECRET_KEY` | `${{secret()}}` or generate manually | ✅ Yes |
| `AGENTIC_AUDIT_HMAC_KEY` | Generate with command below | ✅ Yes |

**Generate AGENTIC_AUDIT_HMAC_KEY:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **⚠️ CRITICAL**: `AGENTIC_AUDIT_HMAC_KEY` is **required** for production audit logging. Without it, the application will fail at runtime when attempting to log security events. Use Railway's `${{secret()}}` syntax or set a manually generated secret.

### 4. Optional Configuration

#### Additional LLM Providers
| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Claude |
| `GEMINI_API_KEY` | Google Gemini |
| `GROK_API_KEY` | xAI Grok |

#### Kafka (External Provider)
If using Kafka instead of Redis Streams, use a managed service like [Upstash Kafka](https://upstash.com/kafka) or [Confluent Cloud](https://confluent.cloud):

```bash
USE_KAFKA_INGESTION=true
USE_KAFKA_AUDIT=true
KAFKA_BOOTSTRAP_SERVERS=your-cluster.upstash.io:9092
KAFKA_SASL_USERNAME=your-username
KAFKA_SASL_PASSWORD=your-password
KAFKA_SECURITY_PROTOCOL=SASL_SSL
```

#### Neo4j (Knowledge Graph)
For knowledge graph features, use [Neo4j Aura](https://neo4j.com/cloud/aura/):

```bash
NEO4J_URL=bolt+s://your-instance.databases.neo4j.io:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

#### Observability
```bash
SENTRY_DSN=https://your-sentry-dsn
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector:4317
```

#### Encryption Key Generation
Generate a Fernet encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Environment Variables Reference

### Core (Required)
| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | Environment mode | `production` |
| `DEBUG` | Debug mode | `false` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `SECRET_KEY` | Application secret | - |
| `JWT_SECRET_KEY` | JWT signing secret | - |
| `AGENTIC_AUDIT_HMAC_KEY` | Audit log signing key (REQUIRED) | - |

### Database (Auto-injected)
| Variable | Description | Source |
|----------|-------------|--------|
| `DATABASE_URL` | PostgreSQL connection | Railway Plugin |
| `REDIS_URL` | Redis connection | Railway Plugin |

### LLM Configuration
| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | Default LLM provider | `openai` |
| `LLM_MODEL` | Default model | `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Anthropic/Claude key | - |
| `GEMINI_API_KEY` | Google Gemini key | - |
| `GROK_API_KEY` | xAI Grok key | - |

### Kafka (Optional)
| Variable | Description | Default |
|----------|-------------|---------|
| `USE_KAFKA_INGESTION` | Enable Kafka ingestion | `false` |
| `USE_KAFKA_AUDIT` | Enable Kafka audit | `false` |
| `KAFKA_BOOTSTRAP_SERVERS` | Broker addresses | - |
| `KAFKA_SECURITY_PROTOCOL` | Security protocol | `PLAINTEXT` |

### Performance
| Variable | Description | Default |
|----------|-------------|---------|
| `WORKER_COUNT` | Number of workers | `4` |
| `MAX_CONCURRENT_TASKS` | Max concurrent tasks | `50` |

## Troubleshooting

### Critical: Audit Logging Error
**Error**: `FATAL: log_audit_event called for 'security_redact' but no signing key is configured and not in DEV_MODE`

**Cause**: The audit logging system requires `AGENTIC_AUDIT_HMAC_KEY` to sign security events in production.

**Solution**:
1. Generate a secure key:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Add it to Railway Variables:
   - Go to your project → Variables tab
   - Add `AGENTIC_AUDIT_HMAC_KEY` with the generated value
   - Or use Railway's secret syntax: `${{secret()}}`
3. Redeploy the application

### Health Check Failing
- Ensure `/health` endpoint is accessible
- Check `healthcheckTimeout` (default: 300s)

### Database Connection Issues
- Verify PostgreSQL/Redis plugins are added
- Check `DATABASE_URL` and `REDIS_URL` are injected

### LLM Errors
- Verify `OPENAI_API_KEY` is set correctly
- Check LLM provider API status
