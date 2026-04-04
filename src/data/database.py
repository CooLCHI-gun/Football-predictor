from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import get_settings


def get_engine() -> Engine:
    settings = get_settings()
    sqlite_path = settings.sqlite_file_path
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(settings.database_url, future=True)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    effective_engine = engine or get_engine()
    return sessionmaker(bind=effective_engine, autoflush=False, autocommit=False, future=True)


def ensure_sqlite_parent_dir() -> Path | None:
    settings = get_settings()
    sqlite_path = settings.sqlite_file_path
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_path
