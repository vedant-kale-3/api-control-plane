# API Control Plane

See `docs/PRD.md` and `docs/ARCHITECTURE.md` for the full spec — those two
files are the source of truth for conventions in this repo.

## Local dev setup (Windows)

1. Install Python 3.12+ — check **"Add Python to PATH"** during install.
   > The project has been verified on Python 3.14. PyInstaller 6.21 supports 3.14.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt -r requirements-dev.txt
   ```
4. Run the app — **must be run from the repo root** (`api-control-plane\`):
   ```powershell
   # Make sure the venv is activated AND CWD is api-control-plane\
   python -m app.main
   ```
   > `python -m app.main` only works when the working directory contains the
   > `app\` package folder. Running it from a parent directory will produce
   > `ModuleNotFoundError: No module named 'app'`.

   This creates the SQLite DB at `%LOCALAPPDATA%\APIControlPlane\data.db`,
   seeds default RBAC roles, and opens the native NiceGUI window.

## Run the sample instrumented service (optional, for tracing dev)

```powershell
python -m sample_service.main
```

## Run tests

```powershell
pytest
```

## Package as a Windows exe

Use the `build.ps1` script at the repo root. It handles cleaning, building,
and smoke-testing in one step:

```powershell
# Full build + smoke test (recommended)
.\build.ps1

# Build only (no smoke test)
.\build.ps1 -SkipSmokeTest

# Smoke test only — skip rebuilding
.\build.ps1 -SkipBuild

# Give a slow machine more startup time
.\build.ps1 -SmokeTimeoutSeconds 120
```

The script **auto-detects `.venv\Scripts\pyinstaller.exe`** — no need to
activate a separate packaging venv first.

### What the smoke test checks

| Probe | Expected | What it proves |
|---|---|---|
| `GET /` | HTTP 200 | NiceGUI / Uvicorn started successfully |
| `GET /api/keys` | HTTP 401 | FastAPI router is mounted; auth is enforced |
| `GET /webhook/rotate-key` | HTTP 405 | All POST routes are registered |

The app binds a random ephemeral port each launch (`app\main.py:_find_free_port`).
The script discovers it via `Get-NetTCPConnection` instead of reading stdout
(the exe is built `console=False`).

### Output

```
dist\
  APIControlPlane\
    APIControlPlane.exe     <- launcher stub (run this)
    _internal\              <- all Python modules, DLLs, NiceGUI static files
```

### Known spec gotchas (already fixed in `build\build.spec`)

- **`version_info.txt` path** — use `os.path.join(SPECPATH, 'version_info.txt')`
  inside `EXE()`, not a bare `'build/version_info.txt'` string. PyInstaller
  resolves bare paths relative to the spec file's directory, which doubles
  the `build\` prefix when the spec lives in `build\`.
- **`import os`** — required at the top of the spec for `os.path.join`.

### Distribution requirements

> **Target machines need the WebView2 Runtime** (Edge-based native window).
> It is pre-installed on Windows 10 21H2+ and Windows 11. For older Windows 10,
> ship the WebView2 bootstrapper or require users to install it from
> <https://developer.microsoft.com/en-us/microsoft-edge/webview2/>.

- No Python installation required on the target machine.
- The SQLite DB is auto-created at `%LOCALAPPDATA%\APIControlPlane\data.db`
  on first launch — no manual setup needed.

> **Why no separate packaging venv?** `pyinstaller` and `pytest` live in
> `requirements-dev.txt`. The spec's `excludes=` list explicitly prevents
> them from being bundled even if they are present in the active venv.
> See `build\build.spec` for the full `excludes` list.

