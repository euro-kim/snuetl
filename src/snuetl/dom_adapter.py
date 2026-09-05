from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from .errors import AuthenticationRequired, DiscoveryError
from .models import ContentItem, Course, ModuleItem, RemoteFile

COURSE_PATH = re.compile(r"/courses/([^/?#]+)")
FILE_PATH = re.compile(r"/files/([^/?#]+)(?:/download)?")


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
        dashboard_cards = self.page.locator("a.ic-DashboardCard__link[href*='/courses/']")
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
            "a[href*='/modules/items/'], a[href*='/external_tools/'], a[href*='/learningx/lti/']"
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
