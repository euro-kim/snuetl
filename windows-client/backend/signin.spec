# Build from the repository root with Python 3.12 and PyInstaller.
from pathlib import Path

root = Path(SPEC).resolve().parents[2]
backend = root / "windows-client" / "backend"

a = Analysis(
    [str(backend / "signin_entry.py")],
    pathex=[str(root / "src"), str(backend)],
    binaries=[],
    datas=[],
    hiddenimports=["keyring.backends.Windows", "tzdata"],
    hookspath=[],
    excludes=["discord", "telegram", "yt_dlp", "pytest", "tkinter", "PIL", "numpy", "keyring.backends.macOS", "keyring.backends.SecretService"],
    noarchive=False,
)
# Playwright's generic hook also collects browsers previously installed in the
# build venv. Only the explicitly selected headed Chromium is copied by build.ps1.
a.datas = [item for item in a.datas if ".local-browsers" not in item[0]]
a.binaries = [item for item in a.binaries if ".local-browsers" not in item[0]]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="snuetl-signin",
    # The parent redirects these streams and uses CreateNoWindow to hide the worker.
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="snuetl-signin",
)
