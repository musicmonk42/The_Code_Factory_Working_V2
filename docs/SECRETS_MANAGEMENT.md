# Secrets Management Guide

This guide covers how to securely manage secrets in the Code Factory platform across different environments.

## Table of Contents

- [Overview](#overview)
- [Supported Providers](#supported-providers)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Best Practices](#best-practices)
- [Production Setup](#production-setup)
- [Key Rotation](#key-rotation)
- [Troubleshooting](#troubleshooting)

## Overview

The Code Factory platform supports multiple secrets management providers:

- **Environment Variables** (for PaaS platforms like Railway, Heroku)
- **AWS Secrets Manager** (recommended for AWS deployments)
- **HashiCorp Vault** (recommended for multi-cloud or on-premise)
- **Google Cloud Secret Manager** (recommended for GCP deployments)
- **Azure Key Vault** (recommended for Azure deployments)

## Supported Providers

### Environment Variables (PaaS Platforms)

**Use Case:** Production deployments on PaaS platforms (Railway, Heroku, Render, etc.)

**Setup:**
```bash
# Set in Railway/Heroku dashboard or .env file for local dev
USE_ENV_SECRETS=true
AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64=your-base64-key
OPENAI_API_KEY=your-api-key
DATABASE_PASSWORD=your-password
```

**Production Ready:** ✅ Yes - when `USE_ENV_SECRETS=true` is set

**Features:**
- Native integration with PaaS platforms
- Simple configuration
- No additional services required
- Automatic injection by platform

**Security Notes:**
- Secrets are stored in platform's secure environment variable storage
- Platform handles encryption at rest
- Access controlled by platform's IAM/RBAC
- May be visible in process listings (platform-dependent)

**When to use:**
- Railway, Heroku, Render, or similar PaaS deployments
- Quick prototyping and demos
- Small to medium production workloads on PaaS

**⚠️ NOTE:** While suitable for PaaS platforms, for high-security workloads consider using a dedicated secret manager (AWS Secrets Manager, Vault, etc.)

### Environment Variables (Development Only - Legacy)

**Use Case:** Local development only

**Setup:**
```bash
# Set in .env file (DO NOT set USE_ENV_SECRETS in dev)
OPENAI_API_KEY=your-api-key
DATABASE_PASSWORD=your-password
```

**⚠️ WARNING:** Never use environment variables without `USE_ENV_SECRETS=true` in production or commit them to version control!

### AWS Secrets Manager

**Use Case:** AWS cloud deployments

**Prerequisites:**
```bash
pip install boto3
```

**Setup:**
```bash
# .env or environment
SECRETS_PROVIDER=aws
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

**Create a secret:**
```bash
aws secretsmanager create-secret \
  --name code-factory/openai-api-key \
  --secret-string "your-api-key-here"
```

**Features:**
- Automatic encryption at rest (KMS)
- Fine-grained IAM access control
- Automatic rotation support
- Audit logging with CloudTrail
- Multi-region replication

**Cost:** ~$0.40/secret/month + $0.05 per 10,000 API calls

### HashiCorp Vault

**Use Case:** Multi-cloud, on-premise, or hybrid deployments

**Prerequisites:**
```bash
pip install hvac
```

**Setup:**
```bash
# .env or environment
SECRETS_PROVIDER=vault
VAULT_ADDR=https://vault.example.com:8200
VAULT_TOKEN=your-vault-token
```

**Create a secret:**
```bash
vault kv put secret/code-factory/openai-api-key value="your-api-key-here"
```

**Features:**
- Dynamic secrets generation
- Lease and renewal
- Secret versioning
- Detailed audit logs
- Plugin-based architecture
- High availability

**Deployment:** Self-hosted or HashiCorp Cloud Platform (HCP)

### Google Cloud Secret Manager

**Use Case:** GCP cloud deployments

**Prerequisites:**
```bash
pip install google-cloud-secret-manager
```

**Setup:**
```bash
# .env or environment
SECRETS_PROVIDER=gcp
GCP_PROJECT_ID=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

**Create a secret:**
```bash
echo -n "your-api-key-here" | gcloud secrets create openai-api-key --data-file=-
```

**Features:**
- Automatic encryption
- IAM-based access control
- Audit logging with Cloud Audit Logs
- Versioning and aliases
- Replication control

**Cost:** Free for first 6 secret versions, then $0.06 per version/month + API costs

### Azure Key Vault

**Use Case:** Azure cloud deployments

**Prerequisites:**
```bash
pip install azure-keyvault-secrets azure-identity
```

**Setup:**
```bash
# .env or environment
SECRETS_PROVIDER=azure
AZURE_KEY_VAULT_URL=https://your-vault.vault.azure.net/
```

**Create a secret:**
```bash
az keyvault secret set \
  --vault-name your-vault \
  --name openai-api-key \
  --value "your-api-key-here"
```

**Features:**
- Hardware security module (HSM) backed
- Azure AD authentication
- RBAC and access policies
- Soft delete and purge protection
- Private endpoint support

**Cost:** ~$0.03 per 10,000 transactions

## Configuration

### Environment Variables

Choose your secret manager provider by setting the appropriate environment variable:

```bash
# PaaS Platforms (Railway, Heroku, Render)
USE_ENV_SECRETS=true

# AWS
USE_AWS_SECRETS=true
AWS_REGION=us-east-1

# HashiCorp Vault
USE_HASHICORP_VAULT=true
VAULT_ADDR=https://vault.example.com:8200
VAULT_TOKEN=your-token

# Google Cloud
USE_GCP_SECRETS=true
GCP_PROJECT_ID=your-project-id
```

### Required Secrets

The following secrets should be configured for production:

#### Audit Crypto (Required for Railway/PaaS)
- `AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64` - Master encryption key for audit crypto (base64-encoded)
- `AGENTIC_AUDIT_HMAC_KEY` - HMAC key for audit log signing (64 hex characters)

#### API Keys
- `GROK_API_KEY` - xAI Grok API key
- `OPENAI_API_KEY` - OpenAI API key
- `GOOGLE_API_KEY` - Google Gemini API key
- `ANTHROPIC_API_KEY` - Anthropic Claude API key

#### Database
- `DATABASE_URL` - Database connection string
- `DATABASE_PASSWORD` - Database password
- `REDIS_PASSWORD` - Redis password

#### Security
- `SECRET_KEY` - Application secret key
- `JWT_SECRET_KEY` - JWT signing key
- `ENCRYPTION_KEY` - Data encryption key
- `AUDIT_SIGNING_PRIVATE_KEY` - Audit log signing key

#### Cloud Services (if applicable)
- `AWS_ACCESS_KEY_ID` - AWS access key
- `AWS_SECRET_ACCESS_KEY` - AWS secret key
- `GCP_SERVICE_ACCOUNT_KEY` - GCP service account JSON
- `AZURE_CLIENT_SECRET` - Azure client secret

#### SIEM/Monitoring
- `SPLUNK_TOKEN` - Splunk HEC token
- `DATADOG_API_KEY` - Datadog API key
- `SLACK_WEBHOOK_URL` - Slack notification webhook

## Usage Examples

### Python Code

```python
from omnicore_engine.secrets_manager import get_secret, set_secret, SecretProvider

# Get a secret (uses configured provider)
api_key = get_secret("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY not found")

# Get a secret with default value
db_url = get_secret("DATABASE_URL", default="sqlite:///./dev.db")

# Get a secret from specific provider
aws_key = get_secret("AWS_ACCESS_KEY", provider=SecretProvider.AWS)

# Store a secret (development/testing only)
set_secret("MY_SECRET", "secret-value")

# Use in application
import openai
openai.api_key = get_secret("OPENAI_API_KEY")
```

### FastAPI Integration

```python
from fastapi import FastAPI, Depends
from omnicore_engine.secrets_manager import get_secret

app = FastAPI()

def get_api_key():
    """Dependency to get API key"""
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError("API key not configured")
    return key

@app.get("/generate")
async def generate(api_key: str = Depends(get_api_key)):
    # Use api_key securely
    pass
```

### Docker Compose

```yaml
services:
  omnicore:
    image: code-factory:latest
    environment:
      - SECRETS_PROVIDER=aws
      - AWS_REGION=us-east-1
    # AWS credentials via IAM role (preferred)
    # or via environment variables (less secure)
```

### Kubernetes

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: code-factory
spec:
  serviceAccountName: code-factory-sa  # For cloud provider IAM
  containers:
  - name: omnicore
    image: code-factory:latest
    env:
    - name: SECRETS_PROVIDER
      value: "aws"
    - name: AWS_REGION
      value: "us-east-1"
```

## Best Practices

### 1. Use Managed Services in Production

**DO:**
- Use AWS Secrets Manager for AWS deployments
- Use GCP Secret Manager for GCP deployments
- Use Azure Key Vault for Azure deployments
- Use HashiCorp Vault for multi-cloud or on-premise

**DON'T:**
- Don't use environment variables in production
- Don't hardcode secrets in code
- Don't commit secrets to version control
- Don't share secrets via email or chat

### 2. Principle of Least Privilege

Grant only the minimum permissions needed:

```python
# AWS IAM Policy Example
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "secretsmanager:GetSecretValue"
    ],
    "Resource": [
      "arn:aws:secretsmanager:us-east-1:123456789012:secret:code-factory/*"
    ]
  }]
}
```

### 3. Rotate Secrets Regularly

**Recommended rotation schedule:**
- API keys: Every 90 days
- Database passwords: Every 90 days
- Encryption keys: Every 365 days
- Signing keys: Every 365 days or on compromise

### 4. Audit Access

Enable audit logging for all secret access:

- **AWS:** Enable CloudTrail
- **GCP:** Enable Cloud Audit Logs
- **Azure:** Enable diagnostic settings
- **Vault:** Enable audit device

### 5. Use Separate Secrets per Environment

```
code-factory/production/openai-api-key
code-factory/staging/openai-api-key
code-factory/development/openai-api-key
```

### 6. Monitor for Anomalies

Set up alerts for:
- Unusual secret access patterns
- Failed authentication attempts
- Secret modifications
- Access from unexpected IPs

### 7. Implement Secret Versioning

Keep multiple versions of secrets to support rollback:

```bash
# AWS
aws secretsmanager put-secret-value \
  --secret-id code-factory/api-key \
  --secret-string "new-value"

# Previous versions remain accessible for rollback
```

## Production Setup

### AWS Secrets Manager Setup

1. **Create IAM Role:**
```bash
aws iam create-role \
  --role-name CodeFactorySecretsRole \
  --assume-role-policy-document file://trust-policy.json
```

2. **Attach Policy:**
```bash
aws iam put-role-policy \
  --role-name CodeFactorySecretsRole \
  --policy-name SecretsAccess \
  --policy-document file://secrets-policy.json
```

3. **Create Secrets:**
```bash
aws secretsmanager create-secret \
  --name code-factory/prod/openai-api-key \
  --secret-string "your-api-key" \
  --kms-key-id alias/aws/secretsmanager
```

4. **Enable Rotation (Optional):**
```bash
aws secretsmanager rotate-secret \
  --secret-id code-factory/prod/openai-api-key \
  --rotation-lambda-arn arn:aws:lambda:region:account-id:function:rotation-function \
  --rotation-rules AutomaticallyAfterDays=90
```

### HashiCorp Vault Setup

1. **Install Vault:**
```bash
# Docker
docker run -d --name=vault --cap-add=IPC_LOCK \
  -e 'VAULT_DEV_ROOT_TOKEN_ID=myroot' \
  -p 8200:8200 vault:latest

# Or download binary
wget https://releases.hashicorp.com/vault/1.15.0/vault_1.15.0_linux_amd64.zip
unzip vault_1.15.0_linux_amd64.zip
sudo mv vault /usr/local/bin/
```

2. **Initialize and Unseal:**
```bash
vault operator init
vault operator unseal <unseal-key-1>
vault operator unseal <unseal-key-2>
vault operator unseal <unseal-key-3>
```

3. **Enable KV Secrets Engine:**
```bash
vault secrets enable -version=2 kv
```

4. **Create Secrets:**
```bash
vault kv put secret/code-factory/prod/openai-api-key value="your-api-key"
```

5. **Create Policy:**
```bash
vault policy write code-factory - <<EOF
path "secret/data/code-factory/prod/*" {
  capabilities = ["read"]
}
EOF
```

6. **Create Token:**
```bash
vault token create -policy=code-factory
```

### Google Cloud Secret Manager Setup

1. **Enable API:**
```bash
gcloud services enable secretmanager.googleapis.com
```

2. **Create Service Account:**
```bash
gcloud iam service-accounts create code-factory-secrets \
  --display-name "Code Factory Secrets Access"
```

3. **Grant Permissions:**
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:code-factory-secrets@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

4. **Create Secrets:**
```bash
echo -n "your-api-key" | gcloud secrets create openai-api-key \
  --data-file=- \
  --replication-policy="automatic"
```

### Azure Key Vault Setup

1. **Create Key Vault:**
```bash
az keyvault create \
  --name code-factory-vault \
  --resource-group code-factory-rg \
  --location eastus
```

2. **Set Access Policy:**
```bash
az keyvault set-policy \
  --name code-factory-vault \
  --object-id <principal-id> \
  --secret-permissions get list
```

3. **Create Secrets:**
```bash
az keyvault secret set \
  --vault-name code-factory-vault \
  --name openai-api-key \
  --value "your-api-key"
```

## Key Rotation

### Automated Rotation (Recommended)

#### AWS Secrets Manager

AWS can automatically rotate secrets using Lambda functions:

```python
# rotation-function.py
import boto3
import json

def lambda_handler(event, context):
    secret_id = event['SecretId']
    token = event['Token']
    step = event['Step']
    
    if step == "createSecret":
        # Generate new secret
        new_secret = generate_new_key()
        client = boto3.client('secretsmanager')
        client.put_secret_value(
            SecretId=secret_id,
            SecretString=new_secret,
            VersionStages=['AWSPENDING'],
            ClientRequestToken=token
        )
    
    elif step == "setSecret":
        # Update application with new secret
        pass
    
    elif step == "testSecret":
        # Test new secret works
        pass
    
    elif step == "finishSecret":
        # Mark new secret as current
        pass
```

#### HashiCorp Vault

Vault supports dynamic secrets that are automatically rotated:

```bash
# Enable database secrets engine
vault secrets enable database

# Configure PostgreSQL
vault write database/config/postgresql \
  plugin_name=postgresql-database-plugin \
  connection_url="postgresql://{{username}}:{{password}}@localhost:5432/postgres" \
  allowed_roles="code-factory-role" \
  username="vault-admin" \
  password="vault-password"

# Create role with TTL
vault write database/roles/code-factory-role \
  db_name=postgresql \
  creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';" \
  default_ttl="1h" \
  max_ttl="24h"
```

### Manual Rotation Process

1. **Generate New Secret:**
```bash
# Generate strong random secret
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

2. **Store New Secret:**
```bash
# AWS
aws secretsmanager put-secret-value \
  --secret-id code-factory/api-key \
  --secret-string "new-secret-value"

# Vault
vault kv put secret/code-factory/api-key value="new-secret-value"
```

3. **Update Application:**
```bash
# Rolling restart to pick up new secret
kubectl rollout restart deployment/code-factory
```

4. **Verify:**
```bash
# Test application functionality
curl https://api.codefactory.example.com/health
```

5. **Revoke Old Secret:**
```bash
# After verification, revoke old API key at provider
```

### Rotation Schedule Template

Create a rotation schedule document:

```markdown
# Secret Rotation Schedule

| Secret Name | Last Rotated | Next Rotation | Owner | Notes |
|-------------|--------------|---------------|-------|-------|
| OPENAI_API_KEY | 2025-11-01 | 2026-01-30 | DevOps | Auto-rotation enabled |
| DATABASE_PASSWORD | 2025-11-15 | 2026-02-13 | DevOps | Manual rotation required |
| JWT_SECRET_KEY | 2025-10-01 | 2026-09-30 | Security | Annual rotation |
| ENCRYPTION_KEY | 2025-01-01 | 2026-01-01 | Security | Annual rotation |
```

## Troubleshooting

### Secret Not Found

**Error:** `Secret 'XYZ' not found`

**Solutions:**
1. Verify secret name is correct (case-sensitive)
2. Check IAM/access permissions
3. Verify secret exists in correct region/project
4. Check SECRETS_PROVIDER is configured correctly

### Authentication Failed

**Error:** `Authentication failed` or `Access denied`

**Solutions:**
1. Verify credentials are set correctly
2. Check IAM/RBAC permissions
3. Verify service account has proper roles
4. Check network connectivity to secrets service

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'boto3'`

**Solutions:**
```bash
# Install required provider library
pip install boto3  # AWS
pip install hvac  # Vault
pip install google-cloud-secret-manager  # GCP
pip install azure-keyvault-secrets azure-identity  # Azure
```

### Rate Limiting

**Error:** `Rate limit exceeded`

**Solutions:**
1. Implement caching for frequently accessed secrets
2. Use connection pooling
3. Request rate limit increase from provider
4. Consider using a local cache layer

### Network Issues

**Error:** `Connection timeout` or `Unable to connect`

**Solutions:**
1. Verify network connectivity
2. Check firewall rules
3. Verify DNS resolution
4. Check VPC/subnet configuration
5. Verify service endpoint is reachable

## Migration Guide

### From Environment Variables to Secrets Manager

1. **Audit Current Secrets:**
```bash
grep -r "os.environ\|os.getenv" . --include="*.py" | grep -i "key\|password\|token\|secret"
```

2. **Store in Secrets Manager:**
```bash
# For each secret found
aws secretsmanager create-secret \
  --name code-factory/SECRET_NAME \
  --secret-string "$SECRET_VALUE"
```

3. **Update Code:**
```python
# Before
import os
api_key = os.environ.get("OPENAI_API_KEY")

# After
from omnicore_engine.secrets_manager import get_secret
api_key = get_secret("OPENAI_API_KEY")
```

4. **Test:**
```bash
# Set SECRETS_PROVIDER
export SECRETS_PROVIDER=aws
export AWS_REGION=us-east-1

# Run tests
make test
```

5. **Deploy:**
```bash
# Update deployment configuration
# Remove environment variables
# Set SECRETS_PROVIDER
kubectl set env deployment/code-factory SECRETS_PROVIDER=aws
```

## Additional Resources

- [AWS Secrets Manager Documentation](https://docs.aws.amazon.com/secretsmanager/)
- [HashiCorp Vault Documentation](https://www.vaultproject.io/docs)
- [GCP Secret Manager Documentation](https://cloud.google.com/secret-manager/docs)
- [Azure Key Vault Documentation](https://docs.microsoft.com/azure/key-vault/)
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)

---

**Last Updated:** 2025-11-22  
**Version:** 1.0.0  
**Maintained by:** DevOps Team
