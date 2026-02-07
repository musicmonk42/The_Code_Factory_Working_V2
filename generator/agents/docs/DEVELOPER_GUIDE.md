<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Developer Guide for README to Code Generator - Agents Module

This guide provides practical instructions for internal coders working on the agents module of the README to Code Generator project within The Code Factory. It covers development workflows, testing, debugging, and compliance requirements for a closed-source project adhering to regulated industry standards (SOC2, PCI DSS, HIPAA).

Getting Started

Prerequisites



Python 3.8 or higher

Git

External tools: bandit, semgrep, hadolint, checkov, eslint, golangci-lint, pypandoc, kubectl

Dependencies: Listed in requirements.txt (see README.md).



Setup



Clone the repository (internal access required):git clone <internal-repository-url>

cd Generator/agents





Install dependencies:pip install -r requirements.txt





Set environment variables:export COMPLIANCE\_MODE=true

export SFE\_OTEL\_EXPORTER\_TYPE=console

export SLACK\_WEBHOOK\_URL=<internal-slack-webhook>





Initialize the plugin directory:mkdir -p plugins







Development Workflow

Coding Standards



Style: Follow PEP 8, enforced via ruff. Run ruff check . before committing.

Security: Avoid hardcoded secrets and unsafe functions (e.g., eval, exec). Use Presidio for PII scrubbing.

Async: Use asyncio for all I/O operations to maintain scalability.



Writing Code



Module Structure: Work within the relevant submodule (codegen, critique, testgen, deploy, docgen) or generator\_plugin\_wrapper.py.

Plugins: Add new plugins to plugins/ and register them via omnicore\_engine.PLUGIN\_REGISTRY.

Templates: Update Jinja2 templates in templates/ for prompt generation, ensuring hot-reload compatibility.

Compliance: Enable COMPLIANCE\_MODE=true to enforce audit logging and PII scrubbing.



Testing

Run tests to validate changes:

pytest -v --cov=agents --cov-report=term-missing --cov-report=html --asyncio-mode=auto





Unit Tests: Located in test\_\*.py files for each module (e.g., test\_codegen\_agent.py).

Integration Tests: Cover end-to-end workflows (e.g., test\_e2e\_generator\_pipeline.py).

Coverage: Aim for 90%+ coverage. Use pytest-cov to check.

Mocks: Use unittest.mock for external services (e.g., LLM APIs, Presidio).



Debugging



Logs: Check audit logs in the configured logging system (e.g., SQLite database at history.db).

Metrics: Monitor Prometheus metrics (e.g., workflow\_success, response\_handler\_latency) via the configured exporter.

Traces: Use OpenTelemetry traces for debugging pipeline issues (exported to console by default).

Common Issues:

ModuleNotFoundError: Ensure PYTHONPATH includes D:\\Code\_Factory\\Generator\\agents.

LLM Failures: Check circuit breaker states in \*\_llm\_call.py modules.

Security Violations: Run bandit or semgrep to identify issues early.







Compliance Requirements



PII Scrubbing: Use presidio\_analyzer and presidio\_anonymizer to redact sensitive data (e.g., emails, API keys) in inputs and outputs.

Audit Logging: Ensure audit\_log.log\_action is called for all critical actions (e.g., workflow execution, LLM calls).

Provenance: Verify WorkflowOutput includes correlation\_id, timestamp, and model\_used.

Security Scanning: Integrate tools like bandit, semgrep, trivy, hadolint, and checkov in the pipeline.

Sandbox Execution: Run validations in isolated environments (e.g., Docker containers).



Key Development Tips



Error Handling: Implement retries and circuit breaking in \*\_llm\_call.py modules using tenacity and CircuitBreaker.

Performance: Optimize for async operations to handle concurrent requests efficiently.

Extensibility: Use the plugin-based architecture to add new LLM providers or tools.

Documentation: Update README.md or this guide for significant changes.



Troubleshooting



Dependency Issues: Verify all dependencies in requirements.txt are installed. Check for version conflicts with pipdeptree.

Pipeline Failures: Inspect WorkflowOutput.errors and audit logs for details.

Security Issues: Report vulnerabilities to security@codefactory.com per SECURITY.md.

Support: Contact support@codefactory.com for internal assistance.



