"""
Rate Limits page — Phase 4 implementation.

Wires ui.aggrid to rate_limit_service.list_policies(), shows a live
consumption indicator next to each row (polled via ui.timer), and provides
a form to call rate_limit_service.create_policy().

Conventions (ARCHITECTURE.md section 2.7):
  - Imports only from app.services, never from app.models.
  - GUI event handlers are thin: call service functions, format results.
  - No business logic lives here.
  - actor_role defaults to "admin" until real JWT session resolution is wired
    in (Phase 1 / ARCHITECTURE.md section 2.4 TODO).
"""
from nicegui import ui
from app.services import rate_limit_service
from app.services.rbac_service import PermissionDenied


# ---------------------------------------------------------------------------
# Serialisation helper — converts a RateLimitPolicy to a plain dict for aggrid.
# ---------------------------------------------------------------------------
def _row(policy) -> dict:
    return {
        "id":             policy.id,
        "service_id":     policy.service_id,
        "key_id":         policy.key_id if policy.key_id is not None else "—",
        "limit":          policy.limit,
        "window_seconds": policy.window_seconds,
        "algorithm":      policy.algorithm,
        # Consumption is injected live by the timer; start at "…" while loading.
        "consumption":    "…",
    }


# ---------------------------------------------------------------------------
# Page registration
# ---------------------------------------------------------------------------
def register(build_nav):

    @ui.page("/rate-limits")
    def page():
        # ── TODO: replace with real JWT session lookup (Phase 1) ─────────────
        actor_role: str = "admin"

        build_nav()

        # ════════════════════════════════════════════════════════════════════
        # Page header
        # ════════════════════════════════════════════════════════════════════
        with ui.row().classes("items-center justify-between w-full mb-2"):
            with ui.column().classes("gap-0"):
                ui.label("Rate Limit Policies").classes("text-h5 font-bold")
                ui.label(
                    "Define per-service and per-key request-rate policies. "
                    "Consumption updates live every 5 seconds."
                ).classes("text-sm text-grey-6")

        ui.separator()

        # ════════════════════════════════════════════════════════════════════
        # Policies table + live consumption indicator
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-4 shadow-sm"):

            with ui.row().classes("items-center justify-between w-full mb-2"):
                ui.label("Active Policies").classes("text-subtitle1 font-medium")
                # Live-update badge — pulses to indicate the timer is running.
                with ui.row().classes("items-center gap-1"):
                    live_dot = ui.icon("circle", size="0.6rem").classes(
                        "text-green-500"
                    )
                    ui.label("Live").classes("text-xs text-grey-6")

            # Initial data load — surface PermissionDenied gracefully.
            try:
                initial_policies = rate_limit_service.list_policies()
                initial_rows = [_row(p) for p in initial_policies]
            except PermissionDenied as exc:
                ui.notification(str(exc), type="negative", timeout=0, close_button=True)
                initial_rows = []

            grid = ui.aggrid({
                "columnDefs": [
                    {
                        "field": "id",
                        "headerName": "ID",
                        "width": 70,
                        "pinned": "left",
                    },
                    {
                        "field": "service_id",
                        "headerName": "Service",
                        "width": 90,
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "key_id",
                        "headerName": "Key (scope)",
                        "width": 120,
                        "filter": True,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "params.value === '—' "
                                "? {color: '#6b7280', fontStyle: 'italic'} "
                                ": {}"
                            )
                        },
                    },
                    {
                        "field": "limit",
                        "headerName": "Limit",
                        "width": 90,
                        "sortable": True,
                    },
                    {
                        "field": "window_seconds",
                        "headerName": "Window (s)",
                        "width": 110,
                        "sortable": True,
                    },
                    {
                        "field": "algorithm",
                        "headerName": "Algorithm",
                        "flex": 1,
                        "filter": True,
                    },
                    {
                        # Live consumption column — colour-coded by load.
                        "field": "consumption",
                        "headerName": "Hits (current window)",
                        "width": 175,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "(() => {"
                                "  const v = params.value;"
                                "  if (v === '…' || v === 0) return {color: '#6b7280'};"
                                "  const pct = v / (params.data.limit || 1);"
                                "  if (pct >= 0.9) return {color: '#b91c1c', fontWeight: 'bold'};"
                                "  if (pct >= 0.6) return {color: '#d97706', fontWeight: 'bold'};"
                                "  return {color: '#15803d', fontWeight: 'bold'};"
                                "})()"
                            )
                        },
                    },
                ],
                "rowData": initial_rows,
                "rowSelection": "single",
                "defaultColDef": {
                    "resizable": True,
                },
                "domLayout": "autoHeight",
                "pagination": True,
                "paginationPageSize": 20,
            }).classes("w-full")

        # ── Grid refresh helper ──────────────────────────────────────────────
        def _refresh_grid():
            """Reload policies from the service and push to the grid."""
            try:
                policies = rate_limit_service.list_policies()
                grid.options["rowData"] = [_row(p) for p in policies]
                grid.update()
            except PermissionDenied as exc:
                ui.notification(str(exc), type="negative", timeout=0, close_button=True)

        # ── Live consumption update ──────────────────────────────────────────
        def _update_consumption():
            """
            Pull a snapshot from the service-layer helper (never touches
            _counters directly — ARCHITECTURE.md §2.7) and patch the
            'consumption' field in each grid row in-place, then push the
            update.  This keeps the round-trip minimal: no DB query,
            just the in-memory dict.
            """
            rows: list[dict] = grid.options.get("rowData", [])
            if not rows:
                return

            # Determine the narrowest window across visible policies so the
            # snapshot is as accurate as possible.  Falls back to 60 s if
            # the list is empty.
            windows = [r["window_seconds"] for r in rows if isinstance(r.get("window_seconds"), int)]
            min_window = min(windows) if windows else 60

            snapshot = rate_limit_service.get_consumption_snapshot(
                window_seconds=min_window
            )

            changed = False
            for row in rows:
                # key_id may be "—" (string sentinel) when the policy applies
                # to all keys for a service — no per-key counter exists then.
                raw_key_id = row.get("key_id")
                if isinstance(raw_key_id, int):
                    hits = snapshot.get(raw_key_id, 0)
                else:
                    hits = 0
                new_val = hits if hits > 0 else 0
                if row.get("consumption") != new_val:
                    row["consumption"] = new_val
                    changed = True

            if changed:
                grid.update()

        # Pulse the live dot and refresh consumption on every tick.
        _pulse_state = {"on": True}

        def _tick():
            _update_consumption()
            # Simple blink on the dot so the user can see the timer is alive.
            _pulse_state["on"] = not _pulse_state["on"]
            live_dot.classes(
                "text-green-500" if _pulse_state["on"] else "text-green-200",
                remove="text-green-200 text-green-500",
            )

        ui.timer(5.0, _tick)

        # ── Manual refresh button ────────────────────────────────────────────
        with ui.row().classes("mt-2 gap-2"):
            ui.button("Refresh", icon="refresh", on_click=_refresh_grid).props(
                "outline color=grey-7"
            )

        # ════════════════════════════════════════════════════════════════════
        # Create policy form
        # ════════════════════════════════════════════════════════════════════
        ui.separator().classes("my-6")

        with ui.card().classes("w-full shadow-sm"):
            ui.label("Create New Policy").classes("text-subtitle1 font-medium mb-1")
            ui.label(
                "Leave Key ID blank to apply the policy to all keys for the given service."
            ).classes("text-xs text-grey-6 mb-4")

            with ui.grid(columns=4).classes("w-full gap-4"):
                svc_id_input = ui.number(
                    "Service ID *",
                    min=1,
                    step=1,
                    placeholder="e.g. 1",
                    validation={"Required": lambda v: v is not None},
                )
                limit_input = ui.number(
                    "Limit (requests) *",
                    min=1,
                    step=1,
                    placeholder="e.g. 100",
                    validation={"Required": lambda v: v is not None},
                )
                window_input = ui.number(
                    "Window (seconds) *",
                    min=1,
                    step=1,
                    value=60,
                    placeholder="e.g. 60",
                    validation={"Required": lambda v: v is not None},
                )
                key_id_input = ui.number(
                    "Key ID (optional)",
                    min=1,
                    step=1,
                    placeholder="leave blank for all keys",
                )

            def _create():
                service_id = svc_id_input.value
                limit = limit_input.value
                window_seconds = window_input.value
                key_id = key_id_input.value

                # ── Validation ───────────────────────────────────────────────
                if service_id is None:
                    ui.notification("Service ID is required.", type="warning", icon="warning")
                    return
                if limit is None or limit < 1:
                    ui.notification("Limit must be ≥ 1.", type="warning", icon="warning")
                    return
                if window_seconds is None or window_seconds < 1:
                    ui.notification("Window must be ≥ 1 second.", type="warning", icon="warning")
                    return

                # ── Service call ─────────────────────────────────────────────
                try:
                    rate_limit_service.create_policy(
                        actor_role=actor_role,
                        service_id=int(service_id),
                        limit=int(limit),
                        window_seconds=int(window_seconds),
                        key_id=int(key_id) if key_id is not None else None,
                    )
                except PermissionDenied as exc:
                    ui.notification(str(exc), type="negative", timeout=0, close_button=True)
                    return
                except Exception as exc:
                    ui.notification(
                        f"Failed to create policy: {exc}",
                        type="negative",
                        timeout=0,
                        close_button=True,
                    )
                    return

                ui.notification(
                    "Policy created successfully.",
                    type="positive",
                    icon="check_circle",
                )

                # ── Reset form ───────────────────────────────────────────────
                svc_id_input.set_value(None)
                limit_input.set_value(None)
                window_input.set_value(60)
                key_id_input.set_value(None)

                # ── Refresh table ────────────────────────────────────────────
                _refresh_grid()

            ui.button("Create Policy", icon="add_circle", on_click=_create).classes(
                "mt-4 bg-blue-600 text-white"
            )
