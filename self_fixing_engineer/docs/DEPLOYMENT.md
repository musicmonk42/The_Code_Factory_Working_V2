<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Self Fixing Engineer™ Deployment Guide  
*Confidential & Proprietary — All Rights Reserved*

---

## Table of Contents

1. [Introduction & Philosophy](#introduction--philosophy)  
2. [Pre-Deployment Checklist](#pre-deployment-checklist)  
3. [Supported Environments & Requirements](#supported-environments--requirements)  
4. [CI/CD Pipeline Best Practices](#cicd-pipeline-best-practices)  
5. [Building & Running Locally](#building--running-locally)  
6. [Secure Configuration Management](#secure-configuration-management)  
7. [Component-by-Component Deployment](#component-by-component-deployment)  
    - [7.1 Arbiter Orchestrator](#71-arbiter-orchestrator)
    - [7.2 Intent Capture APIs](#72-intent-capture-apis)
    - [7.3 File Watcher (Live Reload)](#73-file-watcher-live-reload)
    - [7.4 Plugin & Adapter Services](#74-plugin--adapter-services)
    - [7.5 Database & State Stores](#75-database--state-stores)
    - [7.6 Audit Mesh / SIEM / DLT](#76-audit-mesh--siem--dlt)
8. [Scaling, High-Availability & Disaster Recovery](#scaling-high-availability--disaster-recovery)  
9. [Observability, Monitoring & Alerting](#observability-monitoring--alerting)  
10. [Production Hardening Checklist](#production-hardening-checklist)  
11. [Updates, Rollbacks & Zero-Downtime Deploys](#updates-rollbacks--zero-downtime-deploys)  
12. [Troubleshooting & Diagnostics](#troubleshooting--diagnostics)  
13. [Advanced: Multi-Cloud & Hybrid Topologies](#advanced-multi-cloud--hybrid-topologies)  
14. [Reference YAMLs & Templates](#reference-yamls--templates)  
15. [Glossary & Component Index](#glossary--component-index)  
16. [Support, SLA & Escalation](#support-sla--escalation)  
17. [Appendix: Security & Compliance Artifacts](#appendix-security--compliance-artifacts)  

---

## 1. Introduction & Philosophy

Welcome to the gold-standard deployment guide for Self Fixing Engineer™.  
This guide ensures bulletproof, auditable, and scalable deployments—whether on bare metal, VM, cloud, container, or hybrid.  
Every step prioritizes security, resilience, zero-trust, and traceability.

---

## 2. Pre-Deployment Checklist

- Signed license/EULA and onboarding package from [Your Company]
- Host(s) meet minimum resource requirements (CPU, RAM, storage, network)
- Root/admin access (or IAM) for infra and secret provisioning
- Externalized .env or vault-backed secrets
- All dependencies (Python 3.11+, Docker, Kubernetes, Postgres/Redis/etc.)
- Domain/DNS, TLS certificate (for prod)
- SIEM/monitoring and alerting pipeline ready

---

## 3. Supported Environments & Requirements

- **OS:** Ubuntu 22.04+, RHEL 9+, Amazon Linux 2, Windows Server 2022
- **Container:** Docker 24+, Docker Compose 2.20+, Kubernetes 1.25+
- **Cloud:** AWS, GCP, Azure (tested), on-prem (with equivalent infra)
- **Python:** 3.11+ (official support), 3.10 (partial), 3.12+ (in validation)
- **Minimum:** 4 CPUs, 8 GB RAM, 32 GB SSD per node (scale as crew/plugins grow)
- **Networking:** 8000-9000/tcp (API, web, gRPC), SIEM/DLT/DB ports as needed

---

## 4. CI/CD Pipeline Best Practices

- Use sample `.github/workflows/ci.yml` or adapt for GitLab/Jenkins.
- Secrets (DockerHub, Vault, AWS, etc.) must be injected via secure repo settings.
- Always run `pytest`, static analysis (`flake8`, `mypy`), and security checks in CI.
- Recommended: Use OPA (Open Policy Agent) or Snyk as a CI gate for policy compliance.
- Build Docker images with pinned dependency hashes.

---

## 5. Building & Running Locally

**Build:**  
```bash
docker build -t self-fixing-engineer .
```

**Run:**  
```bash
docker run -d -p 9000:9000 --env-file .env self-fixing-engineer
```

- Ensure `.env` is gitignored and secrets never leak to logs.
- Use `python web.py` or `python api.py` for direct dev testing.

---

## 6. Secure Configuration Management

- All sensitive keys MUST be stored in:
  - `.env` (dev/POC only—use vault in prod)
  - HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager
- Never hardcode secrets in Dockerfiles, YAML, or code.
- Sanitize all configs before production rollout.

---

## 7. Component-by-Component Deployment

### 7.1 Arbiter Orchestrator

**Standalone:**
```bash
python run_exploration.py
```

**Docker Compose:**
```yaml
services:
  arbiter:
    build: .
    command: python run_exploration.py
    env_file: .env
    ports:
      - "9000:9000"
```
- Scaling: Use `docker-compose up --scale arbiter=3` or `deploy.replicas` in Compose/Swarm.

### 7.2 Intent Capture APIs

- Deploy as dedicated service or sidecar.

**Command:**
```bash
python web.py  # or python api.py
```
- Ports: Defaults to 9000 (web) and 8000 (api).
- Kubernetes: Use Deployment and Service manifests; expose via Ingress with TLS.

### 7.3 File Watcher (Live Reload)

- For hot reload in dev:
```bash
python file_watcher.py
```
- For prod, prefer tested container image redeploy.

### 7.4 Plugin & Adapter Services

- Enable/disable plugins via YAML config.
- Run plugin containers with explicit resource limits and isolation (see [ARCHITECTURE.md]).
- Register plugins in platform config; ensure API endpoints are reachable and secured.

### 7.5 Database & State Stores

- Use Postgres 14+/Redis 7+ for shared agent state, coordination, and feedback.
- Credentials injected at runtime from vault or `.env`.
- HA: Configure replication, backup, and failover (see [Appendix]).

### 7.6 Audit Mesh / SIEM / DLT

- Integrate with SIEM (Splunk, Elastic, OpenSearch), or push audit events to Hyperledger/Ethereum/DLT of choice.
- Use cryptographic signing with your org's keys (see [Security section]).
- Enable external audit log shipping.

---

## 8. Scaling, High-Availability & Disaster Recovery

- **Horizontal scaling:** Docker Compose/Swarm, K8s Deployments, or ECS/EKS/AKS.
- **Multi-agent/crew:** Stateless agents = easy scaling.
- Use a robust, external store for session or stateful ops.
- **Failover:** Deploy in at least 2 AZs (cloud), with load balancer in front.
- **Backups:** Daily DB backups, frequent audit log exports, config snapshots.
- **Disaster Recovery:** Test rollback using audit mesh—can revert to any previous state.

---

## 9. Observability, Monitoring & Alerting

- **Health endpoints:** All APIs expose `/health`, `/metrics` (Prometheus/OpenTelemetry).
- **Logging:** Centralized shipping (Fluentd, Logstash, or direct to SIEM). Log all agent actions, policy violations, plugin activity, and failures.
- **Alerting:** PagerDuty/Opsgenie/Splunk alerts on container/app health, critical errors, audit mesh issues.
- **Dashboards:** Grafana/ELK with built-in metrics and traces.

---

## 10. Production Hardening Checklist

- All secrets/vaults confirmed (**NO hardcoded creds**)
- TLS enabled, terminated at ingress/reverse proxy
- Central log collection and retention policies enforced
- All health/metrics endpoints live and monitored
- Audit mesh signing keys securely provisioned and rotated
- Policy and config YAMLs validated and checksummed
- RBAC/IAM configured for minimal privilege
- Penetration tests and static analysis (see [Security])
- Automated container image scanning (Trivy, Clair, Snyk)

---

## 11. Updates, Rollbacks & Zero-Downtime Deploys

- **Blue/Green:** Deploy new containers, switch traffic on health pass.
- **Rolling:** Compose/K8s rolling updates; agents respawn seamlessly.
- **Rollback:** Use audit mesh for point-in-time revert (stateful and stateless flows).
- **Watcher:** For dev, `file_watcher.py` live reloads—but never in prod.

---

## 12. Troubleshooting & Diagnostics

**Logs:**
```bash
docker logs <container_id>
```

**Health:**
```bash
curl http://localhost:9000/health
```

- Check env/secrets: All required keys present in `.env`/vault.

**Common issues:**
- Port in use: Check docker-compose/K8s resource conflicts.
- DB connection error: Confirm network/firewall, creds, health.
- Plugin/adapter failure: Review logs, restart isolated service.

---

## 13. Advanced: Multi-Cloud & Hybrid Topologies

- Agents/crews can run across clouds, on-prem, and edge—connect via VPN/VPC, with encrypted tunnels.
- Audit mesh can synchronize/replicate across clouds and regions.
- State/database: Use managed cloud DB or cross-region clusters (see [Appendix]).

---

## 14. Reference YAMLs & Templates

**docker-compose.yml**
```yaml
version: "3.8"
services:
  arbiter:
    build: .
    command: python run_exploration.py
    env_file: .env
    ports: [ "9000:9000" ]
    deploy:
      replicas: 3
      resources: { limits: { cpus: "2", memory: 2G } }
  intent-api:
    build: .
    command: python api.py
    env_file: .env
    ports: [ "8000:8000" ]
  watcher:
    build: .
    command: python file_watcher.py
    env_file: .env
```

**Sample .env**
```env
POSTGRES_URI=postgresql://user:pass@dbhost:5432/self_fixer
REDIS_URI=redis://redis_host:6379/0
JWT_SECRET=supersecretvalue
AUDIT_MESH_KEY=ed25519:...base64key...
SIEM_API=https://siem.corp.com/api
```

---

## 15. Glossary & Component Index

- **Arbiter:** Agentic orchestrator (“brain”)—routes and schedules all jobs.
- **Crew:** Configurable team of agents for repair, audit, compliance, and observability.
- **Intent API:** REST/CLI endpoint for user or system input (triggers).
- **Watcher:** Live reload/deploy process (dev/test only).
- **Plugin:** Modular adapter for cloud, SIEM, DLT, or internal systems.
- **Audit Mesh:** Cryptographically-signed, append-only provenance and log layer.
- **State Store:** Database (Postgres/Redis) for persistent crew/agent state.

---

## 16. Support, SLA & Escalation

- **Enterprise SLA:** 24/7 critical support, dedicated onboarding, and quarterly pen testing.

**Contact:**
- Support: [support@yourcompany.com]
- Emergency: [emergency@yourcompany.com]
- Documentation Portal: [https://yourcompany.com/self-fixing-engineer/docs]

**Escalation:**  
All P1 incidents acknowledged in <15min, resolved or workaround in <2h.  
Automated Escalation: Policy-driven; SIEM/alert integrations escalate to on-call.

---

## 17. Appendix: Security & Compliance Artifacts

- Penetration test results (available under NDA)
- Audit mesh signing keys and rotation procedure
- Compliance mappings (SOC2, PCI, etc.)
- Container/image SBOM and scanning reports

---

*End of DEPLOYMENT.md*