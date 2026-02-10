from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from settings import settings


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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
