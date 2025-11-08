# SFE Platform — Minimal, Reliable Demo Checklist

**Updated:** September 14, 2025
**Goal:** Run a short, repeatable demo that proves the SFE platform can (1) scan a codebase, (2) propose/perform a simple fix, and (3) record a tamper‑evident audit trail — without requiring any external services.

---

## What you’ll demonstrate (in \~6–8 minutes)

* **Static analysis** of a tiny repo (detects a circular import / missing dep).
* **Automated repair** using a proposal file (rule‑based; AI optional).
* **Auditability** via a local, hash‑chained log.

> This flow intentionally avoids cloud/DB dependencies and runs in a local "safe mode" for reliability.

---

## 1) Prerequisites & Environment

### Supported runtime

* **OS:** Windows 10/11, macOS, or Linux
* **Python:** **3.10.x or 3.11.x** (recommended: 3.11.9). Avoid 3.12+ unless you’ve validated locally.

### Create a virtual environment (example)

```bash
python -m venv .venv
# Windows
. .venv/Scripts/activate
# macOS/Linux
# source .venv/bin/activate
```

### Install dependencies

```bash
pip install -r requirements.txt
```

* Optional stacks (Neo4j, Feast, web3, etc.) are **not required** for this demo. The platform falls back to in‑memory/mocks.
* If an import fails for an optional lib, use the provided **stubs.py** or add a quick stub to proceed.

### Enable local “safe mode”

Prefer using the onboard helper:

```bash
python onboard.py --safe
```

If that fails, set config manually (example):

```json
{
  "is_demo_mode": true,
  "demo_safe_mode": true
}
```

Save as **config.json** at the project root. This disables external calls and uses only local storage.

---

## 2) Prepare the Tiny Demo Project

Create a minimal repo with a deliberate circular import.

**Structure**

```
./src
  ├─ main.py
  └─ utils.py
```

**src/main.py**

```python
from utils import helper  # circular dep

def main():
    helper()

if __name__ == "__main__":
    main()
```

**src/utils.py**

```python
from main import main  # circular dep

def helper():
    main()
```

*(If the scaffolder already provided a sample project, use that instead.)*

---

## 3) Demo Flow (Script + Commands)

### Step 1 — Codebase Analysis

**Objective:** Show static analysis + proposal generation (cycles/missing deps).

**Command**

```bash
python cli.py analyze src/ --proposals proposals.json
```

**What to show**

* Open **proposals.json**; you should see something like:

```json
{
  "cycles": [["main", "utils", "main"]],
  "dependency_issues": ["missing: numpy"]
}
```

**If it breaks**

* Check the log: **atco\_artifacts/atco\_audit.log**
* If Pydantic/validation issues cause empty output, hand‑craft a minimal **proposals.json** (as above) so you can proceed.

**Talking point**

> “The analyzer is rule‑based in safe mode, calling out obvious structural problems. In full mode it can layer in AI suggestions, but we keep it deterministic here for reliability.”

---

### Step 2 — Automated Repair

**Objective:** Apply the proposal to fix the circular import.

**Command**

```bash
python cli.py repair src/main.py --proposals proposals.json --force
```

**What to show**

* A modified **src/main.py**/**src/utils.py** and backups in a safe location. Mention that changes are recorded in the audit trail.

**If it breaks**

* Use `--force` to bypass strict checks.
* Manually break the cycle if needed (e.g., move the import inside the function or remove the cross‑call) to demonstrate the flow.
* Review **atco\_artifacts/atco\_audit.log** for details.

**Talking point**

> “This is the platform’s self‑healing path. We start with rules for predictability; AI assistance is optional and gated by approvals in production.”

---

### Step 3 — Auditability & Provenance

**Objective:** Prove transparency and tamper‑evidence.

**View the audit log**

* macOS/Linux:

```bash
cat atco_artifacts/atco_audit.log
```

* Windows (PowerShell/CMD):

```bat
type atco_artifacts\atco_audit.log
```

**If empty**
Seed a simple entry to explain the format:

```bash
echo '{"event":"analysis","timestamp":"2025-09-14T02:14:00Z","hash":"abc123..."}' > atco_artifacts/atco_audit.log
```

**Talking point**

> “Events are hash‑chained locally to detect tampering. Enterprise deployments can anchor summaries to a blockchain if required by policy; the demo keeps it local for simplicity.”

---

## 4) Key Technical Talking Points (Coder‑friendly)

* **Async, pluggable architecture.** Concurrency with `asyncio`; adapters for LLMs, stores, and services.
* **Container‑based sandboxing.** Untrusted plugin code can be isolated (e.g., seccomp/AppArmor) to mitigate escalation risks.
* **Rule‑first decisions with optional AI.** Deterministic rules in safe mode; AI suggestions and human‑in‑the‑loop in production.
* **Zero‑trust, cryptographic provenance.** Actions are policy‑gated and logged with hash chaining for verifiable history.

---

## 5) Troubleshooting Quick Hits

* **Python mismatch (3.12+):** Use 3.10/3.11. Check with `python --version`.
* **Pydantic errors:** Pin a known‑good version in `requirements.txt` or set the expected V1/V2 compat shim if present.
* **Missing optional deps:** Stub them (see `stubs.py`) or comment out non‑demo imports.
* **No proposals generated:** Create a minimal `proposals.json` manually to keep the flow moving.
* **No audit log output:** Re‑run with `--verbose`; ensure `atco_artifacts/` exists; seed an entry if needed.
* **CLI path issues:** Run from the project root; prefer absolute paths if invoking from elsewhere.

---

## 6) Suggested Closing Line (for the demo)

> “You’ve seen static analysis → automated repair → tamper‑evident logging — all locally, no external services. That’s the reliable core we harden and then scale up with AI assistance, team approvals, and enterprise integrations.”
