"""
NiceGUI native-mode entry point. reload=False is important for the packaged
exe — NiceGUI's auto-reload is dev-only (ARCHITECTURE.md section 2.7).
"""
from nicegui import ui
from app.gui.pages import keys, rate_limits, audit_log, rbac_admin, live_trace


def build_nav():
    with ui.left_drawer():
        ui.link("Keys", "/keys")
        ui.link("Rate Limits", "/rate-limits")
        ui.link("Audit Log", "/audit-log")
        ui.link("RBAC Admin", "/rbac-admin")
        ui.link("Live Trace", "/live-trace")


@ui.page("/")
def index():
    build_nav()
    ui.label("API Control Plane").classes("text-h5")
    ui.label("Select a section from the left.")


def register_pages():
    keys.register(build_nav)
    rate_limits.register(build_nav)
    audit_log.register(build_nav)
    rbac_admin.register(build_nav)
    live_trace.register(build_nav)


def run(port: int = 8080) -> None:
    register_pages()
    ui.run(
        native=True,
        window_size=(1280, 800),
        title="API Control Plane",
        reload=False,
        dark=True,
        host="127.0.0.1",  # never expose on 0.0.0.0; loopback only
        port=port,         # free port selected by main._find_free_port()
    )
