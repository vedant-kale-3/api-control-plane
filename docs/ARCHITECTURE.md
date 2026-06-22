# Architecture: API Control Plane

Version: 0.1 (Draft)
Companion to `PRD.md` — this doc defines *how* the system is built, so the conventions here should be treated as binding when generating code (manually or via an agent).

---

## 1. System Overview

A single Python process exposes:
- A **native desktop GUI** (NiceGUI in native mode) for interactive management — keys, rate limits, audit log, RBAC admin, live trace view.
- A **plain HTTP API surface** on the same FastAPI app instance, for CI/CD webhooks and any external/service-to-service calls.

Because NiceGUI is built directly on FastAPI, there is **one app, one process, one port** — not a frontend talking to a backend over HTTP. GUI event handlers call service-layer Python functions directly, in-process. Only the genuinely external-facing routes (webhook, external key API) go over HTTP, because they need to be reachable from outside the process (e.g. a CI runner).

```
┌─────────────────────────────────────────────┐
│              Single Python Process            │
│                                                 │
│  ┌──────────────┐        ┌──────────────────┐ │
│  │  NiceGUI      │ direct │  Service Layer    │ │
│  │  (native mode)│──────► │  (RBAC, keys,     │ │
│  │  pywebview/   │  calls │   rate limits,    │ │
│  │  WebView2     │        │   audit, tracing) │ │
│  └──────────────┘        └─────────┬─────────┘ │
│                                     │            │
│  ┌──────────────┐                  │            │
│  │  FastAPI      │  HTTP    ┌──────▼──────┐    │
│  │  routes:      │◄────────│   SQLite      │    │
│  │  /webhook,    │  (CI/CD,  │  (file-based) │    │
│  │  /api/keys    │   external)└──────────────┘    │
│  └──────────────┘                                │
└─────────────────────────────────────────────────┘
```

## 2. Component Breakdown

### 2.1 Service Layer (`/app/services/`)
Pure Python, no GUI or HTTP imports. Everything else calls into this.
- `key_service.py` — issue, revoke, rotate, list keys
- `rate_limit_service.py` — policy CRUD + enforcement check
- `audit_service.py` — append-only writer + query/filter API
- `rbac_service.py` — permission checks, role/policy CRUD
- `trace_service.py` — ingest spans, expose recent-trace query + live subscription

Rule: GUI code and HTTP route handlers are both *thin* — they call into this layer and format the result. No business logic lives in a NiceGUI page function or a FastAPI route handler.

### 2.2 Data Models (`/app/models/`)
SQLAlchemy models (or SQLModel, given FastAPI is already in the stack):

- `Service` — id, name, description, created_at
- `ApiKey` — id, key_hash, service_id (FK, nullable for multi-service scope via join table if needed), owner, scopes, issued_at, expires_at, revoked_at, last_used_at
- `RateLimitPolicy` — id, service_id, key_id (nullable = applies to all keys for that service), limit, window_seconds, algorithm
- `AuditLogEntry` — id, actor, action, target_type, target_id, timestamp, details (JSON), **immutable — no update/delete path exposed anywhere in the app**
- `Role` — id, name
- `Permission` — id, role_id, resource, action (this is the policy-as-data table Casbin would otherwise manage — see §3.2 below)
- `TraceSpan` — id, service_id, trace_id, span_id, parent_span_id, start_time, end_time, attributes (JSON)

**Decision needed before Phase 1:** store API keys hashed (e.g. SHA-256 of the key, never the raw key) and show the raw key to the user exactly once at creation time. This is standard practice and should not be revisited later — bake it in from the first migration.

### 2.3 RBAC Approach

Two viable options, pick one before Phase 1:

| | Casbin | Custom (Permission table above) |
|---|---|---|
| Pros | Battle-tested policy engine, supports RBAC/ABAC models out of the box, policies are reviewable as data | One less dependency; simpler mental model when the policy shape is just "role can do X on Y" |
| Cons | Adds a dependency + its own policy file/model syntax to learn | You own correctness of the permission-check logic |

**Recommendation:** start with the custom `Permission` table above. It's simple enough for "roles: admin/service-owner/auditor/developer" and keeps the data model RAG-ingestible as plain SQL rather than Casbin's separate model/policy DSL. Revisit Casbin if policies grow into genuinely conditional (ABAC) rules later.

### 2.4 Zero-Trust Implementation

Concretely, this means:
1. **Every** route (GUI-triggered service call *and* HTTP route) re-checks the caller's permission against the RBAC table — there is no "trusted internal call" path that skips the check.
2. The CI/CD webhook does **not** use a shared admin secret. It uses its own scoped service-account-style API key with a narrow permission set (e.g. `rotate_key` on one specific service only).
3. Session/auth tokens are short-lived (JWT with short expiry + refresh), even for the local native GUI session — the GUI is a client of the service layer like any other caller, not a privileged bypass.
4. All of this is logged via `audit_service.py` regardless of success or failure — failed authorization attempts are themselves audit-worthy events.

### 2.5 Rate Limiting Algorithm

**Recommendation: sliding window counter** (not fixed window, not full sliding-log). Reasons:
- Fixed window allows bursts at window boundaries (2x limit possible right at the edge)
- Full sliding-log (storing every request timestamp) is precise but memory/storage-heavy for a SQLite-backed single process
- Sliding window counter (current + previous window weighted) is a good accuracy/cost tradeoff and is simple to implement and explain in an audit

Implementation lives entirely in `rate_limit_service.py` — keep the algorithm isolated there so it can be swapped later without touching call sites.

### 2.6 Live Tracing

- Instrumented services (including the sample/demo service used for development) emit OpenTelemetry spans.
- For services in the same process boundary or reachable over the network, spans are pushed to `trace_service.py` via an OTLP receiver or a simple internal HTTP endpoint.
- `trace_service.py` holds a bounded in-memory ring buffer of recent spans plus a pub/sub mechanism (`asyncio.Queue` per subscriber) that the NiceGUI live view subscribes to via `ui.timer`.
- Long-term trace storage (beyond the live ring buffer) writes to a `TraceSpan` table with a retention policy (e.g. purge after N days) — define N before Phase 3.

### 2.7 NiceGUI Specifics

- Run mode: `ui.run(native=True, window_size=(1280, 800), title="API Control Plane", reload=False)`
  - `reload=False` is important for the packaged exe — NiceGUI's auto-reload is a dev-time feature and should be off in the frozen build.
- Use NiceGUI's AG Grid wrapper (`ui.aggrid`) for the Audit Log and Keys tables — both need sorting/filtering, and rolling your own table component for this is wasted effort.
- Page structure: one NiceGUI page per major section (Keys, Rate Limits, Audit Log, RBAC Admin, Live Trace), behind a left nav, each page module living in `/app/gui/pages/`.
- GUI pages import only from `/app/services/` — never directly from `/app/models/` (keeps the service layer as the single point of truth for business rules, including permission checks).

### 2.8 External HTTP Surface

Routes added to the same FastAPI app (not a separate app):
- `POST /webhook/rotate-key` — CI/CD calls this with its scoped service-account key
- `GET /api/keys` / `POST /api/keys` / `DELETE /api/keys/{id}` — for any external tooling that needs key management outside the GUI

These routes are thin wrappers around `key_service.py`, same as the GUI — same permission checks apply.

## 3. Repository Structure

```
/app
  /models/           # SQLAlchemy/SQLModel models
  /services/         # business logic, no GUI/HTTP imports
  /gui/
    /pages/          # one file per NiceGUI page
    main.py          # ui.run(native=True, ...) entry point
  /api/
    routes.py        # FastAPI external routes (webhook, key API)
  /tracing/
    otel_setup.py
  db.py              # SQLite engine/session setup
  main.py            # process entry point — wires GUI + API routes into one app
/sample_service/      # dummy instrumented microservice for dev/testing tracing
/build/
  app.spec           # PyInstaller spec
/docs/
  PRD.md
  ARCHITECTURE.md
/tests/
```

## 4. Packaging Notes (Phase 6, but decide assumptions now)

- **PyInstaller mode:** start with `--onedir` for development/debugging (faster iteration, easier to inspect what's bundled), switch to `--onefile` for the final distributable if a single file is genuinely required.
- **WebView2 dependency:** pywebview on Windows uses Edge WebView2. Decide target environment now:
  - *"Any Windows machine"* → bundle the WebView2 Evergreen Bootstrapper or fixed-version runtime, and have the installer check/install it.
  - *"Machines we control"* → assume WebView2 is present (it ships with current Windows/Edge) and skip the extra bundling step for v1.
- **SQLite file location:** must be a writable per-user app-data directory (e.g. `%LOCALAPPDATA%\APIControlPlane\data.db`), never a path relative to the frozen exe — frozen exe directories are sometimes not writable depending on install location.
- **Port binding for the webhook/API routes:** test explicitly that the frozen exe can still bind its port — confirm no firewall prompt or binding behavior difference compared to running from source, before Phase 6 is considered done.

## 5. Decisions Still Open (resolve before or during Phase 1)

1. Custom RBAC table vs. Casbin — recommendation given in §2.3, needs final sign-off.
2. Trace retention window (days) — affects `TraceSpan` table growth and SQLite file size over time.
3. `--onedir` vs `--onefile` for final packaging — affects installer design in Phase 6.
4. Whether keys are scoped to exactly one service or many-to-many via a join table — affects the `ApiKey` model shape in the very first migration, so this needs to be settled before Phase 1 starts.
