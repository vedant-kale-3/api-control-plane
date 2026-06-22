"""
OpenTelemetry wiring. For v1, the sample_service emits spans to this
process's trace_service.ingest_span() via a simple internal endpoint —
swap for a real OTLP receiver if instrumented services run out-of-process
on the network. See ARCHITECTURE.md section 2.6.
"""
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

_provider = TracerProvider()
trace.set_tracer_provider(_provider)


def get_tracer(name: str):
    return trace.get_tracer(name)
