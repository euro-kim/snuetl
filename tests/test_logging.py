from snuetl.logging_utils import redact


def test_redacts_secret_assignments_and_url_queries() -> None:
    value = redact("password=hunter2 token:abc GET https://lms.test/file?verifier=secret")
    assert "hunter2" not in value
    assert "abc" not in value
    assert "verifier" not in value
    assert value.endswith("https://lms.test/file")
