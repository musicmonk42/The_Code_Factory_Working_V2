import os
import subprocess
import json
import logging
import sys
import shutil
from typing import Dict, List, Any, Optional, Tuple, Callable
from datetime import datetime
from shlex import quote
import hashlib
import asyncio

# Make Redis optional
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

# --- Global Production Mode Flag (from analyzer.py) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)

# --- Custom Exceptions for graceful error handling ---
class SecurityAnalysisError(RuntimeError):
    """
    Custom exception for errors during security tool execution.
    """
    def __init__(self, message: str, tool: str, original_exception: Optional[Exception] = None):
        super().__init__(f"[SECURITY] {message}")
        self.tool = tool
        self.original_exception = original_exception

class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[SECURITY] {message}")
        try:
            # We need to import alert_operator here in case the top-level import fails
            from .core_utils import alert_operator
            alert_operator(message, alert_level)
        except Exception:
            pass

# --- Centralized Utilities (replacing placeholders) ---
try:
    from .core_utils import alert_operator, scrub_secrets
    from .core_secrets import SECRETS_MANAGER
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency for core_security: {e}. Aborting startup.")
    try:
        from .core_utils import alert_operator
        alert_operator(f"CRITICAL: Security analysis missing core dependency: {e}. Aborting.", level="CRITICAL")
    except Exception:
        pass
    raise RuntimeError("[CRITICAL][SECURITY] Missing core dependency") from e

# --- Caching: Redis Client Initialization ---
REDIS_CLIENT = None
if REDIS_AVAILABLE:
    try:
        REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True
        )
    except Exception as e:
        logger.warning(f"Failed to connect to Redis for caching: {e}. Caching will be disabled.")
        REDIS_CLIENT = None
else:
    logger.info("Redis not available - caching disabled")

# --- Standardized Subprocess Helper ---
def _run_cmd(argv: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """
    Runs a subprocess with standardized, safe parameters.
    Args:
        argv (List[str]): Command arguments.
        timeout (int): Timeout in seconds.
    Returns:
        subprocess.CompletedProcess: The result object.
    """
    logger.info(f"Executing command: {argv}")
    return subprocess.run(
        argv,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
        shell=False
    )

def _tool_path(tool_name: str) -> Optional[str]:
    """Find the full path of a tool, or None if not found."""
    from shutil import which
    return which(tool_name)

def _deep_scrub(data: Any) -> Any:
    """
    Recursively scrubs secrets from nested data structures.
    Assumes scrub_secrets provided by core_utils can handle string secrets, but this ensures nested objects are scrubbed too.
    """
    if isinstance(data, dict):
        return {k: _deep_scrub(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_deep_scrub(item) for item in data]
    elif isinstance(data, str):
        return scrub_secrets(data)
    else:
        return data

def _norm_result(tool: str, severity: str, message: str, path: Optional[str] = None, line: Optional[int] = None, timeout: bool = False) -> Dict[str, Any]:
    """
    Normalizes a security finding into a consistent dictionary format.
    """
    return {
        "tool": tool,
        "severity": severity,
        "message": message,
        "path": path,
        "line": line,
        "timeout": timeout
    }


class SecurityAnalyzer:
    """
    Performs security analysis on a codebase, integrating with tools like Bandit,
    pip-audit, and Snyk, and provides health checking for security components.
    """
    def __init__(self, project_root: str):
        if not os.path.isdir(project_root):
            raise AnalyzerCriticalError(f"Project root '{project_root}' is not a valid directory. Aborting security analysis.")
        self.project_root = os.path.abspath(project_root)
        logger.info(f"SecurityAnalyzer initialized for project: {self.project_root}")
        self._check_tool_availability_on_init()

    def _check_tool_availability_on_init(self):
        tools_missing = []
        tool_checks = {
            'Bandit': ['bandit', '--version'],
            'pip-audit': ['pip-audit', '--version'],
            'Snyk': ['snyk', '--version']
        }
        required_versions = {
            'Bandit': os.getenv("BANDIT_VERSION"),  # Optional: Pin versions with env vars
            'pip-audit': os.getenv("PIP_AUDIT_VERSION"),
            'Snyk': os.getenv("SNYK_VERSION"),
        }
        for tool, command in tool_checks.items():
            tool_path = _tool_path(command[0])
            if not tool_path:
                tools_missing.append(f"{tool} (Not found in PATH)")
                logger.critical(f"CRITICAL: {tool} tool is NOT available in PATH.", extra={'component': tool, 'status': 'Unavailable'})
                continue
            try:
                result = _run_cmd(command, timeout=10)
                if result.returncode != 0:
                     raise RuntimeError(f"Command '{' '.join(command)}' failed with exit code {result.returncode}. Output: {result.stdout.strip() + result.stderr.strip()}")
                version = result.stdout.strip()
                if required_versions.get(tool) and required_versions[tool] not in version:
                    tools_missing.append(f"{tool} (Unexpected version: {version})")
                    logger.critical(f"CRITICAL: {tool} tool version mismatch. Expected: {required_versions[tool]}, Got: {version}",
                                    extra={'component': tool, 'status': 'Version mismatch'})
                else:
                    logger.info(f"{tool} tool is available (version: {version}).", extra={'component': tool, 'status': 'Available'})
            except subprocess.TimeoutExpired:
                tools_missing.append(f"{tool} (Error: Timeout)")
                logger.critical(f"CRITICAL: {tool} tool timed out during availability check.", extra={'component': tool, 'status': 'Unavailable'})
            except Exception as e:
                tools_missing.append(f"{tool} (Error: {e})")
                logger.critical(f"CRITICAL: {tool} tool check failed. {e}", extra={'component': tool, 'status': 'Unavailable'})
        if tools_missing:
            logger.critical(f"CRITICAL: Required security analysis tools are missing or incorrect: {', '.join(tools_missing)}. Aborting startup.")
            alert_operator(f"CRITICAL: Security Analyzer: Missing or incorrect tools: {', '.join(tools_missing)}. Aborting.", level="CRITICAL")
            raise RuntimeError("[CRITICAL][SECURITY] Missing or incorrect security tools")

    def _run_subprocess_safely(self, command: List[str], description: str) -> Tuple[bool, str]:
        logger.info(f"Running {description}: {command}")
        sanitized_command = [quote(c) for c in command]
        tool_path = _tool_path(command[0])
        if not tool_path:
            logger.error(f"Security tool '{command[0]}' not found in PATH for {description}.", extra={'command': ' '.join(sanitized_command)})
            alert_operator(f"CRITICAL: Security tool '{command[0]}' not found during {description}.", level="CRITICAL")
            raise SecurityAnalysisError(f"Security tool '{command[0]}' not found.", tool=command[0])
        
        argv = [tool_path] + command[1:]
        
        try:
            result = _run_cmd(argv, timeout=300)
            if result.returncode == 0:
                logger.debug(f"{description} succeeded.", extra={'command': ' '.join(sanitized_command)})
                return True, result.stdout.strip()
            else:
                full_output = f"stdout:\n{result.stdout.strip()}\n\nstderr:\n{result.stderr.strip()}"
                logger.error(f"{description} failed (exit code {result.returncode}):\n{full_output}", extra={'command': ' '.join(sanitized_command)})
                return False, full_output
        except subprocess.TimeoutExpired:
            logger.error(f"{description} timed out after {300} seconds.", extra={'command': ' '.join(sanitized_command)})
            alert_operator(f"CRITICAL: Security tool '{command[0]}' timed out during {description}.", level="CRITICAL")
            raise SecurityAnalysisError(f"Security tool '{command[0]}' timed out.", tool=command[0])
        except Exception as e:
            logger.error(f"Unexpected error running {description}: {e}", exc_info=True, extra={'command': ' '.join(sanitized_command)})
            alert_operator(f"CRITICAL: Unexpected error during {description}: {e}.", level="CRITICAL")
            raise SecurityAnalysisError(f"Unexpected error running {description}: {e}", tool=command[0], original_exception=e)

    async def _run_tools_in_parallel(self):
        """
        Run Bandit, pip-audit, and Snyk in parallel for performance.
        """
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, self._run_bandit),
            loop.run_in_executor(None, self._run_pip_audit),
            loop.run_in_executor(None, self._run_snyk)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        bandit_issues, pip_vulns, snyk_vulns = [], [], []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                tool = ['Bandit', 'pip-audit', 'Snyk'][idx]
                logger.error(f"{tool} scan raised an exception: {result}")
            else:
                if idx == 0: bandit_issues = result
                elif idx == 1: pip_vulns = result
                elif idx == 2: snyk_vulns = result
        return bandit_issues, pip_vulns, snyk_vulns

    def _run_bandit(self) -> List[Dict[str, Any]]:
        from .core_audit import audit_logger
        logger.info(f"Running Bandit on {self.project_root}...")
        audit_logger.log_event("bandit_scan_start", project_root=self.project_root)
        try:
            success, output = self._run_subprocess_safely(
                ['bandit', '-r', self.project_root, '-f', 'json', '-q'],
                description="Bandit static analysis"
            )
            if not success:
                raise SecurityAnalysisError(f"Bandit scan failed: {output}", tool="Bandit")
            bandit_output = json.loads(output)
            issues = bandit_output.get('results', [])
            logger.info(f"Bandit scan complete. Found {len(issues)} issues.")
            audit_logger.log_event("bandit_scan_complete", project_root=self.project_root, issues_found=len(issues))
            return issues
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Bandit JSON output: {e}. Raw output: {output[:500]}...")
            raise SecurityAnalysisError(f"Failed to parse Bandit output: {e}", tool="Bandit", original_exception=e)
        except SecurityAnalysisError:
            raise
        except Exception as e:
            raise SecurityAnalysisError(f"Unexpected error running Bandit: {e}", tool="Bandit", original_exception=e)

    def _run_pip_audit(self) -> List[Dict[str, Any]]:
        from .core_audit import audit_logger
        logger.info("Running pip-audit for dependency vulnerabilities...")
        audit_logger.log_event("pip_audit_scan_start", project_root=self.project_root)
        try:
            success, output = self._run_subprocess_safely(
                ['pip-audit', '--json'],
                description="pip-audit dependency scan"
            )
            if not success:
                try:
                    pip_audit_output = json.loads(output)
                    if pip_audit_output.get('vulnerabilities'):
                        logger.info("pip-audit found vulnerabilities (non-zero exit code but valid results).")
                        return pip_audit_output.get('vulnerabilities', [])
                except json.JSONDecodeError:
                    raise SecurityAnalysisError(f"pip-audit scan failed: {output}", tool="pip-audit")
            pip_audit_output = json.loads(output)
            vulnerabilities = pip_audit_output.get('vulnerabilities', [])
            logger.info(f"pip-audit complete. Found {len(vulnerabilities)} vulnerabilities.")
            audit_logger.log_event("pip_audit_scan_complete", project_root=self.project_root, vulnerabilities_found=len(vulnerabilities))
            return vulnerabilities
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse pip-audit JSON output: {e}. Raw output: {output[:500]}...")
            raise SecurityAnalysisError(f"Failed to parse pip-audit output: {e}", tool="pip-audit", original_exception=e)
        except SecurityAnalysisError:
            raise
        except Exception as e:
            raise SecurityAnalysisError(f"Unexpected error running pip-audit: {e}", tool="pip-audit", original_exception=e)

    def _run_snyk(self) -> List[Dict[str, Any]]:
        from .core_audit import audit_logger
        logger.info("Running Snyk code and dependency scan...")
        audit_logger.log_event("snyk_scan_start", project_root=self.project_root)
        snyk_token = SECRETS_MANAGER.get_secret("SNYK_TOKEN", required=True if PRODUCTION_MODE else False)
        if not snyk_token:
            logger.error("SNYK_TOKEN environment variable is not set. Snyk scan will be skipped.")
            return []
        snyk_file = os.path.join(self.project_root, "requirements.txt")
        if not os.path.exists(snyk_file):
            logger.warning(f"Snyk scan skipped: requirements.txt not found in {self.project_root}")
            return []
        try:
            success, output = self._run_subprocess_safely(
                ['snyk', 'test', '--json', f'--file={snyk_file}'],
                description="Snyk dependency scan"
            )
            if not success:
                try:
                    snyk_output = json.loads(output)
                    vulnerabilities = snyk_output.get('vulnerabilities', [])
                    logger.info(f"Snyk found {len(vulnerabilities)} vulnerabilities (non-zero exit code but valid results).")
                    return vulnerabilities
                except json.JSONDecodeError:
                    raise SecurityAnalysisError(f"Snyk scan failed: {output}", tool="Snyk")
            snyk_output = json.loads(output)
            vulnerabilities = snyk_output.get('vulnerabilities', [])
            logger.info(f"Snyk scan complete. Found {len(vulnerabilities)} vulnerabilities.")
            audit_logger.log_event("snyk_scan_complete", project_root=self.project_root, vulnerabilities_found=len(vulnerabilities))
            return vulnerabilities
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Snyk JSON output: {e}. Raw output: {output[:500]}...")
            raise SecurityAnalysisError(f"Failed to parse Snyk output: {e}", tool="Snyk", original_exception=e)
        except SecurityAnalysisError:
            raise
        except Exception as e:
            raise SecurityAnalysisError(f"Unexpected error running Snyk: {e}", tool="Snyk", original_exception=e)

    async def perform_security_scan(self) -> Dict[str, Any]:
        from .core_audit import audit_logger
        logger.info(f"Starting comprehensive security scan for {self.project_root}...")
        audit_logger.log_event("security_scan_start", project_root=self.project_root)
        cache_key_hash = hashlib.sha256(self.project_root.encode('utf-8')).hexdigest()
        cache_key = f"security_scan:{cache_key_hash}"
        if REDIS_CLIENT:
            try:
                cached_results = await REDIS_CLIENT.get(cache_key)
                if cached_results:
                    logger.info("Returning cached security scan results.")
                    audit_logger.log_event("security_scan_cache_hit", project_root=self.project_root)
                    return json.loads(cached_results)
            except Exception as e:
                logger.warning(f"Failed to retrieve cached security scan results: {e}.")
        results = {
            "bandit_issues": [],
            "pip_audit_vulnerabilities": [],
            "snyk_vulnerabilities": [],
            "overall_status": "PASS",
            "summary": "No critical security issues detected.",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        # Run tools in parallel for performance
        bandit_issues, pip_audit_vulnerabilities, snyk_vulnerabilities = await self._run_tools_in_parallel()
        try:
            if bandit_issues:
                results["bandit_issues"] = bandit_issues
                results["overall_status"] = "FAIL"
                results["summary"] = "Security issues detected by Bandit."
                logger.warning(f"Bandit detected {len(bandit_issues)} issues.")
                critical_bandit_issues = [issue for issue in bandit_issues if issue.get('issue_severity') in ['HIGH', 'CRITICAL']]
                if critical_bandit_issues:
                    alert_operator(f"CRITICAL: Bandit detected {len(critical_bandit_issues)} HIGH/CRITICAL issues in {self.project_root}.", level="CRITICAL")
                    audit_logger.log_event("critical_bandit_issues", project_root=self.project_root, issues=_deep_scrub(critical_bandit_issues))
            if pip_audit_vulnerabilities:
                results["pip_audit_vulnerabilities"] = pip_audit_vulnerabilities
                results["overall_status"] = "FAIL"
                results["summary"] = "Vulnerable dependencies detected by pip-audit."
                logger.warning(f"pip-audit detected {len(pip_audit_vulnerabilities)} vulnerabilities.")
                critical_pip_audit_vulns = [vuln for vuln in pip_audit_vulnerabilities if vuln.get('vulnerability_id', '').startswith('CVE-')]
                if critical_pip_audit_vulns:
                    alert_operator(f"CRITICAL: pip-audit detected {len(critical_pip_audit_vulns)} CVEs in dependencies for {self.project_root}.", level="CRITICAL")
                    audit_logger.log_event("critical_pip_audit_vulns", project_root=self.project_root, vulns=_deep_scrub(critical_pip_audit_vulns))
            if snyk_vulnerabilities:
                results["snyk_vulnerabilities"] = snyk_vulnerabilities
                results["overall_status"] = "FAIL"
                results["summary"] = "Vulnerabilities detected by Snyk."
                logger.warning(f"Snyk detected {len(snyk_vulnerabilities)} vulnerabilities.")
                critical_snyk_vulns = [vuln for vuln in snyk_vulnerabilities if vuln.get('severity') in ['high', 'critical']]
                if critical_snyk_vulns:
                    alert_operator(f"CRITICAL: Snyk detected {len(critical_snyk_vulns)} HIGH/CRITICAL vulnerabilities in {self.project_root}.", level="CRITICAL")
                    audit_logger.log_event("critical_snyk_vulns", project_root=self.project_root, vulns=_deep_scrub(critical_snyk_vulns))
            if results["overall_status"] == "FAIL":
                alert_operator(f"CRITICAL: Security scan for {self.project_root} FAILED. Issues detected.", level="CRITICAL")
                audit_logger.log_event("security_scan_failed", project_root=self.project_root, status="FAIL", summary=results["summary"])
            else:
                logger.info(f"Security scan for {self.project_root} PASSED.")
                audit_logger.log_event("security_scan_passed", project_root=self.project_root, status="PASS")
            audit_logger.log_event("security_scan_results", project_root=self.project_root, results=_deep_scrub(results))
            if REDIS_CLIENT:
                try:
                    await REDIS_CLIENT.setex(cache_key, 86400, json.dumps(results)) # Cache for 24 hours
                    logger.info(f"Security scan results cached for {self.project_root}.")
                    audit_logger.log_event("security_scan_cache_set", project_root=self.project_root)
                except Exception as e:
                    logger.warning(f"Failed to cache security scan results: {e}.")
        except SecurityAnalysisError as e:
            logger.critical(f"CRITICAL: Security scan aborted due to tool failure: {e}", exc_info=True)
            alert_operator(f"CRITICAL: Security scan for {self.project_root} ABORTED due to tool failure: {e.tool}: {e}.", level="CRITICAL")
            results["overall_status"] = "ABORTED"
            results["summary"] = f"Scan aborted due to tool failure: {e.tool}"
            audit_logger.log_event("security_scan_aborted", project_root=self.project_root, error=str(e), tool=e.tool)
        except Exception as e:
            logger.critical(f"CRITICAL: Unexpected error during security scan: {e}", exc_info=True)
            alert_operator(f"CRITICAL: Unexpected error during security scan for {self.project_root}: {e}.", level="CRITICAL")
            results["overall_status"] = "ERROR"
            results["summary"] = f"Scan failed due to unexpected error: {e}"
            audit_logger.log_event("security_scan_error", project_root=self.project_root, error=str(e))
        return results

    def security_health_check(self, check_only: bool = False) -> bool:
        from .core_audit import audit_logger
        logger.info("Performing security component health check...")
        audit_logger.log_event("security_health_check_start", project_root=self.project_root, check_only=check_only)
        health_status = {
            "bandit_available": False,
            "pip_audit_available": False,
            "snyk_available": False,
            "overall_healthy": True
        }
        tool_checks = {
            'Bandit': ['bandit', '--version'],
            'pip-audit': ['pip-audit', '--version'],
            'Snyk': ['snyk', '--version']
        }
        for tool, command in tool_checks.items():
            tool_path = _tool_path(command[0])
            if not tool_path:
                health_status[f"{tool.lower().replace('-', '_')}_available"] = False
                health_status["overall_healthy"] = False
                logger.error(f"{tool} is NOT available in PATH.", extra={'component': tool, 'status': 'Unavailable'})
                continue
            try:
                result = _run_cmd(command, timeout=10)
                if result.returncode != 0:
                    health_status[f"{tool.lower().replace('-', '_')}_available"] = False
                    health_status["overall_healthy"] = False
                    logger.error(f"Command '{' '.join(command)}' failed with exit code {result.returncode}. Output: {result.stdout.strip() + result.stderr.strip()}")
                    continue
                version = result.stdout.strip()
                logger.info(f"{tool} is available (version: {version}).", extra={'component': tool, 'status': 'Available'})
                health_status[f"{tool.lower().replace('-', '_')}_available"] = True
            except subprocess.TimeoutExpired:
                health_status[f"{tool.lower().replace('-', '_')}_available"] = False
                health_status["overall_healthy"] = False
                logger.error(f"{tool} timed out during availability check.", extra={'component': tool, 'status': 'Unavailable'})
            except Exception as e:
                health_status[f"{tool.lower().replace('-', '_')}_available"] = False
                health_status["overall_healthy"] = False
                logger.error(f"{tool} check failed with unexpected error: {e}.", extra={'component': tool, 'status': 'Unavailable'})
        if not health_status["overall_healthy"]:
            logger.critical("CRITICAL: Security health check failed. Some security analysis tools are missing or unhealthy.")
            audit_logger.log_event("security_health_check_failed", project_root=self.project_root, health_details=health_status)
            alert_operator("CRITICAL: Security health check failed. Some security analysis tools are missing.", level="CRITICAL")
            if not check_only:
                raise RuntimeError("[CRITICAL][SECURITY] Security health check failed")
        logger.info(f"Security component health check complete. Overall healthy: {health_status['overall_healthy']}")
        audit_logger.log_event("security_health_check_complete", project_root=self.project_root, overall_healthy=health_status["overall_healthy"], health_details=health_status)
        return health_status["overall_healthy"]

# Public facing function (used by analyzer.py)
async def security_health_check(project_root: str = ".", check_only: bool = False) -> bool:
    analyzer = SecurityAnalyzer(project_root)
    if check_only:
        return analyzer.security_health_check(check_only=True)
    else:
        scan_results = await analyzer.perform_security_scan()
        return scan_results["overall_status"] == "PASS"

# Example usage (for testing this module independently)
if __name__ == "__main__":
    import shutil
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG)
    def alert_operator(message: str, level: str = "CRITICAL"):
        print(f"[OPS ALERT - {level}] {message}")
    def scrub_secrets(data: Any) -> Any:
        return data
    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            print(f"[AUDIT_LOG] {event_type}: {kwargs}")
    sys.modules['core_utils'] = sys.modules['__main__']
    sys.modules['core_audit'] = sys.modules['__main__']
    audit_logger = DummyAuditLogger()
    if os.path.exists("test_security_project"):
        shutil.rmtree("test_security_project")
    test_project_root = "test_security_project"
    os.makedirs(test_project_root, exist_ok=True)
    with open(os.path.join(test_project_root, "bad_code.py"), "w") as f:
        # INTENTIONAL: Creating test file with security issues to test the scanner
        # nosec: This hardcoded password is for testing security detection only
        f.write("password = 'mysecretpassword'\n")  # nosec - test data
        f.write("import os\n")
        f.write("os.system('ls') # nosec\n")
    with open(os.path.join(test_project_root, "requirements.txt"), "w") as f:
        f.write("Flask==1.0.0\n")
    class DummyRedis:
        def __init__(self):
            self.cache = {}
        async def get(self, key):
            return self.cache.get(key)
        async def setex(self, key, expiry, value):
            self.cache[key] = value
    REDIS_CLIENT = DummyRedis()
    class DummySecretsManager:
        def get_secret(self, key, required=False):
            return "dummy_token"
    sys.modules['core_secrets'] = sys.modules['__main__']
    SECRETS_MANAGER = DummySecretsManager()

    def mock_subprocess_run(command, **kwargs):
        if 'bandit' in command[0]:
            if '--json' in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"results": [{"issue_severity": "HIGH", "file": "bad_code.py"}]}), "")
            return subprocess.CompletedProcess(command, 0, "", "")
        elif 'pip-audit' in command[0]:
            if '--json' in command:
                 return subprocess.CompletedProcess(command, 1, json.dumps({"vulnerabilities": [{"vulnerability_id": "CVE-1234-5678"}]}), "")
            return subprocess.CompletedProcess(command, 0, "", "")
        elif 'snyk' in command[0]:
            if '--json' in command:
                 return subprocess.CompletedProcess(command, 1, json.dumps({"vulnerabilities": [{"id": "SNYK-PYTHON-FLASK-12345", "severity": "high"}]}), "")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise FileNotFoundError
    
    original_run = subprocess.run
    subprocess.run = mock_subprocess_run

    print("\n--- Running Security Health Check (check_only=True) ---")
    is_healthy_only = asyncio.run(security_health_check(project_root=test_project_root, check_only=True))
    print(f"Security components healthy (check_only): {is_healthy_only}")
    
    print("\n--- Running Full Security Scan ---")
    overall_scan_status = asyncio.run(security_health_check(project_root=test_project_root, check_only=False))
    print(f"Overall security scan status: {'PASS' if overall_scan_status else 'FAIL'}")
    
    print("\n--- Testing Missing Bandit (expecting abort in prod) ---")
    original_tool_path = shutil.which
    def mock_which_missing(cmd):
        if cmd == 'bandit':
            return None
        return original_tool_path(cmd)
    shutil.which = mock_which_missing
    
    try:
        original_production_mode = PRODUCTION_MODE
        os.environ["PRODUCTION_MODE"] = "true"
        SecurityAnalyzer(project_root=test_project_root)
    except RuntimeError as e:
        print(f"Caught expected RuntimeError for missing Bandit tool: {e}")
    except Exception as e:
        print(f"Caught unexpected exception: {e}")
    finally:
        os.environ["PRODUCTION_MODE"] = str(original_production_mode).lower()
        shutil.which = original_tool_path
    
    print("\n--- Cleaning up test project ---")
    if os.path.exists(test_project_root):
        shutil.rmtree(test_project_root)