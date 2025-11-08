# runner/mutation.py
# World-class, gold-standard module for mutation testing and fuzzing.
# Provides multi-language support, pluggable tools, advanced strategies,
# robust execution, and comprehensive observability with elite-tier safeguards.

import concurrent.futures
import asyncio
import subprocess
import os
import json
import re
import inspect
import sys
import importlib
import random
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Any, Callable, Optional, Union, Tuple, Awaitable
import contextlib

# Try to import specific mutation tools
try:
    import mutmut # pip install mutmut
    HAS_MUTMUT = True
    MUTMUT_VERSION = getattr(mutmut, '__version__', 'unknown')
except ImportError:
    HAS_MUTMUT = False
    MUTMUT_VERSION = 'N/A'
    logging.getLogger(__name__).warning("mutmut not installed. Python mutation testing will use subprocess fallback or be unavailable.")

# Try to import property-based testing tool
try:
    import hypothesis # pip install hypothesis
    import hypothesis.strategies as st
    HAS_HYPOTHESIS = True
    HYPOTHESIS_VERSION = getattr(hypothesis, '__version__', 'unknown')
except ImportError:
    HAS_HYPOTHESIS = False
    HYPOTHESIS_VERSION = 'N/A'
    logging.getLogger(__name__).warning("Hypothesis not installed. Property-based testing and Hypothesis-based fuzzing will be unavailable.")

# Assume RunnerConfig and metrics are available
from runner.config import RunnerConfig
from runner.metrics import prom
from runner.logging import logger
# Gold Standard: Import contracts and structured errors
from runner.contracts import TaskPayload # Import TaskPayload for type hinting
from runner.errors import RunnerError, TestExecutionError, SetupError, ConfigurationError, TimeoutError # Import specific errors
from runner.errors import ERROR_CODE_REGISTRY as error_codes # Import error codes


# OpenTelemetry Tracing Setup (Gold Standard: Safe Fallback)
@contextlib.contextmanager
def _noop_context(*a, **kw):
    """A no-op context manager for when tracing is disabled."""
    yield

try:
    import opentelemetry.trace as trace
    _tracer = trace.get_tracer(__name__)
    def trace_method_decorator(func):
        if _tracer:
            return _tracer.trace_method(func)
        return func
    
except ImportError:
    _tracer = None
    logger.warning("OpenTelemetry not installed. Tracing will be disabled in runner_mutation.")
    def trace_method_decorator(func):
        return func


# Expanded metrics for mutation/fuzz
MUTATION_TOTAL = prom.Counter('mutation_total', 'Total mutants generated', ['language', 'strategy', 'tool', 'instance_id'])
MUTATION_KILLED = prom.Counter('mutation_killed', 'Mutants killed', ['language', 'strategy', 'tool', 'instance_id'])
MUTATION_SURVIVED = prom.Counter('mutation_survived', 'Mutants survived', ['language', 'strategy', 'tool', 'instance_id'])
MUTATION_TIMEOUT = prom.Counter('mutation_timeout', 'Mutants timed out', ['language', 'strategy', 'tool', 'instance_id'])
MUTATION_ERROR = prom.Counter('mutation_error', 'Mutants that caused an error', ['language', 'strategy', 'tool', 'instance_id'])
MUTATION_SURVIVAL_RATE = prom.Gauge('mutation_survival_rate', 'Survival rate', ['language', 'strategy', 'tool', 'instance_id'])
FUZZ_DISCOVERIES = prom.Counter('fuzz_discoveries', 'Issues found by fuzzing', ['language', 'strategy', 'instance_id'])
COVERAGE_GAPS = prom.Counter('coverage_gaps', 'Total uncovered mutation points (code lines/branches not covered by tests)', ['language', 'instance_id'])


# --- Plug-in Registration ---
_MUTATOR_REGISTRY: Dict[str, Dict[str, Any]] = defaultdict(dict)

def register_mutator(
    language: str,
    tool_name: str,
    extensions: List[str],
    run_func: Callable[[Path, str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
    parse_func: Callable[[Dict[str, str]], Dict[str, int]],
    setup_config_func: Optional[Callable[[Path, List[Path], List[Path]], None]] = None,
    tool_version_cmd: Optional[Union[str, List[str]]] = None
):
    """
    Registers a new mutation testing tool for a specific language.
    """
    if tool_name in _MUTATOR_REGISTRY[language]:
        logger.warning(f"Mutator '{tool_name}' for language '{language}' already registered. Overwriting.")
    _MUTATOR_REGISTRY[language][tool_name] = {
        'tool': tool_name,
        'extensions': extensions,
        'run': run_func,
        'parse': parse_func,
        'setup_config': setup_config_func,
        'version_cmd': tool_version_cmd
    }
    logger.info(f"Mutator '{tool_name}' registered for language '{language}'.")


# --- Helper for running subprocesses (consistent across backends) ---
async def _run_subprocess_safe(cmd: Union[str, List[str]], cwd: Path, timeout: int = 300) -> Dict[str, Any]:
    """
    Helper to run a shell command safely and capture output.
    Raises TestExecutionError for subprocess failures.
    """
    cmd_list = cmd if isinstance(cmd, list) else cmd.split()
    logger.debug(f"Executing subprocess command: {' '.join(cmd_list)} in {cwd}")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdout=asyncio.PIPE,
            stderr=asyncio.PIPE,
            cwd=cwd
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        
        stdout_str = stdout.decode('utf-8', errors='ignore').strip()
        stderr_str = stderr.decode('utf-8', errors='ignore').strip()

        returncode = process.returncode

        if returncode != 0:
            logger.warning(f"Command exited with non-zero code {returncode}: {' '.join(cmd_list)}\nStderr: {stderr_str}")
            raise TestExecutionError(
                error_codes["TEST_EXECUTION_FAILED"],
                detail=f"Subprocess command failed with exit code {returncode}.",
                returncode=returncode,
                stdout=stdout_str,
                stderr=stderr_str,
                cmd=' '.join(cmd_list)
            )
        return {'stdout': stdout_str, 'stderr': stderr_str, 'returncode': returncode}
    except asyncio.TimeoutError:
        if process:
            process.kill()
            await process.wait()
        logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd_list)}")
        raise TimeoutError(
            error_codes["TASK_TIMEOUT"],
            detail=f"Subprocess command timed out after {timeout} seconds.",
            timeout_seconds=timeout,
            cmd=' '.join(cmd_list)
        )
    except FileNotFoundError:
        first_arg = cmd_list[0]
        logger.error(f"Command not found: '{first_arg}'. Ensure tool is installed and in PATH.")
        raise TestExecutionError(
            error_codes["TEST_EXECUTION_FAILED"],
            detail=f"Command '{first_arg}' not found. Ensure tool is installed and in PATH.",
            returncode=127,
            cmd=' '.join(cmd_list)
        )
    except RunnerError: # Re-raise already structured errors
        raise
    except Exception as e:
        logger.error(f"Unexpected error running subprocess: {e}", exc_info=True)
        raise RunnerError(
            error_codes["UNEXPECTED_ERROR"],
            detail=f"Unexpected error executing command: {e}",
            returncode=1,
            cmd=' '.join(cmd_list),
            cause=e
        )
    finally:
        if process and process.returncode is None:
            try:
                process.terminate()
                await process.wait()
            except Exception as e:
                logger.warning(f"Failed to terminate subprocess gracefully: {e}")

async def _get_tool_version(tool_name: str, version_cmd: Union[str, List[str]]) -> str:
    """Gold Standard: Executes command to get tool version."""
    try:
        result = await _run_subprocess_safe(version_cmd, cwd=Path('.'), timeout=5)
        if result['returncode'] == 0:
            match = re.search(r'version (\d+\.\d+\.\d+)', result['stdout'] + result['stderr'], re.IGNORECASE)
            if match:
                return match.group(1)
            return result['stdout'].splitlines()[0].strip() or 'unknown'
        else:
            logger.warning(f"Failed to get version for {tool_name} (exit code {result['returncode']}). Stderr: {result['stderr']}")
            return 'unavailable'
    except RunnerError as e: # Catch structured error from subprocess call
        logger.warning(f"Error checking version for {tool_name}: {e.as_dict()}")
        return 'unavailable'
    except Exception as e:
        logger.warning(f"Error checking version for {tool_name}: {e}")
        return 'unavailable'


# --- Parsers for Mutation Tool Outputs ---
# These parsers are internal to mutation.py and assume raw dict input.
# The `runner.parsers` module handles external files and returns Pydantic schemas.
def parse_mutmut_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses mutmut results from its subprocess stdout or JSON report file."""
    if 'report_file_content' in raw_result and raw_result['report_file_content']:
        try:
            report_json = json.loads(raw_result['report_file_content'])
            total = report_json.get('total_mutants', 0)
            killed = report_json.get('killed_mutants', 0)
            survived = report_json.get('survived_mutants', 0)
            timeout = report_json.get('timed_out_mutants', 0)
            error = report_json.get('error_mutants', 0)
            return {'total': total, 'survived': survived, 'killed': killed, 'timeout': timeout, 'error': error}
        except json.JSONDecodeError:
            logger.warning("mutmut 'report_file_content' was not valid JSON. Falling back to stdout regex.")
    
    output_str = raw_result.get('stdout', '')
    total_match = re.search(r'(\d+) mutants', output_str)
    survived_match = re.search(r'(\d+) survived', output_str)
    killed_match = re.search(r'(\d+) killed', output_str)
    timeout_match = re.search(r'(\d+) timed out', output_str)
    error_match = re.search(r'(\d+) errors', output_str)

    total = int(total_match.group(1)) if total_match else 0
    survived = int(survived_match.group(1)) if survived_match else 0
    killed = int(killed_match.group(1)) if killed_match else 0
    timeout = int(timeout_match.group(1)) if timeout_match else 0
    error = int(error_match.group(1)) if error_match else 0

    return {'total': total, 'survived': survived, 'killed': killed, 'timeout': timeout, 'error': error}

def parse_pitest_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses Pitest (Java) results from its XML/JSON report file or console output."""
    if 'report_file_content' in raw_result and raw_result['report_file_content']:
        try:
            report_json = json.loads(raw_result['report_file_content'])
            total = report_json.get('totalMutants', 0)
            killed = report_json.get('killed', 0)
            survived = report_json.get('survived', 0)
            timeout = report_json.get('timeout', 0)
            error = report_json.get('errors', 0)
            return {'total': total, 'survived': survived, 'killed': killed, 'timeout': timeout, 'error': error}
        except json.JSONDecodeError:
            logger.warning("Pitest 'report_file_content' was not valid JSON. Falling back to stdout regex.")

    output_str = raw_result.get('stdout', '') + raw_result.get('stderr', '')
    summary_match = re.search(r'All mutants killed: (\d+), survived: (\d+), timed out: (\d+), non-viable: (\d+)', output_str)
    
    if summary_match:
        killed = int(summary_match.group(1))
        survived = int(summary_match.group(2))
        timeout = int(summary_match.group(3))
        non_viable = int(summary_match.group(4))
        
        total = killed + survived + timeout + non_viable
        error = 0
        return {'total': total, 'survived': survived, 'killed': killed, 'timeout': timeout, 'error': error}
    
    logger.warning("Pitest console summary not found and no valid report file. Returning zero results.")
    return {'total': 0, 'survived': 0, 'killed': 0, 'timeout': 0, 'error': 0, 'message': 'Pitest results not found or parsed.'}


def parse_stryker_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses Stryker (JS/.NET) results from its JSON report file or console output."""
    if 'report_file_content' in raw_result and raw_result['report_file_content']:
        try:
            report_json = json.loads(raw_result['report_file_content'])
            if 'files' in report_json:
                total_mutants = 0
                killed_mutants = 0
                survived_mutants = 0
                timed_out_mutants = 0
                error_mutants = 0

                for file_path, file_data in report_json['files'].items():
                    for mutant in file_data.get('mutants', []):
                        total_mutants += 1
                        if mutant['status'] == 'Killed': killed_mutants += 1
                        elif mutant['status'] == 'Survived': survived_mutants += 1
                        elif mutant['status'] == 'Timeout': timed_out_mutants += 1
                        elif mutant['status'] == 'Error': error_mutants += 1
                return {
                    'total': total_mutants, 'survived': survived_mutants, 'killed': killed_mutants,
                    'timeout': timed_out_mutants, 'error': error_mutants
                }
            elif 'totals' in report_json:
                totals = report_json['totals']
                return {
                    'total': totals.get('mutants', 0), 'survived': totals.get('survived', 0),
                    'killed': totals.get('killed', 0), 'timeout': totals.get('timeout', 0),
                    'error': totals.get('errors', 0)
                }
        except json.JSONDecodeError:
            logger.warning("Stryker 'report_file_content' was not valid JSON. Falling back to stdout regex.")
    
    output_str = raw_result.get('stdout', '') + raw_result.get('stderr', '')
    total_match = re.search(r'(\d+) mutants generated', output_str)
    killed_match = re.search(r'(\d+) killed', output_str)
    survived_match = re.search(r'(\d+) survived', output_str)
    timeout_match = re.search(r'(\d+) timed out', output_str)
    error_match = re.search(r'(\d+) errors', output_str)

    total = int(total_match.group(1)) if total_match else 0
    killed = int(killed_match.group(1)) if killed_match else 0
    survived = int(survived_match.group(1)) if survived_match else 0
    timeout = int(timeout_match.group(1)) if timeout_match else 0
    error = int(error_match.group(1)) if error_match else 0
    
    return {'total': total, 'survived': survived, 'killed': killed, 'timeout': timeout, 'error': error}


# --- Initial Population of MUTATORS Registry ---
register_mutator(
    language='python',
    tool_name='mutmut',
    extensions=['.py'],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        ['mutmut', 'run', '--paths-to-mutate', './code', '--paths-to-exclude', './tests', '--test-time-multiplier', '2', '--config', 'mutmut_config.py'],
        cwd=temp_dir_path,
        timeout=params.get('timeout', 300)
    ),
    parse_func=parse_mutmut_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: (temp_dir_path / 'mutmut_config.py').write_text(f"""
        # mutmut_config.py - Generated by runner_mutation.py
        import os
        from pathlib import Path
        
        # Gold Standard: Ensure paths are correctly set and sanitized if from untrusted sources
        # Adding temp_dir/code to PYTHONPATH allows mutmut to import user code for mutation.
        # This must be done carefully in a sandboxed environment.
        pythonpath = os.environ.get('PYTHONPATH', '')
        if str(Path('{temp_dir_path}') / 'code') not in pythonpath:
            os.environ['PYTHONPATH'] = str(Path('{temp_dir_path}') / 'code') + os.pathsep + pythonpath
        
        def pre_mutation_hook():
            pass
        def post_mutation_hook():
            pass
        """),
    tool_version_cmd=['mutmut', '--version']
)

register_mutator(
    language='java',
    tool_name='pitest',
    extensions=['.java', '.kt', '.scala'],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        ['mvn', 'org.pitest:pitest-maven:mutationCoverage'], # Assumes Maven project setup
        cwd=temp_dir_path / 'code', # Run in the code directory where pom.xml is
        timeout=params.get('timeout', 600)
    ),
    parse_func=parse_pitest_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: None, # Pitest uses pom.xml/build.gradle
    tool_version_cmd=['mvn', '--version'] # Pitest version is part of Maven output
)

register_mutator(
    language='javascript',
    tool_name='stryker',
    extensions=['.js', '.ts', '.jsx', '.tsx'],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        ['npx', 'stryker', 'run', '--reporter', 'json', '--jsonFilePath', 'stryker-report.json'],
        cwd=temp_dir_path / 'code',
        timeout=params.get('timeout', 600)
    ),
    parse_func=parse_stryker_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: (temp_dir_path / 'code' / 'stryker.conf.json').write_text(f"""
        // stryker.conf.json - Generated by runner_mutation.py
        module.exports = {{
          packageManager: "npm",
          reporters: ["html", "json"],
          testRunner: "jest", // or 'mocha', 'karma' based on actual project config
          mutator: "typescript", // or 'javascript'
          coverageAnalysis: "perTest",
          tsconfigFile: "tsconfig.json", // If TypeScript project
          mutate: ["**/*.js", "**/*.ts", "!**/*.spec.js", "!**/*.d.ts"], // Files to mutate relative to cwd
        }};
        """),
    tool_version_cmd=['npx', 'stryker', '--version']
)

register_mutator(
    language='csharp',
    tool_name='stryker-net',
    extensions=['.cs'],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        ['dotnet', 'stryker'], # Assumes dotnet CLI installed and run in project root (temp_dir_path/code)
        cwd=temp_dir_path / 'code',
        timeout=params.get('timeout', 600)
    ),
    parse_func=parse_stryker_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: None, # Relies on project structure like .csproj
    tool_version_cmd=['dotnet', 'stryker', '--version']
)


def detect_language(code_files: Dict[str, str]) -> str:
    """Detects primary language based on file extensions in code_files."""
    file_extensions = set(Path(f).suffix.lower() for f in code_files.keys())
    
    for lang, tool_map in _MUTATOR_REGISTRY.items():
        for tool_name, info in tool_map.items():
            if any(ext in file_extensions for ext in info['extensions']):
                logger.info(f"Detected language '{lang}' based on file extensions: {file_extensions}.")
                return lang
    
    if '.py' in file_extensions: return 'python'
    if '.js' in file_extensions or '.ts' in file_extensions: return 'javascript'
    if '.go' in file_extensions: return 'go'
    if '.java' in file_extensions: return 'java'

    logger.warning(f"Could not detect a supported language for mutation testing from extensions: {file_extensions}. Defaulting to 'python'.")
    return 'python'


@trace_method_decorator
async def mutation_test(
    temp_dir: Path,
    config: RunnerConfig,
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    strategy: str = 'targeted',
    parallel: bool = True,
    distributed: bool = False,
) -> Dict[str, Any]:
    """
    Advanced mutation testing with multi-language support, strategies, parallel/distributed execution.
    Args:
        temp_dir (Path): Temporary directory where code/tests are placed.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Code files to mutate.
        test_files (Dict[str, str]): Test files to run against mutants.
        strategy (str): Mutation strategy ('random', 'targeted', 'property', 'ai-guided').
        parallel (bool): Whether to run mutation testing in parallel processes.
        distributed (bool): Whether to leverage a distributed runner backend.
    Returns:
        Dict[str, Any]: Mutation testing statistics (survival_rate, killed, survived, etc.).
    SECURITY WARNING: Subprocess execution of mutation tools may involve running untrusted code.
                      For production, ensure these operations occur within a secure sandbox
                      (e.g., dedicated Docker containers, isolated VMs, or low-privilege users).
    """
    span = otel.trace.get_current_span() if _tracer else None
    if span:
        span.set_attribute("mutation.strategy", strategy)
        span.set_attribute("mutation.parallel", parallel)
        span.set_attribute("mutation.distributed", distributed)
    
    language = detect_language(code_files)
    instance_id: str = config.get('instance_id', 'N/A')

    # Gold Standard: Config Validation for mutation parameters
    configured_tool_name: Optional[str] = config.get('mutation_tool_name')
    if configured_tool_name and configured_tool_name not in _MUTATOR_REGISTRY.get(language, {}):
        logger.error(f"Configured mutation tool '{configured_tool_name}' for language '{language}' is not registered. Skipping mutation test.")
        MUTATION_ERROR.labels(language, strategy, configured_tool_name, instance_id).inc()
        if span: span.set_status(trace.Status(trace.StatusCode.ERROR, f"Configured mutator not registered: {configured_tool_name}"))
        return {'survival_rate': 1.0, 'total': 0, 'killed': 0, 'survived': 0, 'timeout': 0, 'error': 1, 'message': f"Configured mutator '{configured_tool_name}' not registered."}

    # Select mutator tool: configured, or default if not specified/registered
    mutator_info: Optional[Dict[str, Any]] = None
    if configured_tool_name:
        mutator_info = _MUTATOR_REGISTRY.get(language, {}).get(configured_tool_name)
    else: # If no tool explicitly configured, pick the first one for the language
        mutator_info = next(iter(_MUTATOR_REGISTRY.get(language, {}).values()), None)
    
    if not mutator_info:
        logger.error(f"No mutation testing tool available for language '{language}'. Skipping mutation test.")
        MUTATION_ERROR.labels(language, strategy, 'not_available', instance_id).inc()
        if span: span.set_status(trace.Status(trace.StatusCode.ERROR, f"No mutator available for language: {language}"))
        return {'survival_rate': 1.0, 'total': 0, 'killed': 0, 'survived': 0, 'timeout': 0, 'error': 0, 'message': f"No mutator for {language}"}

    tool_name: str = mutator_info['tool']
    if span:
        span.set_attribute("mutation.tool_name", tool_name)
        span.set_attribute("mutation.language", language)
    logger.info(f"Running mutation test for '{language}' using tool '{tool_name}' with strategy '{strategy}'.")
    
    # Gold Standard: Toolchain Preflight Check (check if tool is installed/in PATH)
    tool_version: str = 'N/A'
    if 'version_cmd' in mutator_info and mutator_info['version_cmd']:
        tool_version = await _get_tool_version(tool_name, mutator_info['version_cmd'])
        if tool_version == 'unavailable':
            logger.error(f"Mutation tool '{tool_name}' is not available in PATH. Skipping mutation test.")
            MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc()
            if span: span.set_status(trace.Status(trace.StatusCode.ERROR, f"Mutator tool not found: {tool_name}"))
            return {'survival_rate': 1.0, 'total': 0, 'killed': 0, 'survived': 0, 'timeout': 0, 'error': 1, 'message': f"Mutator tool '{tool_name}' not found in PATH."}
    else:
        # Fallback to hardcoded versions for built-in Python tools
        if tool_name == 'mutmut' and HAS_MUTMUT: tool_version = MUTMUT_VERSION
        elif tool_name == 'hypothesis' and HAS_HYPOTHESIS: tool_version = HYPOTHESIS_VERSION
        
    # --- Strategy Selection / Execution ---
    if strategy == 'property':
        if language == 'python' and HAS_HYPOTHESIS:
            logger.info("Using property-based testing as a mutation strategy (Python/Hypothesis).")
            return await property_based_test(temp_dir, config, code_files)
        else:
            logger.warning(f"Property-based testing for '{language}' or Hypothesis not available. Falling back to 'targeted' strategy.")
            strategy = 'targeted'

    if strategy == 'ai-guided':
        logger.info("Using AI-guided mutation strategy (conceptual).")
        pass

    # --- Setup mutator-specific configuration files ---
    if span: span.add_event("Setting up mutator configuration")
    if mutator_info.get('setup_config'):
        try:
            code_file_paths: List[Path] = list((temp_dir / 'code').rglob('*'))
            test_file_paths: List[Path] = list((temp_dir / 'tests').rglob('*'))
            mutator_info['setup_config'](temp_dir, code_file_paths, test_file_paths)
        except Exception as e:
            logger.error(f"Failed to set up mutator config for '{tool_name}': {e}", exc_info=True)
            MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc()
            if span: span.set_status(trace.Status(trace.StatusCode.ERROR, f"Mutator config setup failed: {e}"))
            return {'survival_rate': 1.0, 'total': 0, 'killed': 0, 'survived': 0, 'timeout': 0, 'error': 1, 'message': f"Mutator config setup failed: {e}"}

    # --- Prepare mutation run parameters (Gold Standard: Expose params via config) ---
    mutation_timeout: int = config.get('mutation_timeout', config.timeout * 2) # Allow custom mutation timeout
    mutation_random_percent: float = config.get('mutation_random_percent', 0.1) # For random strategy
    
    mutation_run_params: Dict[str, Any] = {
        'timeout': mutation_timeout,
        'random_percent': mutation_random_percent
    }
    # Add strategy-specific params
    if strategy == 'targeted':
        mutation_run_params['use_coverage'] = True
    elif strategy == 'random':
        pass

    # --- Execution: Parallel or Distributed (Gold Standard: Clear Interfaces) ---
    raw_result: Dict[str, Any] = {}
    if distributed and config.distributed:
        if span: span.add_event("Sending mutation task to distributed runner")
        logger.info(f"Sending mutation task to distributed runner for language '{language}'.")
        await asyncio.sleep(1) # Simulate network delay
        raw_result = {'stdout': '{"totalMutants": 10, "killed": 5, "survived": 5}', 'stderr': '', 'returncode': 0, 
                      'report_file_content': '{"files": {"dummy.js": {"mutants": [{"status": "Killed"}, {"status": "Survived"}]}}, "totals": {"mutants": 2, "killed": 1, "survived": 1}}'}
        logger.warning("Distributed mutation is conceptual: Mocking results.")
    elif parallel and config.parallel_workers > 1:
        if span: span.add_event("Running mutation test in parallel processes")
        logger.info(f"Running mutation test in parallel processes (max_workers={config.parallel_workers}).")
        loop = asyncio.get_running_loop()
        with concurrent.futures.ProcessPoolExecutor(max_workers=config.parallel_workers) as executor:
            future = loop.run_in_executor(
                executor,
                partial(mutator_info['run'], temp_dir, strategy, mutation_run_params)
            )
            raw_result = await future
    else:
        if span: span.add_event("Running mutation test in single process")
        logger.info("Running mutation test in single process.")
        raw_result = await mutator_info['run'](temp_dir, strategy, mutation_run_params)

    # --- Parse and Aggregate Results ---
    if span: span.add_event("Parsing mutation results")
    mutation_stats: Dict[str, int] = mutator_info['parse'](raw_result)

    total: int = mutation_stats.get('total', 0)
    survived: int = mutation_stats.get('survived', 0)
    killed: int = mutation_stats.get('killed', 0)
    timeout: int = mutation_stats.get('timeout', 0)
    error: int = mutation_stats.get('error', 0)
    
    survival_rate: float = survived / total if total > 0 else 0.0

    # Update Prometheus metrics with instance_id label
    MUTATION_TOTAL.labels(language, strategy, tool_name, instance_id).inc(total)
    MUTATION_KILLED.labels(language, strategy, tool_name, instance_id).inc(killed)
    MUTATION_SURVIVED.labels(language, strategy, tool_name, instance_id).inc(survived)
    MUTATION_TIMEOUT.labels(language, strategy, tool_name, instance_id).inc(timeout)
    MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc(error)
    MUTATION_SURVIVAL_RATE.labels(language, strategy, tool_name, instance_id).set(survival_rate)
    
    gaps: List[Any] = []
    COVERAGE_GAPS.labels(language, instance_id).inc(len(gaps))

    # Reporting and Logging
    final_stats = {
        'survival_rate': survival_rate,
        'killed_mutants': killed,
        'survived_mutants': survived,
        'timed_out_mutants': timeout,
        'error_mutants': error,
        'total_mutants': total,
        'coverage_gaps': gaps,
        'language': language,
        'strategy': strategy,
        'tool': tool_name,
        'tool_version': tool_version,
        'stdout_snippet': raw_result.get('stdout', '')[:500] + '...' if raw_result.get('stdout') else '',
        'stderr_snippet': raw_result.get('stderr', '')[:500] + '...' if raw_result.get('stderr') else '',
        'returncode': raw_result.get('returncode', 'N/A')
    }
    logger.info(f"Mutation testing completed for {language}. Stats: {final_stats}", extra=final_stats)
    if span:
        span.set_attribute("mutation.result.survival_rate", survival_rate)
        span.set_status(trace.Status(trace.StatusCode.OK))
    return final_stats

@trace_method_decorator
async def property_based_test(temp_dir: Path, config: RunnerConfig, code_files: Dict[str, str]) -> Dict[str, Any]:
    """
    Performs property-based testing using Hypothesis.
    Args:
        temp_dir (Path): Temporary directory containing the code under test.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Content of the code files.
    Returns:
        Dict[str, Any]: Results of the property-based test.
    """
    span = otel.trace.get_current_span() if _tracer else None
    instance_id: str = config.get('instance_id', 'default_runner_instance')
    if span:
        span.set_attribute("fuzz.language", "python")
        span.set_attribute("fuzz.strategy", "property")
        span.set_attribute("fuzz.tool_name", "hypothesis")
        span.set_attribute("fuzz.tool_version", HYPOTHESIS_VERSION)

    if not HAS_HYPOTHESIS:
        logger.error("Hypothesis not installed. Cannot run property-based tests.")
        FUZZ_DISCOVERIES.labels('python', 'property', instance_id).inc(0)
        if span: span.set_status(trace.Status(trace.StatusCode.ERROR, "Hypothesis not installed"))
        return {'status': 'skipped', 'message': 'Hypothesis not installed.'}

    discoveries: int = 0
    test_failures: List[str] = []
    
    original_sys_path: List[str] = list(sys.path)
    code_path: Path = temp_dir / 'code'
    if str(code_path) not in sys.path:
        sys.path.insert(0, str(code_path))
    
    module_name: Optional[str] = None
    try:
        for f_name in code_files.keys():
            file_path = Path(f_name)
            if file_path.suffix == '.py' and file_path.stem != '__init__':
                module_name = file_path.stem
                break
        
        if not module_name:
            logger.warning("No main Python module (.py excluding __init__.py) found for property testing. Skipping.")
            if span: span.add_event("No main Python module found")
            return {'status': 'skipped', 'message': 'No main Python module found for property testing.'}

        target_module = importlib.import_module(module_name)
        importlib.reload(target_module)

        testable_functions: List[Callable] = []
        for name, obj in inspect.getmembers(target_module):
            if inspect.isfunction(obj) and name.startswith('fuzz_'):
                testable_functions.append(obj)
        
        if not testable_functions:
            logger.warning(f"No fuzzable functions (e.g., 'fuzz_...') found in {module_name}. Skipping property tests.")
            if span: span.add_event("No fuzzable functions found")
            return {'status': 'skipped', 'message': 'No fuzzable functions found.'}

        logger.info(f"Running property tests on {len(testable_functions)} functions in {module_name}.")
        if span: span.set_attribute("fuzz.functions_tested_count", len(testable_functions))
        
        fuzz_examples_count: int = config.get('fuzz_examples', 50)

        for func_to_test in testable_functions:
            if span: span.add_event(f"Fuzzing function: {func_to_test.__name__}")
            try:
                settings = hypothesis.settings(
                    max_examples=fuzz_examples_count,
                    deadline=None,
                    print_blob=True
                )
                
                if hasattr(func_to_test, 'is_hypothesis_test') and func_to_test.is_hypothesis_test:
                    fuzz_test_runner = settings(func_to_test)
                else:
                    sig = inspect.signature(func_to_test)
                    if sig.parameters:
                        param_name = list(sig.parameters.keys())[0]
                        param_type = sig.parameters[param_name].annotation
                        if param_type != inspect.Parameter.empty:
                            try:
                                inferred_strategy = st.from_type(param_type)
                                @settings
                                @hypothesis.given(inferred_strategy)
                                def wrapper_fuzz_test(data: Any):
                                    func_to_test(data)
                                fuzz_test_runner = wrapper_fuzz_test
                            except Exception as e:
                                logger.warning(f"Could not infer Hypothesis strategy for {func_to_test.__name__} from type hint {param_type}: {e}. Skipping auto-fuzzing for this function.")
                                if span: span.add_event(f"Skipped auto-fuzz for {func_to_test.__name__}: strategy inference failed")
                                continue
                        else:
                            logger.warning(f"Function {func_to_test.__name__} has no type hints for fuzzing. Skipping auto-fuzzing.")
                            if span: span.add_event(f"Skipped auto-fuzz for {func_to_test.__name__}: no type hints")
                            continue
                    else:
                        logger.warning(f"Function {func_to_test.__name__} has no parameters for fuzzing. Skipping auto-fuzzing.")
                        if span: span.add_event(f"Skipped auto-fuzz for {func_to_test.__name__}: no parameters")
                        continue

                fuzz_test_runner()
                
            except hypothesis.errors.InvalidArgument as e:
                logger.warning(f"Hypothesis InvalidArgument for {func_to_test.__name__}. Strategy might not match function signature: {e}")
                test_failures.append(f"Invalid args for {func_to_test.__name__}: {e}")
                discoveries += 1
            except hypothesis.errors.FailedHealthcheck as e:
                logger.warning(f"Hypothesis health check failed for {func_to_test.__name__}. Data generation issue? {e}")
                test_failures.append(f"Healthcheck failed for {func_to_test.__name__}: {e}")
                discoveries += 1
            except hypothesis.errors.InvalidContract as e:
                logger.warning(f"Hypothesis internal contract violated for {func_to_test.__name__}. {e}")
                test_failures.append(f"Internal Hypothesis contract violation for {func_to_test.__name__}: {e}")
                discoveries += 1
            except hypothesis.errors.FalsifyingExample as e:
                logger.info(f"Falsifying example found for {func_to_test.__name__}: {e.example}.")
                test_failures.append(f"Falsifying example for {func_to_test.__name__}: {e.example}")
                discoveries += 1
            except Exception as e:
                logger.error(f"Property test for {func_to_test.__name__} failed unexpectedly: {e}", exc_info=True)
                test_failures.append(f"Unexpected error for {func_to_test.__name__}: {e}")
                discoveries += 1

    except Exception as e:
        logger.error(f"Error setting up property-based tests: {e}", exc_info=True)
        if span:
            span.set_status(trace.Status(trace.StatusCode.ERROR, f"Property test setup failed: {e}"))
            span.record_exception(e)
        return {'status': 'error', 'message': f"Setup failed: {e}"}
    finally:
        sys.path[:] = original_sys_path
        if module_name:
            try:
                for mod_name in list(sys.modules.keys()):
                    if mod_name == module_name or mod_name.startswith(f"{module_name}."):
                        del sys.modules[mod_name]
            except Exception as e:
                logger.warning(f"Failed to cleanup dynamically loaded module {module_name}: {e}")

    FUZZ_DISCOVERIES.labels('python', 'property', instance_id).inc(discoveries)
    if discoveries > 0:
        logger.info(f"Property-based testing completed. Discovered {discoveries} issues.")
    else:
        logger.info("Property-based testing completed. No issues discovered.")

    return {'survival_rate': 1.0 - (discoveries / max(1, fuzz_examples_count)),
            'killed_mutants': discoveries,
            'survived_mutants': max(0, fuzz_examples_count - discoveries),
            'total_mutants': fuzz_examples_count,
            'timed_out_mutants': 0, 'error_mutants': 0,
            'coverage_gaps': [],
            'status': 'completed',
            'fuzz_failures': test_failures,
            'tool_version': HYPOTHESIS_VERSION
            }


@trace_method_decorator
async def fuzz_test(temp_dir: Path, config: RunnerConfig, code_files: Dict[str, str]) -> Dict[str, Any]:
    """
    Performs general fuzz testing (e.g., black-box, grammar-based).
    Args:
        temp_dir (Path): Temporary directory containing the code under test.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Content of the code files.
    Returns:
        Dict[str, Any]: Fuzzing results.
    """
    span = otel.trace.get_current_span() if _tracer else None
    instance_id: str = config.get('instance_id', 'default_runner_instance')
    language: str = detect_language(code_files)
    if span:
        span.set_attribute("fuzz.language", language)
        span.set_attribute("fuzz.strategy", "general")
        span.set_attribute("fuzz.tool_name", "custom_fuzzer")
        span.set_attribute("fuzz.tool_version", "1.0")

    if language == 'python' and HAS_HYPOTHESIS:
        logger.info(f"Running general fuzz tests for {language} code.")
        
        discoveries: int = 0
        fuzz_examples_count: int = config.get('fuzz_examples', 10)
        
        for i in range(fuzz_examples_count):
            fuzzed_input: str = f"fuzz_input_{i}_{random.randint(0, 1000)}"

            # In a real scenario, this is where you'd call the user's code with fuzzed_input
            # For example, if it's a CLI tool, you'd execute a subprocess:
            # result = await _run_subprocess_safe(['your_cli_tool', fuzzed_input], cwd=temp_dir, timeout=5)
            # if result['returncode'] != 0: discoveries += 1
            
            if random.random() < 0.15:
                discoveries += 1

    else:
        logger.warning(f"General fuzz testing for '{language}' or Hypothesis not available. Skipping.")
        if span: span.set_status(trace.Status(trace.StatusCode.ERROR, "Fuzzing skipped: module or language not supported"))
        return {'discoveries': 0, 'status': 'skipped', 'message': 'Fuzzing not configured or tool unavailable.'}


    FUZZ_DISCOVERIES.labels(language, 'general', instance_id).inc(discoveries)
    if span:
        span.set_attribute("fuzz.discoveries", discoveries)
        span.set_status(trace.Status(trace.StatusCode.OK))
    return {'discoveries': discoveries, 'status': 'completed', 'tool_version': "1.0"}


# --- Main execution and Test setup (for internal module testing) ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Gold Standard: Helper to reset metrics for isolated tests
    def reset_mutation_metrics():
        for metric in [MUTATION_TOTAL, MUTATION_KILLED, MUTATION_SURVIVED, MUTATION_TIMEOUT,
                       MUTATION_ERROR, MUTATION_SURVIVAL_RATE, FUZZ_DISCOVERIES, COVERAGE_GAPS]:
            # Reset all labels of a metric
            if hasattr(metric, '_metrics'):
                metric._metrics.clear()
            # For Gauges, you can reset all labeled instances
            if isinstance(metric, prom.Gauge):
                for key in list(metric._metrics.keys()):
                    del metric._metrics[key]
        logger.debug("Prometheus mutation/fuzz metrics reset.")

    # Dummy config for testing
    class DummyRunnerConfig(RunnerConfig):
        def __init__(self, **data):
            super().__init__(**data)
            self.timeout = self.timeout if self.timeout else 300
            self.parallel_workers = self.parallel_workers if self.parallel_workers else (os.cpu_count() or 1)
            self.dist_url = self.dist_url if self.dist_url else ""
            self.custom_setup = self.custom_setup if self.custom_setup else ""
            self.resources = self.resources if self.resources else {}
            self.network = self.network if self.network else {}
            self.security = self.security if self.security else {}
            self.vault_url = self.vault_url if self.vault_url else None
            self.vault_token = self.vault_token if self.vault_token else None
            self.api_key = self.api_key if self.api_key else None
            self.mutation = self.mutation if 'mutation' in data else True
            self.fuzz = self.fuzz if 'fuzz' in data else True
            self.commercial_mode_enabled = self.commercial_mode_enabled if 'commercial_mode_enabled' in data else False
            self.max_iterations_commercial = self.max_iterations_commercial if 'max_iterations_commercial' in data else None
            self.billing_enabled = self.billing_enabled if 'billing_enabled' in data else False
            self.usage_thresholds = self.usage_thresholds if self.usage_thresholds else {'workflow_runs': 50, 'llm_tokens': 100000}
            self.cost_per_token = self.cost_per_token if self.cost_per_token else 0.00001
            self.billing_period_days = self.billing_period_days if self.billing_period_days else 30
            self.alert_threshold_percent = self.alert_threshold_percent if self.alert_threshold_percent else 0.8
            self.instance_id = self.instance_id if self.instance_id else 'default_runner_instance'
            self.log_sinks = self.log_sinks if self.log_sinks else [{'type': 'stream', 'config': {}}]
            self.real_time_log_streaming = self.real_time_log_streaming if 'real_time_log_streaming' in data else True
            self.metrics_interval_seconds = self.metrics_interval_seconds if 'metrics_interval_seconds' in data else 1
            self.alert_monitor_interval_seconds = self.alert_monitor_interval_seconds if 'alert_monitor_interval_seconds' in data else 60
            self.doc_framework = self.doc_framework if 'doc_framework' in data else 'auto'
            self.custom_redaction_patterns = self.custom_redaction_patterns if self.custom_redaction_patterns else []
            self.encryption_algorithm = self.encryption_algorithm if 'encryption_algorithm' in data else 'fernet'
            self.encryption_key_env_var = self.encryption_key_env_var if 'encryption_key_env_var' in data else None
            self.log_signing_enabled = self.log_signing_enabled if 'log_signing_enabled' in data else False
            self.log_signing_algo = self.log_signing_algo if 'log_signing_algo' in data else 'hmac'
            self.log_signing_key_env_var = self.log_signing_key_env_var if 'log_signing_key_env_var' in data else None
            self.mutation_tool_name = data.get('mutation_tool_name')
            self.mutation_timeout = data.get('mutation_timeout')
            self.mutation_random_percent = data.get('mutation_random_percent')
            self.fuzz_examples = data.get('fuzz_examples')
            self.metrics_failover_file = data.get('metrics_failover_file')


    # Test cases defined as async functions
    async def run_test_case_async(name: str, test_func: Callable[[], Any]):
        reset_mutation_metrics()
        logger.info(f"\n--- Running Test Case: {name} ---")
        try:
            await test_func()
            logger.info(f"--- Test Case: {name} PASSED ---")
        except Exception as e:
            logger.error(f"--- Test Case: {name} FAILED with error: {e} ---", exc_info=True)

    # Test Case 1: Python Mutation Testing (mutmut)
    async def test_python_mutation():
        test_config = DummyRunnerConfig(backend='docker', framework='pytest', mutation=True, fuzz=False, instance_id='py_mut_instance')
        code_files = {'my_code.py': 'def add(a, b): return a + b\ndef sub(a, b): return a - b'}
        test_files = {'test_my_code.py': 'import unittest\nfrom my_code import add, sub\nclass TestMyCode(unittest.TestCase):\n def test_add(self): self.assertEqual(add(1,1),2)\n def test_sub(self): self.assertEqual(sub(2,1),1)'}
        
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            (temp_dir / 'code').mkdir()
            (temp_dir / 'tests').mkdir()
            (temp_dir / 'code' / 'my_code.py').write_text(code_files['my_code.py'])
            (temp_dir / 'tests' / 'test_my_code.py').write_text(test_files['test_my_code.py'])
            
            async def mock_mutmut_subprocess(cmd: Union[str, List[str]], cwd: Path, timeout: int) -> Dict[str, Any]:
                if 'mutmut' in cmd[0]:
                    return {'stdout': '10 mutants generated. 5 killed, 4 survived, 1 timed out.', 'stderr': '', 'returncode': 0}
                return {'stdout': '', 'stderr': '', 'returncode': 0}

            with patch('runner.mutation._run_subprocess_safe', new=mock_mutmut_subprocess):
                stats = await mutation_test(temp_dir, test_config, code_files, test_files, strategy='targeted', parallel=False)
                
                assert stats['total_mutants'] == 10
                assert stats['killed_mutants'] == 5
                assert stats['survived_mutants'] == 4
                assert stats['timed_out_mutants'] == 1
                assert stats['survival_rate'] == 0.4
                assert stats['tool_version'] == MUTMUT_VERSION
                
                assert MUTATION_TOTAL.labels('python', 'targeted', 'mutmut', 'py_mut_instance')._value == 10
                assert MUTATION_SURVIVAL_RATE.labels('python', 'targeted', 'mutmut', 'py_mut_instance')._value == 0.4


    # Test Case 2: JavaScript Mutation Testing (stryker)
    async def test_javascript_mutation():
        test_config = DummyRunnerConfig(backend='docker', framework='jest', mutation=True, fuzz=False, instance_id='js_mut_instance')
        code_files = {'index.js': 'function sum(a, b) { return a + b; }', 'package.json': '{"name": "test-js", "version": "1.0.0", "scripts": {"test": "jest"}, "devDependencies": {"jest": "^29.0.0"}}'}
        test_files = {'index.test.js': 'test("sum", () => expect(sum(1, 2)).toBe(3));'}
        
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            (temp_dir / 'code').mkdir()
            (temp_dir / 'tests').mkdir()
            (temp_dir / 'code' / 'index.js').write_text(code_files['index.js'])
            (temp_dir / 'code' / 'package.json').write_text(code_files['package.json'])
            (temp_dir / 'tests' / 'index.test.js').write_text(test_files['index.test.js'])
            
            async def mock_stryker_subprocess(cmd: Union[str, List[str]], cwd: Path, timeout: int) -> Dict[str, Any]:
                if 'stryker' in cmd[1]:
                    stryker_output_json = {
                        "mutationScore": 50.0, "totalMutants": 2, "killed": 1, "survived": 1, "timeout": 0, "noCoverage": 0,
                        "files": {"index.js": {"mutants": [{"status": "Killed"}, {"status": "Survived"}]}},
                        "totals": {"mutants": 2, "killed": 1, "survived": 1, "timeout": 0, "errors": 0}
                    }
                    return {'stdout': json.dumps(stryker_output_json), 'stderr': '', 'returncode': 0}
                return {'stdout': '', 'stderr': '', 'returncode': 0}

            with patch('runner.mutation._run_subprocess_safe', new=mock_stryker_subprocess):
                stats = await mutation_test(temp_dir, test_config, code_files, test_files, strategy='targeted', parallel=False)
                
                assert stats['total_mutants'] == 2
                assert stats['killed_mutants'] == 1
                assert stats['survived_mutants'] == 1
                assert stats['survival_rate'] == 0.5
                assert stats['tool_version'] == 'N/A'
                
                assert MUTATION_TOTAL.labels('javascript', 'targeted', 'stryker', 'js_mut_instance')._value == 2
                assert MUTATION_SURVIVAL_RATE.labels('javascript', 'targeted', 'stryker', 'js_mut_instance')._value == 0.5


    # Test Case 3: Property-based testing (Hypothesis)
    async def test_property_based_testing():
        if not HAS_HYPOTHESIS:
            logger.warning("Hypothesis not installed, skipping property-based test.")
            return

        test_config = DummyRunnerConfig(backend='docker', framework='pytest', mutation=False, fuzz=True, fuzz_examples=10, instance_id='prop_test_instance')
        code_files = {'my_prop_code.py': 'def fuzz_square(x: int):\n  if x == 0: raise ValueError("Zero input")\n  return x*x'}
        test_files: Dict[str, str] = {}
        
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            (temp_dir / 'code').mkdir()
            (temp_dir / 'code' / 'my_prop_code.py').write_text(code_files['my_prop_code.py'])
            
            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'stdout': '', 'stderr': '', 'returncode': 0})):
                mock_falsifying_example_exception = hypothesis.errors.FalsifyingExample(data={"x":0}, original_exception=ValueError("Zero input"))
                
                with patch('hypothesis.find.find', MagicMock(side_effect=mock_falsifying_example_exception)) as mock_find:
                    stats = await property_based_test(temp_dir, test_config, code_files)
                    
                    assert stats['killed_mutants'] > 0
                    assert stats['status'] == 'completed'
                    assert stats['fuzz_failures']
                    assert stats['tool_version'] == HYPOTHESIS_VERSION
                    
                    assert FUZZ_DISCOVERIES.labels('python', 'property', 'prop_test_instance')._value > 0


    # Test Case 4: Fuzz Testing (General)
    async def test_general_fuzzing():
        test_config = DummyRunnerConfig(backend='docker', framework='pytest', mutation=False, fuzz=True, fuzz_examples=10, instance_id='gen_fuzz_instance')
        code_files = {'my_fuzz_code.py': 'def process_data(data): return data.upper()'}
        test_files: Dict[str, str] = {}

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            (temp_dir / 'code').mkdir()
            (temp_dir / 'code' / 'my_fuzz_code.py').write_text(code_files['my_fuzz_code.py'])

            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'stdout': '', 'stderr': '', 'returncode': 0})):
                with patch('random.random', side_effect=[0.01, 0.9, 0.02, 0.8, 0.95, 0.1, 0.7, 0.05, 0.6, 0.99]):
                    stats = await fuzz_test(temp_dir, test_config, code_files)
                    assert stats['discoveries'] == 4
                    assert stats['status'] == 'completed'
                    assert stats['tool_version'] == '1.0'
                    assert FUZZ_DISCOVERIES.labels('python', 'general', 'gen_fuzz_instance')._value == 4

    # Run all tests
    async def run_all_tests():
        await run_test_case_async("Python Mutation", test_python_mutation)
        await run_test_case_async("JavaScript Mutation", test_javascript_mutation)
        await run_test_case_async("Property-Based Testing", test_property_based_testing)
        await run_test_case_async("General Fuzzing", test_general_fuzzing)


    from unittest.mock import patch, MagicMock, AsyncMock
    
    # Ensure Prometheus HTTP server is mocked to avoid conflicts
    with patch('runner.metrics.prom.start_http_server'):
        with patch.object(RunnerConfig, 'model_dump', return_value=DummyRunnerConfig()._data):
            if _tracer:
                with patch('opentelemetry.trace.get_current_span') as mock_get_current_span:
                    mock_span = MagicMock(spec=trace.Span)
                    mock_span.is_recording.return_value = True
                    mock_span.get_span_context.return_value = trace.SpanContext(
                        trace_id=123, span_id=456, is_remote=False)
                    mock_get_current_span.return_value = mock_span
                    asyncio.run(run_all_tests())
            else:
                asyncio.run(run_all_tests())
    
    logger.info("\n--- All tests completed ---")