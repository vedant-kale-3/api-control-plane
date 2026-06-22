"""
Rate Limits page. TODO for Phase 4: list policies via
rate_limit_service.list_policies(), form to create new policies via
rate_limit_service.create_policy(), and a live consumption indicator per key.
"""
from nicegui import ui


def register(build_nav):
    @ui.page("/rate-limits")
    def page():
        build_nav()
        ui.label("Rate Limits").classes("text-h6")
        ui.label("TODO: policy table + creation form + live consumption view.")
