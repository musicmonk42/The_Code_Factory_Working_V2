<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Audit Log Module Technical Documentation

1\. Introduction

The audit\_log module is a secure, extensible audit logging system within the README to Code Generator project, part of The Code Factory. It is designed for regulated industries (e.g., finance, healthcare, government), ensuring compliance with SOC2, PCI DSS, and HIPAA standards. The module supports multiple interfaces (REST API, gRPC, CLI), storage backends (file-based, SQLite, cloud, streaming), and cryptographic operations (key management, signing, verification), with robust observability and fault tolerance.

1.1 Purpose

This document provides a detailed technical overview of the audit\_log module, covering its architecture, components, interfaces, configuration, compliance features, and testing strategy. It serves as a reference for developers, system architects, and compliance auditors.

1.2 Scope

The module includes:



Core Components: Handle logging, querying, and interface integration (audit\_log.py, audit\_metrics.py, audit\_plugins.py, audit\_utils.py, audit\_log.proto, \_\_init\_\_.py).

Cryptographic Components: Manage keys, cryptographic operations, and secrets (audit\_crypto\_factory.py, audit\_crypto\_ops.py, audit\_crypto\_provider.py, audit\_keystore.py, secrets.py).

Backend Components: Provide storage and retrieval backends (audit\_backend\_core.py, audit\_backend\_file\_sql.py, audit\_backend\_cloud.py, audit\_backend\_streaming.py, audit\_backend\_streaming\_backends.py, audit\_backend\_streaming\_utils.py).



2\. Architecture

The audit\_log module is organized into three submodules: core, cryptographic, and backend components, designed for modularity, extensibility, and compliance.

2.1 Core Components



audit\_log.py:



Purpose: Implements the AuditLog class, coordinating logging, querying, and interface integration (REST API via FastAPI, gRPC via AuditService, CLI via typer).

Key Features: Authentication, plugin integration, backend coordination, tamper detection.

Interfaces: 

REST API: /log (POST), /recent\_history (GET).

gRPC: LogAction, GetRecentHistory RPCs.

CLI: Commands for logging and querying.





Dependencies: fastapi, grpcio, typer, audit\_metrics, audit\_plugins, audit\_utils, audit\_backend.





audit\_metrics.py:



Purpose: Defines Prometheus metrics (e.g., audit\_log\_writes\_total, audit\_log\_errors\_total) and alerting for monitoring.

Key Features: Tracks operations, errors, and anomalies; integrates with prometheus\_client.

Dependencies: prometheus\_client, opentelemetry-sdk.





audit\_plugins.py:



Purpose: Implements AuditPlugin base class for custom event processing (e.g., pre-append, post-query).

Key Features: Pluggable architecture, supports redaction and augmentation.

Dependencies: None (standalone).





audit\_utils.py:



Purpose: Provides utilities for PII redaction (via presidio-analyzer), hashing (compute\_hash), and alerting (send\_alert).

Key Features: Ensures PII compliance and tamper detection.

Dependencies: presidio-analyzer.





audit\_log.proto:



Purpose: Defines the gRPC service (AuditService) with LogAction and GetRecentHistory RPCs.

Key Features: Protobuf-based interface for efficient, typed communication.

Dependencies: grpcio, grpcio-tools.





\_\_init\_\_.py:



Purpose: Module marker for core components.







2.2 Cryptographic Components



audit\_crypto\_factory.py:



Purpose: Implements CryptoProviderFactory for initializing SoftwareCryptoProvider or HSMCryptoProvider.

Key Features: Configures providers via dynaconf, manages KMS decryption, and handles shutdown.

Dependencies: boto3, prometheus\_client, opentelemetry-sdk, audit\_log, secrets.





audit\_crypto\_ops.py:



Purpose: Provides high-level cryptographic operations (sign\_async, verify\_async, chain\_entry\_async) with HMAC fallback.

Key Features: Ensures tamper-evident logging with cryptographic chaining.

Dependencies: cryptography, audit\_crypto\_factory, secrets.





audit\_crypto\_provider.py:



Purpose: Defines CryptoProvider abstract base class and concrete implementations (SoftwareCryptoProvider, HSMCryptoProvider) for signing, verification, key generation, and rotation.

Key Features: Supports RSA, ECDSA, Ed25519; integrates with HSM via pkcs11.

Dependencies: cryptography, pkcs11, audit\_keystore, audit\_crypto\_factory.





audit\_keystore.py:



Purpose: Implements KeyStore and FileKeyStorageBackend for secure key storage, retrieval, and deletion.

Key Features: Uses AES-GCM encryption, atomic writes, and POSIX advisory locking.

Dependencies: cryptography, aiofiles, audit\_crypto\_factory.





secrets.py:



Purpose: Manages secure secret retrieval using AWSSecretsManager, GCPSecretManager, VaultSecretManager, and MockSecretManager (non-production).

Key Features: Enforces production-grade secret managers in compliance mode.

Dependencies: boto3, google-cloud-secretmanager, hvac, audit\_log.





\_\_init\_\_.py:



Purpose: Module marker for cryptographic components.







2.3 Backend Components



audit\_backend\_core.py:



Purpose: Defines LogBackend abstract base class for append, query, schema migration, and health checks.

Key Features: Manages configuration via dynaconf, integrates with Prometheus and OpenTelemetry.

Dependencies: boto3, zstandard, prometheus\_client, opentelemetry-sdk, audit\_utils.





audit\_backend\_file\_sql.py:



Purpose: Implements FileBackend (file-based with write-ahead logging) and SQLiteBackend (SQLite with WAL journal mode).

Key Features: Ensures atomicity and durability for local storage.

Dependencies: aiofiles, sqlite3, audit\_backend\_core.





audit\_backend\_cloud.py:



Purpose: Implements cloud backends (S3Backend, GCSBackend, AzureBlobBackend) with batch writes and schema migration.

Key Features: Supports AWS Athena for S3 queries, batch uploads for cloud storage.

Dependencies: boto3, google-cloud-storage, azure-storage-blob, audit\_backend\_core.





audit\_backend\_streaming.py:



Purpose: Compatibility shim re-exporting streaming backend components.

Key Features: Simplifies imports for streaming backends.

Dependencies: audit\_backend\_streaming\_backends, audit\_backend\_streaming\_utils.





audit\_backend\_streaming\_backends.py:



Purpose: Implements streaming backends (HTTPBackend, KafkaBackend, SplunkBackend, InMemoryBackend).

Key Features: Supports batch uploads, retry queues, and circuit breaking.

Dependencies: aiohttp, aiokafka, audit\_backend\_core, audit\_backend\_streaming\_utils.





audit\_backend\_streaming\_utils.py:



Purpose: Provides utilities like SensitiveDataFilter (PII redaction), SimpleCircuitBreaker (fault tolerance), and PersistentRetryQueue/FileBackedRetryQueue (retry handling).

Key Features: Enhances streaming backend resilience and compliance.

Dependencies: prometheus\_client, aiofiles, audit\_backend\_core.





\_\_init\_\_.py:



Purpose: Module marker for backend components.







2.4 Data Flow



Event Logging:



An audit event (e.g., user\_login) is submitted via REST API, gRPC, or CLI.

The AuditLog class processes the event, applying plugins (audit\_plugins) for pre-processing (e.g., redaction).

The event is signed and chained using audit\_crypto\_ops with keys from audit\_crypto\_provider.

The backend (audit\_backend\_\*.py) encrypts and stores the event, ensuring atomicity and tamper detection.





Querying:



Queries are submitted via REST API or gRPC, authenticated with an access token.

The backend retrieves entries, verifies signatures, and applies post-processing plugins.





Key Management:



Keys are generated and stored via audit\_crypto\_provider and audit\_keystore.

Secrets (e.g., HSM PIN) are retrieved securely via secrets.





Observability:



Metrics are emitted via audit\_metrics and prometheus\_client.

Traces are generated via opentelemetry-sdk.







3\. Interfaces

The module supports three interfaces:



REST API (FastAPI):



Endpoints: /log (POST), /recent\_history (GET).

Authentication: Requires access\_token header.

Example: POST /log {"action": "user\_login", "details\_json": "{\\"email\\": \\"user@example.com\\"}"}.





gRPC (AuditService):



RPCs: LogAction, GetRecentHistory.

Protobuf: Defined in audit\_log.proto.

Example: LogActionRequest(action="user\_login", details\_json="{\\"email\\": \\"user@example.com\\"}").





CLI (typer):



Commands: log, query.

Example: audit\_log log --action user\_login --details-json '{"email": "user@example.com"}'.







4\. Configuration

Configuration is managed via environment variables or a audit\_config.yaml file using dynaconf. Key settings include:

4.1 Core Configuration



AUDIT\_LOG\_BACKEND\_TYPE: Backend type (file, sqlite, s3, http, etc.).

AUDIT\_LOG\_BACKEND\_PARAMS: Backend-specific parameters (e.g., {"log\_file": "/path/to/audit.log"}).

AUDIT\_LOG\_ENCRYPTION\_KEY: Base64-encoded encryption key.

AUDIT\_LOG\_GRPC\_PORT: gRPC server port (default 50051).

AUDIT\_LOG\_IMMUTABLE: Enable immutable logging (true/false).



4.2 Backend Configuration



AUDIT\_COMPRESSION\_ALGO: Compression algorithm (zstd, gzip, none).

AUDIT\_BATCH\_FLUSH\_INTERVAL: Batch flush interval (1–60 seconds).

AUDIT\_TAMPER\_DETECTION\_ENABLED: Enable tamper detection (true/false).

Backend-specific:

FileBackend: log\_file.

SQLiteBackend: db\_file.

S3Backend: bucket, athena\_results\_location, athena\_database, athena\_table.

HTTPBackend: endpoint, query\_endpoint, headers, timeout, verify\_ssl.







4.3 Crypto Configuration



AUDIT\_CRYPTO\_PROVIDER\_TYPE: Crypto provider (software, hsm).

AUDIT\_CRYPTO\_DEFAULT\_ALGO: Algorithm (rsa, ecdsa, ed25519, hmac).

AUDIT\_CRYPTO\_SOFTWARE\_KEY\_DIR: Directory for software keys.

AUDIT\_CRYPTO\_KMS\_KEY\_ID: AWS KMS Key ID.

AUDIT\_CRYPTO\_HSM\_ENABLED: Enable HSM (true/false).

AUDIT\_CRYPTO\_HSM\_LIBRARY\_PATH: PKCS#11 library path.

AUDIT\_CRYPTO\_HSM\_SLOT\_ID: HSM slot ID.

AUDIT\_CRYPTO\_FALLBACK\_HMAC\_SECRET\_B64: HMAC fallback secret (via secrets.py).

SECRET\_MANAGER: Secret manager type (aws, gcp, vault, mock).



Example environment variables:

export AUDIT\_LOG\_BACKEND\_TYPE=file

export AUDIT\_LOG\_BACKEND\_PARAMS='{"log\_file": "/path/to/audit.log"}'

export AUDIT\_LOG\_ENCRYPTION\_KEY='base64\_encoded\_key'

export AUDIT\_CRYPTO\_PROVIDER\_TYPE=software

export AUDIT\_CRYPTO\_DEFAULT\_ALGO=rsa

export AUDIT\_CRYPTO\_SOFTWARE\_KEY\_DIR=/path/to/keys

export AUDIT\_CRYPTO\_KMS\_KEY\_ID=mock\_kms\_key\_id

export AWS\_REGION=us-east-1

export SECRET\_MANAGER=aws



5\. Compliance and Security

The module is designed for regulated environments:



PII/Secret Redaction: Uses presidio-analyzer and SensitiveDataFilter to redact sensitive data (e.g., emails, tokens) in logs and entries.

Tamper Detection: Implements cryptographic chaining with \_audit\_hash and signature verification.

Audit Logging: Logs all operations (logging, querying, key events, errors) via audit\_log.log\_action.

Encryption: Uses cryptography.fernet for data encryption and AES-GCM for key storage.

Secret Management: Enforces production-grade secret managers (AWSSecretsManager, GCPSecretManager, VaultSecretManager) in COMPLIANCE\_MODE=true.

Secure Key Storage: Implements atomic writes and POSIX advisory locking in audit\_keystore.py.

Fault Tolerance: Includes retry logic (retry\_operation), circuit breaking (SimpleCircuitBreaker), and persistent retry queues (FileBackedRetryQueue).

Observability: Emits Prometheus metrics (e.g., audit\_log\_writes\_total, sign\_operations\_total) and OpenTelemetry traces.



6\. Testing

The module includes unit and E2E integration tests to ensure reliability:



Unit Tests:

Core: test\_audit\_log.py, test\_audit\_metrics.py, test\_audit\_plugins.py, test\_audit\_utils.py.

Crypto: test\_audit\_crypto\_factory.py, test\_audit\_crypto\_ops.py, test\_audit\_crypto\_provider.py, test\_audit\_keystore.py, test\_secrets.py.

Backend: test\_audit\_backend\_core.py, test\_audit\_backend\_file\_sql.py, test\_audit\_backend\_cloud.py, test\_audit\_backend\_streaming.py, test\_audit\_backend\_streaming\_backends.py, test\_audit\_backend\_streaming\_utils.py.





E2E Tests:

test\_e2e\_audit\_log.py: Tests core interfaces (REST API, gRPC, CLI).

test\_e2e\_audit\_crypto.py: Tests cryptographic workflows (key generation, signing, verification).

test\_e2e\_audit\_backend.py: Tests backend workflows (append, query, tamper detection).

test\_e2e\_audit\_log\_module.py: Tests integrated workflows across all components.





Run tests with:pytest -v --cov=audit\_log --cov=audit\_metrics --cov=audit\_plugins --cov=audit\_utils --cov=audit\_crypto\_factory --cov=audit\_crypto\_ops --cov=audit\_crypto\_provider --cov=audit\_keystore --cov=secrets --cov=audit\_backend\_core --cov=audit\_backend\_file\_sql --cov=audit\_backend\_cloud --cov=audit\_backend\_streaming --cov=audit\_backend\_streaming\_backends --cov=audit\_backend\_streaming\_utils --cov-report=term-missing --cov-report=html --asyncio-mode=auto







7\. Deployment



Environment Setup:

Install dependencies: pip install fastapi grpcio grpcio-tools boto3 google-cloud-storage azure-storage-blob aiohttp aiokafka sqlite3 zlib zstandard dynaconf cryptography pkcs11 prometheus-client opentelemetry-sdk typer aiofiles presidio-analyzer google-cloud-secretmanager hvac.

Configure environment variables or audit\_config.yaml.





Production Considerations:

Use HSM or KMS for key storage instead of FileKeyStorageBackend.

Deploy secret managers (AWS/GCP/Vault) and disable MockSecretManager.

Enable COMPLIANCE\_MODE=true and AUDIT\_LOG\_IMMUTABLE=true.

Monitor Prometheus metrics and configure alerting.







8\. Limitations and TODOs



Production Key Storage: FileBackend and FileKeyStorageBackend are not suitable for production; use HSM or KMS.

Schema Versioning: Full support for schema versioning and rollback is partially implemented.

Backend Scalability: Cloud and streaming backends require optimization for high-throughput scenarios.

HSM Integration: HSMCryptoProvider requires live HSM for production testing.

Plugin Enhancements: Additional plugin hooks for advanced event processing.

Stress Testing: High-throughput stress tests (e.g., using locust) are recommended.



9\. Contributing

This module is closed-source for internal use within The Code Factory. Contributions are managed internally, with changes tracked via audit logs for compliance. For issues or feature requests, contact the internal development team.

10\. License

Proprietary. All rights reserved by The Code Factory.

