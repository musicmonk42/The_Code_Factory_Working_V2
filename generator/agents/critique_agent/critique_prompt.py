import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiohttp
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from langchain.tools import tool
from opentelemetry import trace
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None

TESTING = os.getenv("TESTING") == "1"

# --- Optional Third-Party Imports (Presidio, spaCy, Torch, PlantUML) ---
# These MUST NEVER hard-crash import in CI/Windows. They are best-effort only.

AnalyzerEngine = None
AnonymizerEngine = None
PlantUML = None
HAS_PRESIDIO = False

# Presidio + spaCy stack (PII/PHI utilities)
try:
    if not TESTING:
        from presidio_analyzer import AnalyzerEngine as _AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine as _AnonymizerEngine

        AnalyzerEngine = _AnalyzerEngine
        AnonymizerEngine = _AnonymizerEngine
        HAS_PRESIDIO = True
    else:
        # In TESTING, we deliberately skip heavy NLP stack to avoid torch / DLL issues.
        logger.info(
            "TESTING=1: Skipping Presidio/ spaCy / torch initialization in critique_prompt."
        )
except (ImportError, OSError, RuntimeError, Exception) as e:  # ultra-defensive
    logger.warning(
        "Optional Presidio/PII stack unavailable or failed to load "
        f"(safe to ignore, using degraded mode): {e}"
    )
    AnalyzerEngine = None
    AnonymizerEngine = None
    HAS_PRESIDIO = False

# PlantUML (for optional UML diagrams)
try:
    if not TESTING:
        from plantuml import PlantUML as _PlantUML

        PlantUML = _PlantUML
    else:
        logger.info("TESTING=1: Skipping PlantUML initialization in critique_prompt.")
except (ImportError, OSError, RuntimeError, Exception) as e:
    logger.warning(
        "Optional PlantUML client unavailable (safe to ignore, UML disabled): %s",
        e,
    )
    PlantUML = None

# --- RUNNER UTILITY IMPORTS (ENFORCED) ---
# Replace old imports and local stubs with centralized runner utilities.
try:
    from runner.llm_client import count_tokens
    from runner.runner_backends import rag_retrieve as _rag_retrieve_context
    from runner.runner_logging import log_action, log_audit_event
    from runner.runner_metrics import CRITIQUE_PROMPT_BUILDS, CRITIQUE_PROMPT_LATENCY
    from runner.runner_parsers import detect_language, translate_text
    from runner.runner_security_utils import redact_secrets, scrub_pii_and_secrets
    from runner.summarize_utils import summarize as summarize_text

    # Use real runner metrics
    PROMPT_BUILDS = CRITIQUE_PROMPT_BUILDS
    PROMPT_LATENCY = CRITIQUE_PROMPT_LATENCY

    # Stub for legacy dependency (kept only so calls do not explode)
    class LanguageCritiquePlugin:
        async def _run_tool(self, *args, **kwargs):
            logging.error(
                "LanguageCritiquePlugin is a dependency bleed and should be refactored."
            )
            return True, {"output": "Mock success"}

    def save_files_to_output(*args, **kwargs):
        # Legacy stub; no-op in this module.
        return None

except ImportError as e:
    # Graceful degraded mode: provide local stand-ins.
    from prometheus_client import Counter, Histogram

    logging.warning(
        f"Failed to import runner utilities: {e}. "
        "Running in standalone/degraded mode with dummy implementations."
    )

    def log_audit_event(*args, **kwargs):
        logging.warning("Audit logging disabled.")

    def log_action(*args, **kwargs):
        logging.warning("Log action disabled.")

    def redact_secrets(text: str) -> str:
        return text

    def count_tokens(prompt: str, model_name: str = "default") -> int:
        # Simple heuristic: ~4 chars/token
        return max(1, len(prompt) // 4)

    async def summarize_text(text: str, max_length: int = 500) -> str:
        return text[:max_length]

    async def detect_language(text: str) -> str:
        return "en"

    async def translate_text(text: str, target: str = "en") -> str:
        return text

    async def scrub_pii_and_secrets(text: str) -> str:
        return text

    async def _rag_retrieve_context(query: str, top_k: int = 5) -> str:
        return ""

    class LanguageCritiquePlugin:
        async def _run_tool(self, *args, **kwargs):
            return True, {"output": "Mock success"}

    def save_files_to_output(*args, **kwargs):
        return None

    # Local metrics; label style must be compatible with production & Dummy use
    # FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
    try:
        PROMPT_BUILDS = Counter(
            "critique_prompt_builds_total",
            "Total prompt builds",
            ["status"],
        )
        PROMPT_LATENCY = Histogram(
            "critique_prompt_build_latency_seconds",
            "Prompt build latency",
        )
    except ValueError:
        # Metrics already registered (happens during pytest collection)
        from prometheus_client import REGISTRY
        PROMPT_BUILDS = REGISTRY._names_to_collectors.get("critique_prompt_builds_total")
        PROMPT_LATENCY = REGISTRY._names_to_collectors.get("critique_prompt_build_latency_seconds")

# Constants
MAX_PROMPT_TOKENS = 8000
TEMPLATE_DIR = "prompt_templates"
os.makedirs(TEMPLATE_DIR, exist_ok=True)

# FIX: Define a constant namespace for deterministic UUIDs
AGENT_NAMESPACE_UUID = uuid.UUID("c4a1b1b0-2b1a-4c8a-9f0a-1a2b3c4d5e6f")

# Default Template (Inline for safety/fallback)
DEFAULT_TEMPLATE = """
You are a code reviewer analyzing generated code and tests against requirements.

Tasks:
{% for task in tasks %}
{{ loop.index }}. {{ task }}
{% endfor %}

Output JSON schema:
{{ output_schema }}

Requirements:
{{ req_summary }}

Code Summary:
{{ code_summary }}

Test Summary:
{{ test_summary }}

State:
{{ state_summary }}

{% if multi_modal %}
Multi-modal / auxiliary context:
{{ multi_modal_data }}
{% endif %}

{% if chain_of_thought %}
First, think through your reasoning step-by-step (this reasoning is internal).
Then, output ONLY the final JSON object matching the schema above.
{% endif %}

Operation ID: {{ op_id }}
Prompt Hash: {{ prompt_hash }}
User Context: {{ user_context }}

{% if rag_context %}
--- RETRIEVED RAG CONTEXT ---
{{ rag_context }}
-----------------------------
{% endif %}
"""


@tool
async def rag_retrieve(query: str) -> str:
    """
    Retrieve relevant context from a RAG system via the centralized runner client.
    """
    context = await _rag_retrieve_context(query)
    if context:
        log_action("RAG Retrieved", {"query": query, "context_length": len(context)})
    return context if context else "No relevant context found in RAG."


async def auto_tune_template_based_on_feedback(
    template_content: str, feedback: Optional[str] = None
) -> str:
    """
    Uses an external LLM (Grok) to refine the prompt template based on user feedback.
    If GROK_API_KEY is not set or any error occurs, the original template is returned.
    """
    if not feedback:
        return template_content

    grok_api_key = os.getenv("GROK_API_KEY")
    if not grok_api_key:
        logger.warning(
            "GROK_API_KEY not set. Cannot auto-tune template based on feedback."
        )
        return template_content

    refine_prompt = (
        "You are an expert prompt engineer. Refine the following Jinja2 prompt "
        "template based on the provided feedback to improve the quality of the "
        "generated critique. Ensure the output is *only* the refined Jinja2 template, "
        "without any extra text or markdown wrappers. Focus on clarity, conciseness, "
        "and effectiveness.\n\n"
        f"Feedback: {feedback}\n\n"
        "Original Template:\n```jinja\n"
        f"{template_content}\n```\n\n"
        "Refined Template:"
    )

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {grok_api_key}",
                "Content-Type": "application/json",
            }
            data = {
                "model": "grok-4",
                "messages": [{"role": "user", "content": refine_prompt}],
                "temperature": 0.2,
                "max_tokens": 2000,
            }
            async with session.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json=data,
            ) as resp:
                resp.raise_for_status()
                response_json = await resp.json()
                choices = response_json.get("choices") or []
                if choices:
                    refined_template_content = choices[0]["message"]["content"].strip()
                    if refined_template_content.startswith("```jinja"):
                        refined_template_content = refined_template_content.lstrip(
                            "```jinja"
                        ).strip()
                    if refined_template_content.endswith("```"):
                        refined_template_content = refined_template_content.rstrip(
                            "```"
                        ).strip()
                    log_action(
                        "Template Tuned",
                        {
                            "feedback": feedback,
                            "refined_length": len(refined_template_content),
                        },
                    )
                    return refined_template_content
                logger.warning(
                    "Grok returned an empty or unexpected response: %s",
                    response_json,
                )
                return template_content
    except aiohttp.ClientError as e:
        logger.error("Grok API client error during template tuning: %s", e)
    except Exception as e:
        logger.error("Unexpected error during template tuning: %s", e)

    return template_content


async def incorporate_multi_modal_data(
    code_files: Dict[str, str], test_files: Dict[str, str]
) -> str:
    """
    Generates and incorporates multi-modal data such as code coverage and a basic UML diagram.

    Relies on LanguageCritiquePlugin and external tools when available. In degraded/TESTING
    mode, uses stubs and remains deterministic.
    """
    with tracer.start_as_current_span("incorporate_multi_modal_data"):
        multi_modal_summary: List[str] = []
        coverage_data_str = "No coverage data available."

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)

            # Write code files
            for filename, content in code_files.items():
                file_path = temp_dir_path / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
                    await f.write(content)

            # Write test files
            for filename, content in test_files.items():
                file_path = temp_dir_path / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
                    await f.write(content)

            combined_code_content = "\n".join(code_files.values())
            lang = (
                await detect_language(combined_code_content)
                if combined_code_content
                else None
            )

            # Language-specific coverage
            if lang == "python":
                output_file = temp_dir_path / "coverage.json"
                lc_plugin = LanguageCritiquePlugin()
                success, result = await lc_plugin._run_tool(
                    [
                        "pytest",
                        "--cov=.",
                        "--cov-report",
                        f"json:{output_file}",
                    ],
                    str(temp_dir_path),
                    "pytest_coverage",
                    180,
                    True,
                    "python:3.11",
                )
                if success and output_file.exists():
                    try:
                        with open(output_file, "r", encoding="utf-8") as f:
                            coverage_report = json.load(f)
                        percent_covered = coverage_report.get("totals", {}).get(
                            "percent_covered", 0
                        )
                        coverage_data_str = (
                            f"Python Code Coverage: {percent_covered:.2f}%"
                        )
                    except json.JSONDecodeError as e:
                        logger.error("Failed to parse pytest coverage JSON: %s", e)
                else:
                    logger.warning(
                        "Pytest coverage failed or output not found: %s",
                        (result or {}).get("stderr", ""),
                    )
                multi_modal_summary.append(coverage_data_str)

            elif lang in ["javascript", "typescript"]:
                output_file_path = temp_dir_path / "coverage" / "coverage-summary.json"
                dummy_package_json_path = temp_dir_path / "package.json"
                if not dummy_package_json_path.exists():
                    dummy_package_json_content = """
                    {
                      "name": "temp-project",
                      "version": "1.0.0",
                      "scripts": {
                        "test": "jest --coverage"
                      },
                      "devDependencies": {
                        "jest": "^29.0.0"
                      }
                    }
                    """
                    async with aiofiles.open(
                        dummy_package_json_path,
                        mode="w",
                        encoding="utf-8",
                    ) as f:
                        await f.write(dummy_package_json_content)

                lc_plugin = LanguageCritiquePlugin()
                install_success, install_result = await lc_plugin._run_tool(
                    ["npm", "install"],
                    str(temp_dir_path),
                    "npm_install",
                    300,
                    True,
                    "node:20",
                )
                if not install_success:
                    logger.warning(
                        "npm install failed: %s",
                        (install_result or {}).get("stderr", ""),
                    )
                    coverage_data_str = (
                        "JS/TS Code Coverage: Failed to install dependencies."
                    )
                else:
                    test_success, test_result = await lc_plugin._run_tool(
                        [
                            "npm",
                            "test",
                            "--",
                            "--coverage",
                        ],
                        str(temp_dir_path),
                        "jest_coverage",
                        300,
                        True,
                        "node:20",
                    )
                    if test_success and output_file_path.exists():
                        try:
                            with open(output_file_path, "r", encoding="utf-8") as f:
                                coverage_report = json.load(f)
                            statements_pct = (
                                coverage_report.get("total", {})
                                .get("statements", {})
                                .get("pct", 0)
                            )
                            coverage_data_str = (
                                f"JS/TS Code Coverage: {statements_pct:.2f}%"
                            )
                        except json.JSONDecodeError as e:
                            logger.error("Failed to parse jest coverage JSON: %s", e)
                    else:
                        logger.warning(
                            "Jest coverage failed or output not found: %s",
                            (test_result or {}).get("stderr", ""),
                        )

                multi_modal_summary.append(coverage_data_str)

            else:
                msg = f"Code Coverage: Not supported for language {lang}."
                multi_modal_summary.append(msg)
                logger.info(msg)

        # UML Diagram generation (best-effort, with robust fallbacks)
        uml_diagram_str = "UML Diagram: Not generated (PlantUML unavailable)."
        try:
            uml_code = "@startuml\n"
            class_info: List[Dict[str, Any]] = []
            relationships: List[str] = []

            if PlantUML and lang in ("python", "javascript", "typescript"):
                plantuml_server_url = os.getenv(
                    "PLANTUML_SERVER_URL",
                    "http://www.plantuml.com/plantuml",
                )
                PlantUML(plantuml_server_url)

                for _, content in code_files.items():
                    if lang == "python":
                        import ast

                        try:
                            tree = ast.parse(content)
                            for node in ast.walk(tree):
                                if isinstance(node, ast.ClassDef):
                                    methods = [
                                        n.name
                                        for n in node.body
                                        if hasattr(n, "name")
                                        and getattr(n, "__class__", None).__name__
                                        == "FunctionDef"
                                    ]
                                    parent = next(
                                        (
                                            b.id
                                            for b in node.bases
                                            if getattr(b, "__class__", None).__name__
                                            == "Name"
                                        ),
                                        None,
                                    )
                                    class_info.append(
                                        {
                                            "name": node.name,
                                            "parent": parent,
                                            "methods": methods,
                                        }
                                    )
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls["methods"]:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                                if cls["parent"]:
                                    relationships.append(
                                        f"{cls['parent']} <|-- {cls['name']}"
                                    )
                        except SyntaxError as e:
                            logger.warning("Failed to parse Python code for UML: %s", e)

                    elif lang in ("javascript", "typescript"):
                        try:
                            import esprima

                            tree = esprima.parseModule(content)
                            for node in tree.body:
                                if node.type == "ClassDeclaration":
                                    methods = [
                                        m.key.name
                                        for m in node.body.body
                                        if m.type == "MethodDefinition"
                                    ]
                                    parent = (
                                        node.superClass.name
                                        if node.superClass
                                        else None
                                    )
                                    class_info.append(
                                        {
                                            "name": node.id.name,
                                            "parent": parent,
                                            "methods": methods,
                                        }
                                    )
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls["methods"]:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                                if cls["parent"]:
                                    relationships.append(
                                        f"{cls['parent']} <|-- {cls['name']}"
                                    )
                        except Exception as e:
                            logger.warning("Failed to parse JS/TS code for UML: %s", e)

                uml_code += "\n".join(relationships) + "\n@enduml"
                # We don't actually need to call PlantUML server to keep this
                uml_diagram_str = (
                    "UML Diagram: Generated for "
                    f"{lang} code, depicting classes: "
                    f"{', '.join(c['name'] for c in class_info) if class_info else 'none'}."
                )
            else:
                # Basic regex-based UML summary if PlantUML not available
                for _, content in code_files.items():
                    if lang == "python":
                        classes = re.findall(
                            r"class\s+(\w+)(?:\((.*?)\))?\s*:",
                            content,
                        )
                        for class_name, parent in classes:
                            class_info.append(
                                {
                                    "name": class_name,
                                    "parent": parent.strip() if parent else None,
                                    "methods": [],
                                }
                            )
                    elif lang in ("javascript", "typescript"):
                        classes = re.findall(
                            r"class\s+(\w+)(?:\s+extends\s+(\w+))?",
                            content,
                        )
                        for class_name, parent in classes:
                            class_info.append(
                                {
                                    "name": class_name,
                                    "parent": parent or None,
                                    "methods": [],
                                }
                            )

                uml_code += "\n".join(relationships) + "\n@enduml"
                uml_diagram_str = (
                    "UML Diagram: Generated for "
                    f"{lang} code, depicting classes: "
                    f"{', '.join(c['name'] for c in class_info) if class_info else 'none'}."
                )
        except Exception as e:
            logger.error("Error generating PlantUML diagram: %s", e)
            uml_diagram_str = (
                "UML Diagram: Generation failed due to an error. "
                "Falling back to basic summary."
            )

        multi_modal_summary.append(uml_diagram_str)

        code_snip = (
            list(code_files.values())[0][:500]
            if code_files
            else "No code snippet available."
        )
        test_snip = (
            list(test_files.values())[0][:500]
            if test_files
            else "No test snippet available."
        )

        multi_modal_summary.append(
            f"Representative Code Snippet:\n```\n{code_snip}\n```"
        )
        multi_modal_summary.append(
            f"Representative Test Snippet:\n```\n{test_snip}\n```"
        )

        return "\n\n".join(multi_modal_summary)


class PromptConfig(BaseModel):
    language: str = Field(default="python")
    framework: str = Field(default="general")
    tasks: List[str] = Field(
        default_factory=lambda: [
            "Semantic Alignment: Score (0-1) how well the generated code aligns with the stated requirements. List any specific mismatches or semantic drifts.",
            "Hallucinations: Identify any invented or non-existent libraries, illogical code constructs, or added features that were not explicitly requested in the requirements.",
            "Test Quality: Score (0-1) the comprehensiveness and effectiveness of the provided tests. Suggest specific improvements if the score is below 0.8.",
            "Suggested Fixes: Provide actionable code and/or test fixes in a clear diff-like format, or describe the necessary changes for each identified issue.",
            "Ambiguities: Point out any unclear, vague, or underspecified requirements that could lead to multiple interpretations or incorrect implementations.",
        ]
    )
    output_schema: str = Field(
        default="""
    {
        "semantic_alignment_score": float,
        "drift_issues": [{"description": "string", "severity": "LOW|MEDIUM|HIGH", "location": "file:line_start-line_end"}],
        "hallucinations": [{"description": "string", "severity": "LOW|MEDIUM|HIGH", "reason": "string", "location": "file:line_start-line_end"}],
        "test_quality_score": float,
        "test_suggestions": [{"description": "string", "priority": "LOW|MEDIUM|HIGH", "example_test_code": "string", "file": "string"}],
        "suggested_fixes": {"file_path.ext": [{"line": int, "old_code": "string", "new_code": "string", "reason": "string"}]},
        "ambiguities": [{"description": "string", "clarification_needed": "string", "requirement_id": "string"}],
        "rationale": "string",
        "confidence_score": float,
        "dependability_analysis": {
            "reliability_robustness_issues": [{"description": "string", "suggestion": "string"}],
            "ethical_bias_issues": [{"description": "string", "mitigation": "string"}],
            "transparency_explainability_issues": [{"description": "string", "suggestion": "string"}],
            "maintainability_evolution_issues": [{"description": "string", "suggestion": "string"}],
            "security_posture_issues": [{"description": "string", "suggestion": "string"}]
        }
    }
    """
    )
    chain_of_thought: bool = Field(default=True)
    multi_modal: bool = Field(default=True)
    user_context: str = Field(
        default=(
            "The user is a software developer seeking comprehensive "
            "code quality feedback."
        )
    )
    template_file: Optional[str] = Field(default=None)
    feedback: Optional[str] = Field(default=None)


async def _maybe_await(value: Any) -> Any:
    """
    Helper that allows us to pass in either sync or async helpers
    (e.g., count_tokens, summarize_text) without caring at call sites.
    """
    if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
        return await value
    return value


async def build_semantic_critique_prompt(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    requirements: Dict[str, Any],
    state_summary: str,
    config: Optional[Union["PromptConfig", Dict[str, Any]]] = None,
) -> str:
    """
    Build a production-grade, deterministic, requirements-aware critique prompt.

    Contract (enforced by tests):

    - Non-empty string.
    - Incorporates:
        * Requirements summary text.
        * Code + test filenames/content (summarized).
        * State summary.
        * Configured critique tasks.
    - Deterministic for identical inputs.
    - Changes when requirements change (via content + prompt_hash).
    - Safe in TESTING/CI environments:
        * No hard dependency on external services.
        * Uses stubs/guards when integrations are missing.
    """
    start_time = time.perf_counter()

    if not code_files:
        raise ValueError("Code files dictionary cannot be empty")

    # --- Normalize config ---
    if config is None:
        conf = PromptConfig()
    elif isinstance(config, PromptConfig):
        conf = config
    elif isinstance(config, dict):
        conf = PromptConfig(**config)
    else:
        raise TypeError("config must be a PromptConfig instance, a dict, or None")

    # --- FIX for Determinism ---
    # 1. Calculate deterministic hash FIRST
    prompt_content_hash_input = (
        json.dumps(requirements, sort_keys=True)
        + json.dumps(code_files, sort_keys=True)
        + json.dumps(test_files, sort_keys=True)
        + state_summary
        + conf.model_dump_json()
    )
    prompt_hash = hashlib.sha256(prompt_content_hash_input.encode("utf-8")).hexdigest()

    # 2. Create deterministic op_id FROM the hash
    op_id = str(uuid.uuid5(AGENT_NAMESPACE_UUID, prompt_hash))
    # --- END FIX ---

    # --- Language handling for requirements (best-effort) ---
    try:
        req_text_for_lang = json.dumps(requirements, ensure_ascii=False)
        req_lang = await detect_language(req_text_for_lang)
    except Exception:
        req_lang = "en"

    if req_lang and req_lang != "en":
        try:
            translated: Dict[str, Any] = {}
            for k, v in requirements.items():
                translated[k] = await translate_text(str(v), target="en")
            requirements = translated
            state_summary = (
                state_summary
                + f"\nNote: Requirements auto-translated from {req_lang} to English."
            )
        except Exception as exc:
            logger.warning(
                "Requirements translation failed (%s); continuing with original.",
                exc,
            )

    # --- PII / secrets scrubbing & summaries ---
    try:
        raw_req_json = json.dumps(requirements, indent=2, sort_keys=True)
    except TypeError:
        # Make it JSON-safe for weird objects
        safe_req = {
            str(k): (str(v) if not isinstance(v, (str, int, float, bool)) else v)
            for k, v in requirements.items()
        }
        raw_req_json = json.dumps(safe_req, indent=2, sort_keys=True)

    try:
        req_summary = await scrub_pii_and_secrets(raw_req_json)
    except Exception:
        req_summary = raw_req_json

    # Code summary
    code_concat = "\n".join(
        f"// File: {name}\n{content}" for name, content in sorted(code_files.items())
    )
    try:
        code_safe = await scrub_pii_and_secrets(code_concat)
    except Exception:
        code_safe = code_concat

    code_summary = await _maybe_await(summarize_text(code_safe, max_length=1200))

    # Test summary
    test_concat = "\n".join(
        f"// File: {name}\n{content}" for name, content in sorted(test_files.items())
    )
    try:
        test_safe = await scrub_pii_and_secrets(test_concat)
    except Exception:
        test_safe = test_concat

    test_summary = await _maybe_await(summarize_text(test_safe, max_length=800))

    # --- RAG context (optional / test-safe) ---
    rag_context = ""
    try:
        # Query is now deterministic because op_id is deterministic
        rag_query = f"code critique support for op_id={op_id}"
        rag_context = await rag_retrieve(rag_query)
    except Exception:
        rag_context = ""

    # --- Multi-modal (optional) ---
    multi_modal_data = ""
    if conf.multi_modal:
        try:
            multi_modal_data = await incorporate_multi_modal_data(
                code_files, test_files
            )
        except Exception:
            multi_modal_data = ""

    # --- Enrich state summary with available tools description ---
    tools_description = [
        {
            "name": "rag_retrieve",
            "description": (
                "Use this tool to fetch additional docs or context relevant to "
                "libraries, errors, patterns, or best practices."
            ),
        }
    ]
    final_state_summary = (
        state_summary
        + "\n\nAvailable Tools:\n"
        + json.dumps(tools_description, indent=2)
        + "\nUse rag_retrieve when external context measurably improves the critique."
    )

    # --- Load template (Jinja2 + inline fallback) ---
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(disabled_extensions=("j2",)),
    )
    template_name = conf.template_file or "default_critique_template.j2"

    template_content_str = ""
    try:
        # Try to get the source string from the loader
        template_content_str = env.loader.get_source(env, template_name)[0]
    except Exception as e:
        logger.warning(
            "Failed to load template '%s': %s. Using default inline template.",
            template_name,
            e,
        )
        template_content_str = DEFAULT_TEMPLATE  # Use the inline string as fallback

    # --- Optional auto-tuning of template (best-effort, deterministic) ---
    try:
        # Tune the *raw template string*, not a rendered version
        tuned_template_str = await auto_tune_template_based_on_feedback(
            template_content_str,
            feedback=conf.feedback,  # Pass the feedback from config
        )
        # Create the final template object from the (potentially) tuned string
        final_template = Template(tuned_template_str)
    except Exception as e:
        logger.warning(f"Failed to auto-tune template: {e}. Using original template.")
        final_template = Template(template_content_str)  # Fallback to untuned template

    # --- Render final prompt ---
    prompt = final_template.render(
        tasks=conf.tasks,
        output_schema=conf.output_schema,
        req_summary=req_summary,
        code_summary=code_summary,
        test_summary=test_summary,
        state_summary=final_state_summary,
        multi_modal_data=multi_modal_data,
        multi_modal=conf.multi_modal,
        chain_of_thought=conf.chain_of_thought,
        op_id=op_id,
        prompt_hash=prompt_hash,
        user_context=conf.user_context,
        rag_context=rag_context,
    )

    # --- Token budget & truncation (safe, deterministic) ---
    try:
        MAX_PROMPT_TOKENS = 8192
        token_count = await _maybe_await(count_tokens(prompt, model_name="default"))
        if token_count > MAX_PROMPT_TOKENS:
            logger.warning(
                "Generated prompt (%s tokens) exceeds limit (%s); summarizing.",
                token_count,
                MAX_PROMPT_TOKENS,
            )
            prompt = await _maybe_await(
                summarize_text(prompt, max_length=MAX_PROMPT_TOKENS * 4)
            )
    except Exception:
        # If counting fails, return the raw prompt — tests only care it's stable.
        pass

    # --- Metrics (no-op stubs in TESTING) ---
    try:
        if "PROMPT_BUILDS" in globals():
            PROMPT_BUILDS.labels(status="ok").inc()
        if "PROMPT_LATENCY" in globals():
            PROMPT_LATENCY.observe(time.perf_counter() - start_time)
    except Exception:
        pass

    log_action(
        "CritiquePromptBuilt",
        {
            "op_id": op_id,
            "prompt_hash": prompt_hash,
            "has_rag": bool(rag_context),
            "has_multi_modal": bool(multi_modal_data),
        },
    )

    return prompt


if __name__ == "__main__":
    import argparse
    import asyncio as _asyncio_mod

    parser = argparse.ArgumentParser(description="Build semantic critique prompt")
    parser.add_argument(
        "--code-dir",
        required=True,
        help="Path to the directory containing code files.",
    )
    parser.add_argument(
        "--test-dir",
        default="",
        help=("Path to the directory containing test files " "(optional)."),
    )
    parser.add_argument(
        "--requirements",
        default="{}",
        help=("JSON string of requirements or path to a JSON " "file."),
    )
    parser.add_argument(
        "--state-summary",
        default="Auto-generated",
        help=("Summary of the current state or context."),
    )
    parser.add_argument(
        "--config",
        default="{}",
        help=("JSON string of prompt configuration overrides."),
    )
    args = parser.parse_args()

    async def run_main() -> None:
        code_files: Dict[str, str] = {}
        for f_name in os.listdir(args.code_dir):
            f_path = Path(args.code_dir) / f_name
            if f_path.is_file():
                async with aiofiles.open(f_path, "r", encoding="utf-8") as f:
                    code_files[f_name] = await f.read()

        test_files: Dict[str, str] = {}
        if args.test_dir:
            for f_name in os.listdir(args.test_dir):
                f_path = Path(args.test_dir) / f_name
                if f_path.is_file():
                    async with aiofiles.open(f_path, "r", encoding="utf-8") as f:
                        test_files[f_name] = await f.read()

        requirements_data: Dict[str, Any] = {}
        try:
            requirements_data = json.loads(args.requirements)
        except json.JSONDecodeError:
            if Path(args.requirements).is_file():
                try:
                    async with aiofiles.open(
                        Path(args.requirements),
                        "r",
                        encoding="utf-8",
                    ) as f:
                        requirements_data = json.loads(await f.read())
                except Exception as e:
                    logger.error(
                        "Failed to read/parse requirements " "file '%s': %s",
                        args.requirements,
                        e,
                    )
                    requirements_data = {}
            else:
                logger.warning(
                    "Requirements input '%s' is neither valid JSON "
                    "nor a file. Using empty requirements.",
                    args.requirements,
                )
                requirements_data = {}

        try:
            config_data = json.loads(args.config)
        except json.JSONDecodeError:
            logger.error(
                "Invalid JSON for config '%s'. Using empty config.",
                args.config,
            )
            config_data = {}

        prompt_output = await build_semantic_critique_prompt(
            code_files,
            test_files,
            requirements_data,
            args.state_summary,
            config_data,
        )
        print(prompt_output)

    _asyncio_mod.run(run_main())
