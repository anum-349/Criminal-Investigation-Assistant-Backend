# alembic/env.py
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, create_engine, event
from sqlalchemy.engine import Engine

from alembic import context

# ── 1. Make sure your project root is on sys.path ────────────────────────────
#    Adjust the number of ".." if alembic/ is nested deeper.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── 2. Load .env before reading DATABASE_URL ─────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── 3. Import your metadata so Alembic can diff the schema ───────────────────
#    Import Base from wherever you define it.  If you re-export it from db.py
#    that works too: `from db import Base`
from models import Base  # adjust if your Base lives elsewhere

# ── 4. Pull the real DATABASE_URL (same logic as db.py) ──────────────────────
DATABASE_URL = os.environ.get(
    "SQLALCHEMY_DATABASE_URL",
    "sqlite:///./sqlite_data/criminal_investigation.db",
)

# ── 5. Alembic Config object ──────────────────────────────────────────────────
config = context.config
# Override the (blank) sqlalchemy.url in alembic.ini at runtime
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── 6. SQLite PRAGMAs — mirror db.py so FK constraints are respected ─────────
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()


# ── 7. Offline migrations (generate SQL without connecting) ───────────────────
def run_migrations_offline() -> None:
    """
    Emit migration SQL to stdout without an active DB connection.
    Useful for reviewing or applying migrations manually.

    Usage:
        alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite doesn't support ALTER TABLE for most changes; Alembic can
        # recreate tables instead when render_as_batch=True.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── 8. Online migrations (connect and apply) ──────────────────────────────────
def run_migrations_online() -> None:
    """
    Apply migrations against a live database connection.

    Usage:
        alembic upgrade head
    """
    connect_args = {}
    if DATABASE_URL.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    connectable = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        poolclass=pool.NullPool,   # no connection pooling during migrations
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # ↓ Critical for SQLite: allows column/FK changes via table recreation
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()