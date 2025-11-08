# Setup Guide

This handbook provides step-by-step instructions for setting up the development environment for the **AI README-to-App Code Generator investor demo**. Follow these steps to ensure smooth installation and reliable demo execution.

---

## 📝 Prerequisites

- **Python:** 3.9 or higher
- **Git:** For cloning the repository
- **Docker:** (Optional) For validation and local LLM setup
- **Ollama:** (Optional) For LocalProvider (no API key needed)
- **External Tools:**  
  - `hadolint` (Dockerfile linting)  
  - `trivy` (security scanning; optional for validation)
- **API Key:** `GROK_API_KEY` or equivalent for cloud providers (optional if using local LLM)

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
- *Optional for cloud providers:* `openai`, `anthropic`, `google-generativeai`

> **If `requirements.txt` is missing:**  
> ```bash
> pip install aiohttp prometheus_client opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc aiofiles rich pyyaml ruamel.yaml hcl2 sentence-transformers tiktoken fastapi uvicorn
> ```

### 4. Install External Tools (optional for full validation)

```bash
# hadolint
sudo apt-get install -y hadolint   # Or use your OS’s package manager

# trivy
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh
```

### 5. Set Up Local LLM (Optional, use if no API keys)

```bash
docker run -d -p 11434:11434 ollama/ollama
ollama pull llama2
```

**Verify Ollama is running:**
```bash
curl http://localhost:11434
```

### 6. Configure Environment

```bash
export GROK_API_KEY="your-grok-api-key"        # Optional if using LocalProvider
export SLACK_WEBHOOK="your-slack-webhook"      # Optional for alerts
```

**Alternatively, create a `.env` file:**
```
GROK_API_KEY=your-grok-api-key
SLACK_WEBHOOK=your-slack-webhook
```

**Load with:**
```bash
source .env
```

### 7. Create Template Directory

```bash
mkdir deploy_templates
echo -e "Generate a production-grade Dockerfile for these files: {{ files | join(', ') }}.\nInclude all required build, environment, network, and security settings.\nConfiguration must be safe, efficient, and ready for CI/CD.\nAdditional instructions: {{ instructions | default('') }}" > deploy_templates/docker_default.jinja
```

### 8. Create Few-Shot Examples (optional, for enhanced prompts)

```bash
mkdir few_shot_examples
echo '{"query": "Python Flask app Dockerfile", "example": "FROM python:3.11\nCOPY . /app\nWORKDIR /app\nRUN pip install flask\nEXPOSE 8080\nCMD [\"flask\", \"run\", \"--host=0.0.0.0\", \"--port=8080\"]"}' > few_shot_examples/example1.json
```

### 9. Create a Dummy `app.py` (optional, for context)

```bash
echo "from flask import Flask\napp = Flask(__name__)\n@app.route('/')\ndef hello(): return 'Hello, World!'" > app.py
```

---

## ✅ Verification

**Test Python Dependencies:**
```bash
python -c "import aiohttp, prometheus_client, opentelemetry, aiofiles, rich, yaml, hcl2, sentence_transformers, tiktoken, fastapi, uvicorn"
```

**Test Ollama (if using LocalProvider):**
```bash
curl http://localhost:11434
```

**Test External Tools (if installed):**
```bash
hadolint --version
trivy --version
```

---

## 🚦 Next Steps

- **Implement the Demo:** Follow `DEMO_IMPLEMENTATION.md` to build and run the demo.
- **Troubleshooting:** See `DEBUGGING_TROUBLESHOOTING.md` for common issues.
- **Project Context:** Check `ARCHITECTURE.md` for technical details.

---