# Build from the repository root with Python 3.12 and PyInstaller.
from pathlib import Path

root = Path(SPEC).resolve().parents[2]
backend = root / "windows-client" / "backend"

a = Analysis(
    [str(backend / "entry.py")],
    pathex=[str(root / "src"), str(backend)],
    binaries=[],
    datas=[],
    hiddenimports=["keyring.backends.Windows", "tzdata"],
    hookspath=[],
    excludes=["discord", "telegram"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="snuetl-windows-backend",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="snuetl-windows-backend",
)
