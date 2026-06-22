"""
RBAC Admin page. TODO for Phase 4: CRUD for Role/Permission, reading and
writing through app.services.rbac_service (add a manage_roles()-style
function there rather than touching app.models from this page directly).
"""
from nicegui import ui


def register(build_nav):
    @ui.page("/rbac-admin")
    def page():
        build_nav()
        ui.label("RBAC Admin").classes("text-h6")
        ui.label("TODO: role/permission management UI.")
