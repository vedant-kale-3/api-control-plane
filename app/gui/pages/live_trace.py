"""
Live Trace page — Phase 3/4 implementation (ARCHITECTURE.md §2.6).

On page load:
  1. Calls trace_service.get_recent() to pre-populate the table so the
     view is not blank before any new spans arrive.
  2. Calls trace_service.subscribe() to obtain a per-page asyncio.Queue
     that receives every span pushed by trace_service.ingest_span().
  3. A ui.timer(1.0) fires every second, drains all queued spans with
     get_nowait(), prepends them to the table rows, and caps the display
     at MAX_ROWS so the DOM never grows unbounded.
  4. Calls trace_service.unsubscribe() when the page client disconnects so
     orphaned queues don't accumulate in _subscribers.

Conventions (ARCHITECTURE.md §2.7):
  - Imports only from app.services — never from app.models.
  - No business logic here; all state lives in trace_service.
"""
import asyncio
import time
from datetime import datetime

from nicegui import ui, app as nicegui_app
from app.services import trace_service

# Maximum rows shown in the live table.  Older rows are dropped from the
# bottom when this limit is exceeded to keep the DOM lean.
MAX_ROWS = 200


# ---------------------------------------------------------------------------
# Span → display-row serialiser
# ---------------------------------------------------------------------------

def _fmt_time(ts: float | None) -> str:
    """Convert a Unix timestamp float to a human-readable UTC string.
    Returns '—' for None / zero values."""
    if not ts:
        return "—"
    try:
        return datetime.utcfromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    except (OSError, ValueError, OverflowError):
        return str(ts)


def _fmt_duration(span: dict) -> str:
    """Return latency in ms if both start_time and end_time are present."""
    s = span.get("start_time")
    e = span.get("end_time")
    if s and e:
        return f"{(e - s) * 1000:.1f} ms"
    return "—"


def _span_to_row(span: dict) -> dict:
    attrs = span.get("attributes") or {}
    return {
        "trace_id":   (span.get("trace_id") or "")[:16] + "…",  # truncate for display
        "span_id":    (span.get("span_id") or "")[:12] + "…",
        "name":       span.get("name") or "—",
        "service_id": span.get("service_id", "—"),
        "start":      _fmt_time(span.get("start_time")),
        "duration":   _fmt_duration(span),
        "attrs":      str(attrs) if attrs else "{}",
        # Keep raw trace_id for tooltip / full-copy use
        "_trace_id_full": span.get("trace_id", ""),
    }


# ---------------------------------------------------------------------------
# Page registration
# ---------------------------------------------------------------------------

def register(build_nav):

    @ui.page("/live-trace")
    def page():
        build_nav()

        # ════════════════════════════════════════════════════════════════════
        # Page header
        # ════════════════════════════════════════════════════════════════════
        with ui.row().classes("items-center justify-between w-full mb-2"):
            with ui.column().classes("gap-0"):
                ui.label("Live Request Trace").classes("text-h5 font-bold")
                ui.label(
                    "Real-time view of spans arriving from instrumented services. "
                    "Updates every ~1 second."
                ).classes("text-sm text-grey-6")

        ui.separator()

        # ════════════════════════════════════════════════════════════════════
        # Status bar
        # ════════════════════════════════════════════════════════════════════
        with ui.row().classes("items-center gap-3 mt-3 mb-1"):
            live_dot = ui.icon("circle", size="0.6rem").classes("text-green-400")
            status_label = ui.label("Listening for spans…").classes(
                "text-xs text-grey-6"
            )
            span_count_label = ui.label("").classes("text-xs text-grey-5 italic")

        # ════════════════════════════════════════════════════════════════════
        # Span table
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full mt-2 shadow-sm"):

            columns = [
                {"name": "start",      "label": "Time (UTC)",   "field": "start",      "align": "left",   "sortable": True},
                {"name": "name",       "label": "Span Name",    "field": "name",       "align": "left",   "sortable": True},
                {"name": "service_id", "label": "Service",      "field": "service_id", "align": "center", "sortable": True},
                {"name": "duration",   "label": "Duration",     "field": "duration",   "align": "right",  "sortable": True},
                {"name": "trace_id",   "label": "Trace ID",     "field": "trace_id",   "align": "left"},
                {"name": "span_id",    "label": "Span ID",      "field": "span_id",    "align": "left"},
                {"name": "attrs",      "label": "Attributes",   "field": "attrs",      "align": "left"},
            ]

            # Pre-populate with recent spans so the view is never blank.
            recent = trace_service.get_recent(limit=MAX_ROWS)
            # get_recent returns oldest-first; reverse so newest is at top.
            initial_rows = [_span_to_row(s) for s in reversed(recent)]

            table = ui.table(
                columns=columns,
                rows=initial_rows,
                row_key="trace_id",
                pagination={"rowsPerPage": 50, "sortBy": "start", "descending": True},
            ).classes("w-full").style("height: 480px; overflow-y: auto;")

            # Style the duration column to draw attention to slow spans.
            table.add_slot("body-cell-duration", """
                <q-td :props="props">
                    <span :style="{
                        color: (parseFloat(props.value) > 100)
                            ? '#b91c1c'
                            : (parseFloat(props.value) > 20)
                                ? '#d97706'
                                : '#15803d',
                        fontWeight: 'bold'
                    }">{{ props.value }}</span>
                </q-td>
            """)

        with ui.row().classes("mt-2 gap-2"):
            ui.button(
                "Clear Display",
                icon="delete_sweep",
                on_click=lambda: _clear(),
            ).props("outline color=grey-7")

        # ════════════════════════════════════════════════════════════════════
        # Live subscription — one queue per page-load
        # ════════════════════════════════════════════════════════════════════

        queue: asyncio.Queue = trace_service.subscribe()
        _span_count = {"total": len(initial_rows)}
        _pulse = {"on": True}

        def _clear():
            table.rows.clear()
            table.update()
            _span_count["total"] = 0
            span_count_label.set_text("")

        def _poll():
            """Drain all spans that arrived since the last tick and prepend
            them to the table rows.  Uses get_nowait() in a tight loop so
            every span queued between timer ticks is captured in one go,
            regardless of how many arrived.

            Cap the table at MAX_ROWS by truncating the tail (oldest spans)
            so the DOM stays lean regardless of how long the page is open.
            """
            new_rows: list[dict] = []
            try:
                while True:
                    span = queue.get_nowait()
                    new_rows.append(_span_to_row(span))
            except asyncio.QueueEmpty:
                pass

            if not new_rows:
                # Nothing new — just pulse the dot so the user knows the
                # timer is still running.
                _pulse["on"] = not _pulse["on"]
                live_dot.classes(
                    "text-green-400" if _pulse["on"] else "text-green-100",
                    remove="text-green-400 text-green-100",
                )
                return

            # Prepend newest rows to the front and trim the tail.
            table.rows[:0] = new_rows          # prepend in-place
            del table.rows[MAX_ROWS:]          # enforce cap
            table.update()

            _span_count["total"] += len(new_rows)
            span_count_label.set_text(
                f"{_span_count['total']} span(s) received this session"
            )
            status_label.set_text(
                f"Last span at {datetime.utcnow().strftime('%H:%M:%S')} UTC"
            )
            # Keep dot green and solid when data is flowing.
            live_dot.classes("text-green-400", remove="text-green-100")

        timer = ui.timer(1.0, _poll)

        # ── Cleanup: unsubscribe when the browser tab closes so orphaned
        # ── queues don't accumulate in trace_service._subscribers.
        async def _cleanup():
            timer.cancel()
            trace_service.unsubscribe(queue)

        nicegui_app.on_disconnect(_cleanup)
