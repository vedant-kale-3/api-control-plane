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
