#!/usr/bin/env python3
"""
Minimal test script to verify the core runtime error fixes.
This test focuses on the actual code changes without requiring all dependencies.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_counter_import():
    """Test that Counter imports don't conflict in critique_linter.py."""
    print("\n1. Testing Counter import fix in critique_linter.py...")
    try:
        # Read the file to verify the import statement
        with open('generator/agents/critique_agent/critique_linter.py', 'r') as f:
            content = f.read()
        
        # Check for the renamed import
        if 'from collections import Counter as CollectionsCounter' in content:
            print("   ✓ Collections Counter properly renamed to CollectionsCounter")
        else:
            print("   ✗ Collections Counter import not renamed")
            return False
        
        # Check for usage
        if 'CollectionsCounter(e["severity"]' in content:
            print("   ✓ CollectionsCounter used correctly in code")
        else:
            print("   ✗ CollectionsCounter not used in code")
            return False
        
        # Verify prometheus Counter is still imported
        if 'from prometheus_client import Counter' in content:
            print("   ✓ Prometheus Counter still imported")
        else:
            print("   ✗ Prometheus Counter not imported")
            return False
        
        return True
    except Exception as e:
        print(f"   ✗ Test failed: {e}")
        return False


def test_docgen_metrics():
    """Test that docgen_agent.py uses correct metric labels."""
    print("\n2. Testing metrics labels in docgen_agent.py...")
    try:
        with open('generator/agents/docgen_agent/docgen_agent.py', 'r') as f:
            content = f.read()
        
        # Check that task parameter is removed
        if 'task="generate_docs"' in content:
            print("   ✗ Invalid 'task' parameter still present in metrics")
            return False
        
        # Check for correct label usage
        if 'LLM_CALLS_TOTAL.labels(\n                    provider="docgen_agent", model=llm_model\n                ).inc()' in content:
            print("   ✓ LLM_CALLS_TOTAL uses correct labels (provider, model)")
        else:
            print("   ℹ LLM_CALLS_TOTAL format might differ (checking for absence of task parameter)")
        
        if 'LLM_LATENCY_SECONDS.labels(\n                    provider="docgen_agent", model=llm_model\n                ).observe' in content:
            print("   ✓ LLM_LATENCY_SECONDS uses correct labels (provider, model)")
        else:
            print("   ℹ LLM_LATENCY_SECONDS format might differ (checking for absence of task parameter)")
        
        return True
    except Exception as e:
        print(f"   ✗ Test failed: {e}")
        return False


def test_deploy_runner_error():
    """Test that deploy_agent.py uses correct RunnerError signature."""
    print("\n3. Testing RunnerError signature in deploy_agent.py...")
    try:
        with open('generator/agents/deploy_agent/deploy_agent.py', 'r') as f:
            content = f.read()
        
        # Check for proper RunnerError signature
        if 'RunnerError(\n                            error_code="VALIDATION_FAILED"' in content:
            print("   ✓ VALIDATION_FAILED uses correct RunnerError signature")
        else:
            print("   ✗ VALIDATION_FAILED does not use correct signature")
            return False
        
        if 'RunnerError(\n                            error_code="SIMULATION_FAILED"' in content:
            print("   ✓ SIMULATION_FAILED uses correct RunnerError signature")
        else:
            print("   ✗ SIMULATION_FAILED does not use correct signature")
            return False
        
        # Check that error codes are registered
        with open('generator/runner/runner_errors.py', 'r') as f:
            error_content = f.read()
        
        if 'register_error_code(\n    "VALIDATION_FAILED"' in error_content:
            print("   ✓ VALIDATION_FAILED error code registered")
        else:
            print("   ✗ VALIDATION_FAILED error code not registered")
            return False
        
        if 'register_error_code(\n    "SIMULATION_FAILED"' in error_content:
            print("   ✓ SIMULATION_FAILED error code registered")
        else:
            print("   ✗ SIMULATION_FAILED error code not registered")
            return False
        
        return True
    except Exception as e:
        print(f"   ✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_clarifier_permission_handling():
    """Test that clarifier.py handles permissions correctly."""
    print("\n4. Testing permission handling in clarifier.py...")
    try:
        with open('generator/clarifier/clarifier.py', 'r') as f:
            content = f.read()
        
        # Check for permission error handling in _save_history
        if 'except PermissionError as pe:' in content:
            print("   ✓ PermissionError handling added")
        else:
            print("   ✗ PermissionError not handled")
            return False
        
        # Check for fallback path logic
        if 'fallback_path = "/tmp/clarifier_history.json"' in content:
            print("   ✓ Fallback path to /tmp implemented")
        else:
            print("   ✗ Fallback path not implemented")
            return False
        
        # Check for local variable usage (not mutating config)
        if 'save_path = self.config.HISTORY_FILE' in content:
            print("   ✓ Uses local variable for save path (good practice)")
        else:
            print("   ℹ Uses config directly for save path")
        
        # Check for write permission check
        if 'os.access' in content and 'os.W_OK' in content:
            print("   ✓ Write permission check added")
        else:
            print("   ℹ Write permission check may not be present")
        
        # Check for exception handling in graceful_shutdown
        shutdown_section = content[content.find('async def graceful_shutdown'):content.find('async def graceful_shutdown') + 2000] if 'async def graceful_shutdown' in content else ""
        if 'try:' in shutdown_section and 'await self._save_history()' in shutdown_section:
            print("   ✓ graceful_shutdown wraps _save_history in try-except")
        else:
            print("   ℹ graceful_shutdown exception handling may differ")
        
        return True
    except Exception as e:
        print(f"   ✗ Test failed: {e}")
        return False


def test_runner_error_trace_handling():
    """Test that runner_errors.py handles missing OpenTelemetry gracefully."""
    print("\n5. Testing OpenTelemetry fallback in runner_errors.py...")
    try:
        with open('generator/runner/runner_errors.py', 'r') as f:
            content = f.read()
        
        # Check for NoOp trace module
        if 'class _NoOpTrace:' in content:
            print("   ✓ NoOp trace module defined for fallback")
        else:
            print("   ✗ NoOp trace module not found")
            return False
        
        # Check for get_current_span method
        if 'def get_current_span(self):' in content:
            print("   ✓ get_current_span method implemented in fallback")
        else:
            print("   ✗ get_current_span method not implemented")
            return False
        
        # Check that trace is assigned in fallback
        if 'trace = _NoOpTrace()' in content:
            print("   ✓ trace assigned to NoOp implementation when OTel unavailable")
        else:
            print("   ✗ trace not assigned in fallback")
            return False
        
        return True
    except Exception as e:
        print(f"   ✗ Test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing Runtime Error Fixes (Code Review)")
    print("=" * 70)
    
    tests = [
        test_counter_import,
        test_docgen_metrics,
        test_deploy_runner_error,
        test_clarifier_permission_handling,
        test_runner_error_trace_handling,
    ]
    
    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"   ✗ Test {test.__name__} crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    print("\n" + "=" * 70)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("=" * 70)
    
    if all(results):
        print("\n✓ All runtime error fixes verified successfully!")
        return 0
    else:
        print("\n✗ Some tests failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
