from datetime import datetime
from sqlmodel import SQLModel, Field


class AuditLogEntry(SQLModel, table=True):
    """Append-only. No update/delete path is exposed anywhere in the app —
    see ARCHITECTURE.md section 2.2."""
    id: int | None = Field(default=None, primary_key=True)
    actor: str
    action: str
    target_type: str
    target_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: str = "{}"  # JSON-encoded string
