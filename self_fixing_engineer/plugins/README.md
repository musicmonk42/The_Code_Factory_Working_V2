<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Plugins System

A secure, extensible framework for building, loading, and running plugins in orchestration and simulation platforms. The system is designed for **production-readiness**, **security**, and **flexibility**—enabling integration of messaging, notifications, event processing, storage, and runtime extensions.

---

## 🚀 Overview

The Plugins System empowers developers to:

- **Integrate**: Messaging (Kafka, RabbitMQ), notifications (Slack, PagerDuty), event handling (Azure Event Grid, Pub/Sub), backend storage (DLT for tamper-evident checkpoints), and runtime extensions (WASM, gRPC, Python).
- **Standardize**: Enforce a consistent API for plugins, dynamic discovery, versioning, and lifecycle management.
- **Secure**: Sandboxing, auditing, secrets management, permission enforcement.
- **Monitor**: Hot-reload, health checks, metrics, OpenTelemetry tracing, detailed JSON audit logs.

**Production Mode** (`PRODUCTION_MODE=true`) enables strict security (TLS required, demo plugins blocked, manifests/signatures enforced, secrets scrubbing, and full auditing).

---

## 🧩 Core Concepts

### Plugin Manifest

Each plugin must provide a `PLUGIN_MANIFEST` (Python dict, JSON, or YAML) specifying:
- **Metadata:** name, version, author, description, license, homepage, tags
- **Capabilities:** e.g., "publish", "audit", "health_check"
- **Permissions:** e.g., "network_access", "file_write", enforced at runtime
- **Dependencies:** Python packages or binaries
- **Health Check:** function to verify readiness
- **API Version**
- **Demo flag:** (`is_demo_plugin`), which is blocked in prod
- **Security:** HMAC signatures for integrity (in prod)
- **Validation:** Pydantic models

### Plugin Types

- **Python Plugins:** Native Python classes, registered via `@plugin`. Simple, fast, but less isolated.
- **gRPC Plugins:** Remote plugins, loaded with TLS/mTLS, allowlists, and Prometheus metrics.
- **WASM Plugins:** Sandboxed WebAssembly modules, strict memory/time/resource limits, whitelisted host functions, hot-reload.

### Runners

- **grpc_runner.py:** Handles validation, TLS, and monitoring for gRPC plugins.
- **wasm_runner.py:** Loads and manages WASM modules with sandboxing and host function whitelisting.

### Security Model

- **Production Mode:** Enforces TLS, blocks demo plugins, requires signed manifests, scrubs sensitive data, and audits actions.
- **Permissions:** Plugins declare what they need (network, file, etc.)—enforced by the system.
- **Auditing:** All plugin actions are logged via `core_audit.py` (HMAC-signed JSON logs, rate-limited, file-rotated).
- **Secrets Management:** Via `core_secrets.py`—loads from env (or .env in dev), type-safe, reloadable.

### Utilities

- **core_utils.py:** Data scrubbing (regex for secrets), multi-channel alerting (logs, Slack, email, audit).

---

## 💡 Why Use This System?

- **Extensibility:** Add new integrations or backends without touching core code.
- **Isolation:** Sandbox untrusted code (e.g., WASM, gRPC).
- **Compliance-ready:** Auditing, tamper-evidence, PII scrubbing.
- **Resilience:** Retries, circuit breakers, graceful shutdowns.
- **Observability:** Health checks, Prometheus metrics, OpenTelemetry tracing, detailed logs.

---

## 🛠️ Key Capabilities & Supported Plugins

### Capabilities

- **Dynamic Discovery:** Auto-loads plugins by scanning directories and manifests.
- **Lifecycle Management:** Plugins are initialized, started, health-checked, reloaded, and shut down gracefully.
- **API Standardization:** All plugins expose methods such as `health_check()` and `call()`; decorators manage registration.
- **Security Enforcement:** Permissions, allowlists, sandboxing, scrubbing, auditing.
- **Monitoring:** Prometheus metrics, OpenTelemetry tracing, health endpoints.
- **Resilience:** Backoff retries, circuit breakers, dead-letter queues, rate limiting.
- **Configurable:** All via env/secrets; prod disables insecure features.

### Supported Plugins

All plugins are async-capable, configurable via secrets, and come with tests. Each supports dry-run, audit, alerting, and scrubbing.

| Plugin                | Type            | Description                                             | Key Features                                    | Config Keys (Env/Secrets)                            | Example Usage                                       |
|-----------------------|-----------------|---------------------------------------------------------|--------------------------------------------------|------------------------------------------------------|-----------------------------------------------------|
| demo_python_plugin.py | Python          | Demo with health check, blocked in prod                 | Health check, API calls, fallback dummies        | `PRODUCTION_MODE`                                    | `plugin = DemoPythonPlugin(); plugin.health()`       |
| azure_eventgrid_plugin.py | Event Handling | Async Azure Event Grid for audits/events                | Batching, retries, tracing                      | `AZURE_EVENTGRID_ENDPOINT_URL`, `AZURE_EVENTGRID_ACCESS_KEY`, ... | `await hook.audit_hook("event", data)`              |
| dlt_backend.py        | Backend Storage | Tamper-evident checkpoints (Hyperledger Fabric, etc.)   | Save/load/rollback/diff, hash chain, encryption  | `FABRIC_NETWORK_PROFILE`, `OFF_CHAIN_STORAGE_TYPE`, `ENCRYPT_KEY` | `await cm.save("chk", state)`                       |
| kafka_plugin.py       | Messaging       | Kafka producer for audits/events                        | Enqueue, batch, retries, DLQ, metrics           | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, ...        | `await plugin.enqueue_event("event", data)`          |
| pagerduty_plugin.py   | Notification    | PagerDuty event trigger/ack/resolve                     | Queue, batch, retries, circuit breaker          | `PAGERDUTY_ROUTING_KEY`, ...                         | `await gateway.trigger("event", details)`            |
| pubsub_plugin.py      | Messaging       | Google Pub/Sub publisher                                | Publish with attrs, batch, retries, circuit brk  | `PUBSUB_PROJECT_ID`, `PUBSUB_TOPIC_ID`               | `gateway.publish("event", "service", data)`          |
| rabbitmq_plugin.py    | Messaging       | RabbitMQ publisher with routing keys                    | Publish, batch, retries, circuit breaker        | `RABBITMQ_URL`, `RABBITMQ_EXCHANGE`                  | `gateway.publish("event", "service", data, routing_key)` |
| siem_plugin.py        | Logging         | SIEM gateway for multi-target events (e.g., Splunk)     | Multi-target, HMAC, batch, retries, DLQ         | `SIEM_TARGETS_JSON`, `SIEM_ADMIN_API_KEY`            | `manager.publish("target", "event", data)`           |
| slack_plugin.py       | Notification    | Slack webhook to channels                               | Multi-target, batch, retries, DLQ               | `SLACK_TARGETS_JSON`, `SLACK_ADMIN_API_KEY`          | `await manager.publish("channel", "event", data)`    |
| sns_plugin.py         | Messaging       | AWS SNS publisher for notifications                     | Multi-target, batch, retries, DLQ, file locking | `SNS_TARGETS_JSON`, `SNS_ADMIN_API_KEY`              | `await manager.publish("topic", "event", data)`      |

#### Runners

- **grpc_runner.py:** TLS, manifest validation, metrics, health proto interface.
- **wasm_runner.py:** Sandboxed WASM (wasmtime), resource limits, hot-reload.

---

## 🏛️ Architecture

### Core Components

- **core_utils.py:** Scrubbing and alerting (regex redaction, multi-channel dispatch, queue/rate limiting, HTTP retries).
- **core_audit.py:** JSON audit logger (thread-safe, rate-limited, HMAC signed, file-rotated, extra context, SIGHUP reload).
- **core_secrets.py:** Secrets manager (env/.env, type-safe, caching, reloadable, thread-safe).
- **grpc_runner.py:** Connects securely to gRPC plugins, validates manifests, emits Prometheus metrics.
- **wasm_runner.py:** Loads and sandboxes WASM plugins, enforces limits, whitelists host functions.
- **plugin_manager.py:** Loads plugins, registers via decorators, manages lifecycle (assumed).

### Flow

1. **Discovery:** Scan for manifests.
2. **Validation:** Check manifest schema, signature, permissions, dependencies.
3. **Initialization:** Load secrets, configure logging/audit.
4. **Execution:** Call via standardized APIs; runners handle non-Python.
5. **Monitoring:** Health checks, metrics, audit logging.
6. **Shutdown:** Graceful drain of queues/connections.

**Dependencies:**  
Python 3.8+, aiohttp, wasmtime, grpcio, prometheus_client, pydantic, cryptography, redis.asyncio, etc.

---

## 📁 Directory Structure

```
plugins/
  __pycache__/
  azure_eventgrid_plugin/
  dlt_backend/
  kafka/
  pagerduty_plugin/
  pubsub_plugin/
  rabbitmq_plugin/
  siem_plugin/
  slack_plugin/
  sns_plugin/
  tests/
  __init__.py
  core_audit.py
  core_secrets.py
  core_utils.py
  demo_python_plugin.py
  grpc_runner.py
  README.md
  wasm_runner.py
```

- Subdirs contain plugin-specific code/tests (e.g., `slack_plugin/`).
- `tests/` includes both unit and E2E tests (validation, startup, prod checks).

---

## ⚙️ Setup

### Dependencies

Install via pip:

```
pip install aiohttp aiokafka aiormq cryptography dotenv grpcio prometheus_client psutil pydantic pydantic-settings python-dotenv pythonjsonlogger redis wasmtime
```

For specifics:
- Kafka: `aiokafka`
- RabbitMQ: `aiormq`
- WASM: `wasmtime`
- DLT: `hfc`, `boto3`
- Optional: `opentelemetry`, `google-cloud-pubsub`, etc.

### Environment Variables

- `PRODUCTION_MODE`: `"true"` for strict security (default: `"false"`).
- `ENVIRONMENT`: `"prod"`/`"dev"` (affects .env loading).
- `PLUGIN_DIRS`: Comma-separated plugin search paths.
- `HMAC_KEY`: Used for manifest/log signing.
- **Plugin-specific:** e.g., `KAFKA_BOOTSTRAP_SERVERS`, `SLACK_WEBHOOK_URL`.
- **Secrets:** Use a vault in prod; fallback to env vars in dev.

**Dev:** `export PRODUCTION_MODE=false`  
**Prod:** Secure env, no .env, require TLS everywhere.

---

## 🧪 Testing

- **Run All Tests:** `pytest tests/ -v`
- **Coverage:** `pytest --cov=plugins tests/`
- **E2E:** `pytest test_plugins_e2e.py`
- **Fixtures:** Reset singletons, mock externals, temp dirs for files.
- **Edge Cases:** Queue full, timeouts, invalid manifests, tampering.

**Production readiness:** 85%+ coverage, demos allowed only in non-prod.

---

## 🏗️ Building Plugins

### 1. Python Plugin

```python
PLUGIN_MANIFEST = {
    "name": "my_plugin",
    "version": "1.0.0",
    "description": "My plugin",
    "capabilities": ["my_cap"],
    "permissions": ["network_access_limited"],
    "dependencies": ["requests"],
    "health_check": "my_health",
    "api_version": "v1",
    "is_demo_plugin": False
}

from omnicore_engine.plugin_registry import plugin

@plugin(kind=PlugInKind.INTEGRATION)
class MyPlugin:
    def my_health(self):
        return {"ok": True}
    def call(self, data):
        return data
```

- Place in `plugins/`, test with `test_my_plugin.py`.

### 2. gRPC Plugin

- Define proto (e.g., `my_plugin.proto`), implement server.
- Client: Use `grpc_runner.py` for connection.
- Manifest must include endpoint, TLS certs.
- Build proto: `grpc_tools.python_protobuf my_plugin.proto`

### 3. WASM Plugin

- Write in Rust/C++/etc., compile to WASM.
- Export functions (`health_check`, `call`).
- Manifest with sandbox:
    ```json
    {
      "sandbox": {"enabled": true, "memory": "64MB", "runtime_seconds": 5}
    }
    ```
- Test sandbox and limits.

### Common Steps

- **Validation:** Use Pydantic models.
- **Security:** Declare permissions, scrub data.
- **Testing:** Unit (mock deps), E2E (simulate ops/failures).
- **Docs:** Add to README, generate via CLI.

---

## ⚡ Usage

### Loading Plugins

```python
from plugins.plugin_manager import load_all_plugins, PluginManager
manager = PluginManager()
plugins = manager.load_all_plugins(plugin_dirs=["plugins/"])
print(plugins.keys())
```

### Calling Plugins

```python
result = plugins["my_plugin"].call(data={"key": "value"})
health = plugins["my_plugin"].health_check()
```

### Management

- **Reload:** `manager.reload_plugin("my_plugin")`
- **Unload/Shutdown:** `manager.unload_plugin("my_plugin")`
- **Health:** `manager.check_all_health()`

### CLI Tools

- **List:** `python -m plugins.plugin_manager --list`
- **Reload:** `python -m plugins.plugin_manager --reload my_plugin`
- **Docs:** `python -m plugins.plugin_manager --generate-docs output.md`
- **Health:** `python -m plugins.plugin_manager --health`

---

## 🔒 Security & Best Practices

- **Production Mode:** Blocks demo plugins, enforces TLS/mTLS, requires signed manifests/logs.
- **Data Scrubbing:** `scrub_secrets(obj)` redacts secrets/keys/tokens/JWTs/AWS/GCP creds.
- **Auditing:** `audit_logger.log_event(...)`—JSON, rate-limited, HMAC-signed, with context.
- **Secrets:** Use `SecretsManager.get_secret(key, required=True)`. Never hardcode.
- **Sandboxing (WASM):** Memory/time limits, no FS/network by default, host func whitelist.
- **Permissions:** Declared in manifest, enforced by policy.
- **Resilience:** Circuit breakers, retries, DLQs (encrypted files).
- **Monitoring:** Prometheus metrics, OpenTelemetry tracing.

**Best Practices:**
- Review manifests/deps before loading plugins.
- Use dry-run/test mode for new plugins.
- Enable strict writes in auditing for compliance.
- Isolate multi-tenant plugins (containers/namespaces).
- Store large state blobs off-chain.

---

## 🧑‍💻 Testing & Contributing

- **Unit Tests:** One per plugin/core, covering validation, ops, failures, prod checks. Use mocks for external deps (aiohttp, redis).
- **E2E Tests:** `test_plugins_e2e.py`—simulate all flows.
- **Coverage:** Target 85%+, run with pytest-cov.
- **Fixtures:** Reset singletons, mock external calls, use temp files/dirs.
- **Edge Cases:** Queue full, timeouts, invalid manifests, tampering.

**Contributing:**
- PRs must include tests (unit/E2E) and docs. Pass lint (`black`, `pylint`), security checks.
- API changes: bump version, update docs/examples.
- New plugins: Add to table, include manifest, tests, config samples.
- Issues/PRs: Use GitHub with labels.

---

## 🏢 Support & Maintenance

- **Issues/PRs:** GitHub repository.
- **Community:** Discuss in issues or external forums.
- **Maintenance:** Actively maintained (as of August 18, 2025).

---

> Build, audit, and deploy new platform capabilities—**securely, flexibly, and safely**!