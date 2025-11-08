# ⚙️ CONFIGURATION.md — Self Fixing Engineer™

---

## Table of Contents

1. [Purpose & Scope](#purpose--scope)
2. [Configuration Principles](#configuration-principles)
3. [Environment Variable Reference](#environment-variable-reference)
4. [Secrets & Key Management](#secrets--key-management)
5. [File-Based Configuration (All Artifacts)](#file-based-configuration-all-artifacts)
6. [Policy, RBAC & Security Controls](#policy-rbac--security-controls)
7. [Agent & Crew Configuration](#agent--crew-configuration)
8. [Simulation Parameters & Control](#simulation-parameters--control)
9. [CI/CD & DevOps Configuration](#cicd--devops-configuration)
10. [Runtime Overrides & Hot Reload](#runtime-overrides--hot-reload)
11. [Versioning, Audit, & Change Management](#versioning-audit--change-management)
12. [Best Practices & Anti-Patterns](#best-practices--anti-patterns)
13. [Troubleshooting & Validation](#troubleshooting--validation)
14. [File Index](#file-index)
15. [Contacts & Escalation](#contacts--escalation)

---

## 1. Purpose & Scope

Defines and documents all configuration surfaces in Self Fixing Engineer™:  
Arbiter (orchestrator), intent capture, simulation, plugins, compliance, CI/CD, and critical runtime behaviors.

Covers: Env vars, config files (JSON/YAML/Python), secret management, policy rules, CI/CD, and audit/repro controls.

**Audience:** Engineers, SREs, integrators, auditors, compliance teams, ops.

---

## 2. Configuration Principles

- **Externalize everything:** No secrets or environment-specific logic in code/images.
- **Declarative over imperative:** Use config files (YAML/JSON) for structure, Python for dynamic only if needed.
- **Least privilege:** Only required permissions and credentials—never “wide open” defaults.
- **Audit and version:** All configs version-controlled, reviewed, and auditable.
- **Hot reload & rollout:** Support for dynamic reload with no downtime (where safe).

---

## 3. Environment Variable Reference

All critical runtime secrets/config are set via environment variables or injected at container/VM start.

| Variable               | Purpose                   | Example Value                   | Required?   |
|------------------------|--------------------------|----------------------------------|-------------|
| OPENAI_API_KEY         | LLM provider API key      | sk-...                           | Yes         |
| REDIS_URL              | Task/data cache           | redis://localhost:6379           | Yes         |
| DATABASE_URL           | State/logging DB          | postgresql://user:pass@host/db   | Yes         |
| HITL_SECRET_SALT       | HITL approval crypto      | Random base64 string             | Yes         |
| AWS_ACCESS_KEY_ID      | AWS plugin integration    | AKIA...                          | Optional    |
| AWS_SECRET_ACCESS_KEY  | AWS plugin integration    | ...                              | Optional    |
| ENCRYPTION_KEY         | Agent data encryption key | Random, 32+ chars, base64        | Yes (prod)  |
| APP_ENV                | Runtime mode              | development, production          | Yes         |

**Tip:** Always use a `.env.example` (no secrets) to document expected env vars.

---

## 4. Secrets & Key Management

- Never commit secrets to version control, Docker images, or config files.
- Use `.env` only for local/dev; in prod, use enterprise vaults (HashiCorp, AWS, Azure, GCP, etc.).
- Rotate all API keys, salts, and encryption keys regularly.
- Review vault access logs and permission scope at least quarterly.

---

## 5. File-Based Configuration (All Artifacts)

| File/Path                 | Format   | Purpose                              | Example/Notes         |
|---------------------------|----------|--------------------------------------|-----------------------|
| arbiter_config.json       | JSON     | AI Orchestrator, model, backend config| See §5.1              |
| crew_config.yaml          | YAML     | Agent/crew roles & skills            | See §7                |
| policies.json             | JSON     | Policy, RBAC, resource limits, sandbox| See §6                |
| config.py                 | Python   | Dynamic config/overrides             | Use sparingly         |
| sim_config.yaml           | YAML     | Simulation, scenario, batch control  | See SIMULATION.md     |
| .github/workflows/ci.yml  | YAML     | CI/CD and workflow setup             | See §9                |

### 5.1. Example arbiter_config.json

```json
{
  "app_settings": {
    "redis_url": "redis://localhost:6379",
    "encryption_key": "your-encryption-key"
  },
  "llm": {
    "model_name": "gpt-4o-mini",
    "api_key": "your-openai-api-key"
  },
  "integrations": {
    "aws": {
      "access_key": "your-key",
      "secret_key": "your-secret"
    }
  }
}
```

---

## 6. Policy, RBAC & Security Controls

**Policy config:** `policies.json`
```json
{
  "domain_rules": {
    "user_data": {
      "allow": true,
      "max_size_kb": 100,
      "sensitive_keys": ["password", "ssn"]
    },
    "code_execution": {
      "sandboxed": true,
      "max_runtime_sec": 60
    }
  }
}
```

- Access controls: Map users/agents/plugins to least privilege roles; use allowlists over denylists.
- Sandboxing: All code/plugin execution policy-driven.
- Audit every change: Policy changes must be PR’d, reviewed, and logged.

---

## 7. Agent & Crew Configuration

**File:** `crew_config.yaml`  
**Purpose:** Define agent “crew” composition, skills, and role mapping.

**Example:**
```yaml
agents:
  - name: refactor
    role: code_refactor
    skills_ref:
      - code_analysis
      - refactoring
  - name: simulation_engine
    role: simulator
    skills_ref:
      - scenario_generation
      - monte_carlo
```
Review regularly as new agent types/skills are introduced.

---

## 8. Simulation Parameters & Control

Where: `sim_config.yaml`, or direct Python variables for research flows.

Fields: Max steps, mutation rate, scenario, agent type, batch size, seed.

**Example (Python):**
```python
SIM_STEPS = 500
MUTATION_RATE = 0.08
SEED = 12345
SCENARIO = "codebase_refactor"
```
See [SIMULATION.md] for exhaustive simulation configuration, batch runs, and reproducibility.

---

## 9. CI/CD & DevOps Configuration

**File:** `.github/workflows/ci.yml`  
**Purpose:** Automate linting, test, type-check, security scan, and deploy

**Example:**
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      - name: Run tests
        run: |
          pytest --maxfail=2 --disable-warnings -v
```
Quality gates: No deployment if test, coverage, or audit checks fail.

---

## 10. Runtime Overrides & Hot Reload

- **Dynamic config:** Some parameters (log level, agent limits) can be overridden at runtime via env vars or control plane APIs.
- **Hot reload:** Where supported, config can be reloaded without downtime—always validate and backup first.
- **Immutable infrastructure:** Prefer redeploy over in-place mutation for major updates.

---

## 11. Versioning, Audit, & Change Management

- All config files are version-controlled—never mutate in-place on prod.
- Major config changes (crew, policy, RBAC) require code review and must be audit-logged.
- Audit mesh records every config/app policy change with signature and rationale.
- Change history is exportable for compliance, disaster recovery, and rollback.

---

## 12. Best Practices & Anti-Patterns

**Best Practices:**
- Use `.env.example` and sample configs in all repos.
- Document every field—include type, allowed values, and security/compliance impact.
- Use explicit config for plugins and skills—never rely on dynamic discovery without audit.
- Set sane defaults, but fail fast if required config is missing.

**Anti-Patterns:**
- Hardcoding secrets or paths
- Overloading a single config for unrelated concerns
- Skipping code review for any policy, RBAC, or agent config change

---

## 13. Troubleshooting & Validation

- Validate YAML: `yamllint <file>`
- Check env: `printenv | grep <KEY>`
- Verify config on startup: Platform logs all config loading errors and missing keys.
- CI: Run all tests with config/secret “smoke test” before each deploy.
- Audit: Review audit_mesh for every config change, rollback, or hot reload.

---

## 14. File Index

| File/Path                   | Purpose / Scope            | Doc Section     |
|-----------------------------|---------------------------|-----------------|
| .env / .env.example         | Runtime secrets/vars       | §3, §4          |
| arbiter_config.json         | Orchestrator core config   | §5              |
| crew_config.yaml            | Agent/crew mapping         | §7              |
| policies.json               | Policy, RBAC, limits       | §6              |
| sim_config.yaml             | Simulation control         | §8              |
| config.py                   | Dynamic config/overrides   | §5, §10         |
| .github/workflows/ci.yml    | CI/CD pipeline             | §9              |

---

## 15. Contacts & Escalation

- **Config questions/issues:** [devops@yourcompany.com]
- **Security, audit, or compliance:** [security@yourcompany.com]

All config changes and incident responses are tracked via the audit mesh.

---