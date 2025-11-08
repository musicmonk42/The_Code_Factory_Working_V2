Audit Crypto Module

Overview

The audit\_crypto module provides a robust, secure cryptographic infrastructure for the README to Code Generator project, part of The Code Factory. It handles key management, cryptographic operations (signing, verification, chaining), and secret storage, ensuring compliance with regulated industry standards such as SOC2, PCI DSS, and HIPAA. The module is designed for tamper-evident audit logging, secure key storage, and resilient cryptographic operations in environments like finance, healthcare, or government.

Key Features



Cryptographic Providers: Supports SoftwareCryptoProvider (software-based keys) and HSMCryptoProvider (hardware security module integration via PKCS#11).

Key Management: Secure key generation, storage, retrieval, and deletion with KeyStore and pluggable backends.

Cryptographic Operations: Implements signing, verification, and cryptographic chaining with HMAC fallback for resilience.

Secret Management: Securely retrieves secrets (e.g., HSM PIN, HMAC fallback secret) using production-grade secret managers (AWSSecretsManager, GCPSecretManager, VaultSecretManager).

Compliance: Ensures PII/secret redaction, audit logging, and tamper detection.

Observability: Integrates Prometheus metrics (e.g., sign\_operations\_total, crypto\_errors\_total) and OpenTelemetry tracing.

Security: Uses cryptography for robust encryption and SensitiveDataFilter to prevent sensitive data leakage in logs.

Fault Tolerance: Includes retry logic, fallback signing, and error alerting for critical operations.



Architecture

The audit\_crypto module is organized into core, provider, key storage, and secret management components:



audit\_crypto\_factory.py: Implements CryptoProviderFactory for initializing and configuring cryptographic providers (SoftwareCryptoProvider, HSMCryptoProvider). Manages KMS decryption and global state.

audit\_crypto\_ops.py: Provides high-level cryptographic operations (sign\_async, verify\_async, chain\_entry\_async) with HMAC fallback support for resilience.

audit\_crypto\_provider.py: Defines the CryptoProvider abstract base class and concrete implementations (SoftwareCryptoProvider, HSMCryptoProvider) for signing, verification, key generation, and rotation.

audit\_keystore.py: Implements KeyStore and FileKeyStorageBackend for secure key storage, retrieval, and deletion, with atomic writes and POSIX advisory locking.

secrets.py: Manages secure secret retrieval using AWSSecretsManager, GCPSecretManager, VaultSecretManager, and a MockSecretManager for non-production environments.

\_\_init\_\_.py: Module marker, enabling imports of crypto components.



Module Dependencies

The module relies on the following dependencies, which must be installed:

pip install boto3 cryptography pkcs11 prometheus-client opentelemetry-sdk aiofiles audit\_log



Optional dependencies for specific secret managers:

pip install google-cloud-secretmanager hvac



Usage

The audit\_crypto module integrates with the audit\_log system to provide cryptographic security for audit events. Below is an example of generating a key, signing, and verifying an audit log entry:

import asyncio

import json

from audit\_crypto\_factory import CryptoProviderFactory

from audit\_crypto\_ops import sign\_async, verify\_async



async def main():

&nbsp;   factory = CryptoProviderFactory()

&nbsp;   provider = factory.get\_provider("software")

&nbsp;   

&nbsp;   # Generate a key

&nbsp;   key\_id = await provider.generate\_key("rsa", 2048)

&nbsp;   

&nbsp;   # Sign an audit log entry

&nbsp;   entry = {

&nbsp;       "action": "user\_login",

&nbsp;       "details\_json": json.dumps({"email": "user@example.com"}),

&nbsp;       "trace\_id": "123e4567-e89b-12d3-a456-426614174000"

&nbsp;   }

&nbsp;   prev\_hash = "prev\_hash\_mock"

&nbsp;   signature, key\_id = await sign\_async(entry, prev\_hash)

&nbsp;   

&nbsp;   # Verify the signature

&nbsp;   entry\_with\_signature = entry.copy()

&nbsp;   entry\_with\_signature\["signature"] = signature

&nbsp;   entry\_with\_signature\["key\_id"] = key\_id

&nbsp;   is\_valid = await verify\_async(entry\_with\_signature, prev\_hash)

&nbsp;   print(f"Signature valid: {is\_valid}")



asyncio.run(main())



Configuration

Configuration is managed via environment variables or a audit\_config.yaml file, using dynaconf. Key environment variables include:



AUDIT\_CRYPTO\_PROVIDER\_TYPE: Crypto provider type (software, hsm).

AUDIT\_CRYPTO\_DEFAULT\_ALGO: Default algorithm (rsa, ecdsa, ed25519, hmac).

AUDIT\_CRYPTO\_KEY\_ROTATION\_INTERVAL\_SECONDS: Key rotation interval (minimum 86400 seconds).

AUDIT\_CRYPTO\_SOFTWARE\_KEY\_DIR: Directory for software keys.

AUDIT\_CRYPTO\_KMS\_KEY\_ID: AWS KMS Key ID for software provider.

AUDIT\_CRYPTO\_ALERT\_ENDPOINT: URL for sending critical alerts.

AUDIT\_CRYPTO\_HSM\_ENABLED: Enable HSM (true, false).

AUDIT\_CRYPTO\_HSM\_LIBRARY\_PATH: Path to PKCS#11 library for HSM.

AUDIT\_CRYPTO\_HSM\_SLOT\_ID: HSM slot ID.

AUDIT\_CRYPTO\_FALLBACK\_HMAC\_SECRET\_B64: Base64-encoded HMAC fallback secret (managed via secrets.py).

AWS\_REGION: AWS region for KMS and Secrets Manager.

SECRET\_MANAGER: Secret manager type (aws, gcp, vault, mock).



Example environment variables:

export AUDIT\_CRYPTO\_PROVIDER\_TYPE=software

export AUDIT\_CRYPTO\_DEFAULT\_ALGO=rsa

export AUDIT\_CRYPTO\_SOFTWARE\_KEY\_DIR=/path/to/keys

export AUDIT\_CRYPTO\_KMS\_KEY\_ID=mock\_kms\_key\_id

export AWS\_REGION=us-east-1

export SECRET\_MANAGER=aws



Compliance and Security

The module is designed for regulated environments:



PII/Secret Redaction: Uses SensitiveDataFilter to redact sensitive data (e.g., HSM PIN, HMAC secrets) in logs.

Tamper Detection: Implements cryptographic chaining via \_audit\_hash in audit\_crypto\_ops.py.

Audit Logging: Integrates with audit\_log.log\_action to log all key events (generation, signing, verification) and errors.

Encryption: Uses cryptography for key generation and signing, with AES-GCM for key storage in audit\_keystore.py.

Secret Management: Enforces production-grade secret managers (AWSSecretsManager, GCPSecretManager, VaultSecretManager) and blocks MockSecretManager in production.

Secure Key Storage: Implements atomic writes and POSIX advisory locking in audit\_keystore.py.

Observability: Emits Prometheus metrics (e.g., sign\_operations\_total, crypto\_errors\_total) and OpenTelemetry traces for all operations.



Testing

The module includes unit and E2E integration tests to ensure reliability:



Unit Tests: Located in test\_audit\_crypto\_\*.py and test\_secrets.py, covering each component.

E2E Tests: Located in test\_e2e\_audit\_crypto.py, validating workflows for key generation, signing, verification, and secret retrieval.

Run tests with:pytest -v --cov=audit\_crypto\_factory --cov=audit\_crypto\_ops --cov=audit\_crypto\_provider --cov=audit\_keystore --cov=secrets --cov-report=term-missing --cov-report=html --asyncio-mode=auto







Limitations and TODOs



Production Key Storage: FileKeyStorageBackend is not suitable for production; use HSM or KMS (noted in audit\_keystore.py).

Key Versioning: Support for versioned keys and revocation policies is partially implemented; full support is needed.

HSM Integration: HSMCryptoProvider requires live HSM for production testing.

Secret Manager Coverage: Additional tests for GCPSecretManager and VaultSecretManager are recommended.

Streaming Support: audit\_crypto\_ops.py supports streaming but requires further optimization for large datasets.



Contributing

This module is closed-source for internal use within The Code Factory. Contributions are managed internally, with changes tracked via audit logs for compliance. For issues or feature requests, contact the internal development team.

License

Proprietary. All rights reserved by The Code Factory.

