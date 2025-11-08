Security Policy for README to Code Generator - Agents Module

The agents module of the README to Code Generator project within The Code Factory is designed with security as a top priority, adhering to regulated industry standards such as SOC2, PCI DSS, and HIPAA. This document outlines the procedures for reporting security vulnerabilities and the measures in place to ensure secure operation.

Supported Versions

The following versions of the agents module are currently supported with security updates:







Version

Supported







1.x.x

:white\_check\_mark:





< 1.0

:x:





Reporting a Vulnerability

If you discover a security vulnerability, please report it promptly and responsibly to ensure timely resolution. Follow these steps:



Do Not Disclose Publicly: Do not disclose the vulnerability in public forums, such as GitHub issues, social media, or other public channels, to prevent exploitation.

Contact Us Privately: Send a detailed report to security@codefactory.com. Include:

A description of the vulnerability.

Steps to reproduce the issue.

Potential impact (e.g., data exposure, unauthorized access).

Any suggested mitigations or fixes.





Encryption: If possible, encrypt your report using our public PGP key (available upon request).

Response Time: You will receive an acknowledgment within 48 hours, and we aim to resolve critical vulnerabilities within 7 days.



Security Features

The agents module includes the following security measures:



PII/Secret Scrubbing: Uses Presidio to redact sensitive data (e.g., emails, API keys) in inputs and outputs.

Audit Logging: Logs all critical actions (e.g., workflow execution, LLM calls) using audit\_log for traceability.

Provenance Tracking: Records metadata (e.g., correlation\_id, timestamp, model\_used) for auditability.

Security Scanning: Integrates tools like bandit, semgrep, trivy, hadolint, and checkov to detect vulnerabilities in code and deployment configurations.

Sandbox Execution: Runs validations in isolated environments to prevent unauthorized access.

Human-in-the-Loop (HITL): Enforces HITL reviews for critical operations (e.g., code fixes, deployment configurations).



Compliance

The module complies with:



SOC2: Ensures data integrity and auditability through logging and provenance.

PCI DSS: Protects sensitive data with PII scrubbing and secure LLM interactions.

HIPAA: Safeguards protected health information (PHI) with anonymization and access controls.



Contact

For security-related inquiries, contact security@codefactory.com. For general support, reach out to support@codefactory.com.

