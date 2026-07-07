# PyInstaller Packaging Audit — API Control Plane

> Audit date: 2026-07-08
> Auditor: Antigravity (static analysis — no code was modified)
> Spec file examined: `build/app.spec`

---

## 1. Runtime Dependencies & Hidden-Import Flags

All packages come from `requirements.txt`. None has a `pyproject.toml`.

| Package | Version Pin | Hidden-Import / Hook Risk | Notes |
|---|---|---|---|
| `fastapi` | `>=0.115` | 🟡 **Medium** — FastAPI relies on Starlette routing internals; `anyio`, `starlette.middleware`, and `starlette.routing` can be missed | Needs `--hidden-import starlette` and often `anyio._backends._asyncio` |
| `httpx` | `>=0.27` | 🟡 **Medium** — async back-end (`httpcore`, `h11`) may not be pulled in | Add `--hidden-import httpcore`, `--hidden-import h11` |
| `nicegui[native]` | `>=2.14` | 🔴 **High** — ships its own static web assets (JS/CSS bundles) inside the package directory; PyInstaller will **not** collect them automatically | See §2 below — `nicegui/static` and `nicegui/templates` must be in `datas` |
| `sqlmodel` | `>=0.0.22` | 🟡 **Medium** — wraps SQLAlchemy; SQLAlchemy dialects are loaded dynamically | Add `--hidden-import sqlalchemy.dialects.sqlite` |
| `pywebview` | `>=5.3` | 🔴 **High** — uses platform-specific back-ends (`edgechromium` on Windows); binary DLLs must be collected; `--hidden-import webview.platforms.winforms` required | Also needs `pythonnet` / `clr` DLLs if the `winforms` back-end is active |
| `pyjwt` | `>=2.9` | 🟢 **Low** — pure Python; PyInstaller handles it. **If `cryptography` is ever added** as an optional PyJWT dep, `--hidden-import cryptography.hazmat.backends.openssl` will be required | Currently no `cryptography` package listed — safe for now |
| `python-multipart` | `>=0.0.9` | 🟢 **Low** — pure Python form parsing; no special hooks needed | — |
| `opentelemetry-api` | `>=1.27` | 🟡 **Medium** — entry-points-based exporter registry uses `importlib.metadata`; exporter back-ends not listed in requirements won't be auto-collected | Currently no exporter listed — risk is low; add if exporters are added later |
| `opentelemetry-sdk` | `>=1.27` | 🟡 **Medium** — same entry-point mechanism as above | — |
| `pyinstaller` | `>=6.10` | — (dev/build only, not a runtime dep) | Should be moved to a `requirements-dev.txt` to avoid bundling the packager itself |
| `pytest` | `>=8.3` | — (test only, not a runtime dep) | Same as above — must not be present in the production venv during packaging |

### Priority Action Items — Hidden Imports

The current `build/app.spec` `hiddenimports` list is:

```python
hiddenimports=['nicegui', 'pywebview'],
```

This is **incomplete**. Minimum additions required before a working build:

```python
hiddenimports=[
    # NiceGUI / Starlette / ASGI layer
    'nicegui',
    'starlette',
    'starlette.middleware.cors',
    'starlette.routing',
    'starlette.staticfiles',
    'anyio',
    'anyio._backends._asyncio',

    # HTTP client
    'httpcore',
    'h11',

    # SQLAlchemy / SQLModel
    'sqlalchemy.dialects.sqlite',

    # WebView (Windows)
    'pywebview',
    'webview.platforms.winforms',

    # OpenTelemetry (no exporters yet — re-audit when added)
    'opentelemetry.sdk.trace',
    'opentelemetry.sdk.trace.export',
],
```

---

## 2. Non-Python Data Files

### 2a. Files owned by this repository

A full recursive scan of the project tree (excluding `.venv`) found **zero** non-Python data files committed to the repository:

| Category | Status |
|---|---|
| `.env` / secrets | ✅ None committed (correct — env vars set at runtime via `LOCALAPPDATA` / OS env) |
| Config YAML / INI | ✅ None |
| SQLite database | ✅ Not committed — created at runtime under `%LOCALAPPDATA%\APIControlPlane\data.db` by `app/db.py` |
| HTML templates | ✅ None (NiceGUI generates UI programmatically; no Jinja templates authored in this repo) |
| Static assets (icons, images, CSS, JS) | ✅ None authored in this repo |
| Docs (`.md`) | ✅ Docs only — not needed at runtime |

**Conclusion:** There are **no application-owned data files** that need to be added to `datas` in the spec.

### 2b. Data files owned by *installed packages* (critical for PyInstaller)

These live inside site-packages and **must** be explicitly added to `datas` because PyInstaller does not collect them automatically:

| Package | Path inside site-packages | Destination in bundle | Risk |
|---|---|---|---|
| `nicegui` | `nicegui/static/` (JS, CSS, fonts, favicon) | `nicegui/static` | 🔴 **Critical** — NiceGUI serves its own UI assets over its internal HTTP server; missing this causes a blank window |
| `nicegui` | `nicegui/templates/` (Jinja2 HTML shell) | `nicegui/templates` | 🔴 **Critical** — same reason |
| `pywebview` | `webview/lib/` (Windows EdgeChromium DLLs on Windows) | `webview/lib` | 🔴 **Critical** — native window will not open without these |
| `opentelemetry-sdk` | Entry-point metadata | (collected by hook or manually) | 🟡 Low currently; escalates when exporters are added |

**Required addition to `build/app.spec` `datas` list:**

```python
from PyInstaller.utils.hooks import collect_data_files

datas = (
    collect_data_files('nicegui')        # static/ + templates/
    + collect_data_files('pywebview')    # webview/lib/ DLLs on Windows
)
```

Then pass this variable into `Analysis(datas=datas, ...)`.

---

## 3. Dynamic Imports — PyInstaller Static-Analysis Gaps

### 3a. Deferred import in `app/db.py` (line 19)

```python
def init_db():
    from app import models  # noqa: F401  ensures all tables are registered first
    SQLModel.metadata.create_all(engine)
```

This is a **deferred (lazy) import** inside a function body. PyInstaller's static analyser will
**not** follow it during the `Analysis` phase.

The import resolves to `app/models/__init__.py` which re-exports all six model modules
(`Service`, `ApiKey`, `RateLimitPolicy`, `AuditLogEntry`, `Role`, `Permission`, `TraceSpan`).
Since those are all concrete files in the `app/` package they _will_ be found via package scanning
— but only if `app` is properly included via `pathex`. Verify that `pathex=['..']` correctly
resolves to the directory containing the `app/` package.

**Risk level:** 🟡 Medium — likely works, but depends on `pathex` correctness.

### 3b. Circular-break deferred imports in `app/services/rbac_service.py` (lines 54, 85)

```python
# Inside add_role() and add_permission()
from app.services.audit_service import log_action
```

These are inside function bodies to break a circular import at module load time. PyInstaller will
miss these during its dependency graph walk.

**Risk level:** 🟡 Medium — `audit_service` *will* be included because it is a concrete module
under `app/services/`, but an explicit hidden import is safer:

```python
hiddenimports=['app.services.audit_service'],
```

### 3c. SQLAlchemy dialect auto-discovery

SQLAlchemy (used by SQLModel) uses an entry-point / `importlib.metadata` registry for dialect loading:

```
create_engine("sqlite:///...") → sqlalchemy.dialects.sqlite → pysqlite → _sqlite3 (C extension)
```

The `_sqlite3` CPython extension (`.pyd` on Windows) and `sqlalchemy.dialects.sqlite` must both be
present in the bundle. PyInstaller ships a SQLAlchemy hook that covers this, but it is not
explicitly referenced in `hookspath` in the current spec.

**Risk level:** 🟡 Medium — add `--hidden-import sqlalchemy.dialects.sqlite` as a safeguard.

### 3d. OpenTelemetry exporter registry

`opentelemetry-sdk` registers exporters via `importlib.metadata` entry points. No exporter is
currently installed, so no dynamic load will occur at runtime. **If** an OTLP or console exporter
is added later it must be added to `hiddenimports`.

**Risk level:** 🟢 Low (current state).

### 3e. No `importlib.import_module`, `__import__`, or plugin loader found

A full codebase scan confirmed **zero** uses of:
- `importlib.import_module`
- `__import__()`
- Any plugin discovery pattern (file globs, entry-point scanning by the app itself)

---

## 4. Entry Point Analysis

### ✅ Single, Clear Entry Point Confirmed

| File | Role |
|---|---|
| `app/main.py` | **Primary entry point** — `if __name__ == "__main__"` guard; calls `bootstrap()` then `run()` |
| `bootstrap()` | Runs `init_db()`, `seed_default_roles()`, mounts FastAPI router onto the NiceGUI app object |
| `run()` in `app/gui/main.py` | Registers all NiceGUI pages, then calls `ui.run(native=True, …, reload=False)` which starts both the embedded Uvicorn/Starlette server **and** the pywebview native window in a single blocking call |

**No separate FastAPI/Uvicorn subprocess is spawned.** The entire backend and GUI run in-process
via NiceGUI's `native=True` mode. This is the ideal topology for a PyInstaller single-exe
deployment.

The current spec correctly points to `'../app/main.py'` as the analysis target.

> **Note:** `console=False` in the spec suppresses the console window (correct for a GUI app).
> During initial packaging debugging, temporarily flip this to `console=True` so Python
> tracebacks are visible on startup failure.

### Entry Point — One Gap

The `app/main.py` docstring notes `python -m app.main` usage, implying the CWD is the project
root. PyInstaller changes the CWD to the `_MEIPASS` temp dir when frozen. The DB path in
`app/db.py` is correctly written to `%LOCALAPPDATA%\APIControlPlane\data.db` (not relative to
CWD), so **the DB is safe**. Any future relative file access added to the codebase must use
`sys._MEIPASS` or `Path(__file__).parent` equivalents rather than bare relative paths.

---

## 5. Summary Risk Table

| Area | Risk | Action Required |
|---|---|---|
| `hiddenimports` list in spec | 🔴 High | Expand — see §1 |
| NiceGUI `static/` + `templates/` not in `datas` | 🔴 Critical | Add `collect_data_files('nicegui')` — see §2b |
| pywebview Windows DLLs not in `datas` | 🔴 Critical | Add `collect_data_files('pywebview')` — see §2b |
| Deferred `from app import models` in `db.py` | 🟡 Medium | Verify `pathex` correctness; consider explicit `hiddenimport` |
| Circular-break deferred imports in `rbac_service.py` | 🟡 Medium | Add `--hidden-import app.services.audit_service` |
| SQLAlchemy sqlite dialect | 🟡 Medium | Add `--hidden-import sqlalchemy.dialects.sqlite` |
| No `.env` / config / static assets in repo | ✅ None | Nothing to add to `datas` for app-owned files |
| No `importlib` / plugin loading | ✅ None | No action needed |
| Entry point (`app/main.py`) | ✅ Clear | Spec target is correct |
| DB written to `%LOCALAPPDATA%` | ✅ Safe | Correct frozen-path pattern already in place |
| `pytest` + `pyinstaller` in runtime requirements | 🟡 Low | Move to `requirements-dev.txt` |

---

## 6. Recommended Next Steps (in order)

1. **Split requirements** — move `pyinstaller` and `pytest` to `requirements-dev.txt` so they are
   never accidentally bundled into the frozen app.
2. **Expand `hiddenimports`** in `build/app.spec` per the list in §1.
3. **Add `datas`** for `nicegui` and `pywebview` using `collect_data_files()` per §2b.
4. **Build and test in `--onedir` mode** (already the spec default — keep this until stable):
   ```powershell
   pyinstaller build/app.spec
   ```
5. **Smoke-test on a clean Windows VM** (no Python installed) by launching
   `dist/APIControlPlane/APIControlPlane.exe` and verifying all five GUI pages load and the
   `/api/keys` endpoint is reachable via `curl`.
6. **Only then** switch to `--onefile` if a single-file distribution is required.
