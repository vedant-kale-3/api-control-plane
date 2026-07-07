# PyInstaller spec — start with --onedir for development (faster iteration,
# easier to inspect what's bundled). Switch to --onefile only once the
# --onedir build is verified working on a clean Windows VM
# (ARCHITECTURE.md, Packaging Notes).
#
# Build with: pyinstaller build/app.spec

block_cipher = None

a = Analysis(
    ['../app/main.py'],
    pathex=['..'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'nicegui',
        'pywebview',
        # Deferred import inside app/db.py:init_db() — PyInstaller's graph walk
        # never enters function bodies, so these modules are invisible to it.
        'app.models',
        'app.models.service',
        'app.models.api_key',
        'app.models.rate_limit',
        'app.models.audit_log',
        'app.models.rbac',
        'app.models.trace',
        # Deferred imports inside app/services/rbac_service.py (add_role /
        # add_permission) that break a circular dependency at module load time.
        'app.services.audit_service',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='APIControlPlane',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='APIControlPlane',
)
