<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

**Updates Made**:
- Timestamp updated to September 10, 2025.
- Added complete `requirements.txt` and Docker setup (Dockerfile, Docker Compose).
- Included API access instructions (`http://localhost:8000/docs`).
- Added SFE integration examples (intent, mesh).
- Added community links (Discord, wiki).
- Clarified testing and submodule references.
- Noted resolved pytest issues and pending multimodal/load tests.

---

### Updated `ESSENTIALS_FOR_NEW_DEVS.md`

```markdown
# Essential Guide for a Developer New to Arbiter (Coming in Cold)

Welcome! If you're diving into Arbiter for the first time—with zero prior context—this guide distills the must-know essentials to get you productive fast.  

**Arbiter** is the core orchestration module in the Self-Fixing Engineer (SFE) platform: an autonomous AI system for code analysis, self-repair, evolution, and governance. Built with Python 3.10+, it emphasizes modularity, ethics, and production readiness. Think of it as the "brain" coordinating agents, policies, knowledge, and plugins in an AI-driven software engineering workflow.

As of September 10, 2025, Arbiter is aligned with AI regulations like the **EU AI Act** (high-risk for autonomous systems) and **NIST AI RMF** (traceability, self-improvement).

---

## 1. What Arbiter Is (High-Level Purpose)

**Core Role:**  
Arbiter orchestrates self-healing AI agents that analyze, fix, and evolve codebases autonomously. It:

- Enforces ethical "constitution" rules (`arbiter_constitution.py`).
- Manages knowledge graphs (`knowledge_graph`) and meta-learning (`meta_learning_orchestrator`).
- Runs agent simulations/competitions (`arena.py`).
- Handles human feedback loops (`human_loop.py`).
- Integrates plugins for LLMs and multimodal data (`plugins/`).

**Key Problems It Solves:**

- **Autonomy with Guardrails:** Agents evolve code but follow ethics and policies (`policies.json`).
- **Self-Improvement:** Meta-learning and exploration optimize agents (`arbiter_growth.py`).
- **Compliance & Audit:** Tamper-evident logs (`audit_log.py`) and metrics (`metrics.py`) for 2025 AI laws.
- **Extensibility:** Plugins for LLMs (OpenAI, Anthropic, Gemini, Ollama) and backends (Postgres, Redis, Neo4j).
- **SFE Integration:** Connects with `intent_agent`, `mesh`, `guardrails`, and `chaincode`.

**Not For:** Simple scripting or non-code AI tasks (use LangChain for general-purpose LLMs).

---

## 2. Setup & First Steps

### Prerequisites

- **Python 3.10+**: Install via `pyenv`.
- **Dependencies**: See `requirements.txt` in root README.
- **Services**: Redis, Postgres, Neo4j (Docker recommended; see README).
- **Env Vars**: Set in `.env` (e.g., `REDIS_URL`, `OPENAI_API_KEY`).

### Quick Start

1. Clone: `git clone https://github.com/unexpected-innovations/sfe.git && cd sfe/arbiter`
2. Install: `pip install -r requirements.txt`
3. Run: `python run_exploration.py --mode single`
4. Access API: `http://localhost:8000/docs` (arena mode)
5. Metrics: `http://localhost:9090/metrics`

**Docker**:
```bash
docker build -t arbiter .
docker run --env-file .env -p 8000:8000 -p 9090:9090 arbiter

3. Core Architecture
Arbiter is modular, async-heavy, and integrates with SFE components. Key files:

Core: arbiter.py (agent management), arena.py (simulations), config.py (settings).
Submodules:

arbiter_growth: Manages agent evolution (arbiter_growth_manager.py, idempotency.py).
bug_manager: Bug detection/fixing (bug_manager.py, remediations.py).
explainable_reasoner: LLM-driven explanations (explainable_reasoner.py, prompt_strategies.py).
knowledge_graph: Knowledge storage (core.py, multimodal.py).
learner: Learning logic (core.py, fuzzy.py).
meta_learning_orchestrator: Meta-learning (orchestrator.py, models.py).
models: Data backends (postgres_client.py, redis_client.py, knowledge_graph_db.py).
plugins: Extensibility (llm_client.py, multimodal/interface.py).
policy: Governance (core.py, policy_manager.py).


Utils: metrics.py (Prometheus), otel_config.py (tracing), logging_utils.py (PII redaction).

SFE Flow:
python# Intent to Arbiter task
from arbiter.decision_optimizer import DecisionOptimizer
async def handle_intent(intent):
    optimizer = DecisionOptimizer()
    await optimizer.allocate_task({"id": "task123", "type": "code_fix", "data": intent})

4. Key Development Practices

Async Programming: Use asyncio/aiohttp. Avoid blocking calls; wrap sync code in executors.
Plugins: Register via registry.register in arbiter_plugin_registry.py.
Security: Enable PII redaction (logging_utils.py), audit dependencies for 2025 CVEs (e.g., semgrep scan).
Performance: Tune circuit breakers/retries (policy/circuit_breaker.py). Scale via Redis/Neo4j sharding.
Compliance: Document risk assessments for EU AI Act; use audit_log.py for traceability.
Debugging: Use logs (logs/), traces (otel_config.py), and health checks (utils.py).

Example Debug Config (VSCode launch.json):
json{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run Arbiter",
      "type": "python",
      "request": "launch",
      "program": "run_exploration.py",
      "args": ["--mode", "single"],
      "envFile": "${workspaceFolder}/.env"
    }
  ]
}

5. Submodule Guides

arbiter_growth: Handles agent state updates (arbiter_growth_manager.py), idempotency (idempotency.py), and storage (storage_backends.py).
bug_manager: Scans/fixes bugs (bug_manager.py, remediations.py) with notifications (notifications.py).
explainable_reasoner: Generates LLM explanations (explainable_reasoner.py) with adapters (adapters.py).
knowledge_graph: Manages knowledge (core.py) with multimodal support (multimodal.py).
learner: Learns from data (core.py) with fuzzy parsing (fuzzy.py).
meta_learning_orchestrator: Optimizes learning (orchestrator.py, models.py).
models: Provides data backends (postgres_client.py, redis_client.py, knowledge_graph_db.py).
policy: Enforces rules (core.py, policy_manager.py) from policies.json.
plugins: Extends functionality (llm_client.py, multimodal/interface.py).


6. Next Steps & Resources

Read Docs: Root README.md, submodule READMEs (e.g., policy/README.md).
Hands-On: Run run_exploration.py --mode arena; test multimodal with plugins/multimodal/interface.py.
Deep Dives:

Architecture: arbiter_architecture.mmd (render via mermaid.live).
Core: policy/core.py (governance), plugins/llm_client.py (LLM integration).


Community:

GitHub: https://github.com/unexpected-innovations/sfe
Discord: https://discord.gg/sfe-community
Wiki: https://github.com/unexpected-innovations/sfe/wiki


Learning: Search "agentic AI workflows 2025" or "EU AI Act high-risk systems" for context. Check NIST AI RMF resources.


7. Bugs/Common Pitfalls

Resolved: Pytest import errors (e.g., test_orchestrator_config.py); use mocks for Redis/Neo4j.
Pending: Quantum-resistant crypto (Kyber); test multimodal processing.
Pitfalls: Async deadlocks (use locks), unhandled LLM timeouts, adversarial inputs in learner.