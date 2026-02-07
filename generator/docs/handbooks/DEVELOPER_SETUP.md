<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Developer Setup

This handbook provides detailed instructions for setting up the development environment for the **AI README-to-App Code Generator** as of July 28, 2025. It covers dependencies, external tools, API keys, local LLM setup, and configuration for maintaining or extending the platform—including the investor demo.

---

## 📝 Prerequisites

- **Python**: 3.9 or higher
- **Git**: For version control
- **Docker**: (Optional) For validation and local LLM setup
- **Ollama**: (Optional) For local LLM provider (`local_provider.py`)
- **External Tools**:  
  - `hadolint` (Dockerfile linting)  
  - `trivy` (security scanning)  
  - `helm` (Helm validation, optional for full functionality)
- **API Keys**: `GROK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `CLAUDE_API_KEY` (optional if using local LLM)

---

## ⚡ Setup Steps

### 1. Clone the Repository

```bash
git clone https://github.com/xai-org/readme-to-app.git
cd readme-to-app
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

**Key dependencies:**  
- `aiohttp`, `prometheus_client`, `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`
- `aiofiles`, `rich`, `pyyaml`, `ruamel.yaml`, `hcl2`, `sentence-transformers`, `tiktoken`
- `fastapi`, `uvicorn`
- *Optional for cloud providers*: `openai`, `anthropic`, `google-generativeai`

> **If `requirements.txt` is missing:**  
> ```bash
> pip install aiohttp prometheus_client opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc aiofiles rich pyyaml ruamel.yaml hcl2 sentence-transformers tiktoken fastapi uvicorn openai anthropic google-generativeai
> ```

### 4. Install External Tools (Optional for Full Validation)

```bash
# hadolint
sudo apt-get install -y hadolint   # Or equivalent for your OS

# trivy
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh

# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### 5. Set Up Local LLM (Optional, Use if No API Keys)

```bash
docker run -d -p 11434:11434 ollama/ollama
ollama pull llama2
```

**Verify Ollama:**
```bash
curl http://localhost:11434
```

### 6. Configure Environment

```bash
export GROK_API_KEY="your-grok-api-key"          # Optional if using LocalProvider
export SLACK_WEBHOOK="your-slack-webhook"        # Optional for alerts
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"   # Optional for tracing
```

**Or create a `.env` file:**
```
GROK_API_KEY=your-grok-api-key
SLACK_WEBHOOK=your-slack-webhook
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```
**Load with:**
```bash
source .env
```

### 7. Set Up Templates

```bash
mkdir deploy_templates
echo -e "Generate a production-grade {{ target }} configuration for these files: {{ files | join(', ') }}.\nInclude all required build, environment, network, and security settings.\nConfiguration must be safe, efficient, and ready for CI/CD.\nAdditional instructions: {{ instructions | default('') }}" > deploy_templates/docker_default.jinja
```

### 8. Set Up Few-Shot Examples (Optional for Enhanced Prompts)

```bash
mkdir few_shot_examples
echo '{"query": "Python Flask app Dockerfile", "example": "FROM python:3.11\nCOPY . /app\nWORKDIR /app\nRUN pip install flask\nEXPOSE 8080\nCMD [\"flask\", \"run\", \"--host=0.0.0.0\", \"--port=8080\"]"}' > few_shot_examples/example1.json
```

### 9. Set Up Tracing (Optional for Debugging)

```bash
docker run -d -p 16686:16686 jaegertracing/all-in-one
```

---

## ✅ Verification

- **Dependencies:**
  ```bash
  python -c "import aiohttp, prometheus_client, opentelemetry, aiofiles, rich, yaml, hcl2, sentence_transformers, tiktoken, fastapi, uvicorn, openai, anthropic, google.generativeai"
  ```
- **Ollama:**
  ```bash
  curl http://localhost:11434
  ```
- **Tools:**
  ```bash
  hadolint --version
  trivy --version
  helm version
  ```

---

## ⏭️ Next Steps

- **System Overview:** Review [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md).
- **Maintenance:** See [MAINTENANCE_GUIDE.md](MAINTENANCE_GUIDE.md).
- **Extending:** Check [EXTENSIBILITY_GUIDE.md](EXTENSIBILITY_GUIDE.md).
- **Testing:** Refer to [TESTING_GUIDE.md](TESTING_GUIDE.md).

---