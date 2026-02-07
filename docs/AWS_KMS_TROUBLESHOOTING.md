<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# AWS KMS Troubleshooting Guide

## Overview

This guide helps you troubleshoot AWS KMS-related issues in the audit crypto system. The application uses AWS KMS to encrypt and decrypt the master key used for audit log cryptographic operations.

## Common Issues

### InvalidCiphertextException Error

**Error Message:**
```
botocore.errorfactory.InvalidCiphertextException: An error occurred (InvalidCiphertextException) when calling the Decrypt operation
```

**Symptoms:**
- Application logs show "Failed to initialize software key master in production"
- System falls back to `DummyCryptoProvider` with security warnings
- Audit log integrity is compromised

#### Root Causes

1. **KMS Key Rotation**: The KMS key used to encrypt the master key was rotated
2. **Environment Migration**: Encrypted data was copied from a different AWS account or region
3. **Wrong KMS Key Configuration**: The `KMS_KEY_ID` environment variable points to a different key than the one used for encryption
4. **Wrong AWS Region**: The `AWS_DEFAULT_REGION` or `AWS_REGION` is set to a different region than where the KMS key exists

#### Resolution Steps

##### Option 1: Update the Encrypted Master Key (Recommended)

1. **Generate a new master key**:
   ```python
   from cryptography.fernet import Fernet
   import base64
   
   # Generate a new 32-byte Fernet key
   new_master_key = Fernet.generate_key()
   print(f"New master key (base64): {base64.b64encode(new_master_key).decode()}")
   ```

2. **Encrypt the new key with your current KMS key**:
   ```bash
   # Using AWS CLI
   aws kms encrypt \
     --key-id "alias/your-kms-key-alias" \
     --plaintext fileb://<(echo -n "your-base64-master-key" | base64 -d) \
     --query CiphertextBlob \
     --output text
   ```

3. **Update the secret in your secret manager**:
   
   **AWS Secrets Manager:**
   ```bash
   aws secretsmanager update-secret \
     --secret-id AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 \
     --secret-string "base64-encoded-ciphertext"
   ```
   
   **Environment Variable (if using EnvVarSecretManager):**
   ```bash
   export AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64="base64-encoded-ciphertext"
   ```

4. **Restart the application** to pick up the new encrypted key

##### Option 2: Verify AWS Configuration

1. **Check AWS credentials are valid**:
   ```bash
   aws sts get-caller-identity
   ```

2. **Verify KMS key ID is correct**:
   ```bash
   # Check your KMS_KEY_ID environment variable
   echo $KMS_KEY_ID
   
   # List available KMS keys
   aws kms list-keys
   
   # Describe a specific key
   aws kms describe-key --key-id "your-key-id"
   ```

3. **Verify AWS region matches**:
   ```bash
   echo $AWS_DEFAULT_REGION
   echo $AWS_REGION
   ```

##### Option 3: Clear and Regenerate (Data Loss Warning)

⚠️ **WARNING**: This will invalidate all existing encrypted audit data!

1. **Clear the invalid encrypted key from your secret manager**:
   ```bash
   # AWS Secrets Manager
   aws secretsmanager delete-secret \
     --secret-id AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 \
     --force-delete-without-recovery
   ```

2. **Allow the application to generate a new key** by setting:
   ```bash
   # This will cause initialization to fail, forcing manual intervention
   # DO NOT use AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1 in production
   ```

3. **Follow Option 1** to generate and encrypt a new master key

### Excessive Logging / Railway Rate Limiting

**Symptoms:**
```
Railway rate limit of 500 logs/sec reached for replica
Messages dropped: 514, 380
```

**Root Cause:**
- The same error is being logged repeatedly without rate limiting
- Each retry attempt logs the full error with traceback

**Resolution:**

The codebase now includes rate limiting for critical error messages. Each unique error type will only be logged once per 60 seconds, preventing log flooding.

**Configuration:**
- Rate limiting is automatic and cannot be disabled
- The default interval is 60 seconds
- This applies to all SECURITY CRITICAL and initialization error messages

**Verify Rate Limiting is Working:**
```bash
# Count occurrences of specific error in last minute
journalctl -u your-service --since "1 minute ago" | grep "InvalidCiphertextException" | wc -l
```

You should see at most 1-2 log entries per minute for the same error.

## Environment Variables

### Required for KMS Operations

| Variable | Description | Example |
|----------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | AWS access key with KMS permissions | `AKIAIOSFODNN7EXAMPLE` |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` |
| `AWS_DEFAULT_REGION` or `AWS_REGION` | AWS region where KMS key exists | `us-east-1` |
| `KMS_KEY_ID` | KMS key ID or alias | `alias/audit-crypto-key` |

### Required for Audit Crypto

| Variable | Description | Example |
|----------|-------------|---------|
| `AUDIT_CRYPTO_PROVIDER_TYPE` | Crypto provider type | `software` |
| `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` | Base64-encoded KMS-encrypted master key | (stored in secret manager) |

### Optional / Development

| Variable | Description | Default | Production Recommendation |
|----------|-------------|---------|---------------------------|
| `AUDIT_CRYPTO_ALLOW_INIT_FAILURE` | Allow fallback to DummyCryptoProvider on initialization failure | `0` | **MUST be `0` in production** |
| `AUDIT_CRYPTO_MODE` | Crypto mode | `enabled` | `enabled` |

## Security Best Practices

1. **Never set `AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1` in production**
   - This causes the system to fall back to DummyCryptoProvider
   - Provides NO REAL SECURITY for audit logs
   - Compromises audit log integrity

2. **Use AWS Secrets Manager or similar for secret storage**
   - Never store encrypted keys in environment variables in production
   - Use proper secret rotation policies
   - Enable secret versioning

3. **Enable KMS key rotation**
   - AWS KMS supports automatic key rotation
   - Update your encrypted master key after rotation
   - Test your key rotation process regularly

4. **Monitor KMS API calls**
   - Enable CloudTrail logging for KMS operations
   - Set up alerts for KMS decrypt failures
   - Monitor KMS API throttling

5. **Test disaster recovery procedures**
   - Document your key recovery process
   - Test key restoration from backups
   - Ensure encrypted master keys are backed up securely

## Debugging Commands

### Check KMS Permissions
```bash
aws kms decrypt \
  --ciphertext-blob fileb://<(echo "your-base64-ciphertext" | base64 -d) \
  --key-id "your-key-id" \
  --query Plaintext \
  --output text
```

### List Available Secrets
```bash
# AWS Secrets Manager
aws secretsmanager list-secrets

# Get specific secret value
aws secretsmanager get-secret-value \
  --secret-id AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64
```

### Test Boto3 Connection
```python
import boto3
kms = boto3.client('kms')
print(kms.list_keys())
```

## Support

If you continue to experience issues after following this guide:

1. Check the application logs for detailed error messages
2. Verify all environment variables are set correctly
3. Ensure AWS credentials have the necessary KMS permissions
4. Review the [SECRETS_MANAGEMENT.md](SECRETS_MANAGEMENT.md) documentation
5. Contact your infrastructure team for assistance with KMS configuration

## Related Documentation

- [SECRETS_MANAGEMENT.md](SECRETS_MANAGEMENT.md) - Secret management best practices
- [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment procedures
- [SECURITY_DEPLOYMENT_GUIDE.md](SECURITY_DEPLOYMENT_GUIDE.md) - Security configuration guide
- [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) - Complete environment variable reference
