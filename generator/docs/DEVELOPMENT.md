# Development Guide for AI README-to-App Code Generator

This guide provides step-by-step instructions for setting up your development environment, running and testing the platform, and debugging the **AI README-to-App Code Generator**. It is intended for Python developers and DevOps engineers.

---

## 🚦 Prerequisites

- **Python:** 3.9 or higher
- **Git:** For version control
- **Docker:** (Optional) For config validation and local LLMs
- **Ollama:** (Optional) For running local LLMs (used by `local_provider.py`)
- **External Tools:**  
  - `hadolint` (Dockerfile linting)  
  - `trivy` (security scanning)  
  - `helm` (Helm validation)

---

## ⚡ Setup

### 1. Clone the Repository

```bash
git clone https://github.com/xai-org/readme-to-app.git
cd readme-to-app
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

**Key dependencies:**  
- aiohttp, prometheus_client, opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc
- aiofiles, rich, pyyaml, ruamel.yaml, hcl2, sentence-transformers, tiktoken
- fastapi, uvicorn (API server)
- Optional: anthropic, openai, google-generativeai (for cloud LLMs)

### 4. Install External Tools (Optional but Recommended)

```bash
# hadolint
sudo apt-get install -y hadolint        # Or use a package manager for your OS

# trivy
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh

# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### 5. Set Up Local LLM (Optional)

```bash
docker run -d -p 11434:11434 ollama/ollama
ollama pull llama2
```

### 6. Configure Environment Variables

```bash
export GROK_API_KEY="your-grok-api-key"
# OR use: OPENAI_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY
export SLACK_WEBHOOK="your-slack-webhook"   # Optional (for notifications)
```

### 7. Create Template Directory

```bash
mkdir deploy_templates
echo -e "Generate a production-grade {{ target }} configuration for these files: {{ files | join(', ') }}.\nInclude all required build, environment, network, and security settings.\nConfiguration must be safe, efficient, and ready for CI/CD.\nAdditional instructions: {{ instructions | default('') }}" > deploy_templates/docker_default.jinja
```

---

## 🚀 Running the Platform

### Run the Investor Demo

```bash
python demo_investor.py
```

### Run the CLI

```bash
python -m deploy_llm_call --prompt "Create a Python Flask app" --model grok-4
```

### Start the FastAPI Server

```bash
python -m deploy_llm_call --server
```

---

## 🧪 Testing

### Full Test Suite

```bash
python -m unittest discover
```

### Test Specific Modules

```bash
python -m unittest deploy_llm_call
python -m unittest deploy_prompt
```

### Property-Based Testing (with hypothesis)

```bash
python -m unittest discover -k "test_.*"
```

---

## 🐞 Debugging

- **Logs:** Use `run_id` in logs for end-to-end traceability.
- **Metrics:** Access [Prometheus metrics](http://localhost:8000/metrics) in your browser.
- **Tracing:** Use OpenTelemetry (Jaeger, Tempo, etc.) for distributed tracing.
- **Health Checks:**  
  ```bash
  curl http://localhost:8000/health
  ```
- **Circuit Breakers:** Reset with the provider’s `reset_circuit()` method if disabled.

---

## 📁 Directory Structure

- `providers/`         — LLM provider plugins (e.g., `grok_provider.py`)
- `handler_plugins/`   — Format handlers for output normalization
- `validator_plugins/` — Config validators (Docker, Helm, etc.)
- `deploy_templates/`  — Jinja2 templates for prompt generation
- `utils/`             — Utilities (`file_utils.py`, `observability_utils.py`, etc.)

---

## 💡 Development Tips

- **Hot-Reloading:**  
  - Editing plugins or templates in `providers/`, `handler_plugins/`, or `deploy_templates/` triggers hot-reloading.
- **Observability:**  
  - Monitor `/metrics` and traces for real-time debugging.
- **Security:**  
  - Always scrub prompts and configs for secrets and PII.
- **Testing:**  
  - Maintain >90% test coverage. Add unit, integration, and property-based tests for new features.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution standards and pull request guidelines.

---