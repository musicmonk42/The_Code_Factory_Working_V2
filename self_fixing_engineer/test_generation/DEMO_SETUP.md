# Test Generation Demo Setup Guide

This guide provides an excessively thorough, step-by-step process to set up a demo for the `test_generation` module (part of the Self-Fixing Engineer platform). It is suitable for both technical and non-technical audiences and ensures a stable, auditable, and visually clear demonstration of automated test generation, auditing, compliance, and reporting. The process leverages the post-v3.1 `venvs.py` fix (resolving venv creation race conditions and audit logging failures), and is cross-platform (Windows, Linux, Docker).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Setup Steps](#setup-steps)
  - [Step 1: Clone and Verify Repository](#step-1-clone-and-verify-repository)
  - [Step 2: Set Up Environment](#step-2-set-up-environment)
  - [Step 3: Install Dependencies](#step-3-install-dependencies)
  - [Step 4: Create Demo Configuration](#step-4-create-demo-configuration)
  - [Step 5: Add Sample Inputs](#step-5-add-sample-inputs)
  - [Step 6: Validate Setup with Tests](#step-6-validate-setup-with-tests)
  - [Step 7: Prepare Demo Execution](#step-7-prepare-demo-execution)
- [Environment-Specific Instructions](#environment-specific-instructions)
  - [Windows](#windows)
  - [Linux](#linux)
  - [Docker](#docker)
- [Running the Demo](#running-the-demo)
  - [CLI Demo](#cli-demo)
  - [API Demo](#api-demo)
  - [Error Simulation](#error-simulation)
- [Expected Outputs](#expected-outputs)
- [Troubleshooting](#troubleshooting)
- [Best Practices for Demo Delivery](#best-practices-for-demo-delivery)
- [Additional Resources](#additional-resources)

---

## Prerequisites

- **Operating System:** Windows (tested on paths like `D:\Code_Factory\self_fixing_engineer`), Linux, or a Docker-compatible system
- **Python:** 3.12+ (recommended); 3.8+ supported
- **Git:** For cloning the repository
- **Internet access** (for pip install; or cache packages for offline)
- **Hardware:**  
  - Minimum: 4GB RAM, 2 CPU cores, 5GB free disk  
  - Recommended: 8GB RAM, 4 CPU cores, 10GB SSD
- **Permissions:** Write access to project root (e.g., `D:\Code_Factory\self_fixing_engineer\atco_artifacts`)
- **Tools (optional):**
  - Docker (containerized demos)
  - Browser (to view HTML reports)
  - Text editor (for config/input files)
- **Knowledge:**
  - Basic Python/CLI familiarity
  - Understanding of test_generation features (see README.md)

**Verification:**  
- `python --version` (should output 3.12.x or compatible)  
- `git --version`  
- Disk space: `dir` (Windows) / `df -h` (Linux)

---

## Setup Steps

### Step 1: Clone and Verify Repository

```bash
git clone https://your-repo/self_fixing_engineer.git
cd self_fixing_engineer/test_generation
```
- **If offline:** Copy the `test_generation` folder to target location (e.g., `D:\Code_Factory\self_fixing_engineer\test_generation`).

**Verify directory structure:**  
Ensure the following exist:
- `orchestrator/` (`orchestrator.py`, `venvs.py`, `cli.py`, ...)
- `gen_agent/` (`agents.py`, `api.py`, ...)
- `tests/` (`test_venvs.py`, ...)
- `requirements.txt`, `README.md`

```bash
dir test_generation    # Windows
ls test_generation     # Linux
```

**Check venvs.py fix:**  
Open `orchestrator/venvs.py` and confirm:
- `import filelock, tempfile, subprocess, venv`
- `filelock.FileLock(lock_path, timeout=30)` in `_create_and_manage_python_env`
- `tempfile.TemporaryDirectory(dir=project_root, prefix="venv_")`
- Audit events (`venv_creation_success` etc.)

---

### Step 2: Set Up Environment

**Create virtual environment:**
```bash
python -m venv .venv
```
- **Windows:** `.venv\Scripts\activate`
- **Linux:** `source .venv/bin/activate`

**Verify activation:**
```bash
python --version      # Should show Python 3.12.x
which pip             # Should point to .venv/bin/pip or .venv\Scripts\pip
```

**Set environment variables:**
- **Demo mode (optional):**
  - Linux: `export DEMO_MODE=1`
  - Windows: `set DEMO_MODE=1`
- **Log level:**  
  - Linux: `export LOG_LEVEL=INFO`
  - Windows: `set LOG_LEVEL=INFO`

**Ensure write permissions:**
- **Windows:**  
  `icacls "D:\Code_Factory\self_fixing_engineer\atco_artifacts" /grant Users:F`
- **Linux:**  
  `chmod -R u+rwX atco_artifacts`

---

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```
- **Key dependencies:** `pytest`, `filelock==3.12.0`, `pynguin==0.32.0`, `bandit`, `locust`, `tenacity`, `aiofiles`, `prometheus_client`

**(Recommended) Generate and use lock file:**
```bash
pip freeze > requirements.lock
pip install -r requirements.lock
```

**Verify installation:**
```bash
pip check
pip list
```

**Offline setup (optional):**
```bash
pip download -r requirements.txt -d ./deps
pip install --no-index --find-links ./deps -r requirements.txt
```

---

### Step 4: Create Demo Configuration

Create `test_generation/atco_config.json`:

```json
{
  "max_parallel_generation": 1,
  "python_venv_deps": ["pytest==7.4.0", "pynguin==0.32.0"],
  "backend_timeouts": {
    "pynguin": 30,
    "jest_llm": 60
  },
  "suite_dir": "tests",
  "sarif_export_dir": "atco_artifacts/sarif_reports",
  "log_level": "INFO",
  "compliance_reporting": {"enabled": true},
  "mutation_testing": {"enabled": false}
}
```

**Validate config:**
```bash
python -c "from test_generation.orchestrator.config import load_config; load_config('.', 'atco_config.json')"
```
Should exit without errors.

---

### Step 5: Add Sample Inputs

**Create demo directory:**
```bash
mkdir test_generation/demo
```

**Sample code (`demo/my_module.py`):**
```python
def divide(a, b):
    return a / b  # Bug: ZeroDivisionError
```

**Sample spec (`demo/spec.txt`):**
```
Feature: Division
  Scenario: Divide two numbers
    Given two numbers 10 and 2
    When divide is called
    Then the result is 5
```

---

### Step 6: Validate Setup with Tests

```bash
pytest test_generation/tests test_generation/orchestrator/tests --cov=test_generation --cov-report=html
```
- **Expected:** ~85–90% coverage; all tests pass.

**Key tests:**  
- `test_venvs.test_happy_path`  
- `test_venvs.test_concurrent_venvs`  
- `test_e2e_pipeline.test_e2e_pipeline_full_success`  
- `test_integration_e2e.test_e2e_happy_and_quarantine_paths`

**(Windows-specific test):**  
Add to `test_venvs.py` if not present:
```python
@pytest.mark.skipif(os.name != "nt", reason="Windows only")
async def test_windows_venv_creation(temp_project_root):
    async with temporary_env(temp_project_root, language="python", required_deps=["pytest==7.4.0"]):
        assert os.path.exists(os.path.join(temp_project_root, "Scripts", "python.exe"))
```
Run:
```bash
pytest test_venvs.py::test_windows_venv_creation
```

---

### Step 7: Prepare Demo Execution

**(Optional) Add `--demo` CLI flag:**  
Update `orchestrator/cli.py` with a `--demo` option for pre-set demo config.

**Create demo script (`test_generation/demo.sh`):**

```bash
#!/bin/bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p atco_artifacts/sarif_reports tests
python -m test_generation.orchestrator.cli --config atco_config.json --suite-dir tests --demo
echo "View report: atco_artifacts/sarif_reports/report.html"
```
**Linux:** `chmod +x demo.sh`

---

## Environment-Specific Instructions

### Windows

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
icacls "D:\Code_Factory\self_fixing_engineer\atco_artifacts" /grant Users:F
```
- Test on Windows paths
- Run `test_venvs.test_windows_venv_creation`
- Disable antivirus if needed (filelock may be blocked)

### Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod -R u+rwX atco_artifacts
```
- Run full test suite
- Ensure disk space

### Docker

**Create `docker-compose.yml`:**
```yaml
services:
  atco:
    image: python:3.12-slim
    volumes:
      - ./:/app
    working_dir: /app/test_generation
    environment:
      - DEMO_MODE=1
      - LOG_LEVEL=INFO
    command: python -m test_generation.orchestrator.cli --config atco_config.json --demo
```
**Build/Run:**
```bash
docker-compose up
```
- Ensures isolated, reproducible environment

---

## Running the Demo

### CLI Demo

```bash
python -m test_generation.orchestrator.cli --config atco_config.json --suite-dir tests --demo
```

- **Technical:**  
  - Show `my_module.py`, `spec.txt`
  - Run CLI, explain audit logs, policy checks

- **Non-Technical:**  
  - Show HTML report (`atco_artifacts/sarif_reports`)
  - Walk through: Buggy code → tests → compliance/audit

### API Demo

**Start server:**
```bash
python -m test_generation.gen_agent.api
```
**Send request:**
```bash
curl -X POST http://localhost:5000/generate-tests \
  -H "Content-Type: application/json" \
  -d '{"spec": "Test division", "language": "Python"}'
```
- Show returned JSON with test code/status

### Error Simulation

- **Venv failure:**  
  Remove write permissions, run CLI; check `venv_creation_failure` in audit log
- **Signal interrupt:**  
  Run CLI, press Ctrl+C; check `venv_creation_cancelled` in logs

---

## Expected Outputs

- **Tests:** `tests/test_my_module.py` (e.g., Pynguin-generated)
- **Reports:** `atco_artifacts/sarif_reports/report.html`, `report.sarif.json`
- **Logs:** `test_gen_agent.log` (CLI), `atco_audit.log` (audit)
- **Example audit event:**
  ```json
  {
    "event_type": "venv_creation_success",
    "venv_path": "atco_artifacts/venv_XXXXXX",
    "duration": 1.5
  }
  ```

---

## Troubleshooting

- **Venv creation fails:**  
  - Symptom: `venv_creation_failure` in audit log  
  - Fix: Check permissions, increase timeout in config, verify dependencies

- **Config errors:**  
  - Symptom: JSON parse errors  
  - Fix: Use valid JSON config

- **Dependency issues:**  
  - Symptom: Missing packages  
  - Fix: `pip install -r requirements.lock`

- **Signal interrupt:**  
  - Symptom: `venv_creation_cancelled` in logs  
  - Fix: Retry CLI

- **Windows issues:**  
  - Symptom: File locking or antivirus errors  
  - Fix: Disable antivirus, run concurrency tests

**Debug tips:**  
- `export LOG_LEVEL=DEBUG`  
- Tail `test_gen_agent.log`, `atco_audit.log`  
- Run `pytest test_venvs.py`

---

## Best Practices for Demo Delivery

- **Preparation:**  
  - Test on target platform  
  - Pre-run CLI to cache outputs  
  - Backup `atco_artifacts` for recovery

- **Technical audience:**  
  - Explain `venvs.py` fix (filelock, temp dirs)  
  - Show audit logs

- **Non-Technical audience:**  
  - Focus on HTML report, compliance  
  - Simplify story: "Buggy code → Automated tests → Compliance"

- **Live tips:**  
  - Use simplified CLI: `--demo`  
  - Have troubleshooting notes handy  
  - Monitor logs in real-time

---

## Additional Resources

- `README.md`: Core module documentation
- `docs/DEMO_SCRIPT.md`: Demo narratives
- `docs/DEMO_TROUBLESHOOTING.md`: Debug guide
- `docs/CHANGELOG.md`: History of fixes (e.g., venvs.py v3.1)
- `tests/`: `test_venvs.py`, `test_e2e_pipeline.py` for validation

---

**Your demo is now ready to impress both developers and execs!**