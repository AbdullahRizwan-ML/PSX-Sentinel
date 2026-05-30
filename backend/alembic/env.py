"""
PSX Sentinel — Alembic Environment Configuration

Configures Alembic to use:
- The SQLAlchemy Base.metadata from our models for autogeneration
- The sync engine from session.py (Alembic requires synchronous connections)
- The real DATABASE_URL from Settings, with asyncpg replaced by psycopg2

Supports both offline (SQL script generation) and online (direct DB)
migration modes.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import sync_engine

# Alembic Config object — provides access to alembic.ini values
config = context.config

# Set up Python logging from the alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is the metadata that Alembic uses for autogenerate support.
# It must include all models that should be tracked for migrations.
target_metadata = Base.metadata

# Override the sqlalchemy.url with our actual database URL (sync version)
settings = get_settings()
config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"),
)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is also acceptable here. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output as raw SQL.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    Uses the sync_engine from session.py to connect directly to the
    database and apply migrations.
    """
    connectable = sync_engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
