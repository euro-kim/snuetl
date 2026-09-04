from pathlib import Path

from snuetl.models import Course
from snuetl.puller import PullSummary, _managed_write
from snuetl.state import StateStore


def test_managed_write_preserves_local_edits(tmp_path: Path) -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    destination = tmp_path / "article.md"
    summary = PullSummary()
    with StateStore(tmp_path / "state.db") as store, store.transaction():
        store.upsert_course(course)
        _managed_write(
            store,
            summary,
            artifact_type="article",
            course=course,
            source_id="page:1",
            destination=destination,
            body=b"remote one",
            source_revision="one",
            force=False,
        )
    destination.write_bytes(b"my local edit")
    with StateStore(tmp_path / "state.db") as store, store.transaction():
        conflict = _managed_write(
            store,
            summary,
            artifact_type="article",
            course=course,
            source_id="page:1",
            destination=destination,
            body=b"remote two",
            source_revision="two",
            force=False,
        )
    assert destination.read_bytes() == b"my local edit"
    assert conflict.read_bytes() == b"remote two"
    assert conflict != destination
    assert summary.conflicts == 1
