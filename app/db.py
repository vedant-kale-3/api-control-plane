from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session
from app.paths import user_data_dir


def get_db_path() -> Path:
    """Return the path to the SQLite database file.

    Delegates to ``app.paths.user_data_dir()`` so the writable-directory
    logic is centralised in one place (app/paths.py) rather than duplicated
    here.  The directory is guaranteed to exist when this function returns.
    """
    return user_data_dir() / "data.db"


DB_PATH = get_db_path()
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def init_db():
    from app import models  # noqa: F401  ensures all tables are registered first
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
