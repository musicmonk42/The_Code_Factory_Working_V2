# Database Migrations

This directory contains Alembic database migrations for the Code Factory platform.

## Prerequisites

Before generating or running migrations, ensure you have all dependencies installed:

```bash
pip install -r requirements.txt
```

## Generating Migrations

To generate a new migration based on model changes:

```bash
# For SQLite (development)
DB_PATH="sqlite:///./omnicore.db" alembic revision --autogenerate -m "Description of changes"

# For PostgreSQL (production)
DB_PATH="postgresql+psycopg2://user:pass@host:5432/dbname" alembic revision --autogenerate -m "Description of changes"
```

**Note:** Alembic needs access to all model classes to detect changes. Ensure all models are imported in `env.py` and that your environment has all necessary dependencies installed.

## Running Migrations

To apply migrations:

```bash
# Upgrade to latest version
alembic upgrade head

# Downgrade one version
alembic downgrade -1

# View migration history
alembic history

# Check current version
alembic current
```

## Environment Variables

The migration system reads the database URL from environment variables in this order:
1. `DATABASE_URL`
2. `DB_PATH`
3. Default: `sqlite:///./omnicore.db`

## Async Database Support

The application uses async SQLAlchemy (`asyncpg` for PostgreSQL, `aiosqlite` for SQLite), but Alembic uses synchronous drivers:
- `postgresql+asyncpg://` → `postgresql+psycopg2://`
- `sqlite+aiosqlite://` → `sqlite://`

The conversion is handled automatically in `env.py`.

## Production Deployment

In production, migrations are automatically applied by the `Database.create_tables()` method if the migrations directory exists. See `omnicore_engine/database/database.py` lines ~954-980.

To manually run migrations in production:

```bash
# Set production database URL
export DB_PATH="postgresql+psycopg2://..."

# Apply migrations
alembic upgrade head
```

## Troubleshooting

### "ModuleNotFoundError" when generating migrations

Ensure all dependencies are installed:
```bash
pip install -r requirements.txt
```

### Empty migration generated

This usually means models couldn't be imported. Check:
1. All dependencies are installed
2. No import errors in model files
3. All models are imported in `env.py`

### Migration conflicts

If you have multiple development branches with migrations:
```bash
# Merge migration branches
alembic merge heads -m "Merge migration branches"
```

## Directory Structure

```
migrations/
├── env.py              # Alembic environment configuration
├── script.py.mako      # Template for new migration files
├── versions/           # Migration version files
│   └── .gitkeep       # Ensures directory is tracked by git
└── README.md          # This file
```
