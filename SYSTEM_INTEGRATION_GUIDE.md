# Code Factory Platform - Enterprise System Integration Guide

**Document Version:** 2.0
**Date:** February 11, 2026
**Purpose:** Technical integration guide for banking, manufacturing, and enterprise systems
**Repository:** The_Code_Factory_Working_V2
**Platform Version:** 1.0.0

---

## Executive Summary

This document provides a comprehensive technical analysis of how the Code Factory Platform can be integrated into enterprise systems, with specific focus on banking and manufacturing sectors. The platform offers a sophisticated, AI-driven development automation solution with enterprise-grade security, compliance, and scalability features designed for regulated industries.

### Key Integration Capabilities

- **API-First Architecture**: RESTful APIs with OpenAPI 3.1 specifications
- **Event-Driven Communication**: Kafka and Redis-based message bus for real-time integration
- **Secure Authentication**: OAuth2, JWT, MFA with SOC2/ISO27001/HIPAA compliance
- **Database Flexibility**: PostgreSQL/Citus for distributed SQL, SQLAlchemy ORM
- **Blockchain Audit**: DLT integration via Hyperledger Fabric, Ethereum, Corda, Quorum
- **Multi-Cloud Ready**: AWS, Azure, GCP integrations with native SIEM support
- **Container-Native**: Docker, Kubernetes, and Helm deployment options

---

## Table of Contents

1. [Integration Architecture Overview](#1-integration-architecture-overview)
2. [Banking Sector Integration](#2-banking-sector-integration)
3. [Manufacturing Sector Integration](#3-manufacturing-sector-integration)
4. [API Integration Patterns](#4-api-integration-patterns)
5. [Message Bus Integration](#5-message-bus-integration)
6. [Database Integration](#6-database-integration)
7. [Security & Authentication Integration](#7-security--authentication-integration)
8. [Blockchain (DLT) Integration](#8-blockchain-dlt-integration)
9. [Monitoring & SIEM Integration](#9-monitoring--siem-integration)
10. [Deployment Integration](#10-deployment-integration)
11. [Integration Best Practices](#11-integration-best-practices)
12. [Troubleshooting & Support](#12-troubleshooting--support)

---

## 1. Integration Architecture Overview

### 1.1 Platform Architecture

The Code Factory Platform follows a three-tier architecture optimized for enterprise integration:

```
┌─────────────────────────────────────────────────────────────────┐
│                    External Systems Layer                        │
│  (Banking Core, ERP, MES, Legacy Systems, Cloud Services)       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Integration Layer                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ REST API │  │ Message  │  │  Event   │  │   DLT    │       │
│  │ Gateway  │  │   Bus    │  │ Stream   │  │ Adapter  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Code Factory Platform                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Generator   │  │   OmniCore   │  │     SFE      │         │
│  │   (RCG)      │◄─┤    Engine    │─►│  (Arbiter)   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│         │                 │                   │                  │
│         └─────────────────┴───────────────────┘                  │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         Shared Infrastructure Layer                       │  │
│  │  • Message Bus (Kafka/Redis)                             │  │
│  │  • Database (PostgreSQL/Citus)                           │  │
│  │  • Observability (Prometheus/OpenTelemetry)              │  │
│  │  • DLT (Hyperledger Fabric/EVM)                          │  │
│  │  • SIEM (Splunk/ELK/Azure Sentinel)                      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Integration Points

The platform exposes six primary integration interfaces:

| Interface | Protocol | Use Case | Port/Endpoint |
|-----------|----------|----------|---------------|
| **REST API** | HTTP/HTTPS | Synchronous operations, CRUD | 8000/TCP |
| **Message Bus** | Kafka/Redis | Asynchronous events, pub/sub | 9092/6379 |
| **WebSocket** | WSS | Real-time bidirectional updates | 8000/ws |
| **Database** | PostgreSQL | Direct data access (admin) | 5432 |
| **DLT Chain** | HTTP/Native | Blockchain checkpoint queries | Varies |
| **Metrics** | Prometheus | Observability and monitoring | 9090 |

---

## 2. Banking Sector Integration

### 2.1 Banking Use Cases

The Code Factory Platform addresses critical banking needs:

#### A. Core Banking System Modernization
**Scenario**: Legacy COBOL/mainframe migration to microservices

**Integration Pattern**:
```
Legacy Core Banking System
        ↓
   (Batch Export/API)
        ↓
Code Factory Platform ──► Generate Modern Microservices
        ↓
   (CI/CD Pipeline)
        ↓
New Cloud-Native Banking Platform
```

**Implementation**:
1. **Requirements Extraction**: Export business rules from legacy system
2. **Code Generation**: Use Code Factory to generate microservices (Java/Spring Boot or Python/FastAPI)
3. **Compliance Verification**: Automatic PCI-DSS, SOC2, ISO27001 compliance checks
4. **Testing**: Auto-generated integration tests with 70-90% coverage
5. **Deployment**: Container-native deployment to Kubernetes
6. **Audit Trail**: Blockchain-based immutable change log

#### B. API Gateway for Banking Services
**Scenario**: Unified API layer for mobile/web banking

**Integration Pattern**:
```
Mobile App ──┐
Web App ────┼──► API Gateway (Code Factory Generated)
Partners ───┘           ↓
                   ┌────┴────┐
                   │         │
            Account Service  Payment Service
              Transaction Service
```

**Key Features**:
- OAuth2/OpenID Connect authentication
- Rate limiting (per customer, per endpoint)
- PII redaction in logs
- Real-time fraud detection hooks
- Audit logging to SIEM

#### C. Regulatory Compliance Automation
**Scenario**: Automated generation of compliance reports

**Integration Pattern**:
```
Code Changes (Git) ──► Code Factory SFE
                            ↓
                    Compliance Analysis
                            ↓
              ┌─────────────┴─────────────┐
              │                           │
    NIST 800-53 Report          SOC2 Evidence
    PCI-DSS Checklist           ISO27001 Controls
              │                           │
              └────────► DLT Checkpoint ◄─┘
                         (Immutable Audit)
```

### 2.2 Banking-Specific Configuration

#### Security Configuration for Banking

```python
# security_config.py - Banking profile
{
    "password_policy": {
        "min_length": 16,
        "complexity": "HIGH",
        "history_count": 24,
        "expiry_days": 60
    },
    "mfa": {
        "required": True,
        "methods": ["TOTP", "FIDO2"],
        "backup_codes": True
    },
    "session": {
        "idle_timeout_minutes": 15,
        "absolute_timeout_minutes": 240,
        "max_concurrent": 2
    },
    "encryption": {
        "algorithm": "AES-256-GCM",
        "key_rotation_days": 90,
        "tls_version": "1.3"
    },
    "compliance": {
        "frameworks": ["PCI-DSS", "SOC2", "GLBA"],
        "audit_retention_days": 2555  # 7 years
    }
}
```

#### API Integration Example - Account Service

```bash
# Step 1: Create job
curl -X POST https://codefactory.bank.com/api/jobs/ \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Generate Account Microservice",
    "metadata": {
      "compliance": ["PCI-DSS", "SOC2"],
      "language": "java",
      "framework": "spring-boot",
      "database": "postgresql",
      "authentication": "oauth2"
    }
  }'

# Response: {"id": "job-bank-001", ...}

# Step 2: Upload requirements
curl -X POST https://codefactory.bank.com/api/generator/job-bank-001/upload \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -F "files=@account_service_requirements.md"

# Step 3: Monitor progress
curl https://codefactory.bank.com/api/jobs/job-bank-001/progress \
  -H "Authorization: Bearer ${JWT_TOKEN}"

# Step 4: Download artifacts
curl https://codefactory.bank.com/api/jobs/job-bank-001/artifacts \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -o account-service.zip
```

### 2.3 Banking Integration Architecture

```
┌────────────────────────────────────────────────────────────┐
│                  Banking Infrastructure                     │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────┐ │
│  │ Core Banking │    │   Payment    │    │   Customer  │ │
│  │   System     │    │   Gateway    │    │   Database  │ │
│  └──────┬───────┘    └──────┬───────┘    └──────┬──────┘ │
│         │                   │                    │         │
└─────────┼───────────────────┼────────────────────┼─────────┘
          │                   │                    │
          └───────────────────┴────────────────────┘
                              ↓
          ┌───────────────────────────────────────┐
          │    Enterprise Service Bus (ESB)       │
          │    or API Management Layer            │
          └───────────────────┬───────────────────┘
                              ↓
          ┌───────────────────────────────────────┐
          │     Code Factory Integration          │
          │  ┌─────────────────────────────────┐  │
          │  │ REST API Gateway (TLS 1.3)     │  │
          │  └────────────┬────────────────────┘  │
          │               ↓                        │
          │  ┌─────────────────────────────────┐  │
          │  │ Message Bus (Kafka)             │  │
          │  │ - Audit Events                  │  │
          │  │ - Code Generation Events        │  │
          │  │ - Fix Notifications             │  │
          │  └────────────┬────────────────────┘  │
          │               ↓                        │
          │  ┌─────────────────────────────────┐  │
          │  │ Code Factory Core               │  │
          │  │ - Generator + OmniCore + SFE    │  │
          │  └────────────┬────────────────────┘  │
          │               ↓                        │
          │  ┌─────────────────────────────────┐  │
          │  │ Blockchain Audit (Fabric)       │  │
          │  │ - Immutable Change Log          │  │
          │  │ - Compliance Evidence           │  │
          │  └─────────────────────────────────┘  │
          └───────────────────────────────────────┘
                              ↓
          ┌───────────────────────────────────────┐
          │      Banking SIEM & Monitoring        │
          │  - Splunk Enterprise Security         │
          │  - Azure Sentinel                     │
          │  - IBM QRadar                         │
          └───────────────────────────────────────┘
```

### 2.4 Banking Compliance Features

| Regulation | Code Factory Feature | Implementation |
|------------|---------------------|----------------|
| **PCI-DSS** | PII Redaction | Presidio-based automatic detection |
| **SOC2** | Audit Logging | Tamper-evident logs with Merkle trees |
| **GLBA** | Encryption | AES-256-GCM for data at rest/transit |
| **FFIEC** | Access Control | RBAC + MFA + session management |
| **Basel III** | Change Management | DLT-based immutable audit trail |
| **Reg E/Z** | Error Tracking | Automated bug detection and fixing |

---

## 3. Manufacturing Sector Integration

### 3.1 Manufacturing Use Cases

#### A. Manufacturing Execution System (MES) Integration
**Scenario**: Generate control software for production lines

**Integration Pattern**:
```
Production Requirements ──► Code Factory
                                ↓
                        Generate MES Module
                                ↓
              ┌─────────────────┴─────────────────┐
              │                                   │
    PLC/SCADA Interface                    IoT Gateway
              │                                   │
              └────────► Production Line ◄────────┘
```

**Generated Artifacts**:
- PLC communication drivers (Modbus, OPC-UA, Profinet)
- Real-time data collection services
- Production scheduling algorithms
- Quality control checks
- Downtime monitoring

#### B. Supply Chain Integration
**Scenario**: Automated API generation for supplier systems

**Integration Pattern**:
```
Supplier A API ──┐
Supplier B API ──┼──► Code Factory ──► Unified Supply Chain API
Supplier C API ──┘                             ↓
                                    ERP System Integration
```

**Key Features**:
- Protocol translation (REST, SOAP, EDI, AS2)
- Data format conversion (XML, JSON, CSV)
- Real-time inventory tracking
- Blockchain-based provenance tracking
- Automated order processing

#### C. Industrial IoT (IIoT) Platform
**Scenario**: Edge device management and data processing

**Integration Pattern**:
```
Edge Devices (Sensors) ──► MQTT/CoAP
                                ↓
                        IoT Gateway (Code Factory Generated)
                                ↓
                    ┌───────────┴───────────┐
                    │                       │
            Data Processing          ML Inference
            Time-Series DB          Anomaly Detection
                    │                       │
                    └───────► Dashboard ◄───┘
```

### 3.2 Manufacturing-Specific Configuration

#### OT (Operational Technology) Security

```python
# security_config.py - Manufacturing profile
{
    "network_segmentation": {
        "level_0": "Physical Process",  # Air-gapped
        "level_1": "Basic Control (PLC)",  # Isolated
        "level_2": "Supervisory Control (SCADA)",  # DMZ
        "level_3": "MES/Operations",  # Protected
        "level_4": "Business Systems"  # Standard
    },
    "access_control": {
        "safety_critical": {
            "approval_required": True,
            "dual_control": True,
            "time_delay_seconds": 300
        },
        "production_write": {
            "mfa_required": True,
            "session_timeout_minutes": 10
        }
    },
    "compliance": {
        "frameworks": ["ISO27001", "IEC62443", "NIST800-82"],
        "safety_standards": ["IEC61508", "ISO26262"],
        "audit_retention_days": 1825  # 5 years
    },
    "communication": {
        "protocols": ["OPC-UA", "Modbus-TCP", "Profinet", "MQTT"],
        "encryption": "TLS 1.3 + IPSec",
        "certificate_rotation_days": 90
    }
}
```

#### MQTT Integration Example - IoT Gateway

```bash
# Code Factory configuration for IoT gateway generation
curl -X POST https://codefactory.manufacturing.com/api/jobs/ \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Generate IIoT Gateway Service",
    "metadata": {
      "language": "python",
      "framework": "fastapi",
      "protocols": ["mqtt", "opcua", "modbus"],
      "features": [
        "real_time_data_collection",
        "edge_analytics",
        "time_series_storage",
        "anomaly_detection"
      ],
      "compliance": ["IEC62443", "ISO27001"],
      "deployment": "kubernetes"
    }
  }'
```

### 3.3 Manufacturing Integration Architecture

```
┌────────────────────────────────────────────────────────────┐
│               Manufacturing Operations Layer                │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │   PLC    │  │  SCADA   │  │  Robots  │  │ Sensors  │  │
│  │ Siemens  │  │ Wonderw. │  │  Fanuc   │  │ (IoT)    │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │             │              │             │         │
└───────┼─────────────┼──────────────┼─────────────┼─────────┘
        │             │              │             │
        └─────────────┴──────────────┴─────────────┘
                              ↓
        ┌───────────────────────────────────────────┐
        │       Industrial Network (OT Network)     │
        │       - Firewalled from IT Network        │
        │       - IEC62443 Compliant                │
        └───────────────────┬───────────────────────┘
                            ↓
        ┌───────────────────────────────────────────┐
        │        Code Factory Integration           │
        │  ┌─────────────────────────────────────┐  │
        │  │ OPC-UA/Modbus Gateway               │  │
        │  │ (Generated by Code Factory)         │  │
        │  └────────────┬────────────────────────┘  │
        │               ↓                            │
        │  ┌─────────────────────────────────────┐  │
        │  │ Message Bus (MQTT/Kafka)            │  │
        │  │ - Machine Data Stream               │  │
        │  │ - Quality Events                    │  │
        │  │ - Downtime Alerts                   │  │
        │  └────────────┬────────────────────────┘  │
        │               ↓                            │
        │  ┌─────────────────────────────────────┐  │
        │  │ Code Factory Core                   │  │
        │  │ - MES Module Generation             │  │
        │  │ - Control Logic Synthesis           │  │
        │  │ - Self-Fixing for Control Code      │  │
        │  └────────────┬────────────────────────┘  │
        │               ↓                            │
        │  ┌─────────────────────────────────────┐  │
        │  │ Time-Series Database                │  │
        │  │ - InfluxDB / TimescaleDB            │  │
        │  └─────────────────────────────────────┘  │
        └───────────────────────────────────────────┘
                            ↓
        ┌───────────────────────────────────────────┐
        │          Enterprise Systems               │
        │  - ERP (SAP, Oracle)                      │
        │  - PLM (Siemens Teamcenter, PTC Windchill)│
        │  - Quality Management (QMS)               │
        └───────────────────────────────────────────┘
```

### 3.4 Manufacturing Compliance Features

| Standard | Code Factory Feature | Implementation |
|----------|---------------------|----------------|
| **IEC 62443** | Network Segmentation | Deployment zones with firewalls |
| **ISO 27001** | Risk Management | Automated vulnerability scanning |
| **IEC 61508** | Safety Integrity | Formal verification of control logic |
| **ISO 26262** | Automotive Safety | Traceability via DLT |
| **NIST 800-82** | OT Security | Defense-in-depth architecture |
| **FDA 21 CFR Part 11** | Electronic Records | Tamper-evident audit logs |

---

## 4. API Integration Patterns

### 4.1 REST API Overview

**Base URL**: `https://codefactory.yourdomain.com/api`
**API Version**: v1.0.0
**Protocol**: HTTPS (TLS 1.3)
**Authentication**: OAuth2 Bearer Token / API Key
**Rate Limiting**: 100 req/sec (configurable)

### 4.2 Authentication Flow

#### OAuth2 Authorization Code Flow (Recommended for Web Apps)

```bash
# Step 1: Redirect user to authorization endpoint
https://codefactory.yourdomain.com/oauth/authorize?
  response_type=code&
  client_id=YOUR_CLIENT_ID&
  redirect_uri=https://yourapp.com/callback&
  scope=jobs:write jobs:read sfe:execute&
  state=RANDOM_STATE_STRING

# Step 2: Exchange authorization code for access token
curl -X POST https://codefactory.yourdomain.com/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=AUTH_CODE_FROM_STEP_1" \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "redirect_uri=https://yourapp.com/callback"

# Response:
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "refresh_token_here",
  "scope": "jobs:write jobs:read sfe:execute"
}

# Step 3: Use access token in API requests
curl -X GET https://codefactory.yourdomain.com/api/jobs/ \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

#### API Key Authentication (Recommended for Server-to-Server)

```bash
# Create API key
curl -X POST https://codefactory.yourdomain.com/api/api-keys/ \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Integration",
    "scopes": ["jobs:write", "jobs:read", "sfe:execute"],
    "expires_at": "2027-12-31T23:59:59Z",
    "rate_limit": 1000
  }'

# Response:
{
  "id": "ak_1234567890",
  "key": "sk_live_abc123xyz789...",
  "name": "Production Integration",
  "created_at": "2026-02-11T22:00:00Z"
}

# Use API key in requests
curl -X GET https://codefactory.yourdomain.com/api/jobs/ \
  -H "X-API-Key: sk_live_abc123xyz789..."
```

### 4.3 Core API Endpoints

#### Job Management

```bash
# Create a job
POST /api/jobs/
Content-Type: application/json
Authorization: Bearer {token}

{
  "description": "Generate payment processing microservice",
  "metadata": {
    "project": "banking-core",
    "compliance": ["PCI-DSS", "SOC2"],
    "language": "java",
    "framework": "spring-boot"
  }
}

# Response: 201 Created
{
  "id": "job-20260211-001",
  "status": "pending",
  "created_at": "2026-02-11T22:00:00Z",
  "description": "Generate payment processing microservice",
  "metadata": {...}
}

# List jobs
GET /api/jobs/?status=completed&page=1&per_page=20
Authorization: Bearer {token}

# Response: 200 OK
{
  "jobs": [
    {
      "id": "job-20260211-001",
      "status": "completed",
      "created_at": "2026-02-11T22:00:00Z",
      "completed_at": "2026-02-11T22:05:30Z",
      "description": "...",
      "artifacts_count": 15
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 20,
  "pages": 8
}

# Get job details
GET /api/jobs/job-20260211-001
Authorization: Bearer {token}

# Response: 200 OK
{
  "id": "job-20260211-001",
  "status": "completed",
  "progress": {
    "clarification": {"status": "completed", "progress": 100},
    "code_generation": {"status": "completed", "progress": 100},
    "test_generation": {"status": "completed", "progress": 100},
    "deployment_config": {"status": "completed", "progress": 100},
    "documentation": {"status": "completed", "progress": 100},
    "critique": {"status": "completed", "progress": 100},
    "self_fixing": {"status": "completed", "progress": 100}
  },
  "artifacts": [
    {"type": "source_code", "path": "src/main/java/...", "size": 15234},
    {"type": "tests", "path": "src/test/java/...", "size": 8923},
    {"type": "dockerfile", "path": "Dockerfile", "size": 1234}
  ]
}

# Download artifacts
GET /api/jobs/job-20260211-001/artifacts
Authorization: Bearer {token}
Accept: application/zip

# Response: 200 OK (binary zip file)
```

#### File Upload

```bash
# Upload requirements file
POST /api/generator/job-20260211-001/upload
Authorization: Bearer {token}
Content-Type: multipart/form-data

files: (binary) payment_service_requirements.md
files: (binary) api_design.yaml

# Response: 200 OK
{
  "uploaded_files": [
    {
      "filename": "payment_service_requirements.md",
      "size": 5432,
      "content_type": "text/markdown"
    },
    {
      "filename": "api_design.yaml",
      "size": 2314,
      "content_type": "application/yaml"
    }
  ],
  "job_id": "job-20260211-001",
  "status": "processing"
}
```

#### Self-Fixing Engineer (SFE)

```bash
# Analyze code for errors
POST /api/sfe/job-20260211-001/analyze
Authorization: Bearer {token}
Content-Type: application/json

{
  "code_path": "src/main/java/com/bank/payment/",
  "analysis_depth": "comprehensive",
  "include_optimizations": true
}

# Response: 202 Accepted
{
  "analysis_id": "analysis-001",
  "job_id": "job-20260211-001",
  "status": "in_progress",
  "estimated_completion": "2026-02-11T22:10:00Z"
}

# Get detected errors
GET /api/sfe/job-20260211-001/errors
Authorization: Bearer {token}

# Response: 200 OK
{
  "errors": [
    {
      "id": "error-001",
      "severity": "high",
      "type": "security_vulnerability",
      "description": "SQL Injection vulnerability in payment query",
      "file": "PaymentController.java",
      "line": 145,
      "code_snippet": "SELECT * FROM payments WHERE id = '" + paymentId + "'",
      "cwe": "CWE-89"
    },
    {
      "id": "error-002",
      "severity": "medium",
      "type": "performance",
      "description": "N+1 query pattern detected",
      "file": "TransactionService.java",
      "line": 78
    }
  ],
  "total": 2
}

# Propose fix
POST /api/sfe/errors/error-001/propose-fix
Authorization: Bearer {token}

# Response: 200 OK
{
  "fix_id": "fix-001",
  "error_id": "error-001",
  "strategy": "parameterized_query",
  "diff": "@@ -145,1 +145,2 @@\n- SELECT * FROM payments WHERE id = '" + paymentId + "'\n+ PreparedStatement ps = conn.prepareStatement(\"SELECT * FROM payments WHERE id = ?\");\n+ ps.setString(1, paymentId);",
  "confidence": 0.95,
  "estimated_impact": "Eliminates SQL injection vulnerability"
}

# Review fix
POST /api/sfe/fixes/fix-001/review
Authorization: Bearer {token}
Content-Type: application/json

{
  "approved": true,
  "comments": "Fix looks good, parameterized query is the correct approach",
  "reviewer_id": "user-12345"
}

# Apply fix
POST /api/sfe/fixes/fix-001/apply
Authorization: Bearer {token}
Content-Type: application/json

{
  "dry_run": false,
  "create_backup": true,
  "run_tests": true
}

# Response: 200 OK
{
  "fix_id": "fix-001",
  "status": "applied",
  "backup_id": "backup-001",
  "test_results": {
    "passed": 245,
    "failed": 0,
    "skipped": 3
  },
  "checkpoint_id": "checkpoint-2026-02-11-001",
  "dlt_transaction_id": "0x7b8f9a2c..."
}

# Rollback if needed
POST /api/sfe/fixes/fix-001/rollback
Authorization: Bearer {token}

# Response: 200 OK
{
  "fix_id": "fix-001",
  "status": "rolled_back",
  "restored_from": "backup-001"
}
```

### 4.4 Real-Time Event Streaming

#### WebSocket Integration

```javascript
// JavaScript/TypeScript client
const ws = new WebSocket('wss://codefactory.yourdomain.com/api/events/ws');

// Authenticate
ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'authenticate',
    token: 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
  }));
};

// Subscribe to job updates
ws.send(JSON.stringify({
  type: 'subscribe',
  channels: ['jobs', 'sfe'],
  filters: {
    job_id: 'job-20260211-001'
  }
}));

// Receive events
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  switch(data.event_type) {
    case 'job_progress_updated':
      console.log(`Job ${data.job_id}: ${data.stage} - ${data.progress}%`);
      updateProgressBar(data.progress);
      break;

    case 'error_detected':
      console.log(`Error detected: ${data.error.description}`);
      showErrorNotification(data.error);
      break;

    case 'fix_proposed':
      console.log(`Fix proposed for error ${data.error_id}`);
      showFixReviewDialog(data.fix);
      break;

    case 'job_completed':
      console.log(`Job ${data.job_id} completed with ${data.artifacts_count} artifacts`);
      downloadArtifacts(data.job_id);
      break;
  }
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = () => {
  console.log('WebSocket connection closed');
  // Implement reconnection logic
};
```

#### Server-Sent Events (SSE) Integration

```javascript
// JavaScript/TypeScript client
const eventSource = new EventSource(
  'https://codefactory.yourdomain.com/api/events/sse?job_id=job-20260211-001&token=Bearer_TOKEN'
);

eventSource.addEventListener('job_updated', (event) => {
  const data = JSON.parse(event.data);
  console.log('Job update:', data);
  updateDashboard(data);
});

eventSource.addEventListener('error_detected', (event) => {
  const data = JSON.parse(event.data);
  console.log('Error detected:', data);
  showAlert(data);
});

eventSource.onerror = (error) => {
  console.error('SSE error:', error);
  eventSource.close();
};
```

---

## 5. Message Bus Integration

### 5.1 Kafka Integration

#### Architecture

The Code Factory Platform uses Apache Kafka for production-grade event streaming:

```
Code Factory Platform
        ↓
   Kafka Producer
        ↓
 Kafka Cluster (3+ brokers)
   ├─ Topic: code-generation-events
   ├─ Topic: sfe-analysis-events
   ├─ Topic: audit-events
   └─ Topic: metrics-events
        ↓
   Kafka Consumer
        ↓
External Systems (Your Apps)
```

#### Configuration

```bash
# Environment variables for Kafka integration
export KAFKA_ENABLED=true
export KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092,kafka3:9092
export KAFKA_SECURITY_PROTOCOL=SASL_SSL
export KAFKA_SASL_MECHANISM=SCRAM-SHA-512
export KAFKA_SASL_USERNAME=codefactory-producer
export KAFKA_SASL_PASSWORD=secure-password-here

# Consumer group configuration
export KAFKA_CONSUMER_GROUP_ID=external-system-consumers
export KAFKA_AUTO_OFFSET_RESET=earliest
export KAFKA_ENABLE_AUTO_COMMIT=false  # Manual commit for reliability

# Producer configuration
export KAFKA_PRODUCER_ACKS=all  # All replicas must acknowledge
export KAFKA_PRODUCER_RETRIES=10
export KAFKA_PRODUCER_IDEMPOTENCE=true
```

#### Consumer Implementation (Python)

```python
from aiokafka import AIOKafkaConsumer
import json
import asyncio

async def consume_code_factory_events():
    consumer = AIOKafkaConsumer(
        'code-generation-events',
        'sfe-analysis-events',
        'audit-events',
        bootstrap_servers='kafka1:9092,kafka2:9092,kafka3:9092',
        group_id='banking-system-integration',
        security_protocol='SASL_SSL',
        sasl_mechanism='SCRAM-SHA-512',
        sasl_plain_username='banking-consumer',
        sasl_plain_password='secure-password',
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        enable_auto_commit=False,
        auto_offset_reset='earliest'
    )

    await consumer.start()

    try:
        async for msg in consumer:
            event = msg.value

            # Process event based on type
            if event['event_type'] == 'job_completed':
                await process_job_completion(event)
            elif event['event_type'] == 'error_detected':
                await notify_team(event)
            elif event['event_type'] == 'fix_applied':
                await update_audit_log(event)

            # Manual commit after successful processing
            await consumer.commit()

    except Exception as e:
        print(f"Error consuming messages: {e}")
    finally:
        await consumer.stop()

async def process_job_completion(event):
    job_id = event['job_id']
    artifacts_url = event['artifacts_url']

    # Download artifacts
    artifacts = await download_artifacts(artifacts_url)

    # Deploy to staging environment
    await deploy_to_staging(artifacts)

    # Send notification
    await send_slack_notification(f"Job {job_id} completed and deployed to staging")

if __name__ == "__main__":
    asyncio.run(consume_code_factory_events())
```

#### Consumer Implementation (Java)

```java
import org.apache.kafka.clients.consumer.*;
import org.apache.kafka.common.serialization.StringDeserializer;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.*;

public class CodeFactoryEventConsumer {

    public static void main(String[] args) {
        Properties props = new Properties();
        props.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG,
                  "kafka1:9092,kafka2:9092,kafka3:9092");
        props.put(ConsumerConfig.GROUP_ID_CONFIG, "manufacturing-system");
        props.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,
                  StringDeserializer.class.getName());
        props.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG,
                  StringDeserializer.class.getName());
        props.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        props.put("security.protocol", "SASL_SSL");
        props.put("sasl.mechanism", "SCRAM-SHA-512");
        props.put("sasl.jaas.config",
                  "org.apache.kafka.common.security.scram.ScramLoginModule " +
                  "required username=\"manufacturing-consumer\" " +
                  "password=\"secure-password\";");

        KafkaConsumer<String, String> consumer = new KafkaConsumer<>(props);
        consumer.subscribe(Arrays.asList(
            "code-generation-events",
            "sfe-analysis-events"
        ));

        ObjectMapper mapper = new ObjectMapper();

        try {
            while (true) {
                ConsumerRecords<String, String> records =
                    consumer.poll(Duration.ofMillis(100));

                for (ConsumerRecord<String, String> record : records) {
                    Map<String, Object> event =
                        mapper.readValue(record.value(), Map.class);

                    String eventType = (String) event.get("event_type");

                    switch (eventType) {
                        case "job_completed":
                            processJobCompletion(event);
                            break;
                        case "error_detected":
                            handleErrorDetection(event);
                            break;
                        case "fix_applied":
                            updateChangeLog(event);
                            break;
                    }
                }

                consumer.commitSync();
            }
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            consumer.close();
        }
    }

    private static void processJobCompletion(Map<String, Object> event) {
        String jobId = (String) event.get("job_id");
        System.out.println("Processing completed job: " + jobId);
        // Implement your business logic here
    }
}
```

### 5.2 Redis Integration

#### Pub/Sub Pattern

```python
import redis.asyncio as redis
import json

async def subscribe_to_code_factory_events():
    r = await redis.from_url(
        'redis://codefactory-redis:6379/0',
        password='secure-password',
        decode_responses=True
    )

    pubsub = r.pubsub()
    await pubsub.subscribe(
        'code_factory:jobs',
        'code_factory:sfe',
        'code_factory:audit'
    )

    async for message in pubsub.listen():
        if message['type'] == 'message':
            event = json.loads(message['data'])

            channel = message['channel']
            if channel == 'code_factory:jobs':
                await handle_job_event(event)
            elif channel == 'code_factory:sfe':
                await handle_sfe_event(event)
            elif channel == 'code_factory:audit':
                await handle_audit_event(event)

async def handle_job_event(event):
    if event['event_type'] == 'job_completed':
        job_id = event['job_id']
        print(f"Job {job_id} completed")
        # Trigger downstream processes
```

---

## 6. Database Integration

### 6.1 Direct Database Access (Admin Only)

**Connection String**:
```
postgresql+asyncpg://admin:password@codefactory-db:5432/codefactory
```

**Schema Overview**:
```sql
-- Jobs table
CREATE TABLE jobs (
    id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(50) NOT NULL,
    description TEXT,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

-- Artifacts table
CREATE TABLE artifacts (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(255) REFERENCES jobs(id),
    artifact_type VARCHAR(100) NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER,
    checksum VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Audit events table
CREATE TABLE audit_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    user_id VARCHAR(255),
    job_id VARCHAR(255),
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    event_data JSONB,
    ip_address INET,
    user_agent TEXT
);

-- Checkpoints table (DLT metadata)
CREATE TABLE checkpoints (
    id SERIAL PRIMARY KEY,
    checkpoint_id VARCHAR(255) UNIQUE NOT NULL,
    job_id VARCHAR(255) REFERENCES jobs(id),
    dlt_type VARCHAR(50) NOT NULL,
    transaction_hash VARCHAR(255),
    block_number BIGINT,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 6.2 Read Replica Access (Reporting)

For analytics and reporting, connect to read replicas:

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Read replica connection
replica_engine = create_async_engine(
    'postgresql+asyncpg://reader:password@codefactory-db-replica:5432/codefactory',
    pool_size=20,
    max_overflow=10,
    echo=False
)

AsyncReplicaSession = sessionmaker(
    replica_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Query example
async def get_job_statistics(start_date, end_date):
    async with AsyncReplicaSession() as session:
        query = """
        SELECT
            status,
            COUNT(*) as count,
            AVG(EXTRACT(EPOCH FROM (completed_at - created_at))) as avg_duration_seconds
        FROM jobs
        WHERE created_at BETWEEN :start_date AND :end_date
        GROUP BY status
        """
        result = await session.execute(query, {
            'start_date': start_date,
            'end_date': end_date
        })
        return result.fetchall()
```

### 6.3 Citus Integration (Distributed SQL)

For large-scale deployments, Code Factory uses PostgreSQL with Citus extension for horizontal scaling:

```sql
-- Enable Citus extension
CREATE EXTENSION citus;

-- Distribute tables across nodes
SELECT create_distributed_table('jobs', 'id');
SELECT create_distributed_table('artifacts', 'job_id');
SELECT create_distributed_table('audit_events', 'job_id');

-- Add worker nodes
SELECT * from citus_add_node('worker1.codefactory.internal', 5432);
SELECT * from citus_add_node('worker2.codefactory.internal', 5432);
SELECT * from citus_add_node('worker3.codefactory.internal', 5432);

-- Rebalance data
SELECT rebalance_table_shards('jobs');
```

---

## 7. Security & Authentication Integration

### 7.1 Enterprise SSO Integration

#### SAML 2.0 Integration

```python
# SAML configuration for Code Factory
{
    "saml": {
        "idp": {
            "entity_id": "https://idp.yourcompany.com",
            "sso_url": "https://idp.yourcompany.com/saml/sso",
            "slo_url": "https://idp.yourcompany.com/saml/slo",
            "x509_cert": "MIIDXTCCAkWgAwIBAgIJAKZ..."
        },
        "sp": {
            "entity_id": "https://codefactory.yourcompany.com",
            "assertion_consumer_service_url": "https://codefactory.yourcompany.com/saml/acs",
            "single_logout_service_url": "https://codefactory.yourcompany.com/saml/sls",
            "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        },
        "security": {
            "want_assertions_signed": true,
            "want_messages_signed": true,
            "signature_algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
        },
        "attribute_mapping": {
            "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
            "first_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
            "last_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
            "groups": "http://schemas.xmlsoap.org/claims/Group"
        }
    }
}
```

#### OpenID Connect (OIDC) Integration

```python
# OIDC configuration
{
    "oidc": {
        "issuer": "https://auth.yourcompany.com",
        "authorization_endpoint": "https://auth.yourcompany.com/oauth2/authorize",
        "token_endpoint": "https://auth.yourcompany.com/oauth2/token",
        "userinfo_endpoint": "https://auth.yourcompany.com/oauth2/userinfo",
        "jwks_uri": "https://auth.yourcompany.com/oauth2/v1/keys",
        "client_id": "codefactory-production",
        "client_secret": "client-secret-here",
        "redirect_uri": "https://codefactory.yourcompany.com/oidc/callback",
        "scope": "openid profile email groups",
        "response_type": "code",
        "response_mode": "form_post"
    }
}
```

### 7.2 Mutual TLS (mTLS) for Service-to-Service

```bash
# Generate client certificate
openssl req -new -x509 -days 365 -key client.key -out client.crt \
  -subj "/CN=banking-system.yourcompany.com"

# Configure Code Factory to require client certificates
export API_MTLS_ENABLED=true
export API_MTLS_CA_CERT=/path/to/ca.crt
export API_MTLS_VERIFY_MODE=CERT_REQUIRED

# Make API request with client certificate
curl -X GET https://codefactory.yourcompany.com/api/jobs/ \
  --cert client.crt \
  --key client.key \
  --cacert ca.crt
```

### 7.3 Secret Management Integration

#### HashiCorp Vault

```python
import hvac

# Initialize Vault client
client = hvac.Client(
    url='https://vault.yourcompany.com:8200',
    token='vault-token-here'
)

# Store Code Factory API credentials in Vault
client.secrets.kv.v2.create_or_update_secret(
    path='codefactory/api-credentials',
    secret={
        'api_key': 'sk_live_abc123...',
        'client_id': 'client-id-here',
        'client_secret': 'client-secret-here'
    }
)

# Retrieve credentials
credentials = client.secrets.kv.v2.read_secret_version(
    path='codefactory/api-credentials'
)['data']['data']

api_key = credentials['api_key']
```

#### AWS Secrets Manager

```python
import boto3
import json

# Create Secrets Manager client
client = boto3.client('secretsmanager', region_name='us-east-1')

# Store Code Factory credentials
client.create_secret(
    Name='prod/codefactory/api-credentials',
    SecretString=json.dumps({
        'api_key': 'sk_live_abc123...',
        'client_id': 'client-id-here',
        'client_secret': 'client-secret-here'
    })
)

# Retrieve credentials
response = client.get_secret_value(SecretId='prod/codefactory/api-credentials')
credentials = json.loads(response['SecretString'])
```

#### Azure Key Vault

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Initialize Key Vault client
credential = DefaultAzureCredential()
vault_url = "https://yourcompany-keyvault.vault.azure.net/"
client = SecretClient(vault_url=vault_url, credential=credential)

# Store Code Factory credentials
client.set_secret("codefactory-api-key", "sk_live_abc123...")
client.set_secret("codefactory-client-id", "client-id-here")
client.set_secret("codefactory-client-secret", "client-secret-here")

# Retrieve credentials
api_key = client.get_secret("codefactory-api-key").value
```

---

## 8. Blockchain (DLT) Integration

### 8.1 Hyperledger Fabric Integration

#### Architecture

```
Code Factory SFE
        ↓
Fabric Client (REST/SDK)
        ↓
Fabric Peer Node
        ↓
Ordering Service (Raft/Kafka)
        ↓
Ledger Storage (CouchDB/LevelDB)
```

#### Configuration

```python
# Fabric configuration
{
    "fabric": {
        "network_id": "codefactory-network",
        "channel_name": "audit-channel",
        "chaincode_name": "checkpoint-chaincode",
        "chaincode_version": "1.0",
        "organization": "BankOrg",
        "peer": {
            "endpoint": "grpcs://peer0.bank.com:7051",
            "tls_ca_cert": "/path/to/peer-ca.pem",
            "tls_client_cert": "/path/to/client-cert.pem",
            "tls_client_key": "/path/to/client-key.pem"
        },
        "orderer": {
            "endpoint": "grpcs://orderer.bank.com:7050",
            "tls_ca_cert": "/path/to/orderer-ca.pem"
        },
        "msp": {
            "id": "BankOrgMSP",
            "cert_path": "/path/to/signcerts/cert.pem",
            "key_path": "/path/to/keystore/key.pem"
        }
    }
}
```

#### Query Checkpoints

```python
import requests

# Query checkpoint via REST gateway
response = requests.get(
    'https://fabric-gateway.yourcompany.com/api/v1/channels/audit-channel/chaincodes/checkpoint-chaincode',
    params={
        'fcn': 'GetCheckpoint',
        'args': ['checkpoint-2026-02-11-001']
    },
    headers={
        'Authorization': 'Bearer fabric-token',
        'X-Org-ID': 'BankOrg'
    },
    cert=('client-cert.pem', 'client-key.pem'),
    verify='ca-cert.pem'
)

checkpoint = response.json()
print(f"Checkpoint hash: {checkpoint['hash']}")
print(f"Agent ID: {checkpoint['agentId']}")
print(f"Timestamp: {checkpoint['timestamp']}")
```

### 8.2 Ethereum/EVM Integration

#### Configuration

```python
# EVM configuration
{
    "evm": {
        "provider_url": "https://mainnet.infura.io/v3/YOUR_INFURA_PROJECT_ID",
        "chain_id": 1,  # Mainnet
        "contract_address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb",
        "private_key": "0x...",  # From secure vault
        "gas_price_strategy": "medium",
        "confirmation_blocks": 12
    }
}
```

#### Query Checkpoints via Web3

```python
from web3 import Web3

# Connect to Ethereum node
w3 = Web3(Web3.HTTPProvider('https://mainnet.infura.io/v3/YOUR_PROJECT_ID'))

# Load contract ABI
with open('CheckpointContract_abi.json') as f:
    contract_abi = json.load(f)

# Initialize contract
contract_address = '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb'
contract = w3.eth.contract(address=contract_address, abi=contract_abi)

# Query checkpoint
checkpoint_id = 'checkpoint-2026-02-11-001'
checkpoint = contract.functions.getCheckpoint(checkpoint_id).call()

print(f"Checkpoint ID: {checkpoint[0]}")
print(f"Hash: {checkpoint[1]}")
print(f"Metadata: {checkpoint[2]}")
print(f"Timestamp: {checkpoint[3]}")
print(f"Creator: {checkpoint[4]}")

# Get checkpoint history
history = contract.functions.getCheckpointHistory(checkpoint_id).call()
print(f"Total versions: {len(history)}")
```

### 8.3 Integration Benefits for Banking/Manufacturing

| Benefit | Banking Application | Manufacturing Application |
|---------|-------------------|--------------------------|
| **Immutable Audit Trail** | Regulatory compliance (SOX, Basel) | FDA 21 CFR Part 11 compliance |
| **Provenance Tracking** | Code origin verification | Supply chain traceability |
| **Tamper Detection** | Fraud prevention | Quality assurance |
| **Multi-Party Verification** | External auditor access | Multi-site collaboration |
| **Disaster Recovery** | Rollback to verified state | Production recovery |

---

## 9. Monitoring & SIEM Integration

### 9.1 Prometheus Metrics

#### Configuration

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'codefactory'
    static_configs:
      - targets: ['codefactory-api:9090']
    metrics_path: '/metrics'
    scheme: 'https'
    tls_config:
      ca_file: /path/to/ca.crt
      cert_file: /path/to/client.crt
      key_file: /path/to/client.key
```

#### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `codefactory_jobs_total` | Counter | Total jobs created |
| `codefactory_jobs_active` | Gauge | Currently active jobs |
| `codefactory_generation_duration_seconds` | Histogram | Code generation time |
| `codefactory_sfe_bugs_detected_total` | Counter | Total bugs detected |
| `codefactory_sfe_fixes_applied_total` | Counter | Total fixes applied |
| `codefactory_api_requests_total` | Counter | API request count |
| `codefactory_api_request_duration_seconds` | Histogram | API latency |
| `codefactory_kafka_messages_consumed_total` | Counter | Kafka messages processed |
| `codefactory_dlt_checkpoints_total` | Counter | DLT checkpoints created |

### 9.2 Splunk Integration

```python
import requests
import json

# Splunk HEC configuration
splunk_hec_url = 'https://splunk.yourcompany.com:8088/services/collector'
splunk_hec_token = 'splunk-hec-token-here'

# Send event to Splunk
def send_to_splunk(event):
    payload = {
        'time': event['timestamp'],
        'host': 'codefactory-production',
        'source': 'codefactory:api',
        'sourcetype': 'codefactory:event',
        'event': event
    }

    response = requests.post(
        splunk_hec_url,
        headers={
            'Authorization': f'Splunk {splunk_hec_token}'
        },
        json=payload,
        verify='/path/to/splunk-ca.crt'
    )

    return response.status_code == 200

# Example: Send job completion event
send_to_splunk({
    'timestamp': '2026-02-11T22:00:00Z',
    'event_type': 'job_completed',
    'job_id': 'job-20260211-001',
    'duration_seconds': 330,
    'artifacts_count': 15,
    'status': 'success'
})
```

### 9.3 Azure Sentinel Integration

```python
import requests
import json
import hmac
import hashlib
import base64
from datetime import datetime

# Azure Sentinel configuration
workspace_id = 'your-workspace-id'
shared_key = 'your-shared-key'
log_type = 'CodeFactoryEvents'

def send_to_sentinel(events):
    # Build signature
    method = 'POST'
    content_type = 'application/json'
    resource = '/api/logs'
    rfc1123date = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    content_length = len(json.dumps(events))

    string_to_hash = f"{method}\n{content_length}\n{content_type}\nx-ms-date:{rfc1123date}\n{resource}"
    bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
    decoded_key = base64.b64decode(shared_key)
    encoded_hash = base64.b64encode(
        hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()
    ).decode()

    authorization = f"SharedKey {workspace_id}:{encoded_hash}"

    # Send to Azure Sentinel
    uri = f'https://{workspace_id}.ods.opinsights.azure.com{resource}?api-version=2016-04-01'

    headers = {
        'Content-Type': content_type,
        'Authorization': authorization,
        'Log-Type': log_type,
        'x-ms-date': rfc1123date
    }

    response = requests.post(uri, data=json.dumps(events), headers=headers)
    return response.status_code == 200

# Send events
send_to_sentinel([
    {
        'TimeGenerated': '2026-02-11T22:00:00Z',
        'EventType': 'SecurityVulnerabilityDetected',
        'JobId': 'job-20260211-001',
        'Severity': 'High',
        'Description': 'SQL Injection vulnerability detected',
        'File': 'PaymentController.java',
        'Line': 145
    }
])
```

### 9.4 AWS CloudWatch Integration

```python
import boto3
from datetime import datetime

# CloudWatch Logs configuration
logs_client = boto3.client('logs', region_name='us-east-1')
log_group_name = '/aws/codefactory/production'
log_stream_name = 'api-events'

def send_to_cloudwatch(events):
    # Prepare log events
    log_events = [
        {
            'timestamp': int(datetime.fromisoformat(event['timestamp']).timestamp() * 1000),
            'message': json.dumps(event)
        }
        for event in events
    ]

    # Send to CloudWatch
    response = logs_client.put_log_events(
        logGroupName=log_group_name,
        logStreamName=log_stream_name,
        logEvents=log_events
    )

    return response['ResponseMetadata']['HTTPStatusCode'] == 200

# Send events
send_to_cloudwatch([
    {
        'timestamp': '2026-02-11T22:00:00Z',
        'event_type': 'job_completed',
        'job_id': 'job-20260211-001',
        'status': 'success'
    }
])
```

---

## 10. Deployment Integration

### 10.1 Docker Deployment

#### Docker Compose Integration

```yaml
# docker-compose.yml - External system integration
version: '3.8'

services:
  # Your existing services
  banking-core:
    image: yourcompany/banking-core:latest
    ports:
      - "8080:8080"
    environment:
      - CODE_FACTORY_API_URL=http://codefactory-api:8000
      - CODE_FACTORY_API_KEY=${CODE_FACTORY_API_KEY}
    depends_on:
      - codefactory-api
    networks:
      - banking-network

  # Code Factory Platform
  codefactory-api:
    image: novatraxlabs/codefactory:1.0.0
    ports:
      - "8000:8000"
    environment:
      - APP_ENV=production
      - DATABASE_URL=postgresql://user:pass@postgres:5432/codefactory
      - REDIS_URL=redis://redis:6379/0
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
    depends_on:
      - postgres
      - redis
      - kafka
    networks:
      - banking-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  postgres:
    image: citusdata/citus:12.1
    environment:
      - POSTGRES_DB=codefactory
      - POSTGRES_USER=codefactory
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - banking-network

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data
    networks:
      - banking-network

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    environment:
      - KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181
      - KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092
      - KAFKA_AUTO_CREATE_TOPICS_ENABLE=true
    depends_on:
      - zookeeper
    networks:
      - banking-network

  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      - ZOOKEEPER_CLIENT_PORT=2181
    networks:
      - banking-network

networks:
  banking-network:
    driver: bridge

volumes:
  postgres-data:
  redis-data:
```

### 10.2 Kubernetes Deployment

#### Namespace Isolation

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: codefactory-production
  labels:
    environment: production
    compliance: pci-dss
```

#### Deployment with Resource Limits

```yaml
# codefactory-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: codefactory-api
  namespace: codefactory-production
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: codefactory-api
  template:
    metadata:
      labels:
        app: codefactory-api
        version: v1.0.0
    spec:
      serviceAccountName: codefactory-sa
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
      - name: api
        image: novatraxlabs/codefactory:1.0.0
        ports:
        - containerPort: 8000
          name: http
        - containerPort: 9090
          name: metrics
        env:
        - name: APP_ENV
          value: "production"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: codefactory-secrets
              key: database-url
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: codefactory-secrets
              key: redis-url
        - name: KAFKA_BOOTSTRAP_SERVERS
          value: "kafka.kafka-system.svc.cluster.local:9092"
        - name: ENCRYPTION_KEY
          valueFrom:
            secretKeyRef:
              name: codefactory-secrets
              key: encryption-key
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 30
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 3
        volumeMounts:
        - name: workspace
          mountPath: /app/workspace
        - name: uploads
          mountPath: /app/uploads
      volumes:
      - name: workspace
        persistentVolumeClaim:
          claimName: codefactory-workspace-pvc
      - name: uploads
        persistentVolumeClaim:
          claimName: codefactory-uploads-pvc
```

#### Service with Network Policy

```yaml
# codefactory-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: codefactory-api
  namespace: codefactory-production
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "9090"
spec:
  type: ClusterIP
  selector:
    app: codefactory-api
  ports:
  - name: http
    port: 80
    targetPort: 8000
  - name: metrics
    port: 9090
    targetPort: 9090

---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: codefactory-api-netpol
  namespace: codefactory-production
spec:
  podSelector:
    matchLabels:
      app: codefactory-api
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: banking-system
    ports:
    - protocol: TCP
      port: 8000
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: postgres
    ports:
    - protocol: TCP
      port: 5432
  - to:
    - podSelector:
        matchLabels:
          app: redis
    ports:
    - protocol: TCP
      port: 6379
  - to:
    - namespaceSelector:
        matchLabels:
          name: kafka-system
    ports:
    - protocol: TCP
      port: 9092
```

#### Ingress with TLS

```yaml
# codefactory-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: codefactory-ingress
  namespace: codefactory-production
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/limit-rps: "100"
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
spec:
  tls:
  - hosts:
    - codefactory.yourcompany.com
    secretName: codefactory-tls
  rules:
  - host: codefactory.yourcompany.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: codefactory-api
            port:
              number: 80
```

### 10.3 Helm Chart Integration

```yaml
# values.yaml - Custom values for your deployment
replicaCount: 3

image:
  repository: novatraxlabs/codefactory
  tag: "1.0.0"
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: codefactory.yourcompany.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codefactory-tls
      hosts:
        - codefactory.yourcompany.com

resources:
  requests:
    memory: 1Gi
    cpu: 500m
  limits:
    memory: 4Gi
    cpu: 2000m

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80

postgresql:
  enabled: true
  auth:
    username: codefactory
    password: secure-password
    database: codefactory

redis:
  enabled: true
  auth:
    password: secure-password

kafka:
  enabled: true
  replicaCount: 3

# Integration-specific configuration
config:
  app_env: production
  log_level: INFO
  jwt_expiry_minutes: 60
  rate_limit_per_second: 100

  # Banking-specific settings
  compliance:
    frameworks:
      - PCI-DSS
      - SOC2
      - GLBA
    audit_retention_days: 2555

  security:
    password_min_length: 16
    mfa_required: true
    session_timeout_minutes: 15

  # SIEM integration
  siem:
    enabled: true
    type: splunk
    hec_url: https://splunk.yourcompany.com:8088
    hec_token_secret: splunk-hec-token
```

#### Deploy with Helm

```bash
# Add Code Factory Helm repository
helm repo add codefactory https://charts.novatraxlabs.com
helm repo update

# Install Code Factory
helm install codefactory codefactory/codefactory \
  --namespace codefactory-production \
  --create-namespace \
  --values values.yaml \
  --set postgresql.auth.password=secure-db-password \
  --set redis.auth.password=secure-redis-password

# Upgrade existing deployment
helm upgrade codefactory codefactory/codefactory \
  --namespace codefactory-production \
  --values values.yaml \
  --reuse-values

# Check deployment status
helm status codefactory -n codefactory-production

# Rollback if needed
helm rollback codefactory -n codefactory-production
```

---

## 11. Integration Best Practices

### 11.1 Security Best Practices

1. **API Key Rotation**
   - Rotate API keys every 90 days
   - Use separate keys for dev/staging/production
   - Revoke keys immediately on suspected compromise

2. **Network Segmentation**
   - Deploy Code Factory in isolated network segment
   - Use firewall rules to restrict access
   - Implement zero-trust network architecture

3. **Encryption Everywhere**
   - TLS 1.3 for all API communication
   - Encrypt sensitive data at rest (AES-256-GCM)
   - Use secure key management (Vault, KMS)

4. **Least Privilege**
   - Grant minimum necessary permissions
   - Use service accounts with scoped access
   - Regular access reviews and audits

5. **Audit Logging**
   - Enable comprehensive audit logging
   - Ship logs to SIEM in real-time
   - Retain logs per regulatory requirements

### 11.2 Performance Best Practices

1. **Connection Pooling**
   - Use connection pools for database, Redis, Kafka
   - Configure appropriate pool sizes (50-100 connections)
   - Monitor connection usage and adjust

2. **Caching**
   - Cache frequently accessed data in Redis
   - Use CDN for static artifacts
   - Implement cache invalidation strategies

3. **Rate Limiting**
   - Implement rate limiting per client/API key
   - Use token bucket or leaky bucket algorithms
   - Return 429 status with Retry-After header

4. **Asynchronous Processing**
   - Use Kafka for long-running operations
   - Implement request-response patterns
   - Provide job status polling endpoints

5. **Resource Management**
   - Set appropriate resource limits in Kubernetes
   - Use horizontal pod autoscaling (HPA)
   - Monitor and optimize resource usage

### 11.3 Reliability Best Practices

1. **Health Checks**
   - Implement comprehensive health checks
   - Check dependencies (database, Redis, Kafka)
   - Return detailed health status

2. **Circuit Breakers**
   - Use circuit breakers for external dependencies
   - Configure appropriate thresholds
   - Implement graceful degradation

3. **Retries with Backoff**
   - Retry transient failures with exponential backoff
   - Set maximum retry limits
   - Use jitter to prevent thundering herd

4. **Monitoring and Alerting**
   - Monitor key metrics (latency, error rate, throughput)
   - Set up alerts for anomalies
   - Implement on-call rotation

5. **Disaster Recovery**
   - Regular database backups
   - Test restoration procedures
   - Document runbooks for common issues

### 11.4 Compliance Best Practices

1. **Data Classification**
   - Classify data by sensitivity level
   - Apply appropriate controls per classification
   - Document data flows

2. **Access Control**
   - Implement RBAC for all operations
   - Use MFA for privileged accounts
   - Regular access reviews

3. **Audit Trail**
   - Maintain immutable audit logs
   - Use blockchain for critical events
   - Provide audit reports for regulators

4. **Compliance Automation**
   - Automate compliance checks in CI/CD
   - Generate compliance reports automatically
   - Track compliance posture over time

5. **Vendor Management**
   - Review Code Factory's compliance certifications
   - Include in vendor risk assessments
   - Maintain vendor contact for incidents

---

## 12. Troubleshooting & Support

### 12.1 Common Integration Issues

#### Issue: API Authentication Failures

**Symptoms:**
- 401 Unauthorized responses
- "Invalid token" errors

**Resolution:**
```bash
# Check token expiry
jwt decode $TOKEN

# Verify API key is correct
curl -X GET https://codefactory.yourcompany.com/api/jobs/ \
  -H "X-API-Key: $API_KEY" \
  -v

# Regenerate API key if needed
curl -X POST https://codefactory.yourcompany.com/api/api-keys/regenerate \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"key_id": "ak_1234567890"}'
```

#### Issue: Kafka Connection Failures

**Symptoms:**
- "Failed to connect to Kafka broker" errors
- Message delivery timeouts

**Resolution:**
```bash
# Check Kafka connectivity
telnet kafka.yourcompany.com 9092

# Verify Kafka topics exist
kafka-topics.sh --bootstrap-server kafka:9092 --list

# Check consumer group lag
kafka-consumer-groups.sh --bootstrap-server kafka:9092 --group codefactory-consumers --describe

# Review Kafka configuration
echo $KAFKA_BOOTSTRAP_SERVERS
echo $KAFKA_SECURITY_PROTOCOL
```

#### Issue: Database Connection Pool Exhaustion

**Symptoms:**
- "Connection pool exhausted" errors
- Slow API responses

**Resolution:**
```sql
-- Check active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'codefactory';

-- Kill idle connections
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'codefactory'
AND state = 'idle'
AND state_change < now() - interval '10 minutes';

-- Increase pool size (environment variable)
export DB_POOL_SIZE=100
export DB_POOL_MAX_OVERFLOW=50
```

#### Issue: DLT Checkpoint Failures

**Symptoms:**
- "Failed to record checkpoint" errors
- Missing blockchain transactions

**Resolution:**
```python
# Check DLT connectivity
import requests

response = requests.get('https://fabric-gateway.yourcompany.com/health')
print(response.json())

# Verify DLT credentials
# For Fabric:
openssl x509 -in client-cert.pem -text -noout

# For EVM:
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://mainnet.infura.io/v3/PROJECT_ID'))
print(w3.isConnected())
print(w3.eth.get_balance('YOUR_ACCOUNT_ADDRESS'))

# Enable DLT debug logging
export DLT_LOG_LEVEL=DEBUG
```

### 12.2 Performance Troubleshooting

```bash
# Check API latency
curl -w "@curl-format.txt" -o /dev/null -s https://codefactory.yourcompany.com/api/jobs/

# curl-format.txt:
# time_namelookup:  %{time_namelookup}\n
# time_connect:  %{time_connect}\n
# time_appconnect:  %{time_appconnect}\n
# time_pretransfer:  %{time_pretransfer}\n
# time_redirect:  %{time_redirect}\n
# time_starttransfer:  %{time_starttransfer}\n
# ----------\n
# time_total:  %{time_total}\n

# Check database query performance
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;

# Check Redis latency
redis-cli --latency -h redis.yourcompany.com

# Check Kafka consumer lag
kafka-consumer-groups.sh --bootstrap-server kafka:9092 \
  --group codefactory-consumers \
  --describe \
  | grep -v "LAG" | awk '{print $1, $6}' | sort -k2 -rn | head -10
```

### 12.3 Security Incident Response

#### Suspected API Key Compromise

```bash
# Step 1: Immediately revoke compromised key
curl -X DELETE https://codefactory.yourcompany.com/api/api-keys/ak_compromised \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Step 2: Audit recent usage
curl -X GET "https://codefactory.yourcompany.com/api/audit?api_key=ak_compromised&since=24h" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Step 3: Generate new key
curl -X POST https://codefactory.yourcompany.com/api/api-keys/ \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"name": "Production Integration (Replacement)", "scopes": [...]}'

# Step 4: Update all systems with new key
# Step 5: File incident report
```

#### Data Breach Response

1. **Immediate Actions:**
   - Isolate affected systems
   - Revoke all access tokens
   - Enable enhanced logging
   - Notify security team

2. **Investigation:**
   - Review audit logs
   - Identify scope of breach
   - Determine data accessed
   - Check DLT for unauthorized changes

3. **Remediation:**
   - Patch vulnerabilities
   - Rotate all credentials
   - Update security controls
   - Document lessons learned

4. **Notification:**
   - Notify affected parties
   - Report to regulators (if required)
   - Update incident response plan

### 12.4 Support Resources

#### Documentation

- **Platform Documentation**: https://docs.novatraxlabs.com
- **API Reference**: https://api-docs.novatraxlabs.com
- **GitHub Repository**: https://github.com/musicmonk42/The_Code_Factory_Working_V2

#### Support Channels

- **Email**: support@novatraxlabs.com
- **Enterprise Support**: enterprise-support@novatraxlabs.com
- **Phone**: +1-XXX-XXX-XXXX (24/7 for enterprise)
- **Slack**: #codefactory-support (enterprise customers)

#### Service Level Agreements (SLA)

| Severity | Response Time | Resolution Time | Availability |
|----------|--------------|----------------|--------------|
| **Critical** | 15 minutes | 4 hours | 24/7 |
| **High** | 1 hour | 8 hours | 24/7 |
| **Medium** | 4 hours | 24 hours | Business hours |
| **Low** | 8 hours | 72 hours | Business hours |

#### Professional Services

- **Implementation Services**: Architecture design, deployment, integration
- **Training Services**: Developer training, admin training, best practices
- **Consulting Services**: Compliance consulting, performance optimization
- **Managed Services**: 24/7 operations, monitoring, incident response

---

## Appendix A: API Schema Reference

See `openapi_schema.json` for complete API specification.

---

## Appendix B: Configuration Examples

Complete configuration examples are available in the repository:
- `/k8s/` - Kubernetes manifests
- `/helm/codefactory/` - Helm chart
- `/docker-compose.yml` - Docker Compose
- `/.env.example` - Environment variables

---

## Appendix C: Compliance Mapping

| Regulation | Required Controls | Code Factory Features |
|------------|------------------|----------------------|
| **PCI-DSS v4.0** | Encryption, Access Control, Audit Logs | ✓ AES-256-GCM, RBAC, Tamper-evident logs |
| **SOC 2 Type II** | Security, Availability, Integrity | ✓ Multi-layer security, HA, Checksums |
| **ISO 27001** | ISMS, Risk Management | ✓ Security config, Vulnerability scanning |
| **HIPAA** | PHI Protection, Audit | ✓ PII redaction, Comprehensive audit |
| **GDPR** | Data Privacy, Right to erasure | ✓ PII detection, Data deletion APIs |
| **GLBA** | Financial Data Security | ✓ Encryption, Access controls |
| **IEC 62443** | OT Security, Network Segmentation | ✓ Deployment zones, Protocol security |

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-15 | Engineering Team | Initial release |
| 2.0 | 2026-02-11 | Engineering Team | Added banking/manufacturing sections, expanded integration patterns |

---

**End of Document**
