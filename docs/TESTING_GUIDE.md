# API Control Plane — Feature Testing & Core Logic Guide

> **Audience:** Developers, QA engineers, and security auditors who need to understand what each feature does, the logic running behind it, and how to test it manually or programmatically.

---

## Table of Contents

1. [Application Overview](#1-application-overview)
2. [Running the Application & Tests](#2-running-the-application--tests)
3. [Feature 1: API Key Management](#3-feature-1-api-key-management)
4. [Feature 2: Role-Based Access Control (RBAC)](#4-feature-2-role-based-access-control-rbac)
5. [Feature 3: Rate Limiting](#5-feature-3-rate-limiting)
6. [Feature 4: Audit Log](#6-feature-4-audit-log)
7. [Feature 5: Live Request Trace](#7-feature-5-live-request-trace)
8. [Feature 6: External HTTP API (Webhook & Key Endpoints)](#8-feature-6-external-http-api-webhook--key-endpoints)
9. [Automated Test Suite Reference](#9-automated-test-suite-reference)
10. [Permission Matrix Reference](#10-permission-matrix-reference)

---

## 1. Application Overview

The **API Control Plane** is a single-process Python desktop application (NiceGUI native window + FastAPI). It provides a security dashboard for managing API keys, rate-limiting policies, roles, and observability traces for downstream services.

```
┌─────────────────────────────────────────────┐
│           Single Python Process              │
│                                              │
│  NiceGUI GUI  ──────►  Service Layer         │
│  (pywebview)   direct   (RBAC, keys,         │
│                 calls    rate limits,         │
│  FastAPI        <──────  audit, tracing)      │
│  routes                     │                │
│  (webhook,                  ▼                │
│   /api/keys)             SQLite              │
└─────────────────────────────────────────────┘
```

**Key architectural rules that affect testing:**

| Rule | Impact on Testing |
|------|-------------------|
| Zero-trust: every action re-checks RBAC | Tests must seed roles+permissions before testing any privileged call |
| Keys stored as SHA-256 hash only | Raw key is only visible at creation — you cannot retrieve it later |
| Audit log is append-only | No UPDATE/DELETE path exists; log entries are permanent |
| Rate limiting is in-memory (per process) | Tests must clear `_counters` and control `time.time` to isolate windows |
| Service layer is the single truth layer | GUI pages and API routes are thin wrappers — test the service layer directly |

---

## 2. Running the Application & Tests

### Starting the Application

```powershell
# From the project root (api-control-plane directory)
python -m app.main
```

On first launch, `bootstrap()` runs automatically:
1. Creates the SQLite database at `%LOCALAPPDATA%\APIControlPlane\data.db`
2. Seeds the four default roles (`admin`, `service-owner`, `auditor`, `developer`)
3. Mounts the FastAPI router on the NiceGUI app
4. Opens the native desktop window

### Running the Test Suite

```powershell
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_rbac_service.py -v
pytest tests/test_rate_limit_enforcement.py -v
pytest tests/test_key_service.py -v

# Run a specific test class
pytest tests/test_rbac_service.py::TestCheckPermissionFailClosed -v

# Run a single test
pytest tests/test_rbac_service.py::TestAddRole::test_add_role_happy_path -v
```

> **Note:** All tests use isolated **in-memory SQLite** databases via `monkeypatch`. They never touch the real `data.db` file.

---

## 3. Feature 1: API Key Management

### What It Does

The **API Keys** page (`/keys`) allows authorised users to:

- **List** all API keys across all services (with status: ACTIVE / REVOKED)
- **Issue** a new key for a service, with optional scopes and expiry
- **Revoke** an existing key (sets `revoked_at` timestamp, key remains in DB for audit purposes)

### Core Logic — `app/services/key_service.py`

```
issue_key(actor, actor_role, service_id, owner, scopes, expires_in_days)
  │
  ├─ check_permission(actor_role, "api_key", "create")  ← RBAC gate
  ├─ raw_key = secrets.token_urlsafe(32)                ← cryptographically random
  ├─ key_hash = sha256(raw_key)                         ← only the hash is stored
  ├─ INSERT ApiKey row into DB
  ├─ log_action(actor, "create_key", ...)               ← audit trail
  └─ return raw_key                                     ← only time raw key is visible
```

```
revoke_key(actor, actor_role, key_id)
  │
  ├─ check_permission(actor_role, "api_key", "revoke")  ← RBAC gate
  ├─ key.revoked_at = datetime.utcnow()                 ← soft-delete
  ├─ UPDATE row in DB
  └─ log_action(actor, "revoke_key", ...)               ← audit trail
```

**Security design decisions:**
- `key_hash` is **never shown** in the GUI or API responses (the `_row()` serialiser intentionally omits it)
- The raw key is shown **exactly once** in a dismissible dialog at creation time
- Revocation is **soft** — the row persists for auditing; physical deletion is not supported

### How to Test — Manual (GUI)

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8080/keys` | Table loads, shows all keys (empty on fresh install) |
| 2 | Fill in Service ID = `1`, Owner = `test-user`, Expires = `90` | Form accepts values |
| 3 | Click **Issue Key** | Raw key dialog appears with a one-time key string |
| 4 | Click **Copy to clipboard**, then **I've saved it — Dismiss** | Dialog closes; table refreshes showing new ACTIVE row |
| 5 | Select the new row, click **Revoke Selected** | Row status changes to `REVOKED` (red text) |
| 6 | Try to issue a key **without** filling Service ID | Warning notification: "Service ID is required." |
| 7 | Try to issue a key **without** filling Owner | Warning notification: "Owner is required." |
| 8 | Click **Refresh** | Table reloads from DB |

### How to Test — Automated

```python
# The existing test covers the most critical gate:
# tests/test_key_service.py::test_issue_key_requires_permission

# To expand — test the happy path:
def test_issue_key_admin_succeeds(seeded_db):
    raw = key_service.issue_key("alice", "admin", service_id=1, owner="ci")
    assert isinstance(raw, str) and len(raw) > 10

# Test revoke:
def test_revoke_key(seeded_db):
    raw = key_service.issue_key("alice", "admin", service_id=1, owner="ci")
    keys = key_service.list_keys("admin")
    key_service.revoke_key("alice", "admin", keys[0].id)
    updated = key_service.list_keys("admin")
    assert updated[0].revoked_at is not None
```

---

## 4. Feature 2: Role-Based Access Control (RBAC)

### What It Does

The **RBAC Admin** page (`/rbac-admin`) allows `admin`-role users to:

- **View** all roles and their granted `(resource, action)` permissions in a table
- **Create** a new role (starts with zero permissions — fails closed on every check)
- **Grant** a permission to an existing role (requires a confirmation dialog to prevent accidental escalation)

### Core Logic — `app/services/rbac_service.py`

**Permission check (used by every service function and API route):**

```
check_permission(actor_role, resource, action)
  │
  ├─ Lookup Role row by name → NOT FOUND → raise PermissionDenied("Unknown role")
  └─ Lookup Permission row (role_id, resource, action)
       └─ NOT FOUND → raise PermissionDenied("Role 'X' cannot 'Y' on 'Z'")
       └─ FOUND → return True
```

**Zero-trust rule:** Absence of a row **always** means denied. There is no implicit `allow-all` anywhere.

**Default roles seeded at startup (`seed_default_roles`):**

| Role | Permissions |
|------|------------|
| `admin` | `api_key: create, revoke, list` · `rate_limit_policy: create` · `rbac: manage, read` |
| `service-owner` | `api_key: create, revoke, list` · `rate_limit_policy: create` |
| `auditor` | `audit_log: read` · `rbac: read` |
| `developer` | `api_key: list` |

**Add role flow:**

```
add_role(actor, actor_role, new_role_name)
  │
  ├─ check_permission(actor_role, "rbac", "manage")   ← gate
  ├─ check duplicate → raise ValueError if exists
  ├─ INSERT Role row
  └─ log_action(...)
```

**Grant permission flow:**

```
add_permission(actor, actor_role, target_role_name, resource, action)
  │
  ├─ check_permission(actor_role, "rbac", "manage")   ← gate
  ├─ check target role exists → raise ValueError if not
  ├─ check duplicate permission → raise ValueError if already exists
  ├─ INSERT Permission row
  └─ log_action(...)
```

### How to Test — Manual (GUI)

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/rbac-admin` | Table shows all 4 seeded roles with permissions |
| 2 | Type `qa-engineer` in "Add New Role", click **Create Role** | Success notification; table refreshes with new empty role row (resource/action shown as "—") |
| 3 | In Grant Permission form: Role=`qa-engineer`, Resource=`api_key`, Action=`list`; click **Grant Permission…** | Confirmation dialog appears showing the grant details |
| 4 | Click **Cancel** in the dialog | Nothing changes |
| 5 | Repeat step 3, click **Confirm Grant** | Permission added; table refreshes showing `qa-engineer → api_key → list` |
| 6 | Try to create a role with name `admin` | Warning: "Role 'admin' already exists" |
| 7 | Try to grant `developer → api_key → list` (already seeded) | Warning: "Permission ... already exists" |
| 8 | Try to grant to a role named `nonexistent-role` | Warning: "Role 'nonexistent-role' not found" |

### How to Test — Automated

The file `tests/test_rbac_service.py` has **comprehensive coverage** across these test classes:

| Class | What it tests |
|-------|--------------|
| `TestCheckPermissionFailClosed` | Zero-trust: unknown roles, missing permissions, empty DB, empty string role |
| `TestAddRole` | Unauthorised actors, happy path, duplicate role prevention |
| `TestAddPermission` | Unauthorised actors, unknown target roles, happy path, duplicate prevention |
| `TestListRoles` | Role-based read access gates |
| `TestListPermissions` | Filtered and unfiltered permission reads |
| `TestPermissionMatrix` | Spot-check that roles *cannot* do things they weren't granted |
| `TestRoleWithNoPermissionsFailsClosed` | A role with zero Permission rows is denied on everything |

**Run the full RBAC suite:**

```powershell
pytest tests/test_rbac_service.py -v
```

**Key test to understand the zero-trust model:**

```python
# A role that exists but has NO permissions must be denied on all resources
def test_empty_role_is_denied_on_any_resource(empty_role_db):
    with pytest.raises(PermissionDenied, match="cannot"):
        check_permission("no-perms-role", "api_key", "list")
```

---

## 5. Feature 3: Rate Limiting

### What It Does

The **Rate Limit Policies** page (`/rate-limits`) allows authorised users to:

- **View** configured rate-limit policies per service (and optionally per key)
- **Create** a new policy: `(service_id, limit, window_seconds, optional key_id)`
- **Monitor** live consumption — each row shows current-window hit count, colour-coded (green < 60%, amber 60–90%, red ≥ 90%). The dot in the header blinks every 5 seconds to show the timer is alive.

### Core Logic — `app/services/rate_limit_service.py`

**Algorithm: Sliding Window Counter**

```
check_and_increment(key_id, policy) → bool
  │
  ├─ now = time.time()
  ├─ Prune stale timestamps from history[key_id] (older than window_seconds)
  ├─ current_count = sum of remaining hits
  ├─ if current_count >= policy.limit → return False  (REJECTED, 429)
  └─ else → append (now, 1) to history, return True   (ALLOWED, 200)
```

**Policy lookup order (most specific wins):**

```
1. Policy for (service_id, key_id)  — exact key match
2. Policy for (service_id, NULL)    — service-wide fallback
3. None                             — no policy → allow through (safe default)
```

**In-memory counter structure:**

```python
_counters: dict[int, list[tuple[float, int]]] = defaultdict(list)
# key: key_id (negative int for service-level buckets in webhook)
# value: list of (timestamp, hit_count) tuples within the current window
```

> **Note:** The counter is in-process and resets on application restart. This is by design for the single-process deployment model.

### How to Test — Manual (GUI)

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/rate-limits` | Table shows all policies; live green dot blinks every 5s |
| 2 | Fill: Service ID=`1`, Limit=`5`, Window=`60`, Key ID blank | Click **Create Policy** → success notification |
| 3 | Table refreshes with new row; `Hits (current window)` shows `0` (grey) | |
| 4 | Send 4 requests to `POST /webhook/rotate-key?service_id=1` with a valid `X-API-Key` header | All return 200; consumption counter increments |
| 5 | Wait up to 5s for the live timer to tick | Hit count updates to reflect actual requests |
| 6 | Send a 6th request | Returns 429 with `Rate limit exceeded` message |
| 7 | Wait 61 seconds (window expires) | First request after the window returns 200 again |
| 8 | Try to create a policy with limit = `0` | Warning: "Limit must be ≥ 1." |

### How to Test — Automated

The file `tests/test_rate_limit_enforcement.py` uses FastAPI's `TestClient` and a fake clock to test the full lifecycle without sleeping.

| Test Scenario | Test Method |
|--------------|-------------|
| Requests up to limit are all allowed (200) | `TestRateLimitEnforcement::test_requests_up_to_limit_are_allowed` |
| Request N+1 over limit returns 429 | `test_request_over_limit_is_rejected_with_429` |
| 429 body has human-readable message with window size | `test_429_response_contains_detail_message` |
| After window rolls over, requests allowed again | `test_after_window_rollover_requests_are_allowed_again` |
| Exactly at limit is allowed; limit+1 is denied | `test_limit_exactly_at_boundary_is_allowed` |
| No policy configured → all requests pass through | `TestNoPolicyAllowThrough::test_no_policy_configured_allows_all_requests` |
| Key-specific policy beats service-wide policy | `TestPolicyLookupPriority::test_key_specific_policy_takes_precedence_over_service_wide` |

**Run the full rate-limit suite:**

```powershell
pytest tests/test_rate_limit_enforcement.py -v
```

**Understanding the FakeClock pattern (used in all timing tests):**

```python
class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds

# In the test:
clock = FakeClock()
monkeypatch.setattr("app.services.rate_limit_service.time.time", clock)
clock.advance(61)  # Simulate the window expiring — no actual sleep needed
```

---

## 6. Feature 4: Audit Log

### What It Does

The **Audit Log** page (`/audit-log`) provides a **read-only** view of every privileged action ever taken in the system:

- **Filter** by actor (exact match), action (exact match), date range
- **Clear filters** to reload the default view (latest 200 entries)
- **Export CSV** of the currently filtered rows (uses Python stdlib `csv` — no pandas dependency)

### Core Logic — `app/services/audit_service.py`

**Write path (append-only):**

```
log_action(actor, action, target_type, target_id, details)
  │
  └─ INSERT AuditLogEntry row
     (there is NO update or delete path anywhere in the codebase)
```

**Read path (query with filters):**

```
query_logs(actor, action, date_from, date_to, limit)
  │
  ├─ All parameters are optional
  ├─ actor / action → exact match (case-sensitive) WHERE clauses
  ├─ date_from / date_to → timestamp range WHERE clauses
  └─ Returns rows ordered by timestamp DESC, limited to `limit` rows
```

**What events are logged automatically:**

| Event | Actor field | Action field | Target |
|-------|------------|--------------|--------|
| Issue API key | GUI user / CI actor | `create_key` | `api_key` row ID |
| Revoke API key | GUI user | `revoke_key` | `api_key` row ID |
| Create RBAC role | GUI user | `add_role` | Role name |
| Grant RBAC permission | GUI user | `add_permission` | Permission row ID |

### How to Test — Manual (GUI)

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/audit-log` | Table loads with latest 200 entries (newest first) |
| 2 | Issue a new API key from the Keys page | Return to Audit Log; click **Apply Filters** (no filter) — new `create_key` entry appears at top |
| 3 | Filter Actor = `gui-user`, click **Apply Filters** | Only rows where actor is exactly `gui-user` appear |
| 4 | Filter Action = `add_role`, click **Apply Filters** | Only role-creation events appear |
| 5 | Set From Date = `2024-01-01`, To Date = `2024-12-31` | Entries within that range only |
| 6 | Enter invalid date `2024-13-01` | Warning notification: "Invalid date … use YYYY-MM-DD format" |
| 7 | Click **Export CSV** | Download dialog appears; CSV file contains all visible rows with header |
| 8 | Click **Export CSV** on empty table | Warning: "Nothing to export — the table is empty." |
| 9 | Click **Clear** | All filter inputs reset; full log reloads |

> **Verifying immutability:** There are no Edit, Delete, or Update buttons anywhere on this page — by design. The `AuditLogEntry` model has no UPDATE path exposed in the service layer.

### How to Test — Automated (Service Layer)

```python
from app.services.audit_service import log_action, query_logs

# After seeding a DB fixture:
log_action("alice", "create_key", "api_key", 42, {"service_id": 1})
log_action("bob",   "revoke_key", "api_key", 42)

results = query_logs(actor="alice")
assert len(results) == 1
assert results[0].action == "create_key"

all_logs = query_logs(limit=10)
assert len(all_logs) == 2

# Date range filter
from datetime import datetime
results = query_logs(date_from=datetime(2020, 1, 1), date_to=datetime(2020, 12, 31))
assert len(results) == 0  # No entries in the past
```

---

## 7. Feature 5: Live Request Trace

### What It Does

The **Live Trace** page (`/live-trace`) shows real-time OpenTelemetry spans arriving from instrumented services:

- On page load, **pre-populates** with the last 100 spans from the in-memory ring buffer
- A `ui.timer(1.0)` polls a per-page **asyncio.Queue** every second for new spans
- Rows are prepended (newest first) and capped at 200 to keep the DOM lean
- Duration column is colour-coded: green < 20ms, amber 20–100ms, red > 100ms
- **Clear Display** empties the table view (does not affect the ring buffer)
- When the browser tab/page closes, the queue is automatically unsubscribed

### Core Logic — `app/services/trace_service.py`

```
ingest_span(span_dict)
  │
  ├─ _recent_spans.append(span)      ← bounded deque (max 500)
  └─ for q in _subscribers:
       q.put_nowait(span)            ← fan-out to all live page views

subscribe() → asyncio.Queue          ← called once per page load
unsubscribe(q)                       ← called on page disconnect
get_recent(limit=100) → list         ← called on page load for pre-population
```

**Ingest endpoint (unauthenticated by design — internal use only):**

```
POST /internal/trace
Body: SpanPayload {
  trace_id, span_id, parent_span_id, name,
  service_id, start_time, end_time, attributes
}
→ 202 Accepted
```

### How to Test — Manual

**Using PowerShell (quick span injection):**

```powershell
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8080/internal/trace" `
  -ContentType "application/json" `
  -Body '{"trace_id":"abc123","span_id":"def456","name":"test-op","service_id":1,"start_time":1700000000.0,"end_time":1700000000.150,"attributes":{"http.status":200}}'
```

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/live-trace` | Page shows "Listening for spans…"; green dot blinks every second |
| 2 | POST a span via PowerShell | Within ~1 second, new row appears at top of table |
| 3 | POST a span with `end_time - start_time > 0.1` (>100ms) | Duration column shows value in **red** |
| 4 | POST a span with latency 20–100ms | Duration shows in **amber** |
| 5 | POST a span with latency < 20ms | Duration shows in **green** |
| 6 | Keep the page open and emit 210 spans rapidly | Table stays capped at 200 rows (oldest are dropped) |
| 7 | Click **Clear Display** | Table clears; the ring buffer in memory is unaffected |
| 8 | Close and reopen the tab | Table pre-populates from the ring buffer again |

### How to Test — Automated (Service Layer)

```python
from app.services import trace_service
import asyncio

trace_service.ingest_span({"trace_id": "t1", "name": "op1", "service_id": 1})

recent = trace_service.get_recent(limit=10)
assert len(recent) == 1
assert recent[0]["name"] == "op1"

# Test pub/sub
q = trace_service.subscribe()
trace_service.ingest_span({"trace_id": "t2", "name": "op2"})
span = q.get_nowait()
assert span["name"] == "op2"
trace_service.unsubscribe(q)
```

---

## 8. Feature 6: External HTTP API (Webhook & Key Endpoints)

### What It Does

The FastAPI routes in `app/api/routes.py` expose an HTTP surface for external callers (CI/CD pipelines, automation scripts):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/rotate-key` | `POST` | Rotate (issue a new) key for a service. Rate-limited per configured policy. |
| `/api/keys` | `GET` | List API keys. Optionally filtered by `service_id`. |
| `/internal/trace` | `POST` | Ingest a trace span (unauthenticated — internal use only). |

All endpoints (except `/internal/trace`) require an `X-API-Key` header.

### Core Logic — `app/api/routes.py`

**Authentication placeholder:**

```
_resolve_caller(api_key) → actor_role
  │
  ├─ if not api_key → HTTP 401 "Missing X-API-Key header"
  └─ return "service-account-role"  ← TODO: resolve from DB once ApiKey→Role is wired
```

**Webhook rotate-key flow:**

```
POST /webhook/rotate-key?service_id=X&owner=Y
Header: X-API-Key: <token>
  │
  ├─ _resolve_caller(api_key) → actor_role
  ├─ _get_policy_for_service(service_id) → policy | None
  │     └─ Checks (service_id, key_id) first, then (service_id, NULL) fallback
  ├─ if policy → check_and_increment(bucket_id, policy)
  │     └─ False → HTTP 429 with "Rate limit exceeded..." detail
  ├─ key_service.issue_key(...)
  │     └─ PermissionDenied → HTTP 403
  └─ return {"key": raw_key}
```

### How to Test — Manual (HTTP)

**Prerequisites:** Application is running (`python -m app.main`)

```powershell
# 1. Rotate/issue a key via webhook
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8080/webhook/rotate-key?service_id=1&owner=ci-pipeline" `
  -Headers @{"X-API-Key" = "any-value-while-stub-is-active"}
# Expected: {"key": "<raw_key_string>"}

# 2. List keys
Invoke-WebRequest -Method GET `
  -Uri "http://localhost:8080/api/keys" `
  -Headers @{"X-API-Key" = "any-value"}
# Expected: JSON array of key objects (no key_hash field)

# 3. Call without X-API-Key header → HTTP 401
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8080/webhook/rotate-key?service_id=1&owner=ci"
# Expected: 401 {"detail": "Missing X-API-Key header"}

# 4. Test rate limiting (configure a policy with limit=2 for service 1 first)
# First two calls → 200
# Third call → 429 {"detail": "Rate limit exceeded for service 1. Limit: 2 requests per 60s window."}
```

---

## 9. Automated Test Suite Reference

### Overview

| Test File | Coverage Area | Lines |
|-----------|--------------|-------|
| `tests/test_key_service.py` | Key issuance RBAC gate | ~15 |
| `tests/test_rbac_service.py` | Full RBAC zero-trust, role/permission CRUD | ~437 |
| `tests/test_rate_limit_enforcement.py` | Rate limit lifecycle, window rollover, policy priority | ~365 |

### Fixture Strategy (Common Pattern)

All tests use the same monkeypatching approach to avoid touching the real DB:

```python
@pytest.fixture()
def db_session(monkeypatch):
    # In-memory SQLite, isolated per test
    engine = create_engine("sqlite:///:memory:", ...)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _session_ctx():
        with Session(engine) as s:
            yield s

    monkeypatch.setattr("app.db.get_session", _session_ctx)
    monkeypatch.setattr("app.services.rbac_service.get_session", _session_ctx)
    yield engine

@pytest.fixture()
def seeded_db(db_session, monkeypatch):
    # Extends db_session by running seed_default_roles()
    # and stubbing audit_service.log_action to avoid audit DB side effects
    ...
    seed_default_roles()
```

For rate-limit tests, **`StaticPool`** is used so the in-memory DB is shared across threads (required when using `TestClient` which runs in a thread pool):

```python
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # Required for TestClient's thread pool
)
```

### Common Monkeypatches Used Across Test Files

| Patch target | Why it's patched |
|---|---|
| `app.db.get_session` | Redirect DB calls to the in-memory engine |
| `app.services.rbac_service.get_session` | Same — module holds its own reference |
| `app.services.rate_limit_service.time.time` | Control the sliding-window clock without sleeping |
| `app.services.key_service.issue_key` | Stub key writes in rate-limit tests (not the focus) |
| `app.services.rate_limit_service.check_permission` | Bypass RBAC in rate-limit tests (tested separately) |
| `app.api.routes._resolve_caller` | Bypass auth stub in integration tests |
| `app.services.audit_service.log_action` | Prevent audit writes from interfering with RBAC unit tests |

---

## 10. Permission Matrix Reference

This matrix shows which built-in roles can perform which actions. **Absence of a checkmark means `PermissionDenied`.**

| Action | `admin` | `service-owner` | `auditor` | `developer` |
|--------|:-------:|:---------------:|:---------:|:-----------:|
| `api_key: create` | ✅ | ✅ | ❌ | ❌ |
| `api_key: revoke` | ✅ | ✅ | ❌ | ❌ |
| `api_key: list` | ✅ | ✅ | ❌ | ✅ |
| `rate_limit_policy: create` | ✅ | ✅ | ❌ | ❌ |
| `rbac: manage` | ✅ | ❌ | ❌ | ❌ |
| `rbac: read` | ✅ | ❌ | ✅ | ❌ |
| `audit_log: read` | ❌* | ❌ | ✅ | ❌ |

> \* `admin` can view the Audit Log page in the GUI, but the `audit_log: read` permission is **not** seeded by default. It can be granted explicitly via the RBAC Admin page.

**Critical invariant — Roles with NO permissions fail closed on every check:**  
Presence of a `Role` row alone grants nothing. A brand-new role created via the RBAC Admin UI will be denied on every `check_permission()` call until at least one `Permission` row is explicitly granted to it.

---

## Quick Reference — Key Source Files

| File | Purpose |
|------|---------|
| `app/main.py` | Process entry point — wires DB, RBAC seed, GUI, and API |
| `app/db.py` | SQLite engine, session factory, DB path (`%LOCALAPPDATA%\APIControlPlane\data.db`) |
| `app/services/key_service.py` | API key lifecycle (issue, revoke, list) |
| `app/services/rbac_service.py` | Permission gate + role/permission CRUD |
| `app/services/rate_limit_service.py` | Sliding-window counter enforcement + consumption snapshot |
| `app/services/audit_service.py` | Append-only audit log writer + filtered query |
| `app/services/trace_service.py` | In-memory ring buffer + pub/sub for live traces |
| `app/api/routes.py` | External HTTP routes (webhook, key API, trace ingest) |
| `app/gui/pages/keys.py` | Keys page UI |
| `app/gui/pages/rbac_admin.py` | RBAC Admin page UI |
| `app/gui/pages/rate_limits.py` | Rate Limits page UI (with live consumption timer) |
| `app/gui/pages/audit_log.py` | Audit Log page UI (read-only, CSV export) |
| `app/gui/pages/live_trace.py` | Live Trace page UI (real-time span subscription) |
| `tests/test_rbac_service.py` | Full RBAC test suite (437 lines) |
| `tests/test_rate_limit_enforcement.py` | Rate limit integration tests (365 lines) |
| `tests/test_key_service.py` | Key service smoke test |
| `docs/ARCHITECTURE.md` | System design decisions and component breakdown |
| `docs/PRD.md` | Product requirements document |
