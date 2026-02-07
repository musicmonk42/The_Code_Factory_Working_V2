<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Extensibility Guide

This handbook guides engineers on **extending the AI README-to-App Code Generator** with new providers, output formats, validators, or features as of July 28, 2025.  
The platform’s plugin-based architecture and hot-reloading enable seamless extensions.  
*Assumes familiarity with [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md).*

---

## 🧩 Adding Providers

### 1. Create a Provider Plugin

**File:** `providers/custom_provider.py`
```python
from docgen_llm_call import LLMProvider
import aiohttp
import logging
import uuid
import time
import os

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

### 2. Configure Environment

```bash
export CUSTOM_API_KEY="your-custom-api-key"
```

### 3. Test

```python
from providers.custom_provider import CustomProvider
provider = CustomProvider()
import asyncio
asyncio.run(provider.call("Test prompt", "custom-model"))
```

---

## 🧩 Adding Output Formats

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

### 2. Update Demo (if extending investor demo)

In `demo_investor.py`:
```python
prompt_data = await prompt_agent.build_deploy_prompt(target="helm", files=["app.py"], instructions=app_description)
handled_response = await handle_deploy_response(response["config"], output_format="helm")
await save_files_to_output({"Chart.yaml": enriched_dockerfile}, output_dir)
```

---

## 🧩 Adding Validators

### 1. Create a Validator

**File:** `validator_plugins/custom_validator.py`
```python
from deploy_validator import Validator
import json

class CustomValidator(Validator):
    async def validate(self, config: str, target: str) -> dict:
        report = {"valid": True, "issues": []}
        try:
            json.loads(config)
        except Exception:
            report["valid"] = False
            report["issues"] = ["Invalid JSON"]
        return report

    async def fix(self, config: str, issues: list, target: str) -> str:
        return config  # Placeholder
```

### 2. Update Demo (if applicable)

```python
validation_result = await validate_deploy_configs(enriched_dockerfile, target="custom")
```

---

## 🧩 Adding Features

- **Self-Improving Prompts:**  
  Enhance `deploy_prompt.py`’s `self_improve_prompt` with custom feedback logic.

- **Ensemble Mode:**  
  Enable in `deploy_llm_call.py`:
  ```python
  response = await orchestrator.generate_config(prompt, model="grok-4", ensemble=True)
  ```

- **Custom Metrics:**  
  Add to `demo_investor.py`:
  ```python
  from prometheus_client import Counter
  custom_metric = Counter('demo_custom_metric', 'Custom demo metric', ['type'])
  custom_metric.labels(type='demo_run').inc()
  ```

---

## 💡 Notes

- **Hot-Reloading:** Plugins in `providers/`, `handler_plugins/`, `validator_plugins/`, and templates in `deploy_templates/` are auto-detected.
- **Testing:** Add unit tests in the respective module (e.g., `test_custom_provider.py`).
- **Security:** Ensure new plugins scrub sensitive data using `SECRET_PATTERNS`.

---

## ⏭️ Next Steps

- **Setup:** Follow [DEVELOPER_SETUP.md](DEVELOPER_SETUP.md)
- **Maintenance:** See [MAINTENANCE_GUIDE.md](MAINTENANCE_GUIDE.md)
- **Testing:** Refer to [TESTING_GUIDE.md](TESTING_GUIDE.md)

---