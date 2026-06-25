"""
Sliding window counter rate limiting (ARCHITECTURE.md section 2.5). Kept
isolated in this module so the algorithm can be swapped later without
touching call sites.

NOTE: the in-memory counter below works for a single-process app (matches
the packaged-exe deployment model). If this ever runs multi-process, replace
_counters with a persistent/shared store.
"""
import time
from collections import defaultdict
from sqlmodel import select
from app.db import get_session
from app.models.rate_limit import RateLimitPolicy
from app.services.rbac_service import check_permission

_counters: dict[int, list[tuple[float, int]]] = defaultdict(list)


def get_consumption_snapshot(window_seconds: int = 60) -> dict[int, int]:
    """Return a read-only snapshot of current-window hit counts.

    Returns a plain ``{key_id: hit_count}`` dict for every key_id that has at
    least one recorded hit inside *window_seconds*.  Prunes stale buckets as a
    side effect (same as check_and_increment) so the dict stays lean.

    This is the **only** public entry point the GUI should use to read
    consumption data — it must never import ``_counters`` directly.
    ``window_seconds`` defaults to 60 s; callers that know the policy window
    should pass the actual value for an accurate count.
    """
    now = time.time()
    snapshot: dict[int, int] = {}
    for key_id, history in list(_counters.items()):
        # Prune in place so the dict doesn't grow unboundedly.
        history[:] = [(ts, c) for ts, c in history if now - ts < window_seconds]
        total = sum(c for _, c in history)
        if total > 0:
            snapshot[key_id] = total
    return snapshot


def create_policy(actor_role: str, service_id: int, limit: int, window_seconds: int, key_id: int | None = None):
    check_permission(actor_role, "rate_limit_policy", "create")
    policy = RateLimitPolicy(service_id=service_id, key_id=key_id, limit=limit, window_seconds=window_seconds)
    with get_session() as session:
        session.add(policy)
        session.commit()
        session.refresh(policy)
    return policy


def list_policies(service_id: int | None = None):
    with get_session() as session:
        stmt = select(RateLimitPolicy)
        if service_id:
            stmt = stmt.where(RateLimitPolicy.service_id == service_id)
        return session.exec(stmt).all()


def check_and_increment(key_id: int, policy: RateLimitPolicy) -> bool:
    """Returns True if the request is allowed, False if over limit."""
    now = time.time()
    window = policy.window_seconds
    history = _counters[key_id]
    history[:] = [(ts, c) for ts, c in history if now - ts < window]
    current_count = sum(c for _, c in history)
    if current_count >= policy.limit:
        return False
    history.append((now, 1))
    return True
