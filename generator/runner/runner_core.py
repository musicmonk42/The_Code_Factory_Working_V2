# runner/core.py
# Core logic for the runner system.
# Provides robust, extensible, and observable test execution,
# with strict contract enforcement and structured error handling.

import asyncio
import concurrent.futures
import contextlib  # [NEW] Added for shutdown
import hashlib
import heapq
import json
import os
import shutil
import tempfile
import time
import traceback
import uuid  # Explicitly import uuid for clarity
from abc import ABC
from collections import defaultdict, deque  # Import defaultdict
from contextlib import asynccontextmanager  # [NEW] Added for lifespan
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiohttp
import opentelemetry.trace as trace  # Explicitly import trace for consistency

# [CHANGE A] Add direct import for runtime patching
# FIX: Use relative import to avoid circular import when runner alias isn't set up yet
from generator.runner import runner_parsers
from opentelemetry.trace import (
    Status,
    StatusCode,
)  # Explicitly import Status and StatusCode
from runner.runner_backends import BACKEND_REGISTRY as ALL_BACKENDS

# Import project-specific modules
from runner.runner_config import RunnerConfig, load_config
from runner.runner_contracts import (
    BatchTaskPayload,
    TaskPayload,
    TaskResult,
)  # NEW: Contracts

# --- FIX: Import 'ExecutionError' and alias it to 'TestExecutionError' ---
from runner.runner_errors import BackendError, DistributedError
from runner.runner_errors import ExecutionError as TestExecutionError
from runner.runner_errors import (
    FrameworkError,
    ParsingError,
    RunnerError,
    SetupError,
    TimeoutError,
    error_codes,
)
from runner.runner_logging import log_audit_event, logger
from runner.runner_metrics import *  # Ensure all metrics are imported explicitly

# Gold Standard: Import parser output schemas for strong typing
from runner.runner_parsers import (
    CoverageReportSchema,
    TestReportSchema,
    parse_behave_junit,
    parse_coverage_xml,
    parse_go_coverprofile,
    parse_go_test_json,
    parse_istanbul_json,
    parse_jacoco_xml,
    parse_jest_json,
    parse_junit_xml,
    parse_robot_xml,
    parse_surefire_xml,
    parse_unittest_output,
)
from runner.runner_security_utils import redact_secrets

# --- REFACTOR FIX: Import subprocess_wrapper from process_utils ---

# --- END REFACTOR FIX ---


# Optional imports for mutation/fuzzing
try:
    from runner.runner_mutation import detect_language as _detect_mutation_lang
    from runner.runner_mutation import fuzz_test as _fuzz_test_func
    from runner.runner_mutation import mutation_test as _mutation_test_func

    HAS_MUTATION_MODULE = True
except ImportError:
    HAS_MUTATION_MODULE = False
    logger.warning(
        "runner/mutation.py not found or dependencies missing. Mutation and Fuzz testing features will be unavailable."
    )

try:
    import hypothesis
    import hypothesis.strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False
    logger.warning(
        "Hypothesis not installed. Property-based testing and Hypothesis-based fuzzing will be unavailable."
    )

# --- Constants for metrics labels ---
RUNNER_LABEL_BACKEND = "backend"
RUNNER_LABEL_FRAMEWORK = "framework"
RUNNER_LABEL_INSTANCE_ID = "instance_id"

# --- Constants for queue processing ---
DEFAULT_QUEUE_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_CONCURRENT_TASKS = 3

# --- Queue persistence configuration ---
# The queue file path can be configured via environment variable for containerized environments
# Falls back to the generator directory if not specified
DEFAULT_QUEUE_FILE_NAME = "runner_queue.json"

def _get_queue_file_path() -> Path:
    """
    Get the path to the queue persistence file.
    
    Supports configuration via:
    1. RUNNER_QUEUE_FILE environment variable (absolute or relative path)
    2. Default: generator/runner_queue.json
    
    Returns:
        Path object for the queue file
    """
    env_path = os.environ.get("RUNNER_QUEUE_FILE")
    if env_path:
        return Path(env_path)
    
    # Default to generator directory
    generator_dir = Path(__file__).parent.parent
    return generator_dir / DEFAULT_QUEUE_FILE_NAME

# --- Expanded Frameworks with auto-detection logic ---
# Gold Standard: Ensure parsers are directly callable and return schema objects (via decorators in parsers.py)
FRAMEWORKS: Dict[str, Dict[str, Any]] = {
    "pytest": {
        "cmd": [
            "pytest",
            "--junitxml=results.xml",
            "--cov",
            "--cov-report=xml:cov.xml",
        ],
        "parser": parse_junit_xml,  # Now directly refers to the decorated async parser
        "coverage_parser": parse_coverage_xml,
        "output_files": ["results.xml", "cov.xml", "htmlcov"],
        "detect": lambda files: any("pytest" in content for content in files.values())
        or any(f.startswith("test_") and f.endswith(".py") for f in files.keys()),
    },
    "unittest": {
        "cmd": ["python", "-m", "unittest", "discover", "-v"],
        "parser": parse_unittest_output,
        "coverage_parser": None,
        "output_files": ["results.txt"],
        "detect": lambda files: any("unittest" in content for content in files.values())
        or any(f.startswith("test_") and f.endswith(".py") for f in files.keys()),
    },
    "behave": {
        "cmd": [
            "behave",
            "--junit",
            "--junit-directory",
            ".",
            "--format",
            "json",
            "--outfile",
            "behave-results.json",
        ],
        "parser": parse_behave_junit,
        "coverage_parser": None,
        "output_files": [
            "behave-results.json",
            "behave.xml",
        ],  # Behave can produce JSON or XML
        "detect": lambda files: any(f.endswith(".feature") for f in files.keys()),
    },
    "robot": {
        "cmd": ["robot", "--outputdir", ".", "--output", "output.xml"],
        "parser": parse_robot_xml,
        "coverage_parser": None,
        "output_files": ["output.xml", "log.html", "report.html"],
        "detect": lambda files: any(f.endswith(".robot") for f in files.keys()),
    },
    "jest": {
        "cmd": [
            "npm",
            "test",
            "--",
            "--json",
            "--outputFile=results.json",
            "--coverage",
            "--coverageReporters=json",
        ],
        "parser": parse_jest_json,
        "coverage_parser": parse_istanbul_json,
        "output_files": ["results.json", "coverage", "jest-junit.xml"],
        "detect": lambda files: "jest" in files.get("package.json", "")
        or any(
            f.endswith((".test.js", ".spec.js", ".test.ts", ".spec.ts"))
            for f in files.keys()
        ),
    },
    "mocha": {
        "cmd": [
            "mocha",
            "--reporter",
            "json",
            "--full-trace",
            "--require",
            "source-map-support/register",
        ],
        "parser": lambda path: json.loads(
            path.read_text()
        ),  # Mochar is often text based, not specific parser here
        "coverage_parser": parse_istanbul_json,
        "output_files": ["results.json", "coverage"],
        "detect": lambda files: "mocha" in files.get("package.json", "")
        or any(f.endswith((".test.js", ".spec.js")) for f in files.keys()),
    },
    "go test": {
        "cmd": ["go", "test", "-json", "-coverprofile=coverage.out", "./..."],
        "parser": parse_go_test_json,
        "coverage_parser": parse_go_coverprofile,
        "output_files": ["test_results.json", "coverage.out"],
        "detect": lambda files: any(f.endswith("_test.go") for f in files.keys()),
    },
    "junit": {  # Generic JUnit, often used for Java/Maven/Gradle
        "cmd": ["mvn", "test"],  # Placeholder, actual cmd depends on project setup
        "parser": parse_surefire_xml,  # This parser handles multiple XMLs
        "coverage_parser": parse_jacoco_xml,
        "output_files": [
            "target/surefire-reports",
            "target/site/jacoco",
        ],  # Common Maven output dirs
        "detect": lambda files: "pom.xml" in files.keys()
        or any(f.endswith("Test.java") for f in files.keys()),
    },
    "gradle": {
        "cmd": ["gradle", "test"],
        "parser": parse_junit_xml,  # Often produces JUnit XML
        "coverage_parser": parse_jacoco_xml,  # Can produce JaCoCo XML
        "output_files": ["build/test-results", "build/reports"],
        "detect": lambda files: "build.gradle" in files.keys()
        or any(f.endswith("Test.java") for f in files.keys()),
    },
    "selenium": {
        "cmd": [
            "pytest",
            "--driver",
            "Chrome",
            "--url",
            "http://example.com",
        ],  # Example cmd
        "parser": parse_junit_xml,  # Often integrates with Pytest JUnit output
        "coverage_parser": None,
        "output_files": ["results.xml"],
        "detect": lambda files: any(
            "selenium" in content.lower() for content in files.values()
        ),
    },
}

# --- Documentation Generation/Validation Frameworks ---
DOC_FRAMEWORKS = {
    "sphinx": {
        "cmd": ["sphinx-build", "-b", "html", ".", "_build"],
        "validator_cmd": ["sphinx-build", "-b", "linkcheck", ".", "_build"],
        "output_dir": "_build/html",
        "detect": lambda files: "conf.py" in files.keys()
        or "docs/conf.py" in files.keys(),
    },
    "mkdocs": {
        "cmd": ["mkdocs", "build"],
        "validator_cmd": ["mkdocs", "build", "--strict"],
        "output_dir": "site",
        "detect": lambda files: "mkdocs.yml" in files.keys(),
    },
    "javadoc": {
        "cmd": ["javadoc", "-d", "javadoc", "-sourcepath", "src"],
        "validator_cmd": None,
        "output_dir": "javadoc",
        "detect": lambda files: any(f.endswith(".java") for f in files.keys())
        and "src" in [Path(f).parts[0] for f in files.keys()],
    },
    "jsdoc": {
        "cmd": ["jsdoc", "-c", "jsdoc.json", "-d", "out"],
        "validator_cmd": None,
        "output_dir": "out",
        "detect": lambda files: "jsdoc.json" in files.keys()
        or any(f.endswith((".js", ".ts")) for f in files.keys()),
    },
    "go_doc": {
        "cmd": [
            "godoc",
            "-html",
            ".",
        ],  # `godoc` serves, so this would need capturing output or a custom script
        "validator_cmd": None,
        "output_dir": "godoc_html",
        "detect": lambda files: any(f.endswith(".go") for f in files.keys()),
    },
}


class PrioritizedTask:
    """Wrapper for tasks with priority for the heapq queue."""

    def __init__(self, priority: int, task: TaskPayload):
        self.priority = priority
        self.task = task
        self.task_id = task.task_id

    def __lt__(self, other: "PrioritizedTask") -> bool:
        """Compares tasks by priority (lower number has higher priority)."""
        return self.priority < other.priority


# Global instance for API and ConfigWatcher to interact with
runner_instance: Optional["Runner"] = None
# Lock for config reload safety. Ensures critical re-initialization is atomic.
reloading_lock = asyncio.Lock()


# --- Sandbox Test Execution Functions for TestGen Validator ---


async def run_tests_in_sandbox(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    temp_path: str,
    language: str,
    coverage: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """
    Secure sandbox execution for test validation.

    Args:
        code_files: Dictionary of filename -> code content
        test_files: Dictionary of filename -> test content
        temp_path: Temporary directory path
        language: Programming language
        coverage: Whether to collect coverage data

    Returns:
        Dictionary with test results and coverage data
    """
    try:
        # Validate input files
        if not test_files and not code_files:
            return {
                "status": "failed",
                "error": "No test files or code files provided",
                "coverage_percentage": 0.0,
                "pass_count": 0,
                "fail_count": 0,
            }

        # Create a runner config for sandbox execution
        config = RunnerConfig(
            backend="local",  # Correct attribute name
            framework="pytest",  # Required field
            instance_id=f"sandbox_{uuid.uuid4().hex[:8]}",  # Required field
            timeout=300,  # Correct attribute name
        )
        runner = Runner(config)

        # Create task payload
        task_payload = TaskPayload(
            test_files=test_files,
            code_files=code_files,
            output_path=temp_path,
            timeout=300,
            dry_run=False,
            task_id=f"sandbox_{uuid.uuid4()}",
        )

        # Run the tests
        result = await runner.run_tests(task_payload)

        # Extract coverage and test results
        results = result.results or {}

        return {
            "coverage_percentage": results.get("coverage_percentage", 0.0),
            "lines_covered": results.get("lines_covered", 0),
            "total_lines": results.get("total_lines", 0),
            "test_results": results.get("test_results", {}),
            "pass_count": results.get("pass_count", 0),
            "fail_count": results.get("fail_count", 0),
            "status": "success" if result.status == "completed" else "failed",
        }

    except Exception as e:
        logger.error(f"Sandbox execution failed: {e}")
        return {
            "coverage_percentage": 0.0,
            "lines_covered": 0,
            "total_lines": 0,
            "test_results": {},
            "pass_count": 0,
            "fail_count": 0,
            "status": "failed",
            "error": str(e),
        }


async def run_stress_tests(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    temp_path: str,
    language: str,
    config: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Run stress/performance tests in a controlled environment.

    Args:
        code_files: Dictionary of filename -> code content
        test_files: Dictionary of filename -> test content
        temp_path: Temporary directory path
        language: Programming language
        config: Stress testing configuration

    Returns:
        Dictionary with performance metrics
    """
    try:
        stress_config = config or {}

        # Validate input files
        if not test_files and not code_files:
            return {
                "status": "failed",
                "error": "No test files or code files provided",
                "metrics": {},
            }

        # Create a runner config for stress testing
        runner_config = RunnerConfig(
            backend="local",  # Correct attribute name
            framework="pytest",  # Required field
            instance_id=f"stress_{uuid.uuid4().hex[:8]}",  # Required field
            timeout=stress_config.get("timeout", 600),  # Correct attribute name
        )
        runner = Runner(runner_config)

        # Create task payload for stress testing
        task_payload = TaskPayload(
            test_files=test_files,
            code_files=code_files,
            output_path=temp_path,
            timeout=stress_config.get("timeout", 600),
            dry_run=False,
            task_id=f"stress_{uuid.uuid4()}",
        )

        # Run stress tests multiple times to get performance metrics
        iterations = stress_config.get("iterations", 3)
        response_times = []
        errors = 0
        crashes = 0

        for i in range(iterations):
            try:
                start_time = time.time()
                result = await runner.run_tests(task_payload)
                end_time = time.time()

                response_time = (end_time - start_time) * 1000  # Convert to ms
                response_times.append(response_time)

                if result.status != "completed":
                    errors += 1

            except Exception as e:
                errors += 1
                crashes += 1
                logger.warning(f"Stress test iteration {i+1} crashed: {e}")

        # Calculate metrics
        avg_response_time = (
            sum(response_times) / len(response_times)
            if response_times
            else float("inf")
        )
        error_rate = (errors / iterations) * 100 if iterations > 0 else 100.0
        crashes_detected = crashes > 0

        return {
            "avg_response_time_ms": avg_response_time,
            "error_rate_percentage": error_rate,
            "crashes_detected": crashes_detected,
            "total_iterations": iterations,
            "successful_runs": iterations - errors,
            "response_times": response_times,
            "status": "success" if error_rate < 50 else "failed",
        }

    except Exception as e:
        logger.error(f"Stress testing failed: {e}")
        return {
            "avg_response_time_ms": float("inf"),
            "error_rate_percentage": 100.0,
            "crashes_detected": True,
            "total_iterations": 0,
            "successful_runs": 0,
            "response_times": [],
            "status": "failed",
            "error": str(e),
        }


class Runner(ABC):
    """
    The most robust, extensible, multi-language, multi-backend runner with prioritized,
    distributed, and feedback-driven test execution.
    """

    def __init__(self, config: RunnerConfig):
        self.config: RunnerConfig = config
        self.instance_id: str = self.config.instance_id

        # Initialize backend
        backend_cls = ALL_BACKENDS.get(self.config.backend)
        if not backend_cls:
            raise BackendError(
                error_codes["BACKEND_INIT_FAILURE"],
                detail=f"Configured backend '{self.config.backend}' is not supported or not installed.",
                backend_type=self.config.backend,
            )
        self.backend = backend_cls(self.config)

        self.framework_info: Optional[Dict[str, Any]] = None

        # ThreadPoolExecutor for CPU-bound tasks or blocking I/O (e.g., synchronous SDK calls)
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.parallel_workers
        )
        self.queue: List[PrioritizedTask] = []
        self.provenance_chain: List[Dict[str, Any]] = (
            []
        )  # This is now a log of local hashes, not the full chain
        self.feedback_scores: deque[float] = deque(
            maxlen=getattr(self.config, "feedback_score_history_size", 100)
        )
        self.feedback_model: Dict[str, List[float]] = {}
        self.current_strategy: str = "default"

        # Task status tracking (in-memory map for live state, needs persistence for crash recovery)
        self.task_status_map: Dict[str, TaskResult] = {}
        self._status_lock = asyncio.Lock()  # Add lock for thread-safe status updates

        # [NEW] Background task references
        self._monitor_task: Optional[asyncio.Task] = None
        self._distributed_task: Optional[asyncio.Task] = None
        self._queue_processor_task: Optional[asyncio.Task] = None

        # Backwards-compatible background task for existing integrations/tests
        try:
            loop = asyncio.get_running_loop()
            # Start the queue monitor automatically when an event loop is running
            self._monitor_task = loop.create_task(self._monitor_queue())
            self.background_task = self._monitor_task
        except RuntimeError:
            # No running loop at construction time; services can be started explicitly later.
            self.background_task = None

        self._load_persisted_queue()
        # [REMOVED] _self_test() - Moved to start_services
        # [REMOVED] asyncio.create_task() calls - Moved to start_services

    # [NEW] Explicit async startup
    async def start_services(self):
        """
        Starts all background monitoring and worker tasks.
        This MUST be called from an async context *after* the Runner is instantiated.
        """
        logger.info("Starting Runner background services...")

        # 1. Run self-test before starting workers
        await self._self_test()

        # 2. Start queue monitor
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_queue())

        # 3. Start queue processor for background task execution
        if self._queue_processor_task is None or self._queue_processor_task.done():
            self._queue_processor_task = asyncio.create_task(self._process_queue())
            logger.info("Queue processor started - tasks will be automatically processed")

        # 4. Start distributed worker if configured
        if self.config.distributed and (
            self._distributed_task is None or self._distributed_task.done()
        ):
            self._distributed_task = asyncio.create_task(self._distributed_worker())

        # Log queue status on startup
        if self.queue:
            logger.info(
                json.dumps({
                    "event": "startup_queue_status",
                    "tasks_in_queue": len(self.queue),
                    "task_ids": [pt.task_id for pt in self.queue],
                    "message": "Tasks loaded from persisted queue will be processed"
                })
            )

        logger.info("Runner background services started.")

    # [NEW] Explicit async shutdown
    async def shutdown_services(self):
        """
        Gracefully stops all background monitoring and worker tasks.
        """
        logger.info("Stopping Runner background services...")

        tasks_to_cancel = [self._monitor_task, self._distributed_task, self._queue_processor_task]
        # Include legacy alias if present
        bg_task = getattr(self, "background_task", None)
        if bg_task is not None:
            tasks_to_cancel.append(bg_task)

        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self._monitor_task = None
        self._distributed_task = None
        self._queue_processor_task = None
        if hasattr(self, "background_task"):
            self.background_task = None

        if self.executor:
            self.executor.shutdown(wait=True)

        logger.info("Runner background services stopped.")

    async def _save_files_to_temp_dir(self, files: Dict[str, str], target_dir: Path):
        """
        Helper to asynchronously save a dict of files to a target directory.
        This replaces the missing `save_files_to_output` utility.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        tasks = []
        for file_name, content in files.items():
            # Sanitize file_name to prevent path traversal
            safe_name = os.path.basename(file_name)
            if not safe_name or safe_name in (".", ".."):
                logger.warning(f"Skipping potentially unsafe file name: {file_name}")
                continue

            full_path = target_dir / safe_name
            tasks.append(self._write_file_async(full_path, content))
        await asyncio.gather(*tasks)

    async def _write_file_async(self, path: Path, content: str):
        """Async file write helper."""
        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(content)
        except Exception as e:
            logger.error(f"Failed to write file to temp dir {path}: {e}", exc_info=True)
            # Raise a structured error
            raise SetupError(
                error_codes["SETUP_FAILURE"],
                detail=f"Failed to write file to temp dir {path}",
                task_id="N/A",
                stage="file_save",
                cause=e,
            )

    # [NEW] Converted to async
    async def _self_test(self) -> None:
        span = trace.get_current_span()
        try:
            self.config.model_validate(
                self.config.model_dump()
            )  # Validate config against its Pydantic schema
            span.add_event("Config validated by Pydantic")

            backend_health = self.backend.health()
            if backend_health.get("status") != "healthy":
                raise BackendError(
                    error_codes["BACKEND_INIT_FAILURE"],
                    detail=f"Backend unhealthy during self-test: {backend_health.get('error', 'unknown error')}",
                    backend_type=self.config.backend,
                )
            span.add_event("Backend health check passed")

            dummy_test_files = {
                "test_dummy.py": "import unittest\nclass DummyTests(unittest.TestCase):\n def test_true(self): self.assertTrue(True)"
            }
            dummy_code_files = {"dummy_code.py": "def dummy_func(): pass"}

            with tempfile.TemporaryDirectory() as temp_dir_str:
                temp_path = Path(temp_dir_str)
                dummy_task_payload = TaskPayload(
                    test_files=dummy_test_files,
                    code_files=dummy_code_files,
                    output_path=str(temp_path),
                    timeout=self.config.timeout,
                    dry_run=True,
                    task_id="runner_self_test_task",
                )
                # [FIX] Await the async run_tests call
                test_results: TaskResult = await self.run_tests(dummy_task_payload)

            if test_results.status != "completed" or not (
                test_results.results
                and test_results.results.get("dry_run_result", False)
            ):
                raise TestExecutionError(
                    error_codes["TEST_EXECUTION_FAILED"],
                    detail="Dry-run test failed unexpectedly during self-test.",
                    task_id=dummy_task_payload.task_id,
                    results=test_results.results,
                )

            span.add_event("Dry-run test successful")

            HEALTH_STATUS.labels(
                component_name="overall", instance_id=self.instance_id
            ).set(1)
            logger.info(
                "Runner self-test PASSED: Backend healthy and dummy test run successful."
            )
            RUNNER_CONFIG_VERSION.labels(version=str(self.config.version)).set(1)
        except RunnerError as e:
            HEALTH_STATUS.labels(
                component_name="overall", instance_id=self.instance_id
            ).set(0)
            logger.critical(
                json.dumps(
                    {
                        "event": "runner_self_test_failed",
                        "task_id": e.task_id,
                        "error": e.as_dict(),
                    }
                ),
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.ERROR, f"Self-test failed: {e.error_code}")
            )
            span.record_exception(e)
            raise
        except Exception as e:
            HEALTH_STATUS.labels(
                component_name="overall", instance_id=self.instance_id
            ).set(0)
            logger.critical(
                json.dumps(
                    {
                        "event": "runner_self_test_unexpected_failure",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                ),
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.ERROR, f"Self-test failed unexpectedly: {e}")
            )
            span.record_exception(e)
            raise

    async def _monitor_queue(self) -> None:
        """Monitors the task queue and updates Prometheus metrics."""
        while True:
            try:
                # [FIX] Provide required labels for the RUN_QUEUE gauge
                RUN_QUEUE.labels(framework="all", instance_id=self.instance_id).set(
                    len(self.queue)
                )

                status_counts: Dict[str, int] = defaultdict(
                    int
                )  # Use defaultdict for cleaner counting
                for task_result in self.task_status_map.values():
                    status_counts[task_result.status] += 1

                # Ensure all possible statuses are reported, even if 0
                all_statuses = [
                    "pending",
                    "running",
                    "completed",
                    "failed",
                    "timed_out",
                    "enqueued",
                ]
                for status in all_statuses:
                    RUNNER_TASK_STATUS.labels(status=status).set(status_counts[status])

                # [REMOVED] self.backend.get_metrics()

                if self.config.distributed:
                    # This metric would be set by the distributed coordination logic,
                    # but we can set a default of 1 for a non-distributed leader.
                    DISTRIBUTED_NODES_ACTIVE.set(1)

                await asyncio.sleep(self.config.metrics_interval_seconds)
            except asyncio.CancelledError:
                logger.info("Queue monitor task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in queue monitor: {e}", exc_info=True)
                await asyncio.sleep(
                    self.config.metrics_interval_seconds
                )  # Avoid fast loop on error

    async def _process_queue(self) -> None:
        """
        Background worker that processes tasks from the queue.
        
        This method runs continuously and processes tasks that have been
        enqueued via the enqueue() method or loaded from the persisted queue.
        Tasks are processed in priority order (lower priority number = higher priority).
        """
        logger.info(
            json.dumps({
                "event": "queue_processor_started",
                "message": "Queue processor is now active and will process enqueued tasks"
            })
        )
        
        # Configuration for queue processing - use constants as defaults
        poll_interval = getattr(self.config, "queue_poll_interval_seconds", DEFAULT_QUEUE_POLL_INTERVAL_SECONDS)
        max_concurrent_tasks = getattr(self.config, "max_concurrent_tasks", DEFAULT_MAX_CONCURRENT_TASKS)
        
        # Track running tasks
        running_tasks: Dict[str, asyncio.Task] = {}
        
        while True:
            try:
                # Clean up completed tasks
                completed_task_ids = [
                    task_id for task_id, task in running_tasks.items()
                    if task.done()
                ]
                for task_id in completed_task_ids:
                    try:
                        # Get result to propagate any exceptions to logs
                        running_tasks[task_id].result()
                    except Exception as e:
                        logger.error(f"Task {task_id} failed with error: {e}", exc_info=True)
                    del running_tasks[task_id]
                
                # Check if we can process more tasks
                if len(running_tasks) >= max_concurrent_tasks:
                    await asyncio.sleep(poll_interval)
                    continue
                
                # Check if there are tasks in the queue
                if not self.queue:
                    await asyncio.sleep(poll_interval)
                    continue
                
                # Pop the highest priority task (lowest number)
                prio_task = heapq.heappop(self.queue)
                original_payload = prio_task.task
                
                # Create a copy of the task payload to avoid mutating the original
                # Using Pydantic's model_copy() for proper deep copy that handles all fields
                # This ensures the original payload remains unchanged if used elsewhere
                task_id = original_payload.task_id or str(uuid.uuid4())
                task_payload = original_payload.model_copy(update={"task_id": task_id})
                
                # Log task dispatch event
                logger.info(
                    json.dumps({
                        "event": "task_dispatched",
                        "task_id": task_id,
                        "priority": prio_task.priority,
                        "remaining_queue_size": len(self.queue),
                        "message": f"Task {task_id} dispatched for execution"
                    })
                )
                
                # Create and track the execution task
                execution_task = asyncio.create_task(
                    self._execute_task_safely(task_payload)
                )
                running_tasks[task_id] = execution_task
                
                # Update queue persistence after removing task
                self._persist_queue()
                
            except asyncio.CancelledError:
                logger.info("Queue processor task cancelled.")
                # Cancel all running tasks
                for task_id, task in running_tasks.items():
                    if not task.done():
                        task.cancel()
                break
            except Exception as e:
                logger.error(f"Error in queue processor: {e}", exc_info=True)
                await asyncio.sleep(poll_interval)
    
    async def _execute_task_safely(self, task_payload: TaskPayload) -> None:
        """
        Safely execute a task and handle any errors.
        
        This wrapper ensures that task execution errors are properly logged
        and don't crash the queue processor.
        """
        task_id = task_payload.task_id
        try:
            logger.info(
                json.dumps({
                    "event": "task_execution_started",
                    "task_id": task_id,
                    "dry_run": task_payload.dry_run,
                })
            )
            
            # Run the actual test execution
            result = await self.run_tests(task_payload)
            
            logger.info(
                json.dumps({
                    "event": "task_execution_completed",
                    "task_id": task_id,
                    "status": result.status,
                })
            )
            
        except Exception as e:
            logger.error(
                json.dumps({
                    "event": "task_execution_failed",
                    "task_id": task_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }),
                exc_info=True
            )
            # Update task status to failed
            await self._update_task_status(
                task_id,
                "failed",
                finished_at=time.time(),
                error={"error_code": "EXECUTION_ERROR", "message": str(e)},
            )

    async def _distributed_worker(self) -> None:
        """Worker to send tasks to a distributed runner endpoint."""
        logger.info(
            f"Distributed worker started, sending tasks to: {self.config.dist_url}"
        )
        if not self.config.dist_url:
            logger.warning(
                "Distributed worker enabled but 'dist_url' is not configured. Disabling distributed worker."
            )
            return

        session = aiohttp.ClientSession()

        while True:
            try:
                async with (
                    reloading_lock
                ):  # Acquire lock to prevent reconfig during send
                    if not self.queue:
                        await asyncio.sleep(self.config.dist_poll_interval_seconds)
                        continue

                    # FIX: Use getattr
                    batch_size: int = getattr(self.config, "dist_batch_size", 5)

                    tasks_to_send: List[TaskPayload] = []

                    for _ in range(min(batch_size, len(self.queue))):
                        prio_task = heapq.heappop(self.queue)
                        tasks_to_send.append(prio_task.task)
                        self._update_task_status(
                            prio_task.task_id, "enqueued", started_at=time.time()
                        )

                    logger.info(
                        json.dumps(
                            {
                                "event": "distributed_batch_send_attempt",
                                "count": len(tasks_to_send),
                                "task_ids": [
                                    t.task_id for t in tasks_to_send if t.task_id
                                ],
                                "endpoint": self.config.dist_url,
                            }
                        )
                    )
                    span = trace.get_current_span()
                    span.set_attribute("dist_worker.batch_size", len(tasks_to_send))

                    try:
                        start_dist_latency = time.time()
                        # FIX: Use getattr
                        timeout_val = getattr(
                            self.config, "dist_send_timeout_seconds", 30
                        )
                        async with session.post(
                            self.config.dist_url + "/enqueue_test_task_batch",
                            json=BatchTaskPayload(tasks=tasks_to_send).model_dump(),
                            timeout=timeout_val,
                        ) as resp:
                            resp.raise_for_status()
                            dist_response = await resp.json()

                            dist_latency = time.time() - start_dist_latency
                            DISTRIBUTED_LATENCY.labels(
                                instance_id=self.instance_id
                            ).observe(dist_latency)
                            logger.info(
                                json.dumps(
                                    {
                                        "event": "distributed_batch_send_success",
                                        "response_status": dist_response.get(
                                            "status", "N/A"
                                        ),
                                        "task_ids": dist_response.get("task_ids", []),
                                        "latency_ms": dist_latency * 1000,
                                    }
                                )
                            )

                            span.add_event(
                                "Distributed batch sent",
                                attributes={
                                    "response_status": dist_response.get("status")
                                },
                            )

                            if (
                                dist_response.get("status") == "success"
                                or dist_response.get("status") == "enqueued"
                            ):
                                pass
                            else:
                                error_detail = dist_response.get(
                                    "message", "Remote enqueue failed."
                                )
                                logger.error(
                                    json.dumps(
                                        {
                                            "event": "distributed_batch_send_failed_remote_status",
                                            "detail": error_detail,
                                            "response": dist_response,
                                            "task_ids": [
                                                t.task_id
                                                for t in tasks_to_send
                                                if t.task_id
                                            ],
                                        }
                                    )
                                )
                                RUN_ERRORS.labels(
                                    error_type="distributed_worker",
                                    backend="dist_api_failure",
                                    instance_id=self.instance_id,
                                ).inc()
                                span.set_status(
                                    Status(StatusCode.ERROR, "Remote enqueue failed")
                                )
                                for task in tasks_to_send:
                                    heapq.heappush(
                                        self.queue, PrioritizedTask(task.priority, task)
                                    )
                                    self._update_task_status(
                                        task.task_id,
                                        "pending",
                                        error=DistributedError(
                                            error_codes[
                                                "DISTRIBUTED_COMMUNICATION_ERROR"
                                            ],
                                            detail="Remote enqueue rejected by API",
                                            endpoint=self.config.dist_url,
                                            cause=None,
                                        ).as_dict(),
                                    )
                    except aiohttp.ClientError as e:
                        logger.error(
                            json.dumps(
                                {
                                    "event": "distributed_batch_send_network_error",
                                    "detail": str(e),
                                    "task_ids": [
                                        t.task_id for t in tasks_to_send if t.task_id
                                    ],
                                }
                            ),
                            exc_info=True,
                        )
                        RUN_ERRORS.labels(
                            error_type="distributed_worker",
                            backend="network_error",
                            instance_id=self.instance_id,
                        ).inc()
                        span.set_status(Status(StatusCode.ERROR, f"Network error: {e}"))
                        span.record_exception(e)
                        await self.backend.recover()
                        for task in tasks_to_send:
                            heapq.heappush(
                                self.queue, PrioritizedTask(task.priority, task)
                            )
                            self._update_task_status(
                                task.task_id,
                                "pending",
                                error=DistributedError(
                                    error_codes["DISTRIBUTED_COMMUNICATION_ERROR"],
                                    detail="Network error sending task batch",
                                    endpoint=self.config.dist_url,
                                    cause=e,
                                ).as_dict(),
                            )
                    except asyncio.TimeoutError:
                        # FIX: Use getattr
                        timeout_val = getattr(
                            self.config, "dist_send_timeout_seconds", 30
                        )
                        logger.error(
                            json.dumps(
                                {
                                    "event": "distributed_batch_send_timeout",
                                    "timeout_seconds": timeout_val,
                                    "task_ids": [
                                        t.task_id for t in tasks_to_send if t.task_id
                                    ],
                                }
                            )
                        )
                        RUN_ERRORS.labels(
                            error_type="distributed_worker",
                            backend="send_timeout",
                            instance_id=self.instance_id,
                        ).inc()
                        span.set_status(Status(StatusCode.ERROR, "Send timeout"))
                        for task in tasks_to_send:
                            heapq.heappush(
                                self.queue, PrioritizedTask(task.priority, task)
                            )
                            self._update_task_status(
                                task.task_id,
                                "pending",
                                error=TimeoutError(
                                    error_codes["TASK_TIMEOUT"],
                                    detail="Distributed send timed out",
                                    timeout_seconds=timeout_val,
                                ).as_dict(),
                            )
                    except Exception as e:
                        logger.error(
                            json.dumps(
                                {
                                    "event": "distributed_worker_unexpected_error",
                                    "detail": str(e),
                                    "task_ids": [
                                        t.task_id for t in tasks_to_send if t.task_id
                                    ],
                                }
                            ),
                            exc_info=True,
                        )
                        RUN_ERRORS.labels(
                            error_type="distributed_worker",
                            backend="unexpected_error",
                            instance_id=self.instance_id,
                        ).inc()
                        span.set_status(
                            Status(StatusCode.ERROR, f"Unexpected error: {e}")
                        )
                        span.record_exception(e)
                        for task in tasks_to_send:
                            heapq.heappush(
                                self.queue, PrioritizedTask(task.priority, task)
                            )
                            self._update_task_status(
                                task.task_id,
                                "pending",
                                error=DistributedError(
                                    error_codes["DISTRIBUTED_COMMUNICATION_ERROR"],
                                    detail="Unexpected error in distributed worker",
                                    cause=e,
                                ).as_dict(),
                            )

                await asyncio.sleep(
                    self.config.dist_poll_interval_seconds
                )  # Wait outside the lock
            except asyncio.CancelledError:
                logger.info("Distributed worker task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in distributed worker: {e}", exc_info=True)
                await asyncio.sleep(self.config.dist_poll_interval_seconds)

        # Close session on exit
        if session and not session.closed:
            await session.close()
            logger.info("Distributed worker aiohttp session closed.")

    async def _add_provenance(
        self, entry_data: Dict[str, Any], task_id: str
    ) -> Dict[str, Any]:
        """
        Creates a secure, chained audit log event for a task result.
        This refactored method calls the centralized log_audit_event function
        from runner_logging, which handles signing and chaining.
        """
        span = trace.get_current_span()
        try:
            current_config_snapshot = self.config
            # [PYDANTIC V2 FIX] Remove sort_keys=True
            config_hash = hashlib.sha256(
                current_config_snapshot.model_dump_json().encode()
            ).hexdigest()

            # Redact sensitive data *before* logging, as a defense-in-depth
            # The logger's filter will also redact, but this is safer.
            # [FIX] redact_secrets is now synchronous, remove await
            sanitized_entry_data = redact_secrets(
                entry_data.copy(), patterns=self.config.custom_redaction_patterns
            )

            # Create the data payload for the audit event
            audit_data_payload = {
                "config_hash_at_run": config_hash,
                "backend_used": self.backend.__class__.__name__,
                "framework_used": (
                    self.framework_info.get("cmd", "N/A")
                    if self.framework_info
                    else "N/A"
                ),
                "instance_id": self.instance_id,
                # Log a summary, not the full (potentially huge) data blob
                "result_summary": {
                    k: v
                    for k, v in sanitized_entry_data.items()
                    if k
                    not in [
                        "code_files",
                        "test_files",
                        "coverage_details",
                        "raw_output_summary",
                        "test_cases",
                    ]
                },
            }

            # Add key metrics to the summary
            summary = audit_data_payload["result_summary"]
            summary["pass_rate"] = sanitized_entry_data.get("pass_rate")
            summary["total_tests"] = sanitized_entry_data.get("total_tests")
            summary["coverage_percentage"] = sanitized_entry_data.get(
                "coverage_percentage"
            )
            summary["mutation_survival_rate"] = sanitized_entry_data.get(
                "mutation_survival_rate"
            )

            # Call the V2 centralized audit logger
            # This function (from runner_logging) handles its own hashing, signing, and chaining
            await log_audit_event(
                action="TestRunCompleted",
                data=audit_data_payload,
                task_id=task_id,  # Pass task_id as extra kwarg for context
            )

            logger.debug(f"Provenance audit event logged for task {task_id}")
            span.add_event("Provenance audit event logged")

            # Return the original (unredacted) data for the TaskResult
            # The provenance is now in the *audit log*, not attached to the result object
            return entry_data

        except Exception as e:
            logger.critical(
                f"CRITICAL: Failed to log audit event for task {task_id}: {e}. Audit trail is incomplete.",
                exc_info=True,
            )
            span.set_status(Status(StatusCode.ERROR, "Failed to log audit event"))
            span.record_exception(e)
            # Return the original data even if audit logging fails
            return entry_data

    def _run_feedback(self, results: Dict[str, Any], task_id: str):
        score: float = results.get("pass_rate", 0.5)
        if score is None:
            score = 0.5

        self.feedback_scores.append(score)
        RUN_PASS_RATE.set(
            sum(self.feedback_scores) / len(self.feedback_scores)
            if self.feedback_scores
            else 0.0
        )

        if self.current_strategy not in self.feedback_model:
            self.feedback_model[self.current_strategy] = []
        self.feedback_model[self.current_strategy].append(score)
        avg_score_for_strategy = (
            sum(self.feedback_model[self.current_strategy])
            / len(self.feedback_model[self.current_strategy])
            if self.feedback_model[self.current_strategy]
            else 0.0
        )
        logger.info(
            json.dumps(
                {
                    "event": "feedback_recorded",
                    "task_id": task_id,
                    "score": score,
                    "current_strategy": self.current_strategy,
                    "avg_score_for_strategy": f"{avg_score_for_strategy:.2f}",
                }
            )
        )

        self._adjust_strategy()

    def _adjust_strategy(self):
        if not self.feedback_model:
            return

        # FIX: Use getattr
        min_feedback_points = getattr(
            self.config, "min_feedback_points_for_strategy_adj", 5
        )

        eligible_strategies = {
            k: v
            for k, v in self.feedback_model.items()
            if len(v) >= min_feedback_points
        }

        if not eligible_strategies:
            logger.debug(
                "Not enough feedback points for strategy adjustment. Skipping strategy adjustment."
            )
            return

        best_strategy = max(
            eligible_strategies,
            key=lambda k: sum(eligible_strategies[k]) / len(eligible_strategies[k]),
        )

        if best_strategy != self.current_strategy:
            logger.info(
                json.dumps(
                    {
                        "event": "strategy_adjustment_triggered",
                        "old_strategy": self.current_strategy,
                        "new_strategy": best_strategy,
                        "reason": "Feedback score improvement",
                    }
                )
            )
            self.current_strategy = best_strategy

            # FIX: Use getattr
            strategy_config_map = getattr(self.config, "strategy_configs", {})
            new_strategy_settings = strategy_config_map.get(best_strategy, {})

            if new_strategy_settings:
                if "parallel_workers" in new_strategy_settings:
                    self.config.parallel_workers = new_strategy_settings[
                        "parallel_workers"
                    ]
                if "timeout" in new_strategy_settings:
                    self.config.timeout = new_strategy_settings["timeout"]
                if "backend" in new_strategy_settings:
                    self.config.backend = new_strategy_settings["backend"]

                logger.info(
                    json.dumps(
                        {
                            "event": "strategy_settings_applied",
                            "strategy": best_strategy,
                            "applied_settings": {
                                "parallel_workers": self.config.parallel_workers,
                                "timeout": self.config.timeout,
                                "backend": self.config.backend,
                            },
                        }
                    )
                )

                self.executor.shutdown(wait=True)
                self.executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.config.parallel_workers
                )

                backend_cls = ALL_BACKENDS.get(self.config.backend)
                if backend_cls:
                    self.backend = backend_cls(self.config)
                    logger.info(
                        f"Runner backend re-initialized to {self.backend.__class__.__name__} due to strategy change."
                    )
                else:
                    logger.error(
                        f"Strategy '{best_strategy}' requires unavailable backend '{self.config.backend}'. Keeping current backend."
                    )
            else:
                logger.warning(
                    f"No specific settings found for strategy '{best_strategy}'. No config changes applied."
                )

        # FIX: Use getattr
        anomaly_error_threshold = getattr(self.config, "anomaly_error_threshold", 10)

        current_error_count = 0
        try:
            current_error_count = RUN_ERRORS.labels(
                error_type="all_errors", backend="any", instance_id=self.instance_id
            )._value
        except Exception:
            pass

        if current_error_count > anomaly_error_threshold:
            logger.warning(
                json.dumps(
                    {
                        "event": "anomaly_detected_high_error_rate",
                        "error_count": current_error_count,
                        "threshold": anomaly_error_threshold,
                        "message": "Triggering backend recovery.",
                    }
                )
            )
            try:
                RUN_ERRORS.labels(
                    error_type="all_errors", backend="any", instance_id=self.instance_id
                ).inc(-current_error_count)
            except Exception:
                pass
            asyncio.create_task(self.backend.recover())

    def _detect_framework(self, test_files: Dict[str, str]) -> str:
        if self.config.framework != "auto" and self.config.framework in FRAMEWORKS:
            return self.config.framework

        for fw_name, fw_info in FRAMEWORKS.items():
            if fw_info.get("detect") and fw_info["detect"](test_files):
                logger.info(f"Auto-detected framework: {fw_name}")
                return fw_name

        logger.warning("Could not auto-detect framework. Defaulting to 'pytest'.")
        return "pytest"

    def _persist_queue(self) -> None:
        """
        Persist the task queue to disk for crash recovery.
        
        Uses configurable path via RUNNER_QUEUE_FILE environment variable.
        """
        queue_file = _get_queue_file_path()
        tasks_list = []
        tmp_queue = []
        while self.queue:
            prio_task = heapq.heappop(self.queue)
            # *** FIX: Call model_dump() on the .task attribute ***
            tasks_list.append(
                {"priority": prio_task.priority, "task": prio_task.task.model_dump()}
            )
            tmp_queue.append(prio_task)

        for pt in tmp_queue:
            heapq.heappush(self.queue, pt)

        try:
            # Ensure parent directory exists
            queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(tasks_list, f, indent=2)
            logger.info(f"Task queue persisted successfully to {queue_file} for crash recovery.")
        except Exception as e:
            logger.error(f"Failed to persist task queue: {e}", exc_info=True)
            RUN_ERRORS.labels(
                error_type="persistence",
                backend="queue_save_failed",
                instance_id=self.instance_id,
            ).inc()

    def _load_persisted_queue(self) -> None:
        """
        Load persisted queue from disk on startup.
        
        Uses configurable path via RUNNER_QUEUE_FILE environment variable.
        Tasks are loaded and will be automatically processed by the queue processor.
        """
        queue_file = _get_queue_file_path()
        
        if queue_file.exists():
            try:
                with open(queue_file, "r", encoding="utf-8") as f:
                    tasks_list = json.load(f)
                for t in tasks_list:
                    heapq.heappush(
                        self.queue,
                        PrioritizedTask(t["priority"], TaskPayload(**t["task"])),
                    )
                logger.info(
                    json.dumps({
                        "event": "persisted_queue_loaded",
                        "tasks_loaded": len(tasks_list),
                        "task_ids": [t["task"].get("task_id", "unknown") for t in tasks_list],
                        "queue_file": str(queue_file),
                        "message": f"Loaded {len(tasks_list)} tasks from persisted queue - they will be processed automatically"
                    })
                )
                # Remove the queue file after successful load to prevent duplicate processing
                queue_file.unlink()
            except Exception as e:
                logger.error(
                    f"Failed to load persisted queue from '{queue_file}': {e}. Starting with empty queue.",
                    exc_info=True,
                )
                RUN_ERRORS.labels(
                    error_type="persistence",
                    backend="queue_load_failed",
                    instance_id=self.instance_id,
                ).inc()
            finally:
                # Clean up queue file if it still exists
                if queue_file.exists():
                    try:
                        queue_file.unlink()
                    except Exception:
                        pass

    def _update_task_status_sync(
        self, task_id: str, status: str, **kwargs: Any
    ) -> None:
        """Synchronous wrapper for _update_task_status for backwards compatibility."""
        try:
            asyncio.get_running_loop()
            # If there's a running loop, schedule the coroutine
            asyncio.create_task(self._update_task_status(task_id, status, **kwargs))
        except RuntimeError:
            # No running loop, call synchronously without lock (acceptable for single-threaded use)
            self._update_task_status_unlocked(task_id, status, **kwargs)

    def _update_task_status_unlocked(
        self, task_id: str, status: str, **kwargs: Any
    ) -> None:
        """Internal implementation without lock for sync contexts."""
        if task_id not in self.task_status_map:
            explicit_started_at = kwargs.pop("started_at", None)

            if status == "running":
                started_at = explicit_started_at or time.time()
            else:
                started_at = (
                    explicit_started_at
                    if explicit_started_at is not None
                    else time.time()
                )

            self.task_status_map[task_id] = TaskResult(
                task_id=task_id,
                status=status,
                started_at=started_at,
                finished_at=kwargs.pop("finished_at", None),
                results=kwargs.pop("results", None),
                error=kwargs.pop("error", None),
                tags=kwargs.pop("tags", []),
                environment=kwargs.pop("environment", "production"),
            )
        else:
            task_result = self.task_status_map[task_id]
            try:
                RUNNER_TASK_STATUS.labels(status=task_result.status).dec()
            except Exception:
                pass

            task_result.status = status

            if status in ["completed", "failed", "timed_out"]:
                task_result.finished_at = kwargs.pop("finished_at", time.time())
                task_result.results = kwargs.pop("results", task_result.results)

                error_val = kwargs.pop("error", task_result.error)

                if isinstance(error_val, RunnerError):
                    error_val = error_val.as_dict()
                elif isinstance(error_val, dict) or error_val is None:
                    pass
                else:
                    error_val = {"error_code": str(error_val)}

                task_result.error = error_val
            elif status == "running" and task_result.started_at is None:
                task_result.started_at = kwargs.pop("started_at", time.time())

            for k, v in kwargs.items():
                if hasattr(task_result, k):
                    setattr(task_result, k, v)

    async def _update_task_status(
        self, task_id: str, status: str, **kwargs: Any
    ) -> None:
        """Updates the status of a task in the in-memory map and emits a metric (async with lock)."""
        async with self._status_lock:
            self._update_task_status_unlocked(task_id, status, **kwargs)

        logger.info(
            json.dumps(
                {
                    "event": "task_status_update",
                    "task_id": task_id,
                    "status": status,
                    "details": {
                        k: v for k, v in kwargs.items() if k not in ["results", "error"]
                    },
                }
            )
        )
        try:
            RUNNER_TASK_STATUS.labels(status=status).inc()
        except Exception:
            pass

    def get_task_status(self, task_id: str) -> Optional[TaskResult]:
        """Retrieves the current status of a specific task."""
        return self.task_status_map.get(task_id)

    def get_task_queue_snapshot(self) -> List[Dict[str, Any]]:
        """Provides a snapshot of tasks in the queue (for TUI display)."""
        snapshot = []
        for prio_task in sorted(self.queue, key=lambda x: x.priority):
            task_status = self.task_status_map.get(prio_task.task_id)
            status_str = task_status.status if task_status else "pending"
            snapshot.append(
                {
                    "task_id": prio_task.task_id,
                    "status": status_str,
                    "description": f"Priority: {prio_task.priority}",
                }
            )
        return snapshot

    async def run_tests(self, task_payload: TaskPayload) -> TaskResult:
        span = trace.get_current_span()
        task_id = task_payload.task_id if task_payload.task_id else str(uuid.uuid4())
        task_payload.task_id = task_id

        span.set_attribute("runner.task_id", task_id)
        span.set_attribute("runner.dry_run", task_payload.dry_run)
        span.set_attribute("runner.backend_type", self.config.backend)
        span.set_attribute("runner.framework_config", self.config.framework)
        span.set_attribute(RUNNER_LABEL_INSTANCE_ID, self.instance_id)

        self._update_task_status(
            task_id,
            "running",
            started_at=time.time(),
            tags=task_payload.tags,
            environment=task_payload.environment,
        )

        if task_payload.dry_run:
            logger.info(json.dumps({"event": "dry_run_simulation", "task_id": task_id}))
            span.add_event("Dry run simulation")
            simulated_results = {
                "dry_run_result": True,
                "pass_rate": 1.0,
                "coverage_percentage": 0.9,
                "stdout": "Simulated stdout",
                "stderr": "Simulated stderr",
                "parsed_results": {"tests_run": 1, "failures": 0, "errors": 0},
            }
            task_result = TaskResult(
                task_id=task_id,
                status="completed",
                results=simulated_results,
                started_at=self.task_status_map[task_id].started_at,
                finished_at=time.time(),
                tags=task_payload.tags,
                environment=task_payload.environment,
            )
            self._update_task_status(
                task_id,
                "completed",
                results=simulated_results,
                finished_at=task_result.finished_at,
            )
            return task_result

        timeout: int = (
            task_payload.timeout
            if task_payload.timeout is not None
            else self.config.timeout
        )

        temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
        temp_dir_path: Optional[Path] = None

        try:
            # [CHANGE B START] Dynamically resolve parsers for patching
            actual_framework_name = self.config.framework
            if actual_framework_name == "auto":
                actual_framework_name = self._detect_framework(task_payload.test_files)

            framework_info = FRAMEWORKS.get(actual_framework_name)
            if not framework_info:
                raise FrameworkError(
                    error_codes["FRAMEWORK_UNSUPPORTED"],
                    detail=f"Framework '{actual_framework_name}' not supported or detected.",
                    task_id=task_id,
                    framework_name=actual_framework_name,
                )

            # Use a copy so we don't mutate the global FRAMEWORKS mapping.
            framework_info = dict(framework_info)

            # IMPORTANT:
            # Always resolve parser / coverage_parser from runner.runner_parsers at runtime
            # so that tests patching runner.runner_parsers.parse_junit_xml, etc., take effect.
            if actual_framework_name == "pytest":
                framework_info["parser"] = runner_parsers.parse_junit_xml
                framework_info["coverage_parser"] = runner_parsers.parse_coverage_xml
            elif actual_framework_name == "jest":
                framework_info["parser"] = runner_parsers.parse_jest_json
                framework_info["coverage_parser"] = runner_parsers.parse_istanbul_json
            elif actual_framework_name == "unittest":
                framework_info["parser"] = runner_parsers.parse_unittest_output
            elif actual_framework_name == "behave":
                framework_info["parser"] = runner_parsers.parse_behave_junit
            elif actual_framework_name == "robot":
                framework_info["parser"] = runner_parsers.parse_robot_xml
            elif actual_framework_name == "go test":
                framework_info["parser"] = runner_parsers.parse_go_test_json
                framework_info["coverage_parser"] = runner_parsers.parse_go_coverprofile
            elif actual_framework_name == "junit":
                framework_info["parser"] = runner_parsers.parse_surefire_xml
                framework_info["coverage_parser"] = runner_parsers.parse_jacoco_xml
            elif actual_framework_name == "gradle":
                framework_info["parser"] = runner_parsers.parse_junit_xml
                framework_info["coverage_parser"] = runner_parsers.parse_jacoco_xml
            elif actual_framework_name == "selenium":
                framework_info["parser"] = runner_parsers.parse_junit_xml
            # (Note: 'mocha' parser is a lambda, so it can't be resolved this way - fine for tests)

            self.framework_info = framework_info
            # [CHANGE B END]

            logger.info(
                json.dumps(
                    {
                        "event": "test_execution_started",
                        "task_id": task_id,
                        "backend": self.config.backend,
                        "framework": actual_framework_name,
                        "timeout": timeout,
                    }
                )
            )
            span.set_attribute("runner.actual_framework", actual_framework_name)

            # --- Setup Phase ---
            temp_dir_obj = tempfile.TemporaryDirectory()
            temp_dir_path = Path(temp_dir_obj.name)
            span.set_attribute("runner.temp_dir", str(temp_dir_path))

            try:
                code_sub_dir = temp_dir_path / "code"
                tests_sub_dir = temp_dir_path / "tests"

                # Use the new helper to write files
                await self._save_files_to_temp_dir(
                    task_payload.code_files, code_sub_dir
                )
                await self._save_files_to_temp_dir(
                    task_payload.test_files, tests_sub_dir
                )
                span.add_event("Code and test files saved to temporary directory.")
            except Exception as e:
                # _save_files_to_temp_dir raises SetupError
                if isinstance(e, SetupError):
                    raise
                raise SetupError(
                    error_codes["SETUP_FAILURE"],
                    detail="Failed to save files to temporary directory.",
                    task_id=task_id,
                    stage="file_save",
                    cause=e,
                )

            span.add_event("Backend setup phase initiated")
            try:
                await self.backend.setup(temp_dir_path, self.config.custom_setup)
                span.add_event("Backend setup completed")
            except RunnerError as e:  # Catch structured errors from backend.setup
                e.task_id = task_id  # Ensure task_id is propagated
                raise e  # Re-raise directly
            except Exception as e:  # Catch any other unexpected errors
                raise SetupError(
                    error_codes["SETUP_FAILURE"],
                    detail="Backend setup failed due to unexpected error.",
                    task_id=task_id,
                    stage="backend_setup",
                    cause=e,
                )

            # --- Execution Phase ---
            cmd_to_execute: Union[str, List[str]] = self.framework_info["cmd"]
            span.add_event(f"Executing test command: {cmd_to_execute}")
            exec_results: Dict[str, Any]

            # --- CRITICAL FIX: REPLACE direct subprocess_wrapper call with backend.execute ---
            try:
                # 1. Update the payload with the final command detected by the runner
                #    (The backend might need this for Docker ENTRYPOINT/CMD)
                task_payload.command = cmd_to_execute

                # 2. Call the backend's isolated execution environment
                # Note: self.backend.execute is expected to return TaskResult, or an equivalent structure
                task_result_from_backend: TaskResult = await self.backend.execute(
                    payload=task_payload,
                    work_dir=temp_dir_path,
                    timeout=timeout,
                )

                # 3. Extract the necessary execution results from the TaskResult
                #    (The backend is responsible for populating stdout/stderr/returncode)
                exec_results = {
                    "success": task_result_from_backend.status == "completed",
                    "stdout": task_result_from_backend.results.get("stdout", ""),
                    "stderr": task_result_from_backend.results.get("stderr", ""),
                    "returncode": task_result_from_backend.results.get("returncode", 1),
                    "duration": task_result_from_backend.results.get(
                        "duration",
                        time.time() - self.task_status_map[task_id].started_at,
                    ),
                }

            except RunnerError as e:  # Catch structured errors from backend.execute
                e.task_id = task_id  # Ensure task_id is propagated
                raise e  # Re-raise directly
            except Exception as e:  # Catch any other unexpected errors
                raise TestExecutionError(
                    error_codes["TEST_EXECUTION_FAILED"],
                    detail="Test execution failed due to unexpected error.",
                    task_id=task_id,
                    cmd=str(cmd_to_execute),
                    cause=e,
                )
            # --- END CRITICAL FIX ---

            stdout = exec_results.get("stdout", "")
            stderr = exec_results.get("stderr", "")
            returncode = exec_results.get("returncode", 1)

            if returncode != 0:
                logger.warning(
                    json.dumps(
                        {
                            "event": "test_command_non_zero_exit",
                            "task_id": task_id,
                            "returncode": returncode,
                            "stdout_snippet": stdout[:500],
                            "stderr_snippet": stderr[:500],
                        }
                    )
                )
                span.set_attribute("runner.test_cmd_return_code", returncode)
                span.set_attribute("runner.test_cmd_stderr_snippet", stderr[:500])

            # --- Parsing Results Phase ---
            span.add_event("Parsing test results")
            test_report: TestReportSchema  # Expecting TestReportSchema
            try:
                test_report = await self.framework_info["parser"](
                    temp_dir_path
                )  # Parsers are now async and return schema
                logger.debug(
                    json.dumps(
                        {
                            "event": "parsed_test_results",
                            "task_id": task_id,
                            "results": test_report.model_dump(by_alias=True),
                        }
                    )
                )
            except RunnerError as e:  # Catch structured errors from parser
                e.task_id = task_id
                raise e
            except Exception as e:  # Catch unexpected errors from parser
                raise ParsingError(
                    error_codes["PARSING_ERROR"],
                    detail=f"Failed to parse test results for framework '{actual_framework_name}'.",
                    task_id=task_id,
                    parser_type="test_results",
                    cause=e,
                )

            parsed_results: Dict[str, Any] = test_report.model_dump(
                by_alias=True
            )  # Convert to dict for consistency

            if self.framework_info.get("coverage_parser"):
                span.add_event("Parsing coverage data")
                coverage_report: Optional[CoverageReportSchema] = None
                coverage_parser = self.framework_info.get("coverage_parser")
                try:
                    # --- FIX: Find actual coverage file, not the directory ---
                    cov_file = (
                        temp_dir_path / "coverage.xml"
                    )  # or glob: next(temp_dir_path.glob("coverage*"), None)
                    if cov_file.exists():
                        coverage_report = await coverage_parser(cov_file)
                    else:
                        logger.debug(
                            "No coverage file found; skipping coverage parsing."
                        )

                    if coverage_report:
                        logger.debug(
                            json.dumps(
                                {
                                    "event": "parsed_coverage_data",
                                    "task_id": task_id,
                                    "data": coverage_report.model_dump(by_alias=True),
                                }
                            )
                        )
                        parsed_results["coverage_percentage"] = (
                            coverage_report.coverage_percentage
                        )
                        parsed_results["coverage_details"] = {
                            k: v.model_dump()
                            for k, v in coverage_report.coverage_details.items()
                        }  # Convert CoverageDetail to dict
                        RUN_COVERAGE_PERCENT.set(parsed_results["coverage_percentage"])
                except RunnerError as e:  # Catch structured errors from coverage parser
                    logger.warning(
                        json.dumps(
                            {
                                "event": "coverage_parsing_failed",
                                "task_id": task_id,
                                "framework": actual_framework_name,
                                "error": e.as_dict(),
                            }
                        )
                    )
                    RUN_ERRORS.labels(
                        error_type="CoverageParsingError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_attribute("runner.coverage_parsing_error", e.detail)
                    # Do not re-raise, allow test run to complete with partial data
                except Exception as e:  # Catch unexpected errors from coverage parser
                    logger.warning(
                        json.dumps(
                            {
                                "event": "coverage_parsing_failed",
                                "task_id": task_id,
                                "framework": actual_framework_name,
                                "error": str(e),
                            }
                        )
                    )
                    RUN_ERRORS.labels(
                        error_type="CoverageParsingError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_attribute("runner.coverage_parsing_error", str(e))

            # --- Mutation Testing Phase ---
            # FIX: Use getattr
            if getattr(self.config, "mutation", False) and HAS_MUTATION_MODULE:
                span.add_event("Running mutation tests")
                try:
                    mut_results: Dict[str, Any] = await _mutation_test_func(
                        temp_dir_path,
                        self.config,
                        task_payload.code_files,
                        task_payload.test_files,
                        strategy=getattr(self.config, "mutation_strategy", "targeted"),
                        parallel=getattr(self.config, "mutation_parallel", True),
                        distributed=getattr(self.config, "mutation_distributed", False),
                    )
                    parsed_results["mutation_survival_rate"] = mut_results.get(
                        "survival_rate", 0.0
                    )
                    RUN_MUTATION_SURVIVAL.set(parsed_results["mutation_survival_rate"])
                    logger.info(
                        json.dumps(
                            {
                                "event": "mutation_test_completed",
                                "task_id": task_id,
                                "survival_rate": parsed_results[
                                    "mutation_survival_rate"
                                ],
                            }
                        )
                    )
                except RunnerError as e:  # Catch structured errors from mutation
                    logger.error(
                        json.dumps(
                            {
                                "event": "mutation_test_failed",
                                "task_id": task_id,
                                "error": e.as_dict(),
                            }
                        ),
                        exc_info=True,
                    )
                    RUN_ERRORS.labels(
                        error_type="MutationError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_status(
                        Status(
                            StatusCode.ERROR, f"Mutation testing failed: {e.error_code}"
                        )
                    )
                    span.record_exception(e)
                except Exception as e:
                    logger.error(
                        json.dumps(
                            {
                                "event": "mutation_test_failed",
                                "task_id": task_id,
                                "error": str(e),
                            }
                        ),
                        exc_info=True,
                    )
                    RUN_ERRORS.labels(
                        error_type="MutationError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_status(
                        Status(StatusCode.ERROR, f"Mutation testing failed: {e}")
                    )
                    span.record_exception(e)
            elif getattr(self.config, "mutation", False):
                logger.warning(
                    json.dumps(
                        {
                            "event": "mutation_skipped",
                            "task_id": task_id,
                            "reason": "module unavailable",
                        }
                    )
                )
                span.add_event("Mutation testing skipped: module unavailable")

            # --- Fuzz Testing Phase ---
            # FIX: Use getattr
            if getattr(self.config, "fuzz", False) and HAS_MUTATION_MODULE:
                span.add_event("Running fuzz tests")
                try:
                    fuzz_results: Dict[str, Any] = await _fuzz_test_func(
                        temp_dir_path, self.config, task_payload.code_files
                    )
                    parsed_results["fuzz_discoveries"] = fuzz_results.get(
                        "discoveries", 0
                    )
                    RUN_FUZZ_DISCOVERIES.inc(parsed_results["fuzz_discoveries"])
                    logger.info(
                        json.dumps(
                            {
                                "event": "fuzz_test_completed",
                                "task_id": task_id,
                                "discoveries": parsed_results["fuzz_discoveries"],
                            }
                        )
                    )
                except RunnerError as e:  # Catch structured errors from fuzzing
                    logger.error(
                        json.dumps(
                            {
                                "event": "fuzz_test_failed",
                                "task_id": task_id,
                                "error": e.as_dict(),
                            }
                        ),
                        exc_info=True,
                    )
                    RUN_ERRORS.labels(
                        error_type="FuzzError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_status(
                        Status(StatusCode.ERROR, f"Fuzz testing failed: {e.error_code}")
                    )
                    span.record_exception(e)
                except Exception as e:
                    logger.error(
                        json.dumps(
                            {
                                "event": "fuzz_test_failed",
                                "task_id": task_id,
                                "error": str(e),
                            }
                        ),
                        exc_info=True,
                    )
                    RUN_ERRORS.labels(
                        error_type="FuzzError",
                        backend=self.config.backend,
                        instance_id=self.instance_id,
                    ).inc()
                    span.set_status(
                        Status(StatusCode.ERROR, f"Fuzz testing failed: {e}")
                    )
                    span.record_exception(e)
            elif getattr(self.config, "fuzz", False):
                logger.warning(
                    json.dumps(
                        {
                            "event": "fuzz_skipped",
                            "task_id": task_id,
                            "reason": "module unavailable",
                        }
                    )
                )
                span.add_event("Fuzz testing skipped: module unavailable")

            # --- Documentation Generation/Validation Phase ---
            doc_validation_status_dict = {
                "status": "skipped",
                "message": "Not applicable or no documentation framework detected.",
            }
            actual_doc_framework_name = self.config.doc_framework
            doc_framework_info = None

            if actual_doc_framework_name == "auto":
                detected_doc_fw = None
                for df_name, df_info in DOC_FRAMEWORKS.items():
                    if df_info.get("detect") and df_info["detect"](
                        task_payload.code_files
                    ):
                        detected_doc_fw = df_name
                        break
                if detected_doc_fw:
                    actual_doc_framework_name = detected_doc_fw
                    logger.info(
                        f"Auto-detected documentation framework: {actual_doc_framework_name}."
                    )
                else:
                    logger.info(
                        "No specific documentation framework detected in project. Skipping doc validation."
                    )
                    span.add_event("Doc validation skipped: no framework detected")
                    parsed_results["doc_validation"] = doc_validation_status_dict

            if (
                actual_doc_framework_name != "auto"
                and actual_doc_framework_name is not None
            ):
                doc_framework_info = DOC_FRAMEWORKS.get(actual_doc_framework_name)
                if doc_framework_info:
                    span.add_event(
                        f"Running doc generation/validation for {actual_doc_framework_name}"
                    )
                    doc_output_source_dir = temp_dir_path / "code"

                    try:
                        logger.info(
                            f"Running doc generation using '{actual_doc_framework_name}' for task {task_id}..."
                        )

                        # Prepare payload for documentation build/validation
                        doc_build_payload = TaskPayload(
                            test_files=task_payload.test_files,  # Pass along files for context
                            code_files=task_payload.code_files,
                            output_path=task_payload.output_path,
                            timeout=timeout,
                            dry_run=False,
                            task_id=f"{task_id}_doc_build",
                            command=doc_framework_info["cmd"],
                        )

                        build_result_task = await self.backend.execute(
                            doc_build_payload, doc_output_source_dir, timeout
                        )
                        build_result = build_result_task.results  # Extract results dict

                        validation_result = {
                            "stdout": "",
                            "stderr": "",
                            "returncode": 0,
                        }
                        if doc_framework_info.get("validator_cmd"):
                            logger.info(
                                f"Running doc validation for '{actual_doc_framework_name}' for task {task_id}..."
                            )

                            doc_validate_payload = doc_build_payload.model_copy(
                                update={
                                    "task_id": f"{task_id}_doc_validate",
                                    "command": doc_framework_info["validator_cmd"],
                                }
                            )

                            validation_result_task = await self.backend.execute(
                                doc_validate_payload, doc_output_source_dir, timeout
                            )
                            validation_result = (
                                validation_result_task.results
                            )  # Extract results dict

                        if (
                            build_result.get("returncode", 0) == 0
                            and validation_result.get("returncode", 0) == 0
                        ):
                            doc_validation_status_dict = {
                                "status": "passed",
                                "build_stdout": build_result.get("stdout"),
                                "validation_stdout": validation_result.get("stdout"),
                            }
                            logger.info(
                                f"Documentation validation passed for {actual_doc_framework_name} for task {task_id}."
                            )
                            DOC_VALIDATION_STATUS.labels(
                                doc_framework_name=actual_doc_framework_name,
                                instance_id=self.instance_id,
                            ).set(1)
                        else:
                            doc_validation_status_dict = {
                                "status": "failed",
                                "build_stderr": build_result.get("stderr"),
                                "validation_stderr": validation_result.get("stderr"),
                                "message": "Doc build or validation command returned non-zero exit code.",
                            }
                            logger.warning(
                                f"Documentation validation failed for {actual_doc_framework_name} for task {task_id}."
                            )
                            DOC_VALIDATION_STATUS.labels(
                                doc_framework_name=actual_doc_framework_name,
                                instance_id=self.instance_id,
                            ).set(0)
                            DOC_GENERATION_ERRORS.labels(
                                error_type="validation_failed",
                                doc_framework_name=actual_doc_framework_name,
                                instance_id=self.instance_id,
                            ).inc()
                    except (
                        RunnerError
                    ) as e:  # Catch structured errors from backend.execute
                        doc_validation_status_dict = {
                            "status": "error",
                            "error": e.as_dict(),
                            "message": "Exception during doc generation/validation.",
                        }
                        logger.error(
                            f"Error during doc generation/validation for {actual_doc_framework_name} for task {task_id}: {e.detail}",
                            exc_info=True,
                        )
                        DOC_VALIDATION_STATUS.labels(
                            doc_framework_name=actual_doc_framework_name,
                            instance_id=self.instance_id,
                        ).set(0)
                        DOC_GENERATION_ERRORS.labels(
                            error_type="runtime_error",
                            doc_framework_name=actual_doc_framework_name,
                            instance_id=self.instance_id,
                        ).inc()
                    except Exception as e:
                        doc_validation_status_dict = {
                            "status": "error",
                            "error": str(e),
                            "message": "Exception during doc generation/validation.",
                        }
                        logger.error(
                            f"Error during doc generation/validation for {actual_doc_framework_name} for task {task_id}: {e}",
                            exc_info=True,
                        )
                        DOC_VALIDATION_STATUS.labels(
                            doc_framework_name=actual_doc_framework_name,
                            instance_id=self.instance_id,
                        ).set(0)
                        DOC_GENERATION_ERRORS.labels(
                            error_type="runtime_error",
                            doc_framework_name=actual_doc_framework_name,
                            instance_id=self.instance_id,
                        ).inc()

                    parsed_results["doc_validation"] = doc_validation_status_dict
                    span.set_attribute(
                        "runner.doc_validation_status",
                        doc_validation_status_dict["status"],
                    )
                else:
                    logger.warning(
                        f"Documentation framework '{actual_doc_framework_name}' is not recognized or configured. Skipping doc validation."
                    )
                    parsed_results["doc_validation"] = {
                        "status": "skipped",
                        "message": f"Unknown doc framework: {actual_doc_framework_name}",
                    }

            # --- Output File Collection ---
            output_path_actual = Path(task_payload.output_path)
            output_path_actual.mkdir(parents=True, exist_ok=True)

            for out_file_pattern in self.framework_info.get("output_files", []):
                for file_in_temp in temp_dir_path.rglob(out_file_pattern):
                    if file_in_temp.is_file():
                        target_path = (
                            output_path_actual
                            / "results"
                            / file_in_temp.relative_to(temp_dir_path)
                        )
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(file_in_temp, target_path)
                        logger.debug(
                            f"Copied output file: {file_in_temp} to {target_path}"
                        )
                        span.add_event(f"Output file copied: {file_in_temp.name}")

            # Copy generated documentation output
            if (
                "status" in doc_validation_status_dict
                and doc_validation_status_dict["status"] in ["passed", "failed"]
                and doc_framework_info
            ):
                final_doc_output_source_dir = (
                    temp_dir_path / doc_framework_info["output_dir"]
                )
                if final_doc_output_source_dir.exists():
                    doc_output_dest_dir = output_path_actual / "documentation"
                    shutil.copytree(
                        final_doc_output_source_dir,
                        doc_output_dest_dir,
                        dirs_exist_ok=True,
                    )
                    logger.info(
                        f"Copied generated documentation from {final_doc_output_source_dir} to {doc_output_dest_dir}."
                    )
                    span.add_event("Generated documentation copied")

            # --- Provenance and Feedback Phase ---
            span.add_event("Logging provenance and feedback")
            # Log provenance, but the TaskResult contains the results dict
            await self._add_provenance(parsed_results, task_id)
            self._run_feedback(parsed_results, task_id)

            final_results = parsed_results

            self._update_task_status(
                task_id, "completed", results=final_results, finished_at=time.time()
            )
            span.set_status(Status(StatusCode.OK))
            return TaskResult(
                task_id=task_id,
                status="completed",
                results=final_results,
                started_at=self.task_status_map[task_id].started_at,
                finished_at=time.time(),
                tags=task_payload.tags,
                environment=task_payload.environment,
            )

        except TimeoutError as e:  # Re-raise TimeoutError directly
            self._update_task_status(
                task_id, "timed_out", error=e.as_dict(), finished_at=time.time()
            )
            logger.error(
                f"Test run for task {task_id} timed out: {e.detail}", exc_info=True
            )
            span.set_status(
                Status(StatusCode.ERROR, f"Test run timed out: {e.error_code}")
            )
            span.record_exception(e)
            return self.task_status_map[task_id]
        except RunnerError as e:  # Catch all other structured Runner errors
            self._update_task_status(
                task_id, "failed", error=e.as_dict(), finished_at=time.time()
            )
            logger.error(
                f"Test run for task {task_id} failed with RunnerError: {e.as_dict()}",
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.ERROR, f"Test run failed: {e.error_code}")
            )
            span.record_exception(e)
            return self.task_status_map[task_id]
        except Exception as e:
            error_dict = RunnerError(
                error_codes["UNEXPECTED_ERROR"],
                f"An unexpected error occurred during test run: {e}",
                task_id=task_id,
                cause=e,
            ).as_dict()
            self._update_task_status(
                task_id, "failed", error=error_dict, finished_at=time.time()
            )
            logger.error(
                f"Test run for task {task_id} failed with unexpected error: {error_dict}",
                exc_info=True,
            )
            span.set_status(Status(StatusCode.ERROR, f"Unexpected error: {e}"))
            span.record_exception(e)
            return self.task_status_map[task_id]
        finally:
            if temp_dir_obj:
                try:
                    temp_dir_obj.cleanup()
                    logger.debug(f"Cleaned up temporary directory: {temp_dir_path}")
                except Exception as cleanup_e:
                    logger.warning(
                        f"Failed to cleanup temporary directory {temp_dir_path}: {cleanup_e}",
                        exc_info=True,
                    )
            elif temp_dir_path and temp_dir_path.exists():
                try:
                    shutil.rmtree(temp_dir_path, ignore_errors=True)
                    logger.debug(
                        f"Cleaned up temporary directory (fallback): {temp_dir_path}"
                    )
                except Exception as cleanup_e:
                    logger.warning(
                        f"Failed to cleanup temporary directory (fallback) {temp_dir_path}: {cleanup_e}",
                        exc_info=True,
                    )

    async def enqueue(self, task_payload: TaskPayload) -> TaskResult:
        if task_payload.task_id is None:
            task_payload.task_id = str(uuid.uuid4())

        # [FIX] Create the initial TaskResult in 'enqueued' state as per test requirements
        self._update_task_status(
            task_payload.task_id,
            "enqueued",
            started_at=time.time(),  # Use 'started_at' for enqueue time
            tags=task_payload.tags,
            environment=task_payload.environment,
        )

        heapq.heappush(self.queue, PrioritizedTask(task_payload.priority, task_payload))
        logger.info(
            json.dumps(
                {
                    "event": "task_enqueued",
                    "task_id": task_payload.task_id,
                    "priority": task_payload.priority,
                    "queue_size": len(self.queue),
                }
            )
        )
        self._persist_queue()
        return self.task_status_map[
            task_payload.task_id
        ]  # Return the initial TaskResult


# --- Async Function for Parallel Runs ---
async def parallel_runs(
    runner: "Runner", tasks: List[TaskPayload], priorities: Optional[List[int]] = None
) -> List[TaskResult]:
    if priorities is None:
        priorities = [0] * len(tasks)

    enqueued_task_results: List[TaskResult] = []
    for task, prio in zip(tasks, priorities):
        if task.task_id is None:
            task.task_id = f"parallel_task_{runner.instance_id}_{str(uuid.uuid4())[:8]}"
        task.priority = prio
        # Enqueue and get the initial TaskResult directly from runner.enqueue
        enqueued_task_results.append(await runner.enqueue(task))

    start_time = time.time()
    # FIX: Use getattr
    max_wait_time = getattr(runner.config, "max_parallel_wait_time_seconds", 60)

    completed_task_results_ids: set[str] = (
        set()
    )  # Track IDs of completed tasks to avoid re-checking

    while True:
        all_tasks_processed = True
        for enq_task_result in enqueued_task_results:
            if (
                enq_task_result.task_id in completed_task_results_ids
            ):  # Already processed
                continue

            current_status = runner.get_task_status(enq_task_result.task_id)
            if current_status and current_status.status in [
                "completed",
                "failed",
                "timed_out",
            ]:
                completed_task_results_ids.add(enq_task_result.task_id)
            else:
                all_tasks_processed = False

        if all_tasks_processed:
            logger.info(f"All {len(tasks)} parallel tasks have completed or failed.")
            break

        if time.time() - start_time > max_wait_time:
            logger.warning(
                f"Parallel runs timed out after {max_wait_time}s. {len(runner.queue)} tasks remaining in queue."
            )
            break

        logger.debug(
            f"Waiting for parallel tasks to process. Queue size: {len(runner.queue)}. Completed so far: {len(completed_task_results_ids)}"
        )
        # FIX: Use getattr
        await asyncio.sleep(getattr(runner.config, "dist_poll_interval_seconds", 2))

    final_results: List[TaskResult] = []
    for enq_task_result in enqueued_task_results:
        final_results.append(
            runner.get_task_status(enq_task_result.task_id) or enq_task_result
        )

    logger.info("Parallel runs execution finished. Final status collected.")
    return final_results


async def run_tests(
    test_files: Dict[str, str],
    code_files: Dict[str, str],
    temp_path: str,
    language: str = "python",
    framework: str = "pytest",
    timeout: int = 300,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute tests in an isolated environment.

    This is a convenience wrapper around run_tests_in_sandbox for simpler usage.

    Args:
        test_files: Dictionary of test filename -> content
        code_files: Dictionary of code filename -> content
        temp_path: Temporary directory for execution
        language: Programming language (default: python)
        framework: Test framework to use (default: pytest)
        timeout: Execution timeout in seconds

    Returns:
        Dictionary with test results
    """
    return await run_tests_in_sandbox(
        code_files=code_files,
        test_files=test_files,
        temp_path=temp_path,
        language=language,
        coverage=kwargs.get("coverage", True),
        **kwargs,
    )


# --- ConfigWatcher Callback Integration ---
def _on_config_reload_callback(
    new_config: RunnerConfig,
    diff: Optional[Dict[str, Any]],
    runner_instance_ref: Optional["Runner"] = None,
) -> None:
    """
    Callback for ConfigWatcher to re-initialize parts of the Runner based on new config.
    This needs to be implemented carefully to avoid race conditions with active runs.
    """
    target_runner = (
        runner_instance_ref
        if runner_instance_ref is not None
        else globals().get("runner_instance")
    )

    if target_runner:
        logger.info(
            json.dumps(
                {
                    "event": "config_reload_callback_triggered",
                    "diff": diff,
                    "old_backend": target_runner.config.backend,
                    "new_backend": new_config.backend,
                    "instance_id": target_runner.instance_id,
                }
            )
        )

        async def _apply_config_update():
            async with reloading_lock:
                target_runner.config = new_config
                RUNNER_CONFIG_RELOADS.inc()
                RUNNER_CONFIG_VERSION.labels(version=str(new_config.version)).set(1)

                if "backend" in (diff or {}):
                    logger.info(
                        f"Backend changed from {target_runner.backend.__class__.__name__} to {new_config.backend}. Re-initializing backend."
                    )
                    try:
                        backend_cls = ALL_BACKENDS.get(new_config.backend)
                        if backend_cls:
                            target_runner.backend = backend_cls(new_config)
                            logger.info(
                                f"Backend successfully re-initialized to {new_config.backend}."
                            )
                        else:
                            logger.error(
                                f"New backend '{new_config.backend}' is not registered or unavailable. Keeping old backend."
                            )
                    except (
                        RunnerError
                    ) as e:  # Catch structured errors from backend init
                        logger.error(
                            f"Failed to re-initialize backend to {new_config.backend}: {e.as_dict()}. Keeping old backend.",
                            exc_info=True,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to re-initialize backend to {new_config.backend}: {e}. Keeping old backend.",
                            exc_info=True,
                        )

                if "parallel_workers" in (diff or {}):
                    logger.info(
                        f"Parallel workers changed to {new_config.parallel_workers}. Re-initializing executor."
                    )
                    target_runner.executor.shutdown(wait=True)
                    target_runner.executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=new_config.parallel_workers
                    )
                    logger.info("ThreadPoolExecutor re-initialized.")

                logger.info(
                    "Runner updated with new config settings (e.g., metrics intervals, strategies will adapt)."
                )

        asyncio.create_task(_apply_config_update())
    else:
        logger.warning(
            "Config reload callback triggered but runner_instance is not initialized."
        )


# --- API Setup (Requires FastAPI and uvicorn) ---
# This section assumes 'api' is an initialized FastAPI app and 'cli' is a Click group.
# For running, you would typically uncomment the necessary imports and define these objects.

try:
    import click
    from fastapi import Body, FastAPI, HTTPException, status
    from uvicorn import run as uvicorn_run

    API_AVAILABLE = True
except ImportError:
    logger.info(
        "FastAPI or Click not installed. API/CLI endpoints will not be defined."
    )

    # Define dummies to prevent NameErrors if __name__ == "__main__" is used
    class DummyFastAPI:
        pass

    api = DummyFastAPI()

    class DummyClickGroup:
        pass

    cli = DummyClickGroup()
    API_AVAILABLE = False


if API_AVAILABLE:
    # --- FIX: Replace @api.on_event with lifespan ---
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await startup_event()
        yield
        await shutdown_event()

    api = FastAPI(
        title="Runner API",
        description="API for running automated tests in isolated environments.",
        lifespan=lifespan,
    )
    cli = click.Group()

    # @api.on_event("startup") # DECORATOR REMOVED
    async def startup_event():
        """Initialize Runner on API startup."""
        global runner_instance
        try:
            config = load_config("runner.yaml")
            runner_instance = Runner(config)
            await runner_instance.start_services()  # [FIX] Call async start
            logger.info("Runner instance initialized on API startup.")

            # [FIX] Corrected import path from runner.config to runner.runner_config
            from runner.runner_config import ConfigWatcher

            config_watcher = ConfigWatcher(
                "runner.yaml",
                partial(
                    _on_config_reload_callback, runner_instance_ref=runner_instance
                ),
            )
            asyncio.create_task(config_watcher.start())
            logger.info("ConfigWatcher started for API server.")

        except Exception as e:
            logger.critical(
                f"Failed to initialize Runner or ConfigWatcher on startup: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                "API startup failed due to Runner initialization error."
            ) from e

    # @api.on_event("shutdown") # DECORATOR REMOVED
    async def shutdown_event():
        """Gracefully shut down Runner on API shutdown."""
        if runner_instance:
            logger.info("Runner API shutdown initiated.")
            await runner_instance.shutdown_services()  # [FIX] Call async shutdown

            if hasattr(runner_instance.backend, "close") and callable(
                runner_instance.backend.close
            ):
                if asyncio.iscoroutinefunction(runner_instance.backend.close):
                    await runner_instance.backend.close()
                else:
                    runner_instance.backend.close()
                logger.info("Runner backend resources closed.")
        logger.info("Runner API shutdown complete.")

    @api.post(
        "/run_test_task",
        summary="Run a single test task immediately or enqueue",
        response_model=TaskResult,
    )
    async def api_run_test_task(payload: TaskPayload = Body(...)):
        if runner_instance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runner service not initialized.",
            )

        if payload.task_id is None:
            payload.task_id = f"api_run_task_{uuid.uuid4()}"

        logger.info(
            json.dumps(
                {
                    "event": "api_run_task_received",
                    "task_id": payload.task_id,
                    "distributed": runner_instance.config.distributed,
                }
            )
        )

        try:
            if runner_instance.config.distributed:
                # In distributed mode, /run_test_task should still enqueue
                task_result = await runner_instance.enqueue(payload)
                return task_result
            else:
                # In non-distributed mode, run it directly
                results = await runner_instance.run_tests(payload)
                return results
        except RunnerError as e:
            logger.error(
                json.dumps({"event": "api_run_task_failed", "error": e.as_dict()}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=e.as_dict()
            )
        except Exception as e:
            error_dict = RunnerError(
                error_codes["UNEXPECTED_ERROR"],
                f"An unexpected API error occurred: {e}",
                task_id=payload.task_id,
                cause=e,
            ).as_dict()
            logger.error(
                json.dumps({"event": "api_run_task_failed", "error": error_dict}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_dict
            )

    @api.post(
        "/enqueue_test_task",
        summary="Enqueue a test task for asynchronous execution",
        response_model=TaskResult,
    )
    async def api_enqueue_test_task(payload: TaskPayload = Body(...)):
        if runner_instance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runner service not initialized.",
            )

        if payload.task_id is None:
            payload.task_id = f"api_enq_task_{uuid.uuid4()}"

        logger.info(
            json.dumps(
                {"event": "api_enqueue_task_received", "task_id": payload.task_id}
            )
        )

        try:
            task_result = await runner_instance.enqueue(payload)
            return task_result
        except RunnerError as e:
            logger.error(
                json.dumps({"event": "api_enqueue_task_failed", "error": e.as_dict()}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=e.as_dict()
            )
        except Exception as e:
            error_dict = RunnerError(
                error_codes["UNEXPECTED_ERROR"],
                f"An unexpected API error occurred: {e}",
                task_id=payload.task_id,
                cause=e,
            ).as_dict()
            logger.error(
                json.dumps({"event": "api_enqueue_task_failed", "error": error_dict}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_dict
            )

    @api.post(
        "/enqueue_test_task_batch",
        summary="Enqueue a batch of test tasks for asynchronous execution",
        response_model=Dict[str, Any],
    )
    async def api_enqueue_test_task_batch(batch_payload: BatchTaskPayload = Body(...)):
        if runner_instance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runner service not initialized.",
            )

        enqueued_task_ids = []
        try:
            for task_payload in batch_payload.tasks:
                if task_payload.task_id is None:
                    task_payload.task_id = f"api_batch_task_{runner_instance.instance_id}_{str(uuid.uuid4())[:8]}"
                await runner_instance.enqueue(task_payload)
                enqueued_task_ids.append(task_payload.task_id)

            logger.info(
                json.dumps(
                    {
                        "event": "api_batch_enqueue_completed",
                        "count": len(batch_payload.tasks),
                        "task_ids": enqueued_task_ids,
                    }
                )
            )
            return {
                "status": "batch_enqueued",
                "task_ids": enqueued_task_ids,
                "count": len(batch_payload.tasks),
            }
        except RunnerError as e:
            logger.error(
                json.dumps({"event": "api_batch_enqueue_failed", "error": e.as_dict()}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=e.as_dict()
            )
        except Exception as e:
            error_dict = RunnerError(
                error_codes["UNEXPECTED_ERROR"],
                f"An unexpected API error occurred during batch enqueue: {e}",
                cause=e,
            ).as_dict()
            logger.error(
                json.dumps({"event": "api_batch_enqueue_failed", "error": error_dict}),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_dict
            )

    @api.get(
        "/task_status/{task_id}",
        summary="Get the current status of a test task",
        response_model=TaskResult,
    )
    async def api_get_task_status(task_id: str):
        if runner_instance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runner service not initialized.",
            )

        status_result = runner_instance.get_task_status(task_id)
        if status_result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task ID '{task_id}' not found.",
            )

        logger.info(
            json.dumps(
                {
                    "event": "api_get_task_status",
                    "task_id": task_id,
                    "status": status_result.status,
                }
            )
        )
        return status_result

    @api.get(
        "/metrics_data",
        summary="Get current Prometheus metrics",
        response_model=Dict[str, Any],
    )
    async def api_metrics_data():
        return get_metrics_dict()

    # --- CLI (Requires Click) ---
    import sys

    import click

    @click.group()
    def cli():
        """Runner CLI entry point."""
        pass

    @cli.command(name="run", help="Run a single test task immediately.")
    @click.option(
        "--config",
        default="runner.yaml",
        help="Path to runner config file.",
        type=click.Path(exists=True, path_type=Path),
    )
    @click.option(
        "--test-dir",
        type=click.Path(exists=True, path_type=Path),
        help="Directory containing test files.",
    )
    @click.option(
        "--code-dir",
        type=click.Path(exists=True, path_type=Path),
        help="Directory containing code files.",
    )
    @click.option(
        "--output-path",
        default="./runner_output",
        type=click.Path(path_type=Path),
        help="Path for output results.",
    )
    @click.option("--dry-run", is_flag=True, help="Simulate the test run.")
    @click.option("--timeout", type=int, help="Override default timeout in seconds.")
    @click.option("--task-id", type=str, help="Optional task ID.")
    def cli_run(
        config: Path,
        test_dir: Path,
        code_dir: Path,
        output_path: Path,
        dry_run: bool,
        timeout: Optional[int],
        task_id: Optional[str],
    ):

        async def main_run():
            runner_config = load_config(str(config))
            runner = Runner(runner_config)
            await runner.start_services()  # [FIX] Start services

            test_file_contents = {
                f.name: f.read_text(encoding="utf-8")
                for f in test_dir.iterdir()
                if f.is_file()
            }
            code_file_contents = {
                f.name: f.read_text(encoding="utf-8")
                for f in code_dir.iterdir()
                if f.is_file()
            }

            output_path.mkdir(parents=True, exist_ok=True)

            task_payload = TaskPayload(
                test_files=test_file_contents,
                code_files=code_file_contents,
                output_path=str(output_path),
                timeout=timeout,
                dry_run=dry_run,
                task_id=task_id if task_id else f"cli_run_task_{uuid.uuid4()}",
            )

            try:
                results = await runner.run_tests(task_payload)
                click.echo(json.dumps(results.model_dump(), indent=2))
            except RunnerError as e:
                click.echo(f"Error: {json.dumps(e.as_dict(), indent=2)}", err=True)
                sys.exit(1)
            except Exception as e:
                click.echo(f"Unexpected error: {e}", err=True)
                sys.exit(1)
            finally:
                await runner.shutdown_services()  # [FIX] Shutdown services

        asyncio.run(main_run())

    @cli.command(name="enqueue", help="Enqueue a test task for asynchronous execution.")
    @click.option(
        "--config",
        default="runner.yaml",
        help="Path to runner config file.",
        type=click.Path(exists=True, path_type=Path),
    )
    @click.option(
        "--test-dir",
        type=click.Path(exists=True, path_type=Path),
        help="Directory containing test files.",
    )
    @click.option(
        "--code-dir",
        type=click.Path(exists=True, path_type=Path),
        help="Directory containing code files.",
    )
    @click.option(
        "--output-path",
        default="./runner_output",
        type=click.Path(path_type=Path),
        help="Path for output results.",
    )
    @click.option(
        "--priority",
        default=0,
        type=int,
        help="Priority of the task (lower is higher priority).",
    )
    @click.option("--dry-run", is_flag=True, help="Simulate the test run.")
    @click.option("--timeout", type=int, help="Override default timeout in seconds.")
    @click.option("--task-id", type=str, help="Optional task ID.")
    def cli_enqueue(
        config: Path,
        test_dir: Path,
        code_dir: Path,
        output_path: Path,
        priority: int,
        dry_run: bool,
        timeout: Optional[int],
        task_id: Optional[str],
    ):

        async def main_enqueue():
            runner_config = load_config(str(config))
            runner = Runner(runner_config)
            await runner.start_services()  # [FIX] Start services (needed for workers)

            test_file_contents = {
                f.name: f.read_text(encoding="utf-8")
                for f in test_dir.iterdir()
                if f.is_file()
            }
            code_file_contents = {
                f.name: f.read_text(encoding="utf-8")
                for f in code_dir.iterdir()
                if f.is_file()
            }

            output_path.mkdir(parents=True, exist_ok=True)

            task_payload = TaskPayload(
                test_files=test_file_contents,
                code_files=code_file_contents,
                output_path=str(output_path),
                timeout=timeout,
                dry_run=dry_run,
                priority=priority,
                task_id=task_id if task_id else f"cli_enq_task_{uuid.uuid4()}",
            )
            try:
                await runner.enqueue(task_payload)
                click.echo(
                    f"Task {task_payload.task_id} enqueued with priority {priority}."
                )
            except RunnerError as e:
                click.echo(f"Error: {json.dumps(e.as_dict(), indent=2)}", err=True)
                sys.exit(1)
            except Exception as e:
                click.echo(f"Unexpected error: {e}", err=True)
                sys.exit(1)
            finally:
                await runner.shutdown_services()  # [FIX] Shutdown services

        asyncio.run(main_enqueue())

    @cli.command(name="gui", help="Launch the Runner TUI (Textual User Interface).")
    @click.option("--config", default="runner.yaml", help="Path to runner config file.")
    @click.option("--prod", is_flag=True, help="Run GUI in production mode.")
    def cli_gui(config: str, prod: bool):
        try:
            from runner.runner_app import RunnerApp as MainApp

            app = MainApp(config_path=config, production_mode=prod)
            app.run()
        except ImportError:
            click.echo(
                "Failed to import runner.runner_app. Ensure Textual and its dependencies are installed.",
                err=True,
            )
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error launching TUI: {e}", err=True)
            sys.exit(1)

    @cli.command(name="api-server", help="Start the Runner API server.")
    @click.option("--host", default="0.0.0.0", help="Host to bind the API server to.")
    @click.option(
        "--port", default=8000, type=int, help="Port to run the API server on."
    )
    def cli_api_server(host: str, port: int):
        try:
            from uvicorn import run as uvicorn_run

            # Assumes 'api' is defined in this file
            uvicorn_run(
                "runner.core:api", host=host, port=port, reload=True
            )  # Use string import for uvicorn
        except ImportError:
            click.echo(
                "Failed to import uvicorn or FastAPI. Install with 'pip install uvicorn fastapi'.",
                err=True,
            )
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error starting API server: {e}", err=True)
            sys.exit(1)

    # if __name__ == "__main__":
    #     cli()
