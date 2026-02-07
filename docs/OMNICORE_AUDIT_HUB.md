<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# OmniCore Engine: Central Hub Architecture for Audit Logging

## Overview

**OmniCore Engine is the platform's central hub orchestrator** for The Code Factory. All audit logs, events, and operations flow through OmniCore for centralized orchestration, coordination, and management.

## Architecture

### Central Hub Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    THE CODE FACTORY PLATFORM                     │
│                                                                  │
│  ┌────────────┐         ┌────────────┐         ┌─────────────┐ │
│  │ Generator  │         │    Self    │         │   Other     │ │
│  │  Module    │         │  Fixing    │         │  Modules    │ │
│  │            │         │  Engineer  │         │             │ │
│  └─────┬──────┘         └─────┬──────┘         └──────┬──────┘ │
│        │                      │                       │         │
│        │  Audit Events        │   Audit Events        │         │
│        │                      │                       │         │
│        └──────────────────────┼───────────────────────┘         │
│                               │                                 │
│                               ▼                                 │
│                    ┌─────────────────────┐                     │
│                    │   OMNICORE ENGINE   │                     │
│                    │  Central Hub &      │                     │
│                    │   Orchestrator      │                     │
│                    │                     │                     │
│                    │  • Ingest all logs  │                     │
│                    │  • Validate & enrich│                     │
│                    │  • Route & store    │                     │
│                    │  • Correlate events │                     │
│                    │  • Trigger alerts   │                     │
│                    │  • Manage compliance│                     │
│                    └──────────┬──────────┘                     │
│                               │                                 │
│                               ▼                                 │
│                    ┌─────────────────────┐                     │
│                    │  Unified Storage    │                     │
│                    │  • Primary          │                     │
│                    │  • Archive          │                     │
│                    │  • Hot Cache        │                     │
│                    └─────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## OmniCore's Role as Central Hub

### 1. Audit Log Orchestration

OmniCore orchestrates ALL audit log operations across the platform:

**Ingestion**: 
- Receives audit events from all modules
- Validates event structure and content
- Ensures consistent format across sources

**Processing**:
- Enriches events with context and correlation data
- Applies security and compliance rules
- Performs real-time analysis and pattern detection

**Routing**:
- Routes events to appropriate storage destinations
- Manages multi-tiered storage (hot/warm/cold)
- Coordinates with external systems (SIEM, analytics)

### 2. Cross-Module Coordination

OmniCore coordinates operations across all platform modules:

**Event Correlation**:
```yaml
# Example: Correlate events across modules
job_id: "gen_12345"

Events:
1. Generator: code_generated (job_id: gen_12345)
2. SFE: bug_detected (job_id: gen_12345)
3. SFE: fix_applied (job_id: gen_12345)
4. Generator: test_generated (job_id: gen_12345)
5. OmniCore: workflow_complete (job_id: gen_12345)

# OmniCore correlates all 5 events into a unified workflow view
```

**Workflow Orchestration**:
- Tracks end-to-end workflows across modules
- Maintains workflow state and history
- Provides unified workflow visibility

**Resource Management**:
- Coordinates storage allocation
- Manages retention policies across modules
- Optimizes resource utilization

### 3. Unified Storage Management

OmniCore manages unified storage for all audit logs:

**Multi-Tier Storage**:
```
Hot Tier (OmniCore Redis)
├─ Recent logs (last 24h)
├─ High-performance access
└─ Rapid querying

Warm Tier (OmniCore Elasticsearch)
├─ Searchable logs (last 90 days)
├─ Full-text search
└─ Analytics queries

Cold Tier (OmniCore S3/Archive)
├─ Long-term retention (7 years)
├─ Compliance storage
└─ Archival access
```

**Storage Coordination**:
- Automatic tier transitions
- Unified retention policies
- Cross-tier query support

### 4. Security and Compliance

OmniCore enforces security and compliance across the platform:

**Security Orchestration**:
- Centralized encryption key management
- Unified access control (RBAC)
- Tamper detection across all modules
- Security incident correlation

**Compliance Management**:
- SOC2, HIPAA, PCI-DSS, GDPR enforcement
- Unified retention policies
- Compliance reporting across modules
- Audit trail integrity verification

### 5. Monitoring and Alerting

OmniCore coordinates monitoring and alerting:

**Unified Monitoring**:
- Single pane of glass for all audit events
- Cross-module performance tracking
- Unified health checks

**Intelligent Alerting**:
- Correlates events for smarter alerts
- Reduces alert fatigue with de-duplication
- Routes alerts to appropriate channels
- Escalates based on severity and context

## Module Integration

### Generator Module Integration

**Configuration**: `generator/audit_config.yaml`

```yaml
# Generator routes ALL events to OmniCore
ROUTE_TO_MAIN_AUDIT: true
MAIN_AUDIT_ENDPOINT: "http://localhost:8003/log"

# OmniCore ingests from Generator
OMNICORE_INGESTION: "http://localhost:8001/audit/ingest"
```

**Event Flow**:
```
Generator Event → Generator Audit API → OmniCore Hub → Storage
                  (local processing)   (orchestration)  (unified)
```

### Self-Fixing Engineer Integration

**Configuration**: `self_fixing_engineer/audit_config.yaml`

```yaml
# All SFE sub-modules route to OmniCore
ROUTE_TO_MAIN_AUDIT: true
MAIN_AUDIT_ENDPOINT: "http://localhost:8003/log"
OMNICORE_ENDPOINT: "http://localhost:8001/audit/ingest"

# Sub-modules
arbiter:
  route_to_hub: true
test_generation:
  route_to_hub: true
simulation:
  route_to_hub: true
guardrails:
  route_to_hub: true
```

**Event Flow**:
```
SFE Event → Sub-module Audit → OmniCore Hub → Storage
            (local logging)    (orchestration) (unified)
```

### OmniCore Self-Integration

**Configuration**: `omnicore_engine/audit_config.yaml`

```yaml
# OmniCore logs its own operations
AUDIT_LOG_PATH: "./logs/omnicore_audit.jsonl"

# OmniCore orchestrates its own audit lifecycle
SELF_ORCHESTRATION: true
```

**Event Flow**:
```
OmniCore Event → OmniCore Audit → Direct Storage
                 (self-logging)    (optimized path)
```

## API Endpoints

### OmniCore Hub Endpoints

**Audit Ingestion** (Central Hub):
```
POST /audit/ingest
- Receives audit events from all modules
- Validates and enriches events
- Routes to unified storage
```

**Configuration Status**:
```
GET /audit/config/status
- Returns OmniCore audit configuration
- Shows integration status with all modules
- Displays hub health and capabilities
```

**Workflow Status**:
```
GET /workflow/status/{job_id}
- Cross-module workflow tracking
- Correlated event timeline
- Workflow health and status
```

### Module-Specific Endpoints

**Generator**:
```
GET /audit/config/status (localhost:8000)
- Generator-specific configuration
- Integration with OmniCore status
```

**OmniCore**:
```
GET /audit/config/status (localhost:8001)
- OmniCore hub configuration
- All module integration status
- Central orchestration health
```

## Configuration Files

### Routing Configuration

**File**: `audit_routing_config.yaml`

Defines how all modules route through OmniCore:

```yaml
omnicore_hub:
  role: "central_orchestrator"
  ingestion_endpoint: "http://localhost:8001/audit/ingest"
  capabilities:
    - audit_log_orchestration
    - cross_module_correlation
    - unified_storage_coordination
    - compliance_reporting

generator:
  orchestrated_by: "omnicore"
  route_all_events_to_hub: true
  hub_endpoint: "http://localhost:8001/audit/ingest"

self_fixing_engineer:
  orchestrated_by: "omnicore"
  route_all_events_to_hub: true
  hub_endpoint: "http://localhost:8001/audit/ingest"

omnicore:
  is_hub: true
  self_orchestration: true
```

### Module Configurations

**Generator**: `generator/audit_config.yaml` → `audit_config.enhanced.yaml`

**OmniCore**: `omnicore_engine/audit_config.yaml`

**SFE**: `self_fixing_engineer/audit_config.yaml`

**Routing**: `audit_routing_config.yaml`

## Benefits of Central Hub Architecture

### 1. Unified Operations

- **Single Source of Truth**: All audit data flows through one hub
- **Consistent Processing**: Same rules apply to all modules
- **Unified Access**: One API for all audit operations

### 2. Enhanced Correlation

- **Cross-Module Insights**: Correlate events across the platform
- **Workflow Tracking**: End-to-end visibility
- **Root Cause Analysis**: Trace issues across modules

### 3. Simplified Management

- **Central Configuration**: One place to configure audit policies
- **Unified Monitoring**: Single dashboard for all audit activity
- **Centralized Alerts**: Intelligent alert correlation and routing

### 4. Better Compliance

- **Consistent Enforcement**: Compliance rules applied uniformly
- **Unified Reporting**: Single compliance report for all modules
- **Audit Trail Integrity**: Centralized verification and validation

### 5. Scalability

- **Efficient Resource Use**: Shared storage and processing
- **Optimized Performance**: Intelligent routing and caching
- **Flexible Growth**: Easy to add new modules

## Implementation

### Quick Start

1. **Enable OmniCore Hub**:
```bash
# Start OmniCore (central hub)
cd omnicore_engine
python fastapi_app.py
# Hub running on localhost:8001
```

2. **Configure Modules**:
```bash
# Generator routes to OmniCore
export MAIN_AUDIT_ENDPOINT="http://localhost:8001/audit/ingest"

# SFE routes to OmniCore
export OMNICORE_ENDPOINT="http://localhost:8001/audit/ingest"
```

3. **Verify Routing**:
```bash
# Check OmniCore hub status
curl http://localhost:8001/audit/config/status

# Test event ingestion
curl -X POST http://localhost:8001/audit/ingest \
  -H "Content-Type: application/json" \
  -d '{"source_module":"test","event_type":"test_event"}'
```

### Validation

Run the integration tests:
```bash
# Test all audit configuration
python test_audit_config_integration.py

# Test API endpoints
python test_audit_config_endpoints.py
```

## Troubleshooting

### OmniCore Hub Not Receiving Events

**Check**:
1. OmniCore is running: `curl http://localhost:8001/health`
2. Routing configured: Check `audit_routing_config.yaml`
3. Endpoints accessible: Test ingestion endpoint

### Module Not Routing to Hub

**Check**:
1. Module configuration has `route_to_hub: true`
2. Hub endpoint is correct: `http://localhost:8001/audit/ingest`
3. Network connectivity between module and hub

### Events Not Appearing in Storage

**Check**:
1. OmniCore storage configuration
2. Storage backend health
3. Routing rules in `audit_routing_config.yaml`

## Documentation Links

- **Main Audit Configuration**: `docs/AUDIT_CONFIGURATION.md`
- **Web UI Access**: `docs/AUDIT_CONFIGURATION_WEB_ACCESS.md`
- **Routing Config**: `audit_routing_config.yaml`
- **OmniCore Config**: `omnicore_engine/audit_config.yaml`
- **Generator Config**: `generator/audit_config.yaml`
- **SFE Config**: `self_fixing_engineer/audit_config.yaml`

## Conclusion

OmniCore Engine serves as the **central hub orchestrator** for all audit operations in The Code Factory platform. This architecture provides:

- **Unified orchestration** of all audit events
- **Cross-module correlation** and insights
- **Centralized management** and control
- **Enhanced compliance** and security
- **Scalable and efficient** operations

All audit logs flow through OmniCore, making it the single source of truth for audit data and the central point of control for audit operations across the platform.

---

**Version**: 1.0  
**Last Updated**: February 2026  
**Status**: Production Ready
