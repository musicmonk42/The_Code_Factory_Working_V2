\# MetaLearning Orchestrator Submodule



This submodule provides core infrastructure for \*\*secure, observable, and distributed machine learning orchestration\*\*, including:

\- Tamper-evident audit logging (file/Kafka)

\- Configurable, validated runtime configuration

\- Structured logging with PII redaction and trace correlation

\- Prometheus metrics (multiprocess-safe)

\- Pluggable HTTP service clients

\- Strongly-typed, immutable data models



It is designed as a reusable foundation within a larger ML orchestration platform.



---



\## Quickstart



\### 1. \*\*Install Dependencies\*\*



```bash

pip install -r requirements.txt

\# For optional features:

pip install aiokafka aioboto3 watchdog etcd3-py boto3 aioredis prometheus\_client opentelemetry-sdk aiohttp aiohttp-client-cache

```



\### 2. \*\*Set Required Environment Variables\*\*



At minimum, set these in your environment or `.env`:



```env

ML\_DATA\_LAKE\_PATH=./data/ml\_data.jsonl

ML\_LOCAL\_AUDIT\_LOG\_PATH=./data/audit\_log.jsonl

\# Optional, for secure mode:

ML\_AUDIT\_ENCRYPTION\_KEY=your-fernet-key

ML\_AUDIT\_SIGNING\_PRIVATE\_KEY=your-private-key

ML\_AUDIT\_SIGNING\_PUBLIC\_KEY=your-public-key

```



\### 3. \*\*Sample Usage\*\*



Initialize config and audit logging:



```python

from config import MetaLearningConfig

from audit\_utils import AuditUtils



config = MetaLearningConfig()

audit = AuditUtils(log\_path=config.LOCAL\_AUDIT\_LOG\_PATH)



await audit.add\_audit\_event(

&nbsp;   "system\_start",

&nbsp;   {"details": "Orchestrator started"}

)

```



---



\## Main Components



\### - `config.py`: \*\*Runtime Configuration\*\*

\- Strongly validated (Pydantic), supports dynamic reload (file/etcd).

\- All config options prefixed `ML\_`, see `MetaLearningConfig`.



\### - `audit\_utils.py`: \*\*Audit Logging\*\*

\- Tamper-evident, append-only, supports file and Kafka.

\- Each event is hashed, linked, signed, optionally encrypted.

\- Prometheus metrics for tamper, crypto, event types.



\### - `logging\_utils.py`: \*\*Structured Logging \& Redaction\*\*

\- JSON logs with trace/span correlation.

\- Automatic PII redaction (configurable keys/regex).



\### - `metrics.py`: \*\*Prometheus Metrics Registry\*\*

\- Multiprocess-safe with global labels for environment/cluster.

\- Metrics for ingestion, training, deployment, audit, and errors.



\### - `clients.py`: \*\*HTTP Service Clients\*\*

\- Async, retrying, observable clients for ML platform and agent config services.

\- Centralized logging, metrics, and tracing.



\### - `models.py`: \*\*Immutable Data Models\*\*

\- Pydantic-based, strongly typed, with business validation.

\- Enums for event types, deployment statuses.



---



\## Configuration Reference



| Env Var                      | Meaning                                        | Example / Default              |

|------------------------------|------------------------------------------------|--------------------------------|

| ML\_DATA\_LAKE\_PATH            | Local data file for learning records           | ./data/ml\_data.jsonl           |

| ML\_LOCAL\_AUDIT\_LOG\_PATH      | Audit log file path                            | ./data/audit\_log.jsonl         |

| ML\_AUDIT\_ENCRYPTION\_KEY      | Fernet key for audit log encryption            | None (plaintext if missing)    |

| ML\_AUDIT\_SIGNING\_PRIVATE\_KEY | PEM ECDSA private key (audit log signing)      | None (unsigned if missing)     |

| ML\_AUDIT\_SIGNING\_PUBLIC\_KEY  | PEM ECDSA public key (audit log verification)  | None (unverified if missing)   |

| ML\_KAFKA\_BOOTSTRAP\_SERVERS   | Kafka brokers (comma-separated)                | localhost:9092                 |

| ML\_USE\_KAFKA\_INGESTION       | Enable Kafka for ingestion (true/false)        | false                          |

| ML\_USE\_KAFKA\_AUDIT           | Enable Kafka for audit logs (true/false)       | false                          |

| ...                          | \*(see `config.py` for all options)\*            |                                |



---



\## Public API (Key Classes)



\### `MetaLearningConfig`

\- All config fields are documented in `config.py` (see Python docstrings).

\- Example: `config.DATA\_LAKE\_PATH`



\### `AuditUtils`

\- `add\_audit\_event(event\_type: str, details: Dict)`

\- `validate\_audit\_chain() -> Dict`

\- Usage: see above.



\### `PIIRedactorFilter`

\- For structured logging, used automatically.



\### `MLPlatformClient`, `AgentConfigurationService`

\- Async methods for training, evaluation, deployment, config updates.

\- Methods: `train\_model`, `get\_training\_status`, `evaluate\_model`, etc.



\### `LearningRecord`, `ModelVersion`

\- Strongly typed, immutable. See `models.py` for fields and validation rules.



---



\## Troubleshooting



\- \*\*Audit log not signed/encrypted:\*\* Check that the relevant keys are set in your environment.

\- \*\*Kafka/S3/Redis errors:\*\* Ensure the services are running and accessible; see logs for details.

\- \*\*PII not redacted:\*\* Confirm `LOGGING\_REDACTION\_ENABLED=true` and patterns are set as needed.

\- \*\*Metrics missing:\*\* Make sure `prometheus\_client` is installed and (for multiprocess) `PROMETHEUS\_MULTIPROC\_DIR` is set.



---



\## Extending



\- \*\*Add a new event type:\*\* Extend `EventType` enum in `models.py`.

\- \*\*Add a new ingestion backend:\*\* Refactor `Ingestor` class in the orchestrator module.

\- \*\*Custom metrics:\*\* Register via `metrics.registry`.

\- \*\*Custom health checks:\*\* Extend `MetaLearningConfig.is\_healthy()`.



---



\## Testing



\- Most components are designed for async/await; use `pytest-asyncio` or similar.

\- For integration, mock external services (Kafka, Redis, S3).



---



\## Further Reading



\- See `docs/` for advanced topics: security, extending audit, advanced metrics, and more.



---



\*\*For more details, see \[TECHNICAL\_DOC.md](./TECHNICAL\_DOC.md).\*\*

