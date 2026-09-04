from contextlib import nullcontext

import pytest

from snuetl import ui


def test_checkbox_requires_space_then_confirms_selected_row(monkeypatch) -> None:
    keys = iter(("q", "\x1b[B", " ", "\n"))
    monkeypatch.setattr(ui, "interactive_terminal", lambda: True)
    monkeypatch.setattr(ui, "_key_input", nullcontext)
    monkeypatch.setattr(ui, "_read_key", lambda: next(keys))
    monkeypatch.setattr(ui.console, "clear", lambda: None)
    monkeypatch.setattr(ui.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.console, "screen", nullcontext)

    selected = ui.choose_checkbox(
        "Choose",
        (("Undergraduate", "undergrad"), ("Graduate", "graduate")),
        require_space=True,
    )

    assert selected == "graduate"


def test_checkbox_default_can_be_confirmed_without_typing_yes(monkeypatch) -> None:
    keys = iter(("\n",))
    monkeypatch.setattr(ui, "interactive_terminal", lambda: True)
    monkeypatch.setattr(ui, "_key_input", nullcontext)
    monkeypatch.setattr(ui, "_read_key", lambda: next(keys))
    monkeypatch.setattr(ui.console, "clear", lambda: None)
    monkeypatch.setattr(ui.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.console, "screen", nullcontext)

    assert ui.choose_checkbox("Continue?", (("Yes", True), ("No", False)), default=0)


def test_multiple_checkboxes_return_space_checked_values_on_enter(monkeypatch) -> None:
    keys = iter(("\x1b[B", "\x1b[B", " ", "\n"))
    monkeypatch.setattr(ui, "interactive_terminal", lambda: True)
    monkeypatch.setattr(ui, "_key_input", nullcontext)
    monkeypatch.setattr(ui, "_read_key", lambda: next(keys))
    monkeypatch.setattr(ui.console, "clear", lambda: None)
    monkeypatch.setattr(ui.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.console, "screen", nullcontext)

    selected = ui.choose_checkboxes(
        "Videos",
        (("First", "video-1"), ("Second", "video-2")),
        include_all=True,
    )

    assert selected == ("video-2",)


def test_checkbox_window_tracks_cursor_without_exceeding_terminal_height() -> None:
    start, end = ui._window_bounds(total=100, cursor=75, height=10)

    assert start <= 75 < end
    assert end - start == 8


@pytest.mark.parametrize("key", ("\x03", "\x04", ""))
def test_checkbox_ctrl_c_ctrl_d_or_eof_cancels(monkeypatch, key: str) -> None:
    monkeypatch.setattr(ui.sys.stdin, "read", lambda _count: key)

    with pytest.raises(KeyboardInterrupt):
        ui._read_key()
