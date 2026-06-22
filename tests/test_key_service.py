"""
Minimal smoke test for the key issuance flow. Expand per-phase as services
are filled in. Requires a 'developer'/'admin' role + matching permission
rows to exist — seed_default_roles() should be called in a fixture before
these tests run for real.
"""
import pytest
from app.services.rbac_service import PermissionDenied
from app.services import key_service


def test_issue_key_requires_permission():
    with pytest.raises(PermissionDenied):
        key_service.issue_key("test-actor", "nonexistent-role", service_id=1, owner="test-owner")
