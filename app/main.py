"""
Process entry point: initialises the DB, seeds default RBAC roles, acquires a
single-instance lock so duplicate launches are prevented, mounts the external
FastAPI routes onto the same app NiceGUI uses, selects a free loopback port,
then starts the native GUI window.

Run with: python -m app.main
"""
import sys
import ctypes
import socket

from app.db import init_db
from app.services.rbac_service import seed_default_roles
from app.api.routes import router as api_router
from nicegui import app as nicegui_app
from app.gui.main import run

# Named mutex used to enforce a single running instance.  The "Local\" prefix
# keeps it in the current user's session namespace (works under UAC / Fast
# User Switching).  The OS releases the mutex automatically when the process
# exits — no explicit CloseHandle call is needed in normal flow.
_MUTEX_NAME = "Local\\APIControlPlane_SingleInstance"
_mutex_handle = None  # module-level reference keeps the handle alive


def _acquire_single_instance_lock() -> None:
    """Create a named Windows mutex.

    If ``ERROR_ALREADY_EXISTS`` (183) is returned by ``GetLastError()``,
    another instance is already running.  Show an informational message box
    and exit with code 0 so the user gets clear feedback without a crash
    dialog or traceback.
    """
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0,
            "API Control Plane is already running.\n\n"
            "Check the taskbar or system tray for the existing window.",
            "Already Running",
            0x40,  # MB_ICONINFORMATION
        )
        sys.exit(0)


def _find_free_port() -> int:
    """Bind to 127.0.0.1:0, let the OS assign an ephemeral port, return it.

    The socket is closed immediately after reading the port number.  Because
    only one instance of the app can run (enforced by the mutex above), there
    is no meaningful risk that the port will be claimed by another process
    between this call and NiceGUI binding to it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bootstrap() -> None:
    _acquire_single_instance_lock()
    init_db()
    seed_default_roles()
    nicegui_app.include_router(api_router)


if __name__ == "__main__":
    bootstrap()
    run(port=_find_free_port())
