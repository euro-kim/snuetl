import re

import pytest

from snuetl.browser import _handle_password_change_prompt, _set_trusted_browser
from snuetl.errors import AuthenticationRequired


class _Item:
    def __init__(self, *, text: str = "", checked: bool = False) -> None:
        self.text = text
        self.checked = checked
        self.clicked = False

    def is_visible(self) -> bool:
        return True

    def inner_text(self) -> str:
        return self.text

    def click(self) -> None:
        self.clicked = True

    def check(self) -> None:
        self.checked = True

    def uncheck(self) -> None:
        self.checked = False

    def is_checked(self) -> bool:
        return self.checked


class _Items:
    def __init__(self, *items: _Item) -> None:
        self.items = items

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> _Item:
        return self.items[index]

    def inner_text(self) -> str:
        return self.items[0].inner_text()


class _PasswordPage:
    def __init__(self, body: str, later: _Item | None = None) -> None:
        self.body = _Item(text=body)
        self.later = later

    def locator(self, selector: str) -> _Items:
        assert selector == "body"
        return _Items(self.body)

    def get_by_role(self, role: str, name: re.Pattern[str]) -> _Items:
        if self.later is not None and role in {"button", "link"} and name.search(self.later.text):
            return _Items(self.later)
        return _Items()

    def get_by_text(self, name: re.Pattern[str], exact: bool) -> _Items:
        if self.later is not None and exact and name.search(self.later.text):
            return _Items(self.later)
        return _Items()


def test_optional_password_change_prompt_is_deferred() -> None:
    later = _Item(text="다음에 변경하기")
    page = _PasswordPage("비밀번호 변경 안내\n계속 사용하려면 선택하세요", later)
    assert _handle_password_change_prompt(page) is True
    assert later.clicked is True


def test_mandatory_password_change_prompt_is_reported() -> None:
    page = _PasswordPage("비밀번호가 만료되어 변경이 필요합니다")
    with pytest.raises(AuthenticationRequired, match="requires a password change"):
        _handle_password_change_prompt(page)


def test_trusted_browser_can_be_explicitly_disabled() -> None:
    checkbox = _Item(checked=True)

    class Page:
        def locator(self, selector: str) -> _Items:
            return _Items(checkbox) if selector == "#bypass_check" else _Items()

    assert _set_trusted_browser(Page(), object(), False) is True
    assert checkbox.checked is False
