<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Getting Started with the Self-Healing Import Fixer Module in Self-Fixing Engineer (SFE)

Welcome! If you're completely new to Code Factory or the Self-Fixing Engineer (SFE) platform, this guide assumes no prior knowledge and walks you through the basics of the Self-Healing Import Fixer module. SFE is an autonomous AI platform for code analysis, repair, and evolution. This module focuses on fixing Python import and dependency issues automatically while ensuring compliance.

As of September 10, 2025, this guide is up to date with version 1.0.

What is SFE and the Self-Healing Import Fixer?



SFE Overview: SFE is a self-healing AI system that fixes code bugs, optimizes performance, and evolves software autonomously. It's built by Novatrax Labs LLC and includes modules like arbiter (orchestration), guardrails (safety), and this one.

Self-Healing Import Fixer: Scans Python code for import problems (e.g., cycles, missing dependencies), fixes them, and validates changes with tools like Ruff, Mypy, and pytest. It uses AI for complex refactors and logs actions for compliance (e.g., EU AI Act, NIST AI RMF).



Key Concepts:



Analysis: Builds graphs to detect cycles, dead code, and policy violations (analyzer/core\_graph.py).

Healing: Auto-fixes imports and dependencies with backups (import\_fixer/fixer\_ast.py, fixer\_dep.py).

Validation: Runs linting, type checking, security scans, and tests (import\_fixer/fixer\_validate.py).

Plugins: Extends functionality with custom logic (import\_fixer/fixer\_plugins.py).

Audit: Ensures tamper-evident logging (analyzer/core\_audit.py).



Setup for New Users

Step 1: Environment



OS: Linux/macOS (Windows: use WSL2 for Redis/async support).

Python: 3.10+ (pyenv install 3.10).

Git: Clone the repository:git clone https://github.com/unexpected-innovations/sfe.git \&\& cd sfe/self\_healing\_import\_fixer





Dependencies: Create requirements.txt (based on code analysis):aiohttp>=3.8.0

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

tomli\_w>=1.0.0

graphviz>=0.20.0



Install:pip install -r requirements.txt





Services: Redis (optional, for caching):docker run -d -p 6379:6379 redis







If issues: Run pip check to verify dependencies.

Step 2: Configuration



Create .env:REDIS\_HOST=localhost

REDIS\_PORT=6379

OPENAI\_API\_KEY=sk-...  # Obtain from OpenAI

SLACK\_WEBHOOK=https://hooks.slack.com/...  # Optional

ENCRYPTION\_KEY=$(openssl rand -base64 32)

PRODUCTION\_MODE=false





Load: source .env.



Step 3: First Run



Verify setup:python cli.py selftest





Analyze a codebase:python cli.py analyze src/ --output-format json





Output: JSON report in report.json.

Next: Follow DEMO\_GUIDE.md for a full demo.



Common New User Questions



Where’s the code?: CLI in cli.py, analysis in analyzer/, fixes in import\_fixer/.

What if I get errors?: Run python cli.py selftest; check Troubleshooting in README.md.

How does it fit with SFE?: Feeds fixes to arbiter for orchestration; see README.md for integration.

Need help?: Join Discord (link in README.md) or check logs in logs/.





