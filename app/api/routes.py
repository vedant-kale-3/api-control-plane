"""
External HTTP surface: CI/CD webhook + key management API. These are thin
wrappers around the service layer — the SAME permission checks apply here
as in the GUI. No "trusted internal caller" shortcut exists
(ARCHITECTURE.md section 2.4, zero-trust).
"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from sqlmodel import select
from app.db import get_session
from app.models.rate_limit import RateLimitPolicy
from app.services import key_service
from app.services import trace_service
from app.services.rbac_service import PermissionDenied
from app.services.rate_limit_service import check_and_increment

router = APIRouter()


def _resolve_caller(api_key: str | None) -> str:
    """TODO: resolve the caller's role from their own scoped API key. Webhook
    callers should use a narrowly-scoped service-account key (e.g. permitted
    to rotate keys for exactly one service), never a shared admin secret.
    Replace this placeholder once ApiKey -> Role resolution is implemented."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    return "service-account-role"


def _get_policy_for_service(service_id: int, key_id: int | None = None) -> RateLimitPolicy | None:
    """Return the most-specific RateLimitPolicy that applies to this request.

    Lookup order (most-specific wins, per ARCHITECTURE.md §2.5 model):
      1. Policy scoped to this exact (service_id, key_id) pair — if key_id is known.
      2. Policy scoped to the service with key_id IS NULL (applies to all keys).
      3. None — no policy configured; caller should allow the request through.
    """
    with get_session() as session:
        # Key-specific policy takes priority when we have a real key_id.
        if key_id is not None:
            policy = session.exec(
                select(RateLimitPolicy).where(
                    RateLimitPolicy.service_id == service_id,
                    RateLimitPolicy.key_id == key_id,
                )
            ).first()
            if policy:
                return policy

        # Fall back to the service-wide policy (key_id IS NULL).
        return session.exec(
            select(RateLimitPolicy).where(
                RateLimitPolicy.service_id == service_id,
                RateLimitPolicy.key_id == None,  # noqa: E711 — SQLModel requires `== None`
            )
        ).first()


@router.post("/webhook/rotate-key")
def rotate_key(service_id: int, owner: str, x_api_key: str | None = Header(default=None)):
    actor_role = _resolve_caller(x_api_key)

    # --- Rate-limit enforcement (ARCHITECTURE.md §2.5) ---
    # Use service_id as the counter bucket when we don't yet have a real key_id
    # (the _resolve_caller stub hasn't been fleshed out yet).  Once
    # ApiKey → Role resolution is in place, pass the resolved key_id instead
    # so per-key policies are honoured over the service-wide fallback.
    policy = _get_policy_for_service(service_id, key_id=None)
    if policy is not None:
        allowed = check_and_increment(
            # Negate service_id to use a distinct bucket namespace from real
            # key IDs (which are positive ints from the DB autoincrement).
            key_id=-service_id,
            policy=policy,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for service {service_id}. "
                       f"Limit: {policy.limit} requests per {policy.window_seconds}s window.",
            )

    try:
        new_raw_key = key_service.issue_key("ci-cd-webhook", actor_role, service_id, owner)
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"key": new_raw_key}


@router.get("/api/keys")
def get_keys(service_id: int | None = None, x_api_key: str | None = Header(default=None)):
    actor_role = _resolve_caller(x_api_key)
    try:
        keys = key_service.list_keys(actor_role, service_id)
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return [
        {"id": k.id, "owner": k.owner, "service_id": k.service_id, "revoked_at": k.revoked_at}
        for k in keys
    ]


# ---------------------------------------------------------------------------
# Internal trace ingest endpoint (ARCHITECTURE.md §2.6)
#
# This endpoint is intentionally unauthenticated.  It is only meaningful
# from callers in the same process or on 127.0.0.1 — the single-process
# packaged-exe deployment model means no external actor can reach it without
# first compromising the host machine (at which point HTTP auth provides no
# meaningful additional barrier).  If a multi-process deployment is ever
# adopted, add auth here first.
# ---------------------------------------------------------------------------

class SpanPayload(BaseModel):
    """Loose schema for an inbound span.  All fields are optional so the
    endpoint can accept both minimal hand-crafted spans (sample service)
    and richer OpenTelemetry-formatted payloads in future."""
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str | None = None
    name: str = ""
    service_id: int = 0
    start_time: float | None = None
    end_time: float | None = None
    attributes: dict = {}


@router.post("/internal/trace", status_code=202)
def ingest_trace_span(payload: SpanPayload):
    """Receive a span from an instrumented service and push it into the
    in-memory ring buffer + subscriber queues (trace_service.ingest_span).

    Returns 202 Accepted — the caller does not need to wait for any DB
    write; persistence to the TraceSpan table is a future concern
    (ARCHITECTURE.md §2.6: 'Long-term trace storage … writes to a
    TraceSpan table with a retention policy').
    """
    trace_service.ingest_span(payload.model_dump())
    return {"accepted": True}
