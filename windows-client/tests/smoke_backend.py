"""Exercise the frozen worker's real redirected stdio without an eTL account."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[1]).resolve(strict=True)
    requests = [
        {"id": 1, "method": "ping"},
        {"id": 2, "method": "no.such.method-한글"},
        {"id": 3, "method": "capabilities"},
        {"id": 4, "method": "auth.auto"},
        {"id": 5, "method": "auth.status"},
        {"id": 6, "method": "shutdown"},
    ]
    with tempfile.TemporaryDirectory(prefix="snuetl-smoke-") as data_dir:
        result = subprocess.run(
            [str(executable)],
            input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "SNUETL_WINDOWS_DATA_DIR": data_dir},
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"Packaged backend exited {result.returncode}: {result.stderr}")
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 6, (responses, result.stderr)
    assert responses[0] == {"id": 1, "ok": True, "result": {"protocol_version": 1}}
    assert responses[1]["id"] == 2 and not responses[1]["ok"]
    assert responses[1]["error"]["code"] == "UNKNOWN_METHOD"
    assert "한글" in responses[1]["error"]["message"]
    assert responses[2]["result"]["browser_free"] is True
    assert responses[2]["result"]["automatic_signin"] is False
    assert responses[3]["error"]["code"] == "OPTIONAL_COMPONENT_MISSING"
    assert responses[4]["result"]["configured"] is False
    assert responses[5] == {"id": 6, "ok": True, "result": {"shutdown": True}}
    assert not list(executable.parent.rglob("*playwright*"))
    assert not list(executable.parent.rglob("chrome.exe"))
    assert not list(executable.parent.rglob("node.exe"))
    print("Packaged backend smoke test passed (ping, capability, core-only auth, missing add-on, shutdown, no browser payload).")


if __name__ == "__main__":
    main()
