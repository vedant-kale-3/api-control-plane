"""
Dummy instrumented microservice used only for local dev/testing of the live
tracing feature (Phase 3). Not part of the shipped product.

Run with: python -m sample_service.main
"""
from fastapi import FastAPI
import uuid
import time

app = FastAPI(title="Sample Instrumented Service")


@app.get("/ping")
def ping():
    # TODO: replace with real OpenTelemetry span emission once
    # app/tracing/otel_setup.py's receiver endpoint exists.
    span = {
        "trace_id": str(uuid.uuid4()),
        "span_id": str(uuid.uuid4()),
        "name": "GET /ping",
        "start_time": time.time(),
    }
    return {"status": "ok", "span": span}
