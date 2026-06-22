from sqlmodel import SQLModel, Field


class Role(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class Permission(SQLModel, table=True):
    """Policy-as-data: (role, resource, action) tuples. Every privileged
    action in the app checks against this table — see ARCHITECTURE.md 2.4."""
    id: int | None = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="role.id")
    resource: str
    action: str
