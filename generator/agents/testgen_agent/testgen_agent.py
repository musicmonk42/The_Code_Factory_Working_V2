"""
testgen_agent.py
The orchestrator for the test generation system.

REFACTORED: This agent now fully integrates with the central runner foundation.
It uses runner.llm_client, runner.runner_logging, and runner.runner_metrics,
and all V0/V1 dependencies like deploy_llm_call and audit_log have been removed.

Features:
- Fully observable, resilient, and parallelized agentic loop for test generation.
- Integration with external, strictly enforced modules:
  - runner.llm_client: Centralized LLM interaction.
  - testgen_prompt: For building context-rich, optimized prompts.
  - testgen_response_handler: For parsing and handling LLM responses.
  - testgen_validator: For comprehensive test quality validation.
  - runner.runner_logging: For critical event logging and provenance.
- Async file operations and security scrubbing with Presidio (strictly enforced).
- Granular error handling for LLM calls, validation, and file operations.
- Rich Markdown reporting with badges, PlantUML diagrams, and changelog.
- Token counting for cost-efficient LLM usage and metrics.
- Strict failure enforcement: No silent fallbacks or dummies for critical external components.
- Features: self-healing for LLM output parsing, ensemble mode, advanced routing, rate limiting.
- World-class error reporting via Sentry/External Integration.
- Comprehensive provenance tracking.
"""

import argparse
import asyncio
import hashlib
import importlib  # For CLI dependency checks
import json
import os
import subprocess  # For running external commands like git
import sys  # For sys.exit in CLI
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime  # For timestamps
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aiofiles  # For asynchronous file operations
import aiohttp  # For potential client errors from aiohttp

# Tokenizer: REQUIRED for token counting.
import tiktoken
from opentelemetry.trace import Status, StatusCode  # For OpenTelemetry tracing

# Presidio: REQUIRED for PII/secret scrubbing.
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from runner.llm_client import call_ensemble_api
from runner.runner_errors import LLMError

# --- CENTRAL RUNNER FOUNDATION ---
from runner.runner_logging import (  # FIX: Corrected import path to runner.runner_logging
    add_provenance,
    logger,
    tracer,
)

# --- FIX: Import AsyncRetrying ---
from tenacity import (  # For robust retries
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Test generation specific components: REQUIRED.
# FIXED: Changed to relative imports
from .testgen_prompt import build_agentic_prompt, initialize_codebase_for_rag
from .testgen_response_handler import parse_llm_response
from .testgen_validator import validate_test_quality

# -----------------------------------


# --- External Dependencies (Strictly Enforced) ---
# These imports are expected to be available and functional.
# If any of these fail to import (e.g., module not found), the program will
# terminate at this point, enforcing a strict hard-fail.


# Main LLM orchestration layer: REMOVED (Replaced by runner.llm_client)
# from deploy_llm_call import DeployLLMOrchestrator

# Audit logging: REMOVED (Replaced by runner.runner_logging)
# from audit_log import log_action


# PlantUML (Optional visual dependency for reports): Handled gracefully if not present.
try:
    from plantuml import PlantUML
except ImportError:
    PlantUML = None
    logger.warning("PlantUML library not found. Diagram generation in report will be skipped.")

# Optional: Sentry/External Error Reporting Client
try:
    import sentry_sdk

    # Initialize Sentry if DSN is provided in environment
    if os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            traces_sample_rate=1.0,  # Adjust sampling rate as needed
            profiles_sample_rate=1.0,
            environment=os.getenv("ENVIRONMENT", "development"),
            release=os.getenv("SERVICE_VERSION", "testgen-agent@1.0.0"),
            send_default_pii=False,  # Ensure PII is not sent by default
        )
        logger.info("Sentry SDK initialized.")
    else:
        logger.info("SENTRY_DSN not found. Sentry error reporting is disabled.")
except ImportError:
    logger.info(
        "sentry_sdk not found. External error reporting is disabled. `pip install sentry-sdk` to enable."
    )
    sentry_sdk = None

# REFACTORED: Local logger and tracer definitions removed, using imported runner versions.

# --- Prometheus Metrics ---
# REFACTORED: All local metric definitions have been removed.
# LLM metrics are handled by imported runner metrics.
# Agent-specific metrics (AGENT_RUNS_TOTAL, etc.) are replaced with structured logging.


# --- Security: Sensitive Data Scrubbing ---
def scrub_text(text: str) -> str:
    """
    Strictly redacts sensitive information from the text using Presidio.
    Raises RuntimeError if Presidio fails during scrubbing.
    """
    if not text:
        return ""

    try:
        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()

        entities = [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "US_SSN",
            "IP_ADDRESS",
            "URL",
            "NRP",
            "LOCATION",
            "API_KEY",
        ]

        results = analyzer.analyze(text=text, entities=entities, language="en")
        scrubbed_content = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            anonymizers={"DEFAULT": {"type": "replace", "new_value": "[REDACTED]"}},
        ).text

        return scrubbed_content

    except Exception as e:
        logger.error(f"Presidio PII/secret scrubbing failed critically: {e}", exc_info=True)
        raise RuntimeError(
            f"Critical error during sensitive data scrubbing with Presidio: {e}"
        ) from e


@dataclass
class Policy:
    """Configuration object for defining agent behavior and quality gates."""

    quality_threshold: float = 90.0
    primary_metric: str = "coverage_percentage"
    validation_suite: List[str] = field(
        default_factory=lambda: ["coverage", "mutation", "stress_performance"]
    )
    max_refinements: int = 3
    generation_llm_model: str = "gpt-4o"
    critique_llm_model: str = "claude-3-5-sonnet-20240620"
    refinement_llm_model: str = "gpt-4o"
    self_heal_llm_model: str = "gpt-4o"
    llm_retries: int = 3
    retry_wait_min: int = 1
    retry_wait_max: int = 10


def validate_policy(policy_dict: Dict[str, Any]) -> None:
    """
    Validates a policy dictionary against expected types and values.
    Raises ValueError for invalid configurations, providing detailed feedback.
    """
    required_fields = {
        "quality_threshold",
        "primary_metric",
        "validation_suite",
        "max_refinements",
        "generation_llm_model",
        "critique_llm_model",
        "refinement_llm_model",
        "self_heal_llm_model",
        "llm_retries",
        "retry_wait_min",
        "retry_wait_max",
    }
    missing = required_fields - set(policy_dict.keys())
    if missing:
        raise ValueError(f"Missing required policy fields: {', '.join(missing)}.")

    if not isinstance(policy_dict["quality_threshold"], (int, float)) or not (
        0 <= policy_dict["quality_threshold"] <= 100
    ):
        raise ValueError(
            f"Invalid quality_threshold '{policy_dict['quality_threshold']}'. Must be a number between 0 and 100."
        )
    if policy_dict["primary_metric"] not in [
        "coverage_percentage",
        "mutation_score",
        "stress_performance_score",
    ]:
        raise ValueError(
            f"Invalid primary_metric '{policy_dict['primary_metric']}'. Must be one of: 'coverage_percentage', 'mutation_score', 'stress_performance_score'."
        )
    if not isinstance(policy_dict["validation_suite"], list) or not all(
        isinstance(v, str) for v in policy_dict["validation_suite"]
    ):
        raise ValueError("validation_suite must be a list of strings.")
    if not isinstance(policy_dict["max_refinements"], int) or policy_dict["max_refinements"] < 0:
        raise ValueError(
            f"Invalid max_refinements '{policy_dict['max_refinements']}'. Must be a non-negative integer."
        )
    if not isinstance(policy_dict["llm_retries"], int) or policy_dict["llm_retries"] < 1:
        raise ValueError(
            f"Invalid llm_retries '{policy_dict['llm_retries']}'. Must be a positive integer."
        )
    if (
        not isinstance(policy_dict["retry_wait_min"], (int, float))
        or policy_dict["retry_wait_min"] <= 0
    ):
        raise ValueError(
            f"Invalid retry_wait_min '{policy_dict['retry_wait_min']}'. Must be a positive number."
        )
    if (
        not isinstance(policy_dict["retry_wait_max"], (int, float))
        or policy_dict["retry_wait_max"] < policy_dict["retry_wait_min"]
    ):
        raise ValueError(
            f"Invalid retry_wait_max '{policy_dict['retry_wait_max']}'. Must be greater than or equal to retry_wait_min."
        )
    if not all(
        isinstance(policy_dict[f], str) and policy_dict[f]
        for f in [
            "generation_llm_model",
            "critique_llm_model",
            "refinement_llm_model",
            "self_heal_llm_model",
        ]
    ):
        raise ValueError("All LLM model fields must be non-empty strings.")


class TestGenAgent:
    """
    An intelligent agent that orchestrates the test generation lifecycle.
    REFACTORED: Uses central runner components.
    """

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

        # REFACTORED: Removed self.llm_orchestrator
        logger.info(f"Initializing TestGenAgent for repository: {self.repo_path}")

        try:
            initialize_codebase_for_rag(str(self.repo_path))
            logger.info("Codebase initialized for RAG.")
        except Exception as e:
            logger.error(f"Failed to initialize codebase for RAG: {e}", exc_info=True)
            if sentry_sdk:
                sentry_sdk.capture_exception(e)
            raise RuntimeError(f"Codebase initialization for RAG failed critically: {e}")

    async def _load_code_files(self, target_files: List[str]) -> Dict[str, str]:
        """
        Asynchronously loads and scrubs content from target code files.
        """

        async def read_and_scrub_file(fp: str) -> Tuple[str, str]:
            full_path = self.repo_path / fp
            if not full_path.is_file():
                raise FileNotFoundError(f"Code file not found: {full_path}")

            try:
                async with aiofiles.open(full_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    return fp, scrub_text(content)
            except Exception as e:
                raise ValueError(f"Error reading or scrubbing file {full_path}: {e}") from e

        tasks = [read_and_scrub_file(fp) for fp in target_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        code_files_content = {}
        errors_found = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Failed to load a code file: {result}", exc_info=True)
                errors_found.append(str(result))
                if sentry_sdk:
                    sentry_sdk.capture_exception(result)
            else:
                code_files_content[result[0]] = result[1]

        if errors_found:
            raise ValueError(
                f"Critical error: Failed to load some code files. Details: {'; '.join(errors_found)}"
            )
        if not code_files_content and target_files:
            raise ValueError("No code files were successfully loaded from specified target_files.")

        return code_files_content

    async def _run_validation_suite(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        policy: Policy,
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Runs multiple validation types in parallel and aggregates the results.
        REFACTORED: Uses logger for error metrics.
        """
        with tracer.start_as_current_span("run_validation_suite") as span:
            span.set_attribute("validation.types", json.dumps(policy.validation_suite))
            log_extra = {"run_id": run_id, "language": language}

            semaphore = asyncio.Semaphore(3)

            async def limited_validate(v_type: str):
                async with semaphore:
                    try:
                        return await validate_test_quality(
                            code_files, test_files, language, validation_type=v_type
                        )
                    except Exception as e:
                        logger.error(
                            f"Validation type '{v_type}' failed: {e}",
                            exc_info=True,
                            extra=log_extra,
                        )
                        if sentry_sdk:
                            sentry_sdk.capture_exception(e)
                        raise RuntimeError(
                            f"Validation type '{v_type}' failed critically: {e}"
                        ) from e

            validation_tasks = [limited_validate(v_type) for v_type in policy.validation_suite]
            results = await asyncio.gather(*validation_tasks, return_exceptions=True)

            aggregated_report: Dict[str, Any] = {}
            for v_type, result in zip(policy.validation_suite, results):
                if isinstance(result, Exception):
                    aggregated_report[v_type] = {
                        "error": str(result),
                        "status": "failed_exception",
                    }
                    logger.error(
                        f"Agent run failed validation: ValidationException_{v_type}",
                        extra=log_extra,
                    )
                else:
                    aggregated_report[v_type] = result
                    if result.get("status") == "failed":
                        logger.warning(
                            f"Agent run validation reported failure: ValidationReportedFailed_{v_type}",
                            extra=log_extra,
                        )

            span.set_attribute("validation.results", json.dumps(aggregated_report))
            return aggregated_report

    async def _generate_report_markdown(
        self,
        history: List[Dict[str, Any]],
        language: str,
        final_status: str,
        run_id: str,
    ) -> str:
        """
        Generates a human-readable Markdown report from the agent's run history.
        REFACTORED: Uses logger for subprocess errors.
        """
        if not history:
            return "No run history was recorded to generate a report."

        log_extra = {"run_id": run_id, "language": language}
        report_parts = []

        badge_color = (
            "green"
            if final_status == "success"
            else "yellow" if final_status == "completed_below_threshold" else "red"
        )
        report_parts.append("# Test Generation Report\n\n")
        report_parts.append(
            f"![Status](https://img.shields.io/badge/status-{final_status.replace('_', '%20')}-{badge_color}.svg)\n"
        )
        report_parts.append(f"**Run ID:** `{run_id}`\n")
        report_parts.append(f"**Language:** `{language}`\n")
        report_parts.append(f"**Final Status:** `{final_status}`\n")
        report_parts.append(f"**Generated At:** `{datetime.now().isoformat()}`\n")
        report_parts.append(
            f"**Full Details:** [Audit Log for Run](https://your.audit.log.platform/run/{run_id})\n\n"
        )

        for i, step_data in enumerate(history):
            action = step_data.get("action", "Unknown Action")
            validation_report = step_data.get("validation_report", {})
            primary_metric_name = (
                history[0].get("policy", {}).get("primary_metric", "coverage_percentage")
            )
            # Safely navigate the nested dict structure
            coverage = (
                validation_report.get("coverage", {})
                .get("metrics", {})
                .get(primary_metric_name, "N/A")
            )

            report_parts.append(
                f"## Attempt {i+1} (Action: `{action}`, Primary Metric: {primary_metric_name.replace('_', ' ').title()}: {coverage}%)\n"
            )
            report_parts.append(f"- **Status:** `{step_data.get('status', 'N/A')}`\n")

            if action == "refinement" and "critique_response" in step_data:
                report_parts.append(
                    f"- **Critique:** \n```text\n{scrub_text(step_data['critique_response'].get('content', 'N/A'))}\n```\n"
                )

            report_parts.append("### Validation Results\n")
            for v_type, results in validation_report.items():
                report_parts.append(
                    f"- **{v_type.replace('_', ' ').title()}**: `{json.dumps(results, indent=2)}`\n"
                )
            report_parts.append("\n")

        final_report_data = history[-1]["validation_report"] if history else {}
        report_parts.append("## Final Validation Suite Results\n")
        for v_type, results in final_report_data.items():
            report_parts.append(
                f"- **{v_type.replace('_', ' ').title()}**: `{json.dumps(results, indent=2)}`\n"
            )

        if PlantUML:
            plantuml_server_url = os.getenv(
                "PLANTUML_SERVER_URL", "http://www.plantuml.com/plantuml"
            )
            plantuml_client = PlantUML(plantuml_server_url)

            diagram_uml_code = f"""
@startuml
skinparam handwritten true
title Test Generation Agent Workflow (Run ID: {run_id[:8]})
actor "Developer" as Dev
rectangle "TestGen Agent" as Agent {{
  state "Initialize" as Init
  state "Generate" as Gen
  state "Validate" as Validate
  state "Critique" as Critique
  state "Refine" as Refine
}}
rectangle "External Services" {{
  cloud "LLMs" as LLM
  database "Codebase RAG" as RAG
  artifact "Validation Tools" as Tools
}}
Dev --> Agent : Request Tests
Agent -> RAG : Initialize Codebase
Agent --> Gen : Initial Prompt
Gen --> LLM : Call LLM (Gen)
LLM --> Gen : Generated Tests
Gen --> Validate : Tests
Validate --> Tools : Run Checks
Tools --> Validate : Validation Report
alt Quality Met
  Validate --> Agent : Pass
else Max Refinements
  Validate --> Agent : Fail (Threshold Not Met)
else Issues Found
  Validate --> Critique : Report + Tests
  Critique --> LLM : Critique Prompt
  LLM --> Critique : Critique Response
  Critique --> Refine : Critique + Tests
  Refine --> LLM : Refinement Prompt
  LLM --> Refine : Refined Tests
  Refine --> Validate : Refined Tests
end
Agent --> Dev : Deliver Report
@enduml
"""
            try:
                diagram_url = plantuml_client.get_url(diagram_uml_code)
                report_parts.append(f"\n## Workflow Diagram\n![TestGen Workflow]({diagram_url})\n")
            except Exception as e:
                logger.warning(
                    f"Failed to generate PlantUML diagram: {e}. Skipping diagram.",
                    exc_info=True,
                    extra=log_extra,
                )
                report_parts.append(
                    "\n## Workflow Diagram\n_Diagram generation failed or PlantUML server unavailable._\n"
                )
        else:
            report_parts.append(
                "\n## Workflow Diagram\n_PlantUML library not available. Diagram generation skipped._\n"
            )

        try:
            proc = subprocess.run(
                ["git", "log", "-n", "5", "--pretty=format:%h %ad %s", "--no-merges"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            git_log_output = proc.stdout.strip()
            if git_log_output:
                report_parts.append(
                    f"\n## Recent Commits\n```\n{scrub_text(git_log_output)}\n```\n"
                )
            else:
                report_parts.append("\n## Recent Commits\n_No recent commits found._\n")
        except FileNotFoundError:
            logger.warning(
                "Git command not found. Cannot fetch changelog for report.",
                extra=log_extra,
            )
            report_parts.append("\n## Recent Commits\n_Git command not available._\n")
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"Git log failed (return code {e.returncode}): {e.stderr.strip()}",
                extra=log_extra,
            )
            report_parts.append(
                f"\n## Recent Commits\n_Failed to retrieve changelog (Error: {e.stderr.strip()})._\n"
            )
        except Exception as e:
            logger.warning(
                f"Unexpected error fetching git log: {e}",
                exc_info=True,
                extra=log_extra,
            )
            report_parts.append(
                "\n## Recent Commits\n_Failed to retrieve changelog due to an unexpected error._\n"
            )

        final_report_markdown = "\n".join(report_parts)
        return final_report_markdown

    # --- START FIX ---
    # REFACTORED: Uses central runner LLM client, metrics, and provenance
    # REMOVED the @retry decorator from the method signature
    async def _call_llm_with_retry(
        self,
        prompt: str,
        language: str,
        llm_model: str,
        run_id: str,
        purpose: str,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """
        Wrapper for LLM calls to the central runner with retries and metrics.
        Ensures all LLM interactions are consistent.
        """
        log_extra = {
            "run_id": run_id,
            "language": language,
            "purpose": purpose,
            "model": llm_model,
        }

        # Define the inner function that does the actual work
        async def _attempt_llm_call():
            logger.debug(f"Calling LLM for purpose: {purpose}", extra=log_extra)

            try:
                time.time()

                # REFACTORED: Rely entirely on runner.llm_client for retry, metrics, and tracing
                response = await call_ensemble_api(
                    prompt=prompt,
                    models=[{"model": llm_model}],  # call_ensemble_api expects a list
                    voting_strategy="majority",  # Default strategy
                    stream=stream,
                )

                # NOTE: All metric tracking is handled internally by call_ensemble_api
                # and reflected in the provenance log. We remove all manual increments.

                response_content = response.get("content", "")
                usage = response.get("usage", {})
                # Estimate tokens if not provided by the response
                tokenizer = tiktoken.get_encoding("cl100k_base")
                prompt_tokens = usage.get("input_tokens", len(tokenizer.encode(prompt)))
                completion_tokens = usage.get(
                    "output_tokens", len(tokenizer.encode(response_content))
                )

                # REFACTORED: Replace log_action with add_provenance
                provenance_data = {
                    "action": f"llm_call_{purpose}",
                    "run_id": run_id,
                    "prompt_summary": scrub_text(prompt[:500]),
                    "response_summary": scrub_text(response_content[:500]),
                    "model_used": llm_model,
                    "input_tokens": prompt_tokens,
                    "output_tokens": completion_tokens,
                    "cost_estimate": response.get("cost_usd", "N/A"),
                    "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "response_hash": hashlib.sha256(response_content.encode("utf-8")).hexdigest(),
                }
                add_provenance(provenance_data, **log_extra)

                return response
            except Exception as e:
                logger.error(
                    f"LLM call for '{purpose}' failed: {e}",
                    exc_info=True,
                    extra=log_extra,
                )
                # Re-raise a type of error that the retry policy will catch (defined outside this method)
                if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError, LLMError)):
                    raise  # These are already in the retry list for the external AsyncRetrying
                # Wrap other exceptions in RuntimeError
                raise RuntimeError(f"LLM call failed for '{purpose}' failed: {e}") from e

        # Define the retry strategy using self.policy (which is available here)
        # This correctly passes *numbers* to min and max.
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self.policy.llm_retries),
            wait=wait_exponential(
                multiplier=1,
                min=self.policy.retry_wait_min,
                max=self.policy.retry_wait_max,
            ),
            # The client errors are already retried by the *client*, but we need this retryer
            # here in case the client fails catastrophically or if we need to retry the *ensemble* call.
            retry=retry_if_exception_type(
                (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError, LLMError)
            ),
            reraise=True,  # Re-raise the last exception if all retries fail
        )

        # Call the inner function using the retryer
        return await retryer.call(_attempt_llm_call)

    # --- END FIX ---

    # REFACTORED: Main loop now uses runner logger and provenance
    async def generate_tests(
        self, target_files: List[str], language: str, policy: Policy
    ) -> Dict[str, Any]:
        """
        Orchestrates the generation, validation, critique, and refinement of tests.
        Returns a final report of the process.
        """
        # Store policy for use in _call_llm_with_retry
        self.policy = policy

        with tracer.start_as_current_span("generate_tests_agent_run") as span:
            run_id = f"testgen-run-{uuid.uuid4().hex[:8]}-{int(time.time())}"
            log_extra = {"run_id": run_id, "language": language}
            logger.info("Testgen agent run initiated", extra=log_extra)
            start_time = time.time()

            span.set_attributes(
                {
                    "run_id": run_id,
                    "language": language,
                    "policy.quality_threshold": policy.quality_threshold,
                    "policy.max_refinements": policy.max_refinements,
                    "policy.primary_metric": policy.primary_metric,
                    "policy.validation_suite": json.dumps(policy.validation_suite),
                }
            )

            history = []
            final_status = "failed"

            try:
                span.add_event("Loading code files.")
                code_files = await self._load_code_files(target_files)
                add_provenance(
                    {
                        "action": "code_files_loaded",
                        "run_id": run_id,
                        "files_count": len(code_files),
                        "file_names": list(code_files.keys()),
                    },
                    **log_extra,
                )

                best_tests: Dict[str, str] = {}
                best_validation_report: Dict[str, Any] = {}
                current_metric_value = 0.0

                for attempt in range(policy.max_refinements + 1):
                    span.set_attribute("agent.attempt", attempt)
                    logger.info(
                        f"Starting generation/refinement attempt {attempt + 1}/{policy.max_refinements + 1}.",
                        extra=log_extra,
                    )

                    generated_tests_this_attempt: Dict[str, str] = {}
                    llm_response_from_generation: Dict[str, Any] = {}
                    step_action = "initial_generation" if attempt == 0 else "refinement"
                    step_model = (
                        policy.generation_llm_model if attempt == 0 else policy.refinement_llm_model
                    )

                    if attempt == 0:
                        span.add_event("Building initial generation prompt.")
                        generation_prompt = build_agentic_prompt(
                            "generation", language=language, code_files=code_files
                        )
                        logger.info("Calling LLM for initial test generation.", extra=log_extra)
                        llm_response_from_generation = await self._call_llm_with_retry(
                            generation_prompt,
                            language,
                            policy.generation_llm_model,
                            run_id,
                            "generation",
                        )
                    else:
                        span.add_event(f"Building refinement prompt for attempt {attempt}.")
                        last_step_in_history = history[-1]
                        last_critique_content = last_step_in_history.get(
                            "critique_response", {}
                        ).get("content", "No specific critique provided.")
                        last_validation_report = last_step_in_history.get("validation_report", {})
                        last_generated_tests_content = last_step_in_history.get(
                            "generated_tests_content", {}
                        )

                        refinement_prompt = build_agentic_prompt(
                            "refinement",
                            language=language,
                            code_files=code_files,
                            generated_tests=last_generated_tests_content,
                            validation_feedback=json.dumps(last_validation_report),
                            critique=last_critique_content,
                        )
                        logger.info(
                            f"Calling LLM for refinement attempt {attempt}.",
                            extra=log_extra,
                        )
                        llm_response_from_generation = await self._call_llm_with_retry(
                            refinement_prompt,
                            language,
                            policy.refinement_llm_model,
                            run_id,
                            "refinement",
                        )

                    history.append(
                        {
                            "run_id": run_id,
                            "action": step_action,
                            "llm_model": step_model,
                            "status": "in_progress",
                            "policy": policy.__dict__,
                        }
                    )

                    try:
                        generated_tests_this_attempt = parse_llm_response(
                            llm_response_from_generation.get("content", ""),
                            language=language,
                        )
                        if not generated_tests_this_attempt:
                            raise ValueError("Parsed tests are empty after generation/refinement.")
                    except ValueError as e:
                        logger.warning(
                            f"Failed to parse LLM generated tests ({e}). Attempting self-healing.",
                            exc_info=True,
                            extra=log_extra,
                        )
                        span.add_event("Parse failure, attempting self-heal.")
                        self_heal_prompt = build_agentic_prompt(
                            "self_heal",
                            language=language,
                            generated_tests=llm_response_from_generation.get("content", ""),
                            error_message=str(e),
                        )

                        try:
                            healed_llm_response = await self._call_llm_with_retry(
                                self_heal_prompt,
                                language,
                                policy.self_heal_llm_model,
                                run_id,
                                "self_heal",
                            )
                            generated_tests_this_attempt = parse_llm_response(
                                healed_llm_response.get("content", ""),
                                language=language,
                            )
                            if not generated_tests_this_attempt:
                                raise ValueError("Parsed tests are still empty after self-healing.")
                            add_provenance(
                                {
                                    "action": "self_heal_success",
                                    "run_id": run_id,
                                    "original_error": str(e),
                                },
                                **log_extra,
                            )
                            logger.info("Self-healing successful.", extra=log_extra)
                            span.add_event("Self-healing successful.")
                        except Exception as he:
                            logger.error(
                                f"Self-healing failed: {he}",
                                exc_info=True,
                                extra=log_extra,
                            )
                            raise RuntimeError(
                                f"Failed to generate valid tests even after self-healing: {he}"
                            ) from he

                    history[-1]["generated_tests_content"] = generated_tests_this_attempt
                    history[-1]["llm_response_raw"] = llm_response_from_generation

                    span.add_event("Running validation suite.")
                    validation_report = await self._run_validation_suite(
                        code_files,
                        generated_tests_this_attempt,
                        language,
                        policy,
                        run_id,
                    )
                    add_provenance(
                        {
                            "action": f"validation_report_{attempt}",
                            "run_id": run_id,
                            "report": validation_report,
                        },
                        **log_extra,
                    )
                    history[-1]["validation_report"] = validation_report

                    # Safely access the nested primary metric
                    current_metric_value = 0.0
                    try:
                        current_metric_value = validation_report[
                            policy.primary_metric.split("_")[0]
                        ]["metrics"][policy.primary_metric]
                    except KeyError:
                        logger.warning(
                            f"Primary metric '{policy.primary_metric}' not found in validation report. Defaulting to 0.",
                            extra=log_extra,
                        )

                    logger.info(
                        f"Attempt {attempt + 1} validation completed. {policy.primary_metric}: {current_metric_value:.2f}%.",
                        extra=log_extra,
                    )

                    if current_metric_value >= policy.quality_threshold:
                        logger.info(
                            f"Quality threshold met ({policy.primary_metric}: {current_metric_value:.2f}% >= {policy.quality_threshold}%).",
                            extra=log_extra,
                        )
                        final_status = "success"
                        best_tests = generated_tests_this_attempt
                        best_validation_report = validation_report
                        history[-1]["status"] = "success"
                        break
                    else:
                        logger.info(
                            f"Quality threshold NOT met. Current {policy.primary_metric}: {current_metric_value:.2f}%.",
                            extra=log_extra,
                        )

                        best_metric_so_far = 0.0
                        try:
                            best_metric_so_far = best_validation_report[
                                policy.primary_metric.split("_")[0]
                            ]["metrics"][policy.primary_metric]
                        except KeyError:
                            best_metric_so_far = -1.0  # Ensure first valid report becomes the best

                        if current_metric_value > best_metric_so_far:
                            best_tests = generated_tests_this_attempt
                            best_validation_report = validation_report
                        history[-1]["status"] = "below_threshold"

                    if attempt >= policy.max_refinements:
                        logger.warning(
                            f"Max refinements ({policy.max_refinements}) reached. Final score: {current_metric_value:.2f}%.",
                            extra=log_extra,
                        )
                        final_status = "completed_below_threshold"
                        history[-1]["status"] = "completed_below_threshold"
                        break

                    span.add_event(f"Building critique prompt for attempt {attempt+1}.")
                    critique_prompt = build_agentic_prompt(
                        "critique",
                        language=language,
                        code_files=code_files,
                        generated_tests=generated_tests_this_attempt,
                        validation_feedback=json.dumps(validation_report),
                    )

                    logger.info(
                        f"Calling LLM for critique for attempt {attempt+1}.",
                        extra=log_extra,
                    )
                    critique_response = await self._call_llm_with_retry(
                        critique_prompt,
                        language,
                        policy.critique_llm_model,
                        run_id,
                        "critique",
                    )
                    history[-1]["critique_response"] = critique_response
                    add_provenance(
                        {
                            "action": f"critique_response_{attempt}",
                            "run_id": run_id,
                            "response_summary": scrub_text(
                                critique_response.get("content", "")[:200]
                            ),
                        },
                        **log_extra,
                    )

                else:
                    logger.warning(
                        "Loop completed without meeting threshold. Max refinements reached.",
                        extra=log_extra,
                    )
                    final_status = "completed_below_threshold"

                if not best_tests and generated_tests_this_attempt:
                    best_tests = generated_tests_this_attempt
                    best_validation_report = validation_report

                # Log final stats
                final_metric_value = 0.0
                try:
                    final_metric_value = best_validation_report[
                        policy.primary_metric.split("_")[0]
                    ]["metrics"][policy.primary_metric]
                except KeyError:
                    pass

                logger.info(
                    f"Agent run finished. Final status: {final_status}. Refinements: {attempt}. Final metric '{policy.primary_metric}': {final_metric_value}",
                    extra={
                        **log_extra,
                        "status": final_status,
                        "refinements": attempt,
                        "final_metric_value": final_metric_value,
                    },
                )

                explainability_report = await self._generate_report_markdown(
                    history, language, final_status, run_id
                )

                span.set_status(
                    Status(
                        StatusCode.OK,
                        f"Agent run completed with status: {final_status}.",
                    )
                )

                return {
                    "status": final_status,
                    "generated_tests": best_tests,
                    "final_validation_report": best_validation_report,
                    "explainability_report": explainability_report,
                    "duration_seconds": time.time() - start_time,
                    "run_id": run_id,
                    "history_summary": history,
                }

            except (FileNotFoundError, PermissionError) as e:
                logger.error(f"File operation error: {e}", exc_info=True, extra=log_extra)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                if sentry_sdk:
                    sentry_sdk.capture_exception(e)
                raise RuntimeError(f"Agent run failed due to file operation error: {e}") from e
            except ValueError as e:
                logger.error(
                    f"Configuration, validation, or parsing error: {e}",
                    exc_info=True,
                    extra=log_extra,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                if sentry_sdk:
                    sentry_sdk.capture_exception(e)
                raise RuntimeError(
                    f"Agent run failed due to configuration or parsing error: {e}"
                ) from e
            except RuntimeError as e:
                logger.error(
                    f"LLM operation error or critical validation suite failure: {e}",
                    exc_info=True,
                    extra=log_extra,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                if sentry_sdk:
                    sentry_sdk.capture_exception(e)
                raise RuntimeError(
                    f"Agent run failed due to LLM operation or critical validation failure: {e}"
                ) from e
            except Exception as e:
                logger.critical(
                    f"Unexpected critical error during agent execution: {e}",
                    exc_info=True,
                    extra=log_extra,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                if sentry_sdk:
                    sentry_sdk.capture_exception(e)
                raise RuntimeError(f"Agent run failed due to unexpected critical error: {e}") from e


async def main():
    """CLI entrypoint for the TestGenAgent."""
    # REFACTORED: Removed local logging config

    # REFACTORED: Updated dependency check list
    required_modules_for_cli = [
        "testgen_prompt",
        "testgen_response_handler",
        "testgen_validator",
        "presidio_analyzer",
        "presidio_anonymizer",
        "tiktoken",
        "aiohttp",
    ]
    for module_name in required_modules_for_cli:
        if module_name == "sentry_sdk" and not os.getenv("SENTRY_DSN"):
            continue
        if module_name == "plantuml" and PlantUML is None:
            continue

        try:
            importlib.import_module(module_name)
        except ImportError:
            logger.critical(
                f"Required module missing: {module_name}. Please ensure all external modules are installed and available in PYTHONPATH."
            )
            sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Run the TestGenAgent to generate tests for code files."
    )
    parser.add_argument(
        "target_files",
        nargs="+",
        help="One or more target code files to generate tests for.",
    )
    parser.add_argument(
        "--language",
        required=True,
        help="The programming language of the files (e.g., python, javascript).",
    )
    parser.add_argument("--repo-path", default=".", help="The root path of the code repository.")

    # Policy overrides
    parser.add_argument(
        "--quality-threshold",
        type=float,
        help="Override: The target quality threshold (e.g., coverage percentage).",
    )
    parser.add_argument(
        "--max-refinements",
        type=int,
        help="Override: Maximum number of refinement attempts.",
    )
    parser.add_argument("--primary-metric", help="Override: Primary metric for quality assessment.")
    parser.add_argument(
        "--validation_suite",
        nargs="+",
        help="Override: Space-separated list of validation types.",
    )
    parser.add_argument(
        "--generation_llm_model", help="Override: LLM model for initial generation."
    )
    parser.add_argument("--critique_llm_model", help="Override: LLM model for critique.")
    parser.add_argument("--refinement_llm_model", help="Override: LLM model for refinement.")
    parser.add_argument("--self_heal_llm_model", help="Override: LLM model for self-healing.")
    parser.add_argument(
        "--llm_retries", type=int, help="Override: Number of retries for LLM calls."
    )
    parser.add_argument(
        "--retry_wait_min", type=int, help="Override: Min wait time for LLM retries."
    )
    parser.add_argument(
        "--retry_wait_max", type=int, help="Override: Max wait time for LLM retries."
    )

    parser.add_argument(
        "--config",
        help="Path to a JSON config file for policy settings (CLI args override this).",
    )
    parser.add_argument("--output-file", help="Optional file path to save the JSON results.")

    args = parser.parse_args()

    policy_dict = {}
    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                policy_dict = json.load(f)
        except Exception as e:
            logger.critical(f"Failed to load config file {args.config}: {e}")
            sys.exit(1)

    # Apply command-line argument overrides
    cli_overrides = {
        "quality_threshold": args.quality_threshold,
        "max_refinements": args.max_refinements,
        "primary_metric": args.primary_metric,
        "validation_suite": args.validation_suite,
        "generation_llm_model": args.generation_llm_model,
        "critique_llm_model": args.critique_llm_model,
        "refinement_llm_model": args.refinement_llm_model,
        "self_heal_llm_model": args.self_heal_llm_model,
        "llm_retries": args.llm_retries,
        "retry_wait_min": args.retry_wait_min,
        "retry_wait_max": args.retry_wait_max,
    }
    # Filter out None values so they don't override file-based settings
    policy_dict.update({k: v for k, v in cli_overrides.items() if v is not None})

    try:
        # Use default Policy values if not in file or CLI
        # This requires manually checking against a default Policy instance
        Policy()
        for field_name, field_def in Policy.__dataclass_fields__.items():
            if field_name not in policy_dict:
                if field_def.default_factory is not field.MISSING:
                    policy_dict[field_name] = field_def.default
                else:
                    policy_dict[field_name] = field_def.default

        validate_policy(policy_dict)
        policy = Policy(**policy_dict)
    except Exception as e:
        logger.critical(
            f"Invalid policy configuration: {e}. Please correct your policy settings.",
            exc_info=True,
        )
        sys.exit(1)

    agent = TestGenAgent(repo_path=args.repo_path)

    result = await agent.generate_tests(
        target_files=args.target_files, language=args.language, policy=policy
    )

    print("\n--- Test Generation Run Complete ---")
    print(f"Status: {result['status']}")
    print("\n--- Explainability Report ---")
    print(result.get("explainability_report", "Not generated."))

    print("\n--- Full JSON Output ---")
    result_json = json.dumps(result, indent=2, default=str)
    print(result_json)

    if args.output_file:
        try:
            # REFACTORED: Using standard open() for CLI output, not aiofiles
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(result_json)
            print(f"\nResults saved to {args.output_file}")
        except Exception as e:
            logger.critical(f"Failed to save output to {args.output_file}: {e}")
            sys.exit(1)

    if result.get("status") in ["error", "completed_below_threshold", "failed"]:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    # REFACTORED: Removed local logging config

    # REFACTORED: Cleaned up dependency check list
    required_modules_for_cli = [
        "testgen_prompt",
        "testgen_response_handler",
        "testgen_validator",
        "presidio_analyzer",
        "presidio_anonymizer",
        "tiktoken",
        "aiohttp",
    ]
    for module_name in required_modules_for_cli:
        if module_name == "sentry_sdk" and not os.getenv("SENTRY_DSN"):
            continue
        if module_name == "plantuml" and PlantUML is None:
            continue

        try:
            importlib.import_module(module_name)
        except ImportError:
            logger.critical(
                f"Required module missing: {module_name}. Please ensure all external modules are installed and available in PYTHONPATH."
            )
            sys.exit(1)

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(
            f"CRITICAL: Unhandled critical error during CLI execution: {e}",
            file=sys.stderr,
        )
        if sentry_sdk:
            sentry_sdk.capture_exception(e)
            sentry_sdk.flush()
        sys.exit(1)
