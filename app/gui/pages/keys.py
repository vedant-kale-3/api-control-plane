"""
Keys page — Phase 4 implementation.

Wires ui.aggrid to key_service.list_keys(), provides an issue-key form,
and shows the raw key in a one-time dismissible dialog.

Conventions (ARCHITECTURE.md section 2.7):
  - Imports only from app.services, never from app.models.
  - GUI event handlers are thin: they call service functions and format
    the result; no business logic lives here.
  - actor_role is read from the page-level session state.  The placeholder
    below defaults to "admin" until real JWT session resolution is wired in
    (Phase 1 / ARCHITECTURE.md section 2.4 TODO in routes._resolve_caller).
"""
from nicegui import ui
from app.services import key_service
from app.services.rbac_service import PermissionDenied


# ---------------------------------------------------------------------------
# Serialisation helper — converts a key_service result row to a plain dict
# safe for aggrid rowData.  key_hash is intentionally excluded: we must
# never display the stored hash (ARCHITECTURE.md section 2.2).
# ---------------------------------------------------------------------------
def _row(key) -> dict:
    def _fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"

    return {
        "id":           key.id,
        "owner":        key.owner,
        "service_id":   key.service_id,
        "scopes":       key.scopes or "—",
        "issued_at":    _fmt(key.issued_at),
        "expires_at":   _fmt(key.expires_at),
        "last_used_at": _fmt(key.last_used_at),
        "status":       "REVOKED" if key.revoked_at else "ACTIVE",
    }


# ---------------------------------------------------------------------------
# One-time raw-key dialog
# ---------------------------------------------------------------------------
def _show_raw_key_dialog(raw_key: str) -> None:
    """Renders a modal that displays *raw_key* exactly once.  The dialog
    explains clearly that this is the only time the key will be visible,
    mirrors the ARCHITECTURE.md section 2.2 requirement that keys are
    stored hashed and never retrievable again."""
    with ui.dialog(value=True) as dlg, ui.card().classes("w-full max-w-xl gap-4 p-6"):

        # ── Header ──────────────────────────────────────────────────────────
        with ui.row().classes("items-center gap-2 w-full"):
            ui.icon("key", size="2rem").classes("text-amber-500")
            ui.label("New API Key Created").classes("text-h6 font-bold")

        # ── Warning banner ───────────────────────────────────────────────────
        with ui.card().classes("w-full bg-amber-50 border border-amber-300 rounded p-3"):
            with ui.row().classes("items-start gap-2"):
                ui.icon("warning", size="1.4rem").classes("text-amber-600 mt-0.5")
                ui.label(
                    "This is the only time you will see this key. "
                    "It is stored hashed and cannot be retrieved again. "
                    "Copy it now and store it securely."
                ).classes("text-sm text-amber-800")

        # ── Key display ──────────────────────────────────────────────────────
        ui.label("Raw API Key").classes("text-xs text-grey-6 font-mono")
        key_display = (
            ui.label(raw_key)
            .classes(
                "font-mono text-sm bg-grey-1 border border-grey-3 rounded "
                "p-3 w-full break-all select-all"
            )
        )

        # ── Copy button ──────────────────────────────────────────────────────
        def _copy():
            ui.run_javascript(
                f"navigator.clipboard.writeText({raw_key!r})"
                ".then(() => console.log('key copied'))"
                ".catch(err => console.error('clipboard error:', err))"
            )
            ui.notification(
                "Copied to clipboard!",
                type="positive",
                timeout=2.5,
                icon="content_copy",
            )

        ui.button("Copy to clipboard", icon="content_copy", on_click=_copy).classes(
            "w-full bg-blue-600 text-white"
        )

        ui.separator()

        # ── Dismiss ──────────────────────────────────────────────────────────
        ui.label(
            "Once you dismiss this dialog the key cannot be recovered."
        ).classes("text-xs text-grey-6 italic text-center w-full")

        ui.button(
            "I've saved it — Dismiss",
            icon="check_circle",
            on_click=dlg.close,
        ).classes("w-full bg-green-600 text-white")


# ---------------------------------------------------------------------------
# Page registration
# ---------------------------------------------------------------------------
def register(build_nav):

    @ui.page("/keys")
    def page():
        # ── TODO: replace with real JWT session lookup (Phase 1) ─────────────
        actor_role: str = "admin"
        actor: str = "gui-user"

        build_nav()

        # ════════════════════════════════════════════════════════════════════
        # Page header
        # ════════════════════════════════════════════════════════════════════
        with ui.row().classes("items-center justify-between w-full mb-2"):
            with ui.column().classes("gap-0"):
                ui.label("API Keys").classes("text-h5 font-bold")
                ui.label(
                    "Issue, inspect, and revoke scoped API keys across services."
                ).classes("text-sm text-grey-6")

        ui.separator()

        # ════════════════════════════════════════════════════════════════════
        # Keys table
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-4 shadow-sm"):

            with ui.row().classes("items-center justify-between w-full mb-2"):
                ui.label("Active & Revoked Keys").classes("text-subtitle1 font-medium")
                ui.label(
                    "Select a row to enable the Revoke action."
                ).classes("text-xs text-grey-5 italic")

            # Initial data load — handle PermissionDenied gracefully.
            try:
                initial_keys = key_service.list_keys(actor_role)
                initial_rows = [_row(k) for k in initial_keys]
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
                        "field": "owner",
                        "headerName": "Owner",
                        "flex": 1,
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "service_id",
                        "headerName": "Service",
                        "width": 90,
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "scopes",
                        "headerName": "Scopes",
                        "flex": 1,
                        "filter": True,
                    },
                    {
                        "field": "status",
                        "headerName": "Status",
                        "width": 110,
                        "cellStyle": {
                            "function":
                            "params.value === 'REVOKED' "
                            "? {color: '#b91c1c', fontWeight: 'bold'} "
                            ": {color: '#15803d', fontWeight: 'bold'}"
                        },
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "issued_at",
                        "headerName": "Issued",
                        "width": 150,
                        "sortable": True,
                    },
                    {
                        "field": "expires_at",
                        "headerName": "Expires",
                        "width": 150,
                        "sortable": True,
                    },
                    {
                        "field": "last_used_at",
                        "headerName": "Last Used",
                        "width": 150,
                        "sortable": True,
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

        # ── Refresh helper (used by both issue and revoke) ───────────────────
        def _refresh_grid():
            try:
                keys = key_service.list_keys(actor_role)
                grid.options["rowData"] = [_row(k) for k in keys]
                grid.update()
            except PermissionDenied as exc:
                ui.notification(str(exc), type="negative", timeout=0, close_button=True)

        # ── Revoke selected row ──────────────────────────────────────────────
        async def _revoke_selected():
            row = await grid.get_selected_row()
            if not row:
                ui.notification(
                    "Select a row first, then click Revoke.",
                    type="warning",
                    icon="info",
                )
                return
            key_id: int = row["id"]
            try:
                key_service.revoke_key(actor, actor_role, key_id)
                ui.notification(
                    f"Key #{key_id} revoked.",
                    type="positive",
                    icon="block",
                )
                _refresh_grid()
            except PermissionDenied as exc:
                ui.notification(str(exc), type="negative", timeout=0, close_button=True)
            except ValueError as exc:
                ui.notification(str(exc), type="warning", timeout=0, close_button=True)

        with ui.row().classes("mt-2 gap-2"):
            ui.button("Refresh", icon="refresh", on_click=_refresh_grid).props(
                "outline color=grey-7"
            )
            ui.button("Revoke Selected", icon="block", on_click=_revoke_selected).props(
                "outline color=red-7"
            )

        # ════════════════════════════════════════════════════════════════════
        # Issue-key form
        # ════════════════════════════════════════════════════════════════════
        ui.separator().classes("my-6")

        with ui.card().classes("w-full shadow-sm"):
            ui.label("Issue New API Key").classes("text-subtitle1 font-medium mb-2")
            ui.label(
                "The raw key is shown once at creation time and cannot be retrieved again."
            ).classes("text-xs text-grey-6 mb-4")

            with ui.grid(columns=3).classes("w-full gap-4"):
                svc_id_input = ui.number(
                    "Service ID",
                    min=1,
                    step=1,
                    placeholder="e.g. 1",
                    validation={"Required": lambda v: v is not None},
                )
                owner_input = ui.input(
                    "Owner",
                    placeholder="e.g. ci-pipeline, alice",
                    validation={"Required": lambda v: bool(v and v.strip())},
                )
                expires_input = ui.number(
                    "Expires in days",
                    value=90,
                    min=1,
                    step=1,
                    placeholder="90",
                )

            scopes_input = ui.input(
                "Scopes (optional — comma-separated)",
                placeholder="e.g. read,write",
            ).classes("w-full mt-2")

            def _issue():
                # Validate manually so we can surface a single focused error.
                service_id = svc_id_input.value
                owner = owner_input.value
                expires = expires_input.value

                if not service_id:
                    ui.notification(
                        "Service ID is required.",
                        type="warning",
                        icon="warning",
                    )
                    return
                if not owner or not owner.strip():
                    ui.notification(
                        "Owner is required.",
                        type="warning",
                        icon="warning",
                    )
                    return

                try:
                    raw_key = key_service.issue_key(
                        actor=actor,
                        actor_role=actor_role,
                        service_id=int(service_id),
                        owner=owner.strip(),
                        scopes=scopes_input.value or "",
                        expires_in_days=int(expires) if expires else 90,
                    )
                except PermissionDenied as exc:
                    ui.notification(str(exc), type="negative", timeout=0, close_button=True)
                    return
                except Exception as exc:
                    ui.notification(
                        f"Failed to issue key: {exc}",
                        type="negative",
                        timeout=0,
                        close_button=True,
                    )
                    return

                # Clear the form fields.
                svc_id_input.set_value(None)
                owner_input.set_value("")
                scopes_input.set_value("")
                expires_input.set_value(90)

                # Show raw key in a one-time dialog — this is the ONLY moment
                # the raw value is accessible (ARCHITECTURE.md section 2.2).
                _show_raw_key_dialog(raw_key)

                # Refresh the table so the new row appears.
                _refresh_grid()

            ui.button("Issue Key", icon="add_circle", on_click=_issue).classes(
                "mt-4 bg-blue-600 text-white"
            )
