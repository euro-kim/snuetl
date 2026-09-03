from pathlib import Path

from snuetl.paths import destination_path, sanitize_component


def test_sanitize_component() -> None:
    assert sanitize_component("  Week/One\\notes.  ") == "Week_One_notes"
    assert sanitize_component("../") == "_"
    assert sanitize_component("\x00\x01") == "unnamed"


def test_destination_preserves_hierarchy(tmp_path: Path) -> None:
    result = destination_path(tmp_path, "Algorithms", "42", ("Week 1", "Slides"), "intro.pdf", "7")
    assert result == tmp_path / "Algorithms-42" / "Week 1" / "Slides" / "intro.pdf"


def test_destination_resolves_collision(tmp_path: Path) -> None:
    result = destination_path(
        tmp_path,
        "Algorithms",
        "42",
        (),
        "intro.pdf",
        "77",
        lambda _: True,
    )
    assert result.name == "intro__77.pdf"
