"""
Dummy instrumented microservice used only for local dev/testing of the live
tracing feature (ARCHITECTURE.md §2.6, Phase 3). Not part of the shipped product.

Run alongside the control plane with: python -m sample_service.main

The /ping endpoint records a span and POSTs it to the control plane's
internal trace ingest endpoint (POST /internal/trace).  If the control
plane is not reachable the call fails gracefully — /ping still returns 200
so the service stays useful as a standalone stub.
"""
import time
import uuid

import httpx
from fastapi import FastAPI

app = FastAPI(title="Sample Instrumented Service")

# Control-plane ingest URL.  Override via environment variable for
# environments where the control plane runs on a non-default port.
import os
_CONTROL_PLANE_TRACE_URL = os.environ.get(
    "CONTROL_PLANE_TRACE_URL",
    "http://127.0.0.1:8000/internal/trace",
)

# Reuse a single httpx client so TCP connections are pooled across requests.
# timeout=2 s keeps /ping snappy even when the control plane is down.
_http = httpx.Client(timeout=2.0)


def _emit_span(span: dict) -> None:
    """POST *span* to the control plane's trace ingest endpoint.

    Failures are caught and logged to stderr so they never propagate back
    to the /ping caller — instrumentation must not break the service under
    observation (ARCHITECTURE.md §2.6).
    """
    try:
        resp = _http.post(_CONTROL_PLANE_TRACE_URL, json=span)
        resp.raise_for_status()
    except Exception as exc:
        # Intentional: print to stderr, don't raise.
        print(f"[sample_service] trace emit failed: {exc}", flush=True)


@app.get("/ping")
def ping():
    """Health-check endpoint that also emits a trace span to the control plane.

    Span fields match the SpanPayload schema in app/api/routes.py so the
    control plane can ingest them without coercion.
    """
    t_start = time.time()

    # --- simulate work ---
    # (nothing here — it's a stub; real services would do real work between
    #  start_time and end_time recording)

    t_end = time.time()

    span = {
        "trace_id":      str(uuid.uuid4()),
        "span_id":       str(uuid.uuid4()),
        "parent_span_id": None,
        "name":          "GET /ping",
        "service_id":    0,           # sentinel: "sample service"
        "start_time":    t_start,
        "end_time":      t_end,
        "attributes":    {
            "http.method": "GET",
            "http.route":  "/ping",
            "duration_ms": round((t_end - t_start) * 1000, 3),
        },
    }

    _emit_span(span)

    return {"status": "ok", "trace_id": span["trace_id"]}
