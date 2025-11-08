# runner/process_utils.py
import subprocess
import asyncio
import os
import time
import platform
import concurrent.futures
import backoff
import sys
import shutil
from typing import List, Dict, Optional, Any, Callable, Union
from collections import defaultdict
from pathlib import Path
import hashlib
import tempfile
import aiofiles

# --- V2 Imports (Absolute path fix and feature split) ---
# FIX: Merged all imports from runner_logging into one line
from runner.runner_logging import (
    logger, add_provenance, util_decorator, 
    UTIL_LATENCY, UTIL_ERRORS, UTIL_SELF_HEAL, detect_anomaly
)
# FIX: Removed the incorrect import from runner_metrics
from runner.runner_security_utils import redact_secrets, encrypt_data, decrypt_data
from runner.feedback_handlers import collect_feedback
from runner.runner_errors import RunnerError # Import base structured error
from runner.runner_errors import ERROR_CODE_REGISTRY as error_codes # Import error codes
# Add the LLM client for self-healing logic (Step 3: Add if missing)
try:
    from runner.llm_client import call_llm_api
    _HAS_LLM_CLIENT = True
except ImportError:
    _HAS_LLM_CLIENT = False
    logger.warning("LLM client not found. AI-guided self-healing will be unavailable.")
    async def call_llm_api(*args, **kwargs):
        raise RuntimeError("LLM client unavailable for self-healing.")
# --- End V2 Imports ---

# Assuming 'runner.backends' and 'runner.config' are external dependencies (must be resolved during runtime)
try:
    from runner.runner_backends import BACKENDS
    from runner.runner_config import load_config
    config = load_config('config.yaml')
    _HAS_RUNNER = True
except ImportError:
    _HAS_RUNNER = False
    class MockConfig:
        def __init__(self):
            self.backend = 'local'
    config = MockConfig()
    BACKENDS = {}

# --- 1) Fix POSIX-only resource import ---
IS_WINDOWS = sys.platform.startswith("win")
try:
    if not IS_WINDOWS:
        import resource
    else:
        resource = None
except Exception:
    resource = None

if resource is None:
    class _DummyResource:
        RLIMIT_CPU = 0
        RLIMIT_AS = 0
        RLIMIT_FSIZE = 0
        RUSAGE_SELF = 0
        RUSAGE_CHILDREN = 0
        def setrlimit(self, *a, **k): pass
        def getrlimit(self, *a, **k): return (0, 0)
    resource = _DummyResource()

# --- Circuit Breaker (Standard Pattern) ---
class CircuitBreaker:
    """Implements a Circuit Breaker pattern to prevent cascading failures."""
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, name: str = "default"):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"
        self.name = name
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs):
        async with self._lock:
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = "HALF-OPEN"
                    logger.info(f"Circuit '{self.name}' for {func.__name__} is HALF-OPEN. Attempting recovery.")
                else:
                    logger.warning(f"Circuit '{self.name}' for {func.__name__} is OPEN. Not allowing call.")
                    detect_anomaly(f"circuit_breaker_open_{self.name}", 1, 0, severity="high", anomaly_type="circuit_open")
                    # Raising a structured RunnerError
                    raise RunnerError(error_codes["TEST_EXECUTION_FAILED"], f"Circuit '{self.name}' is OPEN. Execution blocked.")

            try:
                result = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
                await self.reset()
                return result
            except Exception as e:
                self.failures += 1
                self.last_failure_time = time.time()
                logger.warning(f"Circuit '{self.name}' detected failure. Failures: {self.failures}/{self.failure_threshold}")
                if self.failures >= self.failure_threshold:
                    self.state = "OPEN"
                    logger.error(f"Circuit '{self.name}' is now OPEN. Too many failures.")
                    detect_anomaly(f"circuit_breaker_opened_{self.name}", self.failures, self.failure_threshold, severity="critical", anomaly_type="circuit_trip")
                elif self.state == "HALF-OPEN":
                    self.state = "OPEN"
                    logger.error(f"Circuit '{self.name}' failed in HALF-OPEN state. Back to OPEN.")
                raise

    async def reset(self):
        async with self._lock:
            if self.failures > 0 or self.state != "CLOSED":
                logger.info(f"Circuit '{self.name}' breaker reset. Previous state: {self.state}, Failures: {self.failures}")
            self.failures = 0
            self.state = "CLOSED"
            self.last_failure_time = 0.0

# Global breaker registry
BREAKERS: Dict[str, CircuitBreaker] = defaultdict(lambda: CircuitBreaker(name="default_breaker"))

def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Retrieves or creates a named circuit breaker."""
    if name not in BREAKERS:
        BREAKERS[name] = CircuitBreaker(name=name)
        logger.info(f"Created new circuit breaker: '{name}'")
    return BREAKERS[name]

# Privilege drop (sandboxing helper)
def drop_privileges(uid: Optional[int] = None, gid: Optional[int] = None):
    """Returns a preexec_fn for subprocess.Popen to drop privileges."""
    if IS_WINDOWS: return None
    def preexec():
        if os.getuid() == 0:
            try:
                import pwd; import grp
                if uid is None:
                    try: uid_to_set = pwd.getpwnam('nobody').pw_uid
                    except KeyError: uid_to_set = 65534
                else: uid_to_set = uid
                if gid is None:
                    try: gid_to_set = grp.getgrnam('nogroup').gr_gid
                    except KeyError: gid_to_set = 65534
                else: gid_to_set = gid
                os.setgid(gid_to_set)
                os.setuid(uid_to_set)
                logger.info(f"Privileges dropped to UID: {os.getuid()}, GID: {os.getgid()}")
                add_provenance({'action': 'privilege_drop', 'uid': os.getuid(), 'gid': os.getgid()})
            except OSError as e:
                logger.warning(f"Failed to drop privileges: {e}. Process will run with current (potentially elevated) privileges.", exc_info=True)
                UTIL_ERRORS.labels('privilege_drop', type(e).__name__).inc()
        else:
            logger.debug("Not running as root, no privileges to drop.")
    return preexec

# Resource limits (sandboxing helper)
def set_resource_limits(cpu_limit_sec: int = 30, mem_limit_bytes: int = 1024*1024*512, io_limit_ops: Optional[int] = None, net_limit_bytes: Optional[int] = None):
    """Returns a preexec_fn for subprocess.Popen to set resource limits."""
    if resource is None or IS_WINDOWS: return None
    def preexec():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_sec, cpu_limit_sec))
            resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (mem_limit_bytes, mem_limit_bytes))

            logger.info(f"Resource limits set: CPU={cpu_limit_sec}s, Mem={mem_limit_bytes} bytes, FSize={mem_limit_bytes} bytes.")
            add_provenance({'action': 'set_resource_limits', 'cpu_sec': cpu_limit_sec, 'mem_bytes': mem_limit_bytes})

            if io_limit_ops is not None: logger.warning(f"IO operations limit ({io_limit_ops}) requested but not natively supported by `resource` module.")
            if net_limit_bytes is not None: logger.warning(f"Network bytes limit ({net_limit_bytes}) requested but not natively supported by `resource` module.")

        except Exception as e:
            logger.warning(f"Failed to set resource limits: {e}. Process will run without limits.", exc_info=True)
            UTIL_ERRORS.labels('set_resource_limits', type(e).__name__).inc()
    return preexec

# --- Self-Healing AI (Step 3: Self-Healing) ---
async def self_heal_ai_assist(error: RunnerError):
    """AI assistant attempts to suggest a fix for a persistent error."""
    if not _HAS_LLM_CLIENT:
        logger.warning("LLM client unavailable. Skipping AI self-heal assist.")
        return
        
    prompt = f"The Runner system encountered a persistent, critical error during subprocess execution. Error Code: {error.error_code}. Detail: {error.detail}. Cause: {error.cause}. Based on the error, provide a concise suggestion (under 50 words) for debugging or fixing the system configuration."
    
    try:
        fix_suggestion = await call_llm_api(prompt, max_tokens=150)
        logger.critical(f"AI Self-Heal Suggestion: {fix_suggestion}", extra={'error_code': error.error_code})
        collect_feedback("self_heal_ai_suggestion", {"fix": fix_suggestion, "error": error.error_code})
    except Exception as e:
        logger.error(f"AI self-healing failed: Could not call LLM API: {e}", exc_info=True)
        UTIL_ERRORS.labels('ai_self_heal_fail', type(e).__name__).inc()


@util_decorator
@backoff.on_exception(
    backoff.expo,
    (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError),
    max_tries=3,
    factor=2,
    on_giveup=lambda details: asyncio.create_task(self_heal_ai_assist(details['exception'])), # Trigger AI assist on final failure
    logger=logger
)
async def subprocess_wrapper(cmd: List[str], timeout: int = 60, cwd: Optional[Union[str, Path]] = None, sandbox: bool = False, resource_limits: Dict[str, Any] = {}, drop_priv: bool = True, encrypt_output: bool = False, encryption_key: Optional[bytes] = None, encryption_algo: str = 'fernet', circuit_breaker_name: str = 'subprocess_wrapper') -> Dict[str, Any]:
    """
    Executes a shell command in a subprocess, with full sandboxing, security, and observability.
    """
    cmd_str = ' '.join(cmd)
    breaker = get_circuit_breaker(circuit_breaker_name)
    
    def _sync_subprocess_run():
        preexec_fn = None
        creationflags = 0

        if not IS_WINDOWS:
            _preexec_fns: List[Callable] = []

            if drop_priv:
                priv_fn = drop_privileges()
                if priv_fn: _preexec_fns.append(priv_fn)

            if resource_limits:
                limit_fn = set_resource_limits(**resource_limits)
                if limit_fn: _preexec_fns.append(limit_fn)

            if _preexec_fns:
                def combined_preexec():
                    for fn in _preexec_fns:
                        fn()
                preexec_fn = combined_preexec
        else:
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        logger.debug(f"Executing command: {cmd_str} in CWD: {cwd if cwd else os.getcwd()}")

        run_kwargs = {
            'args': cmd,
            'capture_output': True,
            'text': False,
            'timeout': timeout,
            'cwd': cwd,
            'shell': False,
            'check': False
        }
        
        if not IS_WINDOWS:
            if preexec_fn:
                run_kwargs['preexec_fn'] = preexec_fn
        else:
            run_kwargs['creationflags'] = creationflags

        return subprocess.run(**run_kwargs)

    output: Dict[str, Any] = {}

    if sandbox and _HAS_RUNNER:
        # Sandboxed execution via the configured runner backend (e.g., Docker, K8s)
        from runner.runner_backends import BACKEND_REGISTRY
        if not BACKENDS.get(config.backend):
            raise RunnerError(error_codes["BACKEND_INIT_FAILURE"], f"Configured runner backend '{config.backend}' not found.")
        
        backend_instance = BACKEND_REGISTRY[config.backend](config)
        logger.info(f"Executing command in sandbox using backend: {config.backend}")
        try:
            exec_results = await breaker.call(backend_instance.execute, cmd, cwd, timeout) 
            
            output = {
                'success': exec_results.get('returncode', 1) == 0,
                'stdout': redact_secrets(exec_results.get('stdout', '')),
                'stderr': redact_secrets(exec_results.get('stderr', '')),
                'returncode': exec_results.get('returncode', 1),
                'encrypted': False
            }
        except Exception as e:
            logger.error(f"Sandbox execution failed for '{cmd_str}': {e}", exc_info=True)
            UTIL_ERRORS.labels('sandbox_exec_fail', type(e).__name__).inc()
            raise
    else:
        logger.info(f"Executing command locally: {cmd_str}")
        try:
            result = await breaker.call(asyncio.to_thread, _sync_subprocess_run)
            stdout_data = result.stdout if result.stdout is not None else b''
            stderr_data = result.stderr if result.stderr is not None else b''

            # Redact and Encrypt Output (Step 3: Security)
            redacted_stdout = redact_secrets(stdout_data.decode('utf-8', errors='ignore'))
            redacted_stderr = redact_secrets(stderr_data.decode('utf-8', errors='ignore'))

            if encrypt_output and encryption_key:
                if encryption_key is None: raise ValueError("Encryption key must be provided when encrypt_output=True.")
                encrypted_stdout = encrypt_data(redacted_stdout, encryption_key, algorithm=encryption_algo)
                encrypted_stderr = encrypt_data(redacted_stderr, encryption_key, algorithm=encryption_algo)
                output_stdout = encrypted_stdout.hex() 
                output_stderr = encrypted_stderr.hex()
            else:
                output_stdout = redacted_stdout
                output_stderr = redacted_stderr

            output = {
                'success': result.returncode == 0,
                'stdout': output_stdout,
                'stderr': output_stderr,
                'returncode': result.returncode,
                'encrypted': encrypt_output
            }
        except subprocess.TimeoutExpired as e:
            logger.warning(f"Subprocess '{cmd_str}' timed out after {timeout} seconds.")
            output = {
                'success': False,
                'stdout': redact_secrets(e.stdout.decode('utf-8', errors='ignore') if e.stdout else ''),
                'stderr': redact_secrets(e.stderr.decode('utf-8', errors='ignore') if e.stderr else 'Timeout expired.'),
                'returncode': -1,
                'encrypted': False
            }
            UTIL_ERRORS.labels('subprocess_timeout', 'timeout').inc()
            raise
        except Exception as e:
            logger.error(f"Local subprocess execution failed for '{cmd_str}': {e}", exc_info=True)
            UTIL_ERRORS.labels('subprocess_exec_fail', type(e).__name__).inc()
            raise

    provenance_data = add_provenance(output.copy(), action=f"subprocess_execution_{'success' if output['success'] else 'failure'}")
    output['provenance'] = provenance_data
    
    asyncio.create_task(collect_feedback('subprocess_wrapper', output.copy()))
    
    return output

@util_decorator
async def parallel_subprocess(cmds: List[List[str]], max_workers: Optional[int] = None, circuit_breaker_name_prefix: str = 'parallel_subprocess', **kwargs) -> List[Dict[str, Any]]:
    """
    Executes multiple shell commands in parallel using an asyncio.gather.
    """
    if max_workers is None: max_workers = os.cpu_count() or 1
    
    logger.info(f"Executing {len(cmds)} commands in parallel with max {max_workers} workers.")
    
    semaphore = asyncio.Semaphore(max_workers)

    async def single_cmd_with_semaphore_and_breaker(cmd_idx, cmd):
        breaker = get_circuit_breaker(f"{circuit_breaker_name_prefix}_{cmd_idx}")
        async with semaphore:
            return await breaker.call(subprocess_wrapper, cmd, **kwargs)

    tasks = [single_cmd_with_semaphore_and_breaker(i, cmd) for i, cmd in enumerate(cmds)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful_results: List[Dict[str, Any]] = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error(f"Parallel subprocess failed for command {i+1} ('{' '.join(cmds[i])[:50]}...'): {res}", exc_info=True)
            UTIL_ERRORS.labels('parallel_subprocess', type(res).__name__).inc()
        else:
            successful_results.append(res)
    
    logger.info(f"Completed parallel execution. Successful: {len(successful_results)}, Failed: {len(results) - len(successful_results)}")
    add_provenance({'action': 'parallel_subprocess_batch_completed', 'total_cmds': len(cmds), 'successful': len(successful_results)})
    return successful_results

@util_decorator
async def distributed_subprocess(cmds: List[List[str]], backend: Optional[str] = None, circuit_breaker_name_prefix: str = 'distributed_subprocess', **kwargs) -> List[Dict[str, Any]]:
    """
    Executes multiple shell commands using a distributed runner backend.
    """
    if not _HAS_RUNNER:
        logger.error("Runner module not available. Cannot execute distributed subprocesses.")
        raise RuntimeError("Distributed subprocess execution requires the 'runner' module.")

    selected_backend = backend if backend else config.backend
    if selected_backend not in BACKENDS:
        raise ValueError(f"Specified backend '{selected_backend}' not found in available backends.")
    
    from runner.runner_backends import BACKEND_REGISTRY
    runner = BACKEND_REGISTRY[selected_backend](config)
    logger.info(f"Executing {len(cmds)} commands via distributed backend: {selected_backend}")

    runner_tasks = []
    for cmd_list in cmds:
        task_data = {'cmd': ' '.join(cmd_list)}
        task_data.update(kwargs)
        runner_tasks.append(task_data)

    try:
        # Assuming runner.parallel_runs is an async method that returns a list of results
        results_raw = await runner.parallel_runs(runner_tasks)
        processed_results: List[Dict[str, Any]] = []
        for res_raw in results_raw:
            stdout_data = res_raw.get('stdout', b'')
            stderr_data = res_raw.get('stderr', b'')

            stdout_decoded = stdout_data.decode('utf-8', errors='ignore')
            stderr_decoded = stderr_data.decode('utf-8', errors='ignore')

            output = {
                'success': res_raw.get('returncode', 1) == 0,
                'stdout': redact_secrets(stdout_decoded),
                'stderr': redact_secrets(stderr_decoded),
                'returncode': res_raw.get('returncode', 1),
                'remote_id': res_raw.get('id')
            }
            output['provenance'] = add_provenance(output.copy(), action=f"distributed_subprocess_{'success' if output['success'] else 'failure'}")
            processed_results.append(output)
        
        logger.info(f"Distributed execution completed. Processed {len(processed_results)} results.")
        add_provenance({'action': 'distributed_subprocess_batch_completed', 'backend': selected_backend, 'total_cmds': len(cmds), 'successful': sum(1 for r in processed_results if r['success'])})
        return processed_results
    except Exception as e:
        logger.error(f"Distributed subprocess execution failed with backend {selected_backend}: {e}", exc_info=True)
        UTIL_ERRORS.labels('distributed_subprocess', type(e).__name__).inc()
        raise

# Multi-lang subprocess: Add lang-specific wrappers

async def run_javascript(code: str, timeout: int = 30, **kwargs) -> Dict[str, Any]:
    """Executes JavaScript code using Node.js."""
    node_path = shutil.which("node")
    if node_path is None:
        raise FileNotFoundError("Node.js is not installed or not in PATH. Cannot run JavaScript.")
    
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".js", encoding='utf-8') as temp_js_file:
        temp_js_file.write(code)
        temp_js_path = Path(temp_js_file.name)

    try:
        cmd = [node_path, str(temp_js_path)]
        logger.info(f"Running JavaScript via Node.js: {temp_js_path}")
        result = await subprocess_wrapper(cmd, timeout=timeout, circuit_breaker_name='run_javascript', **kwargs)
        add_provenance({'action': 'run_javascript', 'code_hash': hashlib.sha256(code.encode()).hexdigest(), 'result_success': result['success']}, action="javascript_execution")
        return result
    finally:
        if temp_js_path.exists():
            os.remove(temp_js_path)

async def run_python_script(code: str, timeout: int = 30, **kwargs) -> Dict[str, Any]:
    """Executes Python code in a new Python interpreter."""
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".py", encoding='utf-8') as temp_py_file:
        temp_py_file.write(code)
        temp_py_path = Path(temp_py_file.name)

    try:
        cmd = [sys.executable, str(temp_py_path)]
        logger.info(f"Running Python script: {temp_py_path}")
        result = await subprocess_wrapper(cmd, timeout=timeout, circuit_breaker_name='run_python_script', **kwargs)
        add_provenance({'action': 'run_python_script', 'code_hash': hashlib.sha256(code.encode()).hexdigest(), 'result_success': result['success']}, action="python_execution")
        return result
    finally:
        if temp_py_path.exists():
            os.remove(temp_py_path)

async def run_go_script(code: str, timeout: int = 30, **kwargs) -> Dict[str, Any]:
    """Executes Go code by compiling and running it."""
    go_path = shutil.which("go")
    if go_path is None:
        raise FileNotFoundError("Go compiler is not installed or not in PATH. Cannot run Go code.")

    temp_dir = Path(tempfile.mkdtemp(prefix="go_run_"))
    temp_go_file = temp_dir / "main.go"
    output_bin = temp_dir / "main_exec"
    if sys.platform == "win32":
        output_bin = temp_dir / "main_exec.exe"

    try:
        async with aiofiles.open(temp_go_file, 'w+', encoding='utf-8') as f:
            await f.write(code)
        
        compile_cmd = [go_path, "build", "-o", str(output_bin), str(temp_go_file)]
        logger.info(f"Compiling Go code: {' '.join(compile_cmd)}")
        compile_result = await subprocess_wrapper(compile_cmd, timeout=timeout, cwd=temp_dir, circuit_breaker_name='go_compile', **kwargs)

        if not compile_result['success']:
            logger.error(f"Go compilation failed: {compile_result['stderr']}")
            return compile_result

        run_cmd = [str(output_bin)]
        logger.info(f"Running Go binary: {' '.join(run_cmd)}")
        run_result = await subprocess_wrapper(run_cmd, timeout=timeout, cwd=temp_dir, circuit_breaker_name='go_run', **kwargs)
        add_provenance({'action': 'run_go_script', 'code_hash': hashlib.sha256(code.encode()).hexdigest(), 'result_success': run_result['success']}, action="go_execution")
        return run_result
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

# Register language runners
LANGUAGE_RUNNERS: Dict[str, Callable[[str, int, Any], Dict[str, Any]]] = {
    'python': run_python_script,
    'javascript': run_javascript,
    'go': run_go_script
}

def register_language_runner(lang_name: str, runner_func: Callable[[str, int, Any], Dict[str, Any]]):
    """Dynamically registers a new language runner."""
    LANGUAGE_RUNNERS[lang_name] = runner_func
    logger.info(f"Registered language runner for '{lang_name}': {runner_func.__name__}")
    add_provenance({'action': 'register_language_runner', 'language': lang_name, 'runner_func': runner_func.__name__})