import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

# Pydantic for input validation and configuration
try:
    from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Pydantic not available. Configuration and input validation will be skipped in runners.py."
    )

# --- Metrics (Idempotent and Thread-Safe Registration) ---
try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

    PROMETHEUS_AVAILABLE = True
    _metrics_registry = CollectorRegistry(auto_describe=True)
    _metrics_lock = threading.Lock()

    def get_or_create_metric(metric_type, name, documentation, labelnames=None, buckets=None):
        if labelnames is None:
            labelnames = ()
        with _metrics_lock:
            try:
                existing_metric = _metrics_registry._names_to_collectors[name]
                if isinstance(existing_metric, metric_type):
                    return existing_metric
                else:
                    runners_logger.warning(
                        f"Metric '{name}' already registered with a different type. Reusing existing."
                    )
                    return existing_metric
            except KeyError:
                if metric_type == Histogram:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        buckets=buckets or Histogram.DEFAULT_BUCKETS,
                        registry=_metrics_registry,
                    )
                elif metric_type == Counter:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                elif metric_type == Gauge:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                else:
                    raise ValueError(f"Unsupported metric type: {metric_type}")
                return metric

    runners_logger = logging.getLogger("simulation.runners")

    RUNNER_METRICS = {
        "runner_execution_total": get_or_create_metric(
            Counter,
            "runner_execution_total",
            "Total runner executions",
            ["runner_type", "status"],
        ),
        "runner_errors_total": get_or_create_metric(
            Counter,
            "runner_errors_total",
            "Total runner errors",
            ["runner_type", "error_type"],
        ),
        "runner_duration_seconds": get_or_create_metric(
            Histogram,
            "runner_duration_seconds",
            "Runner execution duration in seconds",
            ["runner_type"],
        ),
        "runner_dependency_missing": get_or_create_metric(
            Counter,
            "runner_dependency_missing_total",
            "Total times a runner dependency was missing",
            ["runner_type", "dependency"],
        ),
        "runner_health": get_or_create_metric(
            Gauge,
            "runner_health",
            "Health status of runners (1=healthy, 0=unhealthy)",
            ["runner_type"],
        ),
        "subprocess_latency_seconds": get_or_create_metric(
            Histogram,
            "runner_subprocess_latency_seconds",
            "Subprocess execution latency",
            ["runner_type"],
        ),
    }
except ImportError:
    PROMETHEUS_AVAILABLE = False
    runners_logger = logging.getLogger("simulation.runners")
    runners_logger.warning(
        "Prometheus client not available. Metrics will not be collected in runners.py."
    )
    RUNNER_METRICS = {}

# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
runners_logger.info(f"PRODUCTION_MODE is set to: {PRODUCTION_MODE}")

# --- DLT Integration ---
try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
    from test_generation.audit_log import AuditLogger as DLTLogger

    DLT_LOGGER_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    ClientError = None
    DLT_LOGGER_AVAILABLE = False
    DLTLogger = None
    runners_logger.warning(
        "DLT/Boto3 not available. Audit logging and secure credential handling will be disabled."
    )

try:
    from tenacity import reraise, retry, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        def wrap(f):
            return f

        return wrap

    def stop_after_attempt(x):
        return None

    def wait_exponential(*args, **kwargs):
        return None


runners_logger.setLevel(logging.INFO)
if not runners_logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    runners_logger.addHandler(handler)


# Configuration constants
ASYNC_EXECUTION_TIMEOUT_SECONDS = int(
    os.getenv("RUNNER_ASYNC_EXECUTION_TIMEOUT", "300")
)  # Default 5 minutes


def alert_operator(message: str, level: str = "CRITICAL"):
    runners_logger.critical(f"[OPS ALERT - {level}] {message}")


_runners: Dict[str, Callable] = {}
_runner_dependencies: Dict[str, List[str]] = {}


def register_runner(name: str, dependencies: Optional[List[str]] = None):
    def decorator(func: Callable):
        if name in _runners:
            runners_logger.warning(f"Runner '{name}' already registered. Overwriting.")
        _runners[name] = func
        _runner_dependencies[name] = dependencies or []
        runners_logger.info(f"Registered runner: {name}")
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_health"].labels(runner_type=name).set(1)
        return func

    return decorator


def check_runner_dependencies(runner_name: str) -> bool:
    dependencies = _runner_dependencies.get(runner_name, [])
    missing_deps = []
    for dep in dependencies:
        try:
            __import__(dep)
        except ImportError:
            missing_deps.append(dep)

    if missing_deps:
        runners_logger.error(
            f"Missing dependencies for runner '{runner_name}': {', '.join(missing_deps)}. This runner will not function."
        )
        if PROMETHEUS_AVAILABLE:
            for dep in missing_deps:
                RUNNER_METRICS["runner_dependency_missing"].labels(
                    runner_type=runner_name, dependency=dep
                ).inc()
            RUNNER_METRICS["runner_health"].labels(runner_type=runner_name).set(0)
        return False

    if PROMETHEUS_AVAILABLE:
        RUNNER_METRICS["runner_health"].labels(runner_type=runner_name).set(1)
    return True


if PYDANTIC_AVAILABLE:

    class PythonRunnerConfig(BaseModel):
        script_path: str = Field(
            ..., min_length=1, description="Path to the Python script to execute."
        )
        args: List[str] = Field(
            default_factory=list, description="Command-line arguments for the script."
        )
        env_vars: Dict[str, str] = Field(
            default_factory=dict,
            description="Environment variables for the subprocess.",
        )
        timeout_seconds: int = Field(
            300, ge=1, description="Timeout for script execution in seconds."
        )
        allow_network: bool = False

    class ContainerRunnerConfig(BaseModel):
        image_name: str = Field(..., pattern=r"^[a-zA-Z0-9._/\-:]+$")
        command: List[str] = Field(
            default_factory=list, description="Command to execute inside the container."
        )
        args: List[str] = Field(
            default_factory=list, description="Arguments for the container command."
        )
        env_vars: Dict[str, str] = Field(
            default_factory=dict, description="Environment variables for the container."
        )
        timeout_seconds: int = Field(
            600, ge=1, description="Timeout for container execution in seconds."
        )
        resource_limits: Dict[str, Any] = Field(
            default_factory=dict, description="CPU/memory limits for the container."
        )
        allow_network: bool = False

        @field_validator("image_name")
        def prevent_invalid_image(cls, v):
            if ":" not in v:
                raise ValueError("Image name must include a tag (e.g., 'image:latest')")
            return v

    class AgentConfig(BaseModel):
        runner_type: str = Field(
            ...,
            description="The type of runner to use (e.g., 'python_script', 'container').",
        )
        runner_config: Dict[str, Any] = Field(
            default_factory=dict,
            description="Configuration specific to the chosen runner type.",
        )
        user_id: str = Field("system", description="ID of the user initiating the run.")
        job_id: str = Field(
            default_factory=lambda: f"job-{os.urandom(4).hex()}",
            description="Unique ID for the job.",
        )

        @field_validator("runner_config")
        def validate_runner_config_type(cls, v, info: ValidationInfo):
            runner_type = info.data.get("runner_type")
            if runner_type == "python_script":
                return PythonRunnerConfig(**v).model_dump()
            elif runner_type == "container":
                return ContainerRunnerConfig(**v).model_dump()
            return v

else:

    class PythonRunnerConfig:
        def __init__(self, **kwargs):
            self.script_path = kwargs.get("script_path", "")
            self.args = kwargs.get("args", [])
            self.env_vars = kwargs.get("env_vars", {})
            self.timeout_seconds = kwargs.get("timeout_seconds", 300)
            self.allow_network = kwargs.get("allow_network", False)

    class ContainerRunnerConfig:
        def __init__(self, **kwargs):
            self.image_name = kwargs.get("image_name", "")
            self.command = kwargs.get("command", [])
            self.args = kwargs.get("args", [])
            self.env_vars = kwargs.get("env_vars", {})
            self.timeout_seconds = kwargs.get("timeout_seconds", 600)
            self.resource_limits = kwargs.get("resource_limits", {})
            self.allow_network = kwargs.get("allow_network", False)

    class AgentConfig:
        def __init__(self, **kwargs):
            self.runner_type = kwargs.get("runner_type", "")
            self.runner_config = kwargs.get("runner_config", {})
            self.user_id = kwargs.get("user_id", "system")
            self.job_id = kwargs.get("job_id", f"job-{os.urandom(4).hex()}")


@contextmanager
def time_metric(metric, labels):
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        metric.labels(**labels).observe(duration)


def _load_docker_credentials() -> Dict[str, str]:
    if not BOTO3_AVAILABLE:
        raise RuntimeError("Boto3 is not available for secure credential loading.")
    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId="simulation/docker-credentials")
        return json.loads(response["SecretString"])
    except ClientError as e:
        runners_logger.critical(f"Failed to load Docker credentials: {e}")
        raise
    except Exception as e:
        runners_logger.critical(f"Unexpected error loading Docker credentials: {e}", exc_info=True)
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _execute_subprocess_safely(
    command: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    user: Optional[str] = None,
    resource_limits: Optional[Dict[str, Any]] = None,
    allow_network: bool = False,
    job_id: str = "N/A",
    user_id: str = "N/A",
    runner_type: str = "N/A",
) -> Dict[str, Any]:
    full_env = os.environ.copy()
    if env:
        redacted_env = {
            k: (
                "[REDACTED]"
                if any(s in k.lower() for s in ["key", "secret", "token", "password"])
                else v
            )
            for k, v in env.items()
        }
        runners_logger.info(f"Subprocess environment variables (redacted): {redacted_env}")
        full_env.update(env)

    runners_logger.info(
        f"Launching subprocess: runner_type='{runner_type}', job_id='{job_id}', user_id='{user_id}', "
        f"command='{' '.join(command)}', timeout={timeout}s, user='{user}', "
        f"resource_limits={resource_limits}, allow_network={allow_network}"
    )

    start_time = time.time()
    try:
        if PROMETHEUS_AVAILABLE:
            with time_metric(
                RUNNER_METRICS["subprocess_latency_seconds"],
                {"runner_type": runner_type},
            ):
                process = subprocess.run(
                    command,
                    env=full_env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=timeout,
                )
        else:
            process = subprocess.run(
                command,
                env=full_env,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
        duration = time.time() - start_time
        runners_logger.info(
            f"Subprocess completed: runner_type='{runner_type}', job_id='{job_id}', "
            f"return_code={process.returncode}, duration={duration:.2f}s"
        )
        runners_logger.debug(f"Subprocess stdout: {process.stdout}")
        runners_logger.debug(f"Subprocess stderr: {process.stderr}")
        return {
            "status": "SUCCESS",
            "stdout": process.stdout,
            "stderr": process.stderr,
            "return_code": process.returncode,
            "duration": duration,
        }
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        runners_logger.error(
            f"Subprocess timed out: runner_type='{runner_type}', job_id='{job_id}', "
            f"timeout={timeout}s, duration={duration:.2f}s. Stderr: {e.stderr}"
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="timeout"
            ).inc()
        return {
            "status": "TIMEOUT",
            "stdout": e.stdout,
            "stderr": e.stderr,
            "error": f"Command timed out after {timeout} seconds.",
            "duration": duration,
        }
    except subprocess.CalledProcessError as e:
        duration = time.time() - start_time
        runners_logger.error(
            f"Subprocess failed with non-zero exit code: runner_type='{runner_type}', job_id='{job_id}', "
            f"return_code={e.returncode}, duration={duration:.2f}s. Stderr: {e.stderr}"
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="subprocess_error"
            ).inc()
        raise e
    except FileNotFoundError:
        duration = time.time() - start_time
        runners_logger.error(
            f"Command not found: runner_type='{runner_type}', job_id='{job_id}'. "
            f"Check if '{command[0]}' is in PATH. Duration={duration:.2f}s."
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="command_not_found"
            ).inc()
        return {
            "status": "FAILED",
            "error": f"Command '{command[0]}' not found.",
            "duration": duration,
        }
    except Exception as e:
        duration = time.time() - start_time
        runners_logger.error(
            f"An unexpected error occurred during subprocess execution: runner_type='{runner_type}', job_id='{job_id}', "
            f"error={e}. Duration={duration:.2f}s.",
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="unexpected_error"
            ).inc()
        return {
            "status": "ERROR",
            "error": f"An unexpected error occurred: {e}",
            "duration": duration,
        }


def _perform_integrity_check(file_path: str, expected_mime_type: Optional[str] = None) -> bool:
    if not os.path.exists(file_path):
        runners_logger.error(f"Integrity check failed: File not found at {file_path}")
        return False
    if expected_mime_type and "." in file_path:
        ext = file_path.split(".")[-1].lower()
        if "python" in expected_mime_type.lower() and ext not in ["py", "pyc", "pyo"]:
            runners_logger.warning(
                f"File extension mismatch for {file_path}. Expected Python, got .{ext}."
            )
    runners_logger.info(
        f"Performing conceptual integrity check for {file_path}. (Actual checks not implemented)"
    )
    return True


@register_runner("python_script", dependencies=["sys", "subprocess"])
def run_python_script(config: PythonRunnerConfig, job_id: str, user_id: str) -> Dict[str, Any]:
    if not check_runner_dependencies("python_script"):
        return {
            "status": "DEPENDENCY_MISSING",
            "error": "Required Python environment for script execution is not available.",
        }

    if not _perform_integrity_check(config.script_path, expected_mime_type="text/x-python"):
        runners_logger.error(
            f"Integrity check failed for Python script: {config.script_path}. Aborting."
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type="python_script", error_type="integrity_check_failed"
            ).inc()
        return {
            "status": "SECURITY_ERROR",
            "error": "Integrity check failed for script.",
        }

    command = [sys.executable, config.script_path] + config.args

    start_time = time.time()
    try:
        result = _execute_subprocess_safely(
            command=command,
            env=config.env_vars,
            timeout=config.timeout_seconds,
            job_id=job_id,
            user_id=user_id,
            runner_type="python_script",
            allow_network=config.allow_network,
        )
    except subprocess.CalledProcessError as e:
        result = {
            "status": "FAILED",
            "return_code": e.returncode,
            "error": f"Command failed with exit code {e.returncode}.",
        }

    duration = time.time() - start_time
    if PROMETHEUS_AVAILABLE:
        RUNNER_METRICS["runner_duration_seconds"].labels(runner_type="python_script").observe(
            duration
        )
    if result["status"] == "SUCCESS":
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_execution_total"].labels(
                runner_type="python_script", status="success"
            ).inc()
    else:
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_execution_total"].labels(
                runner_type="python_script", status="failed"
            ).inc()
        alert_operator(
            f"Python script runner failed for job {job_id}: {result.get('error', 'Unknown error')}",
            level="ERROR",
        )
    return result


@register_runner("container", dependencies=["subprocess"])
def run_container(config: ContainerRunnerConfig, job_id: str, user_id: str) -> Dict[str, Any]:
    if not check_runner_dependencies("container"):
        return {
            "status": "DEPENDENCY_MISSING",
            "error": "Required container runtime environment is not available.",
        }

    try:
        if BOTO3_AVAILABLE:
            _ = _load_docker_credentials()  # Load credentials for use, if needed by docker
    except Exception as e:
        runners_logger.error(f"Failed to load container credentials: {e}")
        return {
            "status": "AUTH_ERROR",
            "error": "Failed to load container credentials.",
        }

    command = ["docker", "run", "--rm"]

    if not config.allow_network:
        command.append("--network=none")

    if config.resource_limits:
        if "cpu" in config.resource_limits:
            command.extend(["--cpus", str(config.resource_limits["cpu"])])
        if "memory" in config.resource_limits:
            command.extend(["--memory", str(config.resource_limits["memory"])])

    command.extend(["--security-opt", "no-new-privileges", "--cap-drop", "ALL"])

    for k, v in config.env_vars.items():
        command.extend(["-e", f"{k}={v}"])

    command.append(config.image_name)
    command.extend(config.command)
    command.extend(config.args)

    start_time = time.time()
    try:
        result = _execute_subprocess_safely(
            command=command,
            env={},
            timeout=config.timeout_seconds,
            job_id=job_id,
            user_id=user_id,
            runner_type="container",
            allow_network=config.allow_network,
        )
    except subprocess.CalledProcessError as e:
        result = {
            "status": "FAILED",
            "return_code": e.returncode,
            "error": f"Command failed with exit code {e.returncode}.",
        }

    duration = time.time() - start_time
    if PROMETHEUS_AVAILABLE:
        RUNNER_METRICS["runner_duration_seconds"].labels(runner_type="container").observe(duration)
    if result["status"] == "SUCCESS":
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_execution_total"].labels(
                runner_type="container", status="success"
            ).inc()
    else:
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_execution_total"].labels(
                runner_type="container", status="failed"
            ).inc()
        alert_operator(
            f"Container runner failed for job {job_id}: {result.get('error', 'Unknown error')}",
            level="ERROR",
        )
    return result


def run_agent(agent_config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if PYDANTIC_AVAILABLE:
            validated_config = AgentConfig(**agent_config)
            runner_type = validated_config.runner_type
            runner_config_data = validated_config.runner_config
            job_id = validated_config.job_id
            user_id = validated_config.user_id
        else:
            runners_logger.warning("Pydantic not available. Skipping AgentConfig validation.")
            runner_type = agent_config.get("runner_type")
            runner_config_data = agent_config.get("runner_config", {})
            job_id = agent_config.get("job_id", f"job-{os.urandom(4).hex()}")
            user_id = agent_config.get("user_id", "system")
            if not runner_type:
                raise ValueError("runner_type must be specified in agent_config.")

    except ValidationError as e:
        runners_logger.error(f"Agent configuration validation failed: {e}")
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type="agent_config", error_type="validation_error"
            ).inc()
        alert_operator(f"Agent configuration validation failed: {e}", level="ERROR")
        return {
            "status": "ERROR",
            "message": f"Agent configuration validation failed: {e}",
        }
    except ValueError as e:
        runners_logger.error(f"Agent configuration validation failed: {e}")
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type="agent_config", error_type="validation_error"
            ).inc()
        alert_operator(f"Agent configuration validation failed: {e}", level="ERROR")
        return {
            "status": "ERROR",
            "message": f"Agent configuration validation failed: {e}",
        }

    runner_func = _runners.get(runner_type)
    if not runner_func:
        runners_logger.error(
            f"Unknown runner type: {runner_type}. Available runners: {list(_runners.keys())}"
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="unknown_runner_type"
            ).inc()
        alert_operator(
            f"Unknown runner type '{runner_type}' requested for job {job_id}.",
            level="CRITICAL",
        )
        return {"status": "ERROR", "message": f"Unknown runner type: {runner_type}"}

    if not check_runner_dependencies(runner_type):
        runners_logger.error(
            f"Runner '{runner_type}' cannot be executed due to missing dependencies."
        )
        alert_operator(
            f"Runner '{runner_type}' missing dependencies for job {job_id}.",
            level="CRITICAL",
        )
        return {
            "status": "ERROR",
            "message": f"Runner '{runner_type}' missing dependencies.",
        }

    runners_logger.info(
        f"Executing agent with runner '{runner_type}' for job '{job_id}' by user '{user_id}'."
    )

    dlt_logger = DLTLogger.from_environment() if DLT_LOGGER_AVAILABLE else None

    async def _execute_with_audit():
        if dlt_logger:
            await dlt_logger.add_entry(
                kind="runner",
                name="agent_execution",
                detail={
                    "runner_type": runner_type,
                    "job_id": job_id,
                    "user_id": user_id,
                },
                agent_id="runner",
                correlation_id=f"runner-{job_id}",
            )

        runner_config_obj = None
        if PYDANTIC_AVAILABLE:
            if runner_type == "python_script":
                runner_config_obj = PythonRunnerConfig(**runner_config_data)
            elif runner_type == "container":
                runner_config_obj = ContainerRunnerConfig(**runner_config_data)
        else:
            if runner_type == "python_script":
                runner_config_obj = PythonRunnerConfig(**runner_config_data)
            elif runner_type == "container":
                runner_config_obj = ContainerRunnerConfig(**runner_config_data)

        if runner_config_obj:
            result = runner_func(runner_config_obj, job_id=job_id, user_id=user_id)
        else:
            result = runner_func(runner_config_data, job_id=job_id, user_id=user_id)

        if dlt_logger and result:
            await dlt_logger.add_entry(
                kind="runner",
                name="agent_execution_completed",
                detail={
                    "runner_type": runner_type,
                    "job_id": job_id,
                    "status": result.get("status"),
                },
                agent_id="runner",
                correlation_id=f"runner-{job_id}",
            )
        return result

    try:
        # This part is tricky because run_agent is synchronous but calls an async function.
        # This implementation assumes it might be called from an existing loop or needs to start one.
        try:
            loop = asyncio.get_running_loop()
            # If a loop is running, we can't use run_until_complete or asyncio.run()
            # We need to use run_coroutine_threadsafe and wait for the result
            if loop.is_running():
                # Run the coroutine in the existing loop and wait for the result
                # This is the correct pattern for calling async code from sync context with an active loop
                future = asyncio.run_coroutine_threadsafe(_execute_with_audit(), loop)
                # Wait for the future with a configurable timeout
                return future.result(timeout=ASYNC_EXECUTION_TIMEOUT_SECONDS)
            else:
                # Loop exists but isn't running, use run_until_complete
                return loop.run_until_complete(_execute_with_audit())
        except RuntimeError:  # No running loop
            # No loop exists, create a new one with asyncio.run()
            return asyncio.run(_execute_with_audit())

    except Exception as e:
        runners_logger.error(
            f"Unhandled exception during execution of runner '{runner_type}' for job '{job_id}': {e}",
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE:
            RUNNER_METRICS["runner_errors_total"].labels(
                runner_type=runner_type, error_type="unhandled_exception"
            ).inc()
        alert_operator(
            f"Unhandled exception in runner '{runner_type}' for job {job_id}: {e}",
            level="CRITICAL",
        )

        if DLT_LOGGER_AVAILABLE:
            try:
                dlt_logger = DLTLogger.from_environment()
                asyncio.run(
                    dlt_logger.add_entry(
                        kind="runner",
                        name="agent_error",
                        detail={
                            "runner_type": runner_type,
                            "job_id": job_id,
                            "error": str(e),
                        },
                        agent_id="runner",
                        correlation_id=f"runner-{job_id}",
                    )
                )
            except Exception as dlt_e:
                runners_logger.error(f"Failed to log DLT entry for unhandled exception: {dlt_e}")

        return {"status": "ERROR", "message": f"Unhandled exception: {e}"}


if __name__ == "__main__":
    runners_logger.setLevel(logging.DEBUG)

    example_script_path = "example_script.py"
    with open(example_script_path, "w") as f:
        f.write(
            """
import sys
import os
import json
import time

if __name__ == "__main__":
    print(f"Hello from example_script.py! Args: {sys.argv[1:]}")
    print(f"Env var TEST_ENV: {os.getenv('TEST_ENV', 'NOT_SET')}")
    config_json = os.getenv('SIMULATION_CONFIG_JSON')
    if config_json:
        print(f"Received config: {json.loads(config_json)}")
    
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    should_fail = sys.argv[2].lower() == 'true' if len(sys.argv) > 2 else False

    time.sleep(duration)
    if should_fail:
        sys.exit(1)
    
    print("Script finished successfully.")
    sys.exit(0)
"""
        )

    runners_logger.info("\n--- Running example_script.py (success) ---")
    config_success = {
        "runner_type": "python_script",
        "runner_config": {
            "script_path": example_script_path,
            "args": ["2.0", "false"],
            "env_vars": {"TEST_ENV": "my_value", "SECRET_KEY": "super_secret_123"},
            "timeout_seconds": 10,
            "allow_network": False,
        },
        "user_id": "test_user_1",
        "job_id": "python_job_001",
    }
    result_success = run_agent(config_success)
    runners_logger.info(f"Result (success): {result_success}\n")

    runners_logger.info("\n--- Running example_script.py (failure) ---")
    config_fail = {
        "runner_type": "python_script",
        "runner_config": {
            "script_path": example_script_path,
            "args": ["1.0", "true"],
            "timeout_seconds": 5,
        },
        "user_id": "test_user_2",
        "job_id": "python_job_002",
    }
    result_fail = run_agent(config_fail)
    runners_logger.info(f"Result (failure): {result_fail}\n")

    runners_logger.info("\n--- Running example_script.py (timeout) ---")
    config_timeout = {
        "runner_type": "python_script",
        "runner_config": {
            "script_path": example_script_path,
            "args": ["10.0", "false"],
            "timeout_seconds": 2,
        },
        "user_id": "test_user_3",
        "job_id": "python_job_003",
    }
    result_timeout = run_agent(config_timeout)
    runners_logger.info(f"Result (timeout): {result_timeout}\n")

    runners_logger.info("\n--- Running container (requires Docker) ---")
    container_config = {
        "runner_type": "container",
        "runner_config": {
            "image_name": "alpine/git:latest",
            "command": ["git", "--version"],
            "timeout_seconds": 30,
            "resource_limits": {"cpu": "0.5", "memory": "128m"},
            "allow_network": False,
        },
        "user_id": "test_user_4",
        "job_id": "container_job_001",
    }
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        runners_logger.info("Docker daemon is running. Attempting container execution.")
        result_container = run_agent(container_config)
        runners_logger.info(f"Result (container): {result_container}\n")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        runners_logger.warning(
            f"Skipping container example: Docker not running or command not found. Error: {e}"
        )

    if os.path.exists(example_script_path):
        os.remove(example_script_path)

    if PROMETHEUS_AVAILABLE:
        runners_logger.info("\n--- Prometheus Metrics ---")
        runners_logger.info(generate_latest(registry=_metrics_registry).decode("utf-8"))
