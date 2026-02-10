# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Alembic environment configuration for Code Factory database migrations.

This module configures Alembic to work with the project's SQLAlchemy models
and async database engine.
"""

from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the project root to the Python path to allow imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import the SQLAlchemy Base and all models to ensure they're registered
# This is critical - all models must be imported for autogenerate to work
try:
    # Attempt to import models directly, which will use standalone mode if arbiter is unavailable
    from omnicore_engine.database import models as models_module
    
    Base = models_module.Base
    AgentState = getattr(models_module, 'AgentState', None)
    GeneratorAgentState = getattr(models_module, 'GeneratorAgentState', None)
    SFEAgentState = getattr(models_module, 'SFEAgentState', None)
    ExplainAuditRecord = getattr(models_module, 'ExplainAuditRecord', None)
except Exception as e:
    # If models can't be imported, we can still proceed with just Base for initial setup
    print(f"Warning: Could not import all models: {e}")
    print("Will attempt minimal import. Migration may not detect all tables.")
    # Create a minimal Base as fallback
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the SQLAlchemy URL from environment variable
# Priority: DATABASE_URL > DB_PATH > default SQLite
database_url = os.getenv("DATABASE_URL") or os.getenv("DB_PATH")
if not database_url:
    # Default to SQLite for development
    database_url = "sqlite:///./omnicore.db"

# Convert async URLs to sync for Alembic
# Alembic doesn't support async drivers directly, so we convert:
# postgresql+asyncpg:// -> postgresql+psycopg2://
# sqlite+aiosqlite:// -> sqlite://
if database_url.startswith("postgresql+asyncpg://"):
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
elif database_url.startswith("sqlite+aiosqlite://"):
    database_url = database_url.replace("sqlite+aiosqlite://", "sqlite://")

config.set_main_option("sqlalchemy.url", database_url)

# Add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # Detect column type changes
        compare_server_default=True,  # Detect default value changes
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # Detect column type changes
            compare_server_default=True,  # Detect default value changes
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
