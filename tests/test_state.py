from pathlib import Path

from snuetl.models import Course, RemoteFile
from snuetl.state import StateStore


def test_state_round_trip_and_failure_preserves_completed_path(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    course = Course("c1", "Course", "https://lms.test/courses/c1")
    remote = RemoteFile("f1", "c1", "notes.pdf", (), "https://lms.test/files/f1", 5, "today")
    completed = tmp_path / "notes.pdf"
    with StateStore(database) as store:
        with store.transaction():
            store.upsert_course(course)
            store.record_file(remote, completed, sha256="digest", etag="etag", status="ok")
        stored = store.get_file("c1", "f1")
        assert stored is not None
        assert stored.local_path == str(completed)
        with store.transaction():
            store.record_failure(remote, tmp_path / "new.pdf", "network error")
        failed = store.get_file("c1", "f1")
        assert failed is not None
        assert failed.local_path == str(completed)
        assert failed.status == "failed"


def test_run_summary(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.db") as store:
        run_id = store.begin_run()
        store.finish_run(run_id, "success", downloaded=2, updated=1, unchanged=4, failed=0)
        row = store.last_run()
        assert row is not None
        assert row["status"] == "success"
        assert row["downloaded"] == 2


def test_catalog_counts_remote_metadata_separately(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    course = Course("c1", "Course", "https://lms.test/courses/c1")
    remote = RemoteFile("f1", "c1", "notes.pdf", (), "https://lms.test/files/f1")
    with StateStore(database) as store, store.transaction():
        store.upsert_course(course)
        store.replace_catalog_files(course, [remote])
    with StateStore(database) as store:
        assert store.catalog_counts() == (1, 0, 0)
        assert store.counts() == (1, 0)
