import sys

# Frozen Windows applications can ignore PYTHONIOENCODING. Configure the actual
# protocol streams before serve() captures them as default arguments.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if stream is not None:
        stream.reconfigure(encoding="utf-8")

from snuetl_windows_backend import serve  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(serve())
