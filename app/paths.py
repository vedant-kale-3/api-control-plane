"""
Centralised path helpers for both dev and PyInstaller-frozen execution.

Two functions are provided:

  resource_path(relative)
      Resolves a path to a read-only bundled asset.  In dev mode the base is
      the ``app/`` package directory (next to this file); when frozen by
      PyInstaller the base is ``sys._MEIPASS`` — the temp directory into which
      the bundle is extracted at startup.

  user_data_dir()
      Returns (and creates) the per-user writable data directory.
      Always ``%LOCALAPPDATA%\\APIControlPlane`` — never next to the exe, so
      the app works correctly when installed under Program Files (read-only).

Usage
-----
For bundled read-only assets (icons, default config, …)::

    from app.paths import resource_path
    icon = resource_path("assets/icon.ico")

For writable runtime files (DB, logs, …)::

    from app.paths import user_data_dir
    db_file = user_data_dir() / "data.db"
"""
import os
import sys
from pathlib import Path

APP_NAME = "APIControlPlane"


def resource_path(relative: str) -> Path:
    """Resolve *relative* against the correct base for the current runtime.

    - **Frozen** (``sys.frozen is True``): base = ``sys._MEIPASS`` (the
      PyInstaller temp extraction directory).
    - **Dev** (plain ``python`` invocation): base = the ``app/`` package
      directory (the directory that contains this file).

    The returned ``Path`` is absolute and ready to pass to ``open()`` or
    ``Path.read_bytes()``.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)          # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent       # …/app/
    return base / relative


def user_data_dir() -> Path:
    """Return (and create) the per-user writable data directory.

    Resolves to ``%LOCALAPPDATA%\\APIControlPlane`` on Windows, falling back
    to ``~/.APIControlPlane`` on other platforms.  The directory is created
    (including any missing parents) before the path is returned.
    """
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base
