<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# OmniCore Omega Pro Engine Configuration Reference

## Overview

Configuration is managed via environment variables (or a `.env` file) and loaded with `ArbiterConfig` (pydantic_settings). This document details variables, recommended values, and tuning advice for production and development.

## Environment Variables

| Variable                    | Description                                      | Default                         | Example                              |
|-----------------------------|--------------------------------------------------|----------------------------------|--------------------------------------|
| DATABASE_URL                | DB connection string                             | sqlite+aiosqlite:///omnicore.db  | postgresql+asyncpg://user:pass@host  |
| PLUGIN_DIR                  | Plugin source directory                          | ./plugins                       | /app/plugins                        |
| LOG_LEVEL                   | Logging verbosity                                | INFO                            | DEBUG                               |
| MESSAGE_BUS_SHARD_COUNT     | Number of message bus shards                     | 4                               | 8                                   |
| MESSAGE_BUS_MAX_QUEUE_SIZE  | Max queue size per shard                         | 10000                           | 20000                               |
| DLQ_MAX_RETRIES             | DLQ message retries                              | 3                               | 5                                   |
| DLQ_BACKOFF_FACTOR          | Backoff for DLQ                                  | 1.5                             | 2.0                                 |
| JWT_SECRET                  | OAuth2 JWT secret                                | (unset)                         | your-secret-key                     |
| USER_ID                     | CLI user for PolicyEngine                        | (unset)                         | user123                             |
| ENCRYPTION_KEYS             | Fernet keys (comma-separated)                    | (unset)                         | key1,key2                           |
| PROMETHEUS_PORT             | Prometheus metrics endpoint port                 | 8000                            | 9090                                |
| INFLUXDB_URL                | InfluxDB for metrics (optional)                  | (unset)                         | http://localhost:8086               |
| KAFKA_BOOTSTRAP_SERVERS     | Kafka servers (optional)                         | (unset)                         | localhost:9092                      |
| REDIS_URL                   | Redis URL (optional)                             | (unset)                         | redis://localhost:6379              |

**Example .env:**
```
DATABASE_URL=sqlite+aiosqlite:///omnicore.db
PLUGIN_DIR=./plugins
LOG_LEVEL=INFO
MESSAGE_BUS_SHARD_COUNT=4
MESSAGE_BUS_MAX_QUEUE_SIZE=10000
DLQ_MAX_RETRIES=3
DLQ_BACKOFF_FACTOR=1.5
JWT_SECRET=your-secret-key
USER_ID=user123
ENCRYPTION_KEYS=key1,key2
PROMETHEUS_PORT=9090
INFLUXDB_URL=http://localhost:8086
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
```

## Tuning

- **Dev:** Use SQLite, minimal shards, DEBUG log level.
- **Production:** Use PostgreSQL/Citus, increase shards/queue, secure secrets, tune DLQ and cache.
- **Metrics:** Set PROMETHEUS_PORT to avoid conflicts.
- **Secrets:** Store JWT_SECRET/ENCRYPTION_KEYS in a secrets manager.

**See Also:**  
- [DEPLOYMENT.md](DEPLOYMENT.md)  
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)