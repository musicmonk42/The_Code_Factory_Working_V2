<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Technical Documentation: MetaLearning Orchestrator Submodule



\## 1. Architecture \& Boundaries



This submodule provides \*\*secure, observable, and type-safe infrastructure\*\* for ML orchestration and is intended to be embedded in a larger platform. It exposes:

\- \*\*Audit log chain\*\* (file/Kafka, cryptographically linked \& signed)

\- \*\*Config loader\*\* (Pydantic, runtime reload)

\- \*\*Prometheus metrics\*\* (multiprocess, global labels)

\- \*\*Structured logging\*\* (JSON, trace correlation, PII redaction)

\- \*\*Async service clients\*\* (ML platform, agent config)

\- \*\*Immutable, validated data models\*\*



\*\*All external dependencies (Kafka, S3, Redis, etc.) must be provisioned by the parent application.\*\*



---



\## 2. Main Components



\### 2.1. `config.py`

\- \*\*MetaLearningConfig\*\*: Pydantic-based, all config from env/`.env`/file/etcd.

\- \*\*Dynamic reload\*\*: Via file (watchdog) or etcd (optional).

\- \*\*Health checks\*\*: Redis, S3, and more.

\- \*\*SECURE\_MODE\*\*: Enforces that crypto keys are present; logs warnings if missing.



\### 2.2. `audit\_utils.py`

\- \*\*AuditUtils\*\*: Tamper-evident append-only log.

&nbsp; - \*\*Event chaining\*\*: Each event includes previous hash.

&nbsp; - \*\*ECDSA signatures\*\*: Non-repudiable audit.

&nbsp; - \*\*Fernet encryption\*\*: Optional, for sensitive details.

&nbsp; - \*\*Rotation \& retention\*\*: File/Kafka, configurable.

&nbsp; - \*\*Prometheus metrics\*\*: For events, tamper, crypto errors.

\- \*\*Usage\*\*:

&nbsp; ```python

&nbsp; audit = AuditUtils(log\_path=config.LOCAL\_AUDIT\_LOG\_PATH)

&nbsp; await audit.add\_audit\_event("event\_type", {"key": "value"})

&nbsp; validation = await audit.validate\_audit\_chain()

&nbsp; ```



\### 2.3. `logging\_utils.py`

\- \*\*PIIRedactorFilter\*\*: Recursively redacts sensitive fields (configurable via env).

\- \*\*JSONFormatter\*\*: All logs are JSON, include trace/span if OTel tracing is active.

\- \*\*LogCorrelationFilter\*\*: Adds OTel trace/span IDs to logs.



\### 2.4. `metrics.py`

\- \*\*MetricRegistry\*\*: Registers Prometheus metrics with multiprocess support.

\- \*\*Metrics\*\*: All key orchestrator/business events, latency histograms, error counters.

\- \*\*Global labels\*\*: `environment`, `cluster`.



\### 2.5. `clients.py`

\- \*\*BaseHTTPClient\*\*: Abstracts out async session, retries, PII redaction, metrics, tracing.

\- \*\*MLPlatformClient\*\*: Train, status, eval, deploy.

\- \*\*AgentConfigurationService\*\*: Update agent config, rollback, delete, etc.



\### 2.6. `models.py`

\- \*\*LearningRecord\*\*: Immutable, strongly-typed, event-type enum.

\- \*\*ModelVersion\*\*: Deployment status enum, business validation, immutable.



---



\## 3. Integration Points



\- \*\*Audit log\*\*: Accepts events via `add\_audit\_event`; can validate entire chain.

\- \*\*Config\*\*: Used for all env/config lookups; reloadable at runtime.

\- \*\*Metrics\*\*: Can be scraped via Prometheus HTTP exporter (not included in this submodule).

\- \*\*Logging\*\*: All logs are JSON, suitable for ELK/Splunk/etc. Out-of-the-box OTel tracing integration.

\- \*\*Service clients\*\*: Used by orchestrator to interact with ML platform and agent config service.



---



\## 4. Failure Modes \& Recovery



\- \*\*Missing keys\*\*: Audit log will warn and downgrade to unsigned or plaintext events.

\- \*\*Kafka/S3/Redis down\*\*: Errors logged, metrics incremented; audit and ingestion will fallback to local file where possible.

\- \*\*Audit chain break\*\*: Validation will fail, alerts via metrics/logs. No auto-repair; manual operator intervention required.

\- \*\*Config reload error\*\*: Falls back to last good config, logs error.



---



\## 5. Extending This Submodule



\- \*\*Add a new event type\*\*: Extend the `EventType` enum in `models.py`. Use in audit and orchestrator.

\- \*\*Add a new config field\*\*: Add to `MetaLearningConfig` with appropriate validation.

\- \*\*Add a new backend\*\*: Refactor `audit\_utils.py` or `clients.py` to add new storage or service.

\- \*\*Custom metrics\*\*: Use `metrics.registry.get\_or\_create(...)`.



---



\## 6. Testing



\- \*\*Unit tests\*\*: Use `pytest` and `pytest-asyncio`.

\- \*\*Mocking external dependencies\*\*: Use test doubles for Kafka, Redis, S3, etc.

\- \*\*Audit chain tests\*\*: Insert, tamper, and validate events.



---



\## 7. Security Considerations



\- \*\*Always set keys in production\*\* (`AUDIT\_ENCRYPTION\_KEY`, `AUDIT\_SIGNING\_PRIVATE\_KEY`, `AUDIT\_SIGNING\_PUBLIC\_KEY`).

\- \*\*Monitor metrics\*\* for hash/signature mismatches, crypto errors.

\- \*\*Do not disable PII redaction\*\* (`LOGGING\_REDACTION\_ENABLED=false`) in production.



---



\## 8. FAQ



\*\*Q:\*\* \_How do I know if audit logging is secure?\_

> Check logs for “private key missing” or “not set”; look for nonzero `ml\_audit\_signature\_mismatch\_total` metric in Prometheus.



\*\*Q:\*\* \_Can I use this with my own orchestrator?\_

> Yes, as long as you supply all required config/env vars and integrate with your preferred event loop and service endpoints.



\*\*Q:\*\* \_What breaks the audit chain?\_

> Any tampering or corruption of the log file/Kafka topic, or lost keys. Validate regularly.



---



For any further questions, \*\*see inline docstrings\*\* in each file or reach out to the maintainers.

