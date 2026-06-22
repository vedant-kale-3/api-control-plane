"""
Append-only audit log writer + query. No update/delete function exists for
this table anywhere in the app — see ARCHITECTURE.md section 2.2.
"""
import json
from sqlmodel import select
from app.db import get_session
from app.models.audit_log import AuditLogEntry


def log_action(actor: str, action: str, target_type: str, target_id, details: dict | None = None):
    entry = AuditLogEntry(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=json.dumps(details or {}),
    )
    with get_session() as session:
        session.add(entry)
        session.commit()


def query_logs(actor: str | None = None, action: str | None = None, limit: int = 200):
    with get_session() as session:
        stmt = select(AuditLogEntry)
        if actor:
            stmt = stmt.where(AuditLogEntry.actor == actor)
        if action:
            stmt = stmt.where(AuditLogEntry.action == action)
        stmt = stmt.order_by(AuditLogEntry.timestamp.desc()).limit(limit)
        return session.exec(stmt).all()
