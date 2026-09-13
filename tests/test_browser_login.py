import re

import pytest

from snuetl.browser import (
    _clear_snu_session_cookies,
    _find_login_fields,
    _handle_password_change_prompt,
    _set_trusted_browser,
    _sso_session_expired,
    _submit_login,
    _submit_two_factor_code,
)
from snuetl.errors import AuthenticationRequired


class _Item:
    def __init__(
        self, *, text: str = "", checked: bool = False, visible: bool = True
    ) -> None:
        self.text = text
        self.checked = checked
        self.visible = visible
        self.clicked = False
        self.filled = ""
        self.pressed = ""

    def is_visible(self) -> bool:
        return self.visible

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

    def fill(self, value: str) -> None:
        self.filled = value

    def press(self, value: str) -> None:
        self.pressed = value


class _Items:
    def __init__(self, *items: _Item) -> None:
        self.items = items

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> _Item:
        return self.items[index]

    def inner_text(self) -> str:
        return self.items[0].inner_text()

    def filter(self, **_kwargs) -> "_Items":
        return self


class _LoginScope:
    def __init__(self, selectors=None, roles=None, *, url: str = "") -> None:
        self.selectors = selectors or {}
        self.roles = roles or {}
        self.url = url

    def locator(self, selector: str) -> _Items:
        return _Items(*self.selectors.get(selector, ()))

    def get_by_role(self, role: str, name=None) -> _Items:
        candidates = self.roles.get(role, ())
        if name is None:
            return _Items(*candidates)
        return _Items(*(item for item in candidates if name.search(item.text)))

    def get_by_text(self, name, exact: bool = False) -> _Items:
        candidates = self.roles.get("text", ())
        return _Items(*(item for item in candidates if name.search(item.text)))


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


def test_current_snu_duplicate_language_fields_choose_visible_pair() -> None:
    tab = _Item(text="ID / PW")
    username = _Item()
    password = _Item()
    scope = _LoginScope(
        {
            "#tab-4": (_Item(text="아이디", visible=False), tab),
            "#login_id": (_Item(visible=False), username),
            "#login_pwd": (_Item(visible=False), password),
        }
    )

    result = _find_login_fields(scope)

    assert result == (scope, username, password)
    assert tab.clicked is True


def test_login_fields_can_be_discovered_in_an_embedded_sso_frame() -> None:
    username = _Item()
    password = _Item()
    main = _LoginScope(url="https://etl.snu.ac.kr/login")
    frame = _LoginScope(
        {"#login_id": (username,), "#login_pwd": (password,)},
        url="https://nsso.snu.ac.kr/sso/login",
    )
    page = _LoginScope(url=main.url)
    page.main_frame = main
    page.frames = [main, frame]

    assert _find_login_fields(page) == (frame, username, password)


def test_expired_nsso_transaction_is_recognized() -> None:
    page = _LoginScope(
        {"body": (_Item(text="인증세션이 만료되었습니다.\nES0024"),)},
        url="https://nsso.snu.ac.kr/sso/usr/login/link",
    )

    assert _sso_session_expired(page) is True


def test_only_snu_session_cookies_are_cleared() -> None:
    class Context:
        cleared: list[dict[str, str]] = []

        @staticmethod
        def cookies():
            return [
                {
                    "name": "JSESSIONID",
                    "domain": "nsso.snu.ac.kr",
                    "path": "/sso",
                    "expires": -1,
                },
                {
                    "name": "PHPSESSID",
                    "domain": "etl.snu.ac.kr",
                    "path": "/",
                    "expires": -1,
                },
                {
                    "name": "bpl",
                    "domain": "nsso.snu.ac.kr",
                    "path": "/sso",
                    "expires": 2_000_000_000,
                },
                {
                    "name": "session",
                    "domain": "example.com",
                    "path": "/",
                    "expires": -1,
                },
            ]

        def clear_cookies(self, **kwargs: str) -> None:
            self.cleared.append(kwargs)

    context = Context()

    assert _clear_snu_session_cookies(context) == 2
    assert context.cleared == [
        {"name": "JSESSIONID", "domain": "nsso.snu.ac.kr", "path": "/sso"},
        {"name": "PHPSESSID", "domain": "etl.snu.ac.kr", "path": "/"},
    ]


def test_current_english_login_and_verification_controls_are_supported() -> None:
    password = _Item()
    sign_in = _Item(text="Sign in")
    scope = _LoginScope(roles={"button": (sign_in,)})
    _submit_login(scope, password)
    assert sign_in.clicked is True
    assert password.pressed == ""

    code = _Item()
    verify = _Item(text="Verify")
    scope = _LoginScope({"#id_crtfc_no": (code,)}, {"button": (verify,)})
    _submit_two_factor_code(scope, _LoginScope(), lambda: "123456")
    assert code.filled == "123456"
    assert verify.clicked is True
