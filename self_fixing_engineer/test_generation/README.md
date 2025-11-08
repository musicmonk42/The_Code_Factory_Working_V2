# ATCO Test Generation Platform (Self-Fixing Engineer/Code Factory) 🚀

**Version:** 3.1  
**Last Updated:** August 10, 2025  
**Security Posture:** Zero-Trust, Audit-Logged, Production-Ready  
**Location:** Fairhope, Alabama, USA  
**Maintainer:** Unexpected Innovations Inc  
**Platform:** Python 3.8+ (Pluggable for Multi-Language Support)  
**Test Coverage:** ~85–90% (Unit, Integration, End-to-End)  
**Dependencies:** Managed via `requirements.txt` (Pinned Recommendations Included)  
**Audit Logging:** Tamper-Evident, Required for All Critical Events

---

> The ATCO Test Generation Platform is an autonomous, enterprise-grade system designed to generate, validate, integrate, and report high-quality tests for multi-language codebases. It enforces zero-trust principles on all file operations, configuration reads, and external integrations, ensuring the system fails loudly (never silently) on violations. Built for self-fixing engineer workflows, it automates test creation while maintaining full traceability, compliance, and security.

---

## Table of Contents

- [Features](#features)
- [Security Model & Audit Logging](#security-model--audit-logging)
- [Demo vs Production Mode](#demo-vs-production-mode)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Architecture Overview](#architecture-overview)
- [Extensibility](#extensibility)
- [Testing & Development](#testing--development)
- [Failure Modes & Recovery](#failure-modes--recovery)
- [Production Hardening Checklist](#production-hardening-checklist)
- [Upgrade & Patch Management](#upgrade--patch-management)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Changelog](#changelog)
- [Support](#support)
- [License](#license)

---

## ✨ Features

- **Multi-Language Test Generation:**  
  - Python (Pynguin), JavaScript/TypeScript (Jest w/ LLM), Java (JUnit/Diffblue), Rust (Cargo), Go (Go Modules)
  - Pluggable backends via secure `BackendRegistry`
  - Generates tests from specs (e.g., Gherkin), code, or uncovered modules

- **Zero-Trust File/Path Operations:**  
  - All I/O uses `sanitize_path` to enforce project root boundaries
  - Atomic writes/reads, permission checks, and audits on failures

- **Policy Enforcement & Compliance:**  
  - Integrates NIST, OPA, and custom policies
  - Validates generation/integration with rule enforcement
  - Generates compliance reports (SARIF/HTML)

- **Mutation, Security & Coverage Analysis:**  
  - Mutation testing (`MutationTester`)
  - Security scans (`bandit`, `locust`)
  - Coverage validation, with reports

- **Audit & Observability:**  
  - Tamper-evident logging for all events
  - Prometheus metrics, structured JSON logs

- **Integration & Reporting:**  
  - Auto-integrates passing tests, quarantines failures
  - PR/Jira ticket creation (stub or real)
  - Exports HTML/SARIF reports

- **Concurrency & Efficiency:**  
  - Async operations, configurable parallelism, retries with backoff

- **Extensibility Points:**  
  - Custom backends, pluggable agents, configurable via JSON/env

- **Demo-Friendly:**  
  - Stubbed components for quick setups

---

## 🔒 Security Model & Audit Logging

- **Zero-Trust Model:**  
  - All paths sanitized (`sanitize_path`)  
  - Symlinks disallowed; permissions checked before writes  
  - Dependency integrity checks  
  - Sensitive data redacted in logs

- **Audit Logging:**  
  - Every critical event logged (tamper-evident, optional encryption)
  - Example:
    ```json
    {
      "timestamp": "2025-08-10T12:00:00Z",
      "event_type": "venv_creation_success",
      "details": {"venv_path": "/path/to/venv_XXXXXX", "duration": 1.5},
      "critical": false
    }
    ```

- **Policy Enforcement:**  
  - `PolicyEngine` validates all actions; violations audited & aborted

- **Production Tip:**  
  - Enable encryption for audits via `COMPLIANCE_ENCRYPTION_KEY` env var

---

## 🧪 Demo vs Production Mode

- **Demo Mode:**  
  - Enabled via `DEMO_MODE=1`  
  - Uses stubs for missing deps  
  - Graceful degradation (warnings, mock LLM/graph)  
  - Fast, no external deps, but logs "Demo Mode" warnings

- **Production Mode:**  
  - Default; requires all deps  
  - Full zero-trust, auditing, and policy enforcement

**Switch with the `DEMO_MODE` env var. Always test in demo mode first.**

---

## ⚡ Installation

### Prerequisites

- Python 3.8+ (3.12 recommended)
- Git
- Optional: Docker

### Step-by-Step

1. **Clone:**
   ```bash
   git clone https://your-repo-url/self_fixing_engineer.git
   cd self_fixing_engineer/test_generation
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   # For pinned versions (production):
   pip install -r requirements.lock
   ```

3. **Verify Installation:**
   ```bash
   pytest tests/ orchestrator/tests/
   # Coverage: ~85–90%
   ```

4. **Docker Setup (Optional):**
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY . .
   RUN pip install -r requirements.txt
   CMD ["python", "-m", "test_generation.orchestrator.cli", "--config", "atco_config.json"]
   ```
   ```bash
   docker build -t atco-platform .
   docker run -v /host/path/to/repo:/app/repo atco-platform
   ```

#### Common Issues

- **Missing `arbiter/audit_log.py`:** Copy from repo or stub for demo
- **Windows:** Ensure writable permissions (`icacls`)
- **Dep Conflicts:** Use virtualenv

---

## 🚀 Quick Start

### For Users

1. **Prepare Config (`atco_config.json`):**
   ```json
   {
     "max_parallel_generation": 2,
     "python_venv_deps": ["pytest", "pynguin"],
     "backend_timeouts": {"pynguin": 60},
     "suite_dir": "tests",
     "sarif_export_dir": "atco_artifacts/sarif_reports",
     "compliance_reporting": {"enabled": true}
   }
   ```

2. **Run via CLI:**
   ```bash
   python -m test_generation.orchestrator.cli --config atco_config.json --suite-dir tests
   ```

3. **Run via API:**
   ```bash
   python -m test_generation.gen_agent.api
   # POST to /generate-tests:
   {
     "spec": "Test user login",
     "language": "Python"
   }
   ```

4. **Demo Mode:**
   ```bash
   DEMO_MODE=1 python -m test_generation.orchestrator.cli --config atco_config.json
   ```

### For Developers

- **Add a Backend:**  
  See `backends.py` — implement and register your backend

- **Test:**  
  Add to `test_backends.py`, run with coverage

- **Debug:**  
  Set `LOG_LEVEL=DEBUG`, check logs

---

## ⚙️ Configuration

- **File:** `atco_config.json` (JSON/YAML supported)
- **Env Var Overrides:** Prefix `ATCO_`

#### Key Options

| Key                           | Description                                 | Example                          |
|-------------------------------|---------------------------------------------|----------------------------------|
| `project_root`                | Project base path                           | `"."`                            |
| `suite_dir`                   | Test output dir                             | `"tests"`                        |
| `max_parallel_generation`     | Concurrent generations                      | `4`                              |
| `venv_temp_dir`               | Temp venv path                              | `"atco_artifacts/venv_temp"`     |
| `venv_install_timeout_seconds`| Pip install timeout (seconds)               | `180`                            |
| `python_venv_deps`            | Deps for Python venvs                       | `["pytest", "pynguin"]`          |
| `backend_timeouts`            | Dict of timeouts per backend (sec)          | `{"pynguin": 60}`                |
| `llm_model`                   | LLM model                                   | `"gpt-4o"`                       |
| `compliance_reporting`        | Compliance settings                         | `{"enabled": true}`              |
| `log_level`                   | Logging level                               | `"INFO"`                         |

#### Example Full Config

```json
{
  "max_parallel_generation": 4,
  "venv_temp_dir": "atco_artifacts/venv_temp",
  "venv_install_timeout_seconds": 180,
  "backend_timeouts": {
    "pynguin": 60,
    "jest_llm": 90
  },
  "llm_model": "gpt-4o",
  "compliance_reporting": {"enabled": true},
  "mutation_testing": {"enabled": true, "min_score_for_integration": 80.0},
  "jira_integration": {"enabled": true},
  "log_level": "INFO"
}
```

---

## 🏗️ Architecture Overview

**Entry Points:**
- **CLI:** `cli.py`
- **API:** `api.py`

**Core Pipeline (`orchestrator.py`):**
```
Monitor Uncovered → Generate Tests → Validate (Policy, Security, Mutation)
     ↓
Integrate / Quarantine / PR
     ↓
Report (SARIF/HTML) + Audit + Metrics
```

**Submodules:**
- `gen_agent`: Agent workflows
- `orchestrator`: Venv management, pipeline, stubs
- `utils.py`: File ops, LLM init, coverage runs, security scans
- `audit.py`, `policy_and_audit.py`: Audit logger, policy engine
- `reporting.py`: HTML/SARIF exports

---

## 🧩 Extensibility

- **Add Backend:**  
  - Implement class in `backends.py`
  - Register: `backend_registry.register_backend("new_lang", NewBackend)`
  - Test: Add to `test_backends.py`

- **Custom Agent:**  
  - Define async function in `gen_agent/agents.py`
  - Add to graph in `graph.py`

- **Policy Rules:**  
  - Extend `PolicyEngine` in `policy_and_audit.py`
  - Add rules to `atco_policies.json`

---

## 🧪 Testing & Development

- **Run Tests:**  
  ```bash
  pytest --cov=test_generation --cov-report=html
  ```

- **Development Workflow:**  
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  pytest
  ```

- **Debug:**  
  Set `LOG_LEVEL=DEBUG`, check logs

- **Common Pitfalls:**  
  Mock externals, test Windows paths manually

---

## 🚨 Failure Modes & Recovery

| Failure             | Cause           | Recovery                             |
|---------------------|----------------|--------------------------------------|
| Venv Creation Fails | Race, perms, deps | Check logs, retry, fix deps          |
| Policy Violation    | Invalid tests  | Quarantine, review audits            |
| Missing Dependency  | Incomplete install | Use stubs, install from requirements |
| Signal Interrupt    | SIGINT/SIGTERM | Graceful shutdown, check logs        |

---

## ✅ Production Hardening Checklist

- [ ] Config validated, paths sanitized
- [ ] Deps pinned (`requirements.lock`)
- [ ] Audit logger operational, encryption enabled
- [ ] DEMO_MODE disabled
- [ ] Test suite passes (>80% coverage)
- [ ] Windows tested
- [ ] Metrics endpoint up (Prometheus)
- [ ] Audit logs backed up
- [ ] CI/CD with GitHub Actions
- [ ] Security scan (`bandit -r .`)

---

## ⬆️ Upgrade & Patch Management

- **Upgrade Deps:**  
  Edit `requirements.txt`, run `pip install -r requirements.txt`, generate lock  
- **Patch:**  
  Pull, install, test, validate, check audits  
- **Versioning:**  
  Semantic (`major.minor.patch`), changelog in `CHANGELOG.md`

---

## 🛠️ Troubleshooting

- **Venv Fails:** Check permissions, increase timeout, review logs
- **Policy Errors:** Validate policy config, check audits
- **Logs Missing:** Ensure audit logger present
- **Concurrency:** Reduce `max_parallel_generation`
- **Dep Conflicts:** Run `pip check`, use virtualenv

---

## 🤝 Contributing

1. Fork repo
2. Create branch: `git checkout -b feature/new-backend`
3. Commit: `git commit -m "Add new backend"`
4. Test: `pytest`
5. PR: Submit with description, changes, tests

**Code Review:** Ensure zero-trust, audits, and tests.

---

## 📝 Changelog

- **v3.1 (2025-08-10):** Venv fix, expanded README, test suite enhancements
- **v3.0:** Initial release with multi-language support

Full changelog in `CHANGELOG.md`.

---

## 🆘 Support

- **Issues:** GitHub Issues or internal tracker
- **Security:** Report privately, disable prod mode, review audits
- **Community:** Email maintainer / Slack (if available)
- **Enterprise:** Contact Unexpected Innovations for integration

---

## ⚖️ License

**Proprietary Technology by Unexpected Innovations Inc.**  
_All rights reserved._



---

> Your codebase deserves ATCO’s excellence. Every event, every action—audited, secured, and designed to empower developers and users alike. 🚀