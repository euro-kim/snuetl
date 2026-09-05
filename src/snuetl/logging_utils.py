from __future__ import annotations

import logging
import re
from types import TracebackType
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_SECRET = re.compile(
    r"(?i)(password|passwd|verification[_ -]?code|authorization|cookie|token)\s*[:=]\s*([^\s,;]+)"
)


def redact(value: object) -> str:
    text = str(value)
    text = _SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)

    def strip_query(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,;)")
        suffix = match.group(0)[len(raw) :]
        try:
            parts = urlsplit(raw)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) + suffix
        except ValueError:
            return "[REDACTED_URL]"

    return _URL.sub(strip_query, text)


def redacted_exc_info(
    error: BaseException,
) -> tuple[type[BaseException], BaseException, TracebackType | None]:
    """Keep traceback frames while preventing the formatter from restoring secret text."""
    safe = RuntimeError(f"{type(error).__name__}: {redact(error)}")
    return type(safe), safe, error.__traceback__


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
