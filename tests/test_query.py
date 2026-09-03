from pathlib import Path

import pytest

from snuetl.models import ContentItem, Course, RemoteFile, Semester
from snuetl.query import QueryError, execute_query
from snuetl.state import StateStore
from snuetl.ui import print_query_result


def _seed_catalog(path: Path) -> None:
    semester = Semester("164", "2026-2", "2026년 2학기", 2026)
    course = Course(
        "101",
        "2026-2 Database Systems",
        "https://lms.test/courses/101",
        course_code="DB101",
        semester=semester,
    )
    remote = RemoteFile(
        "501",
        "101",
        "week1.pdf",
        ("Slides",),
        "https://lms.test/files/501/download",
        123,
        "2026-09-01",
    )
    announcement = ContentItem(
        "701",
        "101",
        "announcement",
        "Welcome",
        "https://lms.test/a/701",
        published_at="2026-08-31",
    )
    with StateStore(path) as store, store.transaction():
        store.upsert_course(course)
        store.replace_catalog_files(course, [remote])
        store.replace_catalog_content(
            course,
            ("announcement", "page"),
            [announcement],
            scope="articles",
        )


def test_queries_canonical_views(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _seed_catalog(path)
    result = execute_query(
        path,
        """SELECT semester_code, course_id, course_name, file_id, file_name,
                  folder_path, size_bytes, download_status
             FROM files""",
    )
    assert result.columns == (
        "semester_code",
        "course_id",
        "course_name",
        "file_id",
        "file_name",
        "folder_path",
        "size_bytes",
        "download_status",
    )
    assert result.rows == (
        ("2026-2", "101", "Database Systems", "501", "week1.pdf", "Slides", 123, "remote"),
    )
    announcements = execute_query(path, "SELECT announcement_id, title FROM announcements")
    assert announcements.rows == (("701", "Welcome"),)


def test_query_is_read_only_and_honors_output_limit(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _seed_catalog(path)
    with pytest.raises(QueryError, match="read-only"):
        execute_query(path, "DELETE FROM files")
    result = execute_query(path, "SELECT * FROM files UNION ALL SELECT * FROM files", limit=1)
    assert len(result.rows) == 1
    assert result.truncated is True


def test_machine_readable_query_output_remains_valid_when_truncated(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "state.db"
    _seed_catalog(path)
    result = execute_query(
        path,
        """SELECT course_id, file_name FROM files
           UNION ALL SELECT course_id, file_name FROM files""",
        limit=1,
    )
    print_query_result(result, "json")
    captured = capsys.readouterr()
    assert '"course_id": "101"' in captured.out
    assert "Output limit reached" not in captured.out
    assert "Output limit reached" in captured.err
