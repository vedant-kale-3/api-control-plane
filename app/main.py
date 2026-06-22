"""
Process entry point: initializes the DB, seeds default RBAC roles, mounts the
external FastAPI routes onto the same app NiceGUI uses, then starts the
native GUI window. Run with: python -m app.main
"""
from app.db import init_db
from app.services.rbac_service import seed_default_roles
from app.api.routes import router as api_router
from nicegui import app as nicegui_app
from app.gui.main import run


def bootstrap():
    init_db()
    seed_default_roles()
    nicegui_app.include_router(api_router)


if __name__ == "__main__":
    bootstrap()
    run()
