# =============================================================================
# build.spec — PyInstaller ONEDIR spec for API Control Plane
#
# Build command (run from the repo root, with .venv-package active):
#   pyinstaller build/build.spec
#
# Output:  dist/APIControlPlane/APIControlPlane.exe
#
# Development checklist before building:
#   1. Use a CLEAN venv (only requirements.txt, not requirements-dev.txt) so
#      pyinstaller never bundles test/dev packages into the frozen exe.
#   2. Run on a Windows machine — the webview back-end is Windows-only.
#   3. Smoke-test on a clean VM (no Python installed) before distributing.
#      The target machine needs WebView2 Runtime (Edge-based):
#        https://developer.microsoft.com/en-us/microsoft-edge/webview2/
#      It is pre-installed on Win10 21H2+ and Win11; for older Win10 you
#      must ship the WebView2Loader bootstrapper or require users to install it.
#
# Spec format docs: https://pyinstaller.org/en/stable/spec-files.html
# =============================================================================

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# DATA FILES
# collect_data_files() recursively finds all non-.py files inside a package
# and returns a list of (absolute_src_path, dest_relative_dir) tuples that
# PyInstaller places into the bundle's _MEIPASS directory at runtime.
#
# NiceGUI (577 files):
#   nicegui/static/    — JS/CSS/font bundles served by the internal HTTP server.
#   nicegui/templates/ — Jinja2 HTML shell (index.html).
#   nicegui/elements/  — per-element JS/CSS assets (aggrid, codemirror, mermaid…).
#   Without these the native window is blank — white page, no UI.
#
# pywebview / webview (15 files):
#   webview/lib/   — WebBrowserInterop DLLs + WebView2Loader.dll (x64 & x86).
#   webview/js/    — JS bridge scripts injected into the webview at startup.
#   Without these the native window cannot open at all.
# ---------------------------------------------------------------------------
datas = (
    collect_data_files('nicegui')   # 577 files: static/, templates/, elements/
    + collect_data_files('webview') # 15 files:  lib/ DLLs, js/ bridge scripts
)

# ---------------------------------------------------------------------------
# HIDDEN IMPORTS
# PyInstaller resolves imports by static analysis of AST import statements.
# It cannot see:
#   a) Imports inside function bodies (deferred/lazy imports).
#   b) Modules loaded by entry-point registries (importlib.metadata).
#   c) Dynamically-constructed module names.
#
# Each group below lists the root cause and the minimum set of module names
# required.  Using collect_submodules() instead of hand-listing is preferred
# for packages that use internal plugin registries (uvicorn, anyio, starlette).
# ---------------------------------------------------------------------------
hiddenimports = [

    # ------------------------------------------------------------------
    # App-internal deferred imports (function-body imports invisible to AST)
    # ------------------------------------------------------------------
    # app/db.py:init_db() does: from app import models
    # PyInstaller never enters function bodies during the graph walk.
    'app.models',
    'app.models.service',
    'app.models.api_key',
    'app.models.rate_limit',
    'app.models.audit_log',
    'app.models.rbac',
    'app.models.trace',
    # app/services/rbac_service.py:add_role() and add_permission() do:
    #   from app.services.audit_service import log_action
    # (circular-dependency break at module load time)
    'app.services.audit_service',

    # ------------------------------------------------------------------
    # NiceGUI — all 240 submodules
    # collect_submodules() walks the package and includes every sub-package
    # so none of NiceGUI's on-demand element loaders are missed.
    # ------------------------------------------------------------------
    *collect_submodules('nicegui'),

    # ------------------------------------------------------------------
    # Uvicorn — all 40 submodules
    # NiceGUI embeds Uvicorn as its ASGI server.  Uvicorn selects its HTTP
    # and WebSocket protocol classes at runtime based on what is installed:
    #   h11_impl     (always used — h11 is the pure-Python fallback)
    #   httptools_impl (optional C extension — include if installed)
    # collect_submodules() catches all of them so we don't hard-code each one.
    # ------------------------------------------------------------------
    *collect_submodules('uvicorn'),

    # ------------------------------------------------------------------
    # Starlette — all 34 submodules
    # FastAPI is built on Starlette; many Starlette sub-packages (routing,
    # middleware, staticfiles, responses) are imported lazily at first use
    # rather than at package import time.
    # ------------------------------------------------------------------
    *collect_submodules('starlette'),

    # ------------------------------------------------------------------
    # AnyIO — all 43 submodules
    # anyio selects its async back-end at runtime:
    #   anyio._backends._asyncio  (always used by uvicorn)
    #   anyio._backends._trio     (optional — include to avoid ImportError if
    #                              any code tries to detect trio availability)
    # collect_submodules() is safer than hand-listing _asyncio alone.
    # ------------------------------------------------------------------
    *collect_submodules('anyio'),

    # ------------------------------------------------------------------
    # HTTP client stack
    # httpx is used by sample_service and potentially by future features.
    # httpcore and h11 are its transport layer — imported lazily at first
    # request, so not visible to static analysis.
    # ------------------------------------------------------------------
    'httpx',
    'httpcore',
    'httpcore._async',
    'httpcore._sync',
    'h11',
    'h11._readers',
    'h11._writers',
    'h11._connection',
    'h11._events',
    'h11._util',

    # ------------------------------------------------------------------
    # Pydantic v2
    # pydantic_core is a compiled Rust extension; its submodules are not
    # auto-discovered by static analysis.
    # pydantic.v1 is the compatibility shim kept for libraries that import
    # the v1 API — include it to avoid hard-to-diagnose ImportErrors at runtime.
    # ------------------------------------------------------------------
    'pydantic',
    'pydantic.v1',
    'pydantic_core',
    'pydantic_core.core_schema',

    # ------------------------------------------------------------------
    # SQLAlchemy / SQLModel
    # PyInstaller ships a hook for SQLAlchemy (hook-sqlalchemy.py) that
    # handles dialect collection.  We add the sqlite dialect explicitly as a
    # belt-and-suspenders safeguard — if the hook version ever changes, this
    # ensures we never silently lose SQLite support.
    # ------------------------------------------------------------------
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.pysqlite',

    # ------------------------------------------------------------------
    # pywebview platform back-end (Windows)
    # pywebview chooses its GUI back-end at runtime based on the OS and what
    # is installed.  On Windows it uses the 'edgechromium' back-end which
    # relies on pythonnet (clr) and the WebView2Loader DLL collected above.
    # ------------------------------------------------------------------
    'webview',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',

    # ------------------------------------------------------------------
    # OpenTelemetry — SDK internals
    # The SDK registers span processors and exporters via importlib.metadata
    # entry-points, which are invisible to static analysis.  We bundle the
    # core SDK modules; if OTLP/console exporters are added later, their
    # packages must be added here too.
    # ------------------------------------------------------------------
    'opentelemetry.sdk.trace',
    'opentelemetry.sdk.trace.export',
    'opentelemetry.sdk.trace.sampling',
    'opentelemetry.context',
    'opentelemetry.propagators',
    'opentelemetry.propagators.composite',
    'opentelemetry.propagators.textmap',

    # ------------------------------------------------------------------
    # Miscellaneous
    # ------------------------------------------------------------------
    'multipart',                   # python-multipart: FastAPI form parsing
    'jwt',                         # PyJWT: token decode (imported as 'jwt')
    'email.mime.text',             # pulled in by some starlette error handlers
    'email.mime.multipart',
]

# ---------------------------------------------------------------------------
# ANALYSIS
# pathex=['..'] tells PyInstaller that the project root (one level above
# build/) is on sys.path, so "from app.xxx import yyy" resolves correctly.
# ---------------------------------------------------------------------------
a = Analysis(
    ['../app/main.py'],            # single entry point
    pathex=['..'],                 # project root on sys.path
    binaries=[],                   # no custom native binaries; DLLs come via datas
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],                  # no custom hooks needed (all handled above)
    hooksconfig={},
    runtime_hooks=[],              # no runtime hooks needed
    excludes=[
        # Strip out packages that must NEVER be in a production build.
        # They are in requirements-dev.txt, not requirements.txt, but we
        # list them here as an explicit safety net.
        'pytest',
        'pytest_asyncio',
        '_pytest',
        'pyinstaller',
        # Gunicorn is a Linux-only Uvicorn worker host — not needed on Windows.
        'gunicorn',
        # Trio is optional for anyio; we include its _backends module above
        # for compatibility detection but exclude the full package to save ~2 MB.
        'trio',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE
# console=False   — suppresses the console window for the shipped GUI build.
#                   IMPORTANT: flip to True during initial packaging debug so
#                   Python tracebacks are visible on startup failure.
# version=        — injects the Windows VERSIONINFO resource (right-click >
#                   Properties > Details in Explorer, and shown by AV scanners).
# icon=           — .ico must be 256×256 px minimum, multi-size preferred
#                   (16/32/48/64/128/256).  See note below if the file is missing.
# uac_admin=False — do NOT request elevation; the app writes only to
#                   %LOCALAPPDATA% which is always writable without UAC.
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,         # ONEDIR mode: binaries go into the COLLECT step
    name='APIControlPlane',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,                   # keep debug symbols; set True for smaller release
    upx=False,                     # UPX compression off: causes AV false positives
    console=False,                 # GUI-only; no console window in production
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,              # None = same arch as the build machine
    codesign_identity=None,        # set to your cert CN for code-signed releases
    entitlements_file=None,
    # ── Windows version metadata ──────────────────────────────────────────
    version='build/version_info.txt',
    # ── App icon ──────────────────────────────────────────────────────────
    # Provide a multi-size .ico file at build/icon.ico before building.
    # Required sizes: 16x16, 32x32, 48x48, 64x64, 128x128, 256x256 (RGBA).
    # Generate from a PNG with: magick input.png -define icon:auto-resize
    #     "256,128,64,48,32,16" build/icon.ico
    # icon='build/icon.ico',      # <- uncomment once icon.ico is present
    uac_admin=False,
)

# ---------------------------------------------------------------------------
# COLLECT
# Assembles the final dist/APIControlPlane/ directory:
#   APIControlPlane.exe         — the launcher stub
#   _internal/                  — all Python code, DLLs, and data files
#
# The split between EXE and COLLECT is the defining characteristic of
# ONEDIR mode.  Every file in a.binaries / a.datas lands in _internal/,
# keeping the top-level directory clean.
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='APIControlPlane',
)