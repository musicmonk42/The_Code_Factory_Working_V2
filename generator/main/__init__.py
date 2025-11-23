# main/__init__.py
from .api import api as fastapi_app
from .api import create_db_tables
from .cli import cli as main_cli
from .gui import MainApp
