"""
Audit Log page — Phase 4 implementation.

Wires ui.aggrid to audit_service.query_logs() with filter inputs for
actor, action, and a date range.  Includes a "Export CSV" button that
writes the currently displayed rows to a temporary CSV file using Python's
standard csv module (no extra dependency) and serves it via ui.download.

Design rules (ARCHITECTURE.md §2.7, PRD §5.3):
  - Imports only from app.services — never from app.models.
  - The table is strictly READ-ONLY.  AuditLogEntry is append-only by
    design (ARCHITECTURE.md §2.2); there is intentionally no edit or
    delete affordance anywhere on this page.
  - No business logic lives here — all queries go through audit_service.
"""
import csv
import io
import tempfile
import os
from datetime import datetime, date

from nicegui import ui
from app.services import audit_service


# ---------------------------------------------------------------------------
# CSV field order — determines both aggrid column order and export columns.
# ---------------------------------------------------------------------------
_FIELDS = ["id", "timestamp", "actor", "action", "target_type", "target_id", "details"]


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------
def _row(entry) -> dict:
    return {
        "id":          entry.id,
        "timestamp":   (
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            if entry.timestamp else "—"
        ),
        "actor":       entry.actor,
        "action":      entry.action,
        "target_type": entry.target_type,
        "target_id":   entry.target_id,
        "details":     entry.details or "{}",
    }


# ---------------------------------------------------------------------------
# CSV export helper — stdlib only, no pandas/openpyxl
# ---------------------------------------------------------------------------
def _build_csv(rows: list[dict]) -> str:
    """Serialise *rows* to a CSV string and return the content as a str.

    Uses io.StringIO so the whole operation is in-memory; the caller is
    responsible for writing to a temp file if ui.download needs a path.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Page registration
# ---------------------------------------------------------------------------
def register(build_nav):

    @ui.page("/audit-log")
    def page():
        build_nav()

        # ════════════════════════════════════════════════════════════════════
        # Page header
        # ════════════════════════════════════════════════════════════════════
        with ui.row().classes("items-center justify-between w-full mb-2"):
            with ui.column().classes("gap-0"):
                ui.label("Audit Log").classes("text-h5 font-bold")
                ui.label(
                    "Immutable record of every privileged action. "
                    "Read-only — no entry can be edited or deleted (PRD §5.3, ARCHITECTURE §2.2)."
                ).classes("text-sm text-grey-6")

        ui.separator()

        # ════════════════════════════════════════════════════════════════════
        # Filter bar
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-4 shadow-sm"):
            ui.label("Filters").classes("text-subtitle2 font-medium mb-2")

            with ui.row().classes("items-end gap-4 w-full flex-wrap"):
                actor_input = ui.input(
                    "Actor",
                    placeholder="exact match, e.g. gui-user",
                ).classes("flex-1 min-w-40")

                action_input = ui.input(
                    "Action",
                    placeholder="exact match, e.g. add_role",
                ).classes("flex-1 min-w-40")

                date_from_input = ui.input(
                    "From date (YYYY-MM-DD)",
                    placeholder="e.g. 2024-01-01",
                ).classes("flex-1 min-w-44")

                date_to_input = ui.input(
                    "To date (YYYY-MM-DD, exclusive)",
                    placeholder="e.g. 2024-12-31",
                ).classes("flex-1 min-w-44")

                limit_input = ui.number(
                    "Row limit",
                    value=200,
                    min=1,
                    max=5000,
                    step=100,
                ).classes("w-32")

            with ui.row().classes("gap-2 mt-3"):
                ui.button("Apply Filters", icon="search", on_click=lambda: _apply()).classes(
                    "bg-blue-600 text-white"
                )
                ui.button("Clear", icon="clear", on_click=lambda: _clear()).props(
                    "outline color=grey-7"
                )

        # ════════════════════════════════════════════════════════════════════
        # Audit log table — READ-ONLY
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-4 shadow-sm"):
            with ui.row().classes("items-center justify-between w-full mb-2"):
                ui.label("Log Entries").classes("text-subtitle1 font-medium")
                row_count_label = ui.label("").classes("text-xs text-grey-5 italic")

            try:
                initial_entries = audit_service.query_logs(limit=200)
                initial_rows = [_row(e) for e in initial_entries]
            except Exception as exc:
                ui.notification(
                    f"Failed to load audit log: {exc}",
                    type="negative",
                    timeout=0,
                    close_button=True,
                )
                initial_rows = []

            row_count_label.set_text(f"{len(initial_rows)} entries")

            grid = ui.aggrid({
                "columnDefs": [
                    {
                        "field": "id",
                        "headerName": "ID",
                        "width": 70,
                        "pinned": "left",
                        "sortable": True,
                    },
                    {
                        "field": "timestamp",
                        "headerName": "Timestamp (UTC)",
                        "width": 175,
                        "sortable": True,
                        "sort": "desc",
                    },
                    {
                        "field": "actor",
                        "headerName": "Actor",
                        "flex": 1,
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "action",
                        "headerName": "Action",
                        "flex": 1,
                        "filter": True,
                        "sortable": True,
                        "cellStyle": {
                            "function": (
                                "{"
                                "  const danger = ['revoke_key','delete','remove'];"
                                "  return danger.some(w => params.value && params.value.includes(w))"
                                "    ? {color: '#b91c1c', fontWeight: 'bold'}"
                                "    : {};"
                                "}"
                            )
                        },
                    },
                    {
                        "field": "target_type",
                        "headerName": "Target Type",
                        "width": 130,
                        "filter": True,
                        "sortable": True,
                    },
                    {
                        "field": "target_id",
                        "headerName": "Target ID",
                        "width": 110,
                        "sortable": True,
                    },
                    {
                        "field": "details",
                        "headerName": "Details (JSON)",
                        "flex": 2,
                        # Show raw JSON; cell wraps so long payloads don't
                        # truncate silently — important for compliance review.
                        "wrapText": True,
                        "autoHeight": True,
                        "cellStyle": {
                            "fontFamily": "monospace",
                            "fontSize": "0.78rem",
                            "color": "#374151",
                        },
                    },
                ],
                "rowData": initial_rows,
                # No rowSelection — deliberately omitted so users cannot
                # select rows, reinforcing the read-only nature of this page.
                "defaultColDef": {
                    "resizable": True,
                },
                "domLayout": "autoHeight",
                "pagination": True,
                "paginationPageSize": 50,
            }).classes("w-full")

        # ── Export CSV button ────────────────────────────────────────────────
        with ui.row().classes("mt-3 gap-2 items-center"):
            ui.button(
                "Export CSV",
                icon="download",
                on_click=lambda: _export_csv(),
            ).props("outline color=green-7")

            ui.label(
                "Exports the rows currently shown in the table (respects active filters)."
            ).classes("text-xs text-grey-5 italic")

        # ════════════════════════════════════════════════════════════════════
        # Helpers (defined after widgets are created so closures are valid)
        # ════════════════════════════════════════════════════════════════════

        def _parse_date(value: str | None) -> datetime | None:
            """Parse a 'YYYY-MM-DD' string to midnight datetime; return None on
            blank/invalid input so the filter is simply skipped."""
            if not value or not value.strip():
                return None
            try:
                d = date.fromisoformat(value.strip())
                return datetime(d.year, d.month, d.day)
            except ValueError:
                ui.notification(
                    f"Invalid date '{value}' — use YYYY-MM-DD format.",
                    type="warning",
                    icon="warning",
                )
                return None

        def _apply():
            """Re-query the service with the current filter values and
            update the aggrid row data in-place."""
            actor  = actor_input.value.strip()  if actor_input.value  else None
            action = action_input.value.strip() if action_input.value else None
            date_from = _parse_date(date_from_input.value)
            date_to   = _parse_date(date_to_input.value)
            limit  = int(limit_input.value) if limit_input.value else 200

            # Only pass non-empty strings to the service (falsy → None).
            try:
                entries = audit_service.query_logs(
                    actor=actor or None,
                    action=action or None,
                    date_from=date_from,
                    date_to=date_to,
                    limit=limit,
                )
                rows = [_row(e) for e in entries]
            except Exception as exc:
                ui.notification(
                    f"Query failed: {exc}",
                    type="negative",
                    timeout=0,
                    close_button=True,
                )
                return

            grid.options["rowData"] = rows
            grid.update()
            row_count_label.set_text(f"{len(rows)} entries")

        def _clear():
            """Reset all filter inputs and reload the default view."""
            actor_input.set_value("")
            action_input.set_value("")
            date_from_input.set_value("")
            date_to_input.set_value("")
            limit_input.set_value(200)
            _apply()

        def _export_csv():
            """Write the currently displayed rows to a temp CSV file, then
            serve it to the browser via ui.download.

            Uses Python's stdlib csv module only — no extra dependency
            (PRD §5.3 requirement; request constraint).
            """
            rows: list[dict] = grid.options.get("rowData", [])
            if not rows:
                ui.notification(
                    "Nothing to export — the table is empty.",
                    type="warning",
                    icon="info",
                )
                return

            csv_content = _build_csv(rows)

            # Write to a named temp file so ui.download can serve the bytes.
            # delete=False because ui.download reads it asynchronously after
            # this function returns; the OS will reclaim it on process exit.
            suffix = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=f"_audit_log_{suffix}.csv",
                delete=False,
                newline="",
                encoding="utf-8",
            )
            tmp.write(csv_content)
            tmp.flush()
            tmp.close()

            ui.download(tmp.name, filename=f"audit_log_{suffix}.csv")
            ui.notification(
                f"Exporting {len(rows)} rows to CSV…",
                type="positive",
                icon="download",
                timeout=3,
            )
