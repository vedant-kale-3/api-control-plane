import os
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session


def get_db_path() -> Path:
    """Writable per-user app-data directory — never a path relative to the
    frozen exe (ARCHITECTURE.md, Packaging Notes)."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "APIControlPlane"
    base.mkdir(parents=True, exist_ok=True)
    return base / "data.db"


DB_PATH = get_db_path()
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def init_db():
    from app import models  # noqa: F401  ensures all tables are registered first
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
