import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add the self_fixing_engineer directory so arbiter can be imported
sys.path.insert(0, os.path.join(project_root, 'self_fixing_engineer'))

# Add omnicore_engine directory
sys.path.insert(0, os.path.join(project_root, 'omnicore_engine'))

# Add generator directory
sys.path.insert(0, os.path.join(project_root, 'generator'))

# ---- Pydantic decorator safety shim ----
# Prevents test collection-time errors when pydantic decorators are replaced with non-callables
try:
    import pydantic
    
    # No-op decorator that preserves function/class behavior used by Pydantic decorators
    def _noop_validator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    
    # Defensive: if field_validator/model_validator are not callables (e.g. MagicMock),
    # replace with safe no-op decorators so class definitions don't create non-annotated attributes.
    try:
        if not callable(getattr(pydantic, "field_validator", None)):
            pydantic.field_validator = _noop_validator
    except Exception:
        # keep tests from failing because of unexpected pydantic internal structure
        pydantic.field_validator = _noop_validator  # best-effort
    
    try:
        if not callable(getattr(pydantic, "model_validator", None)):
            pydantic.model_validator = _noop_validator
    except Exception:
        pydantic.model_validator = _noop_validator
        
    # If your tests also mock other pydantic decorators, add similar guards here:
    # pydantic.field_serializer, pydantic.validator, etc.
except ImportError:
    # pydantic not installed, skip shim
    pass

# ---- pytest_plugins configuration ----
# Move from nested conftest files to top-level to avoid pytest deprecation warning
pytest_plugins = ["pytest_asyncio"]