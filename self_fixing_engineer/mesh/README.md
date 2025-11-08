# Mesh Module – Self-Fixing Engineer (SFE) 🚀
**Mesh v2.1.0 – The "Distributed Harmony" Edition**  
**Proprietary Technology by Unexpected Innovations Inc.**  
**All Rights Reserved**

> Orchestrate distributed, agentic workflows with secure, scalable event mesh and checkpointing.

---

## Overview

The Mesh module provides the distributed coordination backbone for the Self-Fixing Engineer (SFE) platform, enabling secure, scalable, and observable event-driven workflows for agent orchestration, policy enforcement, and state persistence. Mesh brings together pub/sub messaging, policy management, and atomic checkpointing with pluggable backends (Redis, S3, Etcd, GCS, Azure, MinIO), zero-trust security (encryption, HMAC, JWT/RBAC), and full observability (Prometheus, OpenTelemetry, Structlog) to power enterprise-grade automation across cloud-native environments.

Crafted with precision in Fairhope, Alabama, USA.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
    - [Global Environment Variables](#global-environment-variables)
    - [Module-Specific Environment Variables](#module-specific-environment-variables)
- [Usage](#usage)
  - [Pub/Sub Messaging](#pubsub-messaging-event_buspy-and-mesh_adapterpy)
  - [Checkpoint Management](#checkpoint-management-checkpoint-subpackage)
  - [Policy Enforcement](#policy-enforcement-mesh_policypy)
  - [Monitoring and Logging](#monitoring-and-logging)
- [Extending Mesh](#extending-mesh)
- [Key Components](#key-components)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)
- [Contribution Guidelines](#contribution-guidelines)
- [Roadmap](#roadmap)
- [Support](#support)
- [License](#license)

---

## Features

- **Pub/Sub Messaging:** At-least-once delivery with Redis Streams, NATS, Kafka, RabbitMQ, AWS SNS/SQS, GCS Pub/Sub, etc. Encryption, HMAC, schema validation, DLQ support.
- **Checkpoint Management:** Atomic, versioned state persistence with local FS, S3, Redis, PostgreSQL, GCS, Azure Blob, MinIO, Etcd. Compression, encryption, HMAC, auditing, auto-heal on corruption.
- **Policy Enforcement:** RBAC/ABAC with JWT validation, pluggable backends, circuit breakers, retries.
- **Observability:** Prometheus metrics, OpenTelemetry tracing, Structlog logging, alert callbacks.
- **Resilience:** Circuit breakers (PyBreaker), jittered retries (Tenacity), DLQs, healthchecks, auto-reconnect.
- **Security:** Zero-trust, TLS, auth, key rotation, HMAC, PII scrubbing, prod mode fail-fast.
- **Extensibility:** Custom backends via decorators, audit hooks, xAI Grok integrations.
- **Performance:** Async I/O, caching, rate limiting, sharding.

All components are async-native, Python 3.12+.

---

## Architecture

Mesh uses a modular, pluggable design:

- **Event Bus Layer (event_bus.py):** High-level publish/subscribe API leveraging adapters.
- **Adapter Layer (mesh_adapter.py):** Pluggable connectors for pub/sub backends.
- **Policy Layer (mesh_policy.py):** Policy enforcement, backend storage for policies.
- **Checkpoint Layer (checkpoint/):** State management: utilities, exceptions, backends, orchestration.

**Interaction Flow:**
```
User/App -> Event Bus API (publish/subscribe)
     |
     v
Policy Enforcer (JWT/RBAC) -> Allow/Deny
     |
     v (Allow)
Adapter (Backend: Redis/Kafka/etc.) -> Publish/Subscribe (Encryption/HMAC/Retry/Breaker/DLQ)
     |
     v (On Save/Load)
Checkpoint Manager -> Backend (FS/S3/etc.) [Compress/Encrypt/Hash/Audit/Exception]
     |
     v
Observability (Metrics/Tracing/Logs) & Auditing (Hooks/DLQ)
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Dependencies (see `requirements.txt`): redis-py, aioboto3, cryptography, pydantic, opentelemetry-distro, prometheus-client, tenacity, pybreaker, cachetools, structlog, aiolimiter, aiofiles, portalocker, etcd3, google-cloud-storage, azure-storage-blob, asyncpg, nats-py, aiokafka, aio-pika.
- For backends: Redis, Etcd, MinIO, AWS/GCP/Azure credentials as needed.
- (Optional) Prometheus/OpenTelemetry Collector for observability.

### Installation

```bash
git clone <enterprise-repo-url>
cd mesh
pip install -r requirements.txt
```
For subpackages (e.g., checkpoint/):
```bash
cd mesh/checkpoint
pip install -r requirements.txt
```

### Configuration

Mesh uses environment variables, loaded via `.env` or export.

#### Global Environment Variables

| Variable                      | Description                                    | Default | Required in Prod? |
|-------------------------------|------------------------------------------------|---------|-------------------|
| PROD_MODE                     | Enable production checks (TLS, keys)           | false   | Yes               |
| ENV                           | Environment label for metrics/logs             | dev     | Yes               |
| TENANT                        | Tenant label for isolation in metrics/logs     | unknown | Yes               |
| LOG_LEVEL                     | Logging level                                  | INFO    | No                |
| OTEL_EXPORTER_OTLP_TRACES_ENDPOINT | OpenTelemetry traces endpoint            | None    | Yes               |
| PROMETHEUS_PORT               | Port for metrics exposure                      | 8000    | No                |

#### Module-Specific Environment Variables

**event_bus.py / mesh_adapter.py (Pub/Sub):**

| Variable                  | Description                                         | Default | Required in Prod? |
|---------------------------|-----------------------------------------------------|---------|-------------------|
| MESH_BACKEND_URL          | Backend URL (e.g. redis://host:port)                | None    | Yes               |
| EVENT_BUS_ENCRYPTION_KEY  | Comma-separated Fernet keys                        | None    | Yes               |
| EVENT_BUS_HMAC_KEY        | HMAC key for integrity                             | None    | Yes               |
| MESH_RETRIES              | Retry attempts                                     | 3       | No                |
| MESH_RETRY_DELAY          | Retry delay (seconds)                              | 1.0     | No                |
| MESH_DLQ_PATH             | DLQ file path                                      | mesh_dlq.jsonl | No           |
| MESH_RATE_LIMIT_RPS       | Rate limit (messages/sec)                          | None    | No                |
| REDIS_USER/REDIS_PASSWORD | Redis auth                                         | None    | Yes (if Redis)    |
| KAFKA_USER/KAFKA_PASSWORD | Kafka auth                                         | None    | Yes (if Kafka)    |
| RABBITMQ_USER/RABBITMQ_PASSWORD | RabbitMQ auth                                | None    | Yes (if RabbitMQ) |

**mesh_policy.py (Policy):**

| Variable                  | Description                                         | Default | Required in Prod? |
|---------------------------|-----------------------------------------------------|---------|-------------------|
| POLICY_ENCRYPTION_KEY     | Fernet keys for policy encryption                  | None    | Yes               |
| POLICY_HMAC_KEY           | HMAC key for policy integrity                      | None    | Yes               |
| POLICY_MAX_RETRIES        | Retry attempts                                     | 3       | No                |
| POLICY_RETRY_DELAY        | Retry delay                                        | 1.0     | No                |
| JWT_SECRET                | Secret for JWT validation                          | None    | Yes               |
| S3_BUCKET_NAME            | S3 bucket for policies                             | None    | If S3 backend     |

**checkpoint/ Subpackage:**

| Variable                      | Description                                    | Default | Required in Prod? |
|-------------------------------|------------------------------------------------|---------|-------------------|
| CHECKPOINT_ENCRYPTION_KEYS    | MultiFernet keys for rotation                  | None    | Yes               |
| CHECKPOINT_HMAC_KEY           | HMAC key for checkpoints                       | None    | Yes               |
| CHECKPOINT_BACKEND            | Backend (local/s3/redis/etc.)                  | local   | Yes               |
| CHECKPOINT_DLQ_PATH           | DLQ path                                       | checkpoint_dlq.jsonl | No           |
| CHECKPOINT_MAX_RETRIES        | Retry attempts                                 | 3       | No                |
| CHECKPOINT_RETRY_DELAY        | Retry delay                                    | 1.0     | No                |
| CHECKPOINT_LOAD_CACHE_TTL     | Cache TTL (seconds)                            | 60      | No                |
| CHECKPOINT_LOAD_CACHE_MAXSIZE | Cache max items                                | 100     | No                |
| AUDIT_FAIL_THRESHOLD          | Audit fail threshold for alerts                | 5       | No                |
| S3_BUCKET_NAME                | S3 bucket for checkpoints                      | None    | If S3             |
| ETCD_HOST/ETCD_PORT           | Etcd host/port                                 | localhost/2379 | If Etcd         |

**Tip:** Load `.env` via:
```bash
export $(grep -v '^#' .env | xargs)
```

---

## Usage

### Pub/Sub Messaging (event_bus.py and mesh_adapter.py)

**Initialize Adapter:**
```python
from mesh_adapter import MeshPubSub
adapter = MeshPubSub(backend_url=os.environ["MESH_BACKEND_URL"])
await adapter.connect()
```

**Publish Event:**
```python
from event_bus import publish_event
from pydantic import BaseModel

class EventSchema(BaseModel):
    message: str
    number: int

await publish_event(
    "test_event",
    {"message": "Hello", "number": 42},
    schema=EventSchema  # Optional validation
)
```
Supports encryption, HMAC, retries, circuit breakers.

**Subscribe Event:**
```python
async def handler(data):
    print(f"Received: {data}")

sub_task = await subscribe_event(
    "test_event",
    handler,
    consumer_group="test_group",
    consumer_name="test_consumer"
)
# Later:
sub_task.cancel()
await sub_task
```
Handles DLQ and redelivery limits.

---

### Checkpoint Management (checkpoint/ subpackage)

**Initialize Manager:**
```python
from checkpoint_manager import CheckpointManager
from pydantic import BaseModel

class StateSchema(BaseModel):
    step: int
    data: dict

manager = CheckpointManager(
    backend_type=os.environ["CHECKPOINT_BACKEND"],
    state_schema=StateSchema,
    keep_versions=10,
    audit_hook=async def hook(event, details): print(f"Audit: {event}"),
    access_policy=lambda user, op, ctx: user == "admin"
)
```

**Save/Load/Rollback:**
```python
await manager.save("workflow_123", {"step": 5, "data": {"progress": 50}}, metadata={"user": "admin"})
state = await manager.load("workflow_123", version=3)  # Or None for latest
await manager.rollback("workflow_123", version=2)
```
Supports atomicity, versioning, compression, encryption, auditing, DLQ.

**DLQ Replay:**
```python
await manager.replay_dlq()
```

**Handle Exceptions:**
```python
try:
    await manager.save("invalid", {})
except CheckpointBackendError as e:
    print(e.context)
```

---

### Policy Enforcement (mesh_policy.py)

**Initialize and Use:**
```python
from mesh_policy import MeshPolicyBackend, MeshPolicyEnforcer

backend = MeshPolicyBackend(backend_type="s3", s3_bucket=os.environ["S3_BUCKET_NAME"])
enforcer = MeshPolicyEnforcer(policy_id="mesh_policy", backend=backend)
await enforcer.load_policy()

policy_data = {"allow": ["publish"], "deny": ["delete"], "version": "1.0"}
await backend.save("mesh_policy", policy_data)
loaded = await backend.load("mesh_policy")

is_allowed = await enforcer.enforce_policy("publish", token="jwt_token_here")
if not is_allowed:
    raise PermissionError("Denied")
```
Supports JWT validation, RBAC/ABAC.

---

### Monitoring and Logging

- **Metrics:** Prometheus (latency, error rates).
- **Tracing:** OpenTelemetry (spans with backend, status).
- **Logging:** Structlog (JSON, rotation, context).
- **Alerting:** Callback integration (Slack, PagerDuty).

Monitor via Grafana/Prometheus or OTEL Collector.

---

## Extending Mesh

- **Add Pub/Sub Backends:** Extend `MeshPubSub` in mesh_adapter.py; register via `_SUPPORTED`.
- **Add Checkpoint Backends:** Use decorator in checkpoint_backends.py.
- **Custom Policies:** Extend `PolicySchema` in mesh_policy.py.
- **Custom Audit Hooks:** Pass an async callable to `CheckpointManager`.

---

## Key Components

- **`__init__.py`** – Package init, prod checks, exports.
- **`event_bus.py`** – Publish/subscribe API, DLQ support.
- **`mesh_adapter.py`** – Core adapter, pluggable backends, sharding.
- **`mesh_policy.py`** – Policy save/load/enforce, HMAC, breakers.
- **`checkpoint/` subpackage** –  
    - **checkpoint_manager.py**: Orchestration, rotation, breakers.  
    - **checkpoint_utils.py**: Crypto, MultiFernet, masking.  
    - **checkpoint_exceptions.py**: Exception hierarchy, observability.  
    - **checkpoint_backends.py**: Storage implementations, MinIO, Etcd.

---

## Tests

- **Run Tests:**
    ```bash
    pytest -v --cov . --cov-report=html
    pytest tests/test_event_bus.py --backend=redis
    ```
    Coverage target: 95%+
- **Structure:** Unit, integration, E2E, security, reliability, performance.
- **Writing Tests:**  
    - Use conftest.py fixtures.  
    - Mark slow tests with `@pytest.mark.slow`.  
    - Mock externals for isolation.

---

## Troubleshooting

- **ImportError:** Install missing deps (`pip install cryptography` etc).
- **Prod Mode Exit:** Set required env vars (encryption keys, etc).
- **Backend Connection Fail:** Verify URLs/creds; use module harness.
- **DLQ Overflow:** Tune rotation; monitor/replay as needed.
- **Decryption Fail:** Check key rotation, HMAC.
- **Policy Denied:** Validate JWT and enforcer logic.
- **CVE Warnings:** Update deps (see logs).
- **Test Failures:** Run with `-v`, check backend availability.

**Debug Tips:**  
Set `LOG_LEVEL=DEBUG`, enable tracing, monitor metrics, inspect DLQ files.

---

## Best Practices

- **Security:** Use secrets managers for keys/creds, enforce prod mode, rotate keys, scrub PII, validate all inputs.
- **Performance:** Use Redis Streams, tune retries, enable caching, shard by tenant, rate limit.
- **Observability:** Use Grafana/Prometheus, OTEL Collector, alert on thresholds, log with context.
- **Deployment:** Use Docker/K8s, healthchecks, CI/CD with lint/test/scan, scale horizontally.

---

## Contribution Guidelines

- **Code Style:** PEP 8, `black`, `ruff`, type hints (`mypy`), Google-style docstrings.
- **Testing:** 95%+ coverage, unit/integration/E2E for new features, use fixtures/mocks.
- **PR Process:** Branch `feature/<name>` or `fix/<name>`, describe changes, link issues, include tests/docs, require 2+ approvals, CI pass, squash to main.

---

## Roadmap

- Async-native backends (aioetcd3, async GCS)
- New pub/sub: Microsoft Service Bus, Pulsar
- Advanced RBAC/ABAC in mesh_policy.py
- xAI Grok 3 API integration
- Complete MinIO backend for checkpointing

---

## Support

Questions?  
Email: [support@unexpectedinnovations.com](mailto:support@unexpectedinnovations.com)  
File issues: `<enterprise-repo-url>/issues` (enterprise access required)

---

## License

**Proprietary and Confidential © 2025 Unexpected Innovations Inc. All rights reserved.**  
Mesh Module and Self-Fixing Engineer™ are proprietary technologies.

Unauthorized copying, distribution, reverse engineering, or use is strictly prohibited.

For commercial licensing or evaluation, contact [support@unexpectedinnovations.com](mailto:support@unexpectedinnovations.com).

---

Unify your distributed workflows with Mesh’s resilient orchestration!