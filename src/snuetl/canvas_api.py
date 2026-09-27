"""Personal Canvas token provisioning, catalog access, and live API commands."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time as monotonic_time
import random
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .canvas_adapter import JSON_GUARD, _response_json
from .config import Config, ensure_private_directory
from .errors import AuthenticationRequired, DiscoveryError

TOKEN_PATTERN = re.compile(r"\b\d+~[A-Za-z0-9_-]{16,}\b")
KOREA = ZoneInfo("Asia/Seoul")


def _http_json(response: httpx.Response) -> Any:
    return json.loads(JSON_GUARD.sub("", response.text, count=1))


@dataclass(frozen=True, slots=True)
class CanvasToken:
    value: str
    origin: str
    user_id: str
    token_id: str
    expires_at: str

    def public(self) -> dict[str, str]:
        return {"origin": self.origin, "user_id": self.user_id, "expires_at": self.expires_at}


def load_token(config: Config) -> CanvasToken | None:
    try:
        data = json.loads(config.canvas_token_path.read_text(encoding="utf-8"))
        token = CanvasToken(**data)
        parsed = urlsplit(token.origin)
        expiry = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not token.value
            or not token.token_id
            or expiry.tzinfo is None
        ):
            return None
        return token
    except (OSError, ValueError, TypeError):
        return None


def _active_user_id(config: Config) -> str | None:
    try:
        metadata = json.loads(config.session_metadata_path.read_text(encoding="utf-8"))
        value = metadata.get("user_id")
        return str(value) if value is not None else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def save_token(config: Config, token: CanvasToken) -> None:
    ensure_private_directory(config.state_dir)
    path = config.canvas_token_path
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(asdict(token)), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


_COURSE_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}


class CanvasClient:
    def __init__(self, config: Config, token: CanvasToken | None = None):
        self.token = token or load_token(config)
        if self.token is None:
            raise AuthenticationRequired("Canvas API access is not set up; run 'snuetl api setup'")
        active_user_id = _active_user_id(config)
        if active_user_id is not None and active_user_id != self.token.user_id:
            raise AuthenticationRequired(
                "Canvas token belongs to another eTL profile; run 'snuetl api setup'"
            )
        self.timeout = config.timeout_seconds
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {self.token.value}"},
            timeout=self.timeout,
            follow_redirects=False,
        )

    def __enter__(self) -> CanvasClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self._http.close()

    def _url(self, path: str) -> str:
        url = urljoin(self.token.origin + "/", path.lstrip("/"))
        if (
            urlsplit(url).scheme != "https"
            or urlsplit(url).netloc != urlsplit(self.token.origin).netloc
        ):
            raise DiscoveryError("Canvas API pagination left the authenticated eTL origin")
        return url

    def _send(self, method: str, url: str) -> httpx.Response:
        for attempt in range(4):
            response = self._http.request(method, url)
            if response.status_code != 429:
                return response
            if attempt == 3:
                return response
            try:
                delay = min(60, max(1, float(response.headers.get("Retry-After", 2 ** (attempt + 1)))))
            except ValueError:
                delay = 2 ** (attempt + 1)
            monotonic_time.sleep(delay + random.uniform(0, 0.5))
        return response

    def request(self, method: str, path: str) -> Any:
        try:
            response = self._send(method, self._url(path))
        except httpx.HTTPError as exc:
            raise DiscoveryError("Canvas API request failed; check network access") from exc
        if response.status_code == 401:
            raise AuthenticationRequired(
                "Canvas token expired or was revoked; run 'snuetl api setup'"
            )
        if response.status_code == 403:
            raise DiscoveryError("eTL denied access to this Canvas API endpoint")
        if response.status_code == 404:
            raise DiscoveryError("this Canvas API endpoint is unavailable on SNU eTL")
        if not 200 <= response.status_code < 300:
            raise DiscoveryError(f"Canvas API returned HTTP {response.status_code}")
        if response.status_code == 204:
            return None
        try:
            return _http_json(response)
        except ValueError as exc:
            raise DiscoveryError("Canvas API returned invalid JSON") from exc

    def pages(self, path: str) -> list[dict[str, Any]]:
        cache_key = (self.token.origin, self.token.user_id + "|" + self.token.token_id, path)
        cacheable = path.startswith("/api/v1/courses?")
        cached = _COURSE_CACHE.get(cache_key) if cacheable else None
        if cached and monotonic_time.monotonic() - cached[0] < 300:
            return [dict(row) for row in cached[1]]
        values: list[dict[str, Any]] = []
        next_url: str | None = path
        seen: set[str] = set()
        while next_url:
            url = self._url(next_url)
            if url in seen:
                raise DiscoveryError("Canvas API pagination loop detected")
            seen.add(url)
            try:
                response = self._send("GET", url)
            except httpx.HTTPError as exc:
                raise DiscoveryError("Canvas API request failed; check network access") from exc
            if response.status_code == 401:
                raise AuthenticationRequired(
                    "Canvas token expired or was revoked; run 'snuetl api setup'"
                )
            if response.status_code == 403:
                raise DiscoveryError("eTL denied access to this Canvas API endpoint")
            if response.status_code == 404:
                raise DiscoveryError("this Canvas API endpoint is unavailable on SNU eTL")
            if not 200 <= response.status_code < 300:
                raise DiscoveryError(f"Canvas API returned HTTP {response.status_code}")
            try:
                payload = _http_json(response)
            except ValueError as exc:
                raise DiscoveryError("Canvas API returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise DiscoveryError("Canvas API returned an unexpected page")
            values.extend(item for item in payload if isinstance(item, dict))
            next_url = None
            for link in response.headers.get("link", "").split(","):
                match = re.match(r'\s*<([^>]+)>;\s*rel="next"', link.strip())
                if match:
                    next_url = match.group(1)
                    break
        if cacheable:
            if len(_COURSE_CACHE) > 32: _COURSE_CACHE.clear()
            _COURSE_CACHE[cache_key] = (monotonic_time.monotonic(), values)
        return values


def token_status(config: Config, *, verify: bool = False) -> dict[str, Any]:
    token = load_token(config)
    if token is None:
        return {"configured": False, "enabled": config.canvas_api_enabled is True}
    expired = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC)
    active_user_id = _active_user_id(config)
    profile_mismatch = active_user_id is not None and active_user_id != token.user_id
    status: dict[str, Any] = {
        "configured": True,
        "enabled": config.canvas_api_enabled is True,
        "expired": expired,
        "profile_mismatch": profile_mismatch,
        "ready": not expired and not profile_mismatch,
        **token.public(),
    }
    if verify and status["ready"]:
        try:
            with CanvasClient(config, token) as client:
                profile = client.request("GET", "/api/v1/users/self/profile")
            status["valid"] = isinstance(profile, dict) and str(profile.get("id")) == token.user_id
        except (AuthenticationRequired, DiscoveryError):
            status["valid"] = False
    elif verify:
        status["valid"] = False
    return status


def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(min(locator.count(), 5)):
            item = locator.nth(index)
            if item.is_visible():
                return item
    return None


def _extract_new_token(page: Any) -> str:
    candidates = (
        "[data-testid='access-token']",
        "#access_token",
        "input[readonly]",
        ".access-token-details code",
        "[role='dialog'] code",
    )
    for selector in candidates:
        for item in page.locator(selector).all():
            if not item.is_visible():
                continue
            raw = (
                item.input_value()
                if item.evaluate("el => el.tagName === 'INPUT'")
                else item.inner_text()
            )
            match = TOKEN_PATTERN.search(raw)
            if match:
                return match.group()
    match = TOKEN_PATTERN.search(page.locator("body").inner_text())
    if match:
        return match.group()
    raise DiscoveryError("Canvas did not display a new access token; inspect Account Settings")


def _create_token_in_ui(
    page: Any, origin: str, expires: date, *, purpose_name: str = "snuetl"
) -> tuple[str, str | None, str | None]:
    page.goto(f"{origin}/profile/settings", wait_until="domcontentloaded")
    new_button = _first_visible(
        page,
        (
            "#generate_token",
            "a:has-text('New Access Token')",
            "button:has-text('New Access Token')",
            "a:has-text('새 액세스 토큰')",
            "button:has-text('새 액세스 토큰')",
        ),
    )
    if new_button is None:
        raise DiscoveryError("Canvas access token creation is unavailable in Account Settings")
    new_button.click()
    purpose = _first_visible(
        page,
        (
            "input[name='access_token[purpose]']",
            "input[name='token[purpose]']",
            "#access_token_purpose",
            "input[placeholder*='Purpose']",
        ),
    )
    expiry = _first_visible(
        page,
        (
            "input[name='access_token[expires_at]']",
            "input[name='token[expires_at]']",
            "#access_token_expires_at",
            "input[placeholder*='Expires']",
        ),
    )
    if purpose is None or expiry is None:
        raise DiscoveryError("Canvas token form is not supported; inspect Account Settings")
    purpose.fill(purpose_name)
    expiry.fill(expires.isoformat())
    submit = _first_visible(
        page,
        (
            "[role='dialog'] button:has-text('Generate Token')",
            "button:has-text('Generate Token')",
            "button:has-text('토큰 생성')",
            "[role='dialog'] button[type='submit']",
        ),
    )
    if submit is None:
        raise DiscoveryError("Canvas token form has no Generate Token action")
    with page.expect_response(
        lambda response: (
            urlsplit(response.url).path == "/profile/tokens" and response.request.method == "POST"
        )
    ) as created:
        submit.click()
    response = created.value
    if not 200 <= response.status < 300:
        raise DiscoveryError(f"Canvas token creation failed with HTTP {response.status}")
    payload = _response_json(response)
    if isinstance(payload, dict):
        value = payload.get("visible_token") or payload.get("token")
        if isinstance(value, str) and value:
            return (
                value,
                str(payload["id"]) if payload.get("id") is not None else None,
                str(payload["expires_at"]) if payload.get("expires_at") else None,
            )
    return _extract_new_token(page), None, None


def _browser_token_id(
    context: Any, page: Any, origin: str, user_id: str, *, purpose_name: str = "snuetl"
) -> str:
    response = context.request.get(
        f"{origin}/api/v1/users/{quote(user_id, safe='')}/user_generated_tokens?per_page=100"
    )
    if response.status == 200:
        data = _response_json(response)
        matches = [
            item
            for item in data
            if isinstance(item, dict)
            and item.get("purpose") == purpose_name
            and item.get("id") is not None
        ]
        if matches:
            latest = max(
                matches, key=lambda item: (str(item.get("created_at") or ""), int(item["id"]))
            )
            return str(latest["id"])
    # Older LearningX deployments expose token management only on the Settings page.
    page.locator("tr.access_token .purpose").first.wait_for(timeout=10_000)
    ids: list[int] = []
    for row in page.locator("tr.access_token").all():
        if row.locator("td.purpose").inner_text().strip() != purpose_name:
            continue
        link = row.locator("a.show_token_link").first.get_attribute("rel") or ""
        match = re.search(r"/profile/tokens/(\d+)$", link)
        if match:
            ids.append(int(match.group(1)))
    if not ids:
        raise DiscoveryError("Canvas created a token, but its ID could not be confirmed")
    return str(max(ids))


def _revoke_via_ui(config: Config, token: CanvasToken) -> None:
    from .lms_session import AuthenticatedLmsSession

    with AuthenticatedLmsSession(config, headless=True) as session:
        session.page.goto(token.origin + "/profile/settings", wait_until="domcontentloaded")
        if session.page.locator("#access_tokens_holder").count() == 0:
            raise DiscoveryError(
                "Canvas token settings are unavailable; remote revocation is unconfirmed"
            )
        link = session.page.locator(
            f"tr.access_token a.delete_key_link[rel$='/profile/tokens/{token.token_id}']"
        )
        if link.count() == 0:
            return  # Already absent from the user's token list.
        session.page.once("dialog", lambda dialog: dialog.accept())
        link.first.click()
        link.first.wait_for(state="detached", timeout=10_000)


def revoke_token(config: Config, token: CanvasToken | None = None) -> None:
    current = token or load_token(config)
    if current is None:
        return
    path = (
        f"/api/v1/users/{quote(current.user_id, safe='')}/tokens/{quote(current.token_id, safe='')}"
    )
    try:
        with CanvasClient(config, current) as client:
            client.request("DELETE", path)
    except (AuthenticationRequired, DiscoveryError):
        _revoke_via_ui(config, current)
    saved = load_token(config)
    if saved is not None and saved.token_id == current.token_id:
        config.canvas_token_path.unlink(missing_ok=True)


def ensure_token(config: Config, *, headless: bool, rotate: bool = False) -> CanvasToken:
    from .lms_session import AuthenticatedLmsSession

    existing = load_token(config)
    if existing and not rotate and token_status(config, verify=True).get("valid") is True:
        return existing
    with AuthenticatedLmsSession(config, headless=headless) as session:
        origin_parts = urlsplit(session.landing_url)
        origin = f"{origin_parts.scheme}://{origin_parts.netloc}"
        response = session.context.request.get(f"{origin}/api/v1/users/self/profile")
        if response.status != 200:
            raise AuthenticationRequired("SNU eTL login is required before Canvas token setup")
        user_id = str(_response_json(response)["id"])
        expires = datetime.now(UTC) + timedelta(days=365)
        value, token_id, actual_expiry = _create_token_in_ui(session.page, origin, expires.date())
        token_id = token_id or _browser_token_id(session.context, session.page, origin, user_id)
        token = CanvasToken(value, origin, user_id, token_id, actual_expiry or expires.isoformat())
        with CanvasClient(config, token) as client:
            profile = client.request("GET", "/api/v1/users/self/profile")
        if not isinstance(profile, dict) or str(profile.get("id")) != user_id:
            raise DiscoveryError("new Canvas token did not authenticate the current user")
        save_token(config, token)
        session.persist(session.landing_url)
    if existing and existing.token_id != token.token_id:
        revoke_token(config, existing)
    return token


def _courses(client: CanvasClient, config: Config, query: str | None) -> list[dict[str, Any]]:
    courses = [
        item
        for item in client.pages(
            "/api/v1/courses?enrollment_state=active&state[]=available&per_page=100"
        )
        if str(item.get("id")) not in config.excluded_course_ids
    ]
    if query:
        exact = [item for item in courses if str(item.get("id")) == query]
        courses = exact or [
            item for item in courses if query.casefold() in str(item.get("name") or "").casefold()
        ]
        if not courses:
            raise DiscoveryError(f"no active course matches {query!r}")
        if len(courses) > 1:
            raise DiscoveryError("course name is ambiguous; use its ID")
    return courses


def _excerpt(value: Any, *, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    plain = " ".join(BeautifulSoup(value, "html.parser").stripped_strings)
    return plain[: limit - 1] + "…" if len(plain) > limit else plain


def _context_batches(courses: list[dict[str, Any]]) -> list[str]:
    return [
        "".join(
            f"&context_codes[]=course_{quote(str(course['id']), safe='')}"
            for course in courses[offset : offset + 10]
        )
        for offset in range(0, len(courses), 10)
    ]


def _timestamp_key(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return datetime.max.replace(tzinfo=UTC)


def _today() -> date:
    return datetime.now(KOREA).date()


def live_data(
    config: Config,
    command: str,
    *,
    token: CanvasToken | None = None,
    course: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    with CanvasClient(config, token) as client:
        if command == "upcoming":
            first = start or _today()
            last = end or first + timedelta(days=30)
            path = f"/api/v1/planner/items?filter=incomplete_items&start_date={first}&end_date={last}&per_page=100"
            return [
                {
                    "course_id": item.get("course_id"),
                    "item_id": item.get("plannable_id"),
                    "type": item.get("plannable_type"),
                    "title": (item.get("plannable") or {}).get("title")
                    or (item.get("plannable") or {}).get("name"),
                    "due_at": (item.get("plannable") or {}).get("due_at")
                    or (item.get("plannable") or {}).get("todo_date"),
                    "url": urljoin(client.token.origin + "/", str(item.get("html_url") or "")),
                    "submission": item.get("submissions"),
                    "new_activity": item.get("new_activity"),
                }
                for item in client.pages(path)
            ]
        if command == "missing":
            return [
                {
                    "course_id": item.get("course_id"),
                    "assignment_id": item.get("id"),
                    "title": item.get("name"),
                    "due_at": item.get("due_at"),
                    "points_possible": item.get("points_possible"),
                    "lock_at": item.get("lock_at"),
                    "url": item.get("html_url"),
                }
                for item in client.pages("/api/v1/users/self/missing_submissions?per_page=100")
            ]
        selected = _courses(client, config, course)
        names = {str(item["id"]): item.get("name") for item in selected}
        if command == "dashboard":
            first = start or _today()
            last = end or first + timedelta(days=7)
            due_by_course: dict[str, list[str]] = {str(item["id"]): [] for item in selected}
            for contexts in _context_batches(selected):
                path = (
                    "/api/v1/planner/items?filter=incomplete_items"
                    f"&start_date={first}&end_date={last}&per_page=100{contexts}"
                )
                for item in client.pages(path):
                    course_id = str(item.get("course_id") or "")
                    if course_id in due_by_course:
                        plannable = item.get("plannable") or {}
                        timestamp = (
                            plannable.get("due_at")
                            or plannable.get("todo_date")
                            or plannable.get("start_at")
                        )
                        if timestamp:
                            due_by_course[course_id].append(str(timestamp))
            missing_by_course = {course_id: 0 for course_id in due_by_course}
            for item in client.pages("/api/v1/users/self/missing_submissions?per_page=100"):
                course_id = str(item.get("course_id") or "")
                if course_id in missing_by_course:
                    missing_by_course[course_id] += 1
            rows = []
            for course_item in selected:
                course_id = str(course_item["id"])
                enrollments = client.pages(
                    f"/api/v1/courses/{quote(course_id, safe='')}/enrollments"
                    "?user_id=self&type[]=StudentEnrollment&per_page=100"
                )
                grade = next(
                    (
                        enrollment.get("grades")
                        for enrollment in enrollments
                        if isinstance(enrollment.get("grades"), dict)
                    ),
                    {},
                )
                due_dates = [value for value in due_by_course[course_id] if value]
                rows.append(
                    {
                        "course_id": course_id,
                        "course_name": course_item.get("name"),
                        "course_code": course_item.get("course_code"),
                        "due_soon": len(due_by_course[course_id]),
                        "missing": missing_by_course[course_id],
                        "next_due_at": min(due_dates, key=_timestamp_key) if due_dates else None,
                        "current_grade": grade.get("current_grade"),
                        "current_score": grade.get("current_score"),
                        "url": course_item.get("html_url"),
                    }
                )
            return rows
        if command == "activity":
            first = start or _today() - timedelta(days=14)
            last = end or _today() + timedelta(days=7)
            rows = []
            for contexts in _context_batches(selected):
                path = (
                    "/api/v1/planner/items?filter=new_activity"
                    f"&start_date={first}&end_date={last}&per_page=100{contexts}"
                )
                for item in client.pages(path):
                    plannable = item.get("plannable") or {}
                    course_id = str(item.get("course_id") or "")
                    rows.append(
                        {
                            "course_id": item.get("course_id"),
                            "course_name": names.get(course_id),
                            "item_id": item.get("plannable_id"),
                            "type": item.get("plannable_type"),
                            "title": plannable.get("title") or plannable.get("name"),
                            "due_at": plannable.get("due_at") or plannable.get("todo_date"),
                            "url": item.get("html_url"),
                        }
                    )
            return rows
        if command == "announcements":
            first = start or _today() - timedelta(days=14)
            last = end or _today()
            rows = []
            for contexts in _context_batches(selected):
                path = f"/api/v1/announcements?start_date={first}&end_date={last}&per_page=100{contexts}"
                for item in client.pages(path):
                    course_id = str(item.get("context_code") or "").removeprefix("course_")
                    rows.append(
                        {
                            "course_id": course_id,
                            "course_name": names.get(course_id),
                            "announcement_id": item.get("id"),
                            "content_revision": hashlib.sha256(str(item.get("message") or "").encode()).hexdigest(),
                            "updated_at": item.get("updated_at"),
                            "title": item.get("title"),
                            "posted_at": item.get("posted_at"),
                            "summary": _excerpt(item.get("message")),
                            "url": item.get("html_url"),
                        }
                    )
            return rows
        if command == "modules":
            rows = []
            for course_item in selected:
                course_id = str(course_item["id"])
                base = f"/api/v1/courses/{quote(course_id, safe='')}/modules"
                for module in client.pages(base + "?include[]=items&include[]=content_details&per_page=100"):
                    module_id = str(module["id"])
                    items = module.get("items")
                    if not isinstance(items, list):
                        items = client.pages(
                            f"{base}/{quote(module_id, safe='')}/items?include[]=content_details&per_page=100"
                        )
                    for item in items or [None]:
                        details = (item or {}).get("content_details") or {}
                        rows.append(
                            {
                                "course_id": course_id,
                                "course_name": course_item.get("name"),
                                "module_id": module.get("id"),
                                "module_name": module.get("name"),
                                "module_state": module.get("state"),
                                "module_unlock_at": module.get("unlock_at"),
                                "item_id": item.get("id") if item else None,
                                "item_title": item.get("title") if item else None,
                                "item_type": item.get("type") if item else None,
                                "item_completed": item.get("completion_requirement", {}).get("completed")
                                if item and isinstance(item.get("completion_requirement"), dict)
                                else None,
                                "item_locked": details.get("locked_for_user"),
                                "due_at": details.get("due_at"),
                                "url": item.get("html_url") if item else None,
                            }
                        )
            return rows
        if command == "feedback":
            since = start or _today() - timedelta(days=30)
            since_utc = datetime.combine(since, time.min, tzinfo=KOREA).astimezone(UTC)
            rows = []
            for course_item in selected:
                course_id = str(course_item["id"])
                path = (
                    f"/api/v1/courses/{quote(course_id, safe='')}/students/submissions"
                    f"?graded_since={since_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}&include[]=assignment"
                    "&include[]=submission_comments&include[]=rubric_assessment"
                    "&order=graded_at&order_direction=descending&per_page=100"
                )
                for submission in client.pages(path):
                    assignment = submission.get("assignment") or {}
                    rubric = assignment.get("rubric") or []
                    criteria = {
                        str(item.get("id")): item.get("description")
                        for item in rubric
                        if isinstance(item, dict)
                    }
                    assessment = submission.get("rubric_assessment") or {}
                    if not isinstance(assessment, dict):
                        assessment = {}
                    rows.append(
                        {
                            "course_id": course_id,
                            "course_name": course_item.get("name"),
                            "assignment_id": submission.get("assignment_id"),
                            "title": assignment.get("name"),
                            "graded_at": submission.get("graded_at"),
                            "score": submission.get("score"),
                            "points_possible": assignment.get("points_possible"),
                            "grade": submission.get("grade"),
                            "comments": [
                                {
                                    "author": comment.get("author_name"),
                                    "posted_at": comment.get("created_at"),
                                    "text": _excerpt(comment.get("comment")),
                                }
                                for comment in submission.get("submission_comments") or []
                                if isinstance(comment, dict)
                            ],
                            "rubric": [
                                {
                                    "criterion": criteria.get(str(criterion_id)),
                                    "points": rating.get("points"),
                                    "comments": _excerpt(rating.get("comments")),
                                }
                                for criterion_id, rating in assessment.items()
                                if isinstance(rating, dict)
                            ],
                            "url": assignment.get("html_url"),
                        }
                    )
            return rows
        if command == "submissions":
            rows = []
            for item in selected:
                course_id = str(item["id"])
                for assignment in client.pages(
                    f"/api/v1/courses/{quote(course_id, safe='')}/assignments?include[]=submission&per_page=100"
                ):
                    submission = assignment.get("submission") or {}
                    rows.append(
                        {
                            "course_id": course_id,
                            "course_name": item.get("name"),
                            "assignment_id": assignment.get("id"),
                            "title": assignment.get("name"),
                            "due_at": assignment.get("due_at"),
                            "submitted_at": submission.get("submitted_at"),
                            "workflow_state": submission.get("workflow_state"),
                            "late": submission.get("late"),
                            "missing": submission.get("missing"),
                            "score": submission.get("score"),
                            "grade": submission.get("grade"),
                            "points_possible": assignment.get("points_possible"),
                            "graded_at": submission.get("graded_at"),
                            "attempt": submission.get("attempt"),
                            "excused": submission.get("excused"),
                            "grade_matches_current_submission": submission.get(
                                "grade_matches_current_submission"
                            ),
                            "url": assignment.get("html_url"),
                        }
                    )
            return rows
        if command == "grades":
            rows = []
            for item in selected:
                course_id = str(item["id"])
                enrollments = client.pages(
                    f"/api/v1/courses/{quote(course_id, safe='')}/enrollments?user_id=self&type[]=StudentEnrollment&per_page=100"
                )
                for enrollment in enrollments:
                    grades = enrollment.get("grades") or {}
                    rows.append(
                        {
                            "course_id": course_id,
                            "course_name": item.get("name"),
                            "current_grade": grades.get("current_grade"),
                            "current_score": grades.get("current_score"),
                            "final_grade": grades.get("final_grade"),
                            "final_score": grades.get("final_score"),
                            "url": grades.get("html_url"),
                        }
                    )
            return rows
        if command == "calendar":
            first = start or _today()
            last = end or first + timedelta(days=30)
            rows = []
            for contexts in _context_batches(selected):
                path = f"/api/v1/calendar_events?start_date={first}&end_date={last}&per_page=100{contexts}"
                rows.extend(
                    {
                        "event_id": item.get("id"),
                        "course_id": str(item.get("context_code") or "").removeprefix("course_"),
                        "title": item.get("title"),
                        "start_at": item.get("start_at"),
                        "end_at": item.get("end_at"),
                        "location": item.get("location_name"),
                        "url": item.get("html_url"),
                    }
                    for item in client.pages(path)
                )
            return rows
        if command == "discussions":
            rows = []
            for item in selected:
                course_id = str(item["id"])
                for topic in client.pages(
                    f"/api/v1/courses/{quote(course_id, safe='')}/discussion_topics?per_page=100"
                ):
                    if topic.get("is_announcement"):
                        continue
                    rows.append(
                        {
                            "course_id": course_id,
                            "course_name": item.get("name"),
                            "topic_id": topic.get("id"),
                            "title": topic.get("title"),
                            "posted_at": topic.get("posted_at"),
                            "last_reply_at": topic.get("last_reply_at"),
                            "unread_count": topic.get("unread_count"),
                            "url": topic.get("html_url"),
                        }
                    )
            return rows
    raise ValueError(f"unknown live Canvas command: {command}")
