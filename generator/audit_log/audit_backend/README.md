<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Audit Backend Module

Overview

The audit\_backend module provides a robust, extensible framework for audit log storage and retrieval in the README to Code Generator project, part of The Code Factory. It supports multiple storage backends, including file-based, SQLite, cloud (AWS S3, Google Cloud Storage, Azure Blob), and streaming (HTTP, Kafka, Splunk, in-memory) solutions, ensuring compliance with regulated industry standards such as SOC2, PCI DSS, and HIPAA. The module is designed for secure, tamper-evident logging with PII redaction, cryptographic chaining, observability, and fault tolerance.

Key Features



Multiple Backends: Supports FileBackend, SQLiteBackend, S3Backend, GCSBackend, AzureBlobBackend, HTTPBackend, KafkaBackend, SplunkBackend, and InMemoryBackend.

Compliance: Implements PII/secret redaction, tamper detection, and audit logging for regulatory compliance.

Observability: Integrates Prometheus metrics and OpenTelemetry tracing for monitoring and alerting.

Security: Uses encryption, cryptographic chaining, and atomic writes to ensure data integrity.

Fault Tolerance: Includes retry logic, circuit breaking, and persistent retry queues for reliable operation.

Extensibility: Provides a pluggable architecture via the LogBackend abstract class and protocol-based design.



Architecture

The audit\_backend module is organized into core, backend-specific, and utility components:



audit\_backend\_core.py: Defines the LogBackend abstract base class with methods for appending, querying, schema migration, and health checks. Manages configuration via dynaconf and integrates with Prometheus and OpenTelemetry.

audit\_backend\_file\_sql.py: Implements FileBackend (file-based storage with write-ahead logging) and SQLiteBackend (SQLite database with WAL journal mode).

audit\_backend\_cloud.py: Implements cloud backends (S3Backend, GCSBackend, AzureBlobBackend) with batch writes and schema migration support (e.g., AWS Athena integration for S3).

audit\_backend\_streaming.py: A compatibility shim re-exporting streaming backend components.

audit\_backend\_streaming\_backends.py: Implements streaming backends (HTTPBackend, KafkaBackend, SplunkBackend, InMemoryBackend) with batch uploads and retry queues.

audit\_backend\_streaming\_utils.py: Provides utilities like SensitiveDataFilter (for PII redaction), SimpleCircuitBreaker (for fault tolerance), and PersistentRetryQueue/FileBackedRetryQueue (for retry handling).

\_\_init\_\_.py: Module marker, enabling imports of backend classes.



Module Dependencies

The module relies on the following dependencies, which must be installed:

pip install boto3 google-cloud-storage azure-storage-blob aiohttp aiokafka sqlite3 zlib zstandard dynaconf prometheus-client opentelemetry-sdk aiofiles audit\_log audit\_utils



Usage

The audit\_backend module is designed to integrate with the audit\_log system, providing storage backends for audit events. Below is an example of using the FileBackend:

from audit\_backend\_file\_sql import FileBackend

import asyncio



async def main():

&nbsp;   backend = FileBackend({"log\_file": "/path/to/audit.log"})

&nbsp;   entry = {

&nbsp;       "action": "user\_login",

&nbsp;       "details\_json": json.dumps({"email": "user@example.com"}),

&nbsp;       "trace\_id": "123e4567-e89b-12d3-a456-426614174000",

&nbsp;       "actor": "user-123"

&nbsp;   }

&nbsp;   await backend.append(entry)

&nbsp;   results = await backend.query({"entry\_id": entry\["entry\_id"]}, limit=1)

&nbsp;   print(results)



asyncio.run(main())



Configuration

Configuration is managed via environment variables or a audit\_config.yaml file, using dynaconf. Key environment variables include:



AUDIT\_ENCRYPTION\_KEYS: List of base64-encoded encryption keys for data protection.

AUDIT\_COMPRESSION\_ALGO: Compression algorithm (zstd, gzip, none).

AUDIT\_COMPRESSION\_LEVEL: Compression level (1–22, default 9).

AUDIT\_BATCH\_FLUSH\_INTERVAL: Interval for batch flushing (1–60 seconds).

AUDIT\_BATCH\_MAX\_SIZE: Maximum batch size (10–1000 entries).

AUDIT\_HEALTH\_CHECK\_INTERVAL: Interval for health checks (30–300 seconds).

AUDIT\_RETRY\_MAX\_ATTEMPTS: Maximum retry attempts for operations (1–5).

AUDIT\_RETRY\_BACKOFF\_FACTOR: Backoff factor for retries (0.1–2.0).

AUDIT\_TAMPER\_DETECTION\_ENABLED: Enable tamper detection (default true).



Backend-specific configurations:



FileBackend: log\_file (path to log file).

SQLiteBackend: db\_file (path to SQLite database).

S3Backend: bucket, athena\_results\_location, athena\_database, athena\_table.

HTTPBackend: endpoint, query\_endpoint, headers, timeout, verify\_ssl.



Example environment variables:

export AUDIT\_LOG\_BACKEND\_TYPE=file

export AUDIT\_LOG\_BACKEND\_PARAMS='{"log\_file": "/path/to/audit.log"}'

export AUDIT\_ENCRYPTION\_KEYS='\["base64\_encoded\_key"]'

export AUDIT\_COMPRESSION\_ALGO=zlib

export AUDIT\_TAMPER\_DETECTION\_ENABLED=true



Compliance and Security

The module is designed for regulated environments:



PII/Secret Redaction: Uses presidio-analyzer for PII redaction and SensitiveDataFilter for log sanitization.

Tamper Detection: Implements cryptographic chaining with \_audit\_hash to detect tampering.

Audit Logging: Integrates with audit\_log.log\_action to log all operations (appends, queries, errors).

Encryption: Uses cryptography.fernet for data encryption in backends.

Atomic Operations: Ensures data integrity with atomic writes (file-based, SQLite) and batch uploads (cloud, streaming).

Observability: Emits Prometheus metrics (e.g., audit\_backend\_writes\_total, audit\_backend\_errors\_total) and OpenTelemetry traces.



Testing

The module includes unit and E2E integration tests to ensure reliability:



Unit Tests: Located in test\_audit\_backend\_\*.py files, covering each backend and utility.

E2E Tests: Located in test\_e2e\_audit\_backend.py, validating workflows for append, query, and error handling across backends.

Run tests with:pytest -v --cov=audit\_backend\_core --cov=audit\_backend\_file\_sql --cov=audit\_backend\_cloud --cov=audit\_backend\_streaming\_backends --cov=audit\_backend\_streaming\_utils --cov-report=term-missing --cov-report=html --asyncio-mode=auto







Limitations and TODOs



Production Key Storage: FileBackend and SQLiteBackend are not suitable for production-grade key storage; use HSM or KMS instead (see audit\_keystore.py).

Cloud Backend Scalability: S3Backend supports Athena queries but requires optimization for high-throughput scenarios.

Streaming Backend Resilience: KafkaBackend and SplunkBackend require additional testing for partition handling and HEC-specific configurations.

Schema Versioning: Full support for schema versioning and rollback is partially implemented; complete in future iterations.



Contributing

This module is closed-source for internal use within The Code Factory. Contributions are managed internally, with changes tracked via audit logs for compliance. For issues or feature requests, contact the internal development team.

License

Proprietary. All rights reserved by The Code Factory.

