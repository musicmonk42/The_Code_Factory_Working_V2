README to Code Generator - Agents Module

The agents module is a core component of the README to Code Generator project within The Code Factory, designed to automate the transformation of natural language requirements (e.g., README files) into production-ready code, tests, deployment configurations, and documentation. It orchestrates a pipeline of specialized agents for code generation, critique, test generation, deployment, and documentation, ensuring compliance with regulated industry standards such as SOC2, PCI DSS, and HIPAA. The module integrates submodules (codegen, critique, testgen, deploy, docgen) under the generator\_plugin\_wrapper.py orchestrator, providing a robust, secure, and observable system for enterprise-grade software development.

Table of Contents



Overview

Features

Architecture

Submodules

Codegen Agent

Critique Agent

Testgen Agent

Deploy Agent

Docgen Agent

Generator Plugin Wrapper





Installation

Usage

Compliance and Security

Contributing

Support

License



Overview

The agents module is the backbone of the README to Code Generator, a project within The Code Factory that converts high-level requirements expressed in natural language (e.g., README files) into fully functional software artifacts. It leverages a modular, agent-based architecture to automate the software development lifecycle (SDLC), producing code, tests, deployment configurations, and documentation. The generator\_plugin\_wrapper.py orchestrates the pipeline, ensuring seamless integration of the codegen, critique, testgen, deploy, and docgen agents.

This module is designed for regulated industries, ensuring:



Security: PII/secret scrubbing with Presidio, secure sandbox execution, and audit logging.

Compliance: Adherence to SOC2, PCI DSS, and HIPAA through provenance tracking and auditability.

Observability: Integration with Prometheus metrics and OpenTelemetry tracing.

Robustness: Self-healing mechanisms, retry logic, and circuit breaking for reliability.



Features



Code Generation: Generates production-ready code in multiple languages (e.g., Python, JavaScript) from README requirements.

Code Critique: Identifies security vulnerabilities, coding issues, and applies automated fixes using linters (e.g., bandit, semgrep, eslint).

Test Generation: Produces comprehensive unit and integration tests with high coverage using frameworks like pytest.

Deployment Configuration: Generates validated deployment artifacts (e.g., Dockerfiles, Kubernetes manifests, Terraform configurations).

Documentation Generation: Creates professional documentation (e.g., README, API docs) in formats like Markdown and reStructuredText.

Pipeline Orchestration: Integrates all agents into a cohesive workflow with error handling and self-healing.

Compliance: Supports regulated industries with PII scrubbing, audit logging, and provenance tracking.

Observability: Provides Prometheus metrics (e.g., workflow\_success, response\_handler\_latency) and OpenTelemetry tracing.

Extensibility: Plugin-based architecture for adding new providers, linters, and validators.



Architecture

The agents module follows a modular, agent-based architecture, orchestrated by generator\_plugin\_wrapper.py. Each submodule handles a specific phase of the SDLC, with clear interfaces and dependencies managed via the omnicore\_engine plugin registry. The pipeline flow is:



Clarify: Refines raw README requirements into structured specifications using the clarifier plugin.

Codegen: The codegen\_agent generates source code based on clarified requirements.

Critique: The critique\_agent analyzes code for issues and applies fixes.

Testgen: The testgen\_agent generates tests to ensure code quality.

Deploy: The deploy\_agent creates and validates deployment configurations.

Docgen: The docgen\_agent produces documentation for the codebase.



Key components include:



LLM Orchestration: Managed by \*\_llm\_call.py modules, supporting providers like OpenAI, Gemini, and Grok.

Prompt Management: Handled by \*\_prompt.py modules with Jinja2 templates and retrieval-augmented generation (RAG).

Response Handling: Processed by \*\_response\_handler.py modules for normalization and validation.

Validation: Performed by \*\_validator.py modules to ensure quality and compliance.

Security: Enforced via Presidio for PII scrubbing and tools like bandit, semgrep, trivy, hadolint, and checkov.



The architecture ensures modularity, scalability, and compliance, with each agent operating asynchronously and integrating with observability tools.

Submodules

Codegen Agent



Files: codegen\_agent.py, codegen\_llm\_call.py, codegen\_prompt.py, codegen\_response\_handler.py

Purpose: Generates production-ready code from README requirements.

Features:

Supports multiple languages (e.g., Python, JavaScript).

Uses LLMs with RAG for context-aware code generation.

Applies security scans (e.g., bandit) and human-in-the-loop (HITL) reviews.





Key Components:

codegen\_prompt.py: Builds prompts with Jinja2 templates and RAG.

codegen\_llm\_call.py: Manages LLM calls with circuit breaking and rate limiting.

codegen\_response\_handler.py: Parses and validates LLM outputs.







Critique Agent



Files: critique\_agent.py, critique\_fixer.py, critique\_linter.py, critique\_llm\_call.py, critique\_prompt.py

Purpose: Analyzes generated code for issues and applies automated fixes.

Features:

Uses linters (e.g., ruff, eslint, golangci-lint) and semantic critique via LLMs.

Applies fixes using regex, diff patching, and LLM-based strategies.

Supports HITL for critical fixes.





Key Components:

critique\_prompt.py: Generates critique prompts with multi-modal data.

critique\_llm\_call.py: Handles LLM calls for semantic analysis.

critique\_linter.py: Runs static analysis tools.

critique\_fixer.py: Applies automated fixes with validation.







Testgen Agent



Files: testgen\_agent.py, testgen\_llm\_call.py, testgen\_prompt.py, testgen\_response\_handler.py, testgen\_validator.py

Purpose: Generates unit and integration tests with high coverage.

Features:

Supports test frameworks (e.g., pytest, jest).

Validates test quality (coverage, mutation, property-based testing).

Integrates with codegen outputs for context-aware test generation.





Key Components:

testgen\_prompt.py: Builds test generation prompts with RAG.

testgen\_llm\_call.py: Manages LLM calls for test generation.

testgen\_response\_handler.py: Parses and validates test outputs.

testgen\_validator.py: Ensures test quality and coverage.







Deploy Agent



Files: deploy\_agent.py, deploy\_llm\_call.py, deploy\_prompt.py, deploy\_response\_handler.py, deploy\_validator.py

Purpose: Generates and validates deployment configurations (e.g., Docker, Kubernetes, Terraform).

Features:

Produces production-grade deployment artifacts.

Validates configurations using tools like hadolint and checkov.

Supports multi-target deployments (e.g., cloud, on-premises).





Key Components:

deploy\_prompt.py: Generates deployment prompts with Jinja2 templates.

deploy\_llm\_call.py: Manages LLM calls for configuration generation.

deploy\_response\_handler.py: Normalizes and validates LLM outputs.

deploy\_validator.py: Ensures deployment artifacts are secure and functional.







Docgen Agent



Files: docgen\_agent.py, docgen\_llm\_call.py, docgen\_prompt.py, docgen\_response\_handler.py, docgen\_validator.py

Purpose: Generates professional documentation (e.g., README, API docs).

Features:

Supports multiple formats (Markdown, reStructuredText, HTML).

Validates documentation quality using NLP metrics (e.g., Flesch-Kincaid).

Integrates with code and test outputs for comprehensive docs.





Key Components:

docgen\_prompt.py: Builds documentation prompts with RAG.

docgen\_llm\_call.py: Manages LLM calls for documentation generation.

docgen\_response\_handler.py: Processes and formats documentation outputs.

docgen\_validator.py: Validates documentation for completeness and readability.







Generator Plugin Wrapper



File: generator\_plugin\_wrapper.py

Purpose: Orchestrates the full pipeline, integrating all agents.

Features:

Coordinates clarify, codegen, critique, testgen, deploy, and docgen phases.

Handles retries, circuit breaking, and self-healing.

Ensures compliance with audit logging and PII scrubbing.





Key Components:

Uses Pydantic for input/output validation.

Integrates with omnicore\_engine for plugin management.

Logs metrics and traces for observability.







Installation

Prerequisites



Python 3.8 or higher

Git

External tools: bandit, semgrep, hadolint, checkov, eslint, golangci-lint, pypandoc, kubectl

Dependencies (see requirements.txt):pip install aiohttp aioredis tiktoken openai google-generativeai pydantic prometheus-client opentelemetry-sdk presidio-analyzer presidio-anonymizer sentence-transformers pinecone-client pyyaml hcl2 ruamel.yaml gitpython esprima pypandoc docutils nltk







Setup



Clone the repository:git clone <repository-url>

cd Generator/agents





Install dependencies:pip install -r requirements.txt





Set environment variables for compliance and observability:export COMPLIANCE\_MODE=true

export SFE\_OTEL\_EXPORTER\_TYPE=console

export SLACK\_WEBHOOK\_URL=<your-slack-webhook>





Initialize the plugin directory:mkdir -p plugins







Directory Structure

agents/

├── codegen\_agent/

│   ├── codegen\_agent.py

│   ├── codegen\_llm\_call.py

│   ├── codegen\_prompt.py

│   ├── codegen\_response\_handler.py

│   ├── \_\_init\_\_.py

│   ├── README.md

├── critique\_agent/

│   ├── critique\_agent.py

│   ├── critique\_fixer.py

│   ├── critique\_linter.py

│   ├── critique\_llm\_call.py

│   ├── critique\_prompt.py

│   ├── \_\_init\_\_.py

│   ├── README.md

├── testgen\_agent/

│   ├── testgen\_agent.py

│   ├── testgen\_llm\_call.py

│   ├── testgen\_prompt.py

│   ├── testgen\_response\_handler.py

│   ├── testgen\_validator.py

│   ├── \_\_init\_\_.py

│   ├── README.md

├── deploy\_agent/

│   ├── deploy\_agent.py

│   ├── deploy\_llm\_call.py

│   ├── deploy\_prompt.py

│   ├── deploy\_response\_handler.py

│   ├── deploy\_validator.py

│   ├── \_\_init\_\_.py

│   ├── README.md

├── docgen\_agent/

│   ├── docgen\_agent.py

│   ├── docgen\_llm\_call.py

│   ├── docgen\_prompt.py

│   ├── docgen\_response\_handler.py

│   ├── docgen\_validator.py

│   ├── \_\_init\_\_.py

│   ├── README.md

├── generator\_plugin\_wrapper.py

├── \_\_init\_\_.py



Usage

Running the Pipeline

To run the full pipeline, use the generator\_plugin\_wrapper.py script:

python -m agents.generator\_plugin\_wrapper \\

&nbsp; --repo-path /path/to/repo \\

&nbsp; --readme "A Flask web service with a single endpoint" \\

&nbsp; --config '{"language": "python", "framework": "flask"}'



Example Input

{

&nbsp; "correlation\_id": "123e4567-e89b-12d3-a456-426614174000",

&nbsp; "repo\_path": "/path/to/repo",

&nbsp; "readme": "A simple Flask web service. Contact: test@example.com",

&nbsp; "config": {

&nbsp;   "language": "python",

&nbsp;   "framework": "flask"

&nbsp; }

}



Example Output

{

&nbsp; "status": "success",

&nbsp; "correlation\_id": "123e4567-e89b-12d3-a456-426614174000",

&nbsp; "timestamp": "2025-09-01T12:00:00Z",

&nbsp; "final\_results": {

&nbsp;   "code\_files": {

&nbsp;     "main.py": "import flask\\napp = flask.Flask(\_\_name\_\_)\\n..."

&nbsp;   },

&nbsp;   "issues": \[],

&nbsp;   "test\_files": {

&nbsp;     "test\_main.py": "import pytest\\nfrom main import hello\\n..."

&nbsp;   },

&nbsp;   "deployment\_artifacts": {

&nbsp;     "docker": "FROM python:3.9-slim\\n..."

&nbsp;   },

&nbsp;   "documentation": "# Updated README\\nGenerated Flask app documentation."

&nbsp; },

&nbsp; "errors": \[],

&nbsp; "provenance": {

&nbsp;   "model\_used": "gpt-4o",

&nbsp;   "timestamp": "2025-09-01T12:00:00Z"

&nbsp; }

}



Testing

Run the test suites to validate functionality:

pytest -v --cov=agents --cov-report=term-missing --cov-report=html --asyncio-mode=auto



Compliance and Security

The agents module is designed for regulated industries with the following features:



PII/Secret Scrubbing: Uses Presidio to redact sensitive data (e.g., emails, API keys) in inputs and outputs.

Audit Logging: Logs all critical actions (e.g., workflow start, LLM calls, errors) using audit\_log.

Provenance Tracking: Records metadata (e.g., correlation\_id, timestamp, model\_used) for traceability.

Security Scanning: Integrates tools like bandit, semgrep, trivy, hadolint, and checkov to detect vulnerabilities.

Sandbox Execution: Runs validations in isolated environments to prevent unauthorized access.

Observability: Exports metrics to Prometheus and traces to OpenTelemetry for monitoring.

Human-in-the-Loop (HITL): Supports HITL reviews for critical operations (e.g., code fixes, deployment configurations).



Compliance Notes



SOC2: Ensures data integrity and auditability through logging and provenance.

PCI DSS: Protects sensitive data with PII scrubbing and secure LLM interactions.

HIPAA: Safeguards protected health information (PHI) with anonymization and access controls.



Contributing

Contributions are welcome! Please follow these guidelines:



Fork the Repository: Create a fork and submit pull requests.

Code Style: Adhere to PEP 8 for Python code, using ruff for linting.

Testing: Write tests for new features using pytest. Ensure 90%+ coverage.

Commits: Use clear commit messages following the Conventional Commits format.

Security: Report vulnerabilities privately via security@codefactory.com.

Documentation: Update README and relevant docs for all changes.



See CONTRIBUTING.md for detailed instructions.

Support

For issues or questions:



File an Issue: Use the GitHub issue tracker.

Contact: Email support@codefactory.com or join our Slack channel (contact for invite).

Security Reports: Send sensitive issues to security@codefactory.com for private disclosure.



License

Novatrax Labs LLC all rights reserved

