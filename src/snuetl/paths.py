from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SEPARATORS = re.compile(r"[/\\]+")
_WHITESPACE = re.compile(r"\s+")


def sanitize_component(value: str, *, fallback: str = "unnamed", limit: int = 120) -> str:
    value = unicodedata.normalize("NFC", value)
    value = _CONTROL.sub("", value)
    value = _SEPARATORS.sub("_", value)
    # Leading/trailing dots are ambiguous path components on Unix and Windows
    # (for example ".." and hidden files), so normalize them away.
    value = _WHITESPACE.sub(" ", value).strip().strip(".")
    if value in {"", ".", ".."}:
        value = fallback
    if len(value) > limit:
        value = value[:limit].rstrip(" .") or fallback
    return value


def course_content_dir(
    root: Path,
    course_name: str,
    course_id: str,
    semester_code: str | None,
    category: str,
) -> Path:
    semester = sanitize_component(semester_code or "unknown-semester")
    course = sanitize_component(f"{course_name}--{course_id}", fallback=f"course--{course_id}")
    content_category = sanitize_component(category, fallback="files")
    return root / semester / course / content_category


def destination_path(
    root: Path,
    course_name: str,
    course_id: str,
    folder_path: tuple[str, ...],
    filename: str,
    remote_id: str,
    is_claimed: Callable[[Path], bool] | None = None,
    *,
    semester_code: str | None = None,
    category: str = "files",
) -> Path:
    # Keep the historical helper default for external callers. Core snuetl
    # supplies semester_code and uses the managed 0.7 hierarchy.
    if semester_code is None and category == "files":
        base = root / sanitize_component(f"{course_name}-{course_id}")
    else:
        base = course_content_dir(root, course_name, course_id, semester_code, category)
    folders = [sanitize_component(part) for part in folder_path if part not in {"", "/"}]
    leaf = sanitize_component(filename, fallback=f"file-{remote_id}")
    candidate = base.joinpath(*folders, leaf)
    if is_claimed and is_claimed(candidate):
        stem, suffix = Path(leaf).stem, Path(leaf).suffix
        candidate = base.joinpath(*folders, f"{stem}__{sanitize_component(remote_id)}{suffix}")

    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved != candidate_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError("generated path escapes the download directory")
    return candidate
