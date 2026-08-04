"""Database engine, migration, and request-session support."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Request
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, create_engine


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def ensure_database_directory(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database."""

    url = make_url(database_url)
    if not url.get_backend_name().startswith("sqlite"):
        return
    database = url.database
    if not database or database == ":memory:":
        return
    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)


def run_migrations(database_url: str) -> None:
    """Upgrade the configured database to the repository's Alembic head."""

    ensure_database_directory(database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # Alembic ConfigParser treats percent signs as interpolation syntax.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def create_database_engine(database_url: str) -> Engine:
    """Create an engine suitable for SQLite or a configured external database."""

    url = make_url(database_url)
    engine_kwargs: dict = {"pool_pre_ping": True}
    if url.get_backend_name().startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **engine_kwargs)


def get_database_session(request: Request) -> Generator[Session, None, None]:
    """Provide a short-lived database session from the application engine."""

    with Session(request.app.state.database_engine) as session:
        yield session
