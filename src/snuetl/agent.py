from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def envelope(
    command: str,
    *,
    data: Any = None,
    ok: bool = True,
    warnings: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "command": command,
        "data": _json_value(data),
        "warnings": _json_value(warnings or []),
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            **_json_value(meta or {}),
        },
    }
    if error is not None:
        result["error"] = _json_value(error)
    return result


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def emit_error(command: str, code: str, message: str, remediation: str) -> None:
    emit(
        envelope(
            command,
            ok=False,
            error={"code": code, "message": message, "remediation": remediation},
        )
    )
