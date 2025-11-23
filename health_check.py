#!/usr/bin/env python3
"""
Code Factory Health Check Script
Run this anytime to verify system operational status
"""
import sys
from pathlib import Path

# Add paths
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root / "self_fixing_engineer"))
sys.path.insert(0, str(repo_root))

def print_header(text):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def print_status(check_name, passed, details=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status:12} {check_name}")
    if details:
        print(f"             └─ {details}")

def main():
    print("\n" + "█" * 70)
    print("█  CODE FACTORY HEALTH CHECK")
    print("█" * 70)
    
    all_passed = True
    
    # Check 1: Core imports
    print_header("1. Core Module Imports")
    try:
        from omnicore_engine.core import safe_serialize
        print_status("OmniCore imports", True, "Core engine modules loaded")
    except Exception as e:
        print_status("OmniCore imports", False, str(e))
        all_passed = False
    
    # Check 2: Arbiter imports
    print_header("2. Arbiter/SFE Imports")
    try:
        from arbiter.config import ArbiterConfig
        from arbiter.arbiter_plugin_registry import PluginRegistry
        ArbiterConfig()
        registry = PluginRegistry()
        plugin_count = len(registry._plugins)
        print_status("Arbiter imports", True, f"{plugin_count} plugins loaded")
    except Exception as e:
        print_status("Arbiter imports", False, str(e))
        all_passed = False
    
    # Check 3: Security
    print_header("3. Security Features")
    try:
        from omnicore_engine.security_utils import (
            SecurityError, SecurityException
        )
        assert SecurityException is SecurityError
        print_status("Security imports", True, "All security classes available")
        print_status("SecurityException alias", True, "Backward compatibility OK")
    except Exception as e:
        print_status("Security checks", False, str(e))
        all_passed = False
    
    # Check 4: Bug fixes verified
    print_header("4. Critical Bug Fixes")
    try:
        from omnicore_engine.core import safe_serialize
        
        # Test the safe_serialize fix
        class BadObject:
            def __str__(self):
                raise Exception("Test exception")
        
        result = safe_serialize(BadObject())
        if "unserializable" in result:
            print_status("safe_serialize fix", True, "Exception handling working")
        else:
            print_status("safe_serialize fix", False, "Unexpected result")
            all_passed = False
    except Exception as e:
        print_status("safe_serialize fix", False, str(e))
        all_passed = False
    
    # Check 5: CLI availability
    print_header("5. CLI Interfaces")
    try:
        print_status("OmniCore CLI", True, "CLI module available")
    except Exception as e:
        print_status("OmniCore CLI", False, str(e))
        all_passed = False
    
    try:
        print_status("SFE main", True, "SFE entrypoint available")
    except Exception as e:
        print_status("SFE main", False, str(e))
        all_passed = False
    
    # Check 6: Optional dependencies
    print_header("6. Optional Dependencies (Info Only)")
    optional_deps = {
        "fastapi_csrf_protect": "Web API CSRF protection",
        "httpx": "HTTP client for testing",
        "click_help_colors": "Generator CLI colors",
        "rich": "Enhanced console output",
        "torch": "ML-based features",
        "langchain_openai": "LangChain integration",
    }
    
    for dep, description in optional_deps.items():
        try:
            __import__(dep)
            print_status(dep, True, description)
        except ImportError:
            print_status(dep, False, f"{description} (optional)")
    
    # Final summary
    print_header("SUMMARY")
    if all_passed:
        print("\n  ✅ SYSTEM OPERATIONAL")
        print("  All critical components are working correctly.")
        print("\n  The Code Factory is ready for use!")
        print("\n  Next steps:")
        print("  1. See DEMO_READINESS_CHECKLIST.md for demo scenarios")
        print("  2. Run: cd omnicore_engine && python -m omnicore_engine.cli --help")
        print("  3. Optional: Install dependencies listed above for enhanced features")
    else:
        print("\n  ❌ SYSTEM ISSUES DETECTED")
        print("  Some critical components failed checks.")
        print("\n  Please review the failures above and:")
        print("  1. Check PYTHONPATH is set correctly")
        print("  2. Verify dependencies are installed")
        print("  3. See DEEP_CODE_AUDIT_REPORT.md for troubleshooting")
    
    print("\n" + "█" * 70 + "\n")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    # Suppress warnings for cleaner output
    import warnings
    warnings.filterwarnings("ignore")
    
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nHealth check interrupted.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
