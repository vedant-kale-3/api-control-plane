"""
RBAC Admin page — Phase 4 implementation.

Shows all roles and their granted permissions, provides a form to create a
new role, and a form to grant a (resource, action) to an existing role.
A confirmation dialog is shown before every permission grant, because
granting a permission can escalate a role's access (ARCHITECTURE.md §2.3).

Conventions (ARCHITECTURE.md §2.7):
  - Imports only from app.services — never from app.models.
  - The actor_role defaults to "admin" until real JWT session resolution is
    wired in (Phase 1 / ARCHITECTURE.md §2.4 TODO).
  - No permission-check logic lives here; every privileged call goes through
    rbac_service which enforces zero-trust gates internally.
"""
from nicegui import ui
from app.services import rbac_service
from app.services.rbac_service import PermissionDenied


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _build_role_permission_rows(actor_role: str) -> list[dict]:
    """Return a flat list of {role, role_id, perm_id, resource, action} dicts.

    One row per (role, permission) pair.  Roles that have no permissions yet
    still appear as a single row with resource/action set to sentinel "—" so
    they are visible in the table.
    """
    try:
        roles = rbac_service.list_roles(actor_role)
    except PermissionDenied:
        return []

    rows: list[dict] = []
    for role in roles:
        try:
            perms = rbac_service.list_permissions(actor_role, target_role_name=role.name)
        except (PermissionDenied, ValueError):
            perms = []

        if perms:
            for perm in perms:
                rows.append({
                    "role":     role.name,
                    "role_id":  role.id,
                    "perm_id":  perm.id,
                    "resource": perm.resource,
                    "action":   perm.action,
                })
        else:
            # Role exists but has no permissions — show it so the admin is
            # aware (a role with no permissions fails closed on every gate).
            rows.append({
                "role":     role.name,
                "role_id":  role.id,
                "perm_id":  None,
                "resource": "—",
                "action":   "—",
            })

    return rows


# ---------------------------------------------------------------------------
# Page registration
# ---------------------------------------------------------------------------

def register(build_nav):

    @ui.page("/rbac-admin")
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
                ui.label("RBAC Admin").classes("text-h5 font-bold")
                ui.label(
                    "Manage roles and their granted permissions. "
                    "Every permission grant requires confirmation — "
                    "incorrect grants can escalate access (ARCHITECTURE.md §2.3)."
                ).classes("text-sm text-grey-6")

        ui.separator()

        # ════════════════════════════════════════════════════════════════════
        # Role × Permission table
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-4 shadow-sm"):
            with ui.row().classes("items-center justify-between w-full mb-2"):
                ui.label("Roles & Permissions").classes("text-subtitle1 font-medium")
                ui.label(
                    "Roles with no permissions fail closed on every access check."
                ).classes("text-xs text-grey-5 italic")

            initial_rows = _build_role_permission_rows(actor_role)

            # NOTE: domLayout:"autoHeight" is intentionally NOT used here.
            # Combining autoHeight with pagination is a known AG Grid issue —
            # the grid never establishes a correct containing height, which
            # causes the next sibling element (Refresh button) and the card
            # below to render on top of the grid rows (the overlap bug).
            # Using the default "normal" layout with an explicit style height
            # keeps everything in correct document flow.
            grid = ui.aggrid({
                "columnDefs": [
                    {
                        "field": "role",
                        "headerName": "Role",
                        "width": 160,
                        "pinned": "left",
                        "sortable": True,
                        "filter": True,
                        # Highlight dangerous elevated roles.
                        "cellStyle": {
                            "function": (
                                "params.value === 'admin' "
                                "? {color: '#b45309', fontWeight: 'bold'} "
                                ": {fontWeight: '500'}"
                            )
                        },
                    },
                    {
                        "field": "resource",
                        "headerName": "Resource",
                        "flex": 1,
                        "filter": True,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "params.value === '—' "
                                "? {color: '#9ca3af', fontStyle: 'italic'} "
                                ": {}"
                            )
                        },
                    },
                    {
                        "field": "action",
                        "headerName": "Action",
                        "flex": 1,
                        "filter": True,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "params.value === '—' "
                                "? {color: '#9ca3af', fontStyle: 'italic'} "
                                ": params.value === 'manage' "
                                "? {color: '#b91c1c', fontWeight: 'bold'} "
                                ": {}"
                            )
                        },
                    },
                    {
                        "field": "perm_id",
                        "headerName": "Perm ID",
                        "width": 95,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "params.value == null "
                                "? {color: '#9ca3af', fontStyle: 'italic'} "
                                ": {}"
                            )
                        },
                        "valueFormatter": {
                            "function": "params.value == null ? '—' : params.value"
                        },
                    },
                ],
                "rowData": initial_rows,
                # No rowSelection — table is for display only; mutations go
                # through the dedicated forms below.
                "defaultColDef": {"resizable": True},
                "pagination": True,
                "paginationPageSize": 30,
            }).classes("w-full").style("height: 420px;")

            # ── Refresh button inside the card so it sits in normal block flow
            # ── and cannot overlap the grid above or the section below.
            ui.row().classes("mt-3")  # spacer between grid and button
            with ui.row().classes("mt-1"):
                ui.button("Refresh", icon="refresh", on_click=lambda: _refresh_grid()).props(
                    "outline color=grey-7"
                )

        # ── Refresh helper (shared by both forms) ────────────────────────────
        def _refresh_grid():
            grid.options["rowData"] = _build_role_permission_rows(actor_role)
            grid.update()

        # ════════════════════════════════════════════════════════════════════
        # Add-role form
        # ════════════════════════════════════════════════════════════════════
        ui.separator().classes("my-6")

        with ui.card().classes("w-full shadow-sm"):
            ui.label("Add New Role").classes("text-subtitle1 font-medium mb-1")
            ui.label(
                "A new role starts with zero permissions and fails closed on "
                "every access check until permissions are explicitly granted below."
            ).classes("text-xs text-grey-6 mb-4")

            with ui.row().classes("items-end gap-4 w-full"):
                new_role_input = ui.input(
                    "Role name *",
                    placeholder="e.g. qa-engineer",
                    validation={"Required": lambda v: bool(v and v.strip())},
                ).classes("flex-1")

                def _add_role():
                    name = new_role_input.value
                    if not name or not name.strip():
                        ui.notification(
                            "Role name is required.",
                            type="warning",
                            icon="warning",
                        )
                        return
                    try:
                        rbac_service.add_role(actor, actor_role, name.strip())
                    except PermissionDenied as exc:
                        ui.notification(
                            str(exc), type="negative", timeout=0, close_button=True
                        )
                        return
                    except ValueError as exc:
                        ui.notification(
                            str(exc), type="warning", timeout=0, close_button=True
                        )
                        return

                    ui.notification(
                        f"Role '{name.strip()}' created.",
                        type="positive",
                        icon="check_circle",
                    )
                    new_role_input.set_value("")
                    _refresh_grid()

                ui.button("Create Role", icon="group_add", on_click=_add_role).classes(
                    "bg-blue-600 text-white"
                )

        # ════════════════════════════════════════════════════════════════════
        # Grant-permission form  (with confirmation dialog — escalation guard)
        # ════════════════════════════════════════════════════════════════════
        ui.separator().classes("my-6")

        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-start gap-2 mb-1"):
                ui.icon("warning", size="1.4rem").classes("text-amber-500 mt-0.5")
                ui.label("Grant Permission to Role").classes(
                    "text-subtitle1 font-medium"
                )

            with ui.card().classes(
                "w-full bg-amber-50 border border-amber-200 rounded p-3 mb-4"
            ):
                ui.label(
                    "Granting a permission broadens a role's access. "
                    "Review carefully — every grant is logged in the audit trail "
                    "and cannot be undone through this UI (requires a DB migration "
                    "or a future revoke-permission function)."
                ).classes("text-xs text-amber-800")

            with ui.grid(columns=3).classes("w-full gap-4"):
                target_role_input = ui.input(
                    "Target role *",
                    placeholder="e.g. developer",
                    validation={"Required": lambda v: bool(v and v.strip())},
                )
                resource_input = ui.input(
                    "Resource *",
                    placeholder="e.g. api_key",
                    validation={"Required": lambda v: bool(v and v.strip())},
                )
                action_input = ui.input(
                    "Action *",
                    placeholder="e.g. list",
                    validation={"Required": lambda v: bool(v and v.strip())},
                )

            def _validate_grant_inputs() -> tuple[str, str, str] | None:
                """Return (role, resource, action) if all fields are valid,
                otherwise show a notification and return None."""
                role    = (target_role_input.value or "").strip()
                resource = (resource_input.value or "").strip()
                action   = (action_input.value or "").strip()

                missing = [
                    name for name, val in
                    [("Target role", role), ("Resource", resource), ("Action", action)]
                    if not val
                ]
                if missing:
                    ui.notification(
                        f"{', '.join(missing)} {'is' if len(missing)==1 else 'are'} required.",
                        type="warning",
                        icon="warning",
                    )
                    return None
                return role, resource, action

            def _do_grant(role: str, resource: str, action: str):
                """Execute the actual service call after confirmation."""
                try:
                    rbac_service.add_permission(
                        actor, actor_role, role, resource, action
                    )
                except PermissionDenied as exc:
                    ui.notification(
                        str(exc), type="negative", timeout=0, close_button=True
                    )
                    return
                except ValueError as exc:
                    ui.notification(
                        str(exc), type="warning", timeout=0, close_button=True
                    )
                    return

                ui.notification(
                    f"Granted '{resource}:{action}' to role '{role}'.",
                    type="positive",
                    icon="check_circle",
                )
                target_role_input.set_value("")
                resource_input.set_value("")
                action_input.set_value("")
                _refresh_grid()

            def _show_confirm_dialog(role: str, resource: str, action: str):
                """Render a blocking confirmation dialog before committing the
                permission grant.  The user must explicitly click 'Confirm Grant'
                — pressing Cancel or dismissing the dialog is a safe no-op."""
                with ui.dialog(value=True) as dlg, ui.card().classes(
                    "w-full max-w-lg gap-4 p-6"
                ):
                    # ── Header ───────────────────────────────────────────────
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.icon("security", size="2rem").classes("text-amber-500")
                        ui.label("Confirm Permission Grant").classes(
                            "text-h6 font-bold"
                        )

                    # ── Summary of what will change ──────────────────────────
                    with ui.card().classes(
                        "w-full bg-amber-50 border border-amber-300 rounded p-4"
                    ):
                        ui.label(
                            "You are about to grant the following permission:"
                        ).classes("text-sm text-amber-800 font-medium mb-2")

                        with ui.grid(columns=2).classes("gap-x-4 gap-y-1 text-sm"):
                            ui.label("Role:").classes("font-semibold text-grey-8")
                            ui.label(role).classes("font-mono text-blue-700")
                            ui.label("Resource:").classes("font-semibold text-grey-8")
                            ui.label(resource).classes("font-mono text-blue-700")
                            ui.label("Action:").classes("font-semibold text-grey-8")
                            ui.label(action).classes("font-mono text-blue-700")

                    ui.label(
                        "This action is logged in the audit trail. "
                        "Proceed only if you intend to broaden this role's access."
                    ).classes("text-xs text-grey-6 italic")

                    ui.separator()

                    # ── Action buttons ───────────────────────────────────────
                    with ui.row().classes("w-full gap-3"):
                        ui.button(
                            "Cancel",
                            icon="close",
                            on_click=dlg.close,
                        ).props("outline color=grey-7").classes("flex-1")

                        def _confirmed():
                            dlg.close()
                            _do_grant(role, resource, action)

                        ui.button(
                            "Confirm Grant",
                            icon="check_circle",
                            on_click=_confirmed,
                        ).classes("flex-1 bg-amber-600 text-white")

            def _request_grant():
                """Validate inputs, then show the confirmation dialog.
                The actual write only happens if the user confirms."""
                result = _validate_grant_inputs()
                if result is None:
                    return  # validation already showed a notification
                role, resource, action = result
                _show_confirm_dialog(role, resource, action)

            ui.button(
                "Grant Permission…",
                icon="lock_open",
                on_click=_request_grant,
            ).classes("mt-4 bg-amber-600 text-white")
