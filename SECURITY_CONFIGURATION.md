# Security Configuration Guide

This document explains the security-related environment variables and warnings
produced by The Code Factory platform at startup.

---

## Security Posture Warnings

At startup the platform logs a **Security Posture Summary** when optional
security features are not configured.  These warnings are acceptable in
development and staging environments but should be resolved before going to
production.

To silence these warnings during local development set:

```
SUPPRESS_SECURITY_WARNINGS=1
```

---

## Encryption Mode

Controls the backend used for encrypting data at rest.

| Variable | Default | Description |
|---|---|---|
| `ENCRYPTION_MODE` | `local` | `local`, `aws_kms`, or `azure_keyvault` |

### `local` (default)
Software-based AES encryption using a locally stored key.  Suitable for
development and single-node deployments.  The key is derived from
`ENCRYPTION_KEY` (Fernet format).

### `aws_kms`
Uses AWS Key Management Service for envelope encryption.  Requires:
- `AWS_REGION` – AWS region (e.g. `us-east-1`)
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or an IAM role)
- A KMS key ARN configured in the application

Warning logged when not configured:
```
⚠ AWS KMS not configured (AWS_REGION not set) - using local encryption
```

### `azure_keyvault`
Uses Azure Key Vault for envelope encryption.  Requires:
- `AZURE_KEYVAULT_URL`
- `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`

---

## Plugin Integrity Checks

The platform can verify the cryptographic hash of loaded plugins against a
manifest to detect tampering.

| Variable | Default | Description |
|---|---|---|
| `PLUGIN_INTEGRITY_CHECK_ENABLED` | `false` (dev) / `true` (prod) | Enable plugin hash verification |
| `HASH_MANIFEST` | *(unset)* | Path to the JSON hash manifest file |

Warning logged when not configured:
```
⚠ Plugin integrity checks disabled (HASH_MANIFEST not set)
```

To enable, generate a manifest and set:

```
HASH_MANIFEST=/app/plugin_hashes.json
PLUGIN_INTEGRITY_CHECK_ENABLED=true
```

---

## Audit Crypto Mode

Controls cryptographic signing of audit log entries.

| Variable | Default | Description |
|---|---|---|
| `AUDIT_CRYPTO_MODE` | `software` | `software`, `hsm`, `dev`, or `disabled` |

- **`software`** – Recommended for production. Requires `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64`.
- **`hsm`** – Hardware Security Module (highest security).
- **`dev`** – Dummy keys; development/testing only.
- **`disabled`** – No signing; blocked in production.

Warning logged when in dev mode:
```
⚠ Audit crypto in DEV mode - not suitable for production
```

---

## Sentry Error Tracking

| Variable | Default | Description |
|---|---|---|
| `SENTRY_DSN` | *(unset)* | Sentry DSN URL to enable error tracking |
| `SENTRY_ENVIRONMENT` | value of `APP_ENV` | Sentry environment tag |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Fraction of transactions to trace (0.0–1.0) |

Warning logged in production when not configured:
```
⚠ SENTRY NOT CONFIGURED IN PRODUCTION
```

Get your DSN from [sentry.io](https://sentry.io/settings/) → Projects → Keys.

---

## Summary: Recommended Production Environment Variables

```dotenv
# Encryption
ENCRYPTION_MODE=aws_kms
AWS_REGION=us-east-1

# Plugin integrity
PLUGIN_INTEGRITY_CHECK_ENABLED=true
HASH_MANIFEST=/app/plugin_hashes.json

# Audit crypto
AUDIT_CRYPTO_MODE=software
AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64=<base64-key>

# Sentry
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
SENTRY_ENVIRONMENT=production
```
