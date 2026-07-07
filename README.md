# API Control Plane

See `docs/PRD.md` and `docs/ARCHITECTURE.md` for the full spec — those two
files are the source of truth for conventions in this repo.

## Local dev setup (Windows)

1. Install Python 3.11+ (3.12 recommended) — check "Add Python to PATH" during install.
2. Create and activate a virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt -r requirements-dev.txt
   ```
4. Run the app:
   ```
   python -m app.main
   ```
   This creates the SQLite DB under `%LOCALAPPDATA%\APIControlPlane\data.db`,
   seeds default RBAC roles, and opens the native NiceGUI window.

## Run the sample instrumented service (for tracing dev, optional)

```
python -m sample_service.main
```

## Run tests

```
pytest
```

## Package as a Windows exe (Phase 6)

Packaging must use a **clean venv** that contains only the runtime deps so
`pyinstaller` itself is never frozen into the exe:

```
python -m venv .venv-package
.venv-package\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller>=6.10
pyinstaller build/app.spec
```

> **Why a separate venv?** `pyinstaller` and `pytest` now live in
> `requirements-dev.txt`, not `requirements.txt`. A packaging venv that only
> installs `requirements.txt` guarantees those tools cannot be accidentally
> pulled into the frozen bundle.

Output lands in `dist/APIControlPlane/`. Test on a clean Windows VM
before distributing — see `docs/ARCHITECTURE.md` Packaging Notes for the
WebView2 dependency decision you need to make first.
