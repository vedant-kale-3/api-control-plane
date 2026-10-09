# API Control Plane — Codebase Documentation

> **Purpose:** This document gives every developer on the team a complete mental model of the API Control Plane project — where things live, what they do, how they connect, and where to make changes. It serves as a companion to `PRD.md` and `ARCHITECTURE.md`.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Directory Structure](#3-directory-structure)
4. [Architecture & Routing](#4-architecture--routing)
5. [Data Models](#5-data-models)
6. [Service Layer](#6-service-layer)
7. [GUI & External APIs](#7-gui--external-apis)
8. [Packaging & Distribution](#8-packaging--distribution)
9. [Developer Conventions](#9-developer-conventions)
10. [Where to Make Changes](#10-where-to-make-changes)

---

## 1. Project Overview

The **API Control Plane** (Web API Safety Dashboard) is a single Python process that exposes:
- A **native desktop GUI** (using NiceGUI in native mode) for interactive management of API keys, rate limits, audit logs, RBAC administration, and a live trace view.
- A **plain HTTP API surface** (using FastAPI) for CI/CD webhooks and external/service-to-service calls.

Because NiceGUI is built directly on FastAPI, the application runs as **one app, one process, and one port**. There is no separate frontend and backend communicating over HTTP for the GUI; GUI event handlers call service-layer Python functions directly in-process.

---

## 2. Tech Stack

| Technology | Role |
|---|---|
| **Python 3.12+** | Core runtime (verified on 3.14) |
| **FastAPI** | HTTP web framework for external routes & underlying GUI server |
| **NiceGUI** | Python-based UI framework running in native mode (pywebview/WebView2) |
| **SQLite** | Local file-based database for persistence |
| **SQLAlchemy / SQLModel** | ORM for database models |
| **PyInstaller** | Packaging the application into a standalone Windows `.exe` |
| **Pytest** | Testing framework |
| **OpenTelemetry** | Tracing and telemetry ingestion |

**Scripts:**
- **Run Dev:** `python -m app.main`
- **Run Tests:** `pytest`
- **Build Exe:** `.\build.ps1` (with optional `-SkipSmokeTest` or `-SkipBuild`)

---

## 3. Directory Structure

```
api-control-plane/
├── app/                          # Main application package
│   ├── models/                   # SQLAlchemy/SQLModel definitions
│   ├── services/                 # Core business logic (no GUI/HTTP imports)
│   │   ├── key_service.py
│   │   ├── rate_limit_service.py
│   │   ├── audit_service.py
│   │   ├── rbac_service.py
│   │   └── trace_service.py
│   ├── gui/                      # NiceGUI components and pages
│   │   ├── pages/                # Individual views (Keys, Rate Limits, etc.)
│   │   └── main.py               # ui.run(native=True) entry point
│   ├── api/
│   │   └── routes.py             # FastAPI external routes (webhooks, key API)
│   ├── tracing/
│   │   └── otel_setup.py         # OpenTelemetry configuration
│   ├── db.py                     # SQLite engine and session setup
│   └── main.py                   # Process entry point (wires GUI + API routes)
│
├── sample_service/               # Dummy instrumented microservice for dev/testing
├── build/
│   └── build.spec                # PyInstaller specification file
├── docs/                         # Project documentation
│   ├── PRD.md
│   └── ARCHITECTURE.md
├── tests/                        # Pytest suite
├── build.ps1                     # PowerShell build and smoke test script
├── requirements.txt              # Prod dependencies
└── requirements-dev.txt          # Dev dependencies (pytest, pyinstaller)
```

---

## 4. Architecture & Routing

The system is designed with a strict **Zero-Trust Implementation**:
1. **Service Layer Isolation:** All business logic lives in `/app/services/`.
2. **Thin Handlers:** GUI code and HTTP route handlers are extremely thin — they solely call into the service layer and format the results. No business logic resides in page functions or route handlers.
3. **Strict Authorization:** Every route (both GUI-triggered calls and HTTP endpoints) re-checks the caller's permission against the RBAC table. There are no "trusted internal calls" that bypass authorization.
4. **Audit Logging:** All actions, including failed authorization attempts, are logged via `audit_service.py`.

```
┌─────────────────────────────────────────────┐
│              Single Python Process            │
│  ┌──────────────┐        ┌──────────────────┐ │
│  │  NiceGUI     │ direct │  Service Layer   │ │
│  │ (native mode)│──────► │  (RBAC, keys,    │ │
│  │              │ calls  │   limits, audit) │ │
│  └──────────────┘        └─────────┬─────────┘ │
│  ┌──────────────┐                  │            │
│  │  FastAPI     │  HTTP    ┌──────▼──────┐    │
│  │  routes:     │◄──────── │   SQLite    │    │
│  │  /webhook    │          │             │    │
│  └──────────────┘          └─────────────┘    │
└─────────────────────────────────────────────┘
```

---

## 5. Data Models

All models are defined in `/app/models/` using SQLAlchemy/SQLModel:

- **`Service`**: `id`, `name`, `description`, `created_at`
- **`ApiKey`**: `id`, `key_hash`, `service_id`, `owner`, `scopes`, `issued_at`, `expires_at`, `revoked_at`, `last_used_at` (Keys are stored hashed; raw keys are shown only once at creation).
- **`RateLimitPolicy`**: `id`, `service_id`, `key_id`, `limit`, `window_seconds`, `algorithm`
- **`AuditLogEntry`**: `id`, `actor`, `action`, `target_type`, `target_id`, `timestamp`, `details` (Immutable append-only).
- **`Role`**: `id`, `name`
- **`Permission`**: `id`, `role_id`, `resource`, `action` (Custom RBAC policy table).
- **`TraceSpan`**: `id`, `service_id`, `trace_id`, `span_id`, `parent_span_id`, `start_time`, `end_time`, `attributes`

---

## 6. Service Layer

The `/app/services/` directory is the heart of the application. It is pure Python with **zero** GUI or HTTP imports.

- **`key_service.py`**: Handles issuing, revoking, rotating, and listing API keys.
- **`rate_limit_service.py`**: Manages policy CRUD and enforces rate limits (implementing a sliding window counter algorithm).
- **`audit_service.py`**: Append-only writer and query/filter API for the audit log.
- **`rbac_service.py`**: Performs permission checks and role/policy CRUD operations.
- **`trace_service.py`**: Ingests OpenTelemetry spans, manages a bounded in-memory ring buffer, and handles pub/sub for the live trace view.

---

## 7. GUI & External APIs

### NiceGUI Native Interface (`/app/gui/`)
- Runs in native mode using pywebview/WebView2.
- Entry config: `ui.run(native=True, window_size=(1280, 800), title="API Control Plane", reload=False)`.
- Uses `ui.aggrid` for complex data tables like Audit Logs and Keys.
- Each major section (Keys, Rate Limits, Audit, RBAC, Traces) has its own module in `/app/gui/pages/`.

### External HTTP Surface (`/app/api/routes.py`)
- **`POST /webhook/rotate-key`**: For CI/CD integrations using scoped service-account keys.
- **`GET / POST / DELETE /api/keys`**: For external tooling needing key management.

---

## 8. Packaging & Distribution

The application is bundled into a standalone Windows `.exe` using PyInstaller, orchestrated by `build.ps1`.

- **Database:** Auto-created on first launch at `%LOCALAPPDATA%\APIControlPlane\data.db`.
- **Runtime:** Requires Microsoft Edge WebView2 on the target machine (pre-installed on Windows 10 21H2+ and Windows 11). No Python installation is needed on target machines.
- **Dynamic Port:** Binds to a random ephemeral port on launch (discovered during smoke tests by `Get-NetTCPConnection`).
- **Output:** Built files are placed in `dist\APIControlPlane\`.

---

## 9. Developer Conventions

- **Virtual Environment:** Always run commands from the repo root (`api-control-plane\`) with the `.venv` activated.
- **Service Layer Purity:** Never import FastAPI request objects or NiceGUI UI elements inside `/app/services/`.
- **GUI Imports:** GUI modules should only import from `/app/services/`, never directly from `/app/models/`. This enforces the service layer as the single source of truth.
- **Audit Immutability:** Never expose an update or delete path for `AuditLogEntry`.
- **Key Security:** Raw API keys are never stored; only their hashes are persisted in the database.

---

## 10. Where to Make Changes

| Task | File / Directory |
|---|---|
| Modify database schema | `/app/models/` |
| Add a new business rule | `/app/services/` (e.g., `key_service.py`) |
| Add a new GUI page | `/app/gui/pages/` and register in `/app/gui/main.py` |
| Expose a new external HTTP API | `/app/api/routes.py` |
| Adjust rate limit algorithms | `/app/services/rate_limit_service.py` |
| Update PyInstaller build process | `build.ps1` and `build/build.spec` |
| Configure OpenTelemetry | `/app/tracing/otel_setup.py` |
| Add tests | `/tests/` |

