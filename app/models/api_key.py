from datetime import datetime
from sqlmodel import SQLModel, Field


class ApiKey(SQLModel, table=True):
    """Raw key is NEVER stored. Only key_hash (sha256). The raw key is shown
    to the caller exactly once, at creation time, in key_service.issue_key()."""
    id: int | None = Field(default=None, primary_key=True)
    key_hash: str = Field(index=True, unique=True)
    service_id: int = Field(foreign_key="service.id")
    owner: str
    scopes: str = ""  # comma-separated for v1
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
