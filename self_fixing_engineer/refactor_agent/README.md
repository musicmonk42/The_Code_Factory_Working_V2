Refactor Agent Module - Self-Fixing Engineer (SFE) 🚀
Universal Crew v10.0.0 - The "Infinite Orchestration" EditionProprietary Technology by Novatrax Labs
Orchestrate AI, human, and plugin agents for self-healing, refactoring, and governance with runtime dynamism.
The Refactor Agent module, or Universal Crew ∞, is the orchestration heart of the Self-Fixing Engineer (SFE) platform. It manages a dynamic crew of AI, human, and plugin agents to automate code refactoring, testing, simulation, and governance across enterprise codebases. Defined by a YAML schema in refactor_agent.yaml, the module supports runtime-resolved roles, skills, and escalation paths, integrating with cloud-native services (AWS, Azure, NATS) and plugins for security, observability, and visualization. Designed for scalability and zero-trust security, it enables continuous learning, cross-repo refactoring, and human-in-the-loop oversight.
Crafted with precision in Fairhope, Alabama, USA.Orchestrate the future with Universal Crew’s infinite adaptability.
Version: 10.0.0 (stable as of September 10, 2025)SPDX-License-Identifier: MITCopyright: © 2025 Novatrax Labs LLC
For new users: If you're new to SFE or Code Factory, start with GETTING_STARTED.md for basics and DEMO_GUIDE.md to run your first demo.

Table of Contents

Features
Architecture
Getting Started
Prerequisites
Installation
Configuration


Usage
CLI Mode
REST API Mode
Dashboard Mode
Monitoring and Logging


Extending Universal Crew
Adding Agents
Custom Plugins
Defining Escalation Paths
Extending Visualizations


Key Components
SFE Integration
Tests
Troubleshooting
Best Practices
Contribution Guidelines
Roadmap
Support
License


Features
The Universal Crew delivers enterprise-grade orchestration:

Dynamic Agent Management: Runtime-loaded agents from refactor_agent.yaml with AI, human, and plugin types.
Escalation Paths: Configurable escalation (e.g., AI to human) with roles/skills resolved at runtime.
Plugin System: Secure, whitelisted plugins for healing, judging, and auditing.
Observability: Prometheus metrics, OpenTelemetry tracing, and tamper-evident auditing.
Compliance: Aligns with EU AI Act and NIST AI RMF via provenance and logging.
Multi-Modal Support: Handles code, text, images, and audio in simulations.
Continuous Learning: Agents evolve via self-healing and oracle integration.


Architecture
The module uses a YAML-driven schema (refactor_agent.yaml) for agents, plugins, and integrations:

Agents: Defined with manifests/entrypoints (plugins/refactor/smart_refactor_agent.py).
Plugins: Loaded via registry with security checks.
Orchestration: Runtime resolution via configdb/vault.
Backends: AWS/Azure/GCP for secrets; NATS for messaging.
Analytics: Optional, with anonymous stats.

Submodules:

analyzer/: Graph building (core_graph.py), policy enforcement (core_policy.py).
import_fixer/: Dependency healing (fixer_dep.py), AST fixes (fixer_ast.py), validation (fixer_validate.py).


Getting Started
See GETTING_STARTED.md for detailed setup if you're new. Quick overview:
Prerequisites

OS: Linux/macOS (Windows: WSL2 for async/Redis).
Python: 3.10+ (pyenv install 3.10).
Dependencies: pip install -r requirements.txt (includes aiohttp, openai, redis, boto3, etc.).
Services: Redis, Postgres, Docker. Use Docker Compose:version: '3.8'
services:
  refactor-agent:
    build: .
    env_file: .env
    volumes:
      - .:/app
  redis:
    image: redis:latest
    ports:
      - "6379:6379"


Environment Variables: Set in .env:REDIS_URL=redis://localhost:6379
OPENAI_API_KEY=sk-...
PRODUCTION_MODE=false



Installation

Clone: git clone https://github.com/unexpected-innovations/sfe.git && cd sfe/refactor_agent
Install: pip install -r requirements.txt
Load env: source .env
Run selftest: python cli.py selftest (if CLI implemented).
Run demo: See DEMO_GUIDE.md.

Configuration
Edit refactor_agent.yaml for agents/plugins/secrets.

Usage

CLI Mode: python cli.py refactor --mode single --config refactor_agent.yaml
REST API Mode: uvicorn api:app --port 8000; docs at /docs.
Dashboard Mode: streamlit run dashboard.py.
Monitoring and Logging: Prometheus at :9090/metrics; logs in logs/.


Extending Universal Crew

Adding Agents: Add to refactor_agent.yaml:agents:
  - id: new_agent
    manifest: plugins/new/manifest.json
    entrypoint: plugins/new/new_agent.py


Custom Plugins: Create plugins/my_plugin.py and register.
Defining Escalation Paths: Update escalation_paths in YAML.
Extending Visualizations: Modify workflow_viz.py.


Key Components

refactor_agent.yaml: Schema for agents/plugins.
plugins/refactor/smart_refactor_agent.py: Core refactor agent.
analyzer/core_graph.py: Graph analysis.
import_fixer/fixer_dep.py: Dependency healing.

SFE Integration
Integrate with arbiter for orchestration:
from arbiter.message_queue_service import MessageQueueService
async def integrate_refactor(result):
    mq = MessageQueueService()
    await mq.publish("refactor_completed", result)


Tests
Run: pytest --cov=plugins.refactor --cov-report=html.Coverage target: 90%. Key tests in tests/ for agents/plugins.
Example:
@pytest.mark.asyncio
async def test_refactor(mocker):
    mocker.patch("smart_refactor_agent.refactor", return_value={"status": "success"})
    result = await refactor_code("src/", dry_run=True)
    assert result["status"] == "success"


Troubleshooting

Missing Plugin?: Check refactor_agent.yaml manifests.
Redis Errors?: Ensure Docker Redis running: docker ps.
AWS Issues?: Verify IAM for SSM/secrets.
Logs: See logs/, sandbox_audit.log.


Best Practices

Use runtime config for agents.
Enable provenance for audits.
Monitor with Prometheus.
Test plugins with mocks.


Contribution Guidelines

Code Style: PEP 8, black, ruff.
Testing: Add to tests/; 90%+ coverage.
Documentation: Update YAML/manifests.
PRs: Run pytest and ruff check.


Roadmap

Q4 2025: Grok 3 integration, chaos testing.
Future: Dynamic escalation, multi-modal support.


Support

Email: support@novatraxlabs.com
GitHub: /issues
Discord: https://discord.gg/sfe-community
Wiki: /wiki


License
Proprietary and Confidential © 2025 Novatrax Labs. All rights reserved.
For licensing: support@novatraxlabs.com.