from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from .errors import AuthenticationRequired, DiscoveryError
from .models import ContentItem, Course, ModuleItem, RemoteFile, semester_from_term

LOGGER = logging.getLogger(__name__)
COURSE_PATH = re.compile(r"/courses/([^/?#]+)")
FILE_PATH = re.compile(r"/files/([^/?#]+)(?:/download)?")
JSON_GUARD = re.compile(r"^\s*while\s*\(\s*1\s*\)\s*;\s*")


class AdapterUnavailable(DiscoveryError):
    pass


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise DiscoveryError(f"cannot determine LMS origin from {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


def _parse_next_link(value: str | None) -> str | None:
    if not value:
        return None
    for part in value.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def _response_json(response: Any) -> Any:
    """Decode Canvas JSON, including SNU's ``while(1);`` response guard."""
    try:
        return response.json()
    except Exception as original:
        try:
            raw = response.text()
            return json.loads(JSON_GUARD.sub("", raw, count=1))
        except Exception:
            raise original


class CanvasApiAdapter:
    """LearningX is Canvas-compatible; use its authenticated API when exposed."""

    def __init__(self, context: Any, origin: str, *, timeout_seconds: float):
        self.context = context
        self.origin = origin.rstrip("/")
        self.timeout_ms = timeout_seconds * 1000

    def _get_pages(self, path: str) -> list[Any]:
        url = urljoin(f"{self.origin}/", path.lstrip("/"))
        values: list[Any] = []
        seen: set[str] = set()
        while url:
            if url in seen:
                raise DiscoveryError("eTL API pagination loop detected")
            seen.add(url)
            response = self.context.request.get(url, timeout=self.timeout_ms)
            if response.status == 401:
                raise AuthenticationRequired("the eTL session is missing or expired")
            if response.status == 403:
                raise DiscoveryError("eTL denied access to this content")
            if response.status == 404:
                raise AdapterUnavailable("Canvas API endpoint is not available")
            if not 200 <= response.status < 300:
                raise DiscoveryError(f"eTL API returned HTTP {response.status}")
            try:
                payload = _response_json(response)
            except Exception as exc:
                raise AdapterUnavailable("eTL API did not return JSON") from exc
            if not isinstance(payload, list):
                raise AdapterUnavailable("eTL API returned an unexpected object")
            values.extend(payload)
            url = _parse_next_link(response.headers.get("link"))
        return values

    def _get_object(self, path: str) -> dict[str, Any]:
        url = urljoin(f"{self.origin}/", path.lstrip("/"))
        response = self.context.request.get(url, timeout=self.timeout_ms)
        if response.status == 401:
            raise AuthenticationRequired("the eTL session is missing or expired")
        if response.status == 403:
            raise DiscoveryError("eTL denied access to this content")
        if response.status == 404:
            raise AdapterUnavailable("Canvas API endpoint is not available")
        if not 200 <= response.status < 300:
            raise DiscoveryError(f"eTL API returned HTTP {response.status}")
        try:
            payload = _response_json(response)
        except Exception as exc:
            raise AdapterUnavailable("eTL API did not return JSON") from exc
        if not isinstance(payload, dict):
            raise AdapterUnavailable("eTL API returned an unexpected object")
        return payload

    def probe(self) -> bool:
        response = self.context.request.get(
            f"{self.origin}/api/v1/users/self/profile", timeout=min(self.timeout_ms, 15_000)
        )
        if response.status in {401, 403}:
            raise AuthenticationRequired("the eTL session is missing or expired")
        if response.status != 200:
            return False
        try:
            payload = _response_json(response)
        except Exception:
            return False
        return isinstance(payload, dict) and payload.get("id") is not None

    def discover_courses(self) -> list[Course]:
        payload = self._get_pages(
            "/api/v1/courses?enrollment_state=active&state[]=available&"
            "include[]=term&include[]=syllabus_body&per_page=100"
        )
        courses: list[Course] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            course_id = str(item["id"])
            name = str(item.get("name") or item.get("course_code") or f"Course {course_id}")
            courses.append(
                Course(
                    course_id,
                    name,
                    f"{self.origin}/courses/{quote(course_id, safe='')}",
                    course_code=str(item.get("course_code") or "") or None,
                    semester=semester_from_term(item.get("term")),
                    starts_at=str(item.get("start_at") or "") or None,
                    ends_at=str(item.get("end_at") or "") or None,
                    syllabus_html=str(item.get("syllabus_body") or "") or None,
                )
            )
        return courses

    @staticmethod
    def _folder_paths(folders: Iterable[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
        by_id = {str(folder["id"]): folder for folder in folders if folder.get("id") is not None}
        cache: dict[str, tuple[str, ...]] = {}

        def resolve(folder_id: str, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
            if folder_id in cache:
                return cache[folder_id]
            folder = by_id.get(folder_id)
            if folder is None or folder_id in seen:
                return ()
            parent = folder.get("parent_folder_id")
            prefix = resolve(str(parent), seen | {folder_id}) if parent is not None else ()
            name = str(folder.get("name") or "").strip()
            # Canvas calls its invisible top-level folder "course files".
            if parent is None or name.casefold() == "course files":
                result = prefix
            else:
                result = (*prefix, name) if name else prefix
            cache[folder_id] = result
            return result

        for key in by_id:
            resolve(key)
        return cache

    def discover_files(self, course: Course) -> list[RemoteFile]:
        course_id = quote(course.remote_id, safe="")
        raw_folders = self._get_pages(f"/api/v1/courses/{course_id}/folders?per_page=100")
        folders = [item for item in raw_folders if isinstance(item, dict)]
        folder_paths = self._folder_paths(folders)
        raw_files = self._get_pages(f"/api/v1/courses/{course_id}/files?per_page=100")
        files: list[RemoteFile] = []
        for item in raw_files:
            if not isinstance(item, dict) or item.get("id") is None or not item.get("url"):
                continue
            remote_id = str(item["id"])
            name = str(item.get("display_name") or item.get("filename") or f"file-{remote_id}")
            folder_id = str(item.get("folder_id")) if item.get("folder_id") is not None else ""
            size_value = item.get("size")
            try:
                size = int(size_value) if size_value is not None else None
            except (TypeError, ValueError):
                size = None
            files.append(
                RemoteFile(
                    remote_id=remote_id,
                    course_id=course.remote_id,
                    name=name,
                    folder_path=folder_paths.get(folder_id, ()),
                    download_url=urljoin(f"{self.origin}/", str(item["url"])),
                    size=size,
                    updated_at=str(item.get("updated_at") or item.get("modified_at") or "") or None,
                    etag=str(item.get("etag") or "") or None,
                    content_type=str(item.get("content-type") or item.get("content_type") or "")
                    or None,
                )
            )
        return files

    def discover_articles(self, course: Course) -> list[ContentItem]:
        course_id = quote(course.remote_id, safe="")
        items: list[ContentItem] = []
        sources = (
            (
                "announcement",
                f"/api/v1/announcements?context_codes[]=course_{course_id}&active_only=true&per_page=100",
            ),
            ("page", f"/api/v1/courses/{course_id}/pages?per_page=100"),
        )
        successful_sources = 0
        for kind, path in sources:
            try:
                payload = self._get_pages(path)
            except AdapterUnavailable:
                # Courses commonly disable Pages or Announcements. An absent
                # optional endpoint is an empty source, not an operational error.
                continue
            except DiscoveryError as exc:
                LOGGER.warning("could not list %ss for course=%s error=%s", kind, course.name, exc)
                continue
            successful_sources += 1
            for item in payload:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                remote_id = str(item.get("id") or item.get("url") or title)
                item_url = str(item.get("html_url") or "")
                if not item_url and kind == "page" and item.get("url"):
                    item_url = f"{self.origin}/courses/{course_id}/pages/{quote(str(item['url']), safe='')}"
                detail = item
                if kind == "page" and not item.get("body") and item.get("url"):
                    try:
                        detail = self._get_object(
                            f"/api/v1/courses/{course_id}/pages/{quote(str(item['url']), safe='')}"
                        )
                    except (DiscoveryError, AdapterUnavailable) as exc:
                        LOGGER.warning(
                            "could not fetch page body course=%s page=%s error=%s",
                            course.name,
                            title,
                            exc,
                        )
                    except Exception as exc:
                        # Page detail is optional enrichment; retain the list
                        # result when a deployment omits this endpoint.
                        LOGGER.debug("page body enrichment unavailable: %s", exc)
                items.append(
                    ContentItem(
                        remote_id=remote_id,
                        course_id=course.remote_id,
                        kind=kind,
                        title=title,
                        url=urljoin(f"{self.origin}/", item_url),
                        published_at=str(
                            item.get("posted_at")
                            or item.get("published_at")
                            or item.get("updated_at")
                            or ""
                        )
                        or None,
                        updated_at=str(detail.get("updated_at") or "") or None,
                        body_html=str(detail.get("message") or detail.get("body") or "") or None,
                    )
                )
        if successful_sources == 0:
            raise AdapterUnavailable("article API endpoints are unavailable")
        return items

    def discover_modules(self, course: Course) -> list[ModuleItem]:
        course_id = quote(course.remote_id, safe="")
        modules = self._get_pages(
            f"/api/v1/courses/{course_id}/modules?include[]=items&"
            "include[]=content_details&per_page=100"
        )
        result: list[ModuleItem] = []
        for module in modules:
            if not isinstance(module, dict) or module.get("id") is None:
                continue
            module_id = str(module["id"])
            raw_items = module.get("items")
            if not isinstance(raw_items, list):
                raw_items = self._get_pages(
                    f"/api/v1/courses/{course_id}/modules/{quote(module_id, safe='')}/items?"
                    "include[]=content_details&per_page=100"
                )
            for item in raw_items:
                if not isinstance(item, dict) or item.get("id") is None:
                    continue
                lock_info = item.get("content_details")
                locked = bool(isinstance(lock_info, dict) and lock_info.get("locked_for_user"))
                result.append(
                    ModuleItem(
                        module_id=module_id,
                        module_name=str(module.get("name") or f"Module {module_id}"),
                        remote_id=str(item["id"]),
                        course_id=course.remote_id,
                        item_type=str(item.get("type") or "Unknown"),
                        title=str(item.get("title") or f"Item {item['id']}"),
                        position=int(item["position"]) if item.get("position") is not None else None,
                        content_id=str(item.get("content_id"))
                        if item.get("content_id") is not None
                        else None,
                        html_url=str(item.get("html_url") or "") or None,
                        external_url=str(item.get("external_url") or "") or None,
                        published=bool(item.get("published", True)),
                        locked=locked,
                    )
                )
        return result

    def discover_assignments(self, course: Course) -> list[ContentItem]:
        course_id = quote(course.remote_id, safe="")
        payload = self._get_pages(
            f"/api/v1/courses/{course_id}/assignments?order_by=due_at&per_page=100"
        )
        items: list[ContentItem] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            remote_id = str(item.get("id") or item["name"])
            items.append(
                ContentItem(
                    remote_id=remote_id,
                    course_id=course.remote_id,
                    kind="assignment",
                    title=str(item["name"]),
                    url=urljoin(f"{self.origin}/", str(item.get("html_url") or "")),
                    published_at=str(item.get("updated_at") or "") or None,
                    due_at=str(item.get("due_at") or "") or None,
                )
            )
        return items


@dataclass(slots=True)
class DomAdapter:
    context: Any
    page: Any
    origin: str
    timeout_seconds: float

    def discover_courses(self) -> list[Course]:
        with suppress(Exception):
            self.page.wait_for_selector(
                "a.ic-DashboardCard__link[href*='/courses/']",
                timeout=self.timeout_seconds * 1000,
            )
        dashboard_cards = self.page.locator(
            "a.ic-DashboardCard__link[href*='/courses/']"
        )
        anchors = (
            dashboard_cards
            if dashboard_cards.count()
            else self.page.locator("a[href*='/courses/']")
        )
        found: dict[str, Course] = {}
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                href = anchor.get_attribute("href") or ""
                match = COURSE_PATH.search(href)
                if not match:
                    continue
                course_id = match.group(1)
                path = urlsplit(urljoin(f"{self.origin}/", href)).path.rstrip("/")
                if path != f"/courses/{course_id}":
                    continue
                # Ignore links to individual course subpages captured by broad navigation.
                title = anchor.locator(".ic-DashboardCard__header-title").first
                name = ""
                if title.count():
                    name = (title.get_attribute("title") or title.inner_text() or "").strip()
                if not name:
                    name = (anchor.get_attribute("title") or anchor.inner_text() or "").strip()
                if not name:
                    continue
                found.setdefault(
                    course_id,
                    Course(course_id, name, f"{self.origin}/courses/{quote(course_id, safe='')}"),
                )
            except Exception:
                continue
        if not found:
            raise DiscoveryError("could not discover active courses from the dashboard")
        return list(found.values())

    def discover_files(self, course: Course) -> list[RemoteFile]:
        url = f"{course.url.rstrip('/')}/files"
        self.page.goto(url, wait_until="domcontentloaded")
        if "sso.snu.ac.kr" in self.page.url or "nsso.snu.ac.kr" in self.page.url:
            raise AuthenticationRequired("the eTL session expired while opening course files")
        with suppress(Exception):
            self.page.wait_for_function(
                """
                () => {
                  const body = document.body?.innerText || '';
                  return document.querySelector("a.ef-name-col__link[href*='/files/']")
                    || !body.includes('더 많은 결과 로드 중');
                }
                """,
                timeout=self.timeout_seconds * 1000,
            )
        anchors = self.page.locator(
            "a.ef-name-col__link[href*='/files/'], a[download][href*='/files/']"
        )
        found: dict[str, RemoteFile] = {}
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                href = anchor.get_attribute("href") or ""
                match = FILE_PATH.search(href)
                if not match:
                    continue
                remote_id = match.group(1)
                name = (
                    anchor.get_attribute("download")
                    or anchor.get_attribute("title")
                    or anchor.inner_text()
                    or f"file-{remote_id}"
                ).strip()
                folder_raw = anchor.get_attribute("data-folder-path") or ""
                folders = tuple(part for part in folder_raw.strip("/").split("/") if part)
                absolute = urljoin(f"{self.origin}/", href)
                if "/download" not in urlsplit(absolute).path and "download=" not in absolute:
                    absolute = f"{absolute.rstrip('/')}/download?download_frd=1"
                found.setdefault(
                    remote_id,
                    RemoteFile(
                        remote_id=remote_id,
                        course_id=course.remote_id,
                        name=name,
                        folder_path=folders,
                        download_url=absolute,
                    ),
                )
            except Exception:
                continue
        return list(found.values())

    def discover_articles(self, course: Course) -> list[ContentItem]:
        found: dict[tuple[str, str], ContentItem] = {}
        routes = (
            ("announcement", "announcements", re.compile(r"/discussion_topics/([^/?#]+)")),
            ("page", "pages", re.compile(r"/pages/([^/?#]+)")),
        )
        for kind, route, pattern in routes:
            self.page.goto(f"{course.url.rstrip('/')}/{route}", wait_until="domcontentloaded")
            anchors = self.page.locator(f"a[href*='/{route}/'], a[href*='/discussion_topics/']")
            for index in range(anchors.count()):
                anchor = anchors.nth(index)
                try:
                    href = anchor.get_attribute("href") or ""
                    match = pattern.search(href)
                    title = (anchor.inner_text() or anchor.get_attribute("title") or "").strip()
                    if not match or not title:
                        continue
                    remote_id = match.group(1)
                    found.setdefault(
                        (kind, remote_id),
                        ContentItem(
                            remote_id,
                            course.remote_id,
                            kind,
                            title,
                            urljoin(f"{self.origin}/", href),
                        ),
                    )
                except Exception:
                    continue
        return list(found.values())

    def discover_assignments(self, course: Course) -> list[ContentItem]:
        self.page.goto(f"{course.url.rstrip('/')}/assignments", wait_until="domcontentloaded")
        anchors = self.page.locator("a[href*='/assignments/']")
        found: dict[str, ContentItem] = {}
        pattern = re.compile(r"/assignments/([^/?#]+)")
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                href = anchor.get_attribute("href") or ""
                match = pattern.search(href)
                title = (anchor.inner_text() or anchor.get_attribute("title") or "").strip()
                if not match or not title:
                    continue
                remote_id = match.group(1)
                found.setdefault(
                    remote_id,
                    ContentItem(
                        remote_id,
                        course.remote_id,
                        "assignment",
                        title,
                        urljoin(f"{self.origin}/", href),
                    ),
                )
            except Exception:
                continue
        return list(found.values())

    def discover_modules(self, course: Course) -> list[ModuleItem]:
        self.page.goto(f"{course.url.rstrip('/')}/modules", wait_until="domcontentloaded")
        anchors = self.page.locator(
            "a[href*='/modules/items/'], a[href*='/external_tools/'], "
            "a[href*='/learningx/lti/']"
        )
        found: dict[str, ModuleItem] = {}
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                href = anchor.get_attribute("href") or ""
                title = (anchor.inner_text() or anchor.get_attribute("title") or "").strip()
                if not href or not title:
                    continue
                match = re.search(r"/(?:items|view)/(\d+)", href)
                remote_id = match.group(1) if match else str(index + 1)
                item_type = "ExternalTool" if "external" in href or "/lti/" in href else "Page"
                found.setdefault(
                    remote_id,
                    ModuleItem(
                        module_id="dom",
                        module_name="Modules",
                        remote_id=remote_id,
                        course_id=course.remote_id,
                        item_type=item_type,
                        title=title,
                        position=index + 1,
                        html_url=urljoin(f"{self.origin}/", href),
                        external_url=urljoin(f"{self.origin}/", href)
                        if item_type == "ExternalTool"
                        else None,
                    ),
                )
            except Exception:
                continue
        return list(found.values())


class DiscoveryService:
    def __init__(self, context: Any, page: Any, *, timeout_seconds: float):
        self.context = context
        self.page = page
        self.origin = origin_of(page.url)
        self.api = CanvasApiAdapter(context, self.origin, timeout_seconds=timeout_seconds)
        self.dom = DomAdapter(context, page, self.origin, timeout_seconds)
        self.use_api = False

    def discover_courses(self) -> list[Course]:
        try:
            courses = self.api.discover_courses()
            self.use_api = True
            LOGGER.info("using the authenticated LearningX/Canvas API")
            return courses
        except AdapterUnavailable:
            LOGGER.info("authenticated course API unavailable; using DOM discovery")
        self.use_api = False
        return self.dom.discover_courses()

    def discover_files(self, course: Course) -> list[RemoteFile]:
        if self.use_api:
            try:
                return self.api.discover_files(course)
            except DiscoveryError:
                LOGGER.warning("API unavailable for course=%s; using DOM fallback", course.name)
        return self.dom.discover_files(course)

    def discover_articles(self, course: Course) -> list[ContentItem]:
        if self.use_api:
            try:
                return self.api.discover_articles(course)
            except DiscoveryError:
                LOGGER.warning("API unavailable for articles in course=%s", course.name)
        return self.dom.discover_articles(course)

    def discover_assignments(self, course: Course) -> list[ContentItem]:
        if self.use_api:
            try:
                return self.api.discover_assignments(course)
            except DiscoveryError:
                LOGGER.warning("API unavailable for assignments in course=%s", course.name)
        return self.dom.discover_assignments(course)

    def discover_modules(self, course: Course) -> list[ModuleItem]:
        if self.use_api:
            try:
                return self.api.discover_modules(course)
            except DiscoveryError:
                LOGGER.warning("API unavailable for modules in course=%s", course.name)
        return self.dom.discover_modules(course)
