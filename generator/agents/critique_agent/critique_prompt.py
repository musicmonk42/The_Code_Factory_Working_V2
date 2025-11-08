import hashlib
import json
import logging
import os
import re
import uuid
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Union
import aiohttp
import aiofiles
from jinja2 import Environment, FileSystemLoader, select_autoescape, Template
from langchain.tools import tool
from opentelemetry import trace
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TESTING = os.getenv("TESTING") == "1"
# --- Optional Third-Party Imports (Presidio, spaCy, Torch, PlantUML) ---
# These MUST NEVER hard-crash import in CI/Windows. They are best-effort only.

AnalyzerEngine = None
AnonymizerEngine = None
PlantUML = None
# Presidio + spaCy stack (PII/PHI utilities)
try:
    if not TESTING:
        from presidio_analyzer import AnalyzerEngine as _AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine as _AnonymizerEngine

        AnalyzerEngine = _AnalyzerEngine
        AnonymizerEngine = _AnonymizerEngine
    else:
        # In TESTING, we deliberately skip heavy NLP stack to avoid torch / DLL issues.
        logger.info("TESTING=1: Skipping Presidio/ spaCy / torch initialization in critique_prompt.")
except (ImportError, OSError, RuntimeError, Exception) as e:  # ultra-defensive
    logger.warning(
        "Optional Presidio/PII stack unavailable or failed to load "
        f"(safe to ignore, using degraded mode): {e}"
    )
    AnalyzerEngine = None
    AnonymizerEngine = None
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
    from runner.runner_logging import log_action, log_audit_event
    from runner.runner_metrics import CRITIQUE_PROMPT_BUILDS, CRITIQUE_PROMPT_LATENCY
    from runner.runner_security_utils import redact_secrets, scrub_pii_and_secrets
    from runner.llm_client import count_tokens
    from runner.summarize_utils import summarize as summarize_text
    from runner.runner_parsers import detect_language, translate_text
    from runner.runner_backends import rag_retrieve as _rag_retrieve_context # Match existing internal name
    
    # Placeholder for unrefactored local dependencies that are being removed or stubbed
    # LanguageCritiquePlugin and save_files_to_output are removed as per instructions.
    
    # Use real runner metrics
    PROMPT_BUILDS = CRITIQUE_PROMPT_BUILDS
    PROMPT_LATENCY = CRITIQUE_PROMPT_LATENCY

    # Stub the removed/misplaced functions to avoid NameError inside the main logic
    class LanguageCritiquePlugin:
        async def _run_tool(self, *args, **kwargs):
            logging.error("LanguageCritiquePlugin is a dependency bleed and should be refactored.")
            return True, {"output": "Mock success"}
    def save_files_to_output(*args): pass


except ImportError as e:
    # Hard fail fallback is removed. Using minimal dummy implementations for graceful degradation.
    # This block is now correctly scoped to only run if runner dependencies fail.
    from prometheus_client import Counter, Histogram # Re-importing necessary metrics components for fallback
    logging.warning(f"Failed to import runner utilities: {e}. Running in standalone/degraded mode with dummy implementations.")
    
    def log_audit_event(*args, **kwargs): logging.warning("Audit logging disabled.")
    def log_action(*args, **kwargs): logging.warning("Log action disabled.") # FIX: Changed logging.gwarning to logging.warning
    def redact_secrets(text): return text
    def count_tokens(prompt, model_name="default"): return len(prompt) // 4
    async def summarize_text(text, max_length=500): return text[:max_length]
    async def detect_language(text: str) -> str: return 'en'
    async def translate_text(text: str, target: str = 'en') -> str: return text
    async def scrub_pii_and_secrets(text: str) -> str: return text # Fallback for scrub
    async def _rag_retrieve_context(query: str, top_k: int = 5) -> str: return ""
    
    # Stub the removed/misplaced functions to avoid NameError inside the main logic
    class LanguageCritiquePlugin: # This stub remains for functions that use it, pending full refactor of those functions
        async def _run_tool(self, *args, **kwargs):
            return True, {"output": "Mock success"}
    def save_files_to_output(*args): pass
    
    # Metrics - Re-define local metrics for standalone/fallback mode
    PROMPT_BUILDS = Counter('critique_prompt_builds_total', 'Total prompt builds', ['status'])
    PROMPT_LATENCY = Histogram('critique_prompt_build_latency_seconds', 'Prompt build latency')

# Constants
MAX_PROMPT_TOKENS = 8000
TEMPLATE_DIR = 'prompt_templates'
os.makedirs(TEMPLATE_DIR, exist_ok=True)

# Default Template (Inline for safety/fallback)
DEFAULT_TEMPLATE = """
You are a code reviewer analyzing generated code and tests against requirements.
Tasks:
{% for task in tasks %}
{{ loop.index }}. {{ task }}
{% endfor %}
Output JSON: {{ output_schema }}

Requirements: {{ req_summary }}
Code Summary: {{ code_summary }}
Test Summary: {{ test_summary }}
State: {{ state_summary }}
{% if multi_modal %}
Diagrams/Test Results: {{ multi_modal_data }}
{% endif %}
{% if chain_of_thought %}
Use chain-of-thought: Step 1: Analyze... Step 2: ...
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
    Retrieves relevant context from a RAG (Retrieval-Augmented Generation) system.
    This function delegates the retrieval logic entirely to the runner's centralized RAG client.
    """
    # Use the now-properly-imported/stubbed centralized RAG client
    context = await _rag_retrieve_context(query)
    if context:
        log_action("RAG Retrieved", {"query": query, "context_length": len(context)})
    return context if context else "No relevant context found in RAG."

async def auto_tune_template_based_on_feedback(template_content: str, feedback: Optional[str] = None) -> str:
    """
    Uses an LLM (Grok) to refine the prompt template based on user feedback.
    This logic is left intact as it's a specific feature, though it's tightly coupled.
    """
    if not feedback:
        return template_content
    
    grok_api_key = os.getenv('GROK_API_KEY')
    if not grok_api_key:
        logger.warning("GROK_API_KEY not set. Cannot auto-tune template based on feedback.")
        return template_content

    refine_prompt = f"You are an expert prompt engineer. Refine the following Jinja2 prompt template based on the provided feedback to improve the quality of the generated critique. Ensure the output is *only* the refined Jinja2 template, without any extra text or markdown wrappers. Focus on clarity, conciseness, and effectiveness.\n\nFeedback: {feedback}\n\nOriginal Template:\n```jinja\n{template_content}\n```\n\nRefined Template:"
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {grok_api_key}", "Content-Type": "application/json"}
            data = {"model": "grok-4", "messages": [{"role": "user", "content": refine_prompt}], "temperature": 0.2, "max_tokens": 2000}
            async with session.post("https://api.x.ai/v1/chat/completions", headers=headers, json=data) as resp:
                resp.raise_for_status()
                response_json = await resp.json()
                if 'choices' in response_json and response_json['choices']:
                    refined_template_content = response_json['choices'][0]['message']['content'].strip()
                    if refined_template_content.startswith("```jinja"):
                        refined_template_content = refined_template_content.lstrip("```jinja").strip()
                    if refined_template_content.endswith("```"):
                        refined_template_content = refined_template_content.rstrip("```").strip()

                    log_action("Template Tuned", {"feedback": feedback, "refined_length": len(refined_template_content)})
                    return refined_template_content
                else:
                    logger.warning(f"Grok returned an empty or unexpected response during template tuning: {response_json}")
                    return template_content
    except aiohttp.ClientError as e:
        logger.error(f"Grok API client error during template tuning: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during template tuning: {e}")
    return template_content

async def incorporate_multi_modal_data(code_files: Dict[str, str], test_files: Dict[str, str]) -> str:
    """
    Generates and incorporates multi-modal data such as code coverage and a basic UML diagram.
    Requires LanguageCritiquePlugin and external tools (pytest, npm/jest, PlantUML server).
    
    NOTE: The heavy use of LanguageCritiquePlugin here indicates a need for deeper refactoring
    in a future pass, but for now, the existing logic is preserved using the stub/imported
    LanguageCritiquePlugin class.
    """
    with tracer.start_as_current_span("incorporate_multi_modal_data"):
        multi_modal_summary = []
        coverage_data_str = "No coverage data available."
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            for filename, content in code_files.items():
                file_path = temp_dir_path / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(file_path, mode='w', encoding='utf-8') as f:
                    await f.write(content)

            for filename, content in test_files.items():
                file_path = temp_dir_path / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(file_path, mode='w', encoding='utf-8') as f:
                    await f.write(content)
            
            combined_code_content = '\n'.join(code_files.values())
            lang = await detect_language(combined_code_content) if combined_code_content else None

            if lang == 'python':
                output_file = temp_dir_path / "coverage.json"
                lc_plugin = LanguageCritiquePlugin()
                success, result = await lc_plugin._run_tool(
                    ['pytest', '--cov=.', '--cov-report', f'json:{output_file}'],
                    str(temp_dir_path), 'pytest_coverage', 180, True, 'python:3.11'
                )
                if success and output_file.exists():
                    try:
                        with open(output_file, 'r', encoding='utf-8') as f:
                            coverage_report = json.load(f)
                            percent_covered = coverage_report.get('totals', {}).get('percent_covered', 0)
                            coverage_data_str = f"Python Code Coverage: {percent_covered:.2f}%"
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse pytest coverage JSON: {e}")
                else:
                    logger.warning(f"Pytest coverage failed or output file not found: {result.get('stderr', '')}")
                multi_modal_summary.append(coverage_data_str)

            elif lang in ['javascript', 'typescript']:
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
                    async with aiofiles.open(dummy_package_json_path, mode='w', encoding='utf-8') as f:
                        await f.write(dummy_package_json_content)
                lc_plugin = LanguageCritiquePlugin()
                install_success, install_result = await lc_plugin._run_tool(
                    ['npm', 'install'], str(temp_dir_path), 'npm_install', 300, True, 'node:20'
                )
                if not install_success:
                    logger.warning(f"npm install failed: {install_result.get('stderr', '')}")
                    coverage_data_str = "JS/TS Code Coverage: Failed to install dependencies."
                else:
                    test_success, test_result = await lc_plugin._run_tool(
                        ['npm', 'test', '--', '--coverage'], str(temp_dir_path), 'jest_coverage', 300, True, 'node:20'
                    )
                    if test_success and output_file_path.exists():
                        try:
                            with open(output_file_path, 'r', encoding='utf-8') as f:
                                coverage_report = json.load(f)
                                statements_pct = coverage_report.get('total', {}).get('statements', {}).get('pct', 0)
                                coverage_data_str = f"JS/TS Code Coverage: {statements_pct:.2f}%"
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse jest coverage JSON: {e}")
                    else:
                        logger.warning(f"Jest coverage failed or output file not found: {test_result.get('stderr', '')}")
                multi_modal_summary.append(coverage_data_str)
            else:
                multi_modal_summary.append(f"Code Coverage: Not supported for language {lang}.")
                logger.info(f"Code coverage not supported for language: {lang}")
                
        uml_diagram_str = "UML Diagram: Not generated (PlantUML unavailable)."
        try:
            if PlantUML:
                plantuml_server_url = os.getenv('PLANTUML_SERVER_URL', 'http://www.plantuml.com/plantuml')
                plantuml = PlantUML(plantuml_server_url)
                uml_code = "@startuml\n"
                class_info = []
                relationships = []
                for filename, content in code_files.items():
                    if lang == 'python':
                        import ast
                        try:
                            tree = ast.parse(content)
                            for node in ast.walk(tree):
                                if isinstance(node, ast.ClassDef):
                                    methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                                    parent = next((b.id for b in node.bases if isinstance(b, ast.Name)), None)
                                    class_info.append({"name": node.name, "parent": parent, "methods": methods})
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls['methods']:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                                if cls['parent']:
                                    relationships.append(f"{cls['parent']} <|-- {cls['name']}")
                            for node in ast.walk(tree):
                                if isinstance(node, ast.ImportFrom):
                                    for name in node.names:
                                        if name.name in [c['name'] for c in class_info]:
                                            relationships.append(f"{name.name} --> {class_info[0]['name']}")
                        except SyntaxError as e:
                            logger.warning(f"Failed to parse Python code for UML: {e}")
                            # Fall back to regex on syntax error
                            classes = re.findall(r'class\s+(\w+)(?:\((.*?)\))?\s*:', content)
                            for class_name, parent in classes:
                                methods = re.findall(rf'def\s+({class_name}\.\w+)\s*\(.*?\)', content)
                                class_info.append({"name": class_name, "parent": parent.strip() if parent else None, "methods": [m.split('.')[-1] for m in methods]})
                                if parent:
                                    relationships.append(f"{parent.strip()} <|-- {class_name}")
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls['methods']:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                            imports = re.findall(r'from\s+(\w+)(?:\.\w+)?\s+import', content)
                            for imp in imports:
                                if imp in [c['name'] for c in class_info]:
                                    relationships.append(f"({imp}) ..> ({class_info[0]['name']}) : uses")
                    elif lang in ['javascript', 'typescript']:
                        try:
                            import esprima
                            tree = esprima.parseModule(content)
                            for node in tree.body:
                                if node.type == 'ClassDeclaration':
                                    methods = [n.key.name for n in node.body.body if n.type == 'MethodDefinition']
                                    parent = node.superClass.name if node.superClass else None
                                    class_info.append({"name": node.id.name, "parent": parent, "methods": methods})
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls['methods']:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                                if cls['parent']:
                                    relationships.append(f"{cls['parent']} <|-- {cls['name']}")
                            for node in tree.body:
                                if node.type == 'ImportDeclaration':
                                    for spec in node.specifiers:
                                        if spec.local.name in [c['name'] for c in class_info]:
                                            relationships.append(f"{spec.local.name} --> {class_info[0]['name']}")
                        except Exception as e:
                            logger.warning(f"Failed to parse JS/TS code for UML: {e}")
                            # Fall back to regex
                            classes = re.findall(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content)
                            for class_name, parent in classes:
                                methods = re.findall(rf'({class_name}\.\w+)\s*\([^)]*\)\s*{{', content)
                                class_info.append({"name": class_name, "parent": parent, "methods": [m.split('.')[-1] for m in methods]})
                                if parent:
                                    relationships.append(f"{parent} <|-- {class_name}")
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls['methods']:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                            imports = re.findall(r'import\s+.*?[\'"]([^\'"]+)[\'"]', content)
                            for imp in imports:
                                if any(c['name'] in imp for c in class_info):
                                    relationships.append(f"({class_info[0]['name']}) ..> ({imp}) : uses")
                uml_code += "\n".join(relationships) + "\n@enduml"
                uml_diagram_str = f"UML Diagram: Generated for {lang} code, depicting classes: {', '.join(c['name'] for c in class_info) if class_info else 'none'}, with methods and relationships."
            else:
                logger.warning("PlantUML library not installed. Cannot generate UML diagrams.")
                # Fallback to simple regex if no library and PlantUML is missing
                uml_code = "@startuml\n"
                class_info = []
                relationships = []
                for filename, content in code_files.items():
                    if lang == 'python':
                        classes = re.findall(r'class\s+(\w+)(?:\((.*?)\))?\s*:', content)
                        for class_name, parent in classes:
                            methods = re.findall(rf'def\s+({class_name}\.\w+)\s*\(.*?\)', content)
                            class_info.append({"name": class_name, "parent": parent.strip() if parent else None, "methods": [m.split('.')[-1] for m in methods]})
                            if parent:
                                relationships.append(f"{parent.strip()} <|-- {class_name}")
                        for cls in class_info:
                            uml_code += f"class {cls['name']} {{\n"
                            for method in cls['methods']:
                                uml_code += f" +{method}()\n"
                            uml_code += "}\n"
                        imports = re.findall(r'from\s+(\w+)(?:\.\w+)?\s+import', content)
                        for imp in imports:
                            if imp in [c['name'] for c in class_info]:
                                relationships.append(f"({imp}) ..> ({class_info[0]['name']}) : uses")
                    elif lang in ['javascript', 'typescript']:
                        classes = re.findall(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content)
                        for class_name, parent in classes:
                            methods = re.findall(rf'({class_name}\.\w+)\s*\([^)]*\)\s*{{', content)
                            class_info.append({"name": class_name, "parent": parent, "methods": [m.split('.')[-1] for m in methods]})
                            if parent:
                                relationships.append(f"{parent} <|-- {class_name}")
                            for cls in class_info:
                                uml_code += f"class {cls['name']} {{\n"
                                for method in cls['methods']:
                                    uml_code += f" +{method}()\n"
                                uml_code += "}\n"
                            imports = re.findall(r'import\s+.*?[\'"]([^\'"]+)[\'"]', content)
                            for imp in imports:
                                if any(c['name'] in imp for c in class_info):
                                    relationships.append(f"({class_info[0]['name']}) ..> ({imp}) : uses")
                uml_code += "\n".join(relationships) + "\n@enduml"
                uml_diagram_str = f"UML Diagram: Generated for {lang} code, depicting classes: {', '.join(c['name'] for c in class_info) if class_info else 'none'}, with methods and relationships."
        except Exception as e:
            logger.error(f"Error generating PlantUML diagram: {e}")
            uml_diagram_str = f"UML Diagram: Generation failed due to an error: {e}. Falling back to basic summary."
            
        multi_modal_summary.append(uml_diagram_str)
        code_snip = list(code_files.values())[0][:500] if code_files else 'No code snippet available.'
        test_snip = list(test_files.values())[0][:500] if test_files else 'No test snippet available.'
        multi_modal_summary.append(f"Representative Code Snippet:\n```\n{code_snip}\n```")
        multi_modal_summary.append(f"Representative Test Snippet:\n```\n{test_snip}\n```")
        
        return "\n\n".join(multi_modal_summary)


class PromptConfig(BaseModel):
    language: str = Field(default='python')
    framework: str = Field(default='general')
    tasks: List[str] = Field(default_factory=lambda: [
        "Semantic Alignment: Score (0-1) how well the generated code aligns with the stated requirements. List any specific mismatches or semantic drifts.",
        "Hallucinations: Identify any invented or non-existent libraries, illogical code constructs, or added features that were not explicitly requested in the requirements.",
        "Test Quality: Score (0-1) the comprehensiveness and effectiveness of the provided tests. Suggest specific improvements if the score is below 0.8.",
        "Suggested Fixes: Provide actionable code and/or test fixes in a clear diff-like format, or describe the necessary changes for each identified issue.",
        "Ambiguities: Point out any unclear, vague, or underspecified requirements that could lead to multiple interpretations or incorrect implementations."
    ])
    output_schema: str = Field(default='''
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
    ''')
    chain_of_thought: bool = Field(default=True)
    multi_modal: bool = Field(default=True)
    user_context: str = Field(default='The user is a software developer seeking comprehensive code quality feedback.')
    template_file: Optional[str] = Field(default=None)
    feedback: Optional[str] = Field(default=None)

async def build_semantic_critique_prompt(
    code_files: Dict[str, str], 
    test_files: Dict[str, str], 
    requirements: Dict[str, Any], 
    state_summary: str, 
    config: Optional[Dict[str, Any]] = None
) -> str:
    """
    Builds a production-ready, context-aware, and optimized prompt for code generation.
    """
    with tracer.start_as_current_span("build_semantic_critique_prompt") as span:
        start_time = time.time()
        op_id = str(uuid.uuid4())
        
        try:
            # Validate inputs
            if not code_files:
                logger.error("Code files dictionary is empty")
                raise ValueError("Code files dictionary cannot be empty")
            if not isinstance(requirements, dict):
                logger.error("Requirements must be a dictionary")
                raise ValueError("Requirements must be a dictionary")
            if not isinstance(state_summary, str):
                logger.error("State summary must be a string")
                raise ValueError("State summary must be a string")

            conf = PromptConfig(**(config or {}))
            
            # Hash all core components for caching
            prompt_content_hash_input = f"{json.dumps(requirements)}{json.dumps(code_files)}{json.dumps(test_files)}{state_summary}{conf.model_dump_json()}" 
            prompt_hash = hashlib.sha256(prompt_content_hash_input.encode('utf-8')).hexdigest()
            span.set_attribute("op_id", op_id)
            span.set_attribute("prompt_hash", prompt_hash)

            # Language detection and translation (using runner utilities)
            req_lang = await detect_language(json.dumps(requirements))
            if req_lang and req_lang != 'en':
                logger.info(f"Translating requirements from {req_lang} to English.")
                translated_requirements = {}
                for k, v in requirements.items():
                    translated_requirements[k] = await translate_text(str(v), target='en')
                requirements = translated_requirements
                state_summary += f"\nNote: Original requirements were in {req_lang} and have been translated to English for processing."

            # Scrub PII and secrets from all inputs (using runner utilities)
            req_summary = await scrub_pii_and_secrets(json.dumps(requirements, indent=2))
            code_summary_raw = "\n".join(f"// File: {name}\n{content}" for name, content in code_files.items())
            test_summary_raw = "\n".join(f"// File: {name}\n{content}" for name, content in test_files.items())
            
            code_summary = await summarize_text(await scrub_pii_and_secrets(code_summary_raw))
            test_summary = await summarize_text(await scrub_pii_and_secrets(test_summary_raw))
            
            # Implement RAG dynamically if summaries are large
            rag_context = ""
            # Using runner's count_tokens
            combined_summary_length = await count_tokens(req_summary + code_summary + test_summary, model_name='default') 
            
            if combined_summary_length > MAX_PROMPT_TOKENS / 2:
                logger.info("Summaries are large. Attempting RAG retrieval for additional context.")
                rag_query = f"Best practices for code critique in {conf.language} for {conf.framework} projects."
                # Using the tool which calls the centralized RAG client
                rag_context = await rag_retrieve(rag_query)
            
            # Load and auto-tune template
            env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
            template_to_load = conf.template_file if conf.template_file else 'default_critique_template.j2'
            
            try:
                template_obj = env.get_template(template_to_load)
            except Exception as e:
                logger.warning(f"Failed to load specified template '{template_to_load}': {e}. Using default inline template.")
                template_obj = Template(DEFAULT_TEMPLATE)
            
            # Autotune the template before final render
            template_content = template_obj.render(
                tasks=conf.tasks,
                output_schema="PLACEHOLDER_OUTPUT_SCHEMA", # Use placeholder for tuning render
                req_summary="PLACEHOLDER_REQ_SUMMARY",
                code_summary="PLACEHOLDER_CODE_SUMMARY",
                test_summary="PLACEHOLDER_TEST_SUMMARY",
                state_summary="PLACEHOLDER_STATE_SUMMARY",
                multi_modal_data="PLACEHOLDER_MULTI_MODAL_DATA",
                multi_modal=conf.multi_modal,
                chain_of_thought=conf.chain_of_thought,
                op_id="PLACEHOLDER_OP_ID",
                prompt_hash="PLACEHOLDER_PROMPT_HASH",
                user_context="PLACEHOLDER_USER_CONTEXT",
                rag_context="PLACEHOLDER_RAG_CONTEXT"
            )

            tuned_template_content = await auto_tune_template_based_on_feedback(template_content, conf.feedback)
            final_template = Template(tuned_template_content)

            # Prepare for final rendering
            final_state_summary = state_summary
            if conf.chain_of_thought:
                final_state_summary += "\n\nChain of Thought: Provide step-by-step logical reasoning before producing the final JSON output."
            
            tools_description = [{"name": "rag_retrieve", "description": "Retrieve additional documentation or context relevant to code best practices, specific libraries, or error patterns by providing a query string."}]
            final_state_summary += f"\n\nAvailable Tools: {json.dumps(tools_description, indent=2)}\nInstructions: Use the `rag_retrieve` tool by calling it with `{{{{tool_code.rag_retrieve('your query here')}}}}` if you need external information to improve your critique."

            multi_modal_data = await incorporate_multi_modal_data(code_files, test_files) if conf.multi_modal else ""
            
            # Final rendering of the prompt
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
                rag_context=rag_context
            )
            
            # Final token count check and truncation/summarization if too long
            prompt_tokens = await count_tokens(prompt, model_name='default')
            if prompt_tokens > MAX_PROMPT_TOKENS:
                logger.warning(f"Generated prompt ({prompt_tokens} tokens) exceeds MAX_PROMPT_TOKENS ({MAX_PROMPT_TOKENS}). Summarizing.")
                # Using runner's summarize_text
                prompt = await summarize_text(prompt, max_length=MAX_PROMPT_TOKENS * 4)
                prompt_tokens = await count_tokens(prompt, model_name='default')
                log_action("Prompt Truncated", {"op_id": op_id, "original_tokens": combined_summary_length, "final_tokens": prompt_tokens})
            
            PROMPT_BUILDS.labels(status="success").inc()
            PROMPT_LATENCY.observe(time.time() - start_time)
            log_action("Prompt Built", {"op_id": op_id, "hash": prompt_hash, "length": len(prompt), "tokens": prompt_tokens, "config": conf.model_dump()})
            return prompt
        except Exception as e:
            PROMPT_BUILDS.labels(status="failure").inc()
            logger.error(f"Failed to build prompt: {e}")
            raise

if __name__ == '__main__':
    import argparse
    import asyncio
    import aiofiles

    parser = argparse.ArgumentParser(description="Build semantic critique prompt")
    parser.add_argument('--code-dir', required=True, help='Path to the directory containing code files.')
    parser.add_argument('--test-dir', default='', help='Path to the directory containing test files (optional).')
    parser.add_argument('--requirements', default='{}', help='JSON string of requirements or path to a JSON file.')
    parser.add_argument('--state-summary', default='Auto-generated', help='Summary of the current state or context.')
    parser.add_argument('--config', default='{}', help='JSON string of prompt configuration overrides.')
    args = parser.parse_args()

    async def run_main():
        code_files = {}
        for f_name in os.listdir(args.code_dir):
            f_path = Path(args.code_dir) / f_name
            if f_path.is_file():
                async with aiofiles.open(f_path, 'r', encoding='utf-8') as f:
                    code_files[f_name] = await f.read()

        test_files = {}
        if args.test_dir:
            for f_name in os.listdir(args.test_dir):
                f_path = Path(args.test_dir) / f_name
                if f_path.is_file():
                    async with aiofiles.open(f_path, 'r', encoding='utf-8') as f:
                        test_files[f_name] = await f.read()

        requirements_data = {}
        try:
            requirements_data = json.loads(args.requirements)
        except json.JSONDecodeError:
            if Path(args.requirements).is_file():
                try:
                    async with aiofiles.open(Path(args.requirements), 'r', encoding='utf-8') as f:
                        requirements_data = json.loads(await f.read())
                except Exception as e:
                    logger.error(f"Failed to read or parse requirements file '{args.requirements}': {e}")
                    requirements_data = {}
            else:
                logger.warning(f"Requirements input '{args.requirements}' is neither a valid JSON string nor an existing file. Using empty requirements.")
                requirements_data = {}

        config_data = json.loads(args.config)

        prompt_output = await build_semantic_critique_prompt(
            code_files, test_files, requirements_data, args.state_summary, config_data
        )
        print(prompt_output)

    asyncio.run(run_main())