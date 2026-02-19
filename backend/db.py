from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .settings import settings


BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(settings.PAYTECH_DATA_DIR) if settings.PAYTECH_DATA_DIR else (BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Default: SQLite em DATA_DIR
DB_URL = settings.PAYTECH_DB_URL or f"sqlite:///{(DATA_DIR / 'paytech.db').as_posix()}"

connect_args = {}
if DB_URL.startswith("sqlite:///"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DB_URL, echo=False, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def bootstrap_database() -> None:
    Base.metadata.create_all(bind=engine)
    if not DB_URL.startswith("sqlite:///"):
        return

    expected_columns = {
        "sessions": {
            "tenant_id": "TEXT",
            "user_id": "TEXT",
        },
        "messages": {
            "tenant_id": "TEXT",
        },
        "files": {
            "tenant_id": "TEXT",
            "full_text_path": "TEXT",
            "rows": "INTEGER",
            "cols": "INTEGER",
            "columns_json": "TEXT",
            "text_chars": "INTEGER",
        },
        "kb_chunks": {
            "tenant_id": "TEXT",
        },
        "downloads_files": {
            "tenant_id": "TEXT",
            "full_text_path": "TEXT",
            "rows": "INTEGER",
            "cols": "INTEGER",
            "columns_json": "TEXT",
            "text_chars": "INTEGER",
        },
        "downloads_chunks": {
            "tenant_id": "TEXT",
        },
        "users": {
            "tenant_id": "TEXT",
            "email": "TEXT DEFAULT ''",
            "status": "TEXT DEFAULT 'ACTIVE'",
        },
    }

    idx_statements = [
        "CREATE INDEX IF NOT EXISTS ix_sessions_tenant_id ON sessions (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_messages_tenant_id ON messages (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_files_tenant_id ON files (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_tenant_id ON kb_chunks (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_downloads_files_tenant_id ON downloads_files (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_downloads_chunks_tenant_id ON downloads_chunks (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
    ]

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, cols in expected_columns.items():
            if table_name not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_type in cols.items():
                if col_name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
        for stmt in idx_statements:
            conn.execute(text(stmt))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
