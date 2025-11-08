Audit Log Module
Overview
The audit_log module is a comprehensive, secure audit logging system within the README to Code Generator project, part of The Code Factory. It provides robust logging, querying, and cryptographic operations for regulated industries such as finance, healthcare, and government, ensuring compliance with SOC2, PCI DSS, and HIPAA standards. The module supports multiple interfaces (REST API, gRPC, CLI) and storage backends (file-based, SQLite, cloud, streaming), with advanced features like PII redaction, cryptographic chaining, and observability.
Key Features

Interfaces: Supports REST API (FastAPI), gRPC (AuditService), and CLI (typer) for flexible logging and querying.
Backends: Includes FileBackend, SQLiteBackend, S3Backend, GCSBackend, AzureBlobBackend, HTTPBackend, KafkaBackend, SplunkBackend, and InMemoryBackend.
Cryptography: Provides secure key management, signing, verification, and HMAC fallback via SoftwareCryptoProvider and HSMCryptoProvider.
Secret Management: Uses production-grade secret managers (AWSSecretsManager, GCPSecretManager, VaultSecretManager) for secure secret retrieval.
Compliance: Implements PII/secret redaction, tamper detection, and audit logging for regulatory compliance.
Observability: Integrates Prometheus metrics (e.g., audit_log_writes_total, sign_operations_total) and OpenTelemetry tracing.
Fault Tolerance: Includes retry logic, circuit breaking, and persistent retry queues for reliable operation.
Extensibility: Offers a plugin system for custom event processing and a pluggable backend architecture.

Architecture
The audit_log module is organized into three submodules: core, cryptographic, and backend components.
Core Components

audit_log.py: Implements the AuditLog class, managing logging, querying, and interface integration (REST API, gRPC, CLI). Coordinates with backends and plugins.
audit_metrics.py: Defines Prometheus metrics and alerting for monitoring audit log operations and anomalies.
audit_plugins.py: Implements a plugin system (AuditPlugin) for pre- and post-processing of audit events.
audit_utils.py: Provides utilities for PII redaction (via presidio-analyzer), hashing, and alerting.
audit_log.proto: Defines the gRPC service (AuditService) with LogAction and GetRecentHistory RPCs.
__init__.py: Module marker for the core components.

Cryptographic Components

audit_crypto_factory.py: Implements CryptoProviderFactory for initializing and configuring cryptographic providers (SoftwareCryptoProvider, HSMCryptoProvider).
audit_crypto_ops.py: Provides high-level cryptographic operations (sign_async, verify_async, chain_entry_async) with HMAC fallback.
audit_crypto_provider.py: Defines CryptoProvider abstract base class and concrete implementations for signing, verification, and key management.
audit_keystore.py: Implements KeyStore and FileKeyStorageBackend for secure key storage, retrieval, and deletion.
secrets.py: Manages secure secret retrieval using AWSSecretsManager, GCPSecretManager, VaultSecretManager, and MockSecretManager (non-production).

Backend Components

audit_backend_core.py: Defines LogBackend abstract base class for backend operations (append, query, schema migration, health checks).
audit_backend_file_sql.py: Implements FileBackend (file-based with WAL) and SQLiteBackend (SQLite with WAL journal mode).
audit_backend_cloud.py: Implements cloud backends (S3Backend, GCSBackend, AzureBlobBackend) with batch writes and schema migration.
audit_backend_streaming.py: Compatibility shim re-exporting streaming backend components.
audit_backend_streaming_backends.py: Implements streaming backends (HTTPBackend, KafkaBackend, SplunkBackend, InMemoryBackend).
audit_backend_streaming_utils.py: Provides utilities like SensitiveDataFilter, SimpleCircuitBreaker, and PersistentRetryQueue/FileBackedRetryQueue.

Module Dependencies
The module relies on the following dependencies, which must be installed:
pip install fastapi grpcio grpcio-tools boto3 google-cloud-storage azure-storage-blob aiohttp aiokafka sqlite3 zlib zstandard dynaconf cryptography pkcs11 prometheus-client opentelemetry-sdk typer aiofiles presidio-analyzer google-cloud-secretmanager hvac

Usage
The audit_log module supports logging and querying audit events via multiple interfaces. Below is an example using the REST API with FileBackend:
import asyncio
import json
from fastapi.testclient import TestClient
from audit_log import api_app

client = TestClient(api_app)

async def main():
    # Log an event
    entry = {
        "action": "user_login",
        "details_json": json.dumps({"email": "user@example.com"}),
        "trace_id": "123e4567-e89b-12d3-a456-426614174000",
        "actor": "user-123",
        "access_token": "mock_token"
    }
    response = client.post("/log", json=entry)
    print(response.json())

    # Query recent history
    response = client.get("/recent_history?limit=1", headers={"access_token": "mock_token"})
    print(response.json())

asyncio.run(main())

Configuration
Configuration is managed via environment variables or a audit_config.yaml file, using dynaconf. Key environment variables include:

Core:

AUDIT_LOG_BACKEND_TYPE: Backend type (file, sqlite, s3, http, etc.).
AUDIT_LOG_BACKEND_PARAMS: Backend-specific parameters (e.g., {"log_file": "/path/to/audit.log"}).
AUDIT_LOG_ENCRYPTION_KEY: Base64-encoded encryption key.
AUDIT_LOG_GRPC_PORT: Port for gRPC server (default 50051).
AUDIT_LOG_IMMUTABLE: Enable immutable logging (true/false).


Backend:

AUDIT_COMPRESSION_ALGO: Compression algorithm (zstd, gzip, none).
AUDIT_BATCH_FLUSH_INTERVAL: Batch flush interval (1–60 seconds).
AUDIT_TAMPER_DETECTION_ENABLED: Enable tamper detection (true/false).
Backend-specific: log_file (File), db_file (SQLite), bucket (S3), endpoint (HTTP).


Crypto:

AUDIT_CRYPTO_PROVIDER_TYPE: Crypto provider (software, hsm).
AUDIT_CRYPTO_DEFAULT_ALGO: Algorithm (rsa, ecdsa, ed25519, hmac).
AUDIT_CRYPTO_SOFTWARE_KEY_DIR: Directory for software keys.
AUDIT_CRYPTO_KMS_KEY_ID: AWS KMS Key ID.
SECRET_MANAGER: Secret manager type (aws, gcp, vault, mock).



Example environment variables:
export AUDIT_LOG_BACKEND_TYPE=file
export AUDIT_LOG_BACKEND_PARAMS='{"log_file": "/path/to/audit.log"}'
export AUDIT_LOG_ENCRYPTION_KEY='base64_encoded_key'
export AUDIT_CRYPTO_PROVIDER_TYPE=software
export AUDIT_CRYPTO_DEFAULT_ALGO=rsa
export AUDIT_CRYPTO_SOFTWARE_KEY_DIR=/path/to/keys
export AUDIT_CRYPTO_KMS_KEY_ID=mock_kms_key_id
export AWS_REGION=us-east-1
export SECRET_MANAGER=aws

Compliance and Security
The module is designed for regulated environments:

PII/Secret Redaction: Uses presidio-analyzer and SensitiveDataFilter to redact sensitive data (e.g., emails, tokens) in logs and entries.
Tamper Detection: Implements cryptographic chaining with _audit_hash and signature verification.
Audit Logging: Logs all operations (logging, querying, key events, errors) via audit_log.log_action.
Encryption: Uses cryptography.fernet for data encryption and AES-GCM for key storage.
Secret Management: Enforces production-grade secret managers, blocking MockSecretManager in production.
Secure Key Storage: Implements atomic writes and POSIX advisory locking for keys.
Observability: Emits Prometheus metrics and OpenTelemetry traces for all operations.

Testing
The module includes unit and E2E integration tests:

Unit Tests: Located in test_audit_log.py, test_audit_metrics.py, test_audit_plugins.py, test_audit_utils.py, test_audit_crypto_*.py, test_secrets.py, and test_audit_backend_*.py.
E2E Tests: Located in test_e2e_audit_log.py, test_e2e_audit_crypto.py, and test_e2e_audit_backend.py, validating full workflows across interfaces, backends, and crypto operations.
Run tests with:pytest -v --cov=audit_log --cov=audit_metrics --cov=audit_plugins --cov=audit_utils --cov=audit_crypto_factory --cov=audit_crypto_ops --cov=audit_crypto_provider --cov=audit_keystore --cov=secrets --cov=audit_backend_core --cov=audit_backend_file_sql --cov=audit_backend_cloud --cov=audit_backend_streaming --cov=audit_backend_streaming_backends --cov=audit_backend_streaming_utils --cov-report=term-missing --cov-report=html --asyncio-mode=auto



Limitations and TODOs

Production Key Storage: FileBackend and FileKeyStorageBackend are not suitable for production; use HSM or KMS.
Schema Versioning: Full support for schema versioning and rollback is partially implemented.
Backend Scalability: Cloud and streaming backends require optimization for high-throughput scenarios.
HSM Integration: HSMCryptoProvider requires live HSM for production testing.
Plugin Enhancements: Additional plugin hooks for advanced event processing are needed.
Stress Testing: High-throughput stress tests (e.g., using locust) are recommended.

Contributing
This module is closed-source for internal use within The Code Factory. Contributions are managed internally, with changes tracked via audit logs for compliance. For issues or feature requests, contact the internal development team.
License
Proprietary. All rights reserved by The Code Factory.