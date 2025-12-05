# main/__init__.py
# Lazy imports to avoid loading heavy dependencies during test collection
import os

# Only import if not in testing mode to avoid dependency loading during test collection
if os.environ.get("TESTING") != "1":
    from .api import api as fastapi_app
    from .api import create_db_tables
    from .cli import cli as main_cli
    from .gui import MainApp
else:
    # Provide None placeholders during testing
    fastapi_app = None
    create_db_tables = None
    main_cli = None
    MainApp = None

__all__ = ['fastapi_app', 'create_db_tables', 'main_cli', 'MainApp']
