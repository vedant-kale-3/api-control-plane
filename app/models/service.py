from datetime import datetime
from sqlmodel import SQLModel, Field


class Service(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
