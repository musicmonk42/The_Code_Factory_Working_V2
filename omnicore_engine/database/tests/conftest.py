"""Test configuration for database tests."""

import pytest


@pytest.fixture(autouse=True)
def clear_sqlalchemy_metadata():
    """Clear SQLAlchemy metadata before each test to avoid table redefinition errors."""
    try:
        from omnicore_engine.database import models

        if hasattr(models, "Base") and hasattr(models.Base, "metadata"):
            models.Base.metadata.clear()
    except ImportError:
        pass

    yield

    try:
        from omnicore_engine.database import models

        if hasattr(models, "Base") and hasattr(models.Base, "metadata"):
            models.Base.metadata.clear()
    except ImportError:
        pass
