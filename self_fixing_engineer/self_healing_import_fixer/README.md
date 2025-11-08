Self-Healing Import Fixer & Analyzer
Enterprise-Grade Automated Python Import/Dependency Healing and Policy Enforcement
🚩 What Is This?

A security-first, audit-heavy, production-grade Python platform that scans, analyzes, and automatically fixes import problems, dependency drift, and policy violations across large codebases. It enforces architectural rules, validates every change, and produces tamper-evident logs for compliance.

If your Python repo is suffering from:

Import cycles
Relative import hell
Stale/missing/unused dependencies
Policy/compliance risks
Unmaintainable code due to import spaghetti

This tool will find it, fix it, and prove what it did.
For new users: If you're new to the Self-Fixing Engineer (SFE) platform or Code Factory, start with GETTING_STARTED.md for basics and DEMO_GUIDE.md to run your first demo.
Version: 1.0 (stable as of September 10, 2025)SPDX-License-Identifier: MITCopyright: © 2025 Unexpected Innovations Inc.
💡 Key Features (No Hype, Just Facts)

Comprehensive Static Analysis

Builds import/call graphs (AST-based, analyzer/core_graph.py)
Detects cycles, dead code, and dynamic imports
Reports architectural violations with line-level precision


Automated Healing

Resolves import cycles mechanically or with AI (import_fixer/fixer_ast.py)
Converts relative to absolute imports
Synchronizes pyproject.toml and requirements.txt (import_fixer/fixer_dep.py)
Atomic changes with backups and rollbacks


AI-Powered Refactoring

Uses OpenAI or compatible LLMs for suggestions (import_fixer/fixer_ai.py)
Sanitized prompts, rate-limited, operator-approved in production


Validation Pipeline

Validates fixes via compilation, linting (Ruff/Flake8), type checking (Mypy), security scans (Bandit/pip-audit/Snyk), and testing (pytest) (import_fixer/fixer_validate.py)
Automatic rollback on failure


Plugin Architecture

Whitelisted, HMAC-signed plugins for custom logic (import_fixer/fixer_plugins.py)
Hooks for pre/post-healing events


Audit & Compliance

Tamper-evident logs with HMAC signatures (analyzer/core_audit.py)
Slack/SIEM alerts for violations
Prometheus metrics for monitoring (analyzer/core_utils.py)



Table of Contents

Features
Getting Started
Usage
CLI Commands
Configuration
SFE Integration
Extending with Plugins
Monitoring & Alerting
Troubleshooting
Security Model
Contribution Guidelines
Tests
License
Support

Getting Started
See GETTING_STARTED.md for detailed setup if you're new. Quick overview:
Prerequisites

OS: Linux/macOS (Windows: use WSL2 for Redis/async).
Python: 3.10+ (pyenv install 3.10).
Dependencies: Install via requirements.txt:pip install -r requirements.txt

Key dependencies:aiohttp>=3.8.0
openai>=1.0.0
tiktoken>=0.7.0
ruff>=0.4.0
mypy>=1.10.0
bandit>=1.7.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
redis>=5.0.0
boto3>=1.28.0
tenacity>=8.2.0
tomli>=2.0.0
tomli_w>=1.0.0
graphviz>=0.20.0

Full list in requirements.txt.
Services: Redis (optional, port 6379):docker run -d -p 6379:6379 redis


Environment Variables: Set in .env:REDIS_HOST=localhost
REDIS_PORT=6379
OPENAI_API_KEY=sk-...
SLACK_WEBHOOK=https://hooks.slack.com/...
ENCRYPTION_KEY=your_32_byte_key_base64_encoded
PRODUCTION_MODE=false



Installation

Clone: git clone https://github.com/unexpected-innovations/sfe.git && cd sfe/self_healing_import_fixer
Install: pip install -r requirements.txt
Load env: source .env
Run selftest: python cli.py selftest
Run demo: See DEMO_GUIDE.md.

Docker Setup
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "cli.py", "selftest"]

Build and run:
docker build -t sfe-import-fixer .
docker run --env-file .env sfe-import-fixer

Use Docker Compose for Redis:
version: '3.8'
services:
  import-fixer:
    build: .
    env_file: .env
    volumes:
      - .:/app
  redis:
    image: redis:latest
    ports:
      - "6379:6379"

Usage

Analyze: Scan for import issues:python cli.py analyze src/ --output-format json > report.json


Heal: Fix issues interactively:python cli.py heal src/ --fix-cycles --interactive


Serve Dashboard: Visualize results (if implemented):python cli.py serve src/ --port 8000



CLI Commands

analyze: Scan for cycles, dead code, policy violations.
heal: Apply fixes (cycles, dependencies, dynamic imports).
serve: Launch dashboard (optional, if FastAPI enabled).
selftest: Verify setup and dependencies.
--list-plugins: List loaded plugins.

Run python cli.py --help for details.
Configuration
Create config.yaml:
analyzer:
  exclude_patterns: ['tests/', 'venv/']
healer:
  ai_model: gpt-4
  max_tokens: 300
audit:
  slack_webhook: https://hooks.slack.com/...
  audit_hmac_key: your_key

SFE Integration
The module integrates with SFE's arbiter for orchestration:
from self_healing_import_fixer.import_fixer.import_fixer_engine import heal_imports
async def integrate_with_arbiter(project_root):
    result = await heal_imports(project_root, dry_run=False, auto_add_deps=True, ai_enabled=True)
    # Publish to arbiter queue
    from arbiter.message_queue_service import MessageQueueService
    mq = MessageQueueService()
    await mq.publish("fix_completed", result)

Extending with Plugins
Create plugins/my_plugin.py:
from fixer_plugins import BasePlugin
class MyPlugin(BasePlugin):
    async def pre_healing(self, context):
        print("Custom pre-healing logic")

Register in cli.py:
from fixer_plugins import PluginManager
plugin_manager = PluginManager()
plugin_manager.register_plugin("my_plugin", MyPlugin())

Monitoring & Alerting

Slack: Critical errors to #ops-alerts.
SIEM: Logs to Splunk (analyzer/core_audit.py).
Prometheus: Metrics at :9090/metrics (if dashboard running).

Troubleshooting

Missing Tool?: Run python cli.py selftest.
Redis Errors?: Check REDIS_HOST, ensure Docker Redis running.
AWS SSM Errors?: Verify IAM permissions.
Plugin Fails?: Check FIXER_PLUGIN_SIGNATURE_KEY.
Logs: See logs/, audit.log.

Security Model: Non-Negotiable Rules

No secrets in ENV/config in production.
No dynamic plugins in production.
No destructive changes without operator approval.
All actions audited with HMAC signatures.
Fail safe and alert on errors.

Contribution Guidelines

Fork and branch: git checkout -b feature/your-feature.
Ensure tests pass: pytest --cov=self_healing_import_fixer --cov-report=html.
Lint: ruff check ..
Type check: mypy ..
Security scan: bandit -r ..
Submit PR with coverage >95%.

Tests
Run tests:
pytest --cov=self_healing_import_fixer --cov-report=html

Key tests in tests/:

test_analyzer.py: Graph building, cycle detection.
test_fixer_dep.py: Dependency syncing.
Example:@pytest.mark.asyncio
async def test_heal_cycle(mocker):
    mocker.patch("fixer_ast.CycleHealer.heal", return_value={"status": "success"})
    result = await heal_imports("src/", dry_run=True)
    assert result["cycle_healing_report"]["status"] == "success"



License
MIT License. See LICENSE.
Support

Email: support@unexpectedinnovations.com
Slack: #ops-alerts
Discord: https://discord.gg/sfe-community
Wiki: /wiki

