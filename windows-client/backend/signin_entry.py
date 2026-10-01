"""Optional browser setup process. Never imported by the core client."""
import os, sys, json
from pathlib import Path
from snuetl import codex_desktop, __version__
codex_desktop.data_dir = lambda: Path(os.environ.get("SNUETL_WINDOWS_DATA_DIR", str(Path(os.environ["LOCALAPPDATA"]) / "SNUETL")))
codex_desktop.SERVICE = codex_desktop.PURPOSE = "snuetl-windows"
import windows_auth
windows_auth.data_dir = codex_desktop.data_dir
codex_desktop._save_token = windows_auth._save_token
if "--self-test" in sys.argv:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        with runtime.chromium.launch(headless=True, channel="chromium") as browser:
            page = browser.new_page()
            page.goto("about:blank")
            assert page.title() == ""
    print(json.dumps({"browser_ready": True, "version": __version__}))
    sys.exit(0)
try:
    codex_desktop._browser_operation(rotate=False, disconnect=False)
except Exception:
    sys.exit(1)
