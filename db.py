import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from models import Base

load_dotenv()
DATABASE_URL = os.environ.get(
    "SQLALCHEMY_DATABASE_URL",
    "sqlite:///./sqlite_data/criminal_investigation.db",
)

# 2. Engine
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
    future=True,
)


# 3. SQLite PRAGMA listener — THE critical bit
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # Only run for SQLite — detect by checking the driver class name.
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        # 1. Enforce foreign keys (and therefore our cascade rules).
        cursor.execute("PRAGMA foreign_keys = ON")
        # 2. Use Write-Ahead-Logging — better concurrency for the
        #    "100 investigators viewing leads simultaneously" non-functional
        #    requirement (R3.2.2.3.6).
        cursor.execute("PRAGMA journal_mode = WAL")
        # 3. Synchronous=NORMAL is safe with WAL and ~3× faster than FULL.
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()


# 4. Session factory
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,   # safer for FastAPI request scopes
    future=True,
)


# 5. FastAPI dependency
# Use as: `def my_route(db: Session = Depends(get_db)): ...`
def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 6. Context-manager helper for scripts (seed, CLI tools, tests)
# Example:
#     from db import session_scope
#     with session_scope() as db:
#         db.add(Province(code="PUNJAB", label="Punjab"))
@contextmanager
def session_scope():
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 7. Init helper — called once at app startup OR by Alembic migrations
def init_db():
    """
    Create all tables defined on the Base metadata.

    In production you'd use Alembic migrations instead — but this is handy
    for local dev and the FYP demo. Safe to call repeatedly: SQLAlchemy
    skips tables that already exist.
    """
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print(f"✓ Schema built at {DATABASE_URL}")
    print(f"✓ {len(Base.metadata.tables)} tables created")