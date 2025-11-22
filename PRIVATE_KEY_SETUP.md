# Private Key Generation Guide

This file contains instructions for generating the private key required for audit signing.

## ⚠️ SECURITY WARNING

**NEVER commit private keys, certificates, or any sensitive credentials to version control!**

The repository has `.pem`, `.key`, and other certificate files in `.gitignore` to prevent accidental commits.

## Generate Audit Signing Private Key

To generate a new RSA private key for audit signing:

```bash
# Generate a 2048-bit RSA private key
openssl genrsa -out private.pem 2048

# Set proper permissions (Unix/Linux/macOS)
chmod 600 private.pem
```

## Configuration

Set the private key in your environment:

```bash
# Option 1: Set as environment variable (for development)
export AUDIT_SIGNING_PRIVATE_KEY="$(cat private.pem)"

# Option 2: Use a secrets manager (for production)
# - AWS Secrets Manager
# - Azure Key Vault
# - HashiCorp Vault
# - Google Cloud Secret Manager
```

## Important Notes

1. **Never share or commit** `private.pem` or the private key content
2. **Keep the key secure** - use appropriate file permissions (600)
3. **Use secrets managers** in production environments
4. **Rotate keys regularly** following your security policy
5. **Back up keys securely** using encrypted storage

## For Production

In production environments:

1. Use a dedicated Key Management Service (KMS)
2. Store keys in a secrets manager
3. Implement key rotation policies
4. Use hardware security modules (HSM) for critical applications
5. Enable audit logging for all key access

## Reference

The private key is used in:
- `self_fixing_engineer/arbiter/meta_learning_orchestrator/audit_utils.py`

For more information about security best practices, see:
- [SECURITY_DEPLOYMENT_GUIDE.md](../SECURITY_DEPLOYMENT_GUIDE.md)
- [SECURITY_AUDIT_REPORT.md](../SECURITY_AUDIT_REPORT.md)
