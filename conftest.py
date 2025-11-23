import os
import sys

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add the self_fixing_engineer directory so arbiter can be imported
sys.path.insert(0, os.path.join(project_root, "self_fixing_engineer"))

# Add omnicore_engine directory
sys.path.insert(0, os.path.join(project_root, "omnicore_engine"))

# Add generator directory
sys.path.insert(0, os.path.join(project_root, "generator"))

# ---- Pydantic decorator safety shim ----
# Prevents test collection-time errors when pydantic decorators are replaced with non-callables
try:
    import pydantic

    # No-op decorator that preserves function/class behavior used by Pydantic decorators
    def _noop_validator(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    # Helper function to safely set pydantic decorators
    def _set_pydantic_decorator_safely(decorator_name):
        """Set a pydantic decorator to no-op if it's not callable."""
        try:
            if not callable(getattr(pydantic, decorator_name, None)):
                setattr(pydantic, decorator_name, _noop_validator)
        except (AttributeError, TypeError):
            # Attribute doesn't exist or has unexpected type
            setattr(pydantic, decorator_name, _noop_validator)  # best-effort

    # Apply to commonly mocked decorators
    _set_pydantic_decorator_safely("field_validator")
    _set_pydantic_decorator_safely("model_validator")
    # If your tests also mock other pydantic decorators, add them here:
    # _set_pydantic_decorator_safely("field_serializer")
    # _set_pydantic_decorator_safely("validator")

except ImportError:
    # pydantic not installed, skip shim
    pass

# ---- pytest_plugins configuration ----
# Move from nested conftest files to top-level to avoid pytest deprecation warning
pytest_plugins = ["pytest_asyncio"]
