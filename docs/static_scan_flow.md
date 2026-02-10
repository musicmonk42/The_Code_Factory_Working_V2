<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Static Scan Trigger and Parsing (Bandit / Mypy / Radon)

- **What triggers the scan?**  
  - Developer-driven: `make security-scan` runs Bandit (and Safety) over the codebase (`Makefile:103-108`, `README.md:744`). Mypy is run via `make lint`/CI (`Makefile:94-111`). Radon is not wired into ASE; it must be invoked manually if needed.  
  - Agent-driven: ASE (A.S.E) agents call scanners themselves—for example, the test-generation/security utilities run Bandit in subprocesses when a security scan step is in the workflow (`self_fixing_engineer/test_generation/utils.py:867-935`, `self_fixing_engineer/test_generation/gen_agent/agents.py:1598-1673`), and the import-fixer validation pipeline triggers Bandit when validating a fix (`self_fixing_engineer/self_healing_import_fixer/import_fixer/fixer_validate.py:748-787`). These invocations are explicit within the agent workflows, not automatic background watchers.

- **How does ASE know a scan was triggered?**  
  - When ASE agents initiate scans, they issue the subprocess call themselves (e.g., `asyncio.create_subprocess_exec` for Bandit), so the initiation is intrinsic to the workflow—no external detection is needed (`test_generation/utils.py:885-935`, `gen_agent/agents.py:1598-1673`, `fixer_validate.py:748-761`).  
  - If a human developer runs `make security-scan` or a manual tool invocation outside ASE, ASE has no implicit detector; the platform only “knows” about scans it launches or results it is given.

- **What triggers ASE to parse results?**  
  - Parsing happens immediately after the agent-launched subprocess completes. Handlers read the captured stdout/stderr JSON and transform it into findings within the same control flow (`test_generation/utils.py:900-935`, `gen_agent/agents.py:1657-1673`, `fixer_validate.py:759-787`). There is no separate log-parsing daemon.

- **Are scan results written to files? How does ASE find them?**  
  - Current Bandit/Mypy/Radon flows keep outputs in-memory (stdout) rather than writing to files. The Bandit integrations request JSON output and parse the returned text directly (`fixer_validate.py:759-787`, `gen_agent/agents.py:1657-1673`). No path discovery is required.  
  - If a developer runs tools manually with file outputs, ASE will not auto-discover them; any ingestion would require explicitly providing those paths or wiring a new hook.

- **Post-deploy scanning vs. live memory**  
  - Static tools are run against code on disk (inside containers, deployed repo copies, uploaded build folders). There is no live-memory scanning. Purpose: catch security/type/risky patterns “post-deploy” before users hit them.

- **Language coverage**  
  - Bandit/Mypy are Python-only. Other languages rely on their native linters/SAST (e.g., Semgrep/ESLint/GolangCI-Lint) where wired into the pipeline; there is no universal multi-language scanner automatically invoked by ASE—you must hook the appropriate tool per language.
