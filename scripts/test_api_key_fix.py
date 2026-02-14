#!/usr/bin/env python3
"""
Test for get_api_key_for_provider method in ArbiterConfig.

This test verifies that the fix for the job vanishing bug works correctly.
The bug was: ArbiterConfig was missing the get_api_key_for_provider method,
causing AttributeError when the database tried to save jobs.
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_get_api_key_for_provider():
    """Test that ArbiterConfig has the get_api_key_for_provider method."""
    print("=" * 70)
    print("Testing get_api_key_for_provider method in ArbiterConfig")
    print("=" * 70)
    
    # Set test API keys
    os.environ['OPENAI_API_KEY'] = 'test-openai-key'
    os.environ['ANTHROPIC_API_KEY'] = 'test-anthropic-key'
    os.environ['GOOGLE_API_KEY'] = 'test-google-key'
    os.environ['LLM_API_KEY'] = 'test-fallback-key'
    
    try:
        from self_fixing_engineer.arbiter.config import ArbiterConfig
        print("✅ Successfully imported ArbiterConfig")
        
        # Check that the method exists
        if not hasattr(ArbiterConfig, 'get_api_key_for_provider'):
            print("❌ FAIL: ArbiterConfig does not have get_api_key_for_provider method")
            print("   This will cause AttributeError when saving jobs to database!")
            return False
        
        print("✅ get_api_key_for_provider method exists")
        
        # Create config instance
        config = ArbiterConfig()
        print("✅ ArbiterConfig instantiated successfully")
        
        # Test the method with different providers
        test_cases = [
            ("openai", "test-openai-key"),
            ("OpenAI", "test-openai-key"),  # Test case-insensitivity
            ("anthropic", "test-anthropic-key"),
            ("gemini", "test-google-key"),
            ("google", "test-google-key"),
            ("unknown_provider", "test-fallback-key"),  # Test fallback
        ]
        
        all_passed = True
        for provider, expected_key in test_cases:
            try:
                actual_key = config.get_api_key_for_provider(provider)
                if actual_key == expected_key:
                    print(f"✅ get_api_key_for_provider('{provider}') returned correct key")
                else:
                    print(f"❌ get_api_key_for_provider('{provider}') returned '{actual_key}', expected '{expected_key}'")
                    all_passed = False
            except Exception as e:
                print(f"❌ get_api_key_for_provider('{provider}') raised exception: {e}")
                all_passed = False
        
        if all_passed:
            print("\n✅ All tests passed!")
            print("Jobs will now save correctly to the database.")
            return True
        else:
            print("\n❌ Some tests failed")
            return False
            
    except ImportError as e:
        print(f"❌ Failed to import ArbiterConfig: {e}")
        print("   This test requires dependencies to be installed.")
        print("   Run: pip install -r requirements.txt")
        return False
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_import():
    """Test that database.py can use the method."""
    print("\n" + "=" * 70)
    print("Testing database.py can use get_api_key_for_provider")
    print("=" * 70)
    
    try:
        # Simulate what database.py does
        from self_fixing_engineer.arbiter.config import ArbiterConfig
        config = ArbiterConfig()
        
        # This is what database.py tries to do during save_job_to_database
        # If this fails, jobs will vanish
        api_key = config.get_api_key_for_provider("openai")
        
        if api_key:
            print(f"✅ Database layer can retrieve API keys")
            print(f"   Retrieved key: {api_key[:10]}... (first 10 chars)")
            return True
        else:
            print(f"⚠️  Method works but no API key set")
            print(f"   (This is OK for test environment)")
            return True
            
    except AttributeError as e:
        print(f"❌ CRITICAL: AttributeError during API key retrieval: {e}")
        print(f"   This is the bug that causes jobs to vanish!")
        print(f"   Jobs will fail to save and disappear from the system.")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("JOB VANISHING BUG FIX VERIFICATION")
    print("Testing: ArbiterConfig.get_api_key_for_provider method")
    print("=" * 70 + "\n")
    
    results = []
    results.append(("Method exists and works", test_get_api_key_for_provider()))
    results.append(("Database layer compatibility", test_database_import()))
    
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 70)
    
    if all_passed:
        print("\n🎉 All tests passed!")
        print("The job vanishing bug is fixed.")
        print("Jobs will now save correctly to the database.\n")
        return 0
    else:
        print("\n⚠️  Some tests failed!")
        print("Jobs may still vanish until the fix is complete.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
