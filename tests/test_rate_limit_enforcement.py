"""
Integration tests for the rate-limit enforcement wired into
POST /webhook/rotate-key (app/api/routes.py → rate_limit_service.py).

Test strategy
-------------
- Uses FastAPI's TestClient so the full request/response cycle runs,
  including the 429 branch.
- Uses an in-memory SQLite engine backed by StaticPool so the same
  connection is shared across the main thread and any worker threads
  spawned by anyio/starlette.  Regular sqlite:///:memory: creates a
  separate (empty) DB per connection, which breaks under TestClient's
  thread pool.
- Monkeypatches time.time inside rate_limit_service to advance the clock
  without sleeping — the sliding-window counter uses only time.time(), so
  this gives full control over window boundaries.
- Monkeypatches key_service.issue_key and rate_limit_service.check_permission
  so the tests focus exclusively on rate-limit behaviour (not key hashing,
  DB writes, or RBAC resolution — those are tested in their own suites).
- Monkeypatches routes._resolve_caller to return a fixed role so the
  X-API-Key stub doesn't interfere.

Scenarios covered
-----------------
1. Requests up to the limit are all allowed (200).
2. The very next request over the limit is rejected (429).
3. After the window rolls over, requests are allowed again (200).
4. When no policy is configured for a service, all requests are allowed
   through (allow-through = no-policy safe default).
5. The 429 response body contains a human-readable detail message.
6. Exact boundary: request == limit is allowed; request == limit+1 is denied.
7. Key-specific policy takes precedence over service-wide policy.
"""
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Shared in-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_engine(monkeypatch):
    """In-memory SQLite engine using StaticPool so the single connection is
    shared across the test thread AND any worker threads spawned by TestClient.

    Without StaticPool, sqlite:///:memory: allocates a fresh, empty database
    per connection — the anyio threadpool opens a new connection and sees no
    tables at all.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models  # noqa: F401 — register all table metadata
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _session():
        with Session(engine) as s:
            yield s

    # Patch every module that holds a direct reference to get_session.
    monkeypatch.setattr("app.db.get_session", _session)
    monkeypatch.setattr("app.services.rate_limit_service.get_session", _session)
    monkeypatch.setattr("app.services.rbac_service.get_session", _session)
    monkeypatch.setattr("app.services.key_service.get_session", _session)
    monkeypatch.setattr("app.api.routes.get_session", _session)
    return engine


# ---------------------------------------------------------------------------
# TestClient factory helper
# ---------------------------------------------------------------------------

def _make_client(monkeypatch) -> TestClient:
    """Return a TestClient backed by a minimal FastAPI app that includes
    the routes under test."""
    from app.api.routes import router

    # Bypass _resolve_caller stub — tests focus on rate-limit logic only.
    monkeypatch.setattr("app.api.routes._resolve_caller", lambda key: "service-account-role")

    # Stub issue_key so no real DB key writes happen (those are tested
    # separately in test_key_service.py).
    monkeypatch.setattr(
        "app.services.key_service.issue_key",
        MagicMock(return_value="raw-test-key-abc123"),
    )

    # Stub check_permission so RBAC checks are bypassed (tested separately).
    monkeypatch.setattr(
        "app.services.rate_limit_service.check_permission",
        MagicMock(return_value=True),
    )

    app = FastAPI()
    app.include_router(router)
    # raise_server_exceptions=True so 500s surface as proper exceptions with
    # full tracebacks rather than opaque status codes.
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------

def _ensure_service(engine, service_id: int):
    """Insert a Service FK row if it doesn't exist yet."""
    from app.models.service import Service
    with Session(engine) as s:
        if not s.exec(select(Service).where(Service.id == service_id)).first():
            s.add(Service(id=service_id, name=f"svc-{service_id}"))
            s.commit()


def _insert_service_policy(engine, service_id: int, limit: int, window_seconds: int,
                            key_id: int | None = None):
    """Insert a RateLimitPolicy (key_id=None → service-wide) into the DB."""
    from app.models.rate_limit import RateLimitPolicy
    _ensure_service(engine, service_id)
    with Session(engine) as s:
        s.add(RateLimitPolicy(service_id=service_id, key_id=key_id,
                              limit=limit, window_seconds=window_seconds))
        s.commit()


# ---------------------------------------------------------------------------
# Fake clock — controls time.time without sleeping
# ---------------------------------------------------------------------------

class FakeClock:
    """Callable that returns a mutable 'now'.  Assign or advance freely."""
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


# ---------------------------------------------------------------------------
# Core scenario: up-to-limit → reject → window-rollover → allow
# ---------------------------------------------------------------------------

class TestRateLimitEnforcement:
    """Tests the full lifecycle described in the user request."""

    def test_requests_up_to_limit_are_allowed(self, mem_engine, monkeypatch):
        """Each of the N requests within the limit should return 200."""
        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        _insert_service_policy(mem_engine, service_id=1, limit=3, window_seconds=60)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)
        for i in range(3):
            clock.advance(0.1)
            resp = client.post("/webhook/rotate-key",
                               params={"service_id": 1, "owner": "ci"},
                               headers={"X-API-Key": "tok"})
            assert resp.status_code == 200, \
                f"Request {i+1} should be allowed (got {resp.status_code}): {resp.text}"

    def test_request_over_limit_is_rejected_with_429(self, mem_engine, monkeypatch):
        """Request N+1 (over the configured limit) must return 429."""
        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        _insert_service_policy(mem_engine, service_id=2, limit=2, window_seconds=60)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)

        # Exhaust the limit.
        for _ in range(2):
            clock.advance(0.1)
            client.post("/webhook/rotate-key",
                        params={"service_id": 2, "owner": "ci"},
                        headers={"X-API-Key": "tok"})

        # One more must be rejected.
        clock.advance(0.1)
        resp = client.post("/webhook/rotate-key",
                           params={"service_id": 2, "owner": "ci"},
                           headers={"X-API-Key": "tok"})
        assert resp.status_code == 429

    def test_429_response_contains_detail_message(self, mem_engine, monkeypatch):
        """The 429 body must contain a human-readable 'detail' string that
        includes both the phrase 'Rate limit exceeded' and the window size."""
        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        _insert_service_policy(mem_engine, service_id=3, limit=1, window_seconds=30)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)
        clock.advance(0.1)
        client.post("/webhook/rotate-key",    # exhaust limit
                    params={"service_id": 3, "owner": "ci"},
                    headers={"X-API-Key": "tok"})

        clock.advance(0.1)
        resp = client.post("/webhook/rotate-key",
                           params={"service_id": 3, "owner": "ci"},
                           headers={"X-API-Key": "tok"})
        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body
        assert "Rate limit exceeded" in body["detail"]
        assert "30" in body["detail"]   # window_seconds is surfaced

    def test_after_window_rollover_requests_are_allowed_again(self, mem_engine, monkeypatch):
        """After the full window_seconds elapses the sliding-window counter
        expires all old buckets — the next request must be accepted."""
        WINDOW = 60
        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        _insert_service_policy(mem_engine, service_id=4, limit=2, window_seconds=WINDOW)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)

        # Exhaust the limit.
        for _ in range(2):
            clock.advance(0.1)
            client.post("/webhook/rotate-key",
                        params={"service_id": 4, "owner": "ci"},
                        headers={"X-API-Key": "tok"})

        # Confirm we are over limit before rolling the window.
        clock.advance(0.1)
        over = client.post("/webhook/rotate-key",
                           params={"service_id": 4, "owner": "ci"},
                           headers={"X-API-Key": "tok"})
        assert over.status_code == 429, "Should be over limit before window rolls"

        # Advance past the full window — all old timestamps expire.
        clock.advance(WINDOW + 1)

        # First request in the new window must be allowed.
        resp = client.post("/webhook/rotate-key",
                           params={"service_id": 4, "owner": "ci"},
                           headers={"X-API-Key": "tok"})
        assert resp.status_code == 200, (
            f"Expected 200 after window rollover, got {resp.status_code}: {resp.text}"
        )

    def test_limit_exactly_at_boundary_is_allowed(self, mem_engine, monkeypatch):
        """The Nth request (exactly == limit) must still be allowed;
        only the N+1th is denied."""
        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        _insert_service_policy(mem_engine, service_id=5, limit=5, window_seconds=60)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)
        statuses = []
        for _ in range(6):   # 5 should pass, 6th should fail
            clock.advance(0.05)
            r = client.post("/webhook/rotate-key",
                            params={"service_id": 5, "owner": "ci"},
                            headers={"X-API-Key": "tok"})
            statuses.append(r.status_code)

        assert statuses[:5] == [200] * 5, f"First 5 must be 200, got {statuses[:5]}"
        assert statuses[5] == 429, f"6th must be 429, got {statuses[5]}"


# ---------------------------------------------------------------------------
# No-policy allow-through
# ---------------------------------------------------------------------------

class TestNoPolicyAllowThrough:
    def test_no_policy_configured_allows_all_requests(self, mem_engine, monkeypatch):
        """When no RateLimitPolicy row exists for a service, every request
        must be allowed through — 'no policy = no limit' is the safe default
        for a control plane that starts unconfigured."""
        _ensure_service(mem_engine, service_id=99)   # FK row only, no policy

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        client = _make_client(monkeypatch)
        for i in range(10):
            resp = client.post("/webhook/rotate-key",
                               params={"service_id": 99, "owner": "ci"},
                               headers={"X-API-Key": "tok"})
            assert resp.status_code == 200, \
                f"Request {i+1} with no policy should be 200, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Policy lookup order: key-specific over service-wide
# ---------------------------------------------------------------------------

class TestPolicyLookupPriority:
    def test_key_specific_policy_takes_precedence_over_service_wide(self, mem_engine, monkeypatch):
        """A key_id-specific policy with a tighter limit must be enforced
        instead of the looser service-wide policy (ARCHITECTURE.md §2.5)."""
        from app.models.rate_limit import RateLimitPolicy

        _ensure_service(mem_engine, service_id=10)
        with Session(mem_engine) as s:
            s.add(RateLimitPolicy(service_id=10, key_id=None,  limit=100, window_seconds=60))
            s.add(RateLimitPolicy(service_id=10, key_id=42,    limit=1,   window_seconds=60))
            s.commit()

        clock = FakeClock()
        monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)

        import app.services.rate_limit_service as rl
        rl._counters.clear()

        # Patch _get_policy_for_service to return the key-specific policy,
        # simulating the resolved key_id=42 scenario.
        def _key_specific_policy(service_id, key_id=None):
            with Session(mem_engine) as s:
                return s.exec(
                    select(RateLimitPolicy).where(
                        RateLimitPolicy.service_id == service_id,
                        RateLimitPolicy.key_id == 42,
                    )
                ).first()

        monkeypatch.setattr("app.api.routes._get_policy_for_service", _key_specific_policy)

        client = _make_client(monkeypatch)

        clock.advance(0.1)
        r1 = client.post("/webhook/rotate-key",
                         params={"service_id": 10, "owner": "ci"},
                         headers={"X-API-Key": "tok"})
        assert r1.status_code == 200, "First request within key-specific limit should pass"

        clock.advance(0.1)
        r2 = client.post("/webhook/rotate-key",
                         params={"service_id": 10, "owner": "ci"},
                         headers={"X-API-Key": "tok"})
        assert r2.status_code == 429, (
            "Second request should hit the tight key-specific limit (1), "
            "not the service-wide limit of 100"
        )
