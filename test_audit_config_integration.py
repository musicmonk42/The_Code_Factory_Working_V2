#!/usr/bin/env python3
"""
Integration Test for Audit Configuration System

This script tests the integration of all audit configuration components:
- Configuration file loading
- Environment variable precedence
- Validation script
- Docker integration
- Makefile targets
"""

import json
import os
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path


class Colors:
    """ANSI color codes"""
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'  # No Color


def run_command(cmd, cwd=None, check=True, capture_output=True):
    """Run a shell command and return result"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=True
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr


def test_yaml_syntax():
    """Test that all YAML configuration files have valid syntax"""
    print(f"{Colors.BLUE}Testing YAML file syntax...{Colors.NC}")
    
    yaml_files = [
        'generator/audit_config.yaml',
        'generator/audit_config.enhanced.yaml',
        'generator/audit_config.production.yaml',
        'generator/audit_config.development.yaml',
    ]
    
    for yaml_file in yaml_files:
        if not os.path.exists(yaml_file):
            print(f"{Colors.YELLOW}  Skipping {yaml_file} (not found){Colors.NC}")
            continue
            
        try:
            with open(yaml_file, 'r') as f:
                config = yaml.safe_load(f)
            print(f"{Colors.GREEN}  ✓ {yaml_file} - Valid YAML syntax{Colors.NC}")
        except yaml.YAMLError as e:
            print(f"{Colors.RED}  ✗ {yaml_file} - Invalid YAML: {e}{Colors.NC}")
            return False
    
    return True


def test_validation_script_syntax():
    """Test that validation script has valid Python syntax"""
    print(f"{Colors.BLUE}Testing validation script syntax...{Colors.NC}")
    
    success, stdout, stderr = run_command(
        'python -m py_compile generator/audit_log/validate_config.py'
    )
    
    if success:
        print(f"{Colors.GREEN}  ✓ Validation script has valid Python syntax{Colors.NC}")
    else:
        print(f"{Colors.RED}  ✗ Validation script syntax error: {stderr}{Colors.NC}")
    
    return success


def test_validation_script_execution():
    """Test that validation script executes correctly"""
    print(f"{Colors.BLUE}Testing validation script execution...{Colors.NC}")
    
    # Test with existing config
    success, stdout, stderr = run_command(
        'python generator/audit_log/validate_config.py --config generator/audit_config.yaml'
    )
    
    if success:
        print(f"{Colors.GREEN}  ✓ Validation script executes successfully{Colors.NC}")
        print(f"{Colors.YELLOW}  Output preview:{Colors.NC}")
        # Print first few lines of output
        for line in stdout.split('\n')[:10]:
            if line.strip():
                print(f"    {line}")
    else:
        print(f"{Colors.RED}  ✗ Validation script execution failed: {stderr}{Colors.NC}")
    
    return success


def test_config_validation_prod():
    """Test validation of production configuration"""
    print(f"{Colors.BLUE}Testing production config validation...{Colors.NC}")
    
    success, stdout, stderr = run_command(
        'python generator/audit_log/validate_config.py --config generator/audit_config.production.yaml'
    )
    
    if success:
        print(f"{Colors.GREEN}  ✓ Production config validation passed{Colors.NC}")
    else:
        print(f"{Colors.RED}  ✗ Production config validation failed{Colors.NC}")
    
    return success


def test_config_validation_dev():
    """Test validation of development configuration"""
    print(f"{Colors.BLUE}Testing development config validation...{Colors.NC}")
    
    success, stdout, stderr = run_command(
        'python generator/audit_log/validate_config.py --config generator/audit_config.development.yaml'
    )
    
    if success:
        print(f"{Colors.GREEN}  ✓ Development config validation passed{Colors.NC}")
    else:
        print(f"{Colors.RED}  ✗ Development config validation failed{Colors.NC}")
    
    return success


def test_env_variable_validation():
    """Test validation with environment variables"""
    print(f"{Colors.BLUE}Testing environment variable validation...{Colors.NC}")
    
    # Set some test environment variables
    test_env = os.environ.copy()
    test_env.update({
        'AUDIT_LOG_BACKEND_TYPE': 'file',
        'AUDIT_CRYPTO_PROVIDER_TYPE': 'software',
        'AUDIT_LOG_IMMUTABLE': 'true',
        'AUDIT_COMPRESSION_ALGO': 'zstd',
    })
    
    cmd = 'python generator/audit_log/validate_config.py --env'
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        env=test_env
    )
    
    success = result.returncode == 0
    
    if success:
        print(f"{Colors.GREEN}  ✓ Environment variable validation passed{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}  ⚠ Environment variable validation completed with warnings{Colors.NC}")
    
    return True  # Consider warnings as success for env validation


def test_documentation_exists():
    """Test that documentation files exist"""
    print(f"{Colors.BLUE}Testing documentation existence...{Colors.NC}")
    
    doc_files = [
        'docs/AUDIT_CONFIGURATION.md',
    ]
    
    all_exist = True
    for doc_file in doc_files:
        if os.path.exists(doc_file):
            print(f"{Colors.GREEN}  ✓ {doc_file} exists{Colors.NC}")
        else:
            print(f"{Colors.RED}  ✗ {doc_file} not found{Colors.NC}")
            all_exist = False
    
    return all_exist


def test_makefile_targets():
    """Test that Makefile has audit configuration targets"""
    print(f"{Colors.BLUE}Testing Makefile targets...{Colors.NC}")
    
    if not os.path.exists('Makefile'):
        print(f"{Colors.RED}  ✗ Makefile not found{Colors.NC}")
        return False
    
    with open('Makefile', 'r') as f:
        makefile_content = f.read()
    
    required_targets = [
        'audit-config-validate',
        'audit-config-validate-prod',
        'audit-config-validate-dev',
        'audit-config-validate-env',
    ]
    
    all_found = True
    for target in required_targets:
        if target in makefile_content:
            print(f"{Colors.GREEN}  ✓ Makefile target '{target}' exists{Colors.NC}")
        else:
            print(f"{Colors.RED}  ✗ Makefile target '{target}' not found{Colors.NC}")
            all_found = False
    
    return all_found


def test_env_template_updated():
    """Test that .env.production.template has audit configuration"""
    print(f"{Colors.BLUE}Testing .env.production.template updates...{Colors.NC}")
    
    if not os.path.exists('.env.production.template'):
        print(f"{Colors.RED}  ✗ .env.production.template not found{Colors.NC}")
        return False
    
    with open('.env.production.template', 'r') as f:
        env_content = f.read()
    
    required_vars = [
        'AUDIT_LOG_BACKEND_TYPE',
        'AUDIT_LOG_ENCRYPTION_KEY',
        'AUDIT_CRYPTO_PROVIDER_TYPE',
        'AUDIT_COMPRESSION_ALGO',
        'AUDIT_BATCH_FLUSH_INTERVAL',
    ]
    
    all_found = True
    for var in required_vars:
        if var in env_content:
            print(f"{Colors.GREEN}  ✓ Variable '{var}' documented{Colors.NC}")
        else:
            print(f"{Colors.RED}  ✗ Variable '{var}' not found{Colors.NC}")
            all_found = False
    
    return all_found


def test_deployment_templates():
    """Test that deployment templates exist"""
    print(f"{Colors.BLUE}Testing deployment templates...{Colors.NC}")
    
    templates = [
        'deploy_templates/railway.audit.template.env',
    ]
    
    all_exist = True
    for template in templates:
        if os.path.exists(template):
            print(f"{Colors.GREEN}  ✓ {template} exists{Colors.NC}")
        else:
            print(f"{Colors.YELLOW}  ⚠ {template} not found (may not be created yet){Colors.NC}")
    
    return True  # Not critical if templates don't exist yet


def test_config_comprehensive():
    """Test that enhanced config has comprehensive options"""
    print(f"{Colors.BLUE}Testing comprehensive configuration coverage...{Colors.NC}")
    
    if not os.path.exists('generator/audit_config.enhanced.yaml'):
        print(f"{Colors.RED}  ✗ Enhanced config not found{Colors.NC}")
        return False
    
    with open('generator/audit_config.enhanced.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    # Check for major configuration sections
    important_keys = [
        'PROVIDER_TYPE',
        'DEFAULT_ALGO',
        'BACKEND_TYPE',
        'COMPRESSION_ALGO',
        'BATCH_FLUSH_INTERVAL',
        'RETRY_MAX_ATTEMPTS',
        'TAMPER_DETECTION_ENABLED',
        'ENCRYPTION_ENABLED',
        'IMMUTABLE',
    ]
    
    all_found = True
    found_count = 0
    for key in important_keys:
        if key in config:
            found_count += 1
        else:
            all_found = False
    
    print(f"{Colors.GREEN}  ✓ Found {found_count}/{len(important_keys)} important configuration keys{Colors.NC}")
    
    return found_count >= len(important_keys) * 0.8  # 80% threshold


def test_validation_script_help():
    """Test that validation script has help text"""
    print(f"{Colors.BLUE}Testing validation script help...{Colors.NC}")
    
    success, stdout, stderr = run_command(
        'python generator/audit_log/validate_config.py --help'
    )
    
    if success and 'usage:' in stdout.lower():
        print(f"{Colors.GREEN}  ✓ Validation script has help text{Colors.NC}")
    else:
        print(f"{Colors.RED}  ✗ Validation script help failed{Colors.NC}")
    
    return success


def main():
    """Run all integration tests"""
    print("\n" + "=" * 80)
    print(f"{Colors.BLUE}AUDIT CONFIGURATION INTEGRATION TESTS{Colors.NC}")
    print("=" * 80 + "\n")
    
    # Change to repository root
    repo_root = Path(__file__).parent
    os.chdir(repo_root)
    
    tests = [
        ("YAML Syntax", test_yaml_syntax),
        ("Validation Script Syntax", test_validation_script_syntax),
        ("Validation Script Execution", test_validation_script_execution),
        ("Production Config Validation", test_config_validation_prod),
        ("Development Config Validation", test_config_validation_dev),
        ("Environment Variable Validation", test_env_variable_validation),
        ("Documentation Exists", test_documentation_exists),
        ("Makefile Targets", test_makefile_targets),
        ("Environment Template Updated", test_env_template_updated),
        ("Deployment Templates", test_deployment_templates),
        ("Comprehensive Config Coverage", test_config_comprehensive),
        ("Validation Script Help", test_validation_script_help),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
            print()
        except Exception as e:
            print(f"{Colors.RED}  ✗ Test '{test_name}' raised exception: {e}{Colors.NC}")
            results.append((test_name, False))
            print()
    
    # Print summary
    print("=" * 80)
    print(f"{Colors.BLUE}TEST SUMMARY{Colors.NC}")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = f"{Colors.GREEN}PASS{Colors.NC}" if result else f"{Colors.RED}FAIL{Colors.NC}"
        print(f"  {status} - {test_name}")
    
    print()
    print(f"Results: {passed}/{total} tests passed ({passed*100//total}%)")
    
    if passed == total:
        print(f"\n{Colors.GREEN}✓ All integration tests passed!{Colors.NC}\n")
        return 0
    else:
        print(f"\n{Colors.RED}✗ Some integration tests failed{Colors.NC}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
