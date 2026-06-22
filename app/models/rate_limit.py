from sqlmodel import SQLModel, Field


class RateLimitPolicy(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(foreign_key="service.id")
    key_id: int | None = Field(default=None, foreign_key="apikey.id")  # null = applies to all keys for the service
    limit: int
    window_seconds: int
    algorithm: str = "sliding_window_counter"
