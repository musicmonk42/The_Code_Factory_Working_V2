# Demo Implementation Guide

This handbook walks you through implementing the investor demo for the **AI README-to-App Code Generator**. The goal: generate a production-ready Dockerfile from a text description, validate it, and save it to disk, with investor-friendly console output via [rich](https://github.com/Textualize/rich).  
*Assumes your environment is set up per [SETUP_GUIDE.md](SETUP_GUIDE.md).*

---

## 🚦 Demo Workflow

1. **Input:** Text description (e.g., “Create a Python Flask app, expose port 8080”)
2. **Prompt:** Generate prompt with `DeployPromptAgent` (`deploy_prompt.py`)
3. **LLM Call:** Use `DeployLLMOrchestrator` (`deploy_llm_call.py`) with `GrokProvider` or `LocalProvider`
4. **Response Handling:** Normalize/enrich using `handle_deploy_response` (`deploy_response_handler.py`)
5. **Validation:** Validate with `validate_deploy_configs` (`deploy_validator.py`)
6. **Output:** Save to `demo_output/Dockerfile` using `save_files_to_output` (`file_utils.py`)
7. **Display:** Show results with rich panels and log with `observability_utils.py`

---

## 📝 Implementation Steps

### 1. Create `demo_investor.py`

Copy and save the following as `demo_investor.py`:

```python name=demo_investor.py
"""
demo_investor.py
Investor demo for AI README-to-App Code Generator.
Generates a Dockerfile from a text description, validates it, and saves it to disk.
"""

import asyncio
import logging
import json
from pathlib import Path
from utils.file_utils import save_files_to_output
from utils.observability_utils import logger, add_provenance
from providers.grok_provider import GrokProvider
from providers.local_provider import LocalProvider
from deploy_llm_call import DeployLLMOrchestrator
from deploy_prompt import DeployPromptAgent
from deploy_response_handler import handle_deploy_response
from deploy_validator import validate_deploy_configs
from rich.console import Console
from rich.panel import Panel

# Setup rich console for investor-friendly output
console = Console()

async def run_investor_demo():
    # 1. Input: Simple app description
    app_description = "Create a Python Flask web app, expose port 8080, and include a basic health check endpoint."
    console.print(Panel(f"Input Description: {app_description}", title="Step 1: Input", border_style="green"))

    # 2. Prompt: Generate using DeployPromptAgent
    prompt_agent = DeployPromptAgent(repo_path=".")
    prompt_data = await prompt_agent.build_deploy_prompt(target="docker", files=["app.py"], instructions=app_description)
    prompt = prompt_data["prompt"]
    console.print(Panel(f"Generated Prompt:\n{prompt[:200]}...", title="Step 2: Prompt", border_style="blue"))

    # 3. LLM Call: Use DeployLLMOrchestrator with GrokProvider or LocalProvider
    orchestrator = DeployLLMOrchestrator()
    try:
        provider = GrokProvider()
        console.print(Panel("Using xAI Grok provider for LLM call", title="Step 3: LLM Call", border_style="cyan"))
    except ValueError as e:
        logger.warning(f"GrokProvider failed: {e}. Falling back to LocalProvider.")
        provider = LocalProvider()
        console.print(Panel("Using Local provider (Ollama) for LLM call", title="Step 3: LLM Call", border_style="cyan"))

    try:
        response = await orchestrator.generate_config(prompt, model="grok-4" if isinstance(provider, GrokProvider) else "llama2")
        dockerfile_content = response["config"]
        console.print(Panel(f"Generated Dockerfile:\n{dockerfile_content}", title="Step 4: Generated Output", border_style="cyan"))
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        dockerfile_content = """
# Fallback Dockerfile
FROM python:3.11
COPY . /app
WORKDIR /app
RUN pip install flask
EXPOSE 8080
CMD ["flask", "run", "--host=0.0.0.0", "--port=8080"]
"""
        console.print(Panel("Using fallback Dockerfile due to LLM failure", title="Step 4: Fallback Output", border_style="yellow"))

    # 4. Response Handling: Normalize and enrich
    handled_response = await handle_deploy_response(dockerfile_content, output_format="dockerfile")
    enriched_dockerfile = handled_response["config"]
    console.print(Panel(f"Enriched Dockerfile:\n{enriched_dockerfile}", title="Step 5: Enriched Output", border_style="magenta"))

    # 5. Validation: Check Dockerfile
    validation_result = await validate_deploy_configs(enriched_dockerfile, target="docker")
    console.print(Panel(f"Validation Result: {validation_result['report']}", title="Step 6: Validation", border_style="magenta"))

    # 6. Output: Save Dockerfile
    output_dir = Path("demo_output")
    await save_files_to_output({"Dockerfile": enriched_dockerfile}, output_dir)
    console.print(Panel(f"Dockerfile saved to {output_dir}/Dockerfile", title="Step 7: Output", border_style="green"))

    # 7. Provenance: Log metadata
    provenance = add_provenance({
        "action": "investor_demo",
        "input": app_description,
        "output": enriched_dockerfile,
        "validation": validation_result["report"]
    }, action="demo_completed")
    console.print(Panel(f"Provenance:\n{json.dumps(provenance, indent=2)}", title="Step 8: Provenance", border_style="blue"))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_investor_demo())
```

---

### 2. Run the Demo

```bash
python demo_investor.py
```

---

### 3. Verify Output

- Check `demo_output/Dockerfile` for the generated file.
- Review console logs for `run_id` and metrics (e.g., `deploy_calls_total`).

---

## 🧩 Key Components

- **`deploy_prompt.py`**: `DeployPromptAgent` generates prompts using Jinja2 templates (`deploy_templates/`)
- **`deploy_llm_call.py`**: `DeployLLMOrchestrator` manages LLM calls with provider selection
- **`deploy_response_handler.py`**: Normalizes and enriches outputs (badges, changelogs)
- **`deploy_validator.py`**: Validates Dockerfiles using hadolint and trivy
- **`file_utils.py`**: Saves configs with compliance metadata
- **`observability_utils.py`, `runner_logging.py`**: Logging and metrics

---

## 📝 Notes

- **Provider Choice:** Use `GrokProvider` for cloud LLM or `LocalProvider` (Ollama) for minimal dependencies.
- **Fallback:** A fallback Dockerfile is included if LLM calls fail.
- **Validation:** Requires hadolint and trivy for full validation; if unavailable, validation will report as skipped.

---

## 🚀 Next Steps

- **Troubleshoot:** See `DEBUGGING_TROUBLESHOOTING.md` for issues.
- **Extend:** Check `EXTENDING_DEMO.md` for enhancements.
- **Context:** Review `ARCHITECTURE.md` for technical details.

---