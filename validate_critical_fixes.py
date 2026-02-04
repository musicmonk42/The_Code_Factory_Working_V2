#!/usr/bin/env python3
"""
Validation script for critical production fixes.

This script validates that the following issues have been resolved:
1. Shutdown handler NameError fix
2. Kafka configuration and circuit breaker
3. CORS configuration
4. Job finalization flow
5. LLM model registration
6. Test fixes
"""

import ast
import os
import sys
from pathlib import Path

# Color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def check_mark(passed: bool) -> str:
    """Return a colored check mark or X."""
    return f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"


def validate_shutdown_handler_fix():
    """Validate that shutdown handler has proper error handling."""
    print("\n1. Validating shutdown handler NameError fix...")
    
    file_path = Path("generator/audit_log/audit_crypto/audit_crypto_factory.py")
    if not file_path.exists():
        print(f"  {check_mark(False)} File not found: {file_path}")
        return False
    
    content = file_path.read_text()
    
    # Check for existence check before using crypto_provider_factory
    has_existence_check = "'crypto_provider_factory' not in globals()" in content
    has_nameerror_handling = "except NameError" in content
    
    print(f"  {check_mark(has_existence_check)} Existence check for crypto_provider_factory")
    print(f"  {check_mark(has_nameerror_handling)} NameError exception handling")
    
    return has_existence_check and has_nameerror_handling


def validate_kafka_configuration():
    """Validate Kafka configuration in config and env files."""
    print("\n2. Validating Kafka configuration...")
    
    # Check server/config.py
    config_path = Path("server/config.py")
    if not config_path.exists():
        print(f"  {check_mark(False)} Config file not found")
        return False
    
    config_content = config_path.read_text()
    
    has_kafka_enabled = "kafka_enabled" in config_content
    has_kafka_required = "kafka_required" in config_content
    has_kafka_retries = "kafka_max_retries" in config_content
    has_kafka_backoff = "kafka_retry_backoff_ms" in config_content
    
    print(f"  {check_mark(has_kafka_enabled)} kafka_enabled configuration")
    print(f"  {check_mark(has_kafka_required)} kafka_required configuration")
    print(f"  {check_mark(has_kafka_retries)} kafka_max_retries configuration")
    print(f"  {check_mark(has_kafka_backoff)} kafka_retry_backoff_ms configuration")
    
    # Check .env.example
    env_path = Path(".env.example")
    if env_path.exists():
        env_content = env_path.read_text()
        has_kafka_enabled_env = "KAFKA_ENABLED" in env_content
        print(f"  {check_mark(has_kafka_enabled_env)} KAFKA_ENABLED in .env.example")
    
    return all([has_kafka_enabled, has_kafka_required, has_kafka_retries, has_kafka_backoff])


def validate_cors_configuration():
    """Validate CORS configuration."""
    print("\n3. Validating CORS configuration...")
    
    results = []
    
    # Check server/main.py
    server_main = Path("server/main.py")
    if server_main.exists():
        content = server_main.read_text()
        has_cors_middleware = "CORSMiddleware" in content
        has_allowed_origins = "ALLOWED_ORIGINS" in content or "allowed_origins" in content
        print(f"  {check_mark(has_cors_middleware)} CORSMiddleware in server/main.py")
        print(f"  {check_mark(has_allowed_origins)} ALLOWED_ORIGINS configuration")
        results.append(has_cors_middleware and has_allowed_origins)
    
    # Check generator/main/api.py
    gen_api = Path("generator/main/api.py")
    if gen_api.exists():
        content = gen_api.read_text()
        has_cors_middleware = "CORSMiddleware" in content
        has_allowed_origins = "ALLOWED_ORIGINS" in content or "allowed_origins" in content
        print(f"  {check_mark(has_cors_middleware)} CORSMiddleware in generator/main/api.py")
        print(f"  {check_mark(has_allowed_origins)} ALLOWED_ORIGINS configuration")
        results.append(has_cors_middleware and has_allowed_origins)
    
    # Check .env.example
    env_path = Path(".env.example")
    if env_path.exists():
        content = env_path.read_text()
        has_cors_docs = "ALLOWED_ORIGINS" in content or "CORS_ORIGINS" in content
        print(f"  {check_mark(has_cors_docs)} CORS documented in .env.example")
        results.append(has_cors_docs)
    
    return all(results)


def validate_job_finalization():
    """Validate job finalization flow."""
    print("\n4. Validating job finalization flow...")
    
    service_path = Path("server/services/omnicore_service.py")
    if not service_path.exists():
        print(f"  {check_mark(False)} Service file not found")
        return False
    
    content = service_path.read_text()
    
    has_finalize_successful = "_finalize_successful_job" in content
    has_finalize_failed = "_finalize_failed_job" in content
    has_status_update = "job.status = JobStatus.COMPLETED" in content
    has_dispatch_sfe = "_dispatch_to_sfe" in content
    
    # Check that finalization is called before return in pipeline
    has_inline_finalization = "await self._finalize_successful_job" in content
    
    print(f"  {check_mark(has_finalize_successful)} _finalize_successful_job method exists")
    print(f"  {check_mark(has_finalize_failed)} _finalize_failed_job method exists")
    print(f"  {check_mark(has_status_update)} Job status update to COMPLETED")
    print(f"  {check_mark(has_dispatch_sfe)} Dispatch to SFE functionality")
    print(f"  {check_mark(has_inline_finalization)} Finalization called inline (not in shutdown)")
    
    return all([has_finalize_successful, has_finalize_failed, has_status_update, 
                has_dispatch_sfe, has_inline_finalization])


def validate_llm_model_registration():
    """Validate LLM model registration."""
    print("\n5. Validating LLM model registration...")
    
    ai_provider_path = Path("generator/runner/providers/ai_provider.py")
    if not ai_provider_path.exists():
        print(f"  {check_mark(False)} AI provider file not found")
        return False
    
    content = ai_provider_path.read_text()
    
    has_gpt4o_mini = "gpt-4o-mini" in content
    print(f"  {check_mark(has_gpt4o_mini)} gpt-4o-mini model registered")
    
    return has_gpt4o_mini


def validate_test_fixes():
    """Validate test fixes."""
    print("\n6. Validating test fixes...")
    
    test_path = Path("self_fixing_engineer/tests/test_arbiter_arbiter_growth_idempotency.py")
    if not test_path.exists():
        print(f"  {check_mark(False)} Test file not found")
        return False
    
    content = test_path.read_text()
    
    # Check for metric baseline capture
    has_baseline_capture = "false_before" in content and "true_before" in content
    has_try_except = "try:" in content and "except (AttributeError, KeyError):" in content
    has_delta_assertions = "- false_before" in content and "- true_before" in content
    
    print(f"  {check_mark(has_baseline_capture)} Metric baseline capture")
    print(f"  {check_mark(has_try_except)} Try/except for metric access")
    print(f"  {check_mark(has_delta_assertions)} Delta-based assertions")
    
    return all([has_baseline_capture, has_try_except, has_delta_assertions])


def validate_presidio_filtering():
    """Validate Presidio warning filtering."""
    print("\n7. Validating Presidio warning filtering...")
    
    security_utils_path = Path("generator/runner/runner_security_utils.py")
    if not security_utils_path.exists():
        print(f"  {check_mark(False)} Security utils file not found")
        return False
    
    content = security_utils_path.read_text()
    
    has_log_filter = "presidio_log_filter" in content
    has_language_filter = "not added to registry because language is not supported" in content
    
    print(f"  {check_mark(has_log_filter)} Presidio log filter defined")
    print(f"  {check_mark(has_language_filter)} Language warning filter")
    
    return has_log_filter and has_language_filter


def main():
    """Run all validation checks."""
    print("=" * 70)
    print("CRITICAL PRODUCTION FIXES - VALIDATION REPORT")
    print("=" * 70)
    
    results = {
        "Shutdown Handler Fix": validate_shutdown_handler_fix(),
        "Kafka Configuration": validate_kafka_configuration(),
        "CORS Configuration": validate_cors_configuration(),
        "Job Finalization": validate_job_finalization(),
        "LLM Model Registration": validate_llm_model_registration(),
        "Test Fixes": validate_test_fixes(),
        "Presidio Filtering": validate_presidio_filtering(),
    }
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for check_name, passed in results.items():
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {check_name}: {status}")
    
    total_checks = len(results)
    passed_checks = sum(results.values())
    
    print(f"\nTotal: {passed_checks}/{total_checks} checks passed")
    
    if passed_checks == total_checks:
        print(f"\n{GREEN}✓ All critical fixes validated successfully!{RESET}")
        return 0
    else:
        print(f"\n{YELLOW}⚠ Some checks failed. Review the output above.{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
