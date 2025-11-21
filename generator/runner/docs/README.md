# Self-Fixing Engineer: Runners Module

A modular, contract-driven, extensible engine for orchestrating, executing, parsing, and reporting on tests, coverage, and mutation analysis across any language or CI/CD environment. Designed for highly regulated industries (e.g., finance, healthcare, aerospace), it ensures traceability, reproducibility, security, and auditability.

---

## Table of Contents

- [Project Purpose](#project-purpose)
- [Key Features](#key-features)
- [Architecture Overview](#architecture-overview)
- [File-by-File Guide](#file-by-file-guide)
- [How It Works](#how-it-works)
- [Input & Output Contracts](#input--output-contracts)
- [Extending the Runners Module](#extending-the-runners-module)
- [Observability, Logging, & Metrics](#observability-logging--metrics)
- [Error Handling & Reliability](#error-handling--reliability)
- [Configuration](#configuration)
- [Security & Sandboxing](#security--sandboxing)
- [CI/CD & DevOps Integration](#cicd--devops-integration)
- [Testing & Coverage](#testing--coverage)
- [Contributing](#contributing)
- [FAQ](#faq)
- [References](#references)

---

## Project Purpose

The `runner` module is the execution and validation backbone of the Self-Fixing Engineer platform, enabling robust test execution, coverage analysis, and mutation testing across diverse programming languages and environments. It is built for extensibility, auditability, and compliance with regulated industry standards, supporting CI/CD pipelines and providing comprehensive observability for DevOps and SRE teams.

---

## Key Features

- **Multi-Language Support**: Executes tests for Python (`unittest`, `pytest`, `behave`, `robot`), Java (`JUnit`, `Surefire`, `JaCoCo`), JavaScript (`Jest`, `Istanbul`), Go, and more, with auto-detection capabilities.
- **Formal Contract Enforcement**: Uses versioned Pydantic schemas (`TaskPayload`, `TaskResult`, `BatchTaskPayload`) for strict input/output validation.
- **Asynchronous & Parallel Execution**: Leverages `asyncio` and `concurrent.futures` for high-performance task orchestration.
- **Extensible Backends & Parsers**: Supports pluggable backends (e.g., Docker, Kubernetes, Lambda) and parsers (e.g., JUnit, Cobertura) via registries.
- **Mutation & Fuzz Testing**: Integrates with tools like `mutmut`, `Hypothesis`, and `Stryker` for advanced testing.
- **Observability**: Integrates with Prometheus, OpenTelemetry, and external sinks (e.g., Datadog, Splunk, New Relic) for metrics and logs.
- **Security**: Implements PII redaction, cryptographic signing (HMAC, RSA, ECDSA), and encryption (Fernet) for logs and data.
- **Auditability**: Ensures traceability with structured errors, provenance tracking, and comprehensive logging.
- **CI/CD Integration**: Seamless integration with GitHub Actions, Jenkins, and other CI systems via CLI, API, or Docker.

---

## Architecture Overview

The `runner` module is modular and loosely coupled, with components for configuration, execution, parsing, logging, metrics, and error handling. Key components include:

- **runner_app.py**: Textual-based TUI for interactive task submission and monitoring.
- **runner_backends.py**: Pluggable execution backends (Docker, Podman, Kubernetes, Lambda, SSH, Node.js, Go, Java).
- **runner_config.py**: Pydantic-based configuration with versioning, secrets management, and dynamic reloading.
- **runner_contracts.py**: Pydantic schemas for task inputs/outputs with versioning.
- **runner_core.py**: Core logic for task orchestration, queuing, and execution.
- **runner_errors.py**: Structured error hierarchy with unique error codes and OpenTelemetry tracing.
- **runner_logging.py**: Structured logging with redaction, signing, and multi-sink support.
- **runner_metrics.py**: Prometheus metrics with external exporters and anomaly-based alerting.
- **runner_mutation.py**: Mutation and fuzz testing with multi-language support.
- **runner_parsers.py**: Parsers for test and coverage reports (JUnit, Cobertura, Jest, etc.).
- **runner_utils.py**: Utilities for file handling, PII redaction, encryption, and provenance.

---

## File-by-File Guide

| File                  | Purpose                                                                 |
|-----------------------|-------------------------------------------------------------------------|
| `runner_app.py`       | Textual TUI for task submission, log viewing, and metrics visualization. |
| `runner_backends.py`  | Execution backends for Docker, Kubernetes, Lambda, etc., with health checks. |
| `runner_config.py`    | Configuration management with Pydantic, secrets, and dynamic reloading.  |
| `runner_contracts.py` | Versioned Pydantic schemas for task inputs/outputs.                     |
| `runner_core.py`      | Core task orchestration and execution logic.                            |
| `runner_errors.py`    | Structured error hierarchy with unique codes and tracing.               |
| `runner_logging.py`   | Structured logging with redaction, signing, and multi-sink support.     |
| `runner_metrics.py`   | Prometheus metrics, external exporters, and anomaly-based alerting.     |
| `runner_mutation.py`  | Mutation and fuzz testing with multi-language support.                  |
| `runner_parsers.py`   | Parsers for test/coverage reports with versioned schemas.               |
| `runner_utils.py`     | Utilities for file handling, PII redaction, encryption, and provenance. |

---

## How It Works

1. **Configuration**: Load `RunnerConfig` from `runner.yaml` or environment variables, validated by Pydantic.
2. **Task Submission**: Users submit tasks via TUI (`runner_app.py`) or CLI/API (`runner_core.py`), using `TaskPayload`.
3. **Execution**: `Runner` orchestrates tasks, delegating to a backend (e.g., `DockerBackend`) for execution.
4. **Parsing**: Test and coverage outputs are parsed (`runner_parsers.py`) into `TestReportSchema`/`CoverageReportSchema`.
5. **Mutation/Fuzz Testing**: Optional mutation (`mutmut`, `Stryker`) or fuzz testing (`Hypothesis`) via `runner_mutation.py`.
6. **Logging**: Structured logs with redaction and signing are written to sinks (`runner_logging.py`).
7. **Metrics**: Prometheus metrics are collected and exported to external systems (`runner_metrics.py`).
8. **Error Handling**: Structured errors (`runner_errors.py`) ensure traceability and auditability.

**Example Workflow**:
```yaml
# runner.yaml
version: 4
backend: docker
framework: pytest
log_sinks:
  - type: file
    config: { path: "logs/test.log" }

# Run via CLI
python -m runner.core --config runner.yaml --test-dir tests/ --code-dir src/ --output-dir output/

# Example test file (tests/test_example.py)
def test_add():
    assert add(1, 2) == 3

Output is parsed, metrics are collected, and logs are written with provenance.

Input & Output Contracts

TaskPayload: Defines test/code files, output path, timeout, and metadata (schema version: 2).
TaskResult: Captures execution status, results, and timestamps (schema version: 2).
BatchTaskPayload: Groups multiple TaskPayloads for batch execution.
TestReportSchema: Parsed test results (total tests, pass rate, test cases).
CoverageReportSchema: Parsed coverage data (percentage, file-level details).

All schemas are versioned and validated using Pydantic, ensuring compliance and auditability.

Extending the Runners Module

New Backend: Implement a subclass of ExecutionBackend in runner_backends.py and register in BACKEND_REGISTRY.from runner.backends import ExecutionBackend, BACKEND_REGISTRY
class CustomBackend(ExecutionBackend):
    async def execute(self, task): ...
BACKEND_REGISTRY['custom'] = CustomBackend


New Parser: Add an async parser in runner_parsers.py with @register_test_parser or @register_coverage_parser.from runner.parsers import register_test_parser
@register_test_parser('custom_format')
async def parse_custom_format(file_path): ...


New Mutator: Register in runner_mutation.py via register_mutator.from runner.mutation import register_mutator
register_mutator('lang', 'tool', ['.ext'], run_func, parse_func)



All extensions must include tests and contract updates, validated in CI.

Observability, Logging, & Metrics

Logging: Structured JSON logs with redaction (PII_PATTERNS), signing (HMAC/RSA/ECDSA), and encryption (Fernet). Supports file, stream, Datadog, Splunk, and New Relic sinks.
Metrics: Prometheus metrics (RUN_SUCCESS, RUN_PASS_RATE, RUN_RESOURCE_USAGE, etc.) with exporters for Datadog and CloudWatch. Anomaly detection triggers alerts.
Tracing: OpenTelemetry integration for distributed tracing, with graceful degradation if unavailable.
Search: In-memory log search via search_logs for audit and debugging.

Example Metric:
# HELP runner_pass_rate Overall test pass rate (0.0 to 1.0)
# TYPE runner_pass_rate gauge
runner_pass_rate 0.85


Error Handling & Reliability

Structured Errors: RunnerError hierarchy (BackendError, TestExecutionError, etc.) with unique codes in ERROR_CODE_REGISTRY.
Recovery: Backends include health checks and recovery mechanisms (e.g., container restart).
Timeouts: Configurable timeouts with TimeoutError for task termination.
Persistence: File operations use atomic writes with backups (runner_utils.py).
Traceability: Errors include task_id, run_id, and provenance for audit trails.

Example Error:
raise TestExecutionError(detail="Test failed", task_id="123", cause=Exception("AssertionError"))


Configuration
Configuration is defined in runner.yaml or environment variables, validated by RunnerConfig (Pydantic, version 4).
Example runner.yaml:
version: 4
backend: docker
framework: pytest
parallel_workers: 2
timeout: 300
mutation: true
log_sinks:
  - type: file
    config: { path: "logs/test.log" }
  - type: datadog
    config: { api_key: "sk-abc123" }
secrets:
  api_key: sk-abc123

Environment Overrides:
export RUNNER_BACKEND=podman
export RUNNER_TIMEOUT=120


Security & Sandboxing

Sandboxing: Backends (e.g., Docker, Firecracker) isolate test execution with resource limits (CPU, memory, network).
Data Protection: PII redaction (emails, SSNs, API keys) and log encryption using Fernet.
Cryptographic Signing: Logs and outputs signed with HMAC, RSA, or ECDSA for integrity.
Validation: All inputs/outputs are validated via Pydantic schemas, preventing injection attacks.
Secrets Management: Supports HashiCorp Vault for secure storage (runner_config.py).


CI/CD & DevOps Integration

GitHub Actions: Workflow for test execution, coverage, mutation, and metrics export.name: Runner CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: python -m runner.core --config runner.yaml


Jenkins/Bamboo: CLI and API endpoints for pipeline integration.
Docker: Official Dockerfile for reproducible builds:FROM python:3.11
COPY . /runner
RUN pip install -r /runner/requirements.txt
CMD ["python", "-m", "runner.core", "--config", "runner.yaml"]


Observability: Metrics and logs integrate with Prometheus, Grafana, Loki, Datadog, Splunk, and New Relic.


Testing & Coverage

Test Suite: Comprehensive unit, integration, and E2E tests in /tests directory, achieving 100% branch coverage.
Test Files:
test_runner_app.py: Tests TUI functionality and task submission.
test_runner_backends.py: Tests execution backends (Docker, Kubernetes, etc.).
test_runner_config.py: Tests configuration loading and validation.
test_runner_contracts.py: Tests Pydantic schemas.
test_runner_core.py: Tests task orchestration and execution.
test_runner_errors.py: Tests structured error handling.
test_runner_logging.py: Tests logging with redaction and signing.
test_runner_metrics.py: Tests metrics collection and alerting.
test_runner_mutation.py: Tests mutation and fuzz testing.
test_runner_parsers.py: Tests test and coverage parsers.
test_runner_utils.py: Tests utility functions (redaction, encryption).
test_runner_e2e.py: E2E integration tests for the entire module.


CI Validation: Tests run in CI, enforcing schema versioning and coverage thresholds.


Contributing

Guidelines: See CONTRIBUTING.md.
Requirements: PRs must pass unit, integration, property, mutation, and contract validation tests, plus linter/type-checks.
New Features: Must include tests, contract updates, and documentation.
Security: All code must adhere to sandboxing and data protection standards.


FAQ
Q: How do I add a new test or coverage format?A: Implement an async parser in runner_parsers.py with @register_test_parser or @register_coverage_parser. Example:
@register_test_parser('custom_format')
async def parse_custom_format(file_path): ...

Q: Can I use my CI system?A: Yes, the module supports any CI system via CLI, API, or Docker. Configure runner.yaml and run python -m runner.core.
Q: How is auditability ensured?A: Structured logs, error codes, provenance tracking, and schema-validated outputs provide full traceability.
Q: How do I enable mutation testing?A: Set mutation: true in runner.yaml and ensure tools like mutmut or Stryker are installed.
Q: Can I integrate with my observability stack?A: Yes, configure log sinks and metrics exporters in runner.yaml for Prometheus, Datadog, Splunk, etc.

References

Pydantic
OpenTelemetry
Prometheus
Mutation Testing
Hypothesis
Textual


For advanced integration help, Dockerfiles, CI workflows, or compliance documentation, contact the project maintainers.

This README is auto-generated and enforced via CI. Contact maintainers to regenerate or extend for new features.Proprietary Software: For internal use by Novatrax Labs LLC under proprietary license.


