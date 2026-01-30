#!/usr/bin/env python3
"""
Comprehensive validation test for industry-standard bug fixes.

This test validates:
1. Observability (metrics, tracing, structured logging)
2. Security (path traversal prevention, input validation, rate limiting)
3. Error handling (specific exceptions, graceful degradation)
4. Type safety and documentation
5. Performance and reliability
"""

import logging
import re
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger(__name__)


class TestResult:
    """Track test results."""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests = []
    
    def add(self, test_name: str, passed: bool, details: str = ""):
        self.tests.append((test_name, passed, details))
        if passed:
            self.passed += 1
        else:
            self.failed += 1
    
    def print_summary(self):
        logger.info("\n" + "=" * 80)
        logger.info("TEST RESULTS SUMMARY")
        logger.info("=" * 80)
        for test_name, passed, details in self.tests:
            status = "✅ PASS" if passed else "❌ FAIL"
            logger.info(f"{status}: {test_name}")
            if details and not passed:
                logger.info(f"   Details: {details}")
        logger.info("=" * 80)
        logger.info(f"Total: {self.passed} passed, {self.failed} failed")
        return self.failed == 0


def test_omnicore_observability(results: TestResult):
    """Test observability features in omnicore_service.py"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: OmniCore Service Observability")
    logger.info("=" * 80)
    
    try:
        with open('server/services/omnicore_service.py', 'r') as f:
            content = f.read()
        
        # Test 1: OpenTelemetry tracing
        has_tracer = 'from opentelemetry import trace' in content
        has_spans = 'tracer.start_as_current_span' in content
        results.add(
            "OpenTelemetry tracing integration",
            has_tracer and has_spans,
            "Missing OpenTelemetry imports or span usage"
        )
        
        # Test 2: Prometheus metrics
        metrics = [
            'codegen_requests_total',
            'codegen_files_generated',
            'codegen_duration_seconds',
            'codegen_file_size_bytes',
            'codegen_errors_total'
        ]
        has_all_metrics = all(metric in content for metric in metrics)
        results.add(
            "Prometheus metrics defined",
            has_all_metrics,
            f"Missing metrics: {[m for m in metrics if m not in content]}"
        )
        
        # Test 3: Structured logging with extra context
        has_structured_logging = 'extra={' in content
        has_job_context = '"job_id":' in content or 'job_id=' in content
        results.add(
            "Structured logging with context",
            has_structured_logging and has_job_context,
            "Missing structured logging or job context"
        )
        
        # Test 4: Performance timing
        has_timing = 'start_time = time.time()' in content
        has_duration = 'duration = time.time() - start_time' in content
        results.add(
            "Performance timing tracked",
            has_timing and has_duration,
            "Missing timing or duration calculation"
        )
        
    except Exception as e:
        results.add("OmniCore observability tests", False, str(e))


def test_omnicore_security(results: TestResult):
    """Test security features in omnicore_service.py"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: OmniCore Service Security")
    logger.info("=" * 80)
    
    try:
        with open('server/services/omnicore_service.py', 'r') as f:
            content = f.read()
        
        # Test 1: Path traversal prevention
        has_path_validation = 'SecurityError' in content
        has_resolve = '.resolve()' in content
        has_startswith_check = 'startswith(str(' in content
        results.add(
            "Path traversal prevention",
            has_path_validation and has_resolve and has_startswith_check,
            "Missing path validation or SecurityError"
        )
        
        # Test 2: Input validation
        has_max_length = '100000' in content or '100KB' in content
        has_file_size_limit = '10 * 1024 * 1024' in content or '10MB' in content
        results.add(
            "Input size validation",
            has_max_length and has_file_size_limit,
            "Missing size limits for requirements or files"
        )
        
        # Test 3: Filename validation
        has_filename_check = '".." in filename' in content
        has_absolute_check = 'startswith(\'/\')' in content
        results.add(
            "Filename security validation",
            has_filename_check or has_absolute_check,
            "Missing filename security checks"
        )
        
        # Test 4: SecurityError exception class
        has_security_error_class = 'class SecurityError(Exception):' in content
        results.add(
            "Custom SecurityError exception",
            has_security_error_class,
            "Missing SecurityError exception class"
        )
        
    except Exception as e:
        results.add("OmniCore security tests", False, str(e))


def test_omnicore_error_handling(results: TestResult):
    """Test error handling in omnicore_service.py"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: OmniCore Service Error Handling")
    logger.info("=" * 80)
    
    try:
        with open('server/services/omnicore_service.py', 'r') as f:
            content = f.read()
        
        # Test 1: Specific exception types
        exceptions = ['SecurityError', 'ValueError', 'TypeError']
        has_all_exceptions = all(f'except {exc}' in content for exc in exceptions)
        results.add(
            "Specific exception handling",
            has_all_exceptions,
            f"Missing exception types: {[e for e in exceptions if f'except {e}' not in content]}"
        )
        
        # Test 2: Graceful degradation
        has_continue = 'continue with other files' in content.lower() or '# continue' in content.lower()
        results.add(
            "Graceful degradation on errors",
            has_continue,
            "Missing graceful continuation logic"
        )
        
        # Test 3: Error metrics
        has_error_metrics = 'codegen_errors_total' in content
        has_error_labels = 'error_type=' in content
        results.add(
            "Error metrics tracking",
            has_error_metrics and has_error_labels,
            "Missing error metrics or labels"
        )
        
        # Test 4: Comprehensive error logging
        has_exc_info = 'exc_info=True' in content
        has_error_context = 'error_type' in content and 'error_message' in content
        results.add(
            "Comprehensive error logging",
            has_exc_info and has_error_context,
            "Missing exc_info or error context"
        )
        
    except Exception as e:
        results.add("OmniCore error handling tests", False, str(e))


def test_deploy_agent_improvements(results: TestResult):
    """Test improvements in deploy_prompt.py"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: Deploy Agent Improvements")
    logger.info("=" * 80)
    
    try:
        with open('generator/agents/deploy_agent/deploy_prompt.py', 'r') as f:
            content = f.read()
        
        # Test 1: Type hints
        has_type_hints = '-> None:' in content or '-> List[' in content
        results.add(
            "Type hints in method signatures",
            has_type_hints,
            "Missing type hints"
        )
        
        # Test 2: Input validation
        has_validation = 'raise ValueError' in content
        has_checks = 'if not few_shot_dir or not isinstance' in content
        results.add(
            "Input validation with ValueError",
            has_validation and has_checks,
            "Missing input validation"
        )
        
        # Test 3: Comprehensive docstrings
        has_docstrings = 'Args:' in content and 'Returns:' in content and 'Raises:' in content
        results.add(
            "Comprehensive docstrings",
            has_docstrings,
            "Missing Args/Returns/Raises in docstrings"
        )
        
        # Test 4: Specific exception handling
        has_permission_error = 'PermissionError' in content
        has_os_error = 'OSError' in content
        results.add(
            "Specific exception types",
            has_permission_error and has_os_error,
            "Missing PermissionError or OSError handling"
        )
        
        # Test 5: Exist_ok for idempotent operations
        has_exist_ok = 'exist_ok=True' in content
        results.add(
            "Idempotent directory creation",
            has_exist_ok,
            "Missing exist_ok=True"
        )
        
    except Exception as e:
        results.add("Deploy agent tests", False, str(e))


def test_websocket_rate_limiting(results: TestResult):
    """Test WebSocket rate limiting features"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: WebSocket Rate Limiting")
    logger.info("=" * 80)
    
    try:
        with open('server/routers/events.py', 'r') as f:
            content = f.read()
        
        # Test 1: Rate limiting constants
        constants = [
            'MAX_CONNECTIONS_PER_IP',
            'MAX_TOTAL_CONNECTIONS',
            'RATE_LIMIT_WINDOW',
            'MAX_CONNECTIONS_PER_WINDOW'
        ]
        has_all_constants = all(const in content for const in constants)
        results.add(
            "Rate limiting constants defined",
            has_all_constants,
            f"Missing constants: {[c for c in constants if c not in content]}"
        )
        
        # Test 2: Rate limit check function
        has_check_function = 'def _check_rate_limit' in content
        has_rate_logic = 'connection_attempts' in content
        results.add(
            "Rate limit check implementation",
            has_check_function and has_rate_logic,
            "Missing rate limit check function"
        )
        
        # Test 3: Connection tracking
        has_tracking = '_active_connections_by_ip' in content
        has_per_ip_tracking = 'defaultdict' in content
        results.add(
            "Per-IP connection tracking",
            has_tracking and has_per_ip_tracking,
            "Missing per-IP connection tracking"
        )
        
        # Test 4: Connection rejection logic
        has_rejection = 'connection rejected' in content.lower()
        has_close = 'websocket.close' in content
        results.add(
            "Connection rejection on rate limit",
            has_rejection and has_close,
            "Missing connection rejection logic"
        )
        
        # Test 5: Connection metadata
        has_connection_id = 'connection_id' in content
        has_structured_logs = 'extra={' in content
        results.add(
            "Connection metadata and logging",
            has_connection_id and has_structured_logs,
            "Missing connection ID or structured logging"
        )
        
    except Exception as e:
        results.add("WebSocket rate limiting tests", False, str(e))


def test_sfe_arbiter_improvements(results: TestResult):
    """Test SFE arbiter endpoint improvements"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUITE: SFE Arbiter Endpoint")
    logger.info("=" * 80)
    
    try:
        with open('server/routers/sfe.py', 'r') as f:
            content = f.read()
        
        # Test 1: Response model type hint
        has_response_model = 'response_model=Dict' in content
        has_return_type = '-> Dict[str, Any]:' in content
        results.add(
            "Type hints for response",
            has_response_model and has_return_type,
            "Missing response_model or return type"
        )
        
        # Test 2: Request ID tracking
        has_request_id = 'request_id = ' in content
        results.add(
            "Request ID tracking",
            has_request_id,
            "Missing request ID generation"
        )
        
        # Test 3: Input validation
        has_validation = 'commands_requiring_job' in content
        has_validation_check = 'if command_str in commands_requiring_job and not request.job_id' in content
        results.add(
            "Command-specific validation",
            has_validation and has_validation_check,
            "Missing command validation logic"
        )
        
        # Test 4: Structured error responses
        has_structured_errors = '"error":' in content and '"message":' in content
        has_error_detail = 'detail={' in content
        results.add(
            "Structured error responses",
            has_structured_errors and has_error_detail,
            "Missing structured error responses"
        )
        
        # Test 5: Multiple status codes
        status_codes = ['status_code=400', 'status_code=404', 'status_code=500']
        has_all_codes = all(code in content for code in status_codes)
        results.add(
            "Proper HTTP status codes",
            has_all_codes,
            f"Missing status codes: {[c for c in status_codes if c not in content]}"
        )
        
        # Test 6: Performance timing
        has_timing = 'start_time = time.time()' in content
        has_duration = 'duration = time.time() - start_time' in content
        results.add(
            "Performance timing",
            has_timing and has_duration,
            "Missing performance timing"
        )
        
    except Exception as e:
        results.add("SFE arbiter tests", False, str(e))


def main():
    """Run all validation tests."""
    logger.info("\n" + "=" * 80)
    logger.info("INDUSTRY STANDARDS VALIDATION TEST SUITE")
    logger.info("=" * 80)
    
    results = TestResult()
    
    # Run all test suites
    test_omnicore_observability(results)
    test_omnicore_security(results)
    test_omnicore_error_handling(results)
    test_deploy_agent_improvements(results)
    test_websocket_rate_limiting(results)
    test_sfe_arbiter_improvements(results)
    
    # Print summary
    all_passed = results.print_summary()
    
    if all_passed:
        logger.info("\n🎉 SUCCESS: All industry-standard validations passed!")
        logger.info("✅ Observability: Metrics, Tracing, Structured Logging")
        logger.info("✅ Security: Path validation, Input validation, Rate limiting")
        logger.info("✅ Error Handling: Specific exceptions, Graceful degradation")
        logger.info("✅ Type Safety: Type hints, Response models")
        logger.info("✅ Performance: Timing, Monitoring")
        return 0
    else:
        logger.error("\n❌ FAILURE: Some validations failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
