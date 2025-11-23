# test_generation/orchestrator/orchestrator.py
import os
import shutil
import json
import sys
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import random
import logging
import traceback
import aiofiles
from collections import defaultdict
from pathlib import Path
from unittest.mock import Mock
import inspect
import uuid
from contextlib import asynccontextmanager

# --- Internal Module Imports ---
from test_generation.orchestrator.config import (
    AUDIT_LOG_FILE,
    QUARANTINE_DIR,
    SARIF_EXPORT_DIR,
    COVERAGE_REPORTS_DIR,
    _ensure_artifact_dirs,
)

# Import console utilities from same package for logging and UI.
from .console import (
    log,
    RICH_AVAILABLE,
    console,
    Progress,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TextColumn,
)

# Import venv utilities from same package for isolation.
try:
    from .venvs import (
        sanitize_path as venv_sanitize_path,
        temporary_env,
        create_and_install_venv,
    )
except ImportError as e:
    logging.getLogger(__name__).error(f"Venvs import failed: {e}. Using stubs.")

    # Basic fallback stubs to prevent crashing
    def venv_sanitize_path(path: str, root: str) -> str:
        return os.path.abspath(os.path.join(root, path))

    @asynccontextmanager
    async def temporary_env(*args, **kwargs):
        yield sys.executable

    async def create_and_install_venv(*args, **kwargs):
        return False, str(sys.exc_info()[1])


# Metrics from same package for monitoring.
try:
    from .metrics import (
        generation_duration,
        integration_success,
        integration_failure,
        METRICS_AVAILABLE,
    )
except ImportError:

    class DummyMetric:
        # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
        DEFAULT_BUCKETS = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.075,
            0.1,
            0.25,
            0.5,
            0.75,
            1.0,
            2.5,
            5.0,
            7.5,
            10.0,
            float("inf"),
        )

        def labels(self, **kwargs):
            return self

        def observe(self, *args):
            pass

        def inc(self):
            pass

        def time(self):
            return self  # Mock the context manager behavior

        def __enter__(self):
            pass  # Add __enter__ for context manager

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass  # Add __exit__ for context manager

    generation_duration = integration_success = integration_failure = DummyMetric()
    METRICS_AVAILABLE = False
    logging.getLogger(__name__).warning("Metrics import failed. Using dummy metrics.")

# Audit from same package for logging.
try:
    from .audit import audit_event, RUN_ID
except ImportError:
    # Fallback stubs
    async def _dummy_audit_event(*args, **kwargs):
        logging.getLogger(__name__).warning(
            f"Audit event '{args[0]}' was called but the audit module is not available."
        )

    audit_event = _dummy_audit_event
    RUN_ID = str(uuid.uuid4())

# Reporting from same package for SARIF outputs.
try:
    from .reporting import _write_sarif_atomically, cleanup_old_temp_files
except ImportError:
    logging.getLogger(__name__).warning(
        "Reporting import failed. Using dummy fallbacks."
    )

    async def _write_sarif_atomically(path, data):
        return True

    async def cleanup_old_temp_files(*args, **kwargs):
        pass


from test_generation.backends import BackendRegistry
from test_generation.policy_and_audit import PolicyEngine, EventBus, redact_sensitive
from test_generation import utils
from test_generation.utils import (
    SecurityScanner,
    KnowledgeGraphClient,
    PRCreator,
    MutationTester,
    CodeEnricher,
    add_atco_header,
    add_mocking_framework_import,
    llm_refine_test_plugin,
    cleanup_path_safe,
    run_jest_and_coverage,
    run_junit_and_coverage,
    compare_files as _compare_files,
    backup_existing_test as _backup_existing_test,
    generate_file_hash as _generate_file_hash,
)
from ..compliance_mapper import generate_report as generate_compliance_report
from .stubs import (
    DummyTestEnricher,
)


# --- Backwards-compatible test hook -----------------------------------------
# Some tests patch orchestrator.run_pytest_and_coverage, others patch
# test_generation.utils.run_pytest_and_coverage. This shim makes both work.
async def run_pytest_and_coverage(*args, **kwargs):
    return await utils.run_pytest_and_coverage(*args, **kwargs)


# Re-export so tests can monkeypatch this module path
# FIX: The original direct import of run_pytest_and_coverage is no longer needed
# and would cause an issue with the shim, so we remove it.
# The shim now provides this function to the module's scope.
compare_files = _compare_files
backup_existing_test = _backup_existing_test
generate_file_hash = _generate_file_hash


# Local audit log fallback: always append JSON lines so tests can assert on events
def _append_local_audit_log(project_root, config, event_name, detail):
    try:
        audit_rel = (config or {}).get(
            "audit_log_file"
        ) or "atco_artifacts/atco_audit.log"
        audit_path = Path(venv_sanitize_path(audit_rel, project_root))
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"event": event_name}
        if isinstance(detail, dict):
            record.update({k: v for k, v in detail.items() if k != "event"})
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log(
            f"Arbiter audit fallback file write failed for event '{event_name}': {e}",
            level="WARNING",
        )


async def _maybe_await(val):
    return await val if inspect.isawaitable(val) else val


async def _execute_test_command(
    language: str,
    project_root: str,
    test_path_relative: str,
    target_identifier: str,
    coverage_report_path: str,
    config: Dict[str, Any],
    venv_path: Optional[str],
) -> tuple[bool, float, str]:
    """Dispatches to the correct test runner based on language."""
    language = (language or "").lower()
    if language == "python":
        return await run_pytest_and_coverage(
            venv_path,
            test_path_relative,
            target_identifier,
            project_root,
            coverage_report_path,
            config,
        )
    elif language in ["javascript", "typescript"]:
        return await run_jest_and_coverage(
            project_root,
            test_path_relative,
            target_identifier,
            coverage_report_path,
            config,
        )
    elif language == "java":
        return await run_junit_and_coverage(
            project_root,
            test_path_relative,
            target_identifier,
            coverage_report_path,
            config,
        )
    else:

        return False, 0.0, f"No test runner configured for language: {language}"


# --- Custom Exception Types ---
class OrchestratorError(Exception):
    """Base exception for orchestrator-related errors."""

    pass


class InitializationError(OrchestratorError):
    """Raised when a core orchestrator component fails to initialize."""

    pass


class OrchestrationPipelineError(OrchestratorError):
    """Raised when a step in the pipeline fails critically."""

    pass


class GenerationOrchestrator:
    """
    Orchestrates the entire test generation and integration pipeline.
    """

    def __init__(self, config: Dict[str, Any], project_root: str, suite_dir: str):
        self.config = config
        self.project_root = project_root
        self.suite_dir = suite_dir
        self.generation_semaphore = asyncio.Semaphore(
            self.config.get("max_parallel_generation", 4)
        )
        self.language_semaphores = defaultdict(
            lambda: asyncio.Semaphore(
                self.config.get("demo_per_lang_concurrency", 2)
                if self.config.get("is_demo_mode")
                else self.config.get("per_lang_concurrency", 4)
            )
        )
        self.rich_progress = None
        self.progress_task_id = None
        self.console = console

        self.policy_engine = self._load_component("policy_engine", "policy_config")
        self.event_bus = self._load_component("event_bus", "event_bus_config")
        self.security_scanner = self._load_component(
            "security_scanner", "scanner_config"
        )
        self.knowledge_graph_client = self._load_component(
            "knowledge_graph_client", "knowledge_graph_config"
        )
        self.pr_creator = self._load_component("pr_creator", "pr_config")
        self.mutation_tester = self._load_component(
            "mutation_tester", "mutation_config"
        )
        self.test_enricher = self._load_test_enricher()

    async def run_pipeline(self, coverage_xml: str) -> dict:
        """Minimal wrapper used by tests: discover -> generate -> integrate."""
        # Note: The test will mock utils.monitor_and_prioritize_uncovered_code to return a list of targets.
        targets = await utils.monitor_and_prioritize_uncovered_code(
            coverage_xml, self.policy_engine, self.project_root, self.config
        )
        gen = await self.generate_tests_for_targets(targets, self.suite_dir)
        return await self.integrate_and_validate_generated_tests(gen)

    def _load_component(self, component_name: str, config_key: str, **kwargs) -> Any:
        """Loads a component based on configuration, with hardcoded fallbacks."""
        try:
            component_config = self.config.get(config_key, {})

            if component_name == "policy_engine":
                return PolicyEngine(component_config, project_root=self.project_root)
            elif component_name == "event_bus":
                return EventBus(config=component_config)
            elif component_name == "security_scanner":
                return SecurityScanner(
                    project_root=self.project_root, config=component_config
                )
            elif component_name == "knowledge_graph_client":
                return KnowledgeGraphClient(
                    project_root=self.project_root, config=component_config
                )
            elif component_name == "pr_creator":
                return PRCreator(
                    project_root=self.project_root, config=component_config
                )
            elif component_name == "mutation_tester":
                return MutationTester(
                    project_root=self.project_root, config=component_config
                )
            else:
                log(
                    f"Warning: Unknown component '{component_name}' requested in _load_component. Returning Mock.",
                    level="WARNING",
                )
                return Mock()
        except Exception as e:
            log(
                f"CRITICAL: Failed to initialize {component_name}: {e}. This is a critical dependency.",
                level="CRITICAL",
            )
            raise InitializationError(
                f"Failed to load {component_name}: {str(e)}"
            ) from e

    def _load_test_enricher(self):
        """Initializes the test enricher with configured plugins."""
        enrichment_plugins_list = []
        if self.config.get("enrichment_plugins", {}).get("header_enabled", True):
            enrichment_plugins_list.append(add_atco_header)
        if self.config.get("enrichment_plugins", {}).get(
            "mocking_import_enabled", True
        ):
            enrichment_plugins_list.append(add_mocking_framework_import)
        if self.config.get("enrichment_plugins", {}).get(
            "llm_refinement_enabled", False
        ):
            enrichment_plugins_list.append(llm_refine_test_plugin)
        try:
            return CodeEnricher(enrichment_plugins_list)
        except Exception as e:
            log(
                f"Warning: Failed to initialize CodeEnricher: {e}. Using a pass-through stub.",
                level="WARNING",
            )
            log(f"Initialization Traceback:\n{traceback.format_exc()}", level="DEBUG")
            return DummyTestEnricher()

    async def generate_tests_for_targets(
        self, targets: list[Dict[str, Any]], output_base_relative: str
    ) -> Dict[str, Dict]:
        """
        Generates tests for a list of target modules/files using appropriate backends.
        """
        summary = {}
        if not targets:
            log("No targets prioritized for test generation.", level="INFO")
            await _maybe_await(
                audit_event(
                    "pipeline_step",
                    {
                        "step": "generation",
                        "result": "skipped",
                        "reason": "no targets",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            return summary

        log(
            f"Starting test generation for {len(targets)} targets in parallel...",
            level="INFO",
        )
        await _maybe_await(
            audit_event(
                "pipeline_step",
                {
                    "step": "generation",
                    "result": "started",
                    "target_count": len(targets),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                run_id=RUN_ID,
            )
        )

        if RICH_AVAILABLE and console:
            self.rich_progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            )
            self.progress_task_id = self.rich_progress.add_task(
                "Generating tests...", total=len(targets)
            )
            self.rich_progress.start()

        retry_settings = {
            "generation_retries": self.config.get("max_gen_retries", 2),
            "retry_backoff_min": self.config.get("retry_backoff_min", 1),
            "retry_backoff_max": self.config.get("retry_backoff_max", 10),
        }

        async def _handle_single_test_generation(
            target_data: Dict[str, Any],
        ) -> tuple[str, bool, str, Any | None]:
            backend_class = BackendRegistry.get_backend(target_data["language"])
            if not backend_class:
                return (
                    target_data["identifier"],
                    False,
                    f"No backend registered for language {target_data['language']}",
                    None,
                )

            backend_instance = backend_class(self.config, self.project_root)
            timeout_map = self.config.get("backend_timeouts", {})
            timeout = timeout_map.get(target_data["language"], 60)

            for i in range(retry_settings["generation_retries"] + 1):
                with generation_duration.labels(
                    language=target_data["language"]
                ).time():
                    try:
                        sanitized_output_path = venv_sanitize_path(
                            output_base_relative, self.project_root
                        )
                        # Fix: Changed the order of parameters to `generate_tests` to match the expected signature.
                        # Also, ensure the return value from the mock in the test is what the function expects.
                        success, err, test_path_relative = (
                            await backend_instance.generate_tests(
                                target_data["identifier"],
                                sanitized_output_path,
                                params={
                                    "retry_count": i,
                                    "timeout": timeout,
                                    **target_data,
                                },
                            )
                        )
                    except Exception as e:
                        success, err, test_path_relative = (
                            False,
                            f"Backend failed with unhandled exception: {e}",
                            None,
                        )
                        log(
                            f"Backend failed for '{target_data['identifier']}': {e}",
                            level="ERROR",
                        )
                        await _maybe_await(
                            audit_event(
                                "backend_run_failure",
                                {
                                    "target": target_data["identifier"],
                                    "error": str(e),
                                    "traceback": traceback.format_exc(),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                run_id=RUN_ID,
                                critical=True,
                            )
                        )
                        continue

                if success and test_path_relative:
                    log(
                        f"Generated test for '{target_data['identifier']}' ({target_data['language']}) successfully.",
                        level="SUCCESS",
                    )
                    integration_success.labels(language=target_data["language"]).inc()
                    return target_data["identifier"], True, "", test_path_relative

                if i < retry_settings["generation_retries"]:
                    log(
                        f"Generation failed for '{target_data['identifier']}' ({target_data['language']}). Retrying ({i+1}/{retry_settings['generation_retries']}): {err}",
                        level="WARNING",
                    )
                    integration_failure.labels(language=target_data["language"]).inc()
                    await asyncio.sleep(
                        random.uniform(
                            retry_settings["retry_backoff_min"],
                            retry_settings["retry_backoff_max"],
                        )
                        * (i + 1)
                    )

            log(
                f"Test generation ultimately failed for '{target_data['identifier']}' ({target_data['language']}) after all retries.",
                level="ERROR",
            )
            integration_failure.labels(language=target_data["language"]).inc()
            return (
                target_data["identifier"],
                False,
                f"Failed after {retry_settings['generation_retries']+1} attempts: {err}",
                None,
            )

        async def _generate_with_semaphore(lang_sem, coro):
            """Gating with both the global and per-language semaphores."""
            async with self.generation_semaphore, lang_sem:
                return await coro

        tasks = []
        for target in targets:
            if not target.get("identifier") or not target.get("language"):
                ident = target.get("identifier") or "<unknown>"
                summary[ident] = {
                    "generation_success": False,
                    "generation_error": "Missing 'identifier' or 'language' in target",
                    "generated_test_path": None,
                    "language": target.get("language", "unknown"),
                }
                continue
            # normalize language once
            if "language" in target and isinstance(target["language"], str):
                target["language"] = target["language"].lower()
            lang_semaphore = self.language_semaphores[target["language"]]
            task = asyncio.create_task(
                _generate_with_semaphore(
                    lang_semaphore, _handle_single_test_generation(target)
                )
            )
            tasks.append(task)

        results = []
        try:
            for task in asyncio.as_completed(tasks):
                try:
                    res = await task
                    results.append(res)
                    if self.rich_progress:
                        self.rich_progress.update(self.progress_task_id, advance=1)
                except asyncio.CancelledError:
                    log("A generation task was cancelled.", level="WARNING")
                    continue
        except asyncio.CancelledError:
            log("Generation tasks cancelled due to graceful shutdown.", level="WARNING")
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self.rich_progress:
                try:
                    self.rich_progress.stop()
                except Exception:
                    pass
        for res in results:
            if isinstance(res, Exception):
                log(f"Critical error in generation task: {res}", level="CRITICAL")
                await _maybe_await(
                    audit_event(
                        "generation_task",
                        {
                            "step": "generation_task",
                            "result": "error",
                            "error": str(res),
                            "traceback": "".join(
                                traceback.format_exception(
                                    type(res), res, res.__traceback__
                                )
                            ),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                        critical=True,
                    )
                )
                continue

            module_identifier, success, err, generated_test_path_relative = res
            original_target = next(
                (t for t in targets if t["identifier"] == module_identifier), None
            )
            lang = original_target["language"] if original_target else "unknown"

            summary[module_identifier] = {
                "generation_success": success,
                "generation_error": err,
                "generated_test_path": generated_test_path_relative,
                "language": lang,
            }
            if not success:
                await _maybe_await(
                    audit_event(
                        "pipeline_step",
                        {
                            "step": "generation",
                            "result": "failure",
                            "target": module_identifier,
                            "error": err,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                    )
                )

        return summary

    async def integrate_and_validate_generated_tests(
        self, generation_summary: Dict[str, Dict]
    ) -> Dict[str, Any]:
        """
        Processes all generated tests: validates, integrates (or quarantines), and logs.
        """
        overall_results: Dict[str, Any] = {
            "summary": {
                "total_targets_considered": len(generation_summary),
                "total_integrated": 0,
                "total_quarantined": 0,
                "total_deduplicated": 0,
                "total_denied_by_policy": 0,
                "total_requires_pr": 0,
                "total_not_generated": 0,
                "total_pr_creation_failed": 0,
            },
            "details": {},
            "ai_metrics": {
                "refinement_success_rate_percent": 0.0,
                "total_refinement_attempts": 0,
                "total_generations": len(generation_summary),
            },
        }
        await _maybe_await(
            audit_event(
                "pipeline_step",
                {
                    "step": "integration_validation",
                    "result": "started",
                    "target_count": len(generation_summary),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                run_id=RUN_ID,
            )
        )

        try:
            # Atomic cleanup of temporary SARIF files from previous runs
            sarif_dir = venv_sanitize_path(
                self.config.get("sarif_export_dir", SARIF_EXPORT_DIR), self.project_root
            )
            await _maybe_await(cleanup_old_temp_files(sarif_dir))
            _ensure_artifact_dirs(self.project_root, self.config)

            # --- NEW: ensure audit log file exists for tests that assert its presence ---
            audit_rel = self.config.get("audit_log_file") or AUDIT_LOG_FILE
            try:
                audit_full = venv_sanitize_path(audit_rel, self.project_root)
                os.makedirs(os.path.dirname(audit_full), exist_ok=True)
                if not os.path.exists(audit_full):
                    with open(audit_full, "a", encoding="utf-8"):
                        pass
            except Exception as _e:
                # Non-fatal: the CLI sets up logging normally; we just need the file to exist here.
                log(
                    f"Warning: Could not initialize audit log file at '{audit_rel}': {_e}",
                    level="WARNING",
                )
            # ---------------------------------------------------------------------------

        except Exception as e:
            log(
                f"Failed to create required directories or cleanup temporary files: {e}",
                level="CRITICAL",
            )
            await _maybe_await(
                audit_event(
                    "dir_creation_failure",
                    {
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            if self.config.get("abort_on_critical", False):
                raise InitializationError(
                    "Failed to set up required directories for the pipeline."
                )
            else:
                raise InitializationError(
                    "Failed to set up required directories for the pipeline."
                )

        log("Starting integration and validation of generated tests...", level="INFO")

        integration_tasks = []
        for module_identifier, gen_data in generation_summary.items():
            if not gen_data.get("generation_success"):
                overall_results["details"][module_identifier] = {
                    "module_identifier": module_identifier,
                    "language": gen_data.get("language", "unknown"),
                    "integration_status": "NOT_GENERATED",
                    "reason": gen_data.get(
                        "generation_error", "Generated test file not found or empty."
                    ),
                    "test_passed": False,
                    "coverage_increase_percent": 0.0,
                    "security_issues_found": False,
                    "security_issues_list": [],
                    "security_max_severity": "NONE",
                    "backup_path": "",
                    "sarif_artifact_path": "",
                    "final_integrated_test_hash": "N/A",
                }
                overall_results["summary"]["total_not_generated"] += 1
                log(
                    f"Skipping integration for '{module_identifier}': Test not generated successfully or file missing.",
                    level="WARNING",
                )
                continue

            generated_test_path_relative = gen_data.get("generated_test_path")
            language = gen_data.get("language")

            # Evaluate PR policy early — before any venv/pytest work.
            try:
                needs_pr, pr_msg = await self.policy_engine.requires_pr_for_integration(
                    generated_test_path_relative
                )
            except Exception as e:
                needs_pr, pr_msg = False, f"Policy check failed: {e}"
            if needs_pr:
                overall_results["details"][module_identifier] = {
                    "integration_status": "REQUIRES_PR",
                    "reason": f"PR required: {pr_msg}",
                    "test_passed": False,
                    "coverage_increase_percent": 0.0,
                    "security_issues_found": False,
                    "security_issues_list": [],
                    "security_max_severity": "NONE",
                    "backup_path": "",
                    "sarif_artifact_path": "",
                    "final_integrated_test_hash": "N/A",
                    "staged_path": None,
                    "audit_event_id": str(uuid.uuid4()),
                    "quarantine_log_path": None,
                }
                overall_results["summary"]["total_requires_pr"] += 1
                continue

            task = self._handle_single_test_integration(
                generated_test_path_relative, module_identifier, language
            )
            integration_tasks.append(task)

        integration_results_list = await asyncio.gather(
            *integration_tasks, return_exceptions=True
        )
        errors = [e for e in integration_results_list if isinstance(e, Exception)]
        for e in errors:
            log(f"Integration task raised: {e}", level="ERROR")
            await _maybe_await(
                audit_event(
                    "integration_task_error",
                    {
                        "error": str(e),
                        "traceback": "".join(
                            traceback.format_exception(type(e), e, e.__traceback__)
                        ),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
        integration_results_list = [
            r for r in integration_results_list if not isinstance(r, Exception)
        ]

        total_refinement_attempts = getattr(
            self.test_enricher, "total_refinement_attempts", 0
        )
        refinement_success_count = getattr(
            self.test_enricher, "refinement_success_count", 0
        )
        refinement_success_rate = (
            (refinement_success_count / total_refinement_attempts) * 100
            if total_refinement_attempts > 0
            else 0.0
        )

        overall_results["ai_metrics"][
            "total_refinement_attempts"
        ] = total_refinement_attempts
        overall_results["ai_metrics"][
            "refinement_success_rate_percent"
        ] = refinement_success_rate

        for result in integration_results_list:
            if result:
                overall_results["details"][result["module_identifier"]] = result
                if result["integration_status"].startswith("INTEGRATED"):
                    overall_results["summary"]["total_integrated"] += 1
                elif result["integration_status"] == "QUARANTINED":
                    overall_results["summary"]["total_quarantined"] += 1
                elif result["integration_status"] == "DEDUPLICATED":
                    overall_results["summary"]["total_deduplicated"] += 1
                elif result["integration_status"] == "DENIED_BY_POLICY":
                    overall_results["summary"]["total_denied_by_policy"] += 1
                elif result["integration_status"] == "REQUIRES_PR":
                    overall_results["summary"]["total_requires_pr"] += 1
                elif result["integration_status"] == "PR_CREATION_FAILED":
                    overall_results["summary"]["total_pr_creation_failed"] += 1
                elif result["integration_status"] == "NOT_GENERATED":
                    pass

        await _maybe_await(
            audit_event(
                "integration_results",
                {
                    "total_integrated": overall_results["summary"]["total_integrated"],
                    "total_quarantined": overall_results["summary"][
                        "total_quarantined"
                    ],
                    "total_requires_pr": overall_results["summary"][
                        "total_requires_pr"
                    ],
                    "total_deduplicated": overall_results["summary"][
                        "total_deduplicated"
                    ],
                    "total_targets_considered": overall_results["summary"][
                        "total_targets_considered"
                    ],
                },
                run_id=RUN_ID,
            )
        )
        try:
            _append_local_audit_log(
                self.project_root,
                self.config,
                "integration_results",
                {
                    "total_integrated": overall_results["summary"].get(
                        "total_integrated", 0
                    ),
                    "total_quarantined": overall_results["summary"].get(
                        "total_quarantined", 0
                    ),
                    "total_requires_pr": overall_results["summary"].get(
                        "total_requires_pr", 0
                    ),
                    "total_deduplicated": overall_results["summary"].get(
                        "total_deduplicated", 0
                    ),
                    "total_targets_considered": overall_results["summary"].get(
                        "total_targets_considered", 0
                    ),
                },
            )
        except Exception:
            pass

        log("Integration and validation complete.", level="INFO")
        await _maybe_await(
            audit_event(
                "pipeline_step",
                {
                    "step": "integration_validation",
                    "result": "completed",
                    "summary": overall_results["summary"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                run_id=RUN_ID,
            )
        )

        if self.config.get("compliance_reporting", {}).get("enabled"):
            log("Generating compliance report...", level="INFO")
            # derive user_id and optional custom config path (if present in config)
            user_id = getattr(self.policy_engine, "user_id", None) or "unknown"
            comp_cfg = self.config.get("compliance_reporting") or {}
            custom_cfg = comp_cfg.get("custom_config_path") or comp_cfg.get(
                "custom_config"
            )

            # keep using the imported alias (if you have one), but pass the right kwargs
            await generate_compliance_report(
                self.project_root,
                user_id=user_id,
                custom_config=custom_cfg,
            )
        else:
            log("Compliance reporting is disabled.", level="INFO")

        return overall_results

    def _calculate_test_quality_score(
        self, test_passed: bool, coverage_increase: float, mutation_score: float
    ) -> float:
        """Calculates a composite test quality score."""
        score = (float(test_passed) * 0.5) + (coverage_increase / 100.0 * 0.25)
        if mutation_score != -1.0:
            score += mutation_score / 100.0 * 0.25
        return score

    async def _handle_single_test_integration(
        self,
        src_test_path_relative: str,
        target_identifier: str,
        language: str,
    ) -> Dict[str, Any]:
        """
        Handles validation and integration for a single generated test file.

        This method now manages the isolated environment for all languages.
        """
        # normalize language defensively
        language = (language or "").lower()
        result_summary = {
            "module_identifier": target_identifier,
            "language": language,
            "test_path": src_test_path_relative,
            "integration_status": "SKIPPED",
            "reason": "Unknown",
            "test_passed": False,
            "coverage_increase_percent": 0.0,
            "mutation_score_percent": -1.0,
            "security_issues_found": False,
            "security_issues_list": [],
            "security_max_severity": "NONE",
            "backup_path": "",
            "sarif_artifact_path": "",
            "final_integrated_test_hash": "N/A",
            "staged_path": None,
            "audit_event_id": str(uuid.uuid4()),
            "quarantine_log_path": None,
        }

        try:
            full_src_test_path = venv_sanitize_path(
                src_test_path_relative, self.project_root
            )
        except ValueError as e:
            result_summary["integration_status"] = "QUARANTINED"
            result_summary["reason"] = f"Pre-flight path validation failed: {e}"
            log(
                f"Failed to validate path for {target_identifier}: {e}",
                level="CRITICAL",
            )
            await _maybe_await(
                audit_event(
                    "test_integration_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            if self.config.get("abort_on_critical", False):
                raise
            return result_summary

        missing_or_empty = False
        try:
            if (
                not os.path.exists(full_src_test_path)
                or os.path.getsize(full_src_test_path) == 0
            ):
                missing_or_empty = True
        except OSError:
            missing_or_empty = True

        if missing_or_empty:
            result_summary["integration_status"] = "QUARANTINED"
            result_summary["reason"] = "Generated test file not found or empty."
            await _maybe_await(
                audit_event(
                    "test_quarantined",
                    {
                        "module": target_identifier,
                        "reason": result_summary["reason"],
                        "action": "file_missing",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            try:
                _append_local_audit_log(
                    self.project_root,
                    self.config,
                    "test_quarantined",
                    {
                        "module": target_identifier,
                        "reason": "Generated test file not found or empty.",
                        "action": "file_missing",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass
            return result_summary

        log(
            f"Enriching test file: {os.path.basename(full_src_test_path)}", level="INFO"
        )
        original_test_content = ""
        try:
            async with aiofiles.open(
                full_src_test_path, "r", encoding="utf-8", errors="ignore"
            ) as f:
                original_test_content = await f.read()
            enriched_test_content = await _maybe_await(
                self.test_enricher.enrich_test(
                    original_test_content, language, self.project_root
                )
            )
            if enriched_test_content != original_test_content:
                async with aiofiles.open(
                    full_src_test_path, "w", encoding="utf-8"
                ) as f:
                    await f.write(enriched_test_content)
                log(
                    f"Test file '{os.path.basename(full_src_test_path)}' enriched.",
                    level="INFO",
                )
            else:
                log(
                    f"Test file '{os.path.basename(full_src_test_path)}' not modified by enrichers.",
                    level="INFO",
                )
            await _maybe_await(
                audit_event(
                    "test_enrichment",
                    {
                        "module": target_identifier,
                        "action": "enrichment_completed",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
        except Exception as e:
            log(
                f"Error enriching test {os.path.basename(full_src_test_path)}: {e}",
                level="ERROR",
            )
            log(f"Enrichment Traceback:\n{traceback.format_exc()}", level="ERROR")
            await _maybe_await(
                audit_event(
                    "test_enrichment_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            pass

        coverage_report_filename = f"coverage_{os.path.basename(src_test_path_relative).replace('.py','').replace('.js','').replace('.java','')}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{random.randint(0,9999)}.xml"
        if language in ["javascript", "typescript"]:
            coverage_report_filename = f"coverage_{os.path.basename(src_test_path_relative).replace('.py','').replace('.js','').replace('.java','')}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{random.randint(0,9999)}.json"

        temp_coverage_report_path_relative = os.path.join(
            COVERAGE_REPORTS_DIR, coverage_report_filename
        )
        full_temp_coverage_report_path = venv_sanitize_path(
            temp_coverage_report_path_relative, self.project_root
        )
        os.makedirs(os.path.dirname(full_temp_coverage_report_path), exist_ok=True)

        test_passed, coverage_increase, exec_log = (
            False,
            0.0,
            f"No test runner for language: {language}",
        )

        # A) Treat venv failure as non-fatal and keep going (fallback to sys.executable)
        import sys

        ok, py = await create_and_install_venv(
            self.project_root, self.config.get("python_venv_deps", ["pytest"])
        )
        if not ok or not py:
            # Do NOT quarantine here—fall back to system Python and proceed.
            self.console.warning(
                f"Venv creation failed ({py}). Falling back to system interpreter."
            )
            py = sys.executable

        try:
            # FIX: Call the shim function defined earlier.
            test_passed, coverage_increase, exec_log = await _execute_test_command(
                language,
                self.project_root,
                src_test_path_relative,
                target_identifier,
                full_temp_coverage_report_path,
                self.config,
                py,
            )
        except Exception as e:
            log(
                f"Exception during test execution for {target_identifier}: {e}",
                level="ERROR",
            )
            exec_log = f"Exception during execution: {e}"
            test_passed = False
            coverage_increase = 0.0
            await _maybe_await(
                audit_event(
                    "test_execution_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )

        result_summary["test_passed"] = test_passed
        result_summary["coverage_increase_percent"] = coverage_increase
        result_summary["reason"] = exec_log

        mutation_success, mutation_score, mutation_log = False, -1.0, ""
        if self.config.get("mutation_testing", {}).get("enabled"):
            try:
                mutation_success, mutation_score, mutation_log = await _maybe_await(
                    self.mutation_tester.run_mutations(
                        target_identifier, src_test_path_relative, language
                    )
                )
            except Exception as e:
                log(
                    f"Exception during mutation testing for {target_identifier}: {e}",
                    level="ERROR",
                )
                mutation_log = f"Exception during mutation testing: {e}"
                mutation_score = -1.0
                mutation_success = False
                await _maybe_await(
                    audit_event(
                        "mutation_test_failure",
                        {
                            "module": target_identifier,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                        critical=True,
                    )
                )

        result_summary["mutation_score_percent"] = mutation_score
        if not mutation_success and self.config.get("mutation_testing", {}).get(
            "enabled"
        ):
            log(
                f"Mutation testing failed for {target_identifier}: {mutation_log}",
                level="WARNING",
            )

        has_security_issues, security_issues_list, security_max_severity = (
            await _maybe_await(
                self.security_scanner.scan_test_file(src_test_path_relative, language)
            )
        )
        result_summary["security_issues_found"] = has_security_issues
        result_summary["security_issues_list"] = security_issues_list
        result_summary["security_max_severity"] = security_max_severity
        if has_security_issues:
            try:
                truncated_issues_list = redact_sensitive(
                    security_issues_list or [], max_items=10
                )
            except TypeError:
                truncated_issues_list = redact_sensitive(security_issues_list or [])
                if len(truncated_issues_list) > 10:
                    truncated_issues_list = truncated_issues_list[:10] + ["..."]

            await _maybe_await(
                audit_event(
                    "security_scan_issues",
                    {
                        "module": target_identifier,
                        "issues": truncated_issues_list,
                        "severity": security_max_severity,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            await _maybe_await(
                self.event_bus.publish(
                    "security_alert",
                    {
                        "module": target_identifier,
                        "issues": security_issues_list,
                        "severity": security_max_severity,
                    },
                )
            )

        integrate_allowed = False
        requires_pr = False
        policy_reason_integrate = "Policy Engine unavailable"
        policy_reason_pr = "Policy Engine unavailable"

        test_quality_score = self._calculate_test_quality_score(
            test_passed, coverage_increase, mutation_score
        )

        try:
            fn = self.policy_engine.should_integrate_test
            res = fn(
                target_identifier,
                test_quality_score,
                language,
                has_security_issues,
                security_max_severity,
            )
            integrate_allowed, policy_reason_integrate = await _maybe_await(res)

            fn = self.policy_engine.requires_pr_for_integration
            res = fn(target_identifier, language, test_quality_score)
            requires_pr, policy_reason_pr = await _maybe_await(res)
        except Exception as e:
            log(
                f"Error during policy enforcement for {target_identifier}: {e}",
                level="CRITICAL",
            )
            integrate_allowed = False
            policy_reason_integrate = f"Policy engine failed: {e}"
            requires_pr = False
            await _maybe_await(
                audit_event(
                    "policy_check_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            if self.config.get("abort_on_critical", False):
                raise OrchestrationPipelineError(
                    f"Policy engine failed for {target_identifier}"
                ) from e

        min_coverage_gain = self.config.get("policy", {}).get(
            "min_coverage_gain_for_integration", 1.0
        )
        min_mutation_score = self.config.get("mutation_testing", {}).get(
            "min_score_for_integration", 50.0
        )

        # FIX: Implement a guard to quarantine the test if the mutation score is too low.
        if (
            not integrate_allowed
            or not test_passed
            or coverage_increase < min_coverage_gain
            or has_security_issues
            or (mutation_score != -1.0 and mutation_score < min_mutation_score)
        ):
            final_quarantine_reason = ""
            if not integrate_allowed:
                final_quarantine_reason = (
                    f"Policy denied integration: {policy_reason_integrate}"
                )
            elif not test_passed:
                final_quarantine_reason = "Test failed during execution."
            elif coverage_increase < min_coverage_gain:
                final_quarantine_reason = f"Test generated insufficient coverage ({coverage_increase:.2f}%). Minimum required is {min_coverage_gain}%."
            elif has_security_issues:
                final_quarantine_reason = f"Security scan found issues ({security_max_severity}). Full details in quarantine log file."
            elif mutation_score != -1.0 and mutation_score < min_mutation_score:
                final_quarantine_reason = f"Mutation score ({mutation_score:.1f}%) below minimum required ({min_mutation_score}%)."

            result_summary["integration_status"] = "QUARANTINED"
            result_summary["reason"] = (
                final_quarantine_reason or "Test did not meet integration criteria."
            )

            base = os.path.basename(full_src_test_path)
            now = datetime.now(timezone.utc)
            stamp = now.strftime("%Y%m%d%H%M%S")
            quarantine_name = f"{os.path.splitext(base)[0]}_{stamp}_{RUN_ID}{os.path.splitext(base)[1]}"
            quarantine_dst_path = venv_sanitize_path(
                os.path.join(QUARANTINE_DIR, quarantine_name), self.project_root
            )

            try:
                shutil.move(full_src_test_path, quarantine_dst_path)
            except Exception as e:
                log(
                    f"Error moving file to quarantine for {target_identifier}: {e}. File may be lost.",
                    level="CRITICAL",
                )
                await _maybe_await(
                    audit_event(
                        "test_quarantined_failure",
                        {
                            "module": target_identifier,
                            "reason": "move_error",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                        critical=True,
                    )
                )
                if self.config.get("abort_on_critical", False):
                    raise

            quarantine_log_path = Path(str(quarantine_dst_path) + ".log")
            try:
                async with aiofiles.open(
                    quarantine_log_path, "w", encoding="utf-8"
                ) as f:
                    await f.write(
                        f"--- ATCO Quarantine Log for {target_identifier} ---\n"
                    )
                    await f.write(
                        f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
                    )
                    await f.write(
                        f"Reason for Quarantine: {result_summary['reason']}\n\n"
                    )
                    await f.write("--- Execution Log ---\n")
                    await f.write(exec_log or "No execution log available.\n")
                    await f.write("\n--- Security Scan Issues (Full List) ---\n")
                    if security_issues_list:
                        await f.write(json.dumps(security_issues_list, indent=2))
                    else:
                        await f.write("No security issues found.\n")
                result_summary["quarantine_log_path"] = str(quarantine_log_path)
                log(
                    f"Full logs for quarantined test saved to: {quarantine_log_path}",
                    level="INFO",
                )
            except Exception as e:
                log(
                    f"Failed to write quarantine log file for {target_identifier}: {e}",
                    level="WARNING",
                )
                await _maybe_await(
                    audit_event(
                        "quarantine_log_failure",
                        {
                            "module": target_identifier,
                            "error": str(e),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                    )
                )

            res = cleanup_path_safe(full_temp_coverage_report_path)
            if inspect.isawaitable(res):
                await res

            log(
                f"Test for '{target_identifier}' quarantined: {result_summary['reason']}",
                level="WARNING",
            )

            await _maybe_await(
                audit_event(
                    "test_quarantined",
                    {
                        "module": target_identifier,
                        "reason": result_summary["reason"],
                        "action": "validation_failure",
                        "quarantine_log": result_summary["quarantine_log_path"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            try:
                _append_local_audit_log(
                    self.project_root,
                    self.config,
                    "test_quarantined",
                    {
                        "module": target_identifier,
                        "reason": result_summary["reason"],
                        "action": "validation_failure",
                        "quarantine_log": result_summary["quarantine_log_path"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass
            await _maybe_await(
                self.event_bus.publish(
                    "test_quarantined",
                    {"module": target_identifier, "reason": result_summary["reason"]},
                )
            )

            if self.config.get("jira_integration", {}).get("enabled"):
                try:
                    res = self.pr_creator.create_jira_ticket(
                        title=f"ATCO: Review Quarantined Test for {target_identifier} ({language}) [Run: {RUN_ID}]",
                        description=f"ATCO quarantined a generated test for {target_identifier} because: {result_summary['reason']}\n\n"
                        f"Original Test Path: {src_test_path_relative}\nQuarantined Path: {quarantine_dst_path}\n"
                        f"Coverage Gain: {result_summary['coverage_increase_percent']:.1f}%, Test Passed: {result_summary['test_passed']}\n"
                        f"Security Issues: {result_summary['security_max_severity']}, Mutation Score: {result_summary['mutation_score_percent']:.1f}%\n\n"
                        f"Full logs for this event can be found at: {result_summary['quarantine_log_path']}",
                    )
                    issue_success, issue_url = await _maybe_await(res)
                    if issue_success:
                        log(
                            f"Jira ticket created for quarantined test: {issue_url}",
                            level="INFO",
                        )
                    else:
                        log(
                            "Failed to create Jira ticket for quarantined test.",
                            level="ERROR",
                        )
                except Exception as e:
                    log(f"Error creating Jira ticket: {e}", level="ERROR")
                    await _maybe_await(
                        audit_event(
                            "jira_ticket_creation_failure",
                            {
                                "module": target_identifier,
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            run_id=RUN_ID,
                            critical=True,
                        )
                    )
                    if self.config.get("abort_on_critical", False):
                        raise

            return result_summary

        elif requires_pr:
            result_summary["integration_status"] = "REQUIRES_PR"
            result_summary["reason"] = policy_reason_pr
            pr_stg_path_dir_full = venv_sanitize_path(
                os.path.join(QUARANTINE_DIR, "for_pr"), self.project_root
            )
            os.makedirs(pr_stg_path_dir_full, exist_ok=True)
            staged_path = venv_sanitize_path(
                os.path.join(
                    pr_stg_path_dir_full, os.path.basename(full_src_test_path)
                ),
                self.project_root,
            )
            try:
                shutil.move(full_src_test_path, staged_path)
                result_summary["staged_path"] = staged_path
            except Exception as e:
                log(
                    f"Error moving file to PR staging for {target_identifier}: {e}",
                    level="CRITICAL",
                )
                await _maybe_await(
                    audit_event(
                        "test_pr_staging_failure",
                        {
                            "module": target_identifier,
                            "reason": "move_error",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                        critical=True,
                    )
                )
                if self.config.get("abort_on_critical", False):
                    raise

            res = cleanup_path_safe(full_temp_coverage_report_path)
            if inspect.isawaitable(res):
                await res

            log(
                f"Test for '{target_identifier}' requires PR: {result_summary['reason']}. Staged for PR.",
                level="INFO",
            )

            await _maybe_await(
                audit_event(
                    "test_staged_for_pr",
                    {
                        "module": target_identifier,
                        "reason": result_summary["reason"],
                        "staged_path": staged_path,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            await _maybe_await(
                self.event_bus.publish(
                    "test_requires_pr",
                    {"module": target_identifier, "reason": result_summary["reason"]},
                )
            )

            if self.config.get("pr_integration", {}).get("enabled"):
                try:
                    pr_branch_name = f"atco/add-tests-{target_identifier.replace('.', '-').replace(os.sep, '-')}-{RUN_ID}"
                    pr_title = f"ATCO: New tests for {target_identifier} ({language}) [Run: {RUN_ID}]"
                    pr_description = (
                        f"Automated tests generated by ATCO for module/file `{target_identifier}` in `{language}`. "
                        f"Policy requires human review: {policy_reason_pr}\n\n"
                        f"Test Quality Score: {test_quality_score:.2f}, Coverage Gain: {coverage_increase:.2f}%, "
                        f"Mutation Score: {mutation_score:.1f}%\n"
                        f"Security Issues: {result_summary['security_max_severity']}"
                    )
                    res = self.pr_creator.create_or_update_pr(
                        branch_name=pr_branch_name,
                        title=pr_title,
                        description=pr_description,
                        files_to_add=[staged_path],
                    )
                    pr_success, pr_url = await _maybe_await(res)
                    if pr_success:
                        log(f"PR created or updated: {pr_url}", level="SUCCESS")
                    else:
                        log(
                            "Failed to create or update PR. Please review staged test manually.",
                            level="ERROR",
                        )
                        result_summary["integration_status"] = "PR_CREATION_FAILED"
                        await _maybe_await(
                            audit_event(
                                "pr_creation_failure",
                                {
                                    "module": target_identifier,
                                    "error": "Failed to create PR",
                                    "staged_path": staged_path,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                run_id=RUN_ID,
                                critical=True,
                            )
                        )
                        if self.config.get("abort_on_critical", False):
                            raise
                except Exception as e:
                    log(
                        f"Error creating/updating PR for {target_identifier}: {e}",
                        level="ERROR",
                    )
                    result_summary["integration_status"] = "PR_CREATION_FAILED"
                    await _maybe_await(
                        audit_event(
                            "pr_creation_failure",
                            {
                                "module": target_identifier,
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            run_id=RUN_ID,
                            critical=True,
                        )
                    )
                    if self.config.get("abort_on_critical", False):
                        raise
            return result_summary

        dst_test_path_relative = os.path.join(
            self.suite_dir, os.path.basename(src_test_path_relative)
        )
        full_dst_test_path = venv_sanitize_path(
            dst_test_path_relative, self.project_root
        )

        try:
            if os.path.isfile(full_dst_test_path):
                if compare_files(
                    venv_sanitize_path(src_test_path_relative, self.project_root),
                    full_dst_test_path,
                ):
                    result_summary["integration_status"] = "DEDUPLICATED"
                    result_summary["reason"] = "Identical test already exists in suite."
                    # Keep the generated test artifact for possible re-runs/quarantine.
                    res = cleanup_path_safe(full_src_test_path)
                    if inspect.isawaitable(res):
                        await res
                    log(
                        f"Deduplicated test for '{target_identifier}' (skipped copy).",
                        level="INFO",
                    )
                else:
                    result_summary["backup_path"] = await _maybe_await(
                        backup_existing_test(dst_test_path_relative, self.project_root)
                    )
                    shutil.copyfile(full_src_test_path, full_dst_test_path)
                    result_summary["integration_status"] = "INTEGRATED_WITH_BACKUP"
                    result_summary["reason"] = (
                        "New test integrated, original backed up."
                    )
                    log(f"Integrated new test for '{target_identifier}'.", level="INFO")
            else:
                shutil.copyfile(full_src_test_path, full_dst_test_path)
                result_summary["integration_status"] = "INTEGRATED"
                result_summary["reason"] = "Test integrated successfully."
                log(f"Integrated new test for '{target_identifier}'.", level="INFO")
        except Exception as e:
            log(
                f"Error during file integration for {target_identifier}: {e}",
                level="CRITICAL",
            )
            result_summary["integration_status"] = "QUARANTINED"
            result_summary["reason"] = f"Failed to integrate test file: {e}"
            try:
                shutil.move(
                    full_src_test_path,
                    venv_sanitize_path(
                        os.path.join(
                            QUARANTINE_DIR, os.path.basename(full_src_test_path)
                        ),
                        self.project_root,
                    ),
                )
            except Exception as move_e:
                log(
                    f"Critical error: Could not move failed test to quarantine for {target_identifier}. File may be lost: {move_e}",
                    level="CRITICAL",
                )
                await _maybe_await(
                    audit_event(
                        "test_integration_failure",
                        {
                            "module": target_identifier,
                            "error": str(move_e),
                            "traceback": traceback.format_exc(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        run_id=RUN_ID,
                        critical=True,
                    )
                )
                if self.config.get("abort_on_critical", False):
                    raise
            await _maybe_await(
                audit_event(
                    "test_integration_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            if self.config.get("abort_on_critical", False):
                raise

        # Keep the generated test artifact for possible re-runs/quarantine.
        res = cleanup_path_safe(full_temp_coverage_report_path)
        if inspect.isawaitable(res):
            await res

        integrated_test_hash = generate_file_hash(full_dst_test_path, self.project_root)
        result_summary["final_integrated_test_hash"] = integrated_test_hash

        policy_hash = getattr(self.policy_engine, "policy_hash", "unknown")

        sarif_data = {
            "tool": "ATCO",
            "atco_version": "3.0",
            "run_id": RUN_ID,
            "generated_by_backend": language,
            "target_identifier": target_identifier,
            "language": language,
            "integration_timestamp": datetime.now(timezone.utc).isoformat(),
            "coverage_gain_percent": result_summary["coverage_increase_percent"],
            "test_passed_exec": result_summary["test_passed"],
            "mutation_score_percent": result_summary["mutation_score_percent"],
            "policy_evaluation": {
                "integration_allowed": integrate_allowed,
                "reason": policy_reason_integrate,
                "requires_pr_by_policy": requires_pr,
            },
            "security_scan_results": {
                "issues_found": result_summary["security_issues_found"],
                "issues_list": redact_sensitive(security_issues_list or []),
                "max_severity": result_summary["security_max_severity"],
            },
            "integrated_test_file_hash_sha256": integrated_test_hash,
            "integrated_test_path_relative": dst_test_path_relative,
            "backup_path_relative": (
                result_summary["backup_path"] if result_summary["backup_path"] else None
            ),
            "atco_policy_hash_at_run": policy_hash,
            "audit_event_id": result_summary["audit_event_id"],
        }
        sarif_file_name = f"atco_sarif_{os.path.basename(dst_test_path_relative).replace('.py', '').replace('.js', '').replace('.java', '')}_{RUN_ID}.json"
        sarif_path_relative = os.path.join(SARIF_EXPORT_DIR, sarif_file_name)
        full_sarif_path = Path(
            venv_sanitize_path(sarif_path_relative, self.project_root)
        )
        os.makedirs(full_sarif_path.parent, exist_ok=True)

        if await _maybe_await(_write_sarif_atomically(full_sarif_path, sarif_data)):
            result_summary["sarif_artifact_path"] = sarif_path_relative
            log(f"SARIF artifact exported to: {sarif_path_relative}", level="INFO")
            await _maybe_await(
                audit_event(
                    "sarif_exported",
                    {
                        "module": target_identifier,
                        "sarif_path": sarif_path_relative,
                        "test_hash": integrated_test_hash,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                )
            )
            try:
                _append_local_audit_log(
                    self.project_root,
                    self.config,
                    "sarif_exported",
                    {
                        "module": target_identifier,
                        "sarif_path": sarif_path_relative,
                        "test_hash": integrated_test_hash,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass
        else:
            result_summary["sarif_artifact_path"] = (
                "Failed to export: could not write file"
            )
            await _maybe_await(
                audit_event(
                    "sarif_export_failure",
                    {
                        "module": target_identifier,
                        "error": "Failed to write SARIF file atomically.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            if self.config.get("abort_on_critical", False):
                raise

        try:
            await _maybe_await(
                self.knowledge_graph_client.update_module_metrics(
                    target_identifier,
                    {
                        "last_test_generated_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "last_coverage_gain_percent": result_summary[
                            "coverage_increase_percent"
                        ],
                        "generation_backend": language,
                        "integration_status": result_summary["integration_status"],
                        "integrated_test_hash": integrated_test_hash,
                        "security_issues": result_summary["security_issues_found"],
                        "security_max_severity": result_summary[
                            "security_max_severity"
                        ],
                        "mutation_score": result_summary["mutation_score_percent"],
                        "test_passed": result_summary["test_passed"],
                        "policy_compliant": integrate_allowed,
                        "requires_pr": requires_pr,
                    },
                )
            )
        except Exception as e:
            log(
                f"Error updating Knowledge Graph for {target_identifier}: {e}",
                level="CRITICAL",
            )
            await _maybe_await(
                audit_event(
                    "knowledge_graph_update_failure",
                    {
                        "module": target_identifier,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    run_id=RUN_ID,
                    critical=True,
                )
            )
            pass

        await _maybe_await(
            audit_event(
                "test_integrated",
                {
                    "kind": "atco",
                    "name": "test_integrated",
                    "detail": {
                        "run_id": RUN_ID,
                        "module": target_identifier,
                        "status": result_summary["integration_status"],
                        "coverage_increase": result_summary[
                            "coverage_increase_percent"
                        ],
                        "sarif_path": result_summary["sarif_artifact_path"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    "agent_id": "orchestrator",
                    "correlation_id": f"atco-{target_identifier}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                run_id=RUN_ID,
            )
        )
        try:
            _append_local_audit_log(
                self.project_root,
                self.config,
                "test_integrated",
                {
                    "run_id": RUN_ID,
                    "module": target_identifier,
                    "status": result_summary.get("integration_status"),
                    "coverage_increase": result_summary.get(
                        "coverage_increase_percent"
                    ),
                    "sarif_path": result_summary.get("sarif_artifact_path"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            pass
        return result_summary
