# DocGen Agent

## Overview

**DocGen Agent** is an enterprise-grade, secure, and extensible orchestrator for automated documentation generation using advanced LLMs, Sphinx, and custom plugins. It is designed for modern engineering teams and regulated industries that require robust security, compliance, and observability in their documentation pipeline. DocGen Agent supports multi-language projects, integrates with CI/CD, and enforces strict privacy and legal standards.

---

## Features

- **Multi-Language Support**: Python, JavaScript, Rust, Go, Java, and more via plugin system.
- **Strict Security & Privacy**: All content is scrubbed using [Presidio](https://microsoft.github.io/presidio/) (no fallback; PII/secret leaks are strictly prevented).
- **LLM-Driven Generation**: Modular, provider-agnostic LLM orchestration (OpenAI, Claude, Gemini, Grok, Local models, etc.) with streaming and fallback.
- **Compliance Plugins**: Automated checks for license, copyright, and custom legal/commercial requirements.
- **Human-in-the-Loop Approval**: Optional Slack/webhook/CLI approval with traceable provenance.
- **Observability**: Prometheus metrics, OpenTelemetry tracing, and structured logging.
- **Self-Healing**: Automated retries and LLM-guided healing for failed or low-quality docs.
- **Batch & Streaming**: API and CLI support for batch and streaming doc generation.
- **Hot-Reloadable Plugins**: For compliance, enrichment, and pre/post-processing hooks.
- **Extensive Validation**: Deep format, structure, and quality validation with explainable reports.
- **Integration Ready**: API endpoints, CLI, and plugin interfaces for CI/CD and DevOps pipelines.

---

## Pipeline Overview

1. **Context Gathering**: Secure, async retrieval of code, configs, tests, and repo history.
2. **Prompt Generation**: Uses advanced templates and few-shot retrieval for context-rich LLM prompts.
3. **LLM Orchestration**: Strict, provider-agnostic LLM calls with streaming, fallback, and ensemble voting.
4. **Response Handling**: Normalization, enrichment, provenance stamping, and structured output.
5. **Validation**: Deep format, compliance, security, and quality checks (auto-correct via LLM if enabled).
6. **Compliance Enforcement**: Pluggable checks for licensing, copyright, and custom requirements.
7. **Approval Workflow**: (Optional) Human review/approval via Slack, webhook, or CLI.
8. **Metrics & Tracing**: Every stage emits Prometheus and OpenTelemetry metrics for audit and monitoring.
9. **Audit Logging**: All actions are logged with provenance and run/tracing IDs for compliance.

---

## Quickstart

### Prerequisites

- Python >= 3.10
- Docker (recommended for isolated execution)
- [Presidio](https://microsoft.github.io/presidio/) (for strict PII/secret scrubbing)
- [Prometheus](https://prometheus.io/) and [OpenTelemetry Collector](https://opentelemetry.io/) (for observability)
- LLM API keys (OpenAI, Claude, Gemini, Grok, etc.) as environment variables
- (Optional) Sphinx, Pandoc, docutils, PlantUML for advanced enrichment and validation

### Install

```bash
pip install -r requirements.txt
# Ensure Presidio and all required CLI tools are available
```

### Usage (CLI)

```bash
python docgen_agent.py --repo-path ./myrepo --doc-type README --target-files main.py utils.py --instructions "Generate a concise, production-ready README."
```

### Usage (API)

The FastAPI app provides endpoints for doc generation, approval, and health checks:

- **/generate**: POST to trigger doc generation
- **/approve**: POST to request human approval
- **/metrics**: GET Prometheus metrics

Example (with HTTPie):

```bash
http POST http://localhost:8000/generate \
  repo_path='./myrepo' \
  doc_type='README' \
  target_files:='["main.py", "utils.py"]' \
  instructions='Generate a concise, production-ready README.'
```

---

## Configuration

DocGen Agent is highly configurable via CLI, API, or a config object. Example parameters:

```json
{
  "repo_path": "./myrepo",
  "doc_type": "README",
  "target_files": ["main.py", "utils.py"],
  "instructions": "Generate a concise, production-ready README.",
  "languages_supported": ["python", "javascript"],
  "human_approval": true,
  "slack_webhook": "https://hooks.slack.com/services/...",
  "approval_ui_url": "http://approval-ui.local/approve"
}
```

**Environment Variables:**
- `OPENAI_API_KEY`, `CLAUDE_API_KEY`, `GEMINI_API_KEY`, `GROK_API_KEY`
- `SLACK_WEBHOOK_URL`
- `APPROVAL_UI_URL`
- `PRESIDIO_*` (for scrubbing configuration)
- And others as required by plugins/tools

---

## Extending DocGen Agent

### Adding a Compliance Plugin

1. Subclass `CompliancePlugin` and implement the `check()` method.
2. Register your plugin using `register_compliance_plugin()` in the agent.

### Adding a Pre/Post-Processing Hook

- Use `add_pre_process_hook(hook_fn)` or `add_post_process_hook(hook_fn)` for custom logic on prompts or results.

### Adding Support for a New LLM Provider

- Extend the LLM Orchestrator with a new provider plugin, and register it with the provider registry.

---

## Security & Compliance

- **PII/Secret Scrubbing**: Strictly enforced via Presidio. Pipeline fails if scrubbing is not possible.
- **Audit Logging**: Every doc generation is tagged with a unique run ID, timestamped, and all provenance is logged.
- **Compliance by Design**: No docs leave the system without passing legal/commercial checks (customizable).

---

## Observability

- **Prometheus**: Metrics for calls, latency, errors, compliance, validation, and token usage.
- **OpenTelemetry**: Traces with span IDs and context for all stages.
- **Logs**: Structured, context-rich, and scrubbed for PII/secrets.

---

## Troubleshooting

- **Presidio Required**: If Presidio is missing, the agent will fail to start.
- **Missing LLM API Key**: Ensure all required LLM API keys are set as environment variables.
- **Plugin Errors**: Check logs for plugin load/registration issues.
- **High-Cardinality Metrics**: Avoid using unique run IDs as Prometheus labels in production metric scrapes.

---

## Contributing

1. Fork this repository and clone it.
2. Add new compliance plugins, hooks, or provider support.
3. Write unit and integration tests for your contributions.
4. Submit a pull request with a detailed description.

---

## License

[MIT License](LICENSE)

---

## Acknowledgements

DocGen Agent leverages [Presidio](https://microsoft.github.io/presidio/), [OpenAI](https://openai.com/), [Anthropic Claude](https://www.anthropic.com/), [Gemini](https://ai.google.dev/), [Grok](https://x.ai/), [Prometheus](https://prometheus.io/), [OpenTelemetry](https://opentelemetry.io/), and [Sphinx](https://www.sphinx-doc.org/), among others.

---

## Contact

For support, security disclosures, or feature requests, please [open an issue](https://github.com/yourorg/yourrepo/issues) or contact the maintainers.