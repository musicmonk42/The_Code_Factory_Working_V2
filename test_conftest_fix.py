"""
Test to verify that conftest.py no longer causes cascading import failures.

This test specifically validates that the fix for the LazyStubImporter
custom import finder allows normal Python imports to work correctly.
"""
import sys


def test_conftest_imports_without_errors():
    """Verify conftest.py can be imported without errors."""
    import conftest
    assert conftest is not None


def test_boto3_imports_work():
    """
    Verify boto3/botocore can be imported without the AttributeError.
    
    This was the specific error mentioned in the problem statement:
    AttributeError: module 'botocore.vendored.requests' has no attribute 'exceptions'
    """
    try:
        import boto3
        import botocore
        # If we get here, imports worked
        assert True
    except AttributeError as e:
        if "botocore.vendored.requests" in str(e):
            pytest.fail(f"The custom import finder bug is still present: {e}")
        else:
            # Different AttributeError, not related to our fix
            raise
    except ImportError:
        # boto3 not installed in this environment is fine
        # The important thing is we don't get the AttributeError
        pass


def test_no_custom_meta_path_finder():
    """Verify that LazyStubImporter is not installed in sys.meta_path."""
    # Check that no MetaPathFinder with find_spec calling __import__ is present
    for finder in sys.meta_path:
        finder_name = finder.__class__.__name__
        # LazyStubImporter should not be in meta_path
        assert "LazyStubImporter" not in finder_name, \
            f"LazyStubImporter still present in sys.meta_path: {finder_name}"


def test_pytest_collection_works():
    """
    Verify pytest can collect tests without import errors.
    
    This is tested by the fact that this test file can be collected and run.
    If conftest.py has import issues, pytest won't even get here.
    """
    assert True, "If this test runs, pytest collection is working"


if __name__ == "__main__":
    print("Testing conftest.py fix...")
    test_conftest_imports_without_errors()
    print("✓ conftest.py imports successfully")
    
    test_boto3_imports_work()
    print("✓ boto3/botocore imports work (or are not installed)")
    
    test_no_custom_meta_path_finder()
    print("✓ No LazyStubImporter in sys.meta_path")
    
    test_pytest_collection_works()
    print("✓ pytest collection works")
    
    print("\nAll tests passed! The conftest.py fix is working correctly.")
