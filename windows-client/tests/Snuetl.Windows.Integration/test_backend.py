"""Offline content fixture; never reads accounts or contacts SNU eTL."""
import json
import os
import sys
import tempfile

for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    result = {}
    if method == "content.hydrate":
        os.makedirs(os.environ["SNUETL_WINDOWS_DATA_DIR"], exist_ok=True)
        fd, name = tempfile.mkstemp(dir=os.environ["SNUETL_WINDOWS_DATA_DIR"])
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"Preserve my course notes.\n")
        result = {"path": name, "size": 26, "sha256": "fixture"}
    print(json.dumps({"id": request["id"], "ok": True, "result": result}), flush=True)
    if method == "shutdown":
        break
