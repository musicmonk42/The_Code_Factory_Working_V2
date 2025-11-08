# Self-Fixing Engineer: Compliance & Audit Guardrails

A robust, enterprise-grade compliance and audit logging framework for secure, policy-driven AI and agent orchestration in self-healing systems.

**Version:** 1.0.0 (August 19, 2025)  
**Authors:** xAI Engineering Team  
**Repository:** [GitHub Link] (Replace with actual repo URL)

---

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Compliance Report CLI](#compliance-report-cli)
  - [Programmatic Usage](#programmatic-usage)
- [API Reference](#api-reference)
- [Optional Integrations & Advanced Features](#optional-integrations--advanced-features)
- [Security Considerations](#security-considerations)
- [Resilience & Error Handling](#resilience--error-handling)
- [Monitoring & Observability](#monitoring--observability)
- [Testing](#testing)
- [Development & Contributing](#development--contributing)
  - [Code Structure](#code-structure)
  - [Extending the Framework](#extending-the-framework)
  - [Troubleshooting & Common Issues](#troubleshooting--common-issues)
- [Changelog](#changelog)
- [License](#license)

---

## Features

### Compliance Mapping & Enforcement

- Loads compliance controls (e.g., NIST, ISO) from a central YAML config (`crew_config.yaml`).
- Analyzes enforcement status and identifies gaps in required controls.
- Raises custom `ComplianceEnforcementError` for violations, blocking non-compliant actions in production.
- Schema validation for YAML configs.

### Audit Logging

- Tamper-evident, hash-chained logs with optional Ed25519 cryptographic signing.
- Correlation IDs, compliance tags (e.g., NIST_AC-6), agent-specific hashing.
- Async and sync APIs; key management with rotation and revocation.

### Metrics & Observability

- Prometheus metrics for compliance blocks, gaps, config failures, unenforced controls.
- Centralized audit logging integration (Splunk/Datadog/Kafka placeholders).
- Health checks for integrations and config status.

### Distributed & Immutable Logging

- Optional Kafka topic publishing for distributed event streaming.
- DLT (Distributed Ledger Technology) backend for immutable audit trails: supports in-memory, EVM, Fabric, Corda.
- Off-chain storage options (e.g., S3, in-memory).

### Resilience & Production Readiness

- Retries with exponential backoff for file, DLT, and Kafka operations.
- Disk space monitoring and fail-fast for missing dependencies.
- Thread-safe operations, graceful degradation of optional features.

### CLI and Python API

- CLI for compliance reports, health checks, and audit verification.
- Python APIs for integration in AI crews/agents.

### Testing & Development

- Comprehensive unit/integration tests with pytest.
- Dummy config generation for easy testing in development mode.

---

## Architecture Overview

The framework consists of two core modules:

- **compliance_mapper.py:** Loads/validates compliance controls from YAML, generates reports, detects gaps, raises enforcement errors, integrates with Prometheus and audit logging.
- **audit_log.py:** Provides tamper-evident logging with hashing, signing, and optional DLT/Kafka backends. Supports async/sync APIs and chain verification.

**Integration Flow:**
```
YAML Config → compliance_mapper.py → Report/Gaps → Prometheus Metrics
                                 → Enforcement Errors → ComplianceEnforcementError
Audit Events → audit_log.py → Local Hash-Chained Log
                             → Optional: Sign (Ed25519) → Kafka/DLT
                             → Verify Chain (CLI/API)
```
_Modules are designed for modularity—extend `_log_to_central_audit` for custom SIEM or use `ComplianceEnforcementError` in agent workflows._

---

## Installation

1. **Clone the repository:**
    ```bash
    git clone https://github.com/your-repo/self-fixing-engineer-guardrails.git
    cd self-fixing-engineer-guardrails
    ```

2. **Install core dependencies:**
    ```bash
    pip install pyyaml cerberus tenacity
    ```

3. **(Optional, for full features):**
    ```bash
    pip install prometheus_client aiofiles portalocker cryptography kafka-python web3 opentelemetry-api requests
    ```

4. **Verify Installation:**
    ```bash
    python -m guardrails.compliance_mapper --health-check
    # Output: {"prometheus_available": true, "config_path_exists": false}
    ```

_Developers: use a virtual environment and pin versions for production._

---

## Configuration

### crew_config.yaml

- **Location:** `agent_orchestration/crew_config.yaml` (override with `CREW_CONFIG_PATH` env var)
- **Structure:** Compliance controls as dict of control IDs to details.

**Example:**
```yaml
compliance_controls:
  AC-1:
    name: Access Control Policy and Procedures
    description: Establishes policies and procedures for managing system and information access.
    status: enforced  # enforced, partially_enforced, logged, not_implemented, not_specified
    required: true
  # Add more controls...
```

- **Validation:** Enforced via Cerberus schema.
- **Extend schema** in `load_compliance_map` for custom fields.

### Environment Variables

- `APP_ENV`: "production" or "development" (default: development)
- `CREW_CONFIG_PATH`: Override YAML path

**Audit Logging:**
- `AUDIT_LOG_PATH`, `PRIVATE_KEY_B64`, `PRIVATE_KEY_PASSWORD`, `PUBLIC_KEY_B64`
- `ALERT_WEBHOOK`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_AUDIT_TOPIC`
- `DLT_TYPE`, DLT backend vars

_Use `.env` in development with `python-dotenv`._

### Failure Modes & Mitigations

- **Missing YAML**: Fails fast in production, logs warning in development.
- **Invalid YAML**: Logs errors, returns empty map.
- **Dependency Missing**: Graceful degradation; fail-fast in production.
- **Low Disk Space**: Alerts; prevents writes.
- **Audit Chain Tampering**: Verification fails; critical log.

_Monitor Prometheus metrics, set up webhook alerts, run CLI verification in CI/CD._

---

## Usage

### Compliance Report CLI

Generate and check compliance reports:
```bash
python -m guardrails.compliance_mapper
# Options:
#   --health-check: Output JSON health status (exit 0 if healthy, 1 otherwise)
```

- **Development:** Auto-creates dummy YAML if missing.
- **Production:** Fails if YAML missing/invalid.
- **Exits non-zero** if required controls have gaps.

**Example:**
```
--- Generating Compliance Coverage Report ---
⚠️ WARNING: Compliance enforcement gaps detected!
🚨 Required controls NOT fully enforced:
 - AC-2 (Current Status: not_implemented)
```

### Programmatic Usage

Integrate into your application:
```python
from guardrails.compliance_mapper import generate_report, ComplianceEnforcementError

try:
    gaps, all_enforced = generate_report("path/to/crew_config.yaml")
    if not all_enforced:
        raise ComplianceEnforcementError("agent_action", "AC-2", "Required control not enforced")
except ComplianceEnforcementError as e:
    # Handle block (e.g., abort agent task)
    print(e)
```
_Use `asyncio.run(_log_to_central_audit(...))` for custom audit events._

---

## API Reference

### compliance_mapper.py

- `load_compliance_map(config_path: str) -> Dict[str, Dict[str, Any]]`  
  Loads and validates YAML; returns compliance controls dict. Raises ComplianceEnforcementError in production if missing.

- `check_coverage(compliance_map: Dict) -> Dict[str, List[str]]`  
  Analyzes gaps; updates Prometheus gauges.

- `generate_report(config_path: str) -> Tuple[Dict[str, List[str]], bool]`  
  Loads map, checks coverage, prints report, logs gaps; returns gaps and enforcement status.

- `health_check() -> Dict[str, Any]`  
  Returns integration status (e.g., Prometheus, config existence).

- `ComplianceEnforcementError(action_name, control_tag, message)`  
  Custom exception for blocks; inc metrics and audits.

_See docstrings in source code for exhaustive details._

### audit_log.py

- `AuditLogger(log_path: str, ...)`  
  Core logger class.

- `add_entry(kind, name, detail, agent_id, ...)`  
  Async log entry.

- `log_event(...)`  
  Sync wrapper.

- `get_last_audit_hash(agent_id)`  
  Retrieve last hash.

- `close()`  
  Clean up resources.

- `health_check()`  
  Integration status.

- `verify_audit_chain(log_path)`  
  Verify log integrity.

- `audit_log_event_async(...)`  
  Async helper for events.

---

## Optional Integrations & Advanced Features

### Prometheus Metrics

- Install `prometheus_client`.
- Expose metrics server:
    ```python
    from prometheus_client import start_http_server
    start_http_server(8000)  # /metrics endpoint
    ```
- Metrics: `self_healing_compliance_block_total`, `self_healing_compliance_gap_alerts_total`, etc.

### Kafka Audit Log

- Install `kafka-python`.
- Set env vars; events auto-published on logs.

### DLT/Audit Ledger

- Implement `plugins.dlt_backend.py` with clients (e.g., `EVMDLTClient`).
- Configure `DLT_TYPE` and backend vars.

### Digital Signing

- Install `cryptography`.
- Set key env vars. Use `key_rotation(logger)` for key management.

_Dev: Extend `append_distributed_log` for custom streaming._

---

## Security Considerations

- **Secrets:** All via env vars; validated for dummies in production.
- **Sanitization:** Logs sanitized for PII.
- **Enforcement:** Fail-fast on missing config/dependencies in production.
- **Verification:** Audit chains signed and verifiable; revoked keys skipped.
- **Mitigation:** Use secrets managers; monitor for low disk space.

_Scan with bandit before deploy._

---

## Resilience & Error Handling

- **Retries:** File/DLT/Kafka ops with exponential backoff.
- **Disk Space:** Monitored; alerts on low space.
- **Degradation:** Optional features disable gracefully.
- **Error Codes:** CLI exits with specific codes:  
  `1`: gaps, `2`: permission/enforcement, `3`: unexpected.

_Dev: Extend retries in `write_dummy_config` for custom ops._

---

## Monitoring & Observability

- **Metrics:** Prometheus for gaps, blocks, failures.
- **Audits:** Events sent to `audit_log.py` or placeholder.
- **Health Checks:** CLI `--health-check` or `health_check()` API.
- **Tracing:** OpenTelemetry in audit verification.

_Integrate with Grafana for dashboards._

---

## Testing

- **Unit/Integration Tests:** Comprehensive pytest suites.
    - `tests/test_compliance_mapper.py`
    - `tests/test_audit_log.py`
    - `tests/test_guardrails_integration.py`

- **Run Tests:**
    ```bash
    pytest -v tests/
    ```

- **Coverage:** 90%+ target; use `pytest-cov`.
- **Dev:** Mock dependencies in fixtures; add tests for custom extensions.

---

## Development & Contributing

### Code Structure

- `__init__.py`: Package marker
- `compliance_mapper.py`:
    - Loads/validates YAML
    - Analyzes gaps/reports
    - Enforces via exceptions
    - Integrates metrics/audits
- `audit_log.py`:
    - Core logger with hashing/signing
    - Integrations (Kafka, DLT)
    - Verification and health checks

_Modules are decoupled: use `compliance_mapper` for policy checks, `audit_log` for logging._

### Extending the Framework

- **Custom Controls:** Add to YAML; extend schema as needed.
- **Audit Backends:** Override `_log_to_central_audit` or implement DLT plugins.
- **Metrics:** Add custom Prometheus counters/gauges.

### Contributing

- Fork repo, create branch (`feature/my-ext`)
- Add tests, update README
- PR with changelog entry
- Follow PEP8; lint with flake8

---

## Troubleshooting & Common Issues

- **Missing YAML:** Check `CREW_CONFIG_PATH`; ensure permissions.
- **Dependency Errors:** Install optionals; check logs for warnings.
- **Invalid Chain:** Verify with CLI; check for tampering.
- **Low Disk:** Monitor alerts; increase storage.
- **Debug:** Set `APP_ENV=development` for tracebacks.

_Logs in `compliance_system.log` / `audit_system.log`._

---

## Changelog

- **v1.0.0 (August 19, 2025):** Initial release with compliance mapping, audit logging, metrics, and integrations.

_Maintain changelog in PRs._

---

