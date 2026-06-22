"""
Audit Log page. TODO for Phase 4: ui.aggrid bound to
audit_service.query_logs(), with filter inputs (actor, action, date range)
and a CSV export button.
"""
from nicegui import ui


def register(build_nav):
    @ui.page("/audit-log")
    def page():
        build_nav()
        ui.label("Audit Log").classes("text-h6")
        ui.label("TODO: filterable table via audit_service.query_logs() + CSV export.")
