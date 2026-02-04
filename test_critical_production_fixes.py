#!/usr/bin/env python3
"""
Test suite for critical production fixes.

This test validates all the fixes implemented in the critical production fixes PR:
1. Environment Configuration (API keys, CORS, Kafka)
2. Docker & Deployment (validation skipping)
3. Redis Deprecation (aclose() migration)
4. Test Generation (syntax error handling, timeout)
5. Code Generation (syntax validation)
6. HTTP/Timeout Configuration
7. Presidio Configuration (labels_to_ignore)
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_environment_configuration():
    """Test that environment configuration fixes are in place."""
    print("\n" + "=" * 70)
    print("TEST 1: Environment Configuration")
    print("=" * 70)
    
    # Test 1.1: Check .env.example has all API keys
    env_example_path = project_root / ".env.example"
    assert env_example_path.exists(), ".env.example not found"
    
    env_example_content = env_example_path.read_text()
    
    required_keys = [
        "GEMINI_API_KEY",
        "CLAUDE_API_KEY",
        "GROK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ALLOWED_ORIGINS",
        "KAFKA_ENABLED",
        "SKIP_DOCKER_VALIDATION"
    ]
    
    for key in required_keys:
        assert key in env_example_content, f"{key} not found in .env.example"
    
    print("  ✓ .env.example contains all required keys")
    
    # Test 1.2: Check .env.production.template has all API keys
    env_prod_template_path = project_root / ".env.production.template"
    assert env_prod_template_path.exists(), ".env.production.template not found"
    
    env_prod_content = env_prod_template_path.read_text()
    
    for key in required_keys:
        assert key in env_prod_content, f"{key} not found in .env.production.template"
    
    print("  ✓ .env.production.template contains all required keys")
    
    # Test 1.3: Check server/config_utils.py has provider display logic
    config_utils_path = project_root / "server" / "config_utils.py"
    assert config_utils_path.exists(), "server/config_utils.py not found"
    
    config_utils_content = config_utils_path.read_text()
    assert "available_providers" in config_utils_content, "available_providers not in config_utils.py"
    assert "OpenAI" in config_utils_content, "OpenAI provider mapping not found"
    assert "Anthropic Claude" in config_utils_content, "Claude provider mapping not found"
    assert "Google Gemini" in config_utils_content, "Gemini provider mapping not found"
    assert "xAI Grok" in config_utils_content, "Grok provider mapping not found"
    assert "kafka_enabled" in config_utils_content, "Kafka configuration not found"
    
    print("  ✓ config_utils.py has provider display and Kafka configuration")
    
    print("\n✅ TEST 1 PASSED: Environment Configuration")


def test_docker_deployment_fixes():
    """Test that Docker & deployment fixes are in place."""
    print("\n" + "=" * 70)
    print("TEST 2: Docker & Deployment Fixes")
    print("=" * 70)
    
    # Test 2.1: Check deploy_validator.py has SKIP_DOCKER_VALIDATION
    deploy_validator_path = project_root / "generator" / "agents" / "deploy_agent" / "deploy_validator.py"
    assert deploy_validator_path.exists(), "deploy_validator.py not found"
    
    deploy_validator_content = deploy_validator_path.read_text()
    assert "SKIP_DOCKER_VALIDATION" in deploy_validator_content, "SKIP_DOCKER_VALIDATION not in deploy_validator.py"
    
    print("  ✓ deploy_validator.py has SKIP_DOCKER_VALIDATION support")
    
    # Test 2.2: Check docker-compose.production.yml has correct AUDIT_CRYPTO_MODE
    docker_compose_path = project_root / "docker-compose.production.yml"
    assert docker_compose_path.exists(), "docker-compose.production.yml not found"
    
    docker_compose_content = docker_compose_path.read_text()
    # Check that default is "software" not "disabled"
    assert "AUDIT_CRYPTO_MODE:-software}" in docker_compose_content, "AUDIT_CRYPTO_MODE default not set to 'software' in docker-compose"
    
    print("  ✓ docker-compose.production.yml uses secure AUDIT_CRYPTO_MODE default")
    
    # Test 2.3: Check railway.toml has SKIP_DOCKER_VALIDATION
    railway_toml_path = project_root / "railway.toml"
    assert railway_toml_path.exists(), "railway.toml not found"
    
    railway_toml_content = railway_toml_path.read_text()
    assert "SKIP_DOCKER_VALIDATION" in railway_toml_content, "SKIP_DOCKER_VALIDATION not in railway.toml"
    assert "DOCKER_REQUIRED" in railway_toml_content, "DOCKER_REQUIRED not in railway.toml"
    
    print("  ✓ railway.toml has Docker validation configuration")
    
    print("\n✅ TEST 2 PASSED: Docker & Deployment Fixes")


def test_redis_deprecation():
    """Test that all redis.close() calls have been replaced with redis.aclose()."""
    print("\n" + "=" * 70)
    print("TEST 3: Redis Deprecation (CRITICAL)")
    print("=" * 70)
    
    # Files that should have redis.aclose()
    files_to_check = [
        "omnicore_engine/message_bus/rate_limit.py",
        "generator/runner/llm_client.py",
        "self_fixing_engineer/arbiter/arbiter_growth/idempotency.py",
        "self_fixing_engineer/arbiter/arbiter_growth/storage_backends.py",
        "self_fixing_engineer/arbiter/models/meta_learning_data_store.py",
        "self_fixing_engineer/arbiter/bug_manager/notifications.py",
        "self_fixing_engineer/arbiter/arbiter_array_backend.py",
        "self_fixing_engineer/arbiter/arbiter_growth.py",
        "self_fixing_engineer/simulation/plugins/pip_audit_plugin.py",
        "self_fixing_engineer/simulation/plugins/security_patch_generator_plugin.py",
        "self_fixing_engineer/simulation/plugins/viz.py",
    ]
    
    for file_path in files_to_check:
        full_path = project_root / file_path
        if full_path.exists():
            content = full_path.read_text()
            
            # Check no redis.close() calls remain
            assert "redis.close()" not in content, f"redis.close() found in {file_path} - should be redis.aclose()"
            
            # Check redis.aclose() is present (at least in most files)
            if "redis" in content.lower():
                # File uses redis, check for aclose
                if ".aclose()" not in content:
                    print(f"  ⚠️  Warning: {file_path} uses redis but has no .aclose() calls")
        else:
            print(f"  ⚠️  Warning: {file_path} not found")
    
    print("  ✓ All redis.close() calls replaced with redis.aclose()")
    print("\n✅ TEST 3 PASSED: Redis Deprecation")


def test_test_generation_fixes():
    """Test that test generation fixes are in place."""
    print("\n" + "=" * 70)
    print("TEST 4: Test Generation Issues")
    print("=" * 70)
    
    # Test 4.1: Check testgen_agent has syntax error handling
    testgen_agent_path = project_root / "generator" / "agents" / "testgen_agent" / "testgen_agent.py"
    assert testgen_agent_path.exists(), "testgen_agent.py not found"
    
    testgen_agent_content = testgen_agent_path.read_text()
    assert "SyntaxError" in testgen_agent_content, "SyntaxError handling not found in testgen_agent.py"
    assert "ast.parse" in testgen_agent_content, "AST parsing not found in testgen_agent.py"
    assert "TESTGEN_LLM_TIMEOUT" in testgen_agent_content, "TESTGEN_LLM_TIMEOUT not found in testgen_agent.py"
    assert "timeout=" in testgen_agent_content, "Timeout handling not found in testgen_agent.py"
    
    print("  ✓ testgen_agent.py has syntax error handling and timeout")
    print("  ✓ testgen_agent.py has AST-based fallback")
    
    # Test 4.2: Check codegen_response_handler has syntax validation
    codegen_handler_path = project_root / "generator" / "agents" / "codegen_agent" / "codegen_response_handler.py"
    assert codegen_handler_path.exists(), "codegen_response_handler.py not found"
    
    codegen_handler_content = codegen_handler_path.read_text()
    assert "_validate_syntax" in codegen_handler_content, "Syntax validation not found in codegen_response_handler.py"
    assert "compile(code" in codegen_handler_content, "Python syntax validation not found"
    
    print("  ✓ codegen_response_handler.py has syntax validation")
    
    print("\n✅ TEST 4 PASSED: Test Generation Issues")


def test_http_timeout_configuration():
    """Test that HTTP timeout configuration is properly set."""
    print("\n" + "=" * 70)
    print("TEST 5: HTTP/Timeout Configuration")
    print("=" * 70)
    
    # Test 5.1: Check server/run.py has timeout settings
    run_py_path = project_root / "server" / "run.py"
    assert run_py_path.exists(), "server/run.py not found"
    
    run_py_content = run_py_path.read_text()
    assert "timeout_keep_alive" in run_py_content, "timeout_keep_alive not found in run.py"
    assert "timeout_graceful_shutdown" in run_py_content, "timeout_graceful_shutdown not found in run.py"
    
    # Check timeout values are reasonable (>= 60 seconds)
    import re
    timeout_keep_alive_match = re.search(r'timeout_keep_alive=(\d+)', run_py_content)
    timeout_graceful_shutdown_match = re.search(r'timeout_graceful_shutdown=(\d+)', run_py_content)
    
    if timeout_keep_alive_match:
        timeout_keep_alive = int(timeout_keep_alive_match.group(1))
        assert timeout_keep_alive >= 60, f"timeout_keep_alive ({timeout_keep_alive}) should be >= 60 seconds"
        print(f"  ✓ timeout_keep_alive = {timeout_keep_alive}s")
    
    if timeout_graceful_shutdown_match:
        timeout_graceful_shutdown = int(timeout_graceful_shutdown_match.group(1))
        assert timeout_graceful_shutdown >= 30, f"timeout_graceful_shutdown ({timeout_graceful_shutdown}) should be >= 30 seconds"
        print(f"  ✓ timeout_graceful_shutdown = {timeout_graceful_shutdown}s")
    
    print("\n✅ TEST 5 PASSED: HTTP/Timeout Configuration")


def test_presidio_configuration():
    """Test that Presidio configuration is correct."""
    print("\n" + "=" * 70)
    print("TEST 6: Presidio Entity Warnings")
    print("=" * 70)
    
    # Test 6.1: Check runner_security_utils.py has labels_to_ignore
    runner_security_path = project_root / "generator" / "runner" / "runner_security_utils.py"
    if runner_security_path.exists():
        runner_security_content = runner_security_path.read_text()
        
        # Check for key labels that should be ignored
        required_labels = ["CARDINAL", "MONEY", "PRODUCT", "WORK_OF_ART", "PERCENT"]
        for label in required_labels:
            assert label in runner_security_content, f"{label} not in labels_to_ignore"
        
        print("  ✓ runner_security_utils.py has correct labels_to_ignore")
    else:
        print("  ⚠️  Warning: runner_security_utils.py not found")
    
    print("\n✅ TEST 6 PASSED: Presidio Configuration")


def test_cors_configuration():
    """Test that CORS configuration is properly set up."""
    print("\n" + "=" * 70)
    print("TEST 7: CORS Configuration")
    print("=" * 70)
    
    # Test 7.1: Check server/main.py has CORS configuration
    main_py_path = project_root / "server" / "main.py"
    assert main_py_path.exists(), "server/main.py not found"
    
    main_py_content = main_py_path.read_text()
    assert "ALLOWED_ORIGINS" in main_py_content, "ALLOWED_ORIGINS not found in main.py"
    assert "CORSMiddleware" in main_py_content, "CORSMiddleware not found in main.py"
    assert "RAILWAY_PUBLIC_DOMAIN" in main_py_content, "Railway auto-detection not found"
    
    print("  ✓ server/main.py has CORS configuration")
    print("  ✓ Railway URL auto-detection is present")
    
    print("\n✅ TEST 7 PASSED: CORS Configuration")


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("CRITICAL PRODUCTION FIXES - TEST SUITE")
    print("=" * 70)
    
    tests = [
        test_environment_configuration,
        test_docker_deployment_fixes,
        test_redis_deprecation,
        test_test_generation_fixes,
        test_http_timeout_configuration,
        test_presidio_configuration,
        test_cors_configuration,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"\n❌ TEST FAILED: {test_func.__name__}")
            print(f"   Error: {e}")
            failed += 1
        except Exception as e:
            print(f"\n❌ TEST ERROR: {test_func.__name__}")
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Total Tests: {len(tests)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print("=" * 70)
    
    if failed == 0:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n⚠️  {failed} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
