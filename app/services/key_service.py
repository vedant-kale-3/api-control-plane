"""
API key lifecycle. Keys are stored hashed (sha256) — the raw key is returned
to the caller exactly once, at creation time, and is never retrievable again
(ARCHITECTURE.md section 2.2).
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from sqlmodel import select
from app.db import get_session
from app.models.api_key import ApiKey
from app.services.rbac_service import check_permission
from app.services.audit_service import log_action


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def issue_key(
    actor: str,
    actor_role: str,
    service_id: int,
    owner: str,
    scopes: str = "",
    expires_in_days: int | None = 90,
) -> str:
    check_permission(actor_role, "api_key", "create")
    raw_key = secrets.token_urlsafe(32)
    key = ApiKey(
        key_hash=_hash_key(raw_key),
        service_id=service_id,
        owner=owner,
        scopes=scopes,
        expires_at=datetime.utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    with get_session() as session:
        session.add(key)
        session.commit()
        session.refresh(key)

    log_action(actor, "create_key", "api_key", key.id, {"service_id": service_id, "owner": owner})
    return raw_key


def revoke_key(actor: str, actor_role: str, key_id: int):
    check_permission(actor_role, "api_key", "revoke")
    with get_session() as session:
        key = session.get(ApiKey, key_id)
        if not key:
            raise ValueError("Key not found")
        key.revoked_at = datetime.utcnow()
        session.add(key)
        session.commit()
    log_action(actor, "revoke_key", "api_key", key_id)


def list_keys(actor_role: str, service_id: int | None = None):
    check_permission(actor_role, "api_key", "list")
    with get_session() as session:
        stmt = select(ApiKey)
        if service_id:
            stmt = stmt.where(ApiKey.service_id == service_id)
        return session.exec(stmt).all()
