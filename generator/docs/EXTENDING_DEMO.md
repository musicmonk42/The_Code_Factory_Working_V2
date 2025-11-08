# Extending the Demo

This handbook guides coders on **extending the investor demo** for the AI README-to-App Code Generator to add new features, such as additional providers, output formats, or validation methods.  
The platform’s plugin-based architecture and hot-reloading make extensions straightforward.  
*Assumes the demo is implemented per [DEMO_IMPLEMENTATION.md](DEMO_IMPLEMENTATION.md).*

---

## 🧩 Extending Providers

Add a new LLM provider to support additional models (e.g., a custom API).

### 1. Create a Provider Plugin

**File:** `providers/custom_provider.py`
```python
from docgen_llm_call import LLMProvider
import aiohttp
import logging
import os
import uuid
import time

logger = logging.getLogger(__name__)

class CustomProvider(LLMProvider):
    def __init__(self):
        self.api_key = os.getenv('CUSTOM_API_KEY')
        if not self.api_key:
            raise ValueError("CUSTOM_API_KEY not set")

    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs):
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            data = {"model": model, "prompt": prompt}
            async with session.post("https://api.custom.com/v1/generate", headers=headers, json=data) as resp:
                resp.raise_for_status()
                result = await resp.json()
                return {"content": result["text"], "model": model, "run_id": str(uuid.uuid4()), "timestamp": time.time()}

    async def count_tokens(self, text: str, model: str) -> int:
        return len(text) // 4  # Approximate

    async def health_check(self) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.custom.com/v1/health") as resp:
                return resp.status == 200
```

### 2. Update Demo Script to Use CustomProvider

Modify `demo_investor.py`:
```python
# In run_investor_demo()
try:
    provider = GrokProvider()
    console.print(Panel("Using xAI Grok provider", title="Step 3: LLM Call", border_style="cyan"))
except ValueError:
    try:
        provider = CustomProvider()
        console.print(Panel("Using Custom provider", title="Step 3: LLM Call", border_style="cyan"))
    except ValueError as e:
        logger.warning(f"CustomProvider failed: {e}. Falling back to LocalProvider.")
        provider = LocalProvider()
        console.print(Panel("Using Local provider (Ollama)", title="Step 3: LLM Call", border_style="cyan"))
```

**Set API Key:**
```bash
export CUSTOM_API_KEY="your-custom-api-key"
```

---

## 🧩 Extending Output Formats

Add support for a new output format (e.g., Helm chart).

### 1. Create a Format Handler

**File:** `handler_plugins/helm_handler.py`
```python
from deploy_response_handler import FormatHandler
import yaml
import json

class HelmHandler(FormatHandler):
    def normalize(self, raw: str) -> dict:
        return yaml.safe_load(raw)

    def convert(self, data: dict, to_format: str) -> str:
        if to_format == "json":
            return json.dumps(data, indent=2)
        return yaml.dump(data)

    def extract_sections(self, data: dict) -> dict:
        return {k: json.dumps(v) for k, v in data.items()}

    def lint(self, data: dict) -> list:
        return []  # Placeholder
```

### 2. Update Demo Script to Support Helm Output

In `demo_investor.py`, after prompt generation:
```python
prompt_data = await prompt_agent.build_deploy_prompt(target="helm", files=["app.py"], instructions=app_description)
# Response handling
handled_response = await handle_deploy_response(response["config"], output_format="helm")
# Validation
validation_result = await validate_deploy_configs(enriched_dockerfile, target="helm")
# Output
await save_files_to_output({"Chart.yaml": enriched_dockerfile}, output_dir)
```

---

## 🧩 Extending Validation

Add a new validator for a custom format.

### 1. Create a Validator

**File:** `validator_plugins/custom_validator.py`
```python
from deploy_validator import Validator
import json

class CustomValidator(Validator):
    async def validate(self, config: str, target: str) -> dict:
        report = {"valid": True, "issues": []}
        try:
            json.loads(config)  # Example validation
        except Exception:
            report["valid"] = False
            report["issues"] = ["Invalid JSON"]
        return report

    async def fix(self, config: str, issues: list, target: str) -> str:
        return config  # Placeholder
```

### 2. Update Demo Script to Use New Validator

```python
validation_result = await validate_deploy_configs(enriched_dockerfile, target="custom")
```

---

## 🧩 Enhancing Observability

**Add custom metrics:**
```python
from prometheus_client import Counter
custom_metric = Counter('demo_custom_metric', 'Custom demo metric', ['type'])
# In run_investor_demo()
custom_metric.labels(type='demo_run').inc()
```

---

## 💡 Notes

- **Hot-Reloading:** Plugins in `providers/`, `handler_plugins/`, and `validator_plugins/` are auto-detected and reloaded at runtime.
- **Testing:** Add unit tests for new plugins in the respective module (e.g., `test_custom_provider.py`).
- **Security:** Ensure new plugins scrub sensitive data using `SECRET_PATTERNS` or an equivalent mechanism.

---

## ⏭️ Next Steps

- **Setup:** Follow [SETUP_GUIDE.md](SETUP_GUIDE.md)
- **Troubleshoot:** See [DEBUGGING_TROUBLESHOOTING.md](DEBUGGING_TROUBLESHOOTING.md)
- **Contribute:** Review [CONTRIBUTING.md](CONTRIBUTING.md)

---