# runner/backends.py
# Execution backends for the runner system.
# Provides isolated environments with robust setup, execution, health checks, and recovery,
# integrating structured error handling and consistent output contracts.
#
# REFACTORING NOTE: This module has been refactored to use subprocess_wrapper from
# runner.process_utils instead of maintaining its own local implementation. This provides:
# - Centralized subprocess management with enhanced circuit breaker support
# - Better sandboxing and resource limiting capabilities
# - Output encryption and redaction features
# - Parallel and distributed execution support
# - Language-specific execution wrappers
# All backends now share the same robust subprocess execution foundation.

import asyncio
import base64
import hashlib  # For run_python_script, run_javascript
import json
import os
import re
import shutil  # For shutil.which to check executable presence
import subprocess

# --- REFACTOR MERGE: Imports added from process_utils.py ---
import sys  # Added for platform check
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type  # Awaitable for async methods

import aiofiles

# --- REFACTOR: Import subprocess_wrapper from process_utils ---
from runner.process_utils import subprocess_wrapper

# Assume runner.config and runner.logging are correctly imported and configured
from runner.runner_config import RunnerConfig

# --- ADDED IMPORT ---
from runner.runner_contracts import TaskPayload, TaskResult

# Import structured errors for consistent error handling across backends
# FIX: Corrected module typo from 'runner.errors' to 'runner.runner_errors'
# --- FIX: Changed 'TestExecutionError' to 'ExecutionError' ---
from runner.runner_errors import (  # Explicitly import used error types
    BackendError,
    ConfigurationError,
    ExecutionError,
    RunnerError,
    SetupError,
    TimeoutError,
)

# --- REFACTOR FIX: Corrected imports to point to runner foundation ---
from runner.runner_logging import add_provenance, logger
from runner.runner_metrics import (  # BACKEND_LATENCY, ERRORS, RECOVERIES, CIRCUIT_BREAKERS,; and get_circuit_breaker are no longer used here.; This logic is encapsulated in the imported subprocess_wrapper.
    HEALTH_STATUS,
)

# --- END REFACTOR MERGE ---


# --- END REFACTOR ---
# --- END REFACTOR FIX ---


# OpenTelemetry Tracing (assuming it's set up globally)
try:
    import opentelemetry.trace as trace

    _tracer = trace.get_tracer(__name__)
except ImportError:
    _tracer = None
    logger.warning("OpenTelemetry not installed. Tracing will be disabled in runner_backends.")


# --- External Library Imports (with graceful degradation) ---
try:
    import docker.types
    from docker import DockerClient
    from docker.errors import APIError as DockerAPIError
    from docker.errors import DockerException, ImageNotFound

    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    DockerClient = None
    DockerException = None
    ImageNotFound = None
    DockerAPIError = None
    docker = None
    logger.warning("docker library not found. DockerBackend will be unavailable.")

try:
    import kubernetes.client as k8s_client
    import kubernetes.config as k8s_config
    from kubernetes.client.rest import ApiException as K8sApiException

    HAS_KUBERNETES = True
except ImportError:
    HAS_KUBERNETES = False
    k8s_client = None
    k8s_config = None
    K8sApiException = None
    logger.warning("kubernetes library not found. KubernetesBackend will be unavailable.")

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError

    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    boto3 = None
    BotoClientError = None
    logger.warning("boto3 not found. LambdaBackend will be unavailable.")

try:
    import libvirt

    HAS_LIBVIRT = True
except ImportError:
    HAS_LIBVIRT = False
    libvirt = None
    logger.warning("libvirt library not found. LibvirtBackend will be unavailable.")

try:
    import paramiko

    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    paramiko = None
    logger.warning("paramiko library not found. SSHBackend will be unavailable.")

# --- FIX: MOVED DEFINITIONS UP ---
# These definitions must appear *before* the imports below them that
# might trigger a circular import loop (e.g., via runner_config ->
# runner_errors -> runner_security_utils -> __init__ -> runner_core -> runner_backends)
# By defining BACKEND_REGISTRY here, it exists when runner_core imports it,
# even if the import is part of a loop.

# --- Backend ABC and Registry ---
BACKEND_REGISTRY: Dict[str, Type["Backend"]] = {}


class Backend(ABC):
    """Abstract Base Class for all execution backends."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.instance_id = config.instance_id

    @abstractmethod
    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        """
        Prepare the backend environment.
        This might involve pulling images, creating containers, or setting up SSH connections.
        """
        pass

    @abstractmethod
    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        """
        Execute the test command in the prepared environment.
        Returns a TaskResult object.
        """
        pass

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """
        Check the health of the backend (e.g., Docker daemon running, K8s API reachable).
        Returns {'status': 'healthy'|'unhealthy', 'details': '...'}.
        """
        pass

    @abstractmethod
    async def recover(self) -> None:
        """
        Attempt to recover the backend from an unhealthy state.
        (e.g., restart Docker, refresh K8s client).
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up any persistent resources (e.g., clients, connections)."""
        pass


def register_backend(name: str) -> Callable[[Type[Backend]], Type[Backend]]:
    """Decorator to register a new backend class."""

    def decorator(cls: Type[Backend]) -> Type[Backend]:
        if name in BACKEND_REGISTRY:
            logger.warning(f"Backend '{name}' is already registered. Overwriting.")
        BACKEND_REGISTRY[name] = cls
        logger.info(f"Registered execution backend: {name}")
        return cls

    return decorator


# --- END FIX ---


# --- REFACTOR MERGE: Sandboxing helpers from process_utils.py ---

# Fix POSIX-only resource import
IS_WINDOWS = sys.platform.startswith("win")
try:
    if not IS_WINDOWS:
        import resource  # POSIX only
    else:
        resource = None  # type: ignore
except Exception:  # ModuleNotFoundError on Windows
    resource = None  # type: ignore

# Define no-op constants if resource is missing
if resource is None:

    class _DummyResource:
        RLIMIT_CPU = 0
        RLIMIT_AS = 0
        RLIMIT_FSIZE = 0
        RUSAGE_SELF = 0
        RUSAGE_CHILDREN = 0

        def setrlimit(self, *a, **k):
            pass

        def getrlimit(self, *a, **k):
            return (0, 0)

        def getrusage(self, *a, **k):
            return 0  # Simplified

    resource = _DummyResource()  # type: ignore


def set_resource_limits(
    cpu_time_limit: int = 10, mem_limit_mb: int = 500, file_size_limit_mb: int = 10
):
    """
    Sets resource limits for the current process (POSIX only).
    This is a critical security sandbox feature to prevent resource exhaustion.
    """
    if IS_WINDOWS or resource is None:
        logger.warning("Resource limiting is not supported on Windows. Skipping.")
        return

    try:
        # CPU time limit (in seconds)
        # RLIMIT_CPU is the time in seconds.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_time_limit, cpu_time_limit))

        # Memory limit (RLIMIT_AS is virtual memory)
        mem_bytes = mem_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        # File size limit
        file_bytes = file_size_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))

        logger.debug(
            f"Set resource limits: CPU={cpu_time_limit}s, Mem={mem_limit_mb}MB, FileSize={file_size_limit_mb}MB"
        )

    except Exception as e:
        logger.error(f"Failed to set resource limits: {e}", exc_info=True)
        # In a high-security context, this could be a fatal error.
        raise ConfigurationError(
            "CONFIGURATION_ERROR",
            detail=f"Failed to apply resource limits: {e}",
            cause=e,
        )


def drop_privileges(user: str = "nobody", group: str = "nogroup"):
    """
    Drops process privileges to a non-privileged user/group (POSIX only).
    This is a critical security sandbox feature to limit the blast radius of code execution.
    """
    if IS_WINDOWS or resource is None:
        logger.warning("Privilege dropping is not supported on Windows. Skipping.")
        return

    if os.getuid() != 0:
        logger.debug("Not running as root. Skipping privilege drop.")
        return

    try:
        import grp
        import pwd

        # Get UID/GID for the target user/group
        target_uid = pwd.getpwnam(user).pw_uid
        target_gid = grp.getgrnam(group).gr_gid

        # Set group first, then user
        os.setgid(target_gid)
        os.setuid(target_uid)

        # Set umask to restrict file permissions
        os.umask(0o077)

        logger.info(
            f"Process privileges dropped to user='{user}' (UID={target_uid}), group='{group}' (GID={target_gid})"
        )

    except (KeyError, OSError, ImportError) as e:
        logger.error(
            f"Failed to drop privileges to '{user}':'{group}': {e}. This is a critical security failure.",
            exc_info=True,
        )
        # This MUST be a fatal error. Running as root when not intended is a critical vulnerability.
        raise ConfigurationError(
            "CONFIGURATION_ERROR",
            detail=f"Failed to drop privileges: {e}. Cannot continue execution as root.",
            cause=e,
        )


# --- REFACTOR NOTE: subprocess_wrapper is now imported from runner.process_utils ---
# The previous local implementation (lines 200-327) has been removed in favor of the
# centralized, more feature-rich implementation from process_utils.py which includes:
# - Enhanced circuit breaker integration
# - Better sandboxing support
# - Output encryption capabilities
# - Parallel execution support
# - Language-specific execution wrappers
# All backends now use the same subprocess_wrapper for consistency and maintainability.
# --- END REFACTOR NOTE ---

# --- Backend ABC and Registry ---
# --- FIX: MOVED TO TOP OF FILE (line 138) ---
# BACKEND_REGISTRY: Dict[str, Type["Backend"]] = {}
# class Backend(ABC):
# ...
# def register_backend(name: str) -> Callable[[Type[Backend]], Type[Backend]]:
# ...
# --- END FIX ---


# --- Local Backend (for lightweight, non-isolated execution) ---
@register_backend("local")
class LocalBackend(Backend):
    """
    Executes commands directly on the host using the sandboxed subprocess_wrapper.
    This backend is fast but provides minimal isolation (relies on POSIX resource limits and privilege dropping).
    """

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.instance_id = config.instance_id
        self.start_time = time.time()

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if custom_setup_script:
            setup_script_path = work_dir / "custom_setup.sh"
            try:
                async with aiofiles.open(setup_script_path, "w", encoding="utf-8") as f:
                    await f.write(custom_setup_script)
                os.chmod(setup_script_path, 0o755)

                # --- REFACTOR FIX: Use subprocess_wrapper ---
                result = await subprocess_wrapper(
                    ["/bin/bash", str(setup_script_path)],
                    timeout=self.config.timeout,
                    cwd=work_dir,
                    drop_priv=False,  # Setup might need privileges
                )
                if not result["success"]:
                    # *** FIX: Use string key, not registry value ***
                    raise SetupError(
                        "SETUP_FAILURE",
                        detail=f"Custom setup script failed: {result['stderr']}",
                        backend_type="local",
                        stage="custom_script",
                        stderr=result["stderr"],
                    )
                logger.info("Local custom setup script executed successfully.")
            except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Subprocess setup timed out: {e}")
                # *** FIX: Use string key, not registry value ***
                raise TimeoutError(
                    "TASK_TIMEOUT",
                    detail=f"Setup timed out after {self.config.timeout} seconds.",
                    timeout_seconds=self.config.timeout,
                    cause=e,
                    cmd=custom_setup_script,
                )
            except Exception as e:
                # *** FIX: Use string key, not registry value ***
                raise SetupError(
                    "SETUP_FAILURE",
                    detail=f"Failed to write or execute custom setup script: {e}",
                    backend_type="local",
                    stage="custom_script",
                    cause=e,
                )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        # --- REFACTOR FIX: Use subprocess_wrapper ---
        command = payload.command  # Get command from payload
        task_id = payload.task_id  # Get task_id from payload

        try:
            result = await subprocess_wrapper(
                command,
                timeout=timeout,
                cwd=work_dir,
                # Sandboxing (set_limits, drop_priv) is handled by default in subprocess_wrapper
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Subprocess execution timed out for task {task_id}: {e}")
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail=f"Execution timed out after {timeout} seconds.",
                task_id=task_id,
                timeout_seconds=timeout,
                cause=e,
                cmd=" ".join(command),
            )

        if not result["success"] and result.get("stderr") != "Circuit breaker is open.":
            # Don't raise ExecutionError if it was just a circuit breaker trip
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail=f"Local execution failed with code {result['returncode']}",
                task_id=task_id,
                returncode=result["returncode"],
                stdout=result.get("stdout"),
                stderr=result.get("stderr"),
                cmd=" ".join(command),
            )

        # Convert dict result to TaskResult
        return TaskResult(
            task_id=task_id,
            status="completed" if result["success"] else "failed",
            results={
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "returncode": result.get("returncode"),
                "duration": result.get("duration"),
            },
            started_at=result.get("start_time", time.time()),  # Guessing start_time
            finished_at=time.time(),
        )

    def health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "details": {
                "uptime": time.time() - getattr(self, "start_time", time.time()),
                "message": "Local execution is always available.",
            },
        }

    async def recover(self) -> None:
        logger.info("LocalBackend requires no recovery. Resetting health status.")
        HEALTH_STATUS.labels(component_name="backend_local", instance_id=self.instance_id).set(1.0)

    async def close(self) -> None:
        pass  # No resources to close


# --- NodeJS Backend ---
@register_backend("nodejs")
class NodeJSBackend(Backend):
    """
    Executes NodeJS scripts.
    REFACTOR: Merged logic from process_utils.run_javascript.
    """

    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        self.node_path = shutil.which("node")
        if not self.node_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "NodeJS executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_nodejs", instance_id=self.instance_id).set(
                0
            )
        else:
            self.health_status = {
                "status": "healthy",
                "details": f"NodeJS found at {self.node_path}",
            }
            HEALTH_STATUS.labels(component_name="backend_nodejs", instance_id=self.instance_id).set(
                1
            )

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.node_path:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="NodeJS backend is not healthy. Executable not found.",
                backend_type="nodejs",
                stage="health_check",
            )

        # Run npm install if package.json exists
        if (work_dir / "package.json").exists():
            logger.info("package.json found. Running npm install...")
            try:
                result = await subprocess_wrapper(
                    ["npm", "install"], timeout=self.config.timeout, cwd=work_dir
                )
            except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
                logger.warning(f"npm install timed out: {e}")
                # *** FIX: Use string key, not registry value ***
                raise TimeoutError(
                    "TASK_TIMEOUT",
                    detail=f"npm install timed out after {self.config.timeout} seconds.",
                    timeout_seconds=self.config.timeout,
                    cause=e,
                    cmd="npm install",
                )
            if not result["success"]:
                # *** FIX: Use string key, not registry value ***
                raise SetupError(
                    "SETUP_FAILURE",
                    detail=f"npm install failed: {result['stderr']}",
                    backend_type="nodejs",
                    stage="npm_install",
                    stderr=result["stderr"],
                )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        """
        Executes the NodeJS command.

        Note: Assumes `payload.command` is a standard command list (e.g., ['node', 'index.js'])
        but includes logic to run raw code content if necessary (for compatibility with process_utils).
        """
        command = payload.command
        task_id = payload.task_id

        if not self.node_path:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="NodeJS executable not found.",
                backend_type="nodejs",
                task_id=task_id,
            )

        # Fallback to running raw code content if the command looks like raw code
        if not command or not any(Path(c).suffix in [".js", ".ts"] for c in command):
            code = " ".join(command)  # Assume the 'command' is the raw code
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            js_file = work_dir / f"script_{code_hash[:10]}.js"

            try:
                async with aiofiles.open(js_file, "w", encoding="utf-8") as f:
                    await f.write(code)
                run_cmd = [self.node_path, str(js_file)]
                logger.info(f"Running NodeJS script: {' '.join(run_cmd)}")
            except Exception as e:
                logger.error(f"Failed to write NodeJS script: {e}", exc_info=True)
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail=f"Failed to write NodeJS script: {e}",
                    cause=e,
                    task_id=task_id,
                )
        else:
            run_cmd = command  # Use the command directly

        # --- REFACTOR MERGE: Use subprocess_wrapper ---
        try:
            start_time = time.time()
            run_result = await subprocess_wrapper(run_cmd, timeout=timeout, cwd=work_dir)

            # Simplified provenance logging for the execution
            add_provenance(
                {
                    "action": "nodejs_execute",
                    "command": " ".join(run_cmd),
                    "result_success": run_result["success"],
                },
                action="nodejs_execution",
            )

            return TaskResult(
                task_id=task_id,
                status="completed" if run_result["success"] else "failed",
                results={
                    "stdout": run_result.get("stdout"),
                    "stderr": run_result.get("stderr"),
                    "returncode": run_result.get("returncode"),
                    "duration": run_result.get("duration"),
                },
                started_at=start_time,
                finished_at=time.time(),
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
            logger.warning(f"NodeJS execution timed out for task {task_id}: {e}")
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail=f"NodeJS execution timed out after {timeout} seconds.",
                task_id=task_id,
                timeout_seconds=timeout,
                cause=e,
                cmd=" ".join(run_cmd),
            )
        except Exception as e:
            logger.error(f"NodeJS execution failed: {e}", exc_info=True)
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail=f"Failed to run NodeJS command: {e}",
                cause=e,
                task_id=task_id,
            )
        # --- END REFACTOR MERGE ---

    def health(self) -> Dict[str, Any]:
        return self.health_status

    async def recover(self) -> None:
        self.node_path = shutil.which("node")
        if not self.node_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "NodeJS executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_nodejs", instance_id=self.instance_id).set(
                0
            )
        else:
            self.health_status = {
                "status": "healthy",
                "details": f"NodeJS found at {self.node_path}",
            }
            HEALTH_STATUS.labels(component_name="backend_nodejs", instance_id=self.instance_id).set(
                1
            )

    async def close(self) -> None:
        pass


# --- Go Backend ---
@register_backend("go")
class GoBackend(Backend):
    """
    Executes Go code.
    REFACTOR: Merged logic from process_utils.run_go_script.
    """

    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        self.go_path = shutil.which("go")
        if not self.go_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "Go executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_go", instance_id=self.instance_id).set(0)
        else:
            self.health_status = {
                "status": "healthy",
                "details": f"Go found at {self.go_path}",
            }
            HEALTH_STATUS.labels(component_name="backend_go", instance_id=self.instance_id).set(1)

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.go_path:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="Go backend is not healthy. Executable not found.",
                backend_type="go",
                stage="health_check",
            )

        # Run go mod init/tidy if go.mod exists
        if (work_dir / "go.mod").exists():
            logger.info("go.mod found. Running go mod tidy...")
            try:
                result = await subprocess_wrapper(
                    [self.go_path, "mod", "tidy"],
                    timeout=self.config.timeout,
                    cwd=work_dir,
                )
            except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
                logger.warning(f"go mod tidy timed out: {e}")
                # *** FIX: Use string key, not registry value ***
                raise TimeoutError(
                    "TASK_TIMEOUT",
                    detail=f"go mod tidy timed out after {self.config.timeout} seconds.",
                    timeout_seconds=self.config.timeout,
                    cause=e,
                    cmd="go mod tidy",
                )
            if not result["success"]:
                # *** FIX: Use string key, not registry value ***
                raise SetupError(
                    "SETUP_FAILURE",
                    detail=f"go mod tidy failed: {result['stderr']}",
                    backend_type="go",
                    stage="go_mod_tidy",
                    stderr=result["stderr"],
                )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        """
        Executes the Go command, compiling it first.
        """
        command = payload.command
        task_id = payload.task_id

        if not self.go_path:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="Go executable not found.",
                backend_type="go",
                task_id=task_id,
            )

        # Determine if the command is a raw file path (e.g., go test ./...) or a code content
        if not command or len(command) == 1 and Path(command[0]).suffix == ".go":
            code = command[0]  # Assume raw code content if only one argument ending in .go
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            go_file = work_dir / f"main_{code_hash[:10]}.go"
            output_bin = work_dir / f"main_{code_hash[:10]}"

            try:
                async with aiofiles.open(go_file, "w", encoding="utf-8") as f:
                    await f.write(code)

                # Compile the Go code
                compile_cmd = [
                    self.go_path,
                    "build",
                    "-o",
                    str(output_bin),
                    str(go_file),
                ]
                logger.info(f"Compiling Go code: {' '.join(compile_cmd)}")
                compile_result = await subprocess_wrapper(
                    compile_cmd, timeout=timeout, cwd=work_dir
                )

                if not compile_result["success"]:
                    logger.error(f"Go compilation failed: {compile_result['stderr']}")
                    return TaskResult(
                        task_id=task_id,
                        status="failed",
                        results={
                            "stdout": compile_result.get("stdout"),
                            "stderr": compile_result.get("stderr"),
                            "returncode": compile_result.get("returncode"),
                            "duration": compile_result.get("duration"),
                        },
                        started_at=compile_result.get("start_time", time.time()),
                        finished_at=time.time(),
                    )

                # Run the compiled binary
                run_cmd = [str(output_bin)]
            except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Go compilation timed out for task {task_id}: {e}")
                # *** FIX: Use string key, not registry value ***
                raise TimeoutError(
                    "TASK_TIMEOUT",
                    detail=f"Go compilation timed out after {timeout} seconds.",
                    task_id=task_id,
                    timeout_seconds=timeout,
                    cause=e,
                    cmd=" ".join(compile_cmd),
                )
            except Exception as e:
                logger.error(f"Failed to write Go script: {e}", exc_info=True)
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail=f"Failed to write Go script: {e}",
                    cause=e,
                    task_id=task_id,
                )
        else:
            run_cmd = command  # Assume standard command list (e.g., ['go', 'test', './...'])

        # --- REFACTOR MERGE: Use subprocess_wrapper ---
        try:
            start_time = time.time()
            run_result = await subprocess_wrapper(run_cmd, timeout=timeout, cwd=work_dir)
            add_provenance(
                {
                    "action": "go_execute",
                    "command": " ".join(run_cmd),
                    "result_success": run_result["success"],
                },
                action="go_execution",
            )
            return TaskResult(
                task_id=task_id,
                status="completed" if run_result["success"] else "failed",
                results={
                    "stdout": run_result.get("stdout"),
                    "stderr": run_result.get("stderr"),
                    "returncode": run_result.get("returncode"),
                    "duration": run_result.get("duration"),
                },
                started_at=start_time,
                finished_at=time.time(),
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Go execution timed out for task {task_id}: {e}")
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail=f"Go execution timed out after {timeout} seconds.",
                task_id=task_id,
                timeout_seconds=timeout,
                cause=e,
                cmd=" ".join(run_cmd),
            )
        except Exception as e:
            logger.error(f"Go execution failed: {e}", exc_info=True)
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail=f"Failed to run Go command: {e}",
                cause=e,
                task_id=task_id,
            )
        # --- END REFACTOR MERGE ---

    def health(self) -> Dict[str, Any]:
        return self.health_status

    async def recover(self) -> None:
        self.go_path = shutil.which("go")
        if not self.go_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "Go executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_go", instance_id=self.instance_id).set(0)
        else:
            self.health_status = {
                "status": "healthy",
                "details": f"Go found at {self.go_path}",
            }
            HEALTH_STATUS.labels(component_name="backend_go", instance_id=self.instance_id).set(1)

    async def close(self) -> None:
        pass


# --- Java Backend ---
@register_backend("java")
class JavaBackend(Backend):
    """
    Executes Java code.
    REFACTOR: Updated to use subprocess_wrapper.
    """

    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        self.java_path = shutil.which("java")
        self.javac_path = shutil.which("javac")
        if not self.java_path or not self.javac_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "Java/Javac executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_java", instance_id=self.instance_id).set(0)
        else:
            self.health_status = {"status": "healthy", "details": "Java/Javac found."}
            HEALTH_STATUS.labels(component_name="backend_java", instance_id=self.instance_id).set(1)

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.java_path or not self.javac_path:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="Java backend is not healthy. Executable not found.",
                backend_type="java",
                stage="health_check",
            )

        # Run mvn/gradle install if pom.xml/build.gradle exists
        try:
            if (work_dir / "pom.xml").exists() and shutil.which("mvn"):
                logger.info("pom.xml found. Running mvn install...")
                result = await subprocess_wrapper(
                    ["mvn", "install"], timeout=self.config.timeout, cwd=work_dir
                )
                if not result["success"]:
                    # *** FIX: Use string key, not registry value ***
                    raise SetupError(
                        "SETUP_FAILURE",
                        detail=f"mvn install failed: {result['stderr']}",
                        backend_type="java",
                        stage="mvn_install",
                        stderr=result["stderr"],
                    )
            elif (work_dir / "build.gradle").exists() and shutil.which("gradle"):
                logger.info("build.gradle found. Running gradle build...")
                result = await subprocess_wrapper(
                    ["gradle", "build"], timeout=self.config.timeout, cwd=work_dir
                )
                if not result["success"]:
                    # *** FIX: Use string key, not registry value ***
                    raise SetupError(
                        "SETUP_FAILURE",
                        detail=f"gradle build failed: {result['stderr']}",
                        backend_type="java",
                        stage="gradle_build",
                        stderr=result["stderr"],
                    )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Java build (mvn/gradle) timed out: {e}")
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail=f"Java build timed out after {self.config.timeout} seconds.",
                timeout_seconds=self.config.timeout,
                cause=e,
                cmd="mvn install / gradle build",
            )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        """
        Executes the Java command, compiling it first.
        Note: Assumes `command` is a list of commands, or raw code content for a single file.
        """
        command = payload.command
        task_id = payload.task_id

        if not self.java_path or not self.javac_path:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="Java/Javac executable not found.",
                backend_type="java",
                task_id=task_id,
            )

        code = " ".join(command)

        # Determine if the command is raw code or a standard command list
        class_name_match = re.search(r"public\s+class\s+(\w+)", code)

        if class_name_match:  # Looks like raw code
            class_name = class_name_match.group(1)
            java_file = work_dir / f"{class_name}.java"
            hashlib.sha256(code.encode()).hexdigest()

            try:
                async with aiofiles.open(java_file, "w", encoding="utf-8") as f:
                    await f.write(code)

                # Compile
                compile_cmd = [self.javac_path, str(java_file)]
                logger.info(f"Compiling Java code: {' '.join(compile_cmd)}")
                compile_result = await subprocess_wrapper(
                    compile_cmd, timeout=timeout, cwd=work_dir
                )
                if not compile_result["success"]:
                    logger.error(f"Java compilation failed: {compile_result['stderr']}")
                    return TaskResult(
                        task_id=task_id,
                        status="failed",
                        results={
                            "stdout": compile_result.get("stdout"),
                            "stderr": compile_result.get("stderr"),
                            "returncode": compile_result.get("returncode"),
                            "duration": compile_result.get("duration"),
                        },
                        started_at=compile_result.get("start_time", time.time()),
                        finished_at=time.time(),
                    )

                # Run
                run_cmd = [
                    self.java_path,
                    "-cp",
                    ".",
                    class_name,
                ]  # Add classpath to include compiled class
            except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Java compilation timed out for task {task_id}: {e}")
                # *** FIX: Use string key, not registry value ***
                raise TimeoutError(
                    "TASK_TIMEOUT",
                    detail=f"Java compilation timed out after {timeout} seconds.",
                    task_id=task_id,
                    timeout_seconds=timeout,
                    cause=e,
                    cmd=" ".join(compile_cmd),
                )
            except Exception as e:
                logger.error(f"Failed to write or compile Java script: {e}", exc_info=True)
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail=f"Failed to write or compile Java script: {e}",
                    cause=e,
                    task_id=task_id,
                )

        elif command:
            run_cmd = command  # Standard command list
        else:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="No command or code provided to execute.",
                backend_type="java",
                task_id=task_id,
            )

        # --- REFACTOR MERGE: Use subprocess_wrapper ---
        try:
            start_time = time.time()
            run_result = await subprocess_wrapper(run_cmd, timeout=timeout, cwd=work_dir)
            add_provenance(
                {
                    "action": "java_execute",
                    "command": " ".join(run_cmd),
                    "result_success": run_result["success"],
                },
                action="java_execution",
            )
            return TaskResult(
                task_id=task_id,
                status="completed" if run_result["success"] else "failed",
                results={
                    "stdout": run_result.get("stdout"),
                    "stderr": run_result.get("stderr"),
                    "returncode": run_result.get("returncode"),
                    "duration": run_result.get("duration"),
                },
                started_at=start_time,
                finished_at=time.time(),
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Java execution timed out for task {task_id}: {e}")
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail=f"Java execution timed out after {timeout} seconds.",
                task_id=task_id,
                timeout_seconds=timeout,
                cause=e,
                cmd=" ".join(run_cmd),
            )
        except Exception as e:
            logger.error(f"Java execution failed: {e}", exc_info=True)
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail=f"Failed to run Java command: {e}",
                cause=e,
                task_id=task_id,
            )
        # --- END REFACTOR MERGE ---

    def health(self) -> Dict[str, Any]:
        return self.health_status

    async def recover(self) -> None:
        self.java_path = shutil.which("java")
        self.javac_path = shutil.which("javac")
        if not self.java_path or not self.javac_path:
            self.health_status = {
                "status": "unhealthy",
                "details": "Java/Javac executable not found in PATH.",
            }
            HEALTH_STATUS.labels(component_name="backend_java", instance_id=self.instance_id).set(0)
        else:
            self.health_status = {"status": "healthy", "details": "Java/Javac found."}
            HEALTH_STATUS.labels(component_name="backend_java", instance_id=self.instance_id).set(1)

    async def close(self) -> None:
        pass


# --- Docker Backend ---
@register_backend("docker")
class DockerBackend(Backend):
    """Executes commands in an isolated Docker container."""

    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        if not HAS_DOCKER:
            self.client = None
            self.health_status = {
                "status": "unhealthy",
                "details": "docker library not installed.",
            }
            HEALTH_STATUS.labels(component_name="backend_docker", instance_id=self.instance_id).set(
                0
            )
            return
        try:
            self.client = DockerClient.from_env()
            self.client.ping()
            self.health_status = {
                "status": "healthy",
                "details": "Docker daemon is responsive.",
            }
            HEALTH_STATUS.labels(component_name="backend_docker", instance_id=self.instance_id).set(
                1
            )
        except DockerException as e:
            self.client = None
            self.health_status = {
                "status": "unhealthy",
                "details": f"Docker daemon connection failed: {e}",
            }
            HEALTH_STATUS.labels(component_name="backend_docker", instance_id=self.instance_id).set(
                0
            )
        except Exception as e:
            self.client = None
            self.health_status = {
                "status": "unhealthy",
                "details": f"Docker client init failed: {e}",
            }
            HEALTH_STATUS.labels(component_name="backend_docker", instance_id=self.instance_id).set(
                0
            )

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.client:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="Docker backend is not healthy.",
                backend_type="docker",
                stage="health_check",
            )

        logger.info(
            f"DockerBackend setup complete for {work_dir}. Image will be pulled/run in execute."
        )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        command = payload.command  # Get command from payload
        task_id = payload.task_id  # Get task_id from payload
        start_time = time.time()  # Record start time

        if not self.client:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="Docker backend is not healthy.",
                backend_type="docker",
                task_id=task_id,
            )

        # Use a specific, language-appropriate image from config
        image_name = self.config.framework_images.get(self.config.framework, "python:3.10-slim")

        # Resource limits
        resource_limits = {
            "mem_limit": self.config.resources.get("memory", "512m"),
            "cpus": self.config.resources.get("cpu", 1.0),
        }

        container = None
        try:
            # Pull image
            await asyncio.to_thread(self.client.images.pull, image_name)

            container = await asyncio.to_thread(
                self.client.containers.create,
                image=image_name,
                command=command,
                working_dir="/app",
                volumes={str(work_dir.resolve()): {"bind": "/app", "mode": "rw"}},
                **resource_limits,
                log_config={"type": "json-file", "config": {"max-size": "1m"}},
                detach=True,
            )

            await asyncio.to_thread(container.start)

            result = await asyncio.to_thread(container.wait, timeout=timeout)

            stdout_bytes = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
            stderr_bytes = await asyncio.to_thread(container.logs, stdout=False, stderr=True)

            stdout = stdout_bytes.decode("utf-8", errors="ignore")
            stderr = stderr_bytes.decode("utf-8", errors="ignore")
            returncode = result.get("StatusCode", -1)
            duration = time.time() - start_time

            if returncode != 0:
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail=f"Container execution failed with code {returncode}",
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    cmd=" ".join(command),
                    task_id=task_id,
                )

            # Convert dict result to TaskResult
            return TaskResult(
                task_id=task_id,
                status="completed",
                results={
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": returncode,
                    "duration": duration,
                    "run_id": container.id,
                },
                started_at=start_time,
                finished_at=time.time(),
            )
        except asyncio.TimeoutError:
            if container:
                await asyncio.to_thread(container.stop, timeout=5)
            # *** FIX: Use string key, not registry value ***
            raise TimeoutError(
                "TASK_TIMEOUT",
                detail="Container execution timed out.",
                timeout_seconds=timeout,
                cmd=" ".join(command),
                task_id=task_id,
            )
        except DockerAPIError as e:
            # *** FIX: Use string key, not registry value ***
            raise BackendError(
                "BACKEND_INIT_FAILURE",
                detail=f"Docker API error: {e}",
                backend_type="docker",
                cause=e,
                task_id=task_id,
            )
        except Exception as e:
            # *** FIX: Use string key, not registry value ***
            raise RunnerError(
                "UNEXPECTED_ERROR",
                detail=f"Unexpected Docker error: {e}",
                cause=e,
                task_id=task_id,
            )
        finally:
            if container:
                await asyncio.to_thread(container.remove, v=True, force=True)

    def health(self) -> Dict[str, Any]:
        if not HAS_DOCKER:
            self.health_status = {
                "status": "unhealthy",
                "details": "docker library not installed.",
            }
        elif self.client:
            try:
                self.client.ping()
                self.health_status = {
                    "status": "healthy",
                    "details": "Docker daemon is responsive.",
                }
            except DockerException as e:
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"Docker daemon connection failed: {e}",
                }
        HEALTH_STATUS.labels(component_name="backend_docker", instance_id=self.instance_id).set(
            1 if self.health_status["status"] == "healthy" else 0
        )
        return self.health_status

    async def recover(self) -> None:
        logger.info("Attempting to recover DockerBackend by re-initializing client...")
        if HAS_DOCKER:
            try:
                self.client = DockerClient.from_env()
                self.client.ping()
                self.health_status = {
                    "status": "healthy",
                    "details": "Docker daemon connection re-established.",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_docker", instance_id=self.instance_id
                ).set(1)
            except DockerException as e:
                self.client = None
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"Docker daemon recovery failed: {e}",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_docker", instance_id=self.instance_id
                ).set(0)

    async def close(self) -> None:
        if self.client:
            await asyncio.to_thread(self.client.close)
            logger.info("Docker client closed.")


# --- Kubernetes Backend ---
@register_backend("kubernetes")
class KubernetesBackend(Backend):
    """Executes commands as a Job in a Kubernetes cluster."""

    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        if not HAS_KUBERNETES:
            self.core_v1 = None
            self.batch_v1 = None
            self.health_status = {
                "status": "unhealthy",
                "details": "kubernetes library not installed.",
            }
            HEALTH_STATUS.labels(
                component_name="backend_kubernetes", instance_id=self.instance_id
            ).set(0)
            return
        try:
            k8s_config.load_kube_config()  # Assumes kubeconfig is available
            self.core_v1 = k8s_client.CoreV1Api()
            self.batch_v1 = k8s_client.BatchV1Api()
            self.namespace = self.config.k8s_namespace
            self.core_v1.read_namespace_status(self.namespace)
            self.health_status = {
                "status": "healthy",
                "details": f"Kubernetes API reachable. Namespace: {self.namespace}",
            }
            HEALTH_STATUS.labels(
                component_name="backend_kubernetes", instance_id=self.instance_id
            ).set(1)
        except K8sApiException as e:
            self.core_v1 = None
            self.batch_v1 = None
            self.health_status = {
                "status": "unhealthy",
                "details": f"Kubernetes API error: {e.reason}",
            }
            HEALTH_STATUS.labels(
                component_name="backend_kubernetes", instance_id=self.instance_id
            ).set(0)
        except Exception as e:
            self.core_v1 = None
            self.batch_v1 = None
            self.health_status = {
                "status": "unhealthy",
                "details": f"Kubernetes client init failed: {e}",
            }
            HEALTH_STATUS.labels(
                component_name="backend_kubernetes", instance_id=self.instance_id
            ).set(0)

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.core_v1 or not self.batch_v1:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="Kubernetes backend is not healthy.",
                backend_type="kubernetes",
                stage="health_check",
            )

        # Setup in K8s involves creating a ConfigMap for the code/tests
        # and a PersistentVolumeClaim for the output.
        # This is a simplified example; a real implementation would be more robust.

        # 1. Create ConfigMap for code files
        config_map_name = f"runner-workdir-{uuid.uuid4().hex[:8]}"
        config_map_data = {}
        for file_path in work_dir.rglob("*"):
            if file_path.is_file():
                try:
                    config_map_data[file_path.name] = file_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Could not read file {file_path} for ConfigMap: {e}")

        config_map = k8s_client.V1ConfigMap(
            api_version="v1",
            kind="ConfigMap",
            metadata={"name": config_map_name},
            data=config_map_data,
        )
        try:
            await asyncio.to_thread(
                self.core_v1.create_namespaced_config_map,
                namespace=self.namespace,
                body=config_map,
            )
            self.config_map_name = config_map_name  # Store for cleanup
        except K8sApiException as e:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail=f"Failed to create ConfigMap: {e.reason}",
                backend_type="kubernetes",
                stage="create_configmap",
                cause=e,
            )

        # 2. (Optional) Create PVC for outputs if needed
        # self.pvc_name = ...

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        command = payload.command
        task_id = payload.task_id
        start_time = time.time()

        if not self.core_v1 or not self.batch_v1 or not hasattr(self, "config_map_name"):
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="Kubernetes backend is not healthy or setup failed.",
                backend_type="kubernetes",
                task_id=task_id,
            )

        job_name = f"runner-job-{uuid.uuid4().hex[:8]}"
        image_name = self.config.framework_images.get(self.config.framework, "python:3.10-slim")

        # Define container resource limits
        resources = k8s_client.V1ResourceRequirements(
            limits={
                "cpu": str(self.config.resources.get("cpu", 1.0)),
                "memory": self.config.resources.get("memory", "512Mi"),
            },
            requests={"cpu": "100m", "memory": "128Mi"},
        )

        container = k8s_client.V1Container(
            name=job_name,
            image=image_name,
            command=command,
            working_dir="/app",
            volume_mounts=[k8s_client.V1VolumeMount(name="workdir-volume", mount_path="/app")],
            resources=resources,
        )

        volume = k8s_client.V1Volume(
            name="workdir-volume",
            config_map=k8s_client.V1ConfigMapVolumeSource(name=self.config_map_name),
        )

        pod_spec = k8s_client.V1PodSpec(
            restart_policy="Never", containers=[container], volumes=[volume]
        )

        job_spec = k8s_client.V1JobSpec(
            template=k8s_client.V1PodTemplateSpec(spec=pod_spec),
            backoff_limit=1,
            active_deadline_seconds=timeout,
        )

        job = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata={"name": job_name},
            spec=job_spec,
        )

        try:
            await asyncio.to_thread(
                self.batch_v1.create_namespaced_job, namespace=self.namespace, body=job
            )

            job_start_time = time.time()
            while True:
                job_status = await asyncio.to_thread(
                    self.batch_v1.read_namespaced_job_status,
                    name=job_name,
                    namespace=self.namespace,
                )

                if job_status.status.succeeded:
                    returncode = 0
                    break
                if job_status.status.failed:
                    returncode = 1
                    break

                if (time.time() - job_start_time) > timeout:
                    # *** FIX: Use string key, not registry value ***
                    raise TimeoutError(
                        "TASK_TIMEOUT",
                        detail="Kubernetes job timed out.",
                        timeout_seconds=timeout,
                        cmd=" ".join(command),
                        task_id=task_id,
                    )

                await asyncio.sleep(2)  # Poll interval

            # Fetch logs from the job's pod
            pods = await asyncio.to_thread(
                self.core_v1.list_namespaced_pod,
                namespace=self.namespace,
                label_selector=f"job-name={job_name}",
            )
            if not pods.items:
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail="K8s job pod not found.",
                    job_name=job_name,
                    task_id=task_id,
                )

            pod_name = pods.items[0].metadata.name
            log_stream = await asyncio.to_thread(
                self.core_v1.read_namespaced_pod_log,
                name=pod_name,
                namespace=self.namespace,
                follow=False,
            )

            stdout = log_stream
            stderr = ""  # K8s logs are combined
            duration = time.time() - start_time

            if returncode != 0:
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail="K8s job execution failed. Check pod logs.",
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    cmd=" ".join(command),
                    job_name=job_name,
                    pod_name=pod_name,
                    task_id=task_id,
                )

            return TaskResult(
                task_id=task_id,
                status="completed",
                results={
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": returncode,
                    "duration": duration,
                    "run_id": job_name,
                },
                started_at=start_time,
                finished_at=time.time(),
            )

        except K8sApiException as e:
            # *** FIX: Use string key, not registry value ***
            raise BackendError(
                "BACKEND_INIT_FAILURE",
                detail=f"Kubernetes API error: {e.reason}",
                backend_type="kubernetes",
                cause=e,
                task_id=task_id,
            )
        except Exception as e:
            # *** FIX: Use string key, not registry value ***
            raise RunnerError(
                "UNEXPECTED_ERROR",
                detail=f"Unexpected K8s error: {e}",
                cause=e,
                task_id=task_id,
            )
        finally:
            # Cleanup job and configmap
            try:
                await asyncio.to_thread(
                    self.batch_v1.delete_namespaced_job,
                    name=job_name,
                    namespace=self.namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
                )
            except K8sApiException as e:
                logger.warning(f"Failed to delete K8s job {job_name}: {e.reason}")
            try:
                await asyncio.to_thread(
                    self.core_v1.delete_namespaced_config_map,
                    name=self.config_map_name,
                    namespace=self.namespace,
                )
            except K8sApiException as e:
                logger.warning(f"Failed to delete K8s ConfigMap {self.config_map_name}: {e.reason}")

    def health(self) -> Dict[str, Any]:
        if not HAS_KUBERNETES:
            self.health_status = {
                "status": "unhealthy",
                "details": "kubernetes library not installed.",
            }
        elif self.core_v1:
            try:
                self.core_v1.read_namespace_status(self.namespace)
                self.health_status = {
                    "status": "healthy",
                    "details": f"Kubernetes API reachable. Namespace: {self.namespace}",
                }
            except K8sApiException as e:
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"Kubernetes API error: {e.reason}",
                }
        HEALTH_STATUS.labels(component_name="backend_kubernetes", instance_id=self.instance_id).set(
            1 if self.health_status["status"] == "healthy" else 0
        )
        return self.health_status

    async def recover(self) -> None:
        logger.info("Attempting to recover KubernetesBackend by reloading kubeconfig...")
        if HAS_KUBERNETES:
            try:
                k8s_config.load_kube_config()
                self.core_v1 = k8s_client.CoreV1Api()
                self.batch_v1 = k8s_client.BatchV1Api()
                self.namespace = self.config.k8s_namespace
                self.core_v1.read_namespace_status(self.namespace)
                self.health_status = {
                    "status": "healthy",
                    "details": "Kubernetes client reloaded and API reachable.",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_kubernetes", instance_id=self.instance_id
                ).set(1)
            except Exception as e:
                self.core_v1 = None
                self.batch_v1 = None
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"Kubernetes client recovery failed: {e}",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_kubernetes", instance_id=self.instance_id
                ).set(0)

    async def close(self) -> None:
        # k8s client doesn't have an explicit async close method for the client object itself
        pass


# Other backends (Lambda, Libvirt, SSH, Firecracker) would follow a similar pattern
# ... (Implementation for LambdaBackend, LibvirtBackend, SSHBackend, FirecrackerBackend) ...


@register_backend("lambda")
class LambdaBackend(Backend):
    def __init__(self, config: RunnerConfig):
        super().__init__(config)
        self.function_name = config.lambda_function_name
        if not HAS_BOTO3:
            self.client = None
            self.health_status = {
                "status": "unhealthy",
                "details": "boto3 library not installed.",
            }
            HEALTH_STATUS.labels(component_name="backend_lambda", instance_id=self.instance_id).set(
                0
            )
            return
        try:
            self.client = boto3.client("lambda", region_name=config.aws_region)
            self.client.get_function_configuration(FunctionName=self.function_name)
            self.health_status = {
                "status": "healthy",
                "details": f"AWS Lambda function {self.function_name} is accessible.",
            }
            HEALTH_STATUS.labels(component_name="backend_lambda", instance_id=self.instance_id).set(
                1
            )
        except (BotoClientError, Exception) as e:
            self.client = None
            self.health_status = {
                "status": "unhealthy",
                "details": f"AWS Lambda client init failed: {e}",
            }
            HEALTH_STATUS.labels(component_name="backend_lambda", instance_id=self.instance_id).set(
                0
            )

    async def setup(self, work_dir: Path, custom_setup_script: Optional[str] = None) -> None:
        if not self.client:
            # *** FIX: Use string key, not registry value ***
            raise SetupError(
                "SETUP_FAILURE",
                detail="Lambda backend is not healthy.",
                backend_type="lambda",
                stage="health_check",
            )
        logger.info(
            "LambdaBackend setup is handled by the Lambda function's environment. No local setup required."
        )

    async def execute(self, payload: TaskPayload, work_dir: Path, timeout: int) -> TaskResult:
        command = payload.command
        task_id = payload.task_id
        start_time = time.time()

        if not self.client:
            # *** FIX: Use string key, not registry value ***
            raise ExecutionError(
                "TEST_EXECUTION_FAILED",
                detail="Lambda backend is not healthy.",
                backend_type="lambda",
                task_id=task_id,
            )

        # Package work_dir files into a payload (e.g., zip or pass as JSON)
        lambda_payload_data = {"command": command, "files": {}, "timeout": timeout}
        for file_path in work_dir.rglob("*"):
            if file_path.is_file():
                try:
                    lambda_payload_data["files"][file_path.name] = file_path.read_text(
                        encoding="utf-8"
                    )
                except Exception:
                    lambda_payload_data["files"][
                        file_path.name
                    ] = file_path.read_bytes().hex()  # Fallback for binary

        try:
            response = await asyncio.to_thread(
                self.client.invoke,
                FunctionName=self.function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(lambda_payload_data),
                LogType="Tail",  # Get last 4KB of logs
            )
            duration = time.time() - start_time

            log_result = base64.b64decode(response.get("LogResult", "")).decode("utf-8")
            response_payload_str = response["Payload"].read().decode("utf-8")

            try:
                result = json.loads(response_payload_str)
            except json.JSONDecodeError:
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail="Lambda function returned invalid JSON.",
                    lambda_payload=response_payload_str,
                    logs=log_result,
                    task_id=task_id,
                )

            if response.get("FunctionError"):
                # *** FIX: Use string key, not registry value ***
                raise ExecutionError(
                    "TEST_EXECUTION_FAILED",
                    detail=f"Lambda function execution failed: {result.get('errorMessage', 'Unknown error')}",
                    returncode=result.get("returncode", 1),
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", result.get("errorMessage", "")),
                    cmd=" ".join(command),
                    logs=log_result,
                    task_id=task_id,
                )

            return TaskResult(
                task_id=task_id,
                status="completed",
                results={
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "returncode": result.get("returncode", 0),
                    "duration": duration,
                    "run_id": response["ResponseMetadata"]["RequestId"],
                    "logs": log_result,
                },
                started_at=start_time,
                finished_at=time.time(),
            )
        except BotoClientError as e:
            # *** FIX: Use string key, not registry value ***
            raise BackendError(
                "BACKEND_INIT_FAILURE",
                detail=f"AWS Lambda API error: {e}",
                backend_type="lambda",
                cause=e,
                task_id=task_id,
            )
        except Exception as e:
            # *** FIX: Use string key, not registry value ***
            raise RunnerError(
                "UNEXPECTED_ERROR",
                detail=f"Unexpected Lambda error: {e}",
                cause=e,
                task_id=task_id,
            )

    def health(self) -> Dict[str, Any]:
        if not HAS_BOTO3:
            self.health_status = {
                "status": "unhealthy",
                "details": "boto3 library not installed.",
            }
        elif self.client:
            try:
                self.client.get_function_configuration(FunctionName=self.function_name)
                self.health_status = {
                    "status": "healthy",
                    "details": f"AWS Lambda function {self.function_name} is accessible.",
                }
            except (BotoClientError, Exception) as e:
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"AWS Lambda client error: {e}",
                }
        HEALTH_STATUS.labels(component_name="backend_lambda", instance_id=self.instance_id).set(
            1 if self.health_status["status"] == "healthy" else 0
        )
        return self.health_status

    async def recover(self) -> None:
        logger.info("Attempting to recover LambdaBackend by re-initializing client...")
        if HAS_BOTO3:
            try:
                self.client = boto3.client("lambda", region_name=self.config.aws_region)
                self.client.get_function_configuration(FunctionName=self.function_name)
                self.health_status = {
                    "status": "healthy",
                    "details": "AWS Lambda client re-established.",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_lambda", instance_id=self.instance_id
                ).set(1)
            except (BotoClientError, Exception) as e:
                self.client = None
                self.health_status = {
                    "status": "unhealthy",
                    "details": f"AWS Lambda client recovery failed: {e}",
                }
                HEALTH_STATUS.labels(
                    component_name="backend_lambda", instance_id=self.instance_id
                ).set(0)

    async def close(self) -> None:
        if self.client:
            await asyncio.to_thread(self.client.close)
            logger.info("BBoto3 Lambda client closed.")


# --- Health Check Aggregator ---
def check_all_backends(config: RunnerConfig) -> Dict[str, Any]:
    """
    Checks the health of all registered backends, not just the configured one.
    """
    backend_health_status = {}
    configured_backend_name = config.backend
    configured_backend_cls = BACKEND_REGISTRY.get(configured_backend_name)

    if configured_backend_cls:
        try:
            backend_instance = configured_backend_cls(config)
            health_status = backend_instance.health()
            backend_health_status[configured_backend_name] = health_status
        except Exception as e:
            backend_health_status[configured_backend_name] = {
                "status": "unhealthy",
                "error": f"Initialization or health check failed: {e}",
            }
    else:
        backend_health_status[configured_backend_name] = {
            "status": "unregistered_or_unavailable",
            "message": "Backend not registered or missing dependencies.",
        }

    for backend_name, backend_cls in BACKEND_REGISTRY.items():
        if backend_name != configured_backend_name:
            try:
                # Use a dummy config to init the backend for availability check
                dummy_config_for_avail_check = RunnerConfig(
                    version=1,
                    backend=backend_name,
                    framework="pytest",
                    instance_id=f"health_check_{backend_name}",
                )
                backend_instance = backend_cls(dummy_config_for_avail_check)
                health_status = backend_instance.health()
                backend_health_status[backend_name] = {
                    "availability": "available",
                    "health": health_status,
                }
            except Exception as e:
                backend_health_status[backend_name] = {
                    "availability": "unavailable",
                    "error": f"Init failed or health check failed: {e}",
                }

    return backend_health_status
