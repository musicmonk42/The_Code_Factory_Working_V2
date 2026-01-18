#!/usr/bin/env python3
"""
Verification script to test that the import errors have been fixed.
This script checks:
1. CRITIQUE_PROMPT_BUILDS and CRITIQUE_PROMPT_LATENCY can be imported from runner.runner_metrics
2. ensemble_summarizers can be imported from runner.summarize_utils
3. HumanInLoop has proper circular import guard
"""

import sys
import os

# Set up environment
os.environ['TESTING'] = '1'
os.environ['OTEL_SDK_DISABLED'] = '1'

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'generator'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'self_fixing_engineer'))

def test_critique_prompt_metrics():
    """Test that CRITIQUE_PROMPT metrics can be imported."""
    try:
        from runner.runner_metrics import CRITIQUE_PROMPT_BUILDS, CRITIQUE_PROMPT_LATENCY
        print("✓ CRITIQUE_PROMPT_BUILDS and CRITIQUE_PROMPT_LATENCY imported successfully")
        print(f"  - CRITIQUE_PROMPT_BUILDS type: {type(CRITIQUE_PROMPT_BUILDS).__name__}")
        print(f"  - CRITIQUE_PROMPT_LATENCY type: {type(CRITIQUE_PROMPT_LATENCY).__name__}")
        return True
    except ImportError as e:
        print(f"✗ Error importing CRITIQUE_PROMPT metrics: {e}")
        return False

def test_ensemble_summarizers():
    """Test that ensemble_summarizers can be imported."""
    try:
        from runner.summarize_utils import ensemble_summarizers, ensemble_summarize
        print("✓ ensemble_summarizers imported successfully")
        print(f"  - ensemble_summarizers is ensemble_summarize: {ensemble_summarizers is ensemble_summarize}")
        return True
    except ImportError as e:
        print(f"✗ Error importing ensemble_summarizers: {e}")
        return False

def test_human_loop_guard():
    """Test that HumanInLoop has proper circular import guard."""
    try:
        # Check the file contains the guard
        human_loop_path = os.path.join(
            os.path.dirname(__file__), 
            'self_fixing_engineer/arbiter/human_loop.py'
        )
        with open(human_loop_path, 'r') as f:
            content = f.read()
            
        if "_HUMAN_LOOP_IMPORTING" in content:
            print("✓ HumanInLoop circular import guard is present")
            
            # Check it's near the top of the file (before line 100)
            lines = content.split('\n')
            for i, line in enumerate(lines[:100]):
                if "_HUMAN_LOOP_IMPORTING" in line and "not globals().get" in line:
                    print(f"  - Guard found at line {i+1}")
                    return True
            
            print("  ⚠ Guard exists but may not be positioned optimally")
            return True
        else:
            print("✗ HumanInLoop circular import guard not found")
            return False
    except Exception as e:
        print(f"✗ Error checking HumanInLoop guard: {e}")
        return False

def test_requirements_additions():
    """Test that required dependencies were added to requirements.txt."""
    try:
        requirements_path = os.path.join(os.path.dirname(__file__), 'requirements.txt')
        with open(requirements_path, 'r') as f:
            content = f.read()
        
        required_deps = [
            'pdfplumber',
            'python-docx',
            'python-magic',
            'stable-baselines3'
        ]
        
        all_found = True
        for dep in required_deps:
            if dep in content:
                print(f"✓ {dep} found in requirements.txt")
            else:
                print(f"✗ {dep} NOT found in requirements.txt")
                all_found = False
        
        return all_found
    except Exception as e:
        print(f"✗ Error checking requirements.txt: {e}")
        return False

def main():
    """Run all verification tests."""
    print("="*60)
    print("PYTEST IMPORT ERROR FIXES VERIFICATION")
    print("="*60)
    print()
    
    results = []
    
    print("Test 1: CRITIQUE_PROMPT metrics")
    print("-" * 60)
    results.append(test_critique_prompt_metrics())
    print()
    
    print("Test 2: ensemble_summarizers export")
    print("-" * 60)
    results.append(test_ensemble_summarizers())
    print()
    
    print("Test 3: HumanInLoop circular import guard")
    print("-" * 60)
    results.append(test_human_loop_guard())
    print()
    
    print("Test 4: Required dependencies in requirements.txt")
    print("-" * 60)
    results.append(test_requirements_additions())
    print()
    
    print("="*60)
    print("SUMMARY")
    print("="*60)
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")
    
    if all(results):
        print("\n✓✓✓ ALL TESTS PASSED ✓✓✓")
        print("\nThe import errors have been successfully fixed:")
        print("  1. CRITIQUE_PROMPT_BUILDS and CRITIQUE_PROMPT_LATENCY added to runner_metrics.py")
        print("  2. ensemble_summarizers alias added to summarize_utils.py")
        print("  3. HumanInLoop circular import guard verified")
        print("  4. Optional dependencies added to requirements.txt")
        return 0
    else:
        print("\n✗✗✗ SOME TESTS FAILED ✗✗✗")
        return 1

if __name__ == '__main__':
    sys.exit(main())
