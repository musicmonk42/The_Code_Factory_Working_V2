<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# 🌐 BACKENDS.md — Self Fixing Engineer™

---

## Table of Contents

1. [Introduction & Philosophy](#introduction--philosophy)
2. [Supported Storage & Checkpoint Backends](#supported-storage--checkpoint-backends)
    - [2.1 Amazon S3](#21-amazon-s3)
    - [2.2 Google Cloud Storage (GCS)](#22-google-cloud-storage-gcs)
    - [2.3 Azure Blob Storage](#23-azure-blob-storage)
    - [2.4 etcd](#24-etcd)
    - [2.5 Local Filesystem](#25-local-filesystem)
3. [Pub/Sub & Mesh Integrations](#pubsub--mesh-integrations)
    - [3.1 Redis](#31-redis)
    - [3.2 Kafka](#32-kafka)
    - [3.3 NATS](#33-nats)
4. [Policy, Config, and Provenance Backends](#policy-config-and-provenance-backends)
    - [4.1 etcd (Policy/Config)](#41-etcd-policyconfig)
    - [4.2 App Mesh / Anthos (Advanced)](#42-app-mesh--anthos-advanced)
5. [Environment & Secrets Management](#environment--secrets-management)
6. [Automated Testing & Quality Gates](#automated-testing--quality-gates)
7. [Security & Compliance Controls](#security--compliance-controls)
8. [Troubleshooting Matrix](#troubleshooting-matrix)
9. [Best Practices, Patterns, and Anti-Patterns](#best-practices-patterns-and-anti-patterns)
10. [Versioning, Extensibility, and Contribution](#versioning-extensibility-and-contribution)
11. [Contacts, Support, and Escalation](#contacts-support-and-escalation)

---

## 1. Introduction & Philosophy

Self Fixing Engineer™ supports plug-and-play, fully-audited, and policy-driven backend integrations for all storage, state, messaging, and config requirements.  
All backends are modular, runtime-configurable, and subjected to automated and manual validation.  
No deployment is production-ready without at least one fully operational backend in each required category.

---

## 2. Supported Storage & Checkpoint Backends

### 2.1 Amazon S3

**Required Environment:**
```env
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
S3_BUCKET=your-bucket
AWS_REGION=us-west-2
```

**Provisioning:**
- Create bucket with private ACL
- Set lifecycle and versioning policies as required by compliance

**Example Code:**
```python
from checkpoint import get_backend_client
client = get_backend_client("s3")
await client.write("key", b"value")
```

**Automated Test:**
```bash
pytest -k test_checkpoint_crud
```

**Security:**
- Grant only minimum required IAM permissions to bucket
- Audit all access via CloudTrail or equivalent

---

### 2.2 Google Cloud Storage (GCS)

**Required Environment:**
```env
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
GCS_BUCKET=your-bucket
```

**Example Code:**
```python
client = get_backend_client("gcs")
await client.write("key", b"value")
```

**Test:**
```bash
pytest -k test_checkpoint_crud
```

**Security:**
- Use a service account with minimum permissions
- Protect credential JSON—never commit to source

---

### 2.3 Azure Blob Storage

**Required Environment:**
```env
AZURE_STORAGE_CONNECTION_STRING=your-connection-string
AZURE_CONTAINER=your-container
```

**Example Code:**
```python
client = get_backend_client("azure")
await client.write("key", b"value")
```

**Security:**
- Use RBAC and SAS tokens wherever possible

---

### 2.4 etcd

**Required Environment:**
```env
ETCD_HOST=localhost
ETCD_PORT=2379
```

**Best Practices:**
- Enable mTLS for all connections
- Use role-based ACLs

---

### 2.5 Local Filesystem

**Required Environment:**  
None (default: ./storage)

**Warning:**  
Only use for development, testing, or airgapped/offline deployments.  
Not recommended for HA or multi-node.

---

## 3. Pub/Sub & Mesh Integrations

### 3.1 Redis

**Required Environment:**
```env
REDIS_URL=redis://localhost:6379/0
```

**Notes:**
- Production: enable AUTH and TLS; set maxmemory and eviction policy

---

### 3.2 Kafka

**Required Environment:**
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=test
```

**Best Practices:**
- Enable SSL, SASL, and topic ACLs
- Set retention and compaction as per data policy

---

### 3.3 NATS

**Required Environment:**
```env
NATS_URL=nats://localhost:4222
```

**Best Practices:**
- Use token or NKey authentication in production
- Audit connections and message flows

---

## 4. Policy, Config, and Provenance Backends

### 4.1 etcd (Policy/Config)

**Example Code:**
```python
from mesh_policy import get_policy_store
store = get_policy_store("etcd")
await store.set_policy("allow", {"user": "test"})
```

---

### 4.2 App Mesh / Anthos (Advanced)

(Stub) Document all config, endpoints, IAM, and operational checks if supported.

---

## 5. Environment & Secrets Management

- Use `.env` for local/dev only
- All production secrets should be managed by enterprise vaults (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault)
- Rotate all keys and credentials regularly (every 90 days or as mandated)
- Never log, print, or expose credentials in error messages

---

## 6. Automated Testing & Quality Gates

All backend modules must pass:
- CRUD operation tests (`test_checkpoint_crud`)
- Concurrency and atomicity tests (for mesh/pubsub)
- Error injection (simulate disconnects, permission errors)

All tests are auto-skipped if backend is not configured, but must be run in at least one environment before each release.

---

## 7. Security & Compliance Controls

- All backend access is policy-driven, with runtime enforcement and audit
- Sensitive data always encrypted in transit (TLS 1.2/1.3 minimum) and at rest (provider default or app-level crypto)
- Every access, write, and delete is audit-logged (see AUDIT.md)
- Regularly review all cloud IAM, bucket/container, and topic policies for drift
- Backends subject to automated security scanning (CI/CD with Trivy, Bandit, pip-audit, etc.)

---

## 8. Troubleshooting Matrix

| Symptom                    | Backend    | Typical Cause           | Resolution                          |
|----------------------------|------------|------------------------|-------------------------------------|
| Credentials/config not found| Any        | Missing env or file    | Set env, check .env or vault        |
| Connection refused/timeout | Any        | Service down, wrong port| Start/restart backend, check DNS    |
| PermissionDenied/AccessDenied| Any      | IAM/ACL misconfig      | Review cloud IAM/ACL, regenerate    |
| NoSuchBucket/BucketNotFound| S3/GCS     | Resource missing       | Create bucket/container             |
| Data not persisted         | Any        | Backend misconfigured  | Review backend logs and config      |
| Backend test skipped       | Any        | Not configured         | Set up env, rerun test              |
| Slow performance           | Any        | Network, quota, throttling| Profile, optimize, consider caching|

---

## 9. Best Practices, Patterns, and Anti-Patterns

**Do:**
- Use unique, least-privilege service accounts for each backend
- Automate provisioning and teardown via infra-as-code
- Monitor for config drift and cloud policy changes
- Run backend tests as part of every PR and release

**Don’t:**
- Share secrets across services or environments
- Use production backends for dev/testing
- Hardcode credentials, endpoint URLs, or bucket/container names

---

## 10. Versioning, Extensibility, and Contribution

- All backend connectors use semantic versioning
- Breaking changes must be documented in CHANGELOG.md and migration notes

**To contribute a new backend:**
- Fork checkpoint.py, mesh_adapter.py, or mesh_policy.py
- Add new backend client, test cases, manifest schema
- Submit a PR with full test coverage and documentation update

---

## 11. Contacts, Support, and Escalation

- Backend integration support: [support@yourcompany.com]
- Security/compliance: [security@yourcompany.com]
- Critical issues or escalation: [escalation@yourcompany.com]
- Enterprise onboarding: [customer.success@yourcompany.com]

---