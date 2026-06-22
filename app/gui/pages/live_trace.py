"""
Live Trace page. TODO for Phase 3/4: subscribe to trace_service.subscribe(),
render incoming spans via ui.timer polling the queue, and show
trace_service.get_recent() on initial page load.
"""
from nicegui import ui


def register(build_nav):
    @ui.page("/live-trace")
    def page():
        build_nav()
        ui.label("Live Trace").classes("text-h6")
        ui.label("TODO: subscribe to trace_service feed and render live spans.")
