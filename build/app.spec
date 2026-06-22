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
    hiddenimports=['nicegui', 'pywebview'],
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
