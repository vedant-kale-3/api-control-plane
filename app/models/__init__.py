# Import all models here so SQLModel.metadata sees every table before
# create_all() runs in app/db.py:init_db().
from app.models.service import Service  # noqa: F401
from app.models.api_key import ApiKey  # noqa: F401
from app.models.rate_limit import RateLimitPolicy  # noqa: F401
from app.models.audit_log import AuditLogEntry  # noqa: F401
from app.models.rbac import Role, Permission  # noqa: F401
from app.models.trace import TraceSpan  # noqa: F401
