"""
Zero-trust unit tests for rbac_service.

Every test uses an isolated in-memory SQLite database so the suite is
fully self-contained and never touches the real data.db file
(ARCHITECTURE.md Packaging Notes — SQLite lives in %LOCALAPPDATA%).

Fixture strategy
----------------
- `db_session` patches `app.db.get_session` to return a fresh in-memory
  Session for every test.  This is the same monkeypatch approach used by
  test_key_service.py — keep it consistent.
- `seeded_db` builds on `db_session` by calling seed_default_roles() so
  the standard four roles + their permissions are present.

Zero-trust requirement being verified (ARCHITECTURE.md §2.4)
-------------------------------------------------------------
Every check_permission() call must raise PermissionDenied (fail-closed)
when:
  (a) the role row does not exist at all, OR
  (b) the role exists but the required Permission row is absent.
There is no "implicit trust" path — absence of a row always → denied.
"""
import pytest
from contextlib import contextmanager
from sqlmodel import SQLModel, Session, create_engine

# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session(monkeypatch):
    """Patch get_session() to return a session backed by an in-memory SQLite
    engine.  Each test gets a fresh engine so there is zero state leakage."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # Import all models so SQLModel.metadata knows every table.
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _session_ctx():
        with Session(engine) as s:
            yield s

    # get_session is used as `with get_session() as session:` so it must be
    # a context-manager-returning callable.
    monkeypatch.setattr("app.db.get_session", _session_ctx)

    # Also patch the import used directly inside rbac_service at call time
    monkeypatch.setattr("app.services.rbac_service.get_session", _session_ctx)

    yield engine


@pytest.fixture()
def seeded_db(db_session, monkeypatch):
    """Extends db_session by seeding the default roles so tests that need a
    valid role+permission environment can run.

    Also stubs out audit_service.log_action so tests don't require the
    AuditLogEntry table (already in schema via app.models import above,
    but this keeps tests decoupled from audit write side-effects)."""
    from unittest.mock import MagicMock
    import app.services.audit_service as aud
    monkeypatch.setattr(aud, "log_action", MagicMock())

    from app.services.rbac_service import seed_default_roles
    seed_default_roles()
    return db_session


# ---------------------------------------------------------------------------
# Import the module under test (after fixtures are defined so monkeypatching
# happens before any module-level DB activity).
# ---------------------------------------------------------------------------
from app.services.rbac_service import (  # noqa: E402
    PermissionDenied,
    check_permission,
    add_role,
    add_permission,
    list_roles,
    list_permissions,
)


# ===========================================================================
# check_permission — zero-trust fail-closed tests
# ===========================================================================

class TestCheckPermissionFailClosed:
    """ARCHITECTURE.md §2.4: 'every request is authenticated and authorized
    on every call'.  Missing role or missing permission → PermissionDenied."""

    def test_unknown_role_raises(self, db_session):
        """Role row does not exist → denied immediately (case a)."""
        with pytest.raises(PermissionDenied, match="Unknown role"):
            check_permission("ghost-role", "api_key", "create")

    def test_empty_db_any_role_raises(self, db_session):
        """DB is completely empty — no roles at all → denied."""
        with pytest.raises(PermissionDenied):
            check_permission("admin", "api_key", "create")

    def test_role_exists_but_permission_missing_raises(self, seeded_db):
        """Role row exists ('developer') but the requested permission
        (api_key, create) is not granted → denied (case b)."""
        with pytest.raises(PermissionDenied, match="cannot 'create' on 'api_key'"):
            check_permission("developer", "api_key", "create")

    def test_role_exists_wrong_resource_raises(self, seeded_db):
        """auditor has audit_log:read but NOT api_key:list → denied."""
        with pytest.raises(PermissionDenied):
            check_permission("auditor", "api_key", "list")

    def test_role_exists_wrong_action_raises(self, seeded_db):
        """developer has api_key:list but NOT api_key:create → denied."""
        with pytest.raises(PermissionDenied):
            check_permission("developer", "api_key", "create")

    def test_check_permission_returns_true_on_success(self, seeded_db):
        """Positive path: admin has api_key:create → returns True."""
        assert check_permission("admin", "api_key", "create") is True

    def test_empty_string_role_raises(self, db_session):
        """Edge case: empty string is not a known role → denied."""
        with pytest.raises(PermissionDenied):
            check_permission("", "api_key", "create")


# ===========================================================================
# add_role — zero-trust gate + happy path
# ===========================================================================

class TestAddRole:
    def test_unauthorised_actor_raises(self, seeded_db, monkeypatch):
        """developer does not have rbac:manage → denied before any DB write."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(PermissionDenied):
            add_role("actor", "developer", "new-role")

    def test_unknown_actor_role_raises(self, seeded_db, monkeypatch):
        """Completely unknown actor_role → denied (case a)."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(PermissionDenied, match="Unknown role"):
            add_role("actor", "no-such-role", "new-role")

    def test_add_role_happy_path(self, seeded_db, monkeypatch):
        """admin (has rbac:manage) can create a new role."""
        mock_log = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", mock_log)

        role = add_role("alice", "admin", "qa-engineer")
        assert role.name == "qa-engineer"
        assert role.id is not None
        mock_log.assert_called_once()

    def test_duplicate_role_raises_value_error(self, seeded_db, monkeypatch):
        """Creating a role whose name already exists → ValueError (not a
        silent no-op — callers must be explicit about idempotency)."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(ValueError, match="already exists"):
            add_role("alice", "admin", "admin")  # 'admin' was seeded

    def test_auditor_cannot_add_role(self, seeded_db, monkeypatch):
        """auditor has rbac:read but NOT rbac:manage → denied."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(PermissionDenied):
            add_role("bob", "auditor", "some-role")


# ===========================================================================
# add_permission — zero-trust gate + happy path
# ===========================================================================

class TestAddPermission:
    def test_unauthorised_actor_raises(self, seeded_db, monkeypatch):
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(PermissionDenied):
            add_permission("actor", "developer", "developer", "api_key", "delete")

    def test_unknown_actor_role_raises(self, seeded_db, monkeypatch):
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(PermissionDenied, match="Unknown role"):
            add_permission("actor", "ghost", "developer", "api_key", "delete")

    def test_target_role_not_found_raises(self, seeded_db, monkeypatch):
        """Caller is authorised but the target role doesn't exist → ValueError."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        with pytest.raises(ValueError, match="not found"):
            add_permission("alice", "admin", "nonexistent-role", "api_key", "create")

    def test_add_permission_happy_path(self, seeded_db, monkeypatch):
        """admin grants developer a new permission — succeeds and is audited."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        mock_log = MagicMock()
        monkeypatch.setattr(aud, "log_action", mock_log)

        perm = add_permission("alice", "admin", "developer", "api_key", "delete")
        assert perm.resource == "api_key"
        assert perm.action == "delete"
        mock_log.assert_called_once()

    def test_duplicate_permission_raises_value_error(self, seeded_db, monkeypatch):
        """Granting an already-existing (role, resource, action) → ValueError."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        # developer already has api_key:list from seed
        with pytest.raises(ValueError, match="already exists"):
            add_permission("alice", "admin", "developer", "api_key", "list")


# ===========================================================================
# list_roles — zero-trust gate
# ===========================================================================

class TestListRoles:
    def test_unknown_role_raises(self, seeded_db):
        with pytest.raises(PermissionDenied):
            list_roles("ghost")

    def test_developer_cannot_list_roles(self, seeded_db):
        """developer does not have rbac:read → denied."""
        with pytest.raises(PermissionDenied):
            list_roles("developer")

    def test_service_owner_cannot_list_roles(self, seeded_db):
        """service-owner does not have rbac:read → denied."""
        with pytest.raises(PermissionDenied):
            list_roles("service-owner")

    def test_admin_can_list_roles(self, seeded_db):
        """admin has rbac:read → succeeds and returns all seeded roles."""
        roles = list_roles("admin")
        role_names = {r.name for r in roles}
        assert {"admin", "auditor", "developer", "service-owner"}.issubset(role_names)

    def test_auditor_can_list_roles(self, seeded_db):
        """auditor has rbac:read → succeeds."""
        roles = list_roles("auditor")
        assert len(roles) >= 4


# ===========================================================================
# list_permissions — zero-trust gate + filter
# ===========================================================================

class TestListPermissions:
    def test_unknown_role_raises(self, seeded_db):
        with pytest.raises(PermissionDenied):
            list_permissions("ghost")

    def test_developer_cannot_list_permissions(self, seeded_db):
        """developer does not have rbac:read → denied."""
        with pytest.raises(PermissionDenied):
            list_permissions("developer")

    def test_admin_can_list_all_permissions(self, seeded_db):
        """admin has rbac:read → returns all Permission rows."""
        perms = list_permissions("admin")
        assert len(perms) > 0

    def test_list_permissions_filter_by_role(self, seeded_db):
        """Filtering by 'developer' returns only that role's permissions."""
        perms = list_permissions("admin", target_role_name="developer")
        # developer is seeded with exactly one permission: api_key:list
        assert len(perms) == 1
        assert perms[0].resource == "api_key"
        assert perms[0].action == "list"

    def test_filter_nonexistent_role_raises(self, seeded_db):
        """Filtering by a role that doesn't exist → ValueError."""
        with pytest.raises(ValueError, match="not found"):
            list_permissions("admin", target_role_name="nonexistent-role")

    def test_auditor_can_list_permissions(self, seeded_db):
        """auditor has rbac:read → succeeds."""
        perms = list_permissions("auditor")
        assert isinstance(perms, list)


# ===========================================================================
# Cross-cutting: zero-trust enforcement by role (permission matrix spot-checks)
# ===========================================================================

class TestPermissionMatrix:
    """Quick matrix checks — each row verifies that a role *cannot* do
    something it was not explicitly granted, reinforcing that every gate
    fails closed on missing permission rows."""

    @pytest.mark.parametrize("role,resource,action", [
        ("developer",     "rbac",               "manage"),
        ("developer",     "rbac",               "read"),
        ("developer",     "rate_limit_policy",  "create"),
        ("developer",     "audit_log",          "read"),
        ("auditor",       "api_key",            "create"),
        ("auditor",       "api_key",            "revoke"),
        ("auditor",       "rbac",               "manage"),
        ("service-owner", "rbac",               "manage"),
        ("service-owner", "rbac",               "read"),
        ("service-owner", "audit_log",          "read"),
    ])
    def test_role_does_not_have_permission(self, seeded_db, role, resource, action):
        with pytest.raises(PermissionDenied):
            check_permission(role, resource, action)


# ===========================================================================
# Zero-permissions role: fail-closed guarantee
# ===========================================================================

class TestRoleWithNoPermissionsFailsClosed:
    """ARCHITECTURE.md §2.4 — a role row that exists in the DB but has
    zero associated Permission rows must never pass a check_permission()
    call.  This is the critical 'fail-closed' invariant: presence of a
    Role row alone confers *no* access at all.

    This directly covers the scenario described in the rbac_admin page
    where a newly-created role starts empty and the UI warns the user it
    "fails closed on every access check until permissions are explicitly
    granted."
    """

    @pytest.fixture()
    def empty_role_db(self, seeded_db, monkeypatch):
        """Extends seeded_db by adding a brand-new role with no permissions.

        The audit log side-effect is stubbed (same pattern as seeded_db)
        so the fixture is self-contained.
        """
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        add_role("fixture-actor", "admin", "no-perms-role")
        return seeded_db

    def test_empty_role_is_denied_on_any_resource(self, empty_role_db):
        """A role with zero permission rows must raise PermissionDenied
        regardless of the resource/action requested (case b, §2.4)."""
        with pytest.raises(PermissionDenied, match="cannot"):
            check_permission("no-perms-role", "api_key", "list")

    def test_empty_role_denied_on_rbac_read(self, empty_role_db):
        """Specifically confirm the role cannot read RBAC data either —
        this matters because list_roles/list_permissions gate on rbac:read."""
        with pytest.raises(PermissionDenied):
            check_permission("no-perms-role", "rbac", "read")

    def test_empty_role_denied_on_rbac_manage(self, empty_role_db):
        """Confirm the role cannot manage RBAC (escalation-grant gate)."""
        with pytest.raises(PermissionDenied):
            check_permission("no-perms-role", "rbac", "manage")

    def test_empty_role_denied_on_audit_log(self, empty_role_db):
        """Confirm the role cannot read the audit log."""
        with pytest.raises(PermissionDenied):
            check_permission("no-perms-role", "audit_log", "read")

    def test_empty_role_denied_on_rate_limit_policy(self, empty_role_db):
        """Confirm the role cannot create rate-limit policies."""
        with pytest.raises(PermissionDenied):
            check_permission("no-perms-role", "rate_limit_policy", "create")

    @pytest.mark.parametrize("resource,action", [
        ("api_key",            "create"),
        ("api_key",            "revoke"),
        ("api_key",            "list"),
        ("rate_limit_policy",  "create"),
        ("rbac",               "manage"),
        ("rbac",               "read"),
        ("audit_log",          "read"),
    ])
    def test_empty_role_fails_closed_across_all_known_permissions(
        self, empty_role_db, resource, action
    ):
        """Parametrised sweep across every (resource, action) pair that
        exists in the seeded permission set — the empty role must be
        denied on all of them without exception."""
        with pytest.raises(PermissionDenied):
            check_permission("no-perms-role", resource, action)

    def test_granting_one_permission_does_not_unlock_others(
        self, empty_role_db, monkeypatch
    ):
        """After explicitly granting a single permission the role passes
        exactly that check — and still fails on every other resource/action.
        This confirms permissions are additive and never implicit."""
        from unittest.mock import MagicMock
        import app.services.audit_service as aud
        monkeypatch.setattr(aud, "log_action", MagicMock())

        # Grant exactly one permission.
        add_permission("actor", "admin", "no-perms-role", "api_key", "list")

        # That specific check must now pass.
        assert check_permission("no-perms-role", "api_key", "list") is True

        # All other checks must still fail.
        still_denied = [
            ("api_key",           "create"),
            ("api_key",           "revoke"),
            ("rate_limit_policy", "create"),
            ("rbac",              "manage"),
            ("rbac",              "read"),
            ("audit_log",         "read"),
        ]
        for resource, action in still_denied:
            with pytest.raises(PermissionDenied, match="cannot"):
                check_permission("no-perms-role", resource, action)
