from snuetl.logging_utils import redact, redacted_exc_info


def test_redacts_secret_assignments_and_url_queries() -> None:
    value = redact("password=hunter2 token:abc GET https://lms.test/file?verifier=secret")
    assert "hunter2" not in value
    assert "abc" not in value
    assert "verifier" not in value
    assert value.endswith("https://lms.test/file")


def test_redacted_traceback_keeps_frames_without_secret_text() -> None:
    try:
        raise RuntimeError("token=super-secret")
    except RuntimeError as error:
        _error_type, safe, traceback = redacted_exc_info(error)
    assert "super-secret" not in str(safe)
    assert "token=[REDACTED]" in str(safe)
    assert traceback is not None
