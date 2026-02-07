<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Audit Configuration System - Quick Start

## Overview

This directory contains comprehensive audit logging configuration for The Code Factory platform. The system provides production-ready audit logging with cryptographic signing, tamper detection, and compliance support.

## Configuration Files

### Core Configuration Files

1. **`audit_config.yaml`** (Current/Active Configuration)
   - The active configuration file used by the application
   - Copy from one of the templates below to get started
   - **Do not commit** if it contains sensitive values

2. **`audit_config.enhanced.yaml`** (Complete Reference)
   - Comprehensive configuration with ALL available options
   - Detailed inline documentation for each setting
   - Use as reference when customizing your configuration
   - Categories: Crypto, Backend, Performance, Security, Compliance

3. **`audit_config.production.yaml`** (Production Template)
   - Production-hardened defaults
   - Security-first configuration
   - Compliance-ready settings (SOC2, HIPAA, PCI-DSS, GDPR)
   - Use this as starting point for production deployments

4. **`audit_config.development.yaml`** (Development Template)
   - Developer-friendly defaults
   - Relaxed security for local testing
   - Fast startup with minimal overhead
   - Use for local development only

## Quick Start

### For Development

```bash
# Copy development config
cp audit_config.development.yaml audit_config.yaml

# Set required environment variables
export AUDIT_LOG_DEV_MODE=true
export AUDIT_CRYPTO_MODE=dev

# Validate configuration
python audit_log/validate_config.py

# Start your application
python ../server/main.py
```

### For Production

```bash
# Copy production config
cp audit_config.production.yaml audit_config.yaml

# Edit configuration with your specific values
vim audit_config.yaml

# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set environment variables (REQUIRED)
export AUDIT_LOG_ENCRYPTION_KEY="<generated-key>"
export AUDIT_CRYPTO_MODE=full
export AUDIT_LOG_DEV_MODE=false

# Validate configuration (strict mode)
python audit_log/validate_config.py --strict

# Deploy application
```

## Configuration Validation

Always validate your configuration before deployment:

```bash
# Validate current config
make audit-config-validate

# Validate production config
make audit-config-validate-prod

# Validate development config
make audit-config-validate-dev

# Validate environment variables
make audit-config-validate-env

# Strict mode (warnings = errors)
make audit-config-validate-strict
```

## Key Configuration Areas

### 1. Cryptographic Provider

Controls how signing keys are managed:

```yaml
PROVIDER_TYPE: "software"    # or "hsm" for Hardware Security Module
DEFAULT_ALGO: "ed25519"      # Signing algorithm
KEY_ROTATION_INTERVAL_SECONDS: 604800  # 7 days
```

**Production Recommendation**: Use HSM or cloud KMS

### 2. Storage Backend

Where audit logs are stored:

```yaml
BACKEND_TYPE: "s3"           # Options: file, sqlite, s3, gcs, azure, kafka
BACKEND_PARAMS:
  bucket: "company-audit-logs"
  prefix: "production/"
  region: "us-east-1"
```

**Production Recommendation**: Use cloud storage (S3, GCS, Azure)

### 3. Security Settings

Critical security configurations:

```yaml
ENCRYPTION_ENABLED: true     # MUST be true in production
IMMUTABLE: true              # MUST be true in production
TAMPER_DETECTION_ENABLED: true  # MUST be true for compliance
RBAC_ENABLED: true           # MUST be true in production
```

### 4. Performance Tuning

Optimize for your workload:

```yaml
COMPRESSION_ALGO: "zstd"     # Reduce storage costs
BATCH_FLUSH_INTERVAL: 5      # Balance latency vs throughput
BATCH_MAX_SIZE: 500          # Batch size for writes
RETRY_MAX_ATTEMPTS: 5        # Fault tolerance
```

### 5. Compliance

Configure for your compliance requirements:

```yaml
COMPLIANCE_MODE: "soc2"      # Options: soc2, hipaa, pci-dss, gdpr
DATA_RETENTION_DAYS: 365     # Minimum retention period
PII_REDACTION_ENABLED: true  # Automatically redact PII
```

## Environment Variables

Environment variables override YAML configuration. See `.env.production.template` for complete list.

**Critical Variables** (Required in Production):
```bash
AUDIT_LOG_ENCRYPTION_KEY="<base64-fernet-key>"
AUDIT_CRYPTO_MODE=full
AUDIT_LOG_DEV_MODE=false
```

## Deployment Checklist

Before deploying to production:

- [ ] Copy `audit_config.production.yaml` to `audit_config.yaml`
- [ ] Generate and secure encryption key
- [ ] Configure cloud storage backend
- [ ] Set up secret manager (AWS, GCP, Vault)
- [ ] Enable all security features
- [ ] Configure appropriate retention period
- [ ] Set up monitoring and alerting
- [ ] Validate configuration in strict mode
- [ ] Test in staging environment
- [ ] Document emergency procedures

## Documentation

Comprehensive documentation available:

- **Complete Configuration Reference**: `../../docs/AUDIT_CONFIGURATION.md`
- **Module Architecture**: `docs/ARCHITECTURE.md`
- **API Documentation**: `README.md`

## Troubleshooting

### Common Issues

**Error: "AUDIT_LOG_ENCRYPTION_KEY environment variable not set"**
```bash
# Generate key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set environment variable
export AUDIT_LOG_ENCRYPTION_KEY="<generated-key>"
```

**Error: "Invalid BACKEND_TYPE"**
```yaml
# Use one of the supported backends
BACKEND_TYPE: "s3"  # file, sqlite, s3, gcs, azure, http, kafka, splunk, memory
```

**Warning: "Using 'software' crypto provider"**
- Acceptable for development and small deployments
- For production, consider migrating to HSM
- Ensure key directory has restricted permissions (0700)

### Get Help

1. Run validation script: `python audit_log/validate_config.py`
2. Check documentation: `docs/AUDIT_CONFIGURATION.md`
3. Review logs with `LOG_LEVEL=DEBUG`
4. Contact internal development team

## Security Best Practices

1. **Never commit secrets** to version control
2. **Use secrets manager** in production (AWS, GCP, Vault)
3. **Rotate keys regularly** (weekly minimum)
4. **Enable all security features** in production
5. **Validate configuration** before deployment
6. **Monitor audit logs** for tampering
7. **Test disaster recovery** procedures
8. **Document security procedures**

## Integration

The audit logging system integrates with:

- **FastAPI REST API**: Port 8003 (configurable)
- **gRPC Service**: Port 50051 (configurable)
- **Prometheus Metrics**: Port 8002 (configurable)
- **OpenTelemetry Tracing**: Distributed tracing support
- **Cloud Storage**: S3, GCS, Azure Blob
- **Streaming**: Kafka, Splunk, HTTP webhooks

## Version Information

- Configuration Schema Version: 1.0
- Last Updated: February 2026
- Compatible with: The Code Factory Platform v1.0+

## Support

For questions or issues:
- Review `docs/AUDIT_CONFIGURATION.md`
- Run `make audit-config-validate`
- Check module README: `README.md`
- Contact: Internal development team
