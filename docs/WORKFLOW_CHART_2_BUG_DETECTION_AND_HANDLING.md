# Workflow Chart 2: Bug Detection and Handling

## Overview

This document provides a comprehensive workflow chart showing how The Code Factory detects bugs in integrated systems and handles them through the Self-Fixing Engineer (SFE) module. This workflow is based on the actual code implementation in the repository.

---

## Bug Detection and Handling Workflow Diagram

```mermaid
flowchart TD
    subgraph INTEGRATED_SYSTEM["🔵 INTEGRATED SYSTEM WITH BUG"]
        A1["🔴 External System<br/>Integrated with Code Factory"]
        A2["⚠️ Bug Occurs<br/>(Runtime Error, Logic Error,<br/>Security Vulnerability)"]
        A1 --> A2
        
        A3["📤 Error Event Generated<br/>via Event Bus or Direct API"]
        A2 --> A3
    end

    subgraph DETECTION_LAYER["🟢 BUG DETECTION LAYER"]
        subgraph MONITORING["Real-Time Monitoring (arbiter/)"]
            B1["Event Bus<br/>(mesh/event_bus.py)<br/>Redis Streams / Pub-Sub"]
            A3 --> B1
            
            B2["Prometheus Metrics<br/>(arbiter/metrics.py)<br/>get_or_create_counter()<br/>get_or_create_gauge()<br/>get_or_create_summary()"]
            B1 --> B2
            
            B3["OpenTelemetry Tracing<br/>(arbiter/otel_config.py)"]
            B1 --> B3
            
            B4["Structured Logging<br/>(arbiter/logging_utils.py)<br/>PIIRedactorFilter"]
            B1 --> B4
        end
        
        subgraph DETECTION_METHODS["Detection Methods"]
            B5["Test Failure Detection<br/>(CI/CD pipeline)"]
            B6["Runtime Exception<br/>Monitoring"]
            B7["Code Health Metrics<br/>(envs/code_health_env.py)"]
            B8["Security Scan Alerts<br/>(security_audit.py)"]
            
            B2 --> B5
            B2 --> B6
            B2 --> B7
            B3 --> B8
        end
        
        B9["🚨 Bug Alert Triggered<br/>BUG_REPORT Counter incremented"]
        B5 --> B9
        B6 --> B9
        B7 --> B9
        B8 --> B9
    end

    subgraph ARBITER_CORE["🟣 ARBITER: CENTRAL CONTROL"]
        C1["Arbiter Receives Alert<br/>(arbiter/arbiter.py)"]
        B9 --> C1
        
        C2["MyArbiterConfig<br/>(Pydantic BaseSettings)<br/>- DATABASE_URL<br/>- REDIS_URL<br/>- ENCRYPTION_KEY<br/>- ENABLE_CRITICAL_FAILURES"]
        C1 --> C2
        
        C3{Bug Severity<br/>Assessment<br/>(utils.py: Severity)}
        C2 --> C3
        
        C4["🔴 CRITICAL"]
        C5["🟠 HIGH"]
        C6["🟡 MEDIUM"]
        C7["🟢 LOW"]
        
        C3 --> C4
        C3 --> C5
        C3 --> C6
        C3 --> C7
        
        C8["Priority Queue Assignment<br/>Based on Settings"]
        C4 --> C8
        C5 --> C8
        C6 --> C8
        C7 --> C8
        
        C9["Arbiter Plugin Registry<br/>(arbiter/arbiter_plugin_registry.py)<br/>PlugInKind enum"]
        C8 --> C9
    end

    subgraph BUG_MANAGER["🟠 BUG MANAGER (arbiter/bug_manager/)"]
        D1["BugManager Class<br/>(bug_manager/bug_manager.py)"]
        C9 --> D1
        
        D2["Settings Validation<br/>(Settings BaseModel)<br/>- DEBUG_MODE<br/>- AUTO_FIX_ENABLED<br/>- ML_REMEDIATION_ENABLED<br/>- RATE_LIMIT_ENABLED"]
        D1 --> D2
        
        D3["RateLimiter Check<br/>(if RATE_LIMIT_ENABLED)"]
        D2 --> D3
        
        D4{Rate Limit<br/>Exceeded?}
        D3 --> D4
        
        D4 --> |"Yes"| D5["BUG_RATE_LIMITED<br/>Counter incremented<br/>RateLimitExceededError"]
        D4 --> |"No"| D6["Process Bug Report"]
        
        D6 --> D7["validate_input_details()<br/>(bug_manager/utils.py)"]
        
        D7 --> D8["redact_pii()<br/>Remove sensitive data"]
        
        D8 --> D9["BUG_CURRENT_ACTIVE_REPORTS<br/>Gauge incremented"]
    end

    subgraph ANALYSIS_PHASE["🔵 ANALYSIS PHASE"]
        D9 --> E1["CodebaseAnalyzer<br/>(arbiter/codebase_analyzer.py)"]
        
        E1 --> E2["AST Parsing<br/>(Python ast module)"]
        E2 --> E3["File Pattern Matching<br/>(fnmatch, glob)"]
        E3 --> E4["Dependency Analysis<br/>(toml, yaml parsing)"]
        E4 --> E5["Import Graph Analysis"]
        
        E5 --> E6["BUG_PROCESSING_DURATION_SECONDS<br/>Histogram recording"]
        
        E6 --> E7["📋 Bug Analysis Report<br/>Generated"]
    end

    subgraph ROOT_CAUSE["🔴 ROOT CAUSE IDENTIFICATION"]
        E7 --> F1["Explainable Reasoner<br/>(arbiter/explainable_reasoner/)"]
        
        F1 --> F2["Knowledge Graph Query<br/>(arbiter/knowledge_graph/)"]
        
        F2 --> F3["Historical Pattern Matching<br/>via PostgresClient"]
        
        F3 --> F4["Decision Optimizer<br/>(arbiter/decision_optimizer.py)"]
        
        F4 --> F5["🎯 Root Cause Identified"]
    end

    subgraph FIX_GENERATION["🟡 FIX GENERATION PHASE"]
        F5 --> G1{AUTO_FIX_ENABLED<br/>in Settings?}
        
        G1 --> |"Yes"| G2["Self-Healing Import Fixer<br/>(self_healing_import_fixer/)"]
        G1 --> |"No"| G10["Skip Auto-Fix<br/>Notify Only"]
        
        G2 --> G3{Fix Type<br/>Required?}
        
        G3 --> |"Import Issue"| G4["Import Fixer<br/>(import_fixer/ directory)"]
        G3 --> |"Dependency Issue"| G5["Analyzer<br/>(analyzer/ directory)"]
        G3 --> |"ML-Based Fix"| G6["MLRemediationModel<br/>(remediations.py)"]
        
        G4 --> G7["BugFixerRegistry<br/>(remediations.py)"]
        G5 --> G7
        G6 --> G7
        
        G7 --> G8["BUG_AUTO_FIX_ATTEMPT<br/>Counter incremented"]
        
        G8 --> G9["🔧 Proposed Fix Generated"]
    end

    subgraph VALIDATION_PHASE["🔵 FIX VALIDATION & TESTING"]
        G9 --> H1["Sandbox Environment<br/>(simulation/sandbox.py)<br/>Multi-backend support:<br/>- Docker<br/>- Podman<br/>- Kubernetes<br/>- Native local"]
        
        H1 --> H2["SandboxPolicy applied:<br/>- network_disabled<br/>- allow_write<br/>- AppArmor profiles<br/>- seccomp profiles"]
        
        H2 --> H3["run_in_sandbox()<br/>Apply Fix in Sandbox"]
        
        H3 --> H4["Run Tests<br/>(parallel.py)"]
        
        H4 --> H5["Compliance Check<br/>(guardrails/compliance_mapper.py)"]
        
        H5 --> H6{All Tests Pass?}
        
        H6 --> |"Yes"| H7["✅ Fix Validated<br/>BUG_AUTO_FIX_SUCCESS++"]
        
        H6 --> |"No"| H8["🔄 Iterate Fix Generation<br/>or escalate to human"]
        H8 --> G6
    end

    subgraph NOTIFICATIONS["🟢 NOTIFICATION DISPATCH"]
        H7 --> I1["NotificationService<br/>(bug_manager/notifications.py)"]
        G10 --> I1
        
        I1 --> I2["Check ENABLED_NOTIFICATION_CHANNELS<br/>('slack', 'email', 'pagerduty')"]
        
        I2 --> I3{Slack<br/>Enabled?}
        I3 --> |"Yes"| I4["Slack Notification<br/>(SLACK_WEBHOOK_URL)"]
        
        I2 --> I5{Email<br/>Enabled?}
        I5 --> |"Yes"| I6["Email Notification<br/>(EMAIL_RECIPIENTS)"]
        
        I2 --> I7{PagerDuty<br/>Enabled?}
        I7 --> |"Yes"| I8["PagerDuty Alert<br/>(PAGERDUTY_ROUTING_KEY)"]
        
        I4 --> I9["BUG_NOTIFICATION_DISPATCH<br/>Counter (channel: 'slack')"]
        I6 --> I10["BUG_NOTIFICATION_DISPATCH<br/>Counter (channel: 'email')"]
        I8 --> I11["BUG_NOTIFICATION_DISPATCH<br/>Counter (channel: 'pagerduty')"]
    end

    subgraph AUDIT_LOGGING["🔴 AUDIT & COMPLIANCE LOGGING"]
        I9 --> J1
        I10 --> J1
        I11 --> J1
        
        J1["AuditLogManager<br/>(bug_manager/audit_log.py)"]
        
        J1 --> J2{AUDIT_LOG_ENABLED<br/>in Settings?}
        
        J2 --> |"Yes"| J3["Write to Audit Log<br/>(AUDIT_LOG_FILE_PATH)"]
        
        J3 --> J4{Remote Audit<br/>Service Enabled?}
        
        J4 --> |"Yes"| J5["Send to Remote Service<br/>(REMOTE_AUDIT_SERVICE_URL)"]
        J4 --> |"No"| J6["Local Audit Only"]
        
        J5 --> J7["Dead Letter Queue<br/>(if remote fails)<br/>AUDIT_DEAD_LETTER_FILE_PATH"]
    end

    subgraph GUARDRAILS["🟠 COMPLIANCE GUARDRAILS"]
        J6 --> K1
        J7 --> K1
        
        K1["Compliance Mapper<br/>(guardrails/compliance_mapper.py)"]
        
        K1 --> K2["Audit Log<br/>(guardrails/audit_log.py)"]
        
        K2 --> K3["NIST/ISO Standards<br/>Verification"]
    end

    subgraph MESH_EVENTS["🔵 MESH EVENT SYSTEM"]
        K3 --> L1["Event Bus Publish<br/>(mesh/event_bus.py)<br/>Redis Streams"]
        
        L1 --> L2["Mesh Adapter<br/>(mesh/mesh_adapter.py)"]
        
        L2 --> L3["Checkpoint Manager<br/>(mesh/checkpoint/)"]
        
        L3 --> L4["Mesh Policy<br/>(mesh/mesh_policy.py)"]
        
        L4 --> L5["📜 Immutable Event Record"]
    end

    subgraph META_LEARNING["🟡 META-LEARNING & IMPROVEMENT"]
        L5 --> M1["Meta Learning Orchestrator<br/>(arbiter/meta_learning_orchestrator/)"]
        
        M1 --> M2["Code Health Environment<br/>(envs/code_health_env.py)<br/>Gymnasium environment"]
        
        M2 --> M3["Evolution Algorithm<br/>(envs/evolution.py)"]
        
        M3 --> M4{STABLE_BASELINES3<br/>AVAILABLE?}
        
        M4 --> |"Yes"| M5["PPO Model Training<br/>(stable_baselines3)"]
        M4 --> |"No"| M6["Skip RL Training"]
        
        M5 --> M7["Update Knowledge Graph"]
        M6 --> M7
        
        M7 --> M8["🧠 System Learns from Bug"]
    end

    subgraph FINAL_STATUS["🟢 FINAL STATUS UPDATE"]
        M8 --> N1["BUG_REPORT_SUCCESS<br/>Counter incremented"]
        
        N1 --> N2["BUG_CURRENT_ACTIVE_REPORTS<br/>Gauge decremented"]
        
        N2 --> N3["✅ BUG HANDLED<br/>Process Complete"]
    end

    %% Styling
    classDef external fill:#2196F3,stroke:#1565C0,stroke-width:2px,color:#FFFFFF;
    classDef detection fill:#4CAF50,stroke:#388E3C,stroke-width:2px,color:#FFFFFF;
    classDef arbiter fill:#9C27B0,stroke:#7B1FA2,stroke-width:2px,color:#FFFFFF;
    classDef bugmgr fill:#FF9800,stroke:#F57C00,stroke-width:2px,color:#FFFFFF;
    classDef analysis fill:#2196F3,stroke:#1565C0,stroke-width:2px,color:#FFFFFF;
    classDef rootcause fill:#F44336,stroke:#D32F2F,stroke-width:2px,color:#FFFFFF;
    classDef fix fill:#FFEB3B,stroke:#FBC02D,stroke-width:2px,color:#000000;
    classDef validation fill:#2196F3,stroke:#1565C0,stroke-width:2px,color:#FFFFFF;
    classDef notify fill:#4CAF50,stroke:#388E3C,stroke-width:2px,color:#FFFFFF;
    classDef audit fill:#F44336,stroke:#D32F2F,stroke-width:2px,color:#FFFFFF;
    classDef guardrails fill:#FF9800,stroke:#F57C00,stroke-width:2px,color:#FFFFFF;
    classDef mesh fill:#2196F3,stroke:#1565C0,stroke-width:2px,color:#FFFFFF;
    classDef learning fill:#FFEB3B,stroke:#FBC02D,stroke-width:2px,color:#000000;
    classDef final fill:#4CAF50,stroke:#388E3C,stroke-width:2px,color:#FFFFFF;
```

---

## Detailed Bug Handling Process Based on Code

### Phase 1: Bug Detection

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `mesh/event_bus.py` | Event Bus | Redis Streams with Consumer Groups for message delivery |
| 2 | `arbiter/metrics.py` | Prometheus | `get_or_create_counter()`, `get_or_create_gauge()`, `get_or_create_summary()` |
| 3 | `arbiter/otel_config.py` | OpenTelemetry | Distributed tracing |
| 4 | `arbiter/logging_utils.py` | Logging | `PIIRedactorFilter` for secure logging |

### Prometheus Metrics (from bug_manager.py)

| Metric | Type | Description |
|--------|------|-------------|
| `bug_report` | Counter | Total bug reports received (labels: severity) |
| `bug_report_success` | Counter | Successfully processed bug reports |
| `bug_report_failed` | Counter | Failed bug reports |
| `bug_auto_fix_attempt` | Counter | Automatic fix attempts |
| `bug_auto_fix_success` | Counter | Successful automatic fixes |
| `bug_notification_dispatch` | Counter | Notifications dispatched (labels: channel) |
| `bug_processing_duration_seconds` | Histogram | Bug processing duration |
| `bug_rate_limited` | Counter | Rate-limited bug reports |
| `bug_current_active_reports` | Gauge | Currently processing reports |
| `bug_notification_failed` | Counter | Failed notifications (labels: channel) |
| `bug_ml_init_failed` | Counter | ML model initialization failures |

### Phase 2: Arbiter Processing

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `arbiter/arbiter.py` | `MyArbiterConfig` | Pydantic BaseSettings for configuration |
| 2 | `arbiter/arbiter.py` | Config | DATABASE_URL, REDIS_URL, ENCRYPTION_KEY |
| 3 | `bug_manager/utils.py` | `Severity` enum | Bug severity classification |
| 4 | `arbiter/arbiter_plugin_registry.py` | `PlugInKind` | Plugin type enumeration |

### Bug Manager Settings (from bug_manager.py)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DEBUG_MODE` | bool | True | Debug mode flag |
| `AUTO_FIX_ENABLED` | bool | True | Enable automatic fixes |
| `ML_REMEDIATION_ENABLED` | bool | True | Use ML for remediation |
| `RATE_LIMIT_ENABLED` | bool | True | Enable rate limiting |
| `RATE_LIMIT_WINDOW_SECONDS` | int | 600 | Rate limit window |
| `RATE_LIMIT_MAX_REPORTS` | int | 3 | Max reports in window |
| `SLACK_WEBHOOK_URL` | str | None | Slack webhook URL |
| `EMAIL_ENABLED` | bool | False | Enable email notifications |
| `PAGERDUTY_ENABLED` | bool | False | Enable PagerDuty |
| `AUDIT_LOG_ENABLED` | bool | True | Enable audit logging |
| `BUG_MAX_CONCURRENT_REPORTS` | int | 50 | Max concurrent processing |

### Phase 3: Analysis

| Step | Code Location | Function/Class | Description |
|------|---------------|----------------|-------------|
| 1 | `arbiter/codebase_analyzer.py` | `CodebaseAnalyzer` | Full codebase scan |
| 2 | `arbiter/codebase_analyzer.py` | AST parsing | Python AST module for code analysis |
| 3 | `arbiter/codebase_analyzer.py` | fnmatch/glob | File pattern matching |
| 4 | `arbiter/codebase_analyzer.py` | toml/yaml | Dependency file parsing |

### Phase 4: Fix Generation

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `self_healing_import_fixer/` | Import Fixer | Fix import issues |
| 2 | `self_healing_import_fixer/analyzer/` | Analyzer | Analyze dependencies |
| 3 | `bug_manager/remediations.py` | `MLRemediationModel` | ML-based fix prediction |
| 4 | `bug_manager/remediations.py` | `BugFixerRegistry` | Registry of fix strategies |

### Phase 5: Validation

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `simulation/sandbox.py` | `run_in_sandbox()` | Multi-backend sandbox execution |
| 2 | `simulation/sandbox.py` | `SandboxPolicy` | Network, write, AppArmor, seccomp |
| 3 | `simulation/parallel.py` | Parallel tests | Parallel test execution |
| 4 | `guardrails/compliance_mapper.py` | Compliance | NIST/ISO verification |

### Phase 6: Notifications

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `bug_manager/notifications.py` | `NotificationService` | Multi-channel notifications |
| 2 | Settings | `ENABLED_NOTIFICATION_CHANNELS` | ('slack', 'email', 'pagerduty') |

### Phase 7: Audit & Mesh

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `bug_manager/audit_log.py` | `AuditLogManager` | Audit log management |
| 2 | `mesh/event_bus.py` | Event Bus | Redis Streams publishing |
| 3 | `mesh/mesh_adapter.py` | Mesh Adapter | Mesh communication |
| 4 | `mesh/checkpoint/` | Checkpoint Manager | State checkpointing |

### Phase 8: Meta-Learning

| Step | Code Location | Component | Description |
|------|---------------|-----------|-------------|
| 1 | `arbiter/meta_learning_orchestrator/` | Meta Learning | Learning from bugs |
| 2 | `envs/code_health_env.py` | Gymnasium Env | RL environment |
| 3 | `envs/evolution.py` | Evolution | Genetic algorithms |
| 4 | stable_baselines3 | PPO | Reinforcement learning (optional) |

---

## Key Files Reference (Verified from Code)

| Layer | Component | Actual File Path |
|-------|-----------|------------------|
| Arbiter | Main | `self_fixing_engineer/arbiter/arbiter.py` |
| Arbiter | Config | `self_fixing_engineer/arbiter/config.py` |
| Arbiter | Plugin Registry | `self_fixing_engineer/arbiter/arbiter_plugin_registry.py` |
| Arbiter | Metrics | `self_fixing_engineer/arbiter/metrics.py` |
| Arbiter | Logging | `self_fixing_engineer/arbiter/logging_utils.py` |
| Bug Manager | Main | `self_fixing_engineer/arbiter/bug_manager/bug_manager.py` |
| Bug Manager | Utils | `self_fixing_engineer/arbiter/bug_manager/utils.py` |
| Bug Manager | Notifications | `self_fixing_engineer/arbiter/bug_manager/notifications.py` |
| Bug Manager | Remediations | `self_fixing_engineer/arbiter/bug_manager/remediations.py` |
| Bug Manager | Audit Log | `self_fixing_engineer/arbiter/bug_manager/audit_log.py` |
| Analysis | Codebase Analyzer | `self_fixing_engineer/arbiter/codebase_analyzer.py` |
| Analysis | Decision Optimizer | `self_fixing_engineer/arbiter/decision_optimizer.py` |
| Analysis | Explainable Reasoner | `self_fixing_engineer/arbiter/explainable_reasoner/` |
| Analysis | Knowledge Graph | `self_fixing_engineer/arbiter/knowledge_graph/` |
| Fixer | Import Fixer | `self_fixing_engineer/self_healing_import_fixer/` |
| Simulation | Sandbox | `self_fixing_engineer/simulation/sandbox.py` |
| Simulation | Parallel | `self_fixing_engineer/simulation/parallel.py` |
| Guardrails | Compliance | `self_fixing_engineer/guardrails/compliance_mapper.py` |
| Guardrails | Audit | `self_fixing_engineer/guardrails/audit_log.py` |
| Mesh | Event Bus | `self_fixing_engineer/mesh/event_bus.py` |
| Mesh | Adapter | `self_fixing_engineer/mesh/mesh_adapter.py` |
| Mesh | Checkpoint | `self_fixing_engineer/mesh/checkpoint/` |
| Envs | Code Health | `self_fixing_engineer/envs/code_health_env.py` |
| Envs | Evolution | `self_fixing_engineer/envs/evolution.py` |
| Server | SFE Service | `server/services/sfe_service.py` |
| Server | SFE Router | `server/routers/sfe.py` |

---

## API Endpoints for Bug Management (from code)

| Endpoint | Method | Router | Description |
|----------|--------|--------|-------------|
| `/api/sfe/{job_id}/analyze` | POST | `sfe.py` | Analyze code for issues |
| `/api/sfe/{job_id}/errors` | GET | `sfe.py` | Get detected errors |
| `/api/sfe/errors/{error_id}/propose-fix` | POST | `sfe.py` | Propose a fix |
| `/api/sfe/fixes/{fix_id}/review` | POST | `sfe.py` | Review proposed fix |
| `/api/sfe/fixes/{fix_id}/apply` | POST | `sfe.py` | Apply fix |
| `/api/sfe/codebase/analyze` | POST | `sfe.py` | Full codebase analysis |
| `/api/sfe/bugs/detect` | POST | `sfe.py` | Detect bugs |
| `/api/sfe/bugs/analyze` | POST | `sfe.py` | Analyze bug |
| `/api/sfe/bugs/prioritize` | POST | `sfe.py` | Prioritize bugs |

---

*Document Version: 1.0.0 - Verified against actual code*
*Last Updated: February 2026*
