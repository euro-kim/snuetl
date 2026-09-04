from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Config, ensure_private_directory
from .credentials import load_credentials
from .errors import AuthenticationRequired

LOGGER = logging.getLogger(__name__)

LOGIN_URL_MARKERS = ("sso.snu.ac.kr", "nsso.snu.ac.kr")
LOGIN_TEXT = re.compile(r"^(아이디|ID)$", re.IGNORECASE)
TWO_FACTOR_TEXT = re.compile(
    r"추가\s*인증|additional\s+(verification|authentication)", re.IGNORECASE
)
TRUST_TEXT = re.compile(
    r"이\s*브라우저에서\s*추가\s*인증\s*사용\s*안함|"
    r"(do not|don't)\s+(use|require).*(verification|authentication).*(browser|device)",
    re.IGNORECASE,
)
PASSWORD_CHANGE_LATER_TEXT = re.compile(
    r"나중에\s*(변경|하기)?|다음에\s*(변경|하기)?|"
    r"change\s+later|later|skip|remind\s+me\s+later",
    re.IGNORECASE,
)
PASSWORD_CHANGE_REQUIRED_TEXT = re.compile(
    r"비밀번호.{0,30}(만료|변경.{0,15}(필요|안내|권장|주기))|"
    r"password.{0,40}(expired|must\s+be\s+changed|change\s+required)",
    re.IGNORECASE | re.DOTALL,
)


def _playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "Playwright is not installed. Install the package and run "
            "'playwright install chromium'."
        ) from exc
    return sync_playwright


@contextmanager
def persistent_browser(config: Config, *, headless: bool) -> Iterator[tuple[Any, Any]]:
    ensure_private_directory(config.state_dir)
    ensure_private_directory(config.profile_dir)
    with _playwright()() as playwright:
        kwargs: dict[str, Any] = {
            "user_data_dir": str(config.profile_dir),
            "headless": headless,
            "accept_downloads": False,
            "args": [
                "--disable-features=PasswordManagerOnboarding",
                "--disable-save-password-bubble",
                "--no-default-browser-check",
            ],
        }
        if config.browser_executable_path:
            kwargs["executable_path"] = str(config.browser_executable_path)
        elif config.browser_channel:
            kwargs["channel"] = config.browser_channel
        context = playwright.chromium.launch_persistent_context(**kwargs)
        _restore_auth_state(context, config)
        context.set_default_timeout(config.timeout_seconds * 1000)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def _atomic_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _restore_auth_state(context: Any, config: Config) -> None:
    if not config.auth_state_path.exists():
        return
    try:
        state = json.loads(config.auth_state_path.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
        if isinstance(cookies, list) and cookies:
            context.add_cookies(cookies)
        origins = state.get("origins", [])
        if isinstance(origins, list) and origins:
            encoded = json.dumps(origins, ensure_ascii=False)
            script = """
                (() => {
                  const saved = __SAVED_STATE__;
                  const match = saved.find(item => item.origin === location.origin);
                  if (match && Array.isArray(match.localStorage)) {
                    for (const item of match.localStorage) localStorage.setItem(item.name, item.value);
                  }
                })();
                """.replace("__SAVED_STATE__", encoded)
            context.add_init_script(script)
    except (OSError, ValueError, TypeError):
        LOGGER.warning("saved authentication state is unreadable; a new login may be required")


def persist_auth_state(context: Any, config: Config, landing_url: str) -> None:
    """Persist session cookies that Chromium normally drops when it exits."""
    state = context.storage_state()
    _atomic_private_json(config.auth_state_path, state)
    parts = urlsplit(landing_url)
    if (parts.hostname or "").lower() == "myetl.snu.ac.kr":
        landing_url = f"{parts.scheme}://{parts.netloc}/"
    _atomic_private_json(config.session_metadata_path, {"landing_url": landing_url})


def authenticated_entry_url(config: Config) -> str:
    if not config.session_metadata_path.exists():
        return config.base_url
    try:
        value = json.loads(config.session_metadata_path.read_text(encoding="utf-8"))
        url = str(value.get("landing_url", ""))
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme == "https" and (host == "snu.ac.kr" or host.endswith(".snu.ac.kr")):
            if host == "myetl.snu.ac.kr":
                return f"{parts.scheme}://{parts.netloc}/"
            return url
    except (OSError, ValueError, TypeError):
        pass
    return config.base_url


def _first_visible(locator: Any) -> Any | None:
    try:
        for index in range(locator.count()):
            item = locator.nth(index)
            if item.is_visible():
                return item
    except Exception:
        return None
    return None


def _visible_password_field(page: Any) -> Any | None:
    return _first_visible(page.locator("input[type='password']"))


def _on_sso(page: Any) -> bool:
    host = (urlsplit(page.url).hostname or "").lower()
    return any(marker in host for marker in LOGIN_URL_MARKERS)


def _login_was_rejected(page: Any) -> bool:
    pattern = re.compile(
        r"아이디.*비밀번호.*(확인|일치)|로그인.*(실패|오류)|"
        r"incorrect|invalid\s+(id|password|credentials)|login\s+failed",
        re.IGNORECASE | re.DOTALL,
    )
    candidates = page.locator(
        "[role='alert'], .error, .error_msg, .error_txt, .alert, .validation-message"
    )
    try:
        for index in range(candidates.count()):
            item = candidates.nth(index)
            if item.is_visible() and pattern.search(item.inner_text() or ""):
                return True
    except Exception:
        return False
    return False


def _select_id_tab(page: Any) -> bool:
    candidates = [
        page.locator("#tab-4"),
        page.get_by_role("tab", name=LOGIN_TEXT),
        page.get_by_text(LOGIN_TEXT, exact=True),
        page.locator("a, button, [role='tab']").filter(has_text=LOGIN_TEXT),
    ]
    for candidate in candidates:
        item = _first_visible(candidate)
        if item is not None:
            try:
                item.click()
                return True
            except Exception:
                continue
    return False


def _find_username_field(page: Any) -> Any | None:
    selectors = (
        "#login_id",
        "input[autocomplete='username']",
        "input[placeholder*='아이디']",
        "input[name='userId']",
        "input[name='username']",
        "input[id*='userId']",
        "input[id*='username']",
        "input[type='text']",
    )
    for selector in selectors:
        item = _first_visible(page.locator(selector))
        if item is not None:
            return item
    return None


def _submit_login(page: Any) -> None:
    buttons = page.get_by_role("button", name=re.compile(r"^로그인$|^log\s*in$", re.IGNORECASE))
    button = _first_visible(buttons)
    if button is not None:
        button.click()
        return
    password = _visible_password_field(page)
    if password is None:
        raise AuthenticationRequired("could not find the SNU login button")
    password.press("Enter")


def _find_two_factor_container(page: Any) -> Any | None:
    dialog = _first_visible(page.get_by_role("dialog"))
    if dialog is not None:
        try:
            if TWO_FACTOR_TEXT.search(dialog.inner_text()):
                return dialog
        except Exception:
            pass
    matches = page.locator("div").filter(has_text=TWO_FACTOR_TEXT)
    visible: list[Any] = []
    try:
        for index in range(min(matches.count(), 30)):
            item = matches.nth(index)
            if item.is_visible():
                visible.append(item)
    except Exception:
        return None
    # Choose the smallest matching container that still owns the modal's
    # checkbox. A plain text node containing "추가 인증" is too narrow.
    for item in reversed(visible):
        try:
            checkboxes = item.locator("input[type='checkbox']")
            if any(checkboxes.nth(i).is_visible() for i in range(checkboxes.count())):
                return item
        except Exception:
            continue
    return visible[0] if visible else None


def _set_trusted_browser(page: Any, container: Any, enabled: bool) -> bool:
    exact = _first_visible(page.locator("#bypass_check"))
    if exact is not None:
        exact.check() if enabled else exact.uncheck()
        return exact.is_checked() is enabled

    for scope in (container, page):
        try:
            by_label = scope.get_by_label(TRUST_TEXT)
            checkbox = _first_visible(by_label)
            if checkbox is not None:
                checkbox.check() if enabled else checkbox.uncheck()
                return checkbox.is_checked() is enabled
        except Exception:
            pass

    try:
        trust_label = _first_visible(container.get_by_text(TRUST_TEXT))
        if trust_label is not None and enabled:
            trust_label.click()
            checkboxes = container.locator("input[type='checkbox']")
            for index in range(checkboxes.count() - 1, -1, -1):
                checkbox = checkboxes.nth(index)
                if checkbox.is_visible() and checkbox.is_checked():
                    return True
    except Exception:
        pass

    # The modal shown by SNU has exactly one checkbox; constrain this fallback
    # to the modal so the background "remember ID" checkbox cannot be selected.
    checkboxes = container.locator("input[type='checkbox']")
    visible = [
        checkboxes.nth(i) for i in range(checkboxes.count()) if checkboxes.nth(i).is_visible()
    ]
    if len(visible) == 1:
        visible[0].check() if enabled else visible[0].uncheck()
        return visible[0].is_checked() is enabled
    # If trust was declined, an absent checkbox already satisfies that choice.
    return not enabled


def _click_named(page: Any, container: Any, pattern: re.Pattern[str]) -> None:
    for scope in (container, page):
        for locator in (
            scope.get_by_role("button", name=pattern),
            scope.get_by_text(pattern, exact=True),
        ):
            item = _first_visible(locator)
            if item is not None:
                item.click()
                return
    raise AuthenticationRequired("SNU authentication control was not found")


def _select_two_factor_method(page: Any, method: str) -> None:
    selector = "#radio-box02" if method == "phone" else "#radio-box01"
    radio = _first_visible(page.locator(selector))
    if radio is None:
        labels = {
            "email": re.compile(r"외부\s*이메일|e-?mail", re.IGNORECASE),
            "phone": re.compile(r"휴대\s*전화|phone|mobile|sms", re.IGNORECASE),
        }
        radio = _first_visible(page.get_by_label(labels[method]))
    if radio is None:
        raise AuthenticationRequired(f"the {method} verification option is unavailable")
    radio.check()


def _send_two_factor_code(page: Any, container: Any, method: str) -> None:
    _select_two_factor_method(page, method)
    _click_named(
        page,
        container,
        re.compile(r"인증\s*코드\s*전송|send\s+(verification\s+)?code", re.IGNORECASE),
    )


def _submit_two_factor_code(
    page: Any,
    container: Any,
    code_provider: Callable[[], str],
) -> None:
    code = code_provider().strip()
    if not code:
        raise AuthenticationRequired("verification code cannot be empty")
    field = _first_visible(page.locator("#id_crtfc_no"))
    if field is None:
        field = _first_visible(
            container.locator("input[autocomplete='one-time-code'], input[placeholder*='인증코드']")
        )
    if field is None:
        raise AuthenticationRequired("could not find the SNU verification-code field")
    field.fill(code)
    _click_named(
        page,
        container,
        re.compile(r"인증\s*확인|confirm\s+(verification|authentication)", re.IGNORECASE),
    )


def _handle_password_change_prompt(page: Any) -> bool:
    """Dismiss an optional password-change reminder or report a mandatory one."""
    try:
        body = page.locator("body").inner_text() or ""
    except Exception:
        return False
    later = None
    for locator in (
        page.get_by_role("button", name=PASSWORD_CHANGE_LATER_TEXT),
        page.get_by_role("link", name=PASSWORD_CHANGE_LATER_TEXT),
        page.get_by_text(PASSWORD_CHANGE_LATER_TEXT, exact=True),
    ):
        later = _first_visible(locator)
        if later is not None:
            break
    if later is not None and re.search(r"비밀번호|password", body, re.IGNORECASE):
        later.click()
        LOGGER.warning("SNU requested a password change; selected the change-later option")
        return True
    if PASSWORD_CHANGE_REQUIRED_TEXT.search(body):
        raise AuthenticationRequired(
            "SNU requires a password change and no safe 'change later' action was found. "
            "Change the password on the SNU website, then run 'snuetl login' to update "
            "the saved credential."
        )
    return False


def canvas_profile_is_authenticated(context: Any, origin: str) -> bool:
    try:
        response = context.request.get(f"{origin}/api/v1/users/self/profile", timeout=15_000)
        if response.status != 200:
            return False
        payload = response.json()
        return isinstance(payload, dict) and payload.get("id") is not None
    except Exception:
        return False


def browser_looks_authenticated(context: Any, page: Any) -> bool:
    if _on_sso(page) or _visible_password_field(page) is not None:
        return False
    parts = urlsplit(page.url)
    if parts.scheme in {"http", "https"} and parts.netloc:
        origin = f"{parts.scheme}://{parts.netloc}"
        if canvas_profile_is_authenticated(context, origin):
            return True
    logout = _first_visible(page.locator("a[href*='logout'], button:has-text('로그아웃')"))
    if logout is not None:
        return True
    login = _first_visible(
        page.locator("a[href*='/login'], button:has-text('로그인'), a:has-text('로그인')")
    )
    if login is not None:
        return False
    markers = page.locator(
        "a[href*='/courses/'], a[href*='/dashboard'], "
        "[aria-label*='Account'], [aria-label*='계정']"
    )
    return _first_visible(markers) is not None


def _login_page(
    config: Config,
    context: Any,
    page: Any,
    username: str,
    password: str,
    *,
    trust_browser: bool,
    two_factor_method_provider: Callable[[], str] | None = None,
    verification_code_provider: Callable[[], str] | None = None,
) -> str:
    entry_url = authenticated_entry_url(config)
    page.goto(entry_url, wait_until="domcontentloaded")
    if browser_looks_authenticated(context, page):
        persist_auth_state(context, config, page.url)
        LOGGER.info("browser profile is already authenticated")
        return page.url
    if entry_url != config.base_url:
        page.goto(config.base_url, wait_until="domcontentloaded")
    form_deadline = time.monotonic() + min(config.login_timeout_seconds, 45)
    username_field = None
    password_field = None
    while time.monotonic() < form_deadline:
        if browser_looks_authenticated(context, page):
            persist_auth_state(context, config, page.url)
            LOGGER.info("browser profile is already authenticated")
            return page.url
        _select_id_tab(page)
        username_field = _find_username_field(page)
        password_field = _visible_password_field(page)
        if username_field is not None and password_field is not None:
            break
        time.sleep(0.25)
    if username_field is None or password_field is None:
        host = urlsplit(page.url).hostname or "unknown host"
        raise AuthenticationRequired(
            f"could not find visible SNU ID/password fields after redirect ({host})"
        )
    username_field.fill(username)
    password_field.fill(password)
    _submit_login(page)

    deadline = time.monotonic() + config.login_timeout_seconds
    announced = False
    trust_configured = False
    delivery_sent = False
    verification_attempts = 0
    last_verification_attempt = 0.0
    while time.monotonic() < deadline:
        if browser_looks_authenticated(context, page):
            persist_auth_state(context, config, page.url)
            LOGGER.info("SNU eTL authentication completed")
            return page.url
        if _handle_password_change_prompt(page):
            time.sleep(0.5)
            continue

        container = _find_two_factor_container(page)
        if container is not None:
            if not trust_configured:
                if not _set_trusted_browser(page, container, trust_browser):
                    action = "enable" if trust_browser else "disable"
                    raise AuthenticationRequired(
                        f"could not {action} trusted-browser authentication"
                    )
                trust_configured = True
                LOGGER.info(
                    "%s 'do not require additional authentication in this browser'",
                    "enabled" if trust_browser else "disabled",
                )
            if not (two_factor_method_provider and verification_code_provider):
                raise AuthenticationRequired(
                    "SNU requires additional verification again. Run 'snuetl login' "
                    "interactively to enter a new email or phone code."
                )
            if not delivery_sent:
                method = two_factor_method_provider().strip().casefold()
                if method not in {"email", "phone"}:
                    raise AuthenticationRequired("2FA method must be 'email' or 'phone'")
                _send_two_factor_code(page, container, method)
                delivery_sent = True
                LOGGER.info("SNU verification code requested by %s", method)
            now = time.monotonic()
            if verification_attempts == 0 or now - last_verification_attempt >= 5:
                if verification_attempts >= 3:
                    raise AuthenticationRequired(
                        "SNU did not accept the verification code after three attempts"
                    )
                _submit_two_factor_code(page, container, verification_code_provider)
                verification_attempts += 1
                last_verification_attempt = time.monotonic()
                LOGGER.info("submitted verification code; waiting for SNU")
            announced = True
        elif not announced and _login_was_rejected(page):
            raise AuthenticationRequired(
                "SNU rejected the saved ID or password. Run 'snuetl login' to update it."
            )
        elif announced and _on_sso(page):
            pass
        time.sleep(0.5)

    raise AuthenticationRequired(
        "login did not complete before the configured timeout; rerun 'snuetl login'"
    )


def interactive_login(
    config: Config,
    username: str,
    password: str,
    *,
    headless: bool = False,
    trust_browser: bool = True,
    two_factor_method_provider: Callable[[], str] | None = None,
    verification_code_provider: Callable[[], str] | None = None,
) -> str:
    """Enroll the persistent browser profile and return the landing URL."""
    with persistent_browser(config, headless=headless) as (context, page):
        return _login_page(
            config,
            context,
            page,
            username,
            password,
            trust_browser=trust_browser,
            two_factor_method_provider=two_factor_method_provider,
            verification_code_provider=verification_code_provider,
        )


def ensure_authenticated_page(config: Config, context: Any, page: Any) -> None:
    """Use saved credentials when the persistent SNU session has expired."""
    if browser_looks_authenticated(context, page):
        return
    credentials = load_credentials(config)
    if credentials is None:
        raise AuthenticationRequired(
            "the SNU session expired and no credentials are saved; run 'snuetl login'"
        )
    LOGGER.info("SNU session expired; signing in automatically with saved credentials")
    _login_page(
        config,
        context,
        page,
        credentials.username,
        credentials.password,
        trust_browser=credentials.trust_browser,
    )


def open_authenticated_browser(config: Config, *, headless: bool) -> Any:
    """Context manager alias kept separate to make dependency injection easy."""
    return persistent_browser(config, headless=headless)
