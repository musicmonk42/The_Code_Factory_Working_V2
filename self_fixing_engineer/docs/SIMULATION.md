# 🧪 SIMULATION.md — Self Fixing Engineer™

---

## Table of Contents

1. [Purpose & Audience](#purpose--audience)
2. [Simulation Architecture Overview](#simulation-architecture-overview)
3. [Core Components & File Structure](#core-components--file-structure)
4. [Environment Setup & Dependencies](#environment-setup--dependencies)
5. [Simulation Configuration Reference](#simulation-configuration-reference)
6. [Running Simulations: CLI & Programmatic Flows](#running-simulations-cli--programmatic-flows)
7. [Metrics, Outputs, & Results Analysis](#metrics-outputs--results-analysis)
8. [Advanced Use Cases & Batch Experimentation](#advanced-use-cases--batch-experimentation)
9. [CI/CD & Automated Quality Gates](#cicd--automated-quality-gates)
10. [Extending & Customizing Agents/Environments](#extending--customizing-agentsenvironments)
11. [Troubleshooting & FAQ](#troubleshooting--faq)
12. [Security, Audit & Compliance in Simulation](#security-audit--compliance-in-simulation)
13. [References & Further Reading](#references--further-reading)
14. [Support & Escalation](#support--escalation)

---

## 1. Purpose & Audience

The simulation framework in Self Fixing Engineer™ enables:

- Autonomous agent benchmarking: RL, heuristic, and hybrid agents in realistic code/infrastructure scenarios.
- Continuous code health regression testing: Before rollout, after refactor, and for every PR/merge.
- Safe agent evolution and reinforcement learning: Training, tuning, and comparing agents without risk to production.

**For:** AI/ML researchers, SREs, QA/devops engineers, plugin developers, and compliance/validation teams.

---

## 2. Simulation Architecture Overview

```
+------------------+      +--------------------+
| sim_config.yaml  |----->| main_sim_runner.py |
+------------------+      +--------------------+
                                  |
               +------------------+------------------+
               |                  |                  |
       code_health_env.py   evolution.py        world.py
               |                  |                  |
        RL/Heuristic Agent(s) <---+                  |
               |                  |                  |
      +-----------------------------+         +---------------+
      |   Simulation Results/Logs    |<--------| Metrics, Audit|
      +-----------------------------+         +---------------+
```

- **Config-driven:** All experiments defined in YAML or via CLI overrides.
- **Modular:** Add new agents/environments with minimal code.
- **Audit/trace ready:** All simulation results are reproducible and exportable.

---

## 3. Core Components & File Structure

- **evolution.py:** RL/agent training loop and base environment classes.
- **code_health_env.py:** Simulates codebase health, refactor/repair, coverage, error metrics.
- **main_sim_runner.py:** Entrypoint for batch or single simulations; CLI and programmatic interface.
- **world.py:** Simulation state, transition, and logic core.
- **sim_config.yaml:** User-editable experiment config.
- **results/:** Outputs, logs, checkpoints, and artifacts.

---

## 4. Environment Setup & Dependencies

- **Python:** 3.8+ (3.11+ recommended for performance and typing)

**Dependencies:**

Install with:
```bash
pip install -r requirements.txt
```
**Extra (optional):**

For RLlib integration:
```bash
pip install ray[rllib]
```
For OpenAI Gym compatibility:
```bash
pip install gymnasium
```

**System Requirements:**
- CPU: 2+ cores recommended for batch/parallel runs
- RAM: 4GB+ for RL, more for large codebases

---

## 5. Simulation Configuration Reference

**Sample sim_config.yaml:**
```yaml
environment:
  type: code_health
  params:
    max_steps: 100
    initial_complexity: 50

agents:
  - type: rl_agent
    policy: q_learning
    learning_rate: 0.1
    gamma: 0.99
  - type: heuristic_agent
    strategy: "basic_static"
    allowed_actions: ["refactor", "repair", "skip"]

output:
  log_dir: simulation/results/
  save_metrics: true
  export_format: [json, csv]

seed: 42
parallel_runs: 4
```

**Fields:**
- `environment.type`: code_health, base_evolution, or custom
- `agents`: List of RL or rule-based agents, with hyperparameters
- `output`: Directory, format, and extra artifact settings
- `seed`: Randomness for reproducibility
- `parallel_runs`: Batch size for experiment replication

---

## 6. Running Simulations: CLI & Programmatic Flows

**Command-Line:**
```bash
python simulation/main_sim_runner.py --config sim_config.yaml
```

**Flags:**
- `--config`: Path to YAML config
- `--output`: Override log directory
- `--seed`: Override random seed
- `--parallel`: Launch multiple concurrent runs

**Python Programmatic Example:**
```python
from evolution import BaseEnvironment

env = BaseEnvironment(max_steps=100)
obs = env.reset()
while not env.done:
    action = agent.act(obs)
    obs, reward, done, info = env.step(action)
```

---

## 7. Metrics, Outputs, & Results Analysis

- Results written to `simulation/results/` or as configured.

**Key outputs:**
- `results.json` / `results.csv`: Full agent traces, rewards, state transitions
- `metrics.json`: Aggregate scores—final reward, episode length, health delta
- `logs/`: Step-by-step actions, error/warning traces, convergence plots
- `checkpoints/`: (If enabled) Agent policy weights for recovery/transfer

**Metrics Explained:**
- Final reward: Overall task performance
- Episode length: How long the agent persisted
- Action sequence: Steps taken by agent(s)
- Health delta: Improvement/degradation in codebase

**Recommended:** Use built-in plotting tools (`python -m simulation.plot_results ...`) or Jupyter notebooks for in-depth analysis.

---

## 8. Advanced Use Cases & Batch Experimentation

- **Parallelization:** Use `parallel_runs` or run multiple configs for batch A/B testing.
- **Checkpointing:** Use runner flags to auto-save agent state at intervals.
- **Custom environments:** Inherit from `BaseEnvironment`, add to config, and register in runner.
- **Cross-agent tournaments:** Pit multiple agent types against each other for best-of-breed selection.
- **Auto-hyperparameter sweep:** Integrate with Ray Tune or custom scripts for automatic tuning.

---

## 9. CI/CD & Automated Quality Gates

- Integrate simulations with every pull request, commit, or merge.
- Fail pipeline on:
  - Regression in health metrics (coverage, error rate, reward)
  - Policy divergence or agent instability
  - Notable agent failures (exceptions, crashes)

**Recommended:** Store a baseline results artifact; require “no regression” vs. main for release.

---

## 10. Extending & Customizing Agents/Environments

- **Custom agent:** Inherit from RL agent base, register new policy in config.
- **Custom environment:** Subclass `BaseEnvironment` or adapt `code_health_env.py`.
- **Full API documentation:** Docstrings in all core simulation modules.
- **Submit PRs or plugins:** See [PLUGINS.md] for pluginized simulation components.

---

## 11. Troubleshooting & FAQ

**Config errors:**
- Validate YAML with `yamllint`
- Check file paths and required fields

**Module import errors:**
- Set PYTHONPATH to project root
- Verify all dependencies installed

**No output/results:**
- Ensure `results/` exists and is writable
- Check logs for silent exceptions or out-of-memory

**Slow simulations:**
- Lower max_steps or parallel runs
- Use lighter agents/environments for testing

---

## 12. Security, Audit & Compliance in Simulation

- **Sandbox all agents:** Agents run in isolated process/container for safety.
- **Audit trace:** All simulations are logged and hash-chained; every result is cryptographically signed for forensic traceability.
- **Sensitive data:** Never use real secrets or production data in simulation environments.
- **Compliance checks:** Optionally integrate with audit mesh to record all simulation outputs for compliance/validation workflows.

---

## 13. References & Further Reading

- OpenAI Gym — RL simulation standards
- RLlib (Ray) — Scalable RL experimentation
- [Self Fixing Engineer™ ARCHITECTURE.md] — Platform integration details
- [PLUGINS.md] — Pluginizing simulation logic

---

## 14. Support & Escalation

- Simulation framework support: [support@yourcompany.com]
- Incident response: [emergency@yourcompany.com]
- Enterprise pilots and research extensions: Contact customer success

---