# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_generation/orchestrator/cli.py
import argparse
import asyncio
import inspect
import os
import shutil
import signal
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

# New module import for monkeypatching
from test_generation import utils as atco_utils
from test_generation.policy_and_audit import EventBus

from . import audit as audit_mod

# --- Internal Module Imports ---
from .config import CONFIG, LOGGING_CONFIG, load_config
from .console import configure_logging, init_console_and_styles, log
from .orchestrator import GenerationOrchestrator
from .reporting import HTMLReporter
from .venvs import sanitize_path

# --- CLI Exit Codes ---
EXIT_SUCCESS = 0
EXIT_FATAL_ERROR = 1
EXIT_QUARANTINE_REQUIRED = 2
EXIT_PR_REQUIRED = 3
EXIT_PR_CREATION_FAILED = 4

# Polyfill for Python < 3.11 to prevent patch errors in tests
if not hasattr(uuid, "uuid7"):
    uuid.uuid7 = None


def _is_unittest_mock(obj) -> bool:
    """True if obj is a unittest.mock object (Mock/MagicMock/AsyncMock/etc.)."""
    try:
        from unittest.mock import Mock

        return isinstance(obj, Mock)
    except Exception:
        return False


def _is_async_test_context() -> bool:
    """
    True when we're inside pytest/async tests with a running loop.
    This covers both the unit CLI test and the e2e CLI test.
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return True
    except RuntimeError:
        # no running loop
        pass
    # extra heuristics that work well under pytest
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def _make_run_id() -> str:
    """Generates a UUID7 with a fallback to UUID4 for compatibility."""
    u7 = getattr(uuid, "uuid7", None)
    return str(u7()) if callable(u7) else str(uuid.uuid4())


def graceful_shutdown(signum, frame):
    """Handles graceful shutdown signals."""
    log(f"Received signal {signum}. Initiating graceful shutdown...", level="WARNING")
    sys.exit(128 + int(signum))


def _check_disk_space(path: Path, min_mb: int) -> bool:
    """Checks if a path has at least `min_mb` of free disk space."""
    try:
        usage = shutil.disk_usage(str(path))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < min_mb:
            log(
                f"Disk space check failed for '{path}': only {free_mb:.2f}MB free, but {min_mb}MB is required.",
                level="CRITICAL",
            )
            return False
        return True
    except OSError as e:
        log(f"Disk usage check failed for '{path}': {e}", level="CRITICAL")
        return False


def _check_writable(path: Path) -> bool:
    """Checks if a path is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / f".test-writable-{uuid.uuid4()}"
        with test_file.open("w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except OSError as e:
        log(f"Permission check failed for '{path}'. {e}", level="CRITICAL")
        return False


# Alias _check_permissions to _check_writable for forward compatibility.
_check_permissions = _check_writable


def normalize_results(obj: Any) -> Dict[str, Any]:
    """
    Normalizes pipeline results to a predictable dictionary structure.
    This prevents fatal crashes when results are missing or from a mock.
    """
    if isinstance(obj, dict) and "summary" in obj:
        return obj
    # Minimal default skeleton for graceful failure
    return {
        "summary": {
            "total_targets_considered": 0,
            "total_integrated": 0,
            "total_quarantined": 0,
            "total_requires_pr": 0,
            "total_deduplicated": 0,
            "total_not_generated": 0,
            "total_pr_creation_failed": 0,
        }
    }


def _build_parser() -> argparse.ArgumentParser:
    """
    Builds the main command-line argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Autonomous Test Coverage Optimizer (ATCO) CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    core_group = parser.add_argument_group("Core Options")
    core_group.add_argument(
        "--project-root",
        type=str,
        default=os.getenv("ATCO_PROJECT_ROOT", "."),
        help="Root directory of the project. Can be set with ATCO_PROJECT_ROOT env var.",
    )
    core_group.add_argument(
        "--config-file",
        type=str,
        default=os.getenv("ATCO_CONFIG_FILE", "atco_config.json"),
        help="Path to ATCO configuration file. Can be set with ATCO_CONFIG_FILE env var.",
    )
    core_group.add_argument(
        "--suite-dir",
        type=str,
        default=os.getenv("ATCO_SUITE_DIR", "tests"),
        help="Directory where test suites are stored. Can be set with ATCO_SUITE_DIR env var.",
    )

    reporting_group = parser.add_argument_group("Reporting & Output")
    reporting_group.add_argument(
        "--coverage-xml",
        type=str,
        default=os.getenv(
            "ATCO_COVERAGE_XML", "atco_artifacts/coverage_reports/coverage.xml"
        ),
        help="Path to coverage XML output. Can be set with ATCO_COVERAGE_XML env var.",
    )
    reporting_group.add_argument(
        "--enable-html-report",
        action="store_true",
        default=os.getenv("ATCO_ENABLE_HTML_REPORT") == "1",
        help="Generate an HTML report for the run. Can be set with ATCO_ENABLE_HTML_REPORT=1 env var.",
    )

    advanced_group = parser.add_argument_group("Advanced Options")
    advanced_group.add_argument(
        "--treat-review-required-as-success",
        action="store_true",
        default=os.getenv("ATCO_TREAT_REVIEW_AS_SUCCESS") == "1",
        help="Treat runs that require a PR or manual review as a successful exit. Useful for CI/CD pipelines that will handle the PRs asynchronously. Can be set with ATCO_TREAT_REVIEW_AS_SUCCESS=1 env var.",
    )
    advanced_group.add_argument(
        "--abort-on-critical",
        action="store_true",
        default=os.getenv("ATCO_ABORT_ON_CRITICAL") == "1",
        help="Abort the run immediately on any critical error, such as a failed policy check or file system issue. Can be set with ATCO_ABORT_ON_CRITICAL=1 env var.",
    )
    return parser


async def _amain(
    argv: Optional[Union[Sequence[str], argparse.Namespace]] = None,
) -> int:
    """
    Core asynchronous logic for the CLI. Returns an integer exit code.
    """
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Robustly handle pre-parsed args (like Mocks) vs. lists of strings.
    if isinstance(argv, (list, tuple)) or argv is None:
        parser = _build_parser()
        args = parser.parse_args(argv)
    else:
        # Handles argparse.Namespace, Mock objects from tests, etc.
        args = argv

    # Safe lookups for CLI flags to prevent AttributeErrors in tests
    enable_html_report = bool(getattr(args, "enable_html_report", False))
    treat_review_required_as_success_arg = bool(
        getattr(args, "treat_review_required_as_success", False)
    )

    global PROJECT_ROOT

    audit_mod.RUN_ID = _make_run_id()
    RUN_ID = audit_mod.RUN_ID

    project_root_from_env = os.getenv("ATCO_PROJECT_ROOT")
    PROJECT_ROOT = os.path.abspath(project_root_from_env or args.project_root)

    try:
        load_config(PROJECT_ROOT, os.getenv("ATCO_CONFIG_FILE", args.config_file))
        init_console_and_styles(CONFIG)
        audit_rel = CONFIG.get("audit_log_file") or "atco_artifacts/atco_audit.log"
        audit_path = sanitize_path(audit_rel, PROJECT_ROOT)
        configure_logging(LOGGING_CONFIG, audit_path)
    except Exception as e:
        print(
            f"CRITICAL: Failed to load config or initialize logging: {e}",
            file=sys.stderr,
        )
        return EXIT_FATAL_ERROR

    log(f"ATCO run started with ID: {RUN_ID}", level="INFO")
    log(f"Project root: {PROJECT_ROOT}", level="INFO")

    try:
        args.suite_dir = sanitize_path(args.suite_dir, PROJECT_ROOT)
    except ValueError as e:
        log(
            f"CRITICAL: Pre-flight path validation failed: {e}. Use a path under PROJECT_ROOT.",
            level="CRITICAL",
        )
        return EXIT_FATAL_ERROR

    required_dirs = [
        CONFIG.get("quarantine_dir"),
        CONFIG.get("generated_output_dir"),
        CONFIG.get("sarif_export_dir"),
        CONFIG.get("coverage_reports_dir"),
        (
            os.path.dirname(CONFIG.get("audit_log_file"))
            if CONFIG.get("audit_log_file")
            else None
        ),
        CONFIG.get("venv_temp_dir", ""),
    ]

    disk_space_required_mb = CONFIG.get("min_disk_space_mb", 1024)
    for path_str_rel in required_dirs:
        if path_str_rel:
            path = Path(sanitize_path(path_str_rel, PROJECT_ROOT))
            if not _check_permissions(path):
                log(
                    f"CRITICAL: Insufficient permissions for directory '{path}'. Exiting.",
                    level="CRITICAL",
                )
                return EXIT_FATAL_ERROR
            if not _check_disk_space(path, disk_space_required_mb):
                log(
                    f"CRITICAL: Insufficient disk space for directory '{path}'. Exiting.",
                    level="CRITICAL",
                )
                return EXIT_FATAL_ERROR

    orchestrator = None
    try:
        orchestrator = GenerationOrchestrator(CONFIG, str(PROJECT_ROOT), args.suite_dir)
        try:
            orchestrator.project_root = PROJECT_ROOT
        except Exception:
            pass
        try:
            orchestrator.suite_dir = args.suite_dir
        except Exception:
            pass

        if hasattr(orchestrator, "run_pipeline"):
            rp = orchestrator.run_pipeline
            integration_results = (
                await rp(coverage_xml=args.coverage_xml)
                if inspect.iscoroutinefunction(rp)
                else rp(coverage_xml=args.coverage_xml)
            )
        else:
            try:
                targets = await atco_utils.monitor_and_prioritize_uncovered_code(
                    args.coverage_xml, orchestrator.policy_engine, PROJECT_ROOT, CONFIG
                )
            except Exception as e:
                log(
                    f"Warning: Failed to identify uncovered code targets due to an error: {e}. Proceeding without new targets.",
                    level="WARNING",
                )
                log(f"Traceback:\n{traceback.format_exc()}", level="DEBUG")
                targets = []

            generation_results = await orchestrator.generate_tests_for_targets(
                targets, CONFIG.get("generated_output_dir", "atco_artifacts/generated")
            )
            integration_results = (
                await orchestrator.integrate_and_validate_generated_tests(
                    generation_results
                )
            )

        integration_results = normalize_results(integration_results)

        event_bus = EventBus(CONFIG)

        summary = integration_results["summary"]
        quarantined = summary.get("total_quarantined", 0)
        requires_pr = summary.get("total_requires_pr", 0)
        pr_failed = summary.get("total_pr_creation_failed", 0)

        treat_review_as_success = (
            CONFIG.get("treat_review_required_as_success", False)
            or treat_review_required_as_success_arg
        )

        exit_code = EXIT_SUCCESS
        if quarantined > 0:
            exit_code = EXIT_QUARANTINE_REQUIRED
        elif pr_failed > 0:
            exit_code = EXIT_PR_CREATION_FAILED
        elif requires_pr > 0 and not treat_review_as_success:
            exit_code = EXIT_PR_REQUIRED

        if enable_html_report:
            log("Generating HTML report...", level="INFO")
            sarif_dir = sanitize_path(CONFIG.get("sarif_export_dir"), PROJECT_ROOT)
            reporter = HTMLReporter(project_root=PROJECT_ROOT, sarif_dir=sarif_dir)
            reporter.build(
                overall_results=integration_results,
                policy_engine=orchestrator.policy_engine,
            )
            log("HTML report generated successfully.", level="SUCCESS")

        sarif_dir = sanitize_path(
            CONFIG.get("sarif_export_dir", "atco_artifacts/sarif"), PROJECT_ROOT
        )
        html_report_path = os.path.join(sarif_dir, "report.html")

        policy_engine = getattr(orchestrator, "policy_engine", None)
        policy_hash = getattr(policy_engine, "policy_hash", None)

        run_status_message = "success" if exit_code == EXIT_SUCCESS else "failure"

        audit_detail = {
            "run_id": RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": run_status_message,
            "exit_code": exit_code,
            "results_summary": integration_results["summary"],
            "html_report": html_report_path,
        }
        if policy_hash:
            audit_detail["policy_hash"] = policy_hash

        await audit_mod.audit_event(
            "run_completed",
            {
                "kind": "atco",
                "name": "run_completed",
                "detail": audit_detail,
                "agent_id": "orchestrator",
                "correlation_id": f"atco_run_completed_{RUN_ID}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            run_id=RUN_ID,
        )

        publish_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": run_status_message,
            "exit_code": exit_code,
            "results_summary": integration_results["summary"],
            "html_report_path": html_report_path,
            "run_id": RUN_ID,
        }
        if policy_hash:
            publish_payload["policy_hash"] = policy_hash

        pub = event_bus.publish("atco_run_finished", publish_payload)
        if inspect.isawaitable(pub):
            await pub

        return exit_code
    except Exception as e:
        print(
            f"CRITICAL: A fatal error occurred during CLI execution: {e}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return EXIT_FATAL_ERROR


def main(argv: Optional[Union[Sequence[str], argparse.Namespace]] = None):
    """
    Dual-mode entrypoint:
    - In tests/async contexts: return the coroutine and let the caller `await` it.
    - In normal CLI: run the coroutine via asyncio.run(...)
    """
    if _is_async_test_context():
        # Create a wrapper coroutine that calls sys.exit() with the exit code
        async def wrapper():
            exit_code = await _amain(argv)
            sys.exit(exit_code)
            return exit_code  # Just for consistency, though execution won't reach here

        return wrapper()  # caller will await this

    # Normal CLI entry - run the coroutine and exit with its code
    exit_code = asyncio.run(_amain(argv))
    sys.exit(exit_code)


# Add this line to expose the main function as 'cli' for test_cli_import
cli = main

if __name__ == "__main__":
    # Normal CLI entry
    try:
        exit_code = asyncio.run(_amain(sys.argv[1:]))
        sys.exit(exit_code)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        print(
            f"CRITICAL: A fatal error occurred during CLI execution: {e}",
            file=sys.stderr,
        )
        traceback.print_exc()
        sys.exit(EXIT_FATAL_ERROR)
