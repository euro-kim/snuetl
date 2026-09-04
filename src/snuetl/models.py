from __future__ import annotations

import re
from dataclasses import dataclass

SEMESTER_NUMBER = re.compile(r"(?P<year>\d{4})\s*년\s*(?P<number>\d+)\s*학기")
SEMESTER_YEAR = re.compile(r"(?P<year>\d{4})\s*년")


@dataclass(frozen=True, slots=True)
class Semester:
    semester_id: str
    semester_code: str
    semester_name: str
    academic_year: int | None = None
    starts_at: str | None = None
    ends_at: str | None = None


def semester_from_term(value: object) -> Semester | None:
    if not isinstance(value, dict) or value.get("id") is None:
        return None
    semester_id = str(value["id"])
    semester_name = str(value.get("name") or f"Semester {semester_id}").strip()
    numbered = SEMESTER_NUMBER.search(semester_name)
    if numbered:
        academic_year = int(numbered.group("year"))
        semester_code = f"{academic_year}-{int(numbered.group('number'))}"
    else:
        year_match = SEMESTER_YEAR.search(semester_name)
        academic_year = int(year_match.group("year")) if year_match else None
        semester_code = "SNUON" if "SNUON" in semester_name.upper() else semester_name
    return Semester(
        semester_id=semester_id,
        semester_code=semester_code,
        semester_name=semester_name,
        academic_year=academic_year,
        starts_at=str(value.get("start_at") or "") or None,
        ends_at=str(value.get("end_at") or "") or None,
    )


@dataclass(frozen=True, slots=True)
class Course:
    remote_id: str
    name: str
    url: str
    course_code: str | None = None
    semester: Semester | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    syllabus_html: str | None = None

    @property
    def display_name(self) -> str:
        """Course title without a redundant leading semester code."""
        if self.semester:
            prefix = f"{self.semester.semester_code} "
            if self.name.startswith(prefix):
                return self.name[len(prefix) :]
        return self.name


@dataclass(frozen=True, slots=True)
class RemoteFile:
    remote_id: str
    course_id: str
    name: str
    folder_path: tuple[str, ...]
    download_url: str
    size: int | None = None
    updated_at: str | None = None
    etag: str | None = None
    content_type: str | None = None

    @property
    def revision(self) -> str:
        """Metadata signature used before a body checksum is available."""
        return "\x1f".join(
            (
                self.updated_at or "",
                str(self.size) if self.size is not None else "",
                self.etag or "",
            )
        )

    @property
    def display_path(self) -> str:
        return "/".join((*self.folder_path, self.name))


@dataclass(frozen=True, slots=True)
class StoredFile:
    remote_id: str
    course_id: str
    remote_path: str
    local_path: str
    size: int | None
    updated_at: str | None
    etag: str | None
    sha256: str | None
    revision: str
    status: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    size: int
    sha256: str
    etag: str | None


@dataclass(frozen=True, slots=True)
class ContentItem:
    remote_id: str
    course_id: str
    kind: str
    title: str
    url: str
    published_at: str | None = None
    due_at: str | None = None
    updated_at: str | None = None
    body_html: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleItem:
    module_id: str
    module_name: str
    remote_id: str
    course_id: str
    item_type: str
    title: str
    position: int | None = None
    content_id: str | None = None
    html_url: str | None = None
    external_url: str | None = None
    published: bool = True
    locked: bool = False


@dataclass(slots=True)
class SyncSummary:
    courses: int = 0
    downloaded: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    @property
    def changed(self) -> int:
        return self.downloaded + self.updated
