# System Overview

This handbook provides a high-level overview of the **AI README-to-App Code Generator**, a platform that automates the generation of deployment configurations (e.g., Dockerfiles, Helm charts, Terraform scripts) from natural language inputs like READMEs or app descriptions.  
It is designed for engineers maintaining or extending the platform, offering context for its purpose, architecture, and components as of **July 28, 2025**.

---

## 🎯 Purpose

The platform **streamlines DevOps** by transforming app descriptions into production-ready deployment configurations using advanced LLMs (OpenAI, xAI Grok, Google Gemini, Anthropic Claude, local models like Ollama).  
It emphasizes **reliability, security, and observability**, with features such as self-improving prompts, ensemble voting, and plugin-based extensibility.  
The **investor demo** (generating a Dockerfile from a text input) showcases its core functionality.

---

## 🚀 Key Features

- **Multi-Provider LLM Orchestration:**  
  Supports multiple LLM providers with dynamic selection based on latency, cost, and quality (`deploy_llm_call.py`).

- **Self-Improving Prompts:**  
  Optimizes prompts using a meta-LLM feedback loop (`deploy_prompt.py`).

- **Ensemble Mode:**  
  Combines responses from multiple providers for higher accuracy (`deploy_llm_call.py`).

- **Observability:**  
  Prometheus metrics, OpenTelemetry tracing, and structured logging (`observability_utils.py`, `runner_logging.py`).

- **Security:**  
  Prompt scrubbing, security scanning with trivy, and compliance tagging (`security_utils.py`, `file_utils.py`).

- **Extensibility:**  
  Hot-reloadable plugins for providers, handlers, and validators.

- **Validation:**  
  Async validation with hadolint and trivy (`deploy_validator.py`).

---

## 🏗️ Architecture

The platform is modular and plugin-based, with the following core components:

- **Prompt Generation (`deploy_prompt.py`):**  
  Creates context-rich prompts using Jinja2 templates and few-shot examples.

- **LLM Orchestration (`deploy_llm_call.py`):**  
  Manages LLM calls with a provider registry, circuit breakers, and retries.

- **Response Handling (`deploy_response_handler.py`):**  
  Normalizes and enriches outputs with badges and changelogs.

- **Validation (`deploy_validator.py`):**  
  Validates configs with external tools (hadolint, trivy) and auto-fixes issues.

- **Utilities (`utils/`):**  
  - File handling (`file_utils.py`)
  - Observability (`observability_utils.py`)
  - Security (`security_utils.py`)

- **Providers:**  
  Implementations for OpenAI (`ai_provider.py`), Grok (`grok_provider.py`), Gemini (`gemini_provider.py`), Claude (`claude_provider.py`), and local LLMs (`local_provider.py`).

> For detailed architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 🔄 Data Flow

1. **Input:**  
   Text description or README processed by `DeployPromptAgent`.
2. **Prompt:**  
   Generated with context (e.g., dependencies, commits) using Jinja2 templates.
3. **LLM Call:**  
   Handled by `DeployLLMOrchestrator` with provider selection.
4. **Response:**  
   Normalized and enriched by `handle_deploy_response`.
5. **Validation:**  
   Checked by `validate_deploy_configs` for correctness and security.
6. **Output:**  
   Saved to disk with provenance via `save_files_to_output`.

---

## 🧑‍💻 Investor Demo

The investor demo generates a Dockerfile from a text input (e.g., “Create a Python Flask app, expose port 8080”), validates it, and saves it to `demo_output/Dockerfile`.  
It uses a single provider (e.g., Grok or local) and displays results with [rich](https://github.com/Textualize/rich).  
See `demo_investor.py` for the reference implementation.

---

## ⏭️ Next Steps

- **Setup:** Follow `DEVELOPER_SETUP.md` to configure the environment.
- **Maintenance:** See `MAINTENANCE_GUIDE.md` for debugging and updates.
- **Extending:** Check `EXTENSIBILITY_GUIDE.md` for adding features.
- **Testing:** Refer to `TESTING_GUIDE.md` for the test suite.
- **Context:** Review `README.md` and `ARCHITECTURE.md`.

---