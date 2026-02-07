<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Clarifier Module
Overview
The Clarifier module is a production-ready system for clarifying ambiguous software requirements using a combination of Large Language Model (LLM) interactions, user prompting, and automated requirement updates. It is designed for highly regulated industries such as healthcare, finance, and aerospace, ensuring compliance with standards like HIPAA, GDPR, SOC 2, and FedRAMP. The module supports multiple user interaction channels (CLI, GUI, Web, Slack, Email, SMS, Voice), multilingual prompts, and robust security features including PII redaction, encryption, and tamper-proof audit logging.
Key Features

Ambiguity Detection and Prioritization: Uses LLMs (e.g., Grok, OpenAI, Anthropic) to identify and prioritize ambiguities in requirements, with rule-based fallbacks for reliability (clarifier_llm_call.py).
User Interaction: Supports multiple channels for prompting users for clarifications, documentation formats, and compliance questions, with localization support (clarifier_prompt.py, clarifier_user_prompt.py).
Requirement Updates: Updates requirements with user answers, handling schema evolution, conflict resolution, and versioning (clarifier_updater.py).
Security and Compliance: Implements PII redaction, encryption (Fernet), and tamper-proof versioning, ensuring compliance with GDPR, HIPAA, and SOC 2.
Observability: Tracks metrics (Prometheus) and tracing (OpenTelemetry) for latency, errors, and compliance events.
Auditability: Logs all actions (prompts, updates, errors) with timestamps and user context for regulatory audits.
Modular Architecture: Integrates with the omnicore_engine plugin system for extensibility.

System Architecture
The Clarifier module consists of the following components:

clarifier.py: Core orchestrator, managing the clarification pipeline, shared utilities (configuration, logging, encryption, circuit breaker), and history storage.
clarifier_llm_call.py: Handles LLM interactions for ambiguity prioritization and language inference, with retries and fallbacks.
clarifier_prompt.py: Manages user-facing prompts for documentation formats and compliance questions, delegating to the core Clarifier.
clarifier_user_prompt.py: Provides multi-channel user interaction (CLI, GUI, Web, Slack, Email, SMS, Voice) with PII redaction and encryption.
clarifier_updater.py: Updates requirements with user answers, handling schema migrations, conflict resolution, and tamper-proof history storage.
prompts/: Contains Jinja2 templates (feedback_prompt.j2, doc_format_question.j2, clarification_prompt.j2) for structured user prompts.

Installation
Prerequisites

Python: 3.8+
Dependencies:pip install dynaconf cryptography boto3 aiohttp aiofiles zstandard jsonschema prometheus-client googletrans==4.0.0-rc1

Optional dependencies:pip install textual fastapi starlette speechrecognition opentelemetry-sdk opentelemetry-exporter-console jinja2 anthropic openai


Environment Variables:
KMS_KEY: Fernet key for encryption (recommended: use AWS KMS or similar).
ALERT_ENDPOINT: URL for alerting (e.g., http://alert-service:8080).
CLARIFIER_EMAIL_SERVER, CLARIFIER_EMAIL_PORT, CLARIFIER_EMAIL_USER, CLARIFIER_EMAIL_PASS: Email server configuration.
CLARIFIER_SLACK_WEBHOOK: Slack webhook URL.
CLARIFIER_SMS_API, CLARIFIER_SMS_KEY: SMS API configuration.
GROK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY: LLM provider credentials.



Setup

Clone the repository:git clone https://github.com/your-org/clarifier.git
cd clarifier


Install dependencies:pip install -r requirements.txt


Configure environment variables in a .env file or secret manager:KMS_KEY=your-fernet-key
ALERT_ENDPOINT=http://alert-service:8080
CLARIFIER_EMAIL_SERVER=smtp.your-domain.com
CLARIFIER_EMAIL_PORT=587
CLARIFIER_EMAIL_USER=user@your-domain.com
CLARIFIER_EMAIL_PASS=your-password
CLARIFIER_SLACK_WEBHOOK=https://hooks.slack.com/services/xxx
CLARIFIER_SMS_API=https://sms-api.com
CLARIFIER_SMS_KEY=your-sms-key
GROK_API_KEY=your-grok-key
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key


Initialize the user profile directory:mkdir user_profiles
chmod 600 user_profiles



Usage
Running the Clarifier Service
Start the Clarifier service to process requirements:
python clarifier.py

To run tests:
python clarifier.py --test

Example: Clarifying Requirements

Prepare a requirements document and ambiguities:requirements = {
    "features": ["User login system", "Payment processing"],
    "schema_version": 1
}
ambiguities = ["User login system unclear", "Payment processing method unspecified"]
user_context = {"user_id": "test_user", "user_email": "user@example.com"}


Run the Clarifier plugin:from clarifier import run
import asyncio

async def main():
    result = await run(requirements, ambiguities)
    print(result)

asyncio.run(main())

Expected output:{
    "requirements": {
        "features": ["User login system", "Payment processing"],
        "schema_version": 2,
        "desired_doc_formats": ["Markdown", "PDF"],
        "clarifications": {
            "User login system unclear": "OAuth-based login",
            "Payment processing method unspecified": "Stripe integration"
        },
        "inferred_features": [],
        "inferred_constraints": [],
        "version_hash": "abc123",
        "prev_hash": null
    }
}



User Interaction Channels
The Clarifier supports multiple channels for user prompts:

CLI: Command-line interface for simple input.
GUI: Textual-based interface (requires textual).
Web: FastAPI-based web form (requires fastapi, starlette).
Slack: Slack webhook integration (requires aiohttp).
Email: SMTP-based email prompts (requires smtplib).
SMS: SMS API integration (requires aiohttp).
Voice: Speech recognition (requires speechrecognition).

Configure the desired channel via INTERACTION_MODE in the .env file (e.g., INTERACTION_MODE=cli).
Compliance Questions
The system prompts users for compliance-related questions (e.g., GDPR, PHI, PCI DSS) and stores answers securely in user_profiles/<user_id>.json. Example:
{
    "user_id": "test_user",
    "preferred_channel": "cli",
    "language": "en",
    "compliance_preferences": {
        "gdpr_apply": true,
        "phi_data": false,
        "pci_dss": true,
        "data_residency": "EU",
        "child_privacy": false
    }
}

Regulatory Compliance
The Clarifier module is designed for highly regulated industries, with the following compliance features:
GDPR (Data Protection)

PII Redaction: Sensitive data (e.g., emails, SSNs, API keys) is redacted using redact_sensitive before logging or storage.
Encryption: User answers and history entries are encrypted using Fernet (via get_fernet).
Data Residency: Configurable to ensure data processing occurs in compliant regions (e.g., EU). External API calls (e.g., Slack, googletrans) must be validated for residency compliance.

HIPAA (Healthcare)

PHI Protection: Protected Health Information (PHI) is redacted and encrypted to prevent leakage.
Audit Logging: All interactions (prompts, updates, errors) are logged with timestamps and user context using log_action.

SOC 2 (Security and Availability)

Circuit Breaker: Prevents cascading failures with process-local circuit breaker (get_circuit_breaker).
Observability: Prometheus metrics (CLARIFIER_CYCLES, LLM_LATENCY, etc.) and OpenTelemetry tracing ensure monitoring and debugging.
Secure Configuration: Secrets are managed via environment variables or a secret manager.

PCI DSS (Payment Data)

Secure Processing: Payment-related ambiguities are clarified with redaction to prevent exposure of credit card data.
Compliance Questions: Prompts for PCI DSS compliance requirements to ensure proper handling.

Security Features

PII Redaction: Uses regex-based redact_sensitive to remove sensitive data (e.g., emails, SSNs, API keys) from prompts, answers, and logs.
Encryption: History (clarifier_updater.py) and user profiles (clarifier_user_prompt.py) are encrypted using Fernet.
Tamper-Proof Versioning: Requirements updates include hash chains (version_hash, prev_hash) for integrity.
Secure Channels: External channels (e.g., Slack, Email) use HTTPS/TLS, with redacted data to prevent leakage.
Logging: Sensitive data is filtered from logs using SensitiveDataFilter (clarifier.py).

Limitations

Data Residency: External APIs (e.g., googletrans, Slack) may not comply with data residency requirements unless configured for specific regions.
Scalability: SQLite-based history storage (clarifier_updater.py) and process-local circuit breaker (clarifier.py) may not scale in distributed environments.
Translation Accuracy: The googletrans library (clarifier_prompt.py) may have rate limits or accuracy issues for multilingual prompts.
Accessibility: GUI/Web channels require additional WCAG-compliant formatting for full accessibility compliance.

Testing
The Clarifier module includes comprehensive test suites in the tests/ directory:

Unit Tests: Validate individual components (clarifier.py, clarifier_llm_call.py, clarifier_prompt.py, clarifier_updater.py, clarifier_user_prompt.py).
Integration Tests: Test interactions between components (e.g., LLM calls, user prompts, requirement updates).
E2E Tests: Validate the full pipeline with PII redaction, compliance questions, and history storage.

Run tests:
python -m unittest discover tests

Contributing
Contributions are welcome! Please see CONTRIBUTING.md for guidelines on reporting bugs, suggesting features, and submitting code changes.
Security
To report vulnerabilities, please follow the process outlined in SECURITY.md. We support responsible disclosure and respond within 48 hours.
License
This project is licensed under the MIT License. See LICENSE.md for details.
Contact
For questions or support, contact the project maintainers at support@your-org.com or join our Slack community at https://slack.your-org.com.