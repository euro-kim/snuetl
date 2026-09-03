from snuetl.models import Course, RemoteFile, semester_from_term


def test_revision_uses_available_metadata() -> None:
    remote = RemoteFile(
        remote_id="1",
        course_id="2",
        name="file.pdf",
        folder_path=("Week 1",),
        download_url="https://example.test/file",
        size=99,
        updated_at="2026-01-01T00:00:00Z",
        etag='"abc"',
    )
    assert remote.display_path == "Week 1/file.pdf"
    assert remote.revision == '2026-01-01T00:00:00Z\x1f99\x1f"abc"'


def test_normalizes_numbered_and_snuon_semesters() -> None:
    numbered = semester_from_term({"id": 164, "name": "2026년 2학기"})
    snuon = semester_from_term({"id": 95, "name": "2024년(SNUON)"})
    assert numbered is not None
    assert (numbered.semester_code, numbered.academic_year) == ("2026-2", 2026)
    assert snuon is not None
    assert (snuon.semester_code, snuon.academic_year) == ("SNUON", 2024)
    course = Course("1", "2026-2 Machine Learning", "https://lms.test/courses/1", semester=numbered)
    assert course.display_name == "Machine Learning"
