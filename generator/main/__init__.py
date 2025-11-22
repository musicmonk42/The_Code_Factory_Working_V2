# main/__init__.py
from .cli import cli as main_cli
from .gui import MainApp
from .api import api as fastapi_app, create_db_tables
