from datetime import datetime
from sqlmodel import SQLModel, Field


class TraceSpan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(foreign_key="service.id")
    trace_id: str = Field(index=True)
    span_id: str
    parent_span_id: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    attributes: str = "{}"
