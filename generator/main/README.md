AI README-to-App Generator Main Module
Confidential: This document is for internal use only and must not be shared without express permission from the project owner.
Overview
The Main module of the AI README-to-App Generator serves as the primary entry point and user interface for orchestrating the generation of applications from requirement documents (e.g., README files). It provides a secure, extensible, and compliant platform for highly regulated industries such as healthcare, finance, and aerospace, ensuring alignment with standards like HIPAA, GDPR, SOC 2, and FedRAMP. The module integrates with the Intent Parser (for document parsing and ambiguity detection) and Clarifier (for ambiguity resolution and requirement updates) modules to form a complete pipeline.
Components

main.py: Orchestrates the startup of CLI, GUI, or API interfaces, manages configuration reloading, logging, metrics, tracing, and graceful shutdown.
api.py: FastAPI-based REST and WebSocket API, offering secure endpoints for workflow execution, parsing, feedback, log searching, and metrics retrieval.
cli.py: Color-rich command-line interface (CLI) for running workflows, monitoring status, searching logs, managing configurations, and submitting feedback.
gui.py: Textual-based graphical user interface (TUI) with tabs for workflow execution, intent parsing, ambiguity clarification, and real-time metrics.

Key Features

Multi-Interface Access: Launch via CLI, GUI, API, or all simultaneously, supporting diverse enterprise use cases.
Secure API: JWT and API key authentication with scope-based access control, rate limiting, and CORS support (api.py).
Extensible CLI: Dynamic command registration, interactive configuration editing, real-time log viewing, and health checks (cli.py).
Interactive TUI: Tabbed interface for workflow execution, intent parsing, ambiguity clarification, and metrics display with multilingual support (gui.py).
Workflow Orchestration: Executes application generation workflows, integrating with Intent Parser and Clarifier (main.py).
Security: PII redaction, encrypted credential storage, and audit logging ensure compliance with regulatory standards.
Observability: Prometheus metrics and OpenTelemetry tracing for monitoring and debugging across all components.
Configuration Management: Live configuration reloading with audit trails and secure editing (main.py, cli.py).
Feedback Loop: Collects user feedback via CLI, GUI, or API to improve system quality (api.py, cli.py, gui.py).

Installation
Prerequisites

Python: 3.8+
Dependencies:pip install click fastapi passlib[bcrypt] prometheus-client pydantic sqlalchemy starlette textual uvicorn aiohttp

Optional dependencies:pip install slowapi opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc opentelemetry-instrumentation-fastapi


Environment Variables:
API_SECRET_KEY: JWT secret for API authentication.
ALERT_ENDPOINT: URL for alerts (e.g., https://alert-service.your-org.com:8080).
PROMETHEUS_PORT: Port for Prometheus metrics (e.g., 9090).
OPENTELEMETRY_ENDPOINT: Endpoint for OpenTelemetry tracing (e.g., http://otel-collector:4317).
DATABASE_URL: Database URL for API user storage (e.g., sqlite:///users.db).



Setup

Clone the internal repository (accessible to authorized personnel only):git clone https://internal-repo.your-org.com/readme-to-app.git
cd readme-to-app/main


Install dependencies:pip install -r requirements.txt


Configure environment variables in a .env file or secret manager:API_SECRET_KEY=test-secret
ALERT_ENDPOINT=https://alert-service.your-org.com:8080
PROMETHEUS_PORT=9090
OPENTELEMETRY_ENDPOINT=http://otel-collector.your-org.com:4317
DATABASE_URL=sqlite:///users.db


Initialize database and output directories:mkdir output
chmod 600 output



Usage
Running the Main Module
Launch the desired interface:
# CLI
python main.py --interface cli

# GUI
python main.py --interface gui

# API
python main.py --interface api

# All interfaces
python main.py --interface all

Example: Running a Workflow

Prepare a requirements document (e.g., requirements.md):- User login system
- Payment processing
Constraint: Must be secure
SSN: 123-45-6789


CLI:python main.py --interface cli run --input requirements.md --user-id test_user

Expected output:Workflow completed: output_path


API:curl -X POST http://localhost:8000/api/v1/run \
     -H "Authorization: Bearer <JWT_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"input": "- User login system\n- Payment processing\nConstraint: Must be secure"}'

Expected response:{
  "status": "success",
  "result": {
    "features": ["User login system", "Payment processing"],
    "constraints": ["Must be secure"],
    "schema_version": 2,
    "desired_doc_formats": ["Markdown", "PDF"],
    "clarifications": {
      "Login method unclear": "OAuth login",
      "Payment method unspecified": "Stripe integration"
    }
  }
}


GUI:
Launch: python main.py --interface gui
Navigate to the Runner tab, enter JSON payload {"input": "- User login system\n- Payment processing\nConstraint: Must be secure"}, and submit.
View results in the output panel.



Feedback Submission

CLI:python main.py --interface cli feedback --rating 0.8 --comments "Good clarity" --user-id test_user


API:curl -X POST http://localhost:8000/api/v1/feedback \
     -H "Authorization: Bearer <JWT_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"rating": 0.8, "comments": "Good clarity"}'


GUI: Use the feedback input field in the GUI to submit ratings and comments.

Regulatory Compliance
The Main module is designed for highly regulated industries, with the following compliance features:
GDPR (Data Protection)

PII Redaction: Sensitive data (e.g., SSNs, API keys) is redacted using redact_secrets (cli.py) or encrypt_log (api.py) in inputs, outputs, and logs.
Authentication: JWT and API key authentication with scope-based access control (api.py) ensure secure user interactions.
Audit Logging: All actions (e.g., workflow execution, feedback submission) are logged with log_action for traceability.

HIPAA (Healthcare)

PHI Protection: PII redaction and encrypted database storage (api.py) protect PHI.
Audit Logging: Logs include timestamps, user IDs, and redacted data, supporting audit trails.

SOC 2 (Security and Availability)

Security: Rate limiting (slowapi in api.py), authentication, and PII redaction ensure data protection.
Availability: Error handling (suggest_recovery_cli in cli.py, HTTP exceptions in api.py) and graceful shutdown (main.py) maintain uptime.
Observability: Prometheus metrics and OpenTelemetry tracing (main.py, api.py, gui.py) provide monitoring.

FedRAMP (Federal)

Data Integrity: Audit logging ensures traceability of all actions.
Secure Deployment: Recommendations for FedRAMP-compliant clouds (e.g., AWS GovCloud) in deployment guides.

Security Features

PII Redaction: Removes sensitive data from inputs, outputs, and logs (redact_secrets, encrypt_log).
Authentication: JWT and API key authentication with scope-based access control (api.py).
Encrypted Storage: User credentials are hashed using passlib (api.py).
Audit Logging: Logs all actions with log_action, ensuring tamper-proof traceability.
Secure Communication: API endpoints use HTTPS/TLS (api.py); CLI and GUI communicate securely via API.
Rate Limiting: Enforces limits (e.g., 10/minute) to prevent abuse (api.py).

Limitations

Data Residency: External API calls (e.g., ALERT_ENDPOINT) may not comply with region-specific requirements unless configured.
Scalability: SQLite database (api.py) and process-local operations limit scalability for large deployments.
Accessibility: GUI (gui.py) lacks explicit WCAG-compliant formatting for screen readers.
Default Credentials: Hardcoded admin user and API key in api.py are insecure for production.

Testing
The Main module includes comprehensive test suites in the tests/ directory:

Unit Tests: Validate individual components (test_main.py, test_api.py, test_cli.py, test_gui.py).
Integration Tests: Test interactions between CLI, GUI, API, and dependencies.
E2E Tests: Validate the full pipeline with test_main_e2e.py, including PII redaction and audit logging.

Run tests:
python -m unittest discover tests

Integration with Intent Parser and Clarifier
The Main module integrates with:

Intent Parser: Parses documents to extract features, constraints, and ambiguities, invoked via CLI (cli.py), GUI (gui.py), or API (/api/v1/parse in api.py).
Clarifier: Resolves ambiguities and updates requirements, integrated through the Runner (api.py, cli.py) and Clarifier tab (gui.py).

Security
To report vulnerabilities, contact the security team at security@your-org.com. Response time is within 48 hours. Do not disclose vulnerabilities without permission.
License
This project is proprietary and confidential. Unauthorized distribution or disclosure is strictly prohibited.
Contact
For support, contact the project maintainers at support@your-org.com.
Confidentiality Notice: This document and the Main module’s source code are proprietary and confidential. Unauthorized distribution or disclosure is strictly prohibited.