# PRD: API Control Plane
**A developer-facing platform for managing API keys, rate limits, and audit logs across microservices.**

Version: 0.1 (Draft)
Status: Pre-development — for RAG ingestion / agent grounding

---

## 1. Problem Statement

Teams running multiple microservices typically end up with API key management, rate limiting, and audit logging scattered across each service — or worse, undocumented and ad-hoc. This leads to:

- Inconsistent access control between services
- No central place to revoke a compromised key across the whole system
- No unified audit trail when investigating unauthorized access
- Security reviews and CI/CD pipelines treating key rotation as a manual, error-prone step

This project centralizes those three concerns (keys, rate limits, audit) behind a single control plane with role-based access control (RBAC) and a zero-trust posture, while staying lightweight enough to ship as a single Windows executable for local/dev or small-team use.

## 2. Goals

| # | Goal | Success Metric |
|---|---|---|
| G1 | Centralize API key issuance/revocation across services | One action revokes a key everywhere it's scoped |
| G2 | Enforce per-service, per-key rate limits | Configurable policy, enforced before request reaches the service |
| G3 | Immutable audit trail of all privileged actions | 100% of key/role/policy changes logged, queryable |
| G4 | Reduce unauthorized access incidents | Target: meaningful, measured reduction post-deployment (baseline must be captured — see §9) |
| G5 | Integrate with existing CI/CD without slowing deploys | Webhook-based key provisioning/rotation, <X s added to pipeline |
| G6 | Ship as a single Windows-native app, no separate install of Python/Node | One `.exe` (or installer), runs offline against local SQLite |

## 3. Non-Goals (for v1)

- Multi-tenant SaaS hosting (this is a self-hosted / single-org tool for v1)
- Cross-platform GUI (macOS/Linux GUI is out of scope for v1; backend logic stays portable for later)
- Full API gateway / reverse proxy functionality (this is a *control plane*, not a gateway — services call out to it or are instrumented to honor its policies, it doesn't sit inline as a proxy in v1)
- Distributed tracing across third-party/external services — tracing covers services we control/instrument

## 4. Target Users

- **Platform/DevOps engineers** managing API keys and rate limits for internal microservices
- **Security/compliance reviewers** who need an audit trail
- **Developers** who need to self-serve a scoped key for local dev or CI without filing a ticket

## 5. Core Features

### 5.1 API Key Management
- Issue keys scoped to one or more services
- Set expiry, auto-rotation interval
- Revoke instantly (propagates to anywhere the key is checked)
- Key metadata: owner, issued-by, last-used timestamp, scopes

### 5.2 Rate Limiting
- Define rate-limit policies per service and/or per key
- Policy model: requests per window (configurable algorithm — see Architecture doc for the chosen approach)
- Live view of current consumption vs. limit per key

### 5.3 Audit Logging
- Every privileged action (key created/revoked, role changed, policy edited) is logged immutably
- Searchable/filterable log view: by actor, service, action type, time range
- Export to CSV for compliance reviews

### 5.4 Role-Based Access Control (RBAC)
- Roles: e.g. `admin`, `service-owner`, `auditor`, `developer`
- Permissions are policy-as-data (not hardcoded), so they can be reviewed/audited themselves
- Every action in the system checks against RBAC — no implicit trust by role title alone

### 5.5 Zero-Trust Model
- No request — internal or external — is trusted by default; every request is authenticated and authorized on every call, not just at a perimeter
- Service-to-service calls (e.g. CI/CD webhook) require their own scoped credentials, not a shared admin secret
- See Architecture doc §3 for the concrete implementation of this principle

### 5.6 Live Request Tracing
- Real-time view of requests flowing through instrumented services
- Useful for debugging *and* for spotting anomalous access patterns as they happen

### 5.7 CI/CD Integration
- Webhook endpoint pipelines call to provision/rotate a key as part of deploy
- Sample GitHub Actions workflow included as reference integration

## 6. User Stories (v1 slice)

1. As a service owner, I can create a new API key scoped to my service with a 90-day expiry.
2. As an admin, I can revoke any key immediately and see that revocation reflected in the audit log.
3. As a developer, I can view my own keys and their current rate-limit consumption.
4. As an auditor, I can filter the audit log by actor and date range and export results.
5. As a DevOps engineer, I can configure a GitHub Actions step that rotates a service's key on every deploy without manual intervention.
6. As anyone investigating an incident, I can see a live trace of requests hitting a given service in the minutes before/after a flagged event.

## 7. Out-of-Scope Risks / Open Questions

- What counts as an "unauthorized access incident" needs a precise, measurable definition before any reduction percentage can be claimed (see §9).
- Decide now: is the local SQLite store ever expected to sync/replicate, or is each install fully standalone? (Affects whether multi-instance deployments are even meaningful for v1.)
- WebView2 runtime dependency on end-user machines (see Architecture doc, Packaging section) — decide target machine assumptions before Phase 6.

## 8. Tech Stack (locked)

| Layer | Choice |
|---|---|
| GUI | NiceGUI, run in native mode (`ui.run(native=True)`, via pywebview/WebView2) |
| Backend logic | FastAPI in-process (NiceGUI is built on FastAPI — single app instance) |
| External API surface | Same FastAPI app, additional routes for CI/CD webhook + key-management API |
| Database | SQLite (embedded, file-based) |
| Auth/RBAC | Casbin (policy-as-data) or custom RBAC layer — decision pending in Architecture doc |
| Tracing | OpenTelemetry → in-process queue → NiceGUI live view |
| Packaging | PyInstaller, single Windows executable |

Full rationale for each choice lives in `ARCHITECTURE.md`.

## 9. Measuring Success Honestly

Before claiming any "% reduction in unauthorized access incidents," define:
- What qualifies as an incident (failed auth attempt? successful unauthorized access? anomalous rate-limit breach?)
- A baseline measurement window *before* this tool is deployed
- The same measurement taken *after* deployment, over a comparable window

Without this, the metric is a claim, not a result. Treat Phase 7 (Hardening & Metrics) in the dev plan as where this baseline gets captured.

## 10. Milestones (maps to development phases)

| Phase | Deliverable |
|---|---|
| 0 | This PRD + Architecture doc, repo scaffold |
| 1 | Data models, auth, RBAC engine |
| 2 | Key + rate-limit management (CRUD + enforcement) |
| 3 | Live request tracing |
| 4 | NiceGUI dashboard |
| 5 | CI/CD webhook integration |
| 6 | Windows packaging (PyInstaller) |
| 7 | Hardening, baseline metrics capture |
