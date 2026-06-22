"""
RBAC permission checks and admin CRUD. Every service-layer function and every
API route must call check_permission() before performing a privileged action.
Zero-trust: there is no "internal call" path that skips this check
(ARCHITECTURE.md section 2.4).
"""
from sqlmodel import select
from app.db import get_session
from app.models.rbac import Role, Permission


class PermissionDenied(Exception):
    pass


def check_permission(actor_role: str, resource: str, action: str) -> bool:
    """Enforce a single permission gate.  Raises PermissionDenied if:
    - *actor_role* is not a known role, OR
    - no Permission row grants (actor_role, resource, action).
    Returns True on success so callers can assert on it in tests.
    """
    with get_session() as session:
        role = session.exec(select(Role).where(Role.name == actor_role)).first()
        if not role:
            raise PermissionDenied(f"Unknown role: {actor_role}")
        perm = session.exec(
            select(Permission).where(
                Permission.role_id == role.id,
                Permission.resource == resource,
                Permission.action == action,
            )
        ).first()
        if not perm:
            raise PermissionDenied(f"Role '{actor_role}' cannot '{action}' on '{resource}'")
        return True


# ---------------------------------------------------------------------------
# RBAC admin functions — each gates on check_permission() first (zero-trust),
# then writes to the DB, then appends an audit entry.  The pattern mirrors
# key_service.py exactly (ARCHITECTURE.md §2.1).
# ---------------------------------------------------------------------------

def add_role(actor: str, actor_role: str, new_role_name: str) -> Role:
    """Create a new named role.  Caller must hold rbac:manage.

    Raises PermissionDenied if the caller is not authorised.
    Raises ValueError if a role with that name already exists.
    """
    check_permission(actor_role, "rbac", "manage")

    # Local import avoids the circular dependency that arises if audit_service
    # imports from rbac_service at module load time.
    from app.services.audit_service import log_action

    with get_session() as session:
        existing = session.exec(select(Role).where(Role.name == new_role_name)).first()
        if existing:
            raise ValueError(f"Role '{new_role_name}' already exists")
        role = Role(name=new_role_name)
        session.add(role)
        session.commit()
        session.refresh(role)

    log_action(actor, "add_role", "rbac", new_role_name, {"role_name": new_role_name})
    return role


def add_permission(
    actor: str,
    actor_role: str,
    target_role_name: str,
    resource: str,
    action: str,
) -> Permission:
    """Grant a (resource, action) permission to an existing role.
    Caller must hold rbac:manage.

    Raises PermissionDenied if the caller is not authorised.
    Raises ValueError if the target role does not exist, or if the
    exact (role, resource, action) row already exists.
    """
    check_permission(actor_role, "rbac", "manage")

    from app.services.audit_service import log_action

    with get_session() as session:
        role = session.exec(select(Role).where(Role.name == target_role_name)).first()
        if not role:
            raise ValueError(f"Role '{target_role_name}' not found")
        existing = session.exec(
            select(Permission).where(
                Permission.role_id == role.id,
                Permission.resource == resource,
                Permission.action == action,
            )
        ).first()
        if existing:
            raise ValueError(
                f"Permission ({target_role_name}, {resource}, {action}) already exists"
            )
        perm = Permission(role_id=role.id, resource=resource, action=action)
        session.add(perm)
        session.commit()
        session.refresh(perm)

    log_action(
        actor,
        "add_permission",
        "rbac",
        perm.id,
        {"role": target_role_name, "resource": resource, "action": action},
    )
    return perm


def list_roles(actor_role: str) -> list[Role]:
    """Return all roles.  Caller must hold rbac:read."""
    check_permission(actor_role, "rbac", "read")
    with get_session() as session:
        return session.exec(select(Role)).all()


def list_permissions(
    actor_role: str,
    target_role_name: str | None = None,
) -> list[Permission]:
    """Return Permission rows.  Caller must hold rbac:read.

    If *target_role_name* is given, filter to that role only.
    Raises ValueError if the named role does not exist.
    """
    check_permission(actor_role, "rbac", "read")
    with get_session() as session:
        stmt = select(Permission)
        if target_role_name is not None:
            role = session.exec(select(Role).where(Role.name == target_role_name)).first()
            if not role:
                raise ValueError(f"Role '{target_role_name}' not found")
            stmt = stmt.where(Permission.role_id == role.id)
        return session.exec(stmt).all()


def seed_default_roles():
    """Run once on first launch. Creates the four roles from PRD.md section 5.4
    with a starter permission set. Extend via the RBAC Admin page, not by editing
    this function later."""
    defaults = {
        "admin": [
            ("api_key", "create"), ("api_key", "revoke"), ("api_key", "list"),
            ("rate_limit_policy", "create"),
            ("rbac", "manage"), ("rbac", "read"),   # admin can manage AND read rbac
        ],
        "service-owner": [
            ("api_key", "create"), ("api_key", "revoke"), ("api_key", "list"),
            ("rate_limit_policy", "create"),
        ],
        "auditor": [
            ("audit_log", "read"),
            ("rbac", "read"),                        # auditor can read (not manage) rbac
        ],
        "developer": [
            ("api_key", "list"),
        ],
    }
    with get_session() as session:
        for role_name, perms in defaults.items():
            role = session.exec(select(Role).where(Role.name == role_name)).first()
            if not role:
                role = Role(name=role_name)
                session.add(role)
                session.commit()
                session.refresh(role)
            for resource, action in perms:
                exists = session.exec(
                    select(Permission).where(
                        Permission.role_id == role.id,
                        Permission.resource == resource,
                        Permission.action == action,
                    )
                ).first()
                if not exists:
                    session.add(Permission(role_id=role.id, resource=resource, action=action))
        session.commit()
