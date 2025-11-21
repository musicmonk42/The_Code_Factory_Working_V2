# 📁 Dev/Migration Scripts – Do Not Ship to Production

This directory contains utility scripts for **local development, testing, and one-time codebase migrations** supporting the Self-Fixing Engineer platform, including the `runner` module. These scripts are explicitly designed for **development-only workflows** and **must not be included in production builds, deployments, or runtime environments**.

---

## 🚫 Production Exclusion Notice

**None of the scripts in this directory are runtime dependencies.** They are intended for controlled use in development or CI/CD maintenance workflows. Including these scripts or their generated outputs in production environments constitutes a **critical security and reliability incident**.

- **Do not deploy**: Scripts must be excluded from production builds, Docker images, and deployment artifacts.
- **CI/CD exclusion**: Ensure `.dockerignore`, `.gitignore`, or package exclusion lists include this directory.
- **Audit requirement**: Any presence of these scripts or their outputs in production must be reported and remediated immediately.

---

## Table of Contents

- [Purpose](#purpose)
- [File Descriptions](#file-descriptions)
- [Safe Usage Checklist](#safe-usage-checklist)
- [Security and Compliance](#security-and-compliance)
- [Integration with Runner Module](#integration-with-runner-module)
- [Testing and Validation](#testing-and-validation)
- [Contributing](#contributing)
- [FAQ](#faq)

---

## Purpose

The `scripts` directory contains utilities to facilitate development, testing, and maintenance of the Self-Fixing Engineer platform, particularly the `runner` module. These scripts support tasks like generating dummy files for local testing, migrating inline prompts to Jinja2 templates, and creating/verifying plugin manifests for CI/CD pipelines. They are designed with regulated industry standards (e.g., SOX, SOC2, PCI, FedRAMP) in mind, emphasizing traceability, reproducibility, and security.

---

## File Descriptions

### `bootstrap_agent_dev.py`

- **Purpose**: Generates dummy/stub Python modules (`audit_log.py`, `utils.py`, `testgen_prompt.py`, `llm_providers/openai.py`) for local development and testing of `testgen_agent.py`, which may produce test files for the `runner` module.
- **Key Features**:
  - Creates minimal implementations of required interfaces (e.g., audit logging, prompt building, LLM client).
  - Automatically creates the `llm_providers` directory if missing.
  - Logs all actions for traceability.
  - Includes prominent warnings against production use.
- **Usage**:
  ```bash
  python scripts/bootstrap_agent_dev.py


Output: Dummy files in the current directory, enabling local execution of testgen_agent.py.
Warning: Development-only. Generated files are placeholders and must be replaced with production implementations. Never include in production builds.

migrate_prompts.py

Purpose: Migrates inline PROMPT_TEMPLATES dictionaries in Python files to Jinja2 .j2 template files, updating source code to load templates at runtime. Used for one-time codebase migrations during development.
Key Features:
Uses AST parsing to extract prompts safely.
Supports recursive directory scanning and dry-run mode.
Creates .bak backups of modified files.
Lints extracted templates for Jinja2 syntax errors.
Generates a JSON migration report for auditability.


Usage:python scripts/migrate_prompts.py --source clarifier_llm_call.py --dest clarifier/prompts/
python scripts/migrate_prompts.py --source . --dest prompts/ --recursive


Output: .j2 template files in the destination directory and updated source files.
Warning: Development-only. Must be removed from deployment artifacts after use. Run with appropriate permissions in a controlled environment.

generate_plugin_manifest.py

Purpose: Generates a SHA256 manifest for Python plugins (.py files) in a directory, with optional Ed25519 signing for authenticity. Supports verification of existing manifests, designed for CI/CD pipelines in regulated environments.
Key Features:
Computes SHA256 hashes and file sizes for plugins.
Supports Ed25519 signing/verification for non-repudiation.
Includes metadata (timestamp, generator version) for auditing.
Enforces fail-closed behavior with --fail-on-unsigned for production.
Produces deterministic JSON output for reproducibility.


Usage:python scripts/generate_plugin_manifest.py /path/to/plugins --sign private_key.pem --out manifest.json
python scripts/generate_plugin_manifest.py --verify manifest.json --pubkey public_key.pem


Output: A JSON manifest file with hashes, metadata, and optional signature.
Warning: Unsigned manifests are not suitable for production. Signing keys should be managed via HSM or Vault in production.


Safe Usage Checklist

✅ Development Only: Use scripts only in local development, testing, or CI/CD maintenance workflows, never at runtime or in production deployments.
✅ Backup Codebase: Commit changes or create full backups before running migration scripts (e.g., migrate_prompts.py).
✅ Review Outputs: Manually verify generated files (e.g., dummy files, .j2 templates, manifests) before integrating into development workflows.
✅ Exclude from Production: Add this directory to .dockerignore, .gitignore, and package exclusion lists (e.g., setup.py, MANIFEST.in).
✅ Secure Execution: Run scripts with minimal permissions in a controlled environment (e.g., local machine, CI/CD runner). Avoid running as root.
✅ Clean Up: Remove generated dummy files and scripts after use, especially before deployment.
✅ Audit Compliance: Ensure audit logs are generated and reviewed for traceability (e.g., JSON reports from migrate_prompts.py).


Security and Compliance
These scripts are designed with regulated industry standards in mind, incorporating the following security and compliance features:

Security:
File I/O Safety: Validates file paths and permissions to prevent unauthorized access (migrate_prompts.py, bootstrap_agent_dev.py).
Cryptographic Integrity: Uses Ed25519 signing for manifests (generate_plugin_manifest.py) to ensure authenticity and non-repudiation.
Deterministic Output: Ensures reproducible results for manifests and migrations, critical for auditability.
Development Isolation: Explicit warnings and checks prevent production misuse.


Compliance:
Traceability: All scripts log actions to stdout or files, with migrate_prompts.py generating JSON reports for audit trails.
Recoverability: migrate_prompts.py creates .bak backups to ensure recoverability of modified files.
Fail-Closed: generate_plugin_manifest.py supports --fail-on-unsigned to enforce signed manifests in production.
Auditability: Structured logging and metadata (e.g., timestamps, generator version) support SOX/SOC2/PCI/FedRAMP requirements.



Recommendations for Enhanced Compliance:

Integrate with a tamper-evident logging system (e.g., runner_logging.py from the runner module) for structured, signed logs.
Use HSM or Vault for signing key management in generate_plugin_manifest.py.
Add cryptographic signing to .j2 files generated by migrate_prompts.py for integrity.


Integration with Runner Module
The scripts module supports the development and deployment of the runner module in the Self-Fixing Engineer platform:

bootstrap_agent_dev.py: Creates dummy files for testgen_agent.py, which may generate test files consumed by runner_core.py or runner_mutation.py during development.
migrate_prompts.py: Migrates prompt templates for components like testgen_agent.py, enabling structured test generation for runner.
generate_plugin_manifest.py: Generates and verifies manifests for plugins (e.g., custom backends in runner_backends.py or parsers in runner_parsers.py), ensuring integrity in CI/CD pipelines.

Example Workflow:

Run bootstrap_agent_dev.py to create dummy files for local testing of testgen_agent.py.
Use migrate_prompts.py to convert inline prompts in testgen_agent.py to .j2 templates.
Generate a manifest for the modified files using generate_plugin_manifest.py in a CI pipeline.
Use the generated test files with runner_core.py for execution and validation.


Testing and Validation
Each script has a dedicated test suite in the tests directory, ensuring 100% coverage and compliance with regulated standards:

test_bootstrap_agent_dev.py: Tests dummy file creation, error handling, and integration with a mock testgen_agent.py.
test_migrate_prompts.py: Tests prompt extraction, template linting, code replacement, and directory migration.
test_generate_plugin_manifest.py: Tests manifest generation, signing, verification, and error handling.
test_scripts_e2e.py: End-to-end integration tests for the entire scripts module, covering workflows like bootstrapping, prompt migration, and manifest generation/verification.

Running Tests:
pip install pytest pytest-asyncio jinja2
pytest tests/ -v --log-level=DEBUG

All tests include audit logging and mock external dependencies to ensure reproducibility. Tests validate security features (e.g., signing, backups) and error handling for compliance.

Contributing

Guidelines: See CONTRIBUTING.md in the root directory.
Requirements: PRs must include tests, pass linter/type-checks, and adhere to security standards.
New Scripts: Must include comprehensive test suites, audit logging, and explicit development-only warnings.
Security: All file operations must validate permissions, and outputs must be deterministic.


FAQ
Q: Can these scripts be used in production?A: No. These scripts are for development and testing only. They must be excluded from production builds and deployments.
Q: How do I ensure generated files are safe?A: Review outputs manually, use dry-run mode (migrate_prompts.py), and verify manifests (generate_plugin_manifest.py). Remove dummy files after testing.
Q: How do I integrate these scripts in CI/CD?A: Use in development or maintenance stages (e.g., GitHub Actions):
name: Migrate Prompts
on: [push]
jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: python scripts/migrate_prompts.py --source . --dest prompts/ --dry-run

Q: How is auditability enforced?A: Scripts generate logs and JSON reports (e.g., migrate_prompts.py). Audit logs include trace IDs and timestamps for traceability.


This README is auto-generated and enforced via CI. Contact maintainers to regenerate or extend for new features.Proprietary Software: For internal use by Novatrax Labs LLC under proprietary license.


