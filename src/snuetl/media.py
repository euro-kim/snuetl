from __future__ import annotations

import html
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit

from .browser_contracts import BrowserContextLike, BrowserPageLike
from .models import Course, ModuleItem

MEDIA_SUFFIXES = {".m3u8", ".mpd", ".mp4", ".webm", ".m4v"}
LEARNINGX_SESSION_COOKIES = {"xn_api_token", "laravel_session", "pni_token", "XSRF-TOKEN"}
ATTENDANCE_ITEM = re.compile(r"lecture_attendance/items/view/(\d+)", re.IGNORECASE)
CONTENT_ID = re.compile(
    r"(?:var\s+content_id\s*=\s*['\"]|[?&]content_id=)([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    url: str
    referer: str


@dataclass(frozen=True, slots=True)
class MediaRequest:
    context: BrowserContextLike
    page: BrowserPageLike
    course: Course
    item: ModuleItem


class MediaResolver(Protocol):
    def resolve(self, request: MediaRequest) -> tuple[MediaCandidate, ...]: ...


def write_cookie_file(context: BrowserContextLike, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="snuetl-cookies-",
        suffix=".txt",
        dir=state_dir,
        delete=False,
    ) as handle:
        path = Path(handle.name)
        handle.write("# Netscape HTTP Cookie File\n")
        for cookie in context.cookies():
            domain = str(cookie.get("domain") or "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expires = int(float(cookie.get("expires") or 0))
            name = str(cookie.get("name") or "").replace("\t", "")
            value = str(cookie.get("value") or "").replace("\t", "")
            handle.write(
                "\t".join(
                    (
                        domain,
                        include_subdomains,
                        str(cookie.get("path") or "/"),
                        secure,
                        str(max(expires, 0)),
                        name,
                        value,
                    )
                )
                + "\n"
            )
    path.chmod(0o600)
    return path


def referer_for(url: str, fallback: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    if host == "lcms.snu.ac.kr" or host.endswith(".naverncp.com"):
        return "https://lcms.snu.ac.kr/"
    return fallback


def append_media_candidate(candidates: list[tuple[str, str]], url: object, referer: str) -> None:
    value = str(url or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return
    if (parts.hostname or "").casefold() == "lcms.snu.ac.kr" and parts.path.casefold().endswith(
        "/viewer/uniplayer/preloader.mp4"
    ):
        return
    candidate = (value, referer_for(value, referer))
    if candidate not in candidates:
        candidates.append(candidate)


def is_lcms_lecture_url(url: object) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").casefold()
    return host == "edge.naverncp.com" or host.endswith(".edge.naverncp.com")


def activate_lcms_player(page: BrowserPageLike, *, timeout_ms: int = 15_000) -> bool:
    clicked = False
    for frame in page.frames:
        if (urlsplit(str(frame.url or "")).hostname or "").casefold() != "lcms.snu.ac.kr":
            continue
        try:
            controls = frame.locator(".vc-front-screen-play-btn")
            for index in range(controls.count()):
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                control.click(force=True, timeout=5_000)
                clicked = True
                break
        except Exception:
            continue
        if clicked:
            break
    if not clicked:
        return False

    elapsed = 0
    while elapsed < timeout_ms:
        for frame in page.frames:
            try:
                values = frame.locator("video.vc-vplay-video1").evaluate_all(
                    "nodes => nodes.map(node => node.currentSrc || node.src || '')"
                )
                if any(is_lcms_lecture_url(value) for value in values):
                    return True
            except Exception:
                pass
        page.wait_for_timeout(500)
        elapsed += 500
    return False


def dom_media_candidates(page: BrowserPageLike) -> tuple[list[tuple[str, str]], list[str]]:
    candidates: list[tuple[str, str]] = []
    content_ids: list[str] = []
    for frame in page.frames:
        frame_url = str(frame.url or "")
        parts = urlsplit(frame_url)
        referer = (
            f"{parts.scheme}://{parts.netloc}/"
            if parts.scheme in {"http", "https"} and parts.netloc
            else "https://lcms.snu.ac.kr/"
        )
        try:
            lcms_frame = (parts.hostname or "").casefold() == "lcms.snu.ac.kr"
            selector = "video.vc-vplay-video1" if lcms_frame else "video, video source"
            values = frame.locator(selector).evaluate_all(
                """
                nodes => nodes.flatMap(node => [
                  node.currentSrc || '', node.src || '', node.getAttribute('src') || ''
                ]).filter(Boolean)
                """
            )
            for value in values:
                if lcms_frame and not is_lcms_lecture_url(value):
                    continue
                append_media_candidate(candidates, value, referer)
        except Exception:
            pass
        try:
            source = frame.content()
        except Exception:
            source = ""
        for match in CONTENT_ID.finditer(source):
            if match.group(1) not in content_ids:
                content_ids.append(match.group(1))
    return candidates, content_ids


def learningx_content_id(
    context: BrowserContextLike, course: Course, item: ModuleItem
) -> str | None:
    match = ATTENDANCE_ITEM.search(item.external_url or "")
    if match is None:
        return None
    try:
        token = next(
            (
                str(cookie.get("value") or "")
                for cookie in context.cookies()
                if cookie.get("name") == "xn_api_token" and cookie.get("value")
            ),
            None,
        )
        if not token:
            return None
        parts = urlsplit(course.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        response = context.request.get(
            f"{origin}/learningx/api/v1/courses/{quote(course.remote_id, safe='')}"
            f"/attendance_items/{quote(match.group(1), safe='')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20_000,
        )
        if response.status != 200:
            return None
        payload = response.json()
        content_id = (payload.get("item_content_data") or {}).get("content_id")
        return str(content_id) if content_id else None
    except Exception:
        return None


def cms_media_candidate(context: BrowserContextLike, content_id: str) -> tuple[str, str] | None:
    try:
        response = context.request.get(
            "https://lcms.snu.ac.kr/viewer/ssplayer/uniplayer_support/content.php"
            f"?content_id={quote(content_id, safe='')}",
            headers={"Referer": "https://lcms.snu.ac.kr/"},
            timeout=20_000,
        )
        if response.status != 200:
            return None
        body = html.unescape(response.text())
        match = re.search(
            r'method=["\']progressive["\'][^>]*>([^<]+)\[MEDIA_FILE\]',
            body,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return urljoin(str(response.url), match.group(1).strip() + "screen.mp4"), (
            "https://lcms.snu.ac.kr/"
        )
    except Exception:
        return None


def clear_stale_learningx_cookies(context: BrowserContextLike) -> None:
    try:
        present = {
            str(cookie.get("name") or "")
            for cookie in context.cookies()
            if cookie.get("name") in LEARNINGX_SESSION_COOKIES
        }
        for name in present:
            context.clear_cookies(name=name)
    except Exception:
        pass


class DomMediaResolver:
    def resolve(self, request: MediaRequest) -> tuple[MediaCandidate, ...]:
        candidates, _ = dom_media_candidates(request.page)
        return tuple(MediaCandidate(*candidate) for candidate in candidates)


class LearningXMediaResolver:
    def resolve(self, request: MediaRequest) -> tuple[MediaCandidate, ...]:
        content_id = learningx_content_id(request.context, request.course, request.item)
        if content_id is None:
            return ()
        candidate = cms_media_candidate(request.context, content_id)
        return (MediaCandidate(*candidate),) if candidate is not None else ()
