# Audit Log Configuration Guide

## Table of Contents

1. [Overview](#overview)
2. [Configuration Methods](#configuration-methods)
3. [Configuration Reference](#configuration-reference)
4. [Security Best Practices](#security-best-practices)
5. [Compliance Configurations](#compliance-configurations)
6. [Migration Guide](#migration-guide)
7. [Troubleshooting](#troubleshooting)
8. [Deployment Examples](#deployment-examples)

## Overview

The Audit Log system provides comprehensive, secure, and compliant audit logging capabilities with support for multiple storage backends, cryptographic providers, and compliance frameworks. Configuration can be managed through YAML files or environment variables, with environment variables taking precedence.

### Key Features

- **Multiple Backends**: File, SQLite, S3, GCS, Azure Blob, HTTP, Kafka, Splunk
- **Cryptographic Providers**: Software-based, HSM (Hardware Security Module)
- **Compliance Support**: SOC2, HIPAA, PCI-DSS, GDPR
- **Advanced Security**: Encryption, tamper detection, PII redaction, RBAC
- **Observability**: Prometheus metrics, OpenTelemetry tracing
- **High Availability**: Retry logic, circuit breakers, batch processing

## Configuration Methods

### Method 1: YAML Configuration Files

Three pre-configured templates are provided:

1. **`audit_config.enhanced.yaml`** - Comprehensive configuration with all options documented
2. **`audit_config.production.yaml`** - Production-hardened security defaults
3. **`audit_config.development.yaml`** - Developer-friendly local testing setup

**Usage:**
```bash
# Copy the appropriate template
cp generator/audit_config.production.yaml generator/audit_config.yaml

# Validate configuration
python generator/audit_log/validate_config.py
```

### Method 2: Environment Variables

Environment variables override YAML configuration. All sensitive values should be set via environment variables or secrets manager.

**Example:**
```bash
export AUDIT_LOG_BACKEND_TYPE=s3
export AUDIT_LOG_ENCRYPTION_KEY="<base64-encoded-key>"
export AUDIT_CRYPTO_PROVIDER_TYPE=software
```

### Method 3: Secrets Manager

For production deployments, use a secrets manager:

- **AWS Secrets Manager**: `SECRET_MANAGER=aws`
- **GCP Secret Manager**: `SECRET_MANAGER=gcp`
- **HashiCorp Vault**: `SECRET_MANAGER=vault`

## Configuration Reference

### Cryptographic Provider Settings

#### `AUDIT_CRYPTO_MODE` (NEW - Security Critical)
- **Type**: String
- **Options**: `"software"`, `"hsm"`, `"dev"`, `"disabled"`
- **Default**: `"software"` (changed from `"disabled"` for security)
- **Environment**: `AUDIT_CRYPTO_MODE`
- **Description**: Controls cryptographic signing mode for audit logs
- **Security Impact**: CRITICAL - Determines audit log integrity guarantees
- **Production**: MUST be `"software"` or `"hsm"` (validated at startup)

**⚠️ CRITICAL SECURITY WARNING:**
- **As of 2026-02-03**: Default changed from `"disabled"` to `"software"` to prevent security breaches
- **Production environments**: Setting `AUDIT_CRYPTO_MODE=disabled` will **BLOCK STARTUP** with a ConfigurationError
- **Audit log integrity**: Disabled mode provides NO cryptographic signatures, violating compliance requirements
- **Regulatory impact**: Disabled crypto violates ISO 27001, SOC 2, NIST SP 800-53, GDPR Article 32

**Mode Details:**
- `"software"` (RECOMMENDED): Uses software-based cryptographic signatures with KMS-encrypted keys
  - Requires: `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` environment variable
  - Provides: Ed25519/RSA/ECDSA signatures for audit log integrity
  - Compliance: Meets most regulatory requirements
  
- `"hsm"` (HIGHEST SECURITY): Uses Hardware Security Module for tamper-resistant signatures
  - Requires: HSM configuration (PKCS#11 library, slot ID, PIN)
  - Provides: Maximum security, FIPS 140-2 compliance
  - Compliance: Required for highest security environments
  
- `"dev"` (DEVELOPMENT ONLY): Uses dummy keys for local development
  - Requires: `AUDIT_LOG_DEV_MODE=true`
  - Security: NO REAL SECURITY - for testing only
  - Production: NEVER use in production
  
- `"disabled"` (DEPRECATED - NOT FOR PRODUCTION): No cryptographic signing
  - **Blocked in production** by automatic validation
  - Security: NO SECURITY - audit logs have no integrity guarantees
  - Use case: Only for initial deployment setup, immediately migrate to `software` or `hsm`
  - Migration: See "Migrating from Disabled Crypto" below

**Migration from Disabled Crypto:**

If you're upgrading from a version with `AUDIT_CRYPTO_MODE=disabled`:

1. **Generate encryption key:**
   ```bash
   python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
   ```

2. **Set environment variables:**
   ```bash
   export AUDIT_CRYPTO_MODE=software
   export AUDIT_CRYPTO_PROVIDER_TYPE=software
   export AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64=<your-generated-key>
   ```

3. **Store key securely** (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault)

4. **Restart application** - startup validation will verify configuration

5. **Verify integrity:**
   ```bash
   python generator/audit_log/validate_config.py --strict
   ```

For existing deployments that need to temporarily bypass validation (NOT RECOMMENDED):
- Set `APP_ENV=development` or `PYTHON_ENV=development` (disables production checks)
- Or set `AUDIT_LOG_DEV_MODE=true` (disables all security)
- **CRITICAL**: This should ONLY be used during migration, not in production

#### `PROVIDER_TYPE`
- **Type**: String
- **Options**: `"software"`, `"hsm"`
- **Default**: `"software"`
- **Environment**: `AUDIT_CRYPTO_PROVIDER_TYPE`
- **Description**: Determines how cryptographic keys are managed
- **Production**: Use `"hsm"` for maximum security
- **Security Impact**: HIGH - HSM provides tamper-resistant key storage

#### `DEFAULT_ALGO`
- **Type**: String
- **Options**: `"rsa"`, `"ecdsa"`, `"ed25519"`, `"hmac"`
- **Default**: `"ed25519"`
- **Environment**: `AUDIT_CRYPTO_DEFAULT_ALGO`
- **Description**: Default signing algorithm for audit entries
- **Recommendation**: `"ed25519"` for best performance and security
- **Security Impact**: HIGH - Affects signature security

#### `KEY_ROTATION_INTERVAL_SECONDS`
- **Type**: Integer
- **Range**: Minimum 86400 (24 hours)
- **Default**: `86400`
- **Environment**: `AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS`
- **Description**: How often to rotate cryptographic keys
- **Production**: 604800 (7 days) recommended
- **Compliance**: Required for SOC2, HIPAA, PCI-DSS
- **Security Impact**: HIGH - Affects key compromise exposure window

#### `SOFTWARE_KEY_DIR`
- **Type**: String
- **Default**: `"audit_keys"`
- **Environment**: `AUDIT_CRYPTO_SOFTWARE_KEY_DIR`
- **Description**: Directory for storing keys (software mode only)
- **Production**: Use absolute path with restricted permissions (0700)
- **Security Impact**: CRITICAL - Compromised directory = compromised keys
- **Warning**: Not suitable for production - use HSM or cloud KMS

#### `KMS_KEY_ID`
- **Type**: String
- **Default**: `"alias/audit-log-key"`
- **Environment**: `AUDIT_CRYPTO_KMS_KEY_ID`
- **Description**: AWS KMS Key ID for encrypting software keys
- **Format**: `"alias/key-name"` or full ARN
- **Security Impact**: CRITICAL - Master encryption key

#### `AWS_REGION`
- **Type**: String
- **Default**: `"us-east-1"`
- **Environment**: `AWS_REGION`
- **Description**: AWS region for KMS operations
- **Production**: Use region closest to your deployment

### Backend Configuration

#### `BACKEND_TYPE`
- **Type**: String
- **Options**: `"file"`, `"sqlite"`, `"s3"`, `"gcs"`, `"azure"`, `"http"`, `"kafka"`, `"splunk"`, `"memory"`
- **Default**: `"file"`
- **Environment**: `AUDIT_LOG_BACKEND_TYPE`
- **Description**: Storage backend for audit logs
- **Production**: Use `"s3"`, `"gcs"`, or `"azure"` for durability
- **Development**: Use `"file"` or `"memory"` for simplicity

#### `BACKEND_PARAMS`
- **Type**: JSON Object
- **Environment**: `AUDIT_LOG_BACKEND_PARAMS` (JSON string)
- **Description**: Backend-specific configuration parameters

**Examples:**
```yaml
# File Backend
BACKEND_PARAMS:
  log_file: "/var/audit/audit.log"

# SQLite Backend
BACKEND_PARAMS:
  db_file: "/var/audit/audit.db"

# S3 Backend
BACKEND_PARAMS:
  bucket: "company-audit-logs"
  prefix: "production/"
  region: "us-east-1"

# Kafka Backend
BACKEND_PARAMS:
  bootstrap_servers: "kafka-1:9092,kafka-2:9092"
  topic: "audit-logs"
```

### Compression Settings

#### `COMPRESSION_ALGO`
- **Type**: String
- **Options**: `"none"`, `"gzip"`, `"zstd"`
- **Default**: `"zstd"`
- **Environment**: `AUDIT_COMPRESSION_ALGO`
- **Description**: Compression algorithm for log entries
- **Recommendation**: `"zstd"` for best ratio and performance
- **Performance Impact**: MEDIUM - CPU vs storage tradeoff

#### `COMPRESSION_LEVEL`
- **Type**: Integer
- **Range**: zstd (1-22), gzip (1-9)
- **Default**: `3`
- **Environment**: `AUDIT_COMPRESSION_LEVEL`
- **Description**: Compression level (algorithm-dependent)
- **Production**: 3 (zstd) or 6 (gzip) for balanced performance

### Batch Processing

#### `BATCH_FLUSH_INTERVAL`
- **Type**: Integer
- **Range**: 1-60 seconds
- **Default**: `10`
- **Environment**: `AUDIT_BATCH_FLUSH_INTERVAL`
- **Description**: How often to flush buffered entries to storage
- **Production**: 5-10 seconds recommended
- **Compliance Impact**: Affects RPO (Recovery Point Objective)

#### `BATCH_MAX_SIZE`
- **Type**: Integer
- **Range**: 1-1000
- **Default**: `100`
- **Environment**: `AUDIT_BATCH_MAX_SIZE`
- **Description**: Maximum batch size before forced flush
- **Production**: 100-500 recommended
- **Memory Impact**: Higher values = more memory usage

### Retry and Fault Tolerance

#### `RETRY_MAX_ATTEMPTS`
- **Type**: Integer
- **Range**: 0-10
- **Default**: `3`
- **Environment**: `AUDIT_RETRY_MAX_ATTEMPTS`
- **Description**: Maximum retry attempts for failed operations
- **Production**: 3-5 recommended
- **Reliability Impact**: HIGH - Affects data durability

#### `RETRY_BACKOFF_FACTOR`
- **Type**: Float
- **Range**: 0.1-5.0
- **Default**: `0.5`
- **Environment**: `AUDIT_RETRY_BACKOFF_FACTOR`
- **Description**: Exponential backoff multiplier for retries
- **Formula**: `delay = backoff_factor * (2 ^ retry_number)`
- **Production**: 0.5-1.0 recommended

### Tamper Detection

#### `TAMPER_DETECTION_ENABLED`
- **Type**: Boolean
- **Default**: `true`
- **Environment**: `AUDIT_TAMPER_DETECTION_ENABLED`
- **Description**: Enable cryptographic chaining for tamper detection
- **Production**: MUST be enabled
- **Compliance**: REQUIRED for SOC2, HIPAA, PCI-DSS
- **Security Impact**: CRITICAL - Detects log tampering

### Health Checks

#### `HEALTH_CHECK_INTERVAL`
- **Type**: Integer
- **Range**: 10-300 seconds
- **Default**: `30`
- **Environment**: `AUDIT_HEALTH_CHECK_INTERVAL`
- **Description**: How often to verify backend health
- **Production**: 30-60 seconds recommended

### API Ports

#### `METRICS_PORT`
- **Type**: Integer
- **Default**: `8002`
- **Environment**: `AUDIT_LOG_METRICS_PORT`
- **Description**: Prometheus metrics server port

#### `API_PORT`
- **Type**: Integer
- **Default**: `8003`
- **Environment**: `AUDIT_LOG_API_PORT`
- **Description**: FastAPI REST API port

#### `GRPC_PORT`
- **Type**: Integer
- **Default**: `50051`
- **Environment**: `AUDIT_LOG_GRPC_PORT`
- **Description**: gRPC service port

### Encryption

#### `ENCRYPTION_ENABLED`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Enable encryption of log entries at rest
- **Production**: MUST be enabled
- **Compliance**: REQUIRED
- **Security Impact**: CRITICAL

#### `ENCRYPTION_KEY`
- **Type**: String (base64-encoded)
- **Environment**: `AUDIT_LOG_ENCRYPTION_KEY` (REQUIRED in production)
- **Description**: Fernet encryption key for log entries
- **Security**: NEVER hardcode - use environment variable or secrets manager
- **Generation**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- **Security Impact**: CRITICAL - Compromised key = compromised logs

### RBAC (Role-Based Access Control)

#### `RBAC_ENABLED`
- **Type**: Boolean
- **Default**: `true`
- **Environment**: `AUDIT_RBAC_ENABLED`
- **Description**: Enable role-based access control
- **Production**: MUST be enabled
- **Security Impact**: HIGH - Controls access to audit logs

#### `USERS_CONFIG_PATH`
- **Type**: String
- **Environment**: `AUDIT_LOG_USERS_CONFIG`
- **Description**: Path to users/roles configuration file
- **Format**: JSON or YAML
- **Security**: Restrict file permissions to 0600

### Observability

#### `TRACING_ENABLED`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Enable OpenTelemetry distributed tracing
- **Performance Impact**: LOW-MEDIUM (depends on sample rate)

#### `TRACING_SAMPLE_RATE`
- **Type**: Float
- **Range**: 0.0-1.0
- **Default**: `0.1`
- **Description**: Percentage of requests to trace
- **Production**: 0.05-0.1 recommended (5-10%)
- **Development**: 1.0 for full visibility

### Immutability

#### `IMMUTABLE`
- **Type**: Boolean
- **Default**: `true`
- **Environment**: `AUDIT_LOG_IMMUTABLE`
- **Description**: Prevent deletion/modification of logs
- **Production**: MUST be enabled
- **Compliance**: REQUIRED for all frameworks
- **Security Impact**: CRITICAL

### PII Redaction

#### `PII_REDACTION_ENABLED`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Automatically redact PII in logs
- **Compliance**: REQUIRED for GDPR, HIPAA
- **Privacy Impact**: HIGH

#### `PII_ENTITIES`
- **Type**: List of Strings
- **Options**: `"EMAIL_ADDRESS"`, `"PHONE_NUMBER"`, `"CREDIT_CARD"`, `"SSN"`, `"IP_ADDRESS"`, etc.
- **Description**: Types of PII to redact
- **Dependencies**: Requires Microsoft Presidio

### Development Mode

#### `DEV_MODE`
- **Type**: Boolean
- **Default**: `false`
- **Environment**: `AUDIT_LOG_DEV_MODE`
- **Description**: Enable development mode with relaxed security
- **Production**: MUST be false
- **Security Impact**: CRITICAL - Disables security features
- **Warning**: NEVER use in production

#### `CRYPTO_ALLOW_INIT_FAILURE`
- **Type**: Boolean
- **Default**: `false`
- **Environment**: `AUDIT_CRYPTO_ALLOW_INIT_FAILURE`
- **Description**: Allow startup without full crypto configuration
- **Production**: MUST be false
- **Use Case**: Development/testing only

#### `CRYPTO_ALLOW_DUMMY_PROVIDER`
- **Type**: Boolean
- **Default**: `false`
- **Environment**: `AUDIT_CRYPTO_ALLOW_DUMMY_PROVIDER`
- **Description**: Allow dummy crypto provider for testing
- **Production**: MUST be false
- **Security Impact**: CRITICAL

### Secret Management

#### `SECRET_MANAGER`
- **Type**: String
- **Options**: `"aws"`, `"gcp"`, `"vault"`, `"env"`, `"mock"`
- **Default**: `"env"`
- **Environment**: `SECRET_MANAGER`
- **Description**: Secret manager type for retrieving sensitive values
- **Production**: Use `"aws"`, `"gcp"`, or `"vault"` - NEVER `"mock"`
- **Security Impact**: CRITICAL

### Compliance

#### `COMPLIANCE_MODE`
- **Type**: String
- **Options**: `"soc2"`, `"hipaa"`, `"pci-dss"`, `"gdpr"`, `"standard"`
- **Default**: `"standard"`
- **Description**: Compliance framework enforcement
- **Impact**: Enforces framework-specific requirements

#### `DATA_RETENTION_DAYS`
- **Type**: Integer
- **Default**: `365`
- **Description**: How long to retain audit logs
- **Requirements**:
  - SOC2: 365 days minimum
  - HIPAA: 2555 days (7 years) minimum
  - PCI-DSS: 365 days minimum
  - GDPR: Per Data Processing Agreement
- **Compliance Impact**: CRITICAL

## Security Best Practices

### Key Management

1. **Never hardcode keys** in configuration files
2. **Use secrets manager** (AWS Secrets Manager, GCP Secret Manager, Vault)
3. **Rotate keys regularly** (weekly minimum for production)
4. **Use HSM** for production deployments when possible
5. **Restrict key file permissions** to 0600 (software mode)

### Encryption

1. **Always enable encryption** in production (`ENCRYPTION_ENABLED: true`)
2. **Use strong encryption keys** (Fernet with proper entropy)
3. **Protect encryption keys** with KMS or HSM
4. **Enable tamper detection** (`TAMPER_DETECTION_ENABLED: true`)

### Access Control

1. **Enable RBAC** in production (`RBAC_ENABLED: true`)
2. **Use principle of least privilege** for user roles
3. **Protect users config file** with restrictive permissions
4. **Audit access to audit logs** (meta-auditing)

### Network Security

1. **Use TLS/SSL** for all network communication
2. **Restrict port access** with firewalls
3. **Use non-default ports** in production when possible
4. **Enable authentication** on all endpoints

### Monitoring

1. **Enable metrics** for observability
2. **Configure alerting** for critical events
3. **Monitor key rotation** status
4. **Track failed access attempts**

## Compliance Configurations

### SOC2 Compliance

```yaml
COMPLIANCE_MODE: "soc2"
DATA_RETENTION_DAYS: 365
ENCRYPTION_ENABLED: true
IMMUTABLE: true
TAMPER_DETECTION_ENABLED: true
RBAC_ENABLED: true
KEY_ROTATION_INTERVAL_SECONDS: 604800  # 7 days
ALERT_MIN_SEVERITY: "error"
```

**Additional Requirements:**
- Regular security audits
- Access logging and monitoring
- Incident response procedures
- Change management documentation

### HIPAA Compliance

```yaml
COMPLIANCE_MODE: "hipaa"
DATA_RETENTION_DAYS: 2555  # 7 years
ENCRYPTION_ENABLED: true
IMMUTABLE: true
TAMPER_DETECTION_ENABLED: true
RBAC_ENABLED: true
PII_REDACTION_ENABLED: true
PII_ENTITIES:
  - "EMAIL_ADDRESS"
  - "PHONE_NUMBER"
  - "SSN"
  - "PERSON"
  - "LOCATION"
KEY_ROTATION_INTERVAL_SECONDS: 604800
```

**Additional Requirements:**
- Business Associate Agreements (BAA)
- Risk assessments
- Breach notification procedures
- PHI encryption at rest and in transit

### PCI-DSS Compliance

```yaml
COMPLIANCE_MODE: "pci-dss"
DATA_RETENTION_DAYS: 365
ENCRYPTION_ENABLED: true
IMMUTABLE: true
TAMPER_DETECTION_ENABLED: true
RBAC_ENABLED: true
PII_REDACTION_ENABLED: true
PII_ENTITIES:
  - "CREDIT_CARD"
  - "CVV"
KEY_ROTATION_INTERVAL_SECONDS: 604800
```

**Additional Requirements:**
- Quarterly vulnerability scans
- Annual penetration testing
- Cardholder data environment (CDE) isolation
- Two-factor authentication

### GDPR Compliance

```yaml
COMPLIANCE_MODE: "gdpr"
DATA_RETENTION_DAYS: 730  # Per DPA requirements
ENCRYPTION_ENABLED: true
IMMUTABLE: true
PII_REDACTION_ENABLED: true
PII_ENTITIES:
  - "EMAIL_ADDRESS"
  - "PHONE_NUMBER"
  - "IP_ADDRESS"
  - "PERSON"
  - "LOCATION"
```

**Additional Requirements:**
- Data Processing Agreement (DPA)
- Right to erasure (GDPR Article 17)
- Data portability
- Privacy by design
- DPIA for high-risk processing

## Migration Guide

### CRITICAL: Migrating from "disabled" to "software" Audit Crypto Mode (2026-02-03)

**Context**: The default `AUDIT_CRYPTO_MODE` has been changed from `"disabled"` to `"software"` to enforce cryptographic signing of audit logs and prevent security breaches.

**Impact**: Existing deployments with `AUDIT_CRYPTO_MODE=disabled` will fail to start in production environments.

**Migration Steps**:

1. **Generate a secure encryption key:**
   ```bash
   # Generate a 256-bit Fernet key
   python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
   # Example output: 3q2+7w==5t6y7u8i9o0p1a2s3d4f5g6h7j8k9l0z1x2c3v4b5n6m7,8.9/0
   ```

2. **Store the key in your secrets manager:**
   ```bash
   # AWS Secrets Manager
   aws secretsmanager create-secret \
     --name audit-crypto-master-key \
     --secret-string "$(python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')"
   
   # Google Cloud Secret Manager
   echo -n "$(python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')" | \
     gcloud secrets create audit-crypto-master-key --data-file=-
   
   # HashiCorp Vault
   vault kv put secret/audit-crypto-master-key \
     value="$(python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')"
   ```

3. **Set the environment variable:**
   ```bash
   # In Railway/Heroku/Cloud Run:
   export AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64="<your-generated-key>"
   
   # In Kubernetes:
   kubectl create secret generic audit-crypto-key \
     --from-literal=master-key="<your-generated-key>"
   
   # In .env file (development only - DO NOT commit to version control):
   AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64=<your-generated-key>
   ```

4. **Update AUDIT_CRYPTO_MODE:**
   ```bash
   # Set in your deployment environment
   export AUDIT_CRYPTO_MODE=software
   
   # Or in .env file:
   AUDIT_CRYPTO_MODE=software
   ```

5. **Set AUDIT_CRYPTO_ALLOW_INIT_FAILURE to 0 (production):**
   ```bash
   # In production, ensure crypto initialization is required
   export AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0
   ```

6. **Restart the application:**
   - Verify startup logs show: `Cryptographic configuration validated successfully`
   - Ensure no `CRITICAL SECURITY ERROR: AUDIT_CRYPTO_MODE=disabled` messages
   - Check audit logs for valid hash chains (no `HASH_CHAIN_BROKEN` errors)

7. **Verify audit log integrity:**
   ```bash
   # Check audit log has cryptographic signatures
   tail -n 10 audit_trail.log | grep -E "signature|hash_chain"
   ```

**Rollback Plan** (emergency only):
```bash
# Temporarily allow disabled mode in non-production only
export AUDIT_CRYPTO_MODE=dev
export AUDIT_LOG_DEV_MODE=true
export AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1
# WARNING: This is a security risk - only use for emergency recovery
```

**Production Validation Checklist**:
- [ ] Encryption key generated and stored securely
- [ ] `AUDIT_CRYPTO_MODE=software` set in environment
- [ ] `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` configured
- [ ] `AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0` in production
- [ ] Startup logs show successful crypto validation
- [ ] Audit logs contain cryptographic signatures
- [ ] No `HASH_CHAIN_BROKEN` errors in logs

### From Environment Variables to Config Files

1. **Inventory current environment variables:**
```bash
env | grep AUDIT_ > current_audit_config.txt
```

2. **Map to YAML configuration:**
```bash
# Use the provided mapping in audit_config.enhanced.yaml
# Section: "ENVIRONMENT VARIABLE MAPPING"
```

3. **Create new config file:**
```bash
cp generator/audit_config.production.yaml generator/audit_config.yaml
# Edit audit_config.yaml with your settings
```

4. **Validate configuration:**
```bash
python generator/audit_log/validate_config.py
```

5. **Test in staging:**
```bash
# Deploy to staging environment
# Run integration tests
# Monitor for issues
```

6. **Gradual rollout:**
```bash
# Deploy to production canary
# Monitor metrics and logs
# Expand to full production
```

### From File Backend to Cloud Storage

1. **Set up cloud storage:**
```bash
# AWS S3 Example
aws s3 mb s3://company-audit-logs
aws s3api put-bucket-versioning \
  --bucket company-audit-logs \
  --versioning-configuration Status=Enabled
```

2. **Update configuration:**
```yaml
BACKEND_TYPE: "s3"
BACKEND_PARAMS:
  bucket: "company-audit-logs"
  prefix: "production/"
  region: "us-east-1"
```

3. **Migrate existing logs:**
```bash
# Copy existing file-based logs to S3
aws s3 sync /path/to/audit_logs/ s3://company-audit-logs/migration/
```

4. **Switch over:**
```bash
# Deploy new configuration
# Verify logs are being written to S3
# Monitor for issues
```

### From Software Keys to HSM

1. **Set up HSM:**
```bash
# Initialize HSM
# Configure PKCS#11 library
# Test connectivity
```

2. **Update configuration:**
```yaml
PROVIDER_TYPE: "hsm"
HSM_LIBRARY_PATH: "/usr/lib/softhsm/libsofthsm2.so"
HSM_SLOT_ID: 0
# HSM_PIN set via environment variable
```

3. **Generate new keys in HSM:**
```bash
# Keys are generated automatically on first use
# Verify key generation in HSM logs
```

4. **Gradual rollout:**
```bash
# Deploy to test environment
# Verify signing/verification
# Deploy to production
```

## Troubleshooting

### Common Configuration Errors

#### Error: "AUDIT_LOG_ENCRYPTION_KEY environment variable not set"

**Cause:** Encryption key not provided in production mode

**Solution:**
```bash
# Generate a new Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set environment variable
export AUDIT_LOG_ENCRYPTION_KEY="<generated-key>"
```

#### Error: "KEY_ROTATION_INTERVAL_SECONDS must be >= 86400"

**Cause:** Key rotation interval too short

**Solution:**
```yaml
# Set to minimum 24 hours (86400 seconds)
KEY_ROTATION_INTERVAL_SECONDS: 86400
```

#### Error: "Invalid BACKEND_TYPE"

**Cause:** Unsupported backend type specified

**Solution:**
```yaml
# Use one of the supported backends
BACKEND_TYPE: "s3"  # or file, sqlite, gcs, azure, http, kafka, splunk, memory
```

#### Warning: "Using 'software' crypto provider"

**Cause:** Software-based keys in production

**Recommendation:**
- Migrate to HSM for production
- Or use cloud KMS (AWS KMS, GCP Cloud KMS)
- Ensure software key directory has restricted permissions (0700)

### Configuration Validation

Run the validation script to catch errors:

```bash
# Validate YAML config
python generator/audit_log/validate_config.py --config generator/audit_config.yaml

# Validate environment variables
python generator/audit_log/validate_config.py --env

# Strict mode (warnings = errors)
python generator/audit_log/validate_config.py --strict
```

### Debug Mode

Enable debug logging for troubleshooting:

```yaml
LOG_LEVEL: "DEBUG"
CRYPTO_DEBUG: true
```

```bash
# Or via environment
export LOG_LEVEL=DEBUG
export AUDIT_CRYPTO_DEBUG=1
```

### Performance Issues

#### High CPU usage

**Check:**
- Compression level too high
- Crypto worker threads too low
- Batch size too small

**Solutions:**
```yaml
COMPRESSION_LEVEL: 3  # Lower from high values
CRYPTO_WORKER_THREADS: 8  # Increase for more parallelism
BATCH_MAX_SIZE: 500  # Increase to reduce operations
```

#### High memory usage

**Check:**
- Batch size too large
- Async queue size too large
- Too many concurrent operations

**Solutions:**
```yaml
BATCH_MAX_SIZE: 100  # Decrease from high values
ASYNC_QUEUE_SIZE: 1000  # Decrease if needed
```

#### Backend write failures

**Check:**
- Network connectivity
- Backend service health
- Retry configuration

**Solutions:**
```yaml
RETRY_MAX_ATTEMPTS: 5  # Increase retries
RETRY_BACKOFF_FACTOR: 1.0  # Increase backoff
HEALTH_CHECK_INTERVAL: 30  # More frequent health checks
```

## Deployment Examples

### Docker Compose

```yaml
version: '3.8'

services:
  audit-service:
    image: your-company/code-factory:latest
    environment:
      # Core settings
      AUDIT_LOG_BACKEND_TYPE: "s3"
      AUDIT_LOG_ENCRYPTION_KEY: "${AUDIT_ENCRYPTION_KEY}"
      AUDIT_LOG_IMMUTABLE: "true"
      
      # Crypto settings
      AUDIT_CRYPTO_PROVIDER_TYPE: "software"
      AUDIT_CRYPTO_MODE: "full"
      
      # Backend settings
      AUDIT_BATCH_FLUSH_INTERVAL: "5"
      AUDIT_BATCH_MAX_SIZE: "500"
      
      # AWS credentials for S3
      AWS_ACCESS_KEY_ID: "${AWS_ACCESS_KEY_ID}"
      AWS_SECRET_ACCESS_KEY: "${AWS_SECRET_ACCESS_KEY}"
      AWS_REGION: "us-east-1"
    ports:
      - "8002:8002"  # Metrics
      - "8003:8003"  # API
      - "50051:50051"  # gRPC
    volumes:
      - ./audit_keys:/var/audit/keys:ro
      - ./audit_config.production.yaml:/app/generator/audit_config.yaml:ro
```

### Kubernetes ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: audit-config
  namespace: production
data:
  audit_config.yaml: |
    PROVIDER_TYPE: "software"
    DEFAULT_ALGO: "ed25519"
    KEY_ROTATION_INTERVAL_SECONDS: 604800
    BACKEND_TYPE: "s3"
    COMPRESSION_ALGO: "zstd"
    COMPRESSION_LEVEL: 3
    BATCH_FLUSH_INTERVAL: 5
    BATCH_MAX_SIZE: 500
    TAMPER_DETECTION_ENABLED: true
    IMMUTABLE: true
    RBAC_ENABLED: true
    COMPLIANCE_MODE: "soc2"
    DATA_RETENTION_DAYS: 365
```

### Kubernetes Secret

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: audit-secrets
  namespace: production
type: Opaque
stringData:
  AUDIT_LOG_ENCRYPTION_KEY: "<base64-encoded-fernet-key>"
  AWS_ACCESS_KEY_ID: "<aws-access-key>"
  AWS_SECRET_ACCESS_KEY: "<aws-secret-key>"
```

### Railway Configuration

Create `railway.json`:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

Set environment variables in Railway dashboard:

```bash
AUDIT_LOG_BACKEND_TYPE=s3
AUDIT_LOG_ENCRYPTION_KEY=<from-railway-secrets>
AUDIT_CRYPTO_PROVIDER_TYPE=software
AUDIT_CRYPTO_MODE=full
AWS_REGION=us-east-1
# ... additional variables
```

## Additional Resources

- **Architecture Documentation**: `generator/audit_log/docs/ARCHITECTURE.md`
- **Patent Documentation**: `generator/audit_log/docs/patent_doc.md`
- **Module README**: `generator/audit_log/README.md`
- **Environment Variables Guide**: `docs/ENVIRONMENT_VARIABLES.md`
- **Security Deployment Guide**: `docs/SECURITY_DEPLOYMENT_GUIDE.md`

## Support and Contact

For issues or questions about audit log configuration:

1. Check this documentation first
2. Run the validation script: `python generator/audit_log/validate_config.py`
3. Review logs with `LOG_LEVEL=DEBUG`
4. Contact internal development team for assistance

## Version History

- **v1.0**: Initial comprehensive configuration documentation
- Current as of: February 2026
