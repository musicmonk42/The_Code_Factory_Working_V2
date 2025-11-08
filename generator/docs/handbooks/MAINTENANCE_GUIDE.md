# Maintenance Guide

This handbook guides engineers on maintaining the **AI README-to-App Code Generator**, covering debugging, monitoring, and updating plugins to ensure reliability and performance as of July 28, 2025.  
*Assumes the environment is set up per [DEVELOPER_SETUP.md](DEVELOPER_SETUP.md).*

---

## 🛠️ Debugging

### Logs

- **Location:**  
  Console by default, or configure to write logs to a file:
  ```python
  logging.basicConfig(level=logging.INFO, filename='app.log')
  ```
- **Key Logs:**  
  - Prompt generation started [run_id=...]
  - Deployment call started [run_id=..., provider=GrokProvider]
  - Validation started [run_id=..., target=docker]
- **Filter for specific runs:**  
  ```bash
  grep "run_id" app.log
  ```

---

### Metrics

- **Access:**  
  Run the FastAPI server:
  ```bash
  python -m deploy_llm_call --server
  curl http://localhost:8000/metrics
  ```
- **Key Metrics:**  
  - `deploy_calls_total`: Tracks LLM calls by provider/model.
  - `deploy_latency_seconds`: Measures call duration.
  - `deploy_errors_total`: Counts errors.
  - `deploy_provider_health`: Monitors provider status.
- **Tools:**  
  Use Prometheus/Grafana for visualization.

---

### Tracing

- **Setup Jaeger:**
  ```bash
  docker run -d -p 16686:16686 jaegertracing/all-in-one
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
  ```
- **Access:**  
  View traces at [http://localhost:16686](http://localhost:16686)
- **Key Spans:**  
  - `grok_api_call`, `openai_api_call`, etc. (LLM calls)
  - `handle_deploy_response` (response handling)
  - `validate_deploy_configs` (validation)

---

### Health Checks

- **Run:**
  ```bash
  curl http://localhost:8000/health
  ```
- **Expected Output:**
  ```json
  {"providers": {"GrokProvider": true, "LocalProvider": true, ...}}
  ```
- **Reset Circuit Breakers:**
  ```python
  from providers.grok_provider import GrokProvider
  provider = GrokProvider()
  provider.reset_circuit()
  ```

---

## 🚨 Common Issues

- **Missing API Key:**  
  `ValueError: GROK_API_KEY not set`  
  **Fix:** Set `GROK_API_KEY` or use LocalProvider with Ollama.

- **Validation Failure:**  
  `subprocess.CalledProcessError`  
  **Fix:** Install `hadolint`, `trivy`, or skip validation.

- **Template Not Found:**  
  `TemplateNotFound`  
  **Fix:** Verify `deploy_templates/docker_default.jinja` exists.

- **Dependency Issues:**  
  `ModuleNotFoundError`  
  **Fix:** Reinstall `requirements.txt`.

---

## 📈 Monitoring

- **Logs:** Monitor `app.log` for errors or run_id-specific issues.
- **Metrics:** Check `/metrics` for performance trends (e.g., latency spikes).
- **Alerts:** Configure `SLACK_WEBHOOK` for circuit breaker notifications.
- **Health:** Regularly check `/health` endpoint to ensure provider availability.

---

## 🧩 Updating Plugins

- **Providers:** Modify/add in `providers/` (e.g., `custom_provider.py`). Hot-reloading auto-detects changes.
- **Handlers:** Update in `handler_plugins/` (e.g., `helm_handler.py`).
- **Validators:** Update in `validator_plugins/` (e.g., `custom_validator.py`).
- **Templates:** Add/edit in `deploy_templates/` (e.g., `helm_default.jinja`).
- **Test all updates:**  
  ```bash
  python -m unittest discover
  ```

---

## 🧪 Investor Demo Maintenance

- **Script:** Maintain `demo_investor.py` (see [DEMO_IMPLEMENTATION.md](DEMO_IMPLEMENTATION.md)).
- **Verify:** Ensure `demo_output/Dockerfile` is generated correctly.
- **Enhance:** Add metrics or output formats as needed.

---

## ⏭️ Next Steps

- **Setup:** Follow [DEVELOPER_SETUP.md](DEVELOPER_SETUP.md)
- **Extending:** See [EXTENSIBILITY_GUIDE.md](EXTENSIBILITY_GUIDE.md)
- **Testing:** Refer to [TESTING_GUIDE.md](TESTING_GUIDE.md)

---