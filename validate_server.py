#!/usr/bin/env python3
"""
Simple validation script for the Code Factory API Server.

This script validates that the server structure is correctly implemented
without requiring all runtime dependencies to be installed.
"""

import os
import sys
from pathlib import Path

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_header(text):
    print(f"\n{BLUE}{'=' * 70}{RESET}")
    print(f"{BLUE}{text:^70}{RESET}")
    print(f"{BLUE}{'=' * 70}{RESET}\n")


def print_success(text):
    print(f"{GREEN}✓{RESET} {text}")


def print_warning(text):
    print(f"{YELLOW}⚠{RESET} {text}")


def print_error(text):
    print(f"{RED}✗{RESET} {text}")


def check_file_exists(file_path, description):
    """Check if a file exists."""
    if os.path.exists(file_path):
        print_success(f"{description}: {file_path}")
        return True
    else:
        print_error(f"{description} missing: {file_path}")
        return False


def check_directory_structure():
    """Validate the server directory structure."""
    print_header("Checking Directory Structure")

    base_dir = Path("server")
    checks = [
        (base_dir, "Server root directory"),
        (base_dir / "routers", "Routers directory"),
        (base_dir / "schemas", "Schemas directory"),
        (base_dir / "services", "Services directory"),
        (base_dir / "middleware", "Middleware directory"),
    ]

    all_ok = True
    for path, description in checks:
        if path.exists():
            print_success(f"{description}: {path}")
        else:
            print_error(f"{description} missing: {path}")
            all_ok = False

    return all_ok


def check_core_files():
    """Check that core files exist."""
    print_header("Checking Core Files")

    files = [
        ("server/__init__.py", "Package init"),
        ("server/main.py", "FastAPI application"),
        ("server/run.py", "Startup script"),
        ("server/README.md", "Documentation"),
    ]

    all_ok = True
    for file_path, description in files:
        if not check_file_exists(file_path, description):
            all_ok = False

    return all_ok


def check_routers():
    """Check that all router files exist."""
    print_header("Checking Routers")

    routers = [
        ("server/routers/__init__.py", "Routers init"),
        ("server/routers/jobs.py", "Jobs router"),
        ("server/routers/generator.py", "Generator router"),
        ("server/routers/omnicore.py", "OmniCore router"),
        ("server/routers/sfe.py", "SFE router"),
        ("server/routers/fixes.py", "Fixes router"),
        ("server/routers/events.py", "Events router"),
    ]

    all_ok = True
    for file_path, description in routers:
        if not check_file_exists(file_path, description):
            all_ok = False

    return all_ok


def check_schemas():
    """Check that all schema files exist."""
    print_header("Checking Schemas")

    schemas = [
        ("server/schemas/__init__.py", "Schemas init"),
        ("server/schemas/common.py", "Common schemas"),
        ("server/schemas/jobs.py", "Job schemas"),
        ("server/schemas/events.py", "Event schemas"),
        ("server/schemas/fixes.py", "Fix schemas"),
    ]

    all_ok = True
    for file_path, description in schemas:
        if not check_file_exists(file_path, description):
            all_ok = False

    return all_ok


def check_services():
    """Check that all service files exist."""
    print_header("Checking Services")

    services = [
        ("server/services/__init__.py", "Services init"),
        ("server/services/generator_service.py", "Generator service"),
        ("server/services/omnicore_service.py", "OmniCore service"),
        ("server/services/sfe_service.py", "SFE service"),
    ]

    all_ok = True
    for file_path, description in services:
        if not check_file_exists(file_path, description):
            all_ok = False

    return all_ok


def count_lines_of_code():
    """Count total lines of code in the server package."""
    print_header("Code Statistics")

    total_lines = 0
    total_files = 0

    for root, dirs, files in os.walk("server"):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r") as f:
                    lines = sum(1 for line in f)
                    total_lines += lines
                    total_files += 1

    print_success(f"Total Python files: {total_files}")
    print_success(f"Total lines of code: {total_lines}")

    return True


def check_key_features():
    """Check that key features are implemented."""
    print_header("Checking Key Features")

    features = {
        "File upload": "upload" in open("server/routers/generator.py").read(),
        "Job management": "create_job" in open("server/routers/jobs.py").read(),
        "Progress tracking": "get_job_progress"
        in open("server/routers/jobs.py").read(),
        "Error detection": "detect_errors"
        in open("server/services/sfe_service.py").read(),
        "Fix proposals": "propose_fix" in open("server/services/sfe_service.py").read(),
        "Fix application": "apply_fix" in open("server/services/sfe_service.py").read(),
        "WebSocket events": "WebSocket" in open("server/routers/events.py").read(),
        "SSE events": "EventSourceResponse" in open("server/routers/events.py").read(),
        "OmniCore routing": "route_job"
        in open("server/services/omnicore_service.py").read(),
        "OpenAPI docs": "openapi_url" in open("server/main.py").read(),
    }

    all_ok = True
    for feature, implemented in features.items():
        if implemented:
            print_success(f"{feature} implementation found")
        else:
            print_error(f"{feature} implementation not found")
            all_ok = False

    return all_ok


def main():
    """Main validation function."""
    print(f"\n{BLUE}{'*' * 70}{RESET}")
    print(f"{BLUE}{'Code Factory API Server - Structure Validation':^70}{RESET}")
    print(f"{BLUE}{'*' * 70}{RESET}")

    # Change to project root
    os.chdir(Path(__file__).parent)

    results = []
    results.append(("Directory Structure", check_directory_structure()))
    results.append(("Core Files", check_core_files()))
    results.append(("Routers", check_routers()))
    results.append(("Schemas", check_schemas()))
    results.append(("Services", check_services()))
    results.append(("Code Statistics", count_lines_of_code()))
    results.append(("Key Features", check_key_features()))

    # Summary
    print_header("Validation Summary")

    all_passed = all(result for _, result in results)
    passed = sum(1 for _, result in results if result)
    total = len(results)

    for check_name, result in results:
        if result:
            print_success(f"{check_name}: PASS")
        else:
            print_error(f"{check_name}: FAIL")

    print(f"\n{BLUE}{'─' * 70}{RESET}")
    if all_passed:
        print(f"{GREEN}✓ All checks passed ({passed}/{total}){RESET}")
        print(f"\n{GREEN}Server structure is correctly implemented!{RESET}\n")
        print(f"{YELLOW}To run the server:{RESET}")
        print(f"  1. Install dependencies: pip install -r requirements.txt")
        print(f"  2. Run server: python server/run.py --reload")
        print(f"  3. Access docs: http://localhost:8000/api/docs")
        return 0
    else:
        print(f"{RED}✗ Some checks failed ({passed}/{total}){RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
