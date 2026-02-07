<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Debugging and Troubleshooting

This handbook provides actionable guidance for debugging and troubleshooting issues while developing the investor demo for the **AI README-to-App Code Generator**. Leverage the platform’s observability features (logs, metrics, tracing) to resolve problems efficiently—without external assistance.

---

## 🛑 Common Issues and Solutions

### 1. **Missing API Key**

**Error:**  
`ValueError: GROK_API_KEY environment variable not set`

**Solution:**  
Set the API key:
```bash
export GROK_API_KEY="your-grok-api-key"
```
Or use LocalProvider with Ollama:
```bash
docker run -d -p 11434:11434 ollama/ollama
ollama pull llama2
curl http://localhost:11434  # Verify
```
**Check:**  
Ensure `demo_investor.py` falls back to LocalProvider if GrokProvider is unavailable.

---

### 2. **Validation Failure**

**Error:**  
`subprocess.CalledProcessError` or missing `hadolint`/`trivy`

**Solution:**  
Install external tools:
```bash
sudo apt-get install -y hadolint
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh
```
Or skip validation (validation_result will indicate skipped).

**Check:**  
Run:
```bash
hadolint --version
trivy --version
```

---

### 3. **Template Not Found**

**Error:**  
`TemplateNotFound` for `deploy_templates/docker_default.jinja`

**Solution:**  
Create the template:
```bash
mkdir deploy_templates
echo -e "Generate a production-grade Dockerfile for these files: {{ files | join(', ') }}.\nInclude all required build, environment, network, and security settings.\nConfiguration must be safe, efficient, and ready for CI/CD.\nAdditional instructions: {{ instructions | default('') }}" > deploy_templates/docker_default.jinja
```

---

### 4. **Dependency Issues**

**Error:**  
`ModuleNotFoundError`

**Solution:**  
Verify and install dependencies from `requirements.txt`:
```bash
pip install -r requirements.txt
```
Or install manually:
```bash
pip install aiohttp prometheus_client opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc aiofiles rich pyyaml ruamel.yaml hcl2 sentence-transformers tiktoken fastapi uvicorn
```
**Check:**  
Run:
```bash
python -c "import aiohttp, prometheus_client, opentelemetry, aiofiles, rich, yaml, hcl2, sentence_transformers, tiktoken, fastapi, uvicorn"
```

---

### 5. **Circuit Breaker Open**

**Error:**  
`CircuitOpenError: Provider disabled due to failures`

**Solution:**  
Reset the circuit breaker:
```python
from providers.grok_provider import GrokProvider
provider = GrokProvider()
provider.reset_circuit()
```
**Check provider health:**
```bash
curl http://localhost:8000/health
```

---

## 🐞 Debugging Techniques

### Logs

- **Check console logs** for run_id:
  ```
  2025-07-28 22:50:00,123 [INFO] Call started [run_id=...]
  ```
- **Filter logs** for run_id:
  ```bash
  grep "run_id" log_file
  ```

### Metrics

- **View Prometheus metrics** at [http://localhost:8000/metrics](http://localhost:8000/metrics) (run `python -m deploy_llm_call --server`)
- **Key metrics:**  
  - `deploy_calls_total`
  - `deploy_latency_seconds`
  - `deploy_errors_total`
- **CLI inspect:**
  ```bash
  curl http://localhost:8000/metrics
  ```

### Tracing

- **Enable OpenTelemetry exporter (e.g., Jaeger):**
  ```bash
  docker run -d -p 16686:16686 jaegertracing/all-in-one
  ```
- **Set endpoint in `.env`:**
  ```
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
  ```
- **View traces:**  
  [http://localhost:16686](http://localhost:16686)

### Health Checks

- **Check health:**
  ```bash
  curl http://localhost:8000/health
  ```
- **Expected Output:**
  ```json
  {"providers": {"GrokProvider": true, "LocalProvider": true, ...}}
  ```

### Tests

- **Run all unit tests:**
  ```bash
  python -m unittest discover
  ```
- **Debug specific tests:**
  ```bash
  python -m unittest deploy_llm_call
  ```

---

## 📊 Logs and Metrics

- **Log Location:**  
  - Console by default, or configure `logging.basicConfig` to write to a file (e.g., `app.log`).

- **Key Logs:**  
  - Prompt generation started [run_id=...]
  - Deployment call started [run_id=..., provider=GrokProvider]
  - Validation started [run_id=..., target=docker]

- **Key Metrics:**  
  - `deploy_calls_total`: LLM call count
  - `deploy_latency_seconds`: Call duration
  - `deploy_errors_total`: Error counts (by provider/model)

---

## ⏭️ Next Steps

- **Implement Demo:** Follow [DEMO_IMPLEMENTATION.md](DEMO_IMPLEMENTATION.md)
- **Extend Demo:** See [EXTENDING_DEMO.md](EXTENDING_DEMO.md)
- **Project Context:** Review [ARCHITECTURE.md](ARCHITECTURE.md)

---