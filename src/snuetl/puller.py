from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .browser import (
    authenticated_entry_url,
    ensure_authenticated_page,
    open_authenticated_browser,
)
from .catalog import inspect_catalog, select_courses
from .config import Config
from .downloader import AuthenticatedDownloader
from .logging_utils import redact
from .models import ContentItem, Course, ModuleItem, RemoteFile, SyncSummary
from .paths import course_content_dir, sanitize_component
from .profile import profile_lock
from .state import StateStore
from .syncer import _sync_file

LOGGER = logging.getLogger(__name__)
SYLLABUS_NAME = re.compile(r"syllabus|course[ _-]*outline|강의\s*계획(?:서)?", re.IGNORECASE)
MEDIA_SUFFIXES = {".m3u8", ".mpd", ".mp4", ".webm", ".m4v"}


@dataclass(slots=True)
class PullPlan:
    kinds: tuple[str, ...]
    courses: tuple[Course, ...]
    files: tuple[tuple[Course, RemoteFile], ...] = ()
    articles: tuple[tuple[Course, ContentItem], ...] = ()
    modules: tuple[tuple[Course, ModuleItem], ...] = ()

    @property
    def video_items(self) -> tuple[tuple[Course, ModuleItem], ...]:
        return tuple(
            pair
            for pair in self.modules
            if pair[1].item_type.casefold() == "externaltool" and pair[1].published
        )

    @property
    def uploaded_media(self) -> tuple[tuple[Course, RemoteFile], ...]:
        return tuple(
            pair
            for pair in self.files
            if (pair[1].content_type or "").casefold().startswith(("video/", "audio/"))
        )

    @property
    def syllabus_files(self) -> tuple[tuple[Course, RemoteFile], ...]:
        return tuple(pair for pair in self.files if SYLLABUS_NAME.search(pair[1].display_path))


@dataclass(slots=True)
class PullSummary:
    courses: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    skipped: int = 0
    failed: int = 0
    artifacts: list[str] = field(default_factory=list)
    warnings: list[dict[str, object]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.failed or self.conflicts or self.warnings)


def _selected_courses(
    courses: tuple[Course, ...], selectors: tuple[str, ...], semesters: tuple[str, ...]
) -> tuple[Course, ...]:
    selected = list(courses)
    if selectors:
        by_id: dict[str, Course] = {}
        for selector in selectors:
            for course in select_courses(list(courses), selector):
                by_id[course.remote_id] = course
        selected = list(by_id.values())
    if semesters:
        wanted = {value.casefold() for value in semesters}
        selected = [
            course
            for course in selected
            if course.semester and course.semester.semester_code.casefold() in wanted
        ]
    return tuple(selected)


def discover_pull_plan(
    config: Config,
    kinds: tuple[str, ...],
    *,
    course_selectors: tuple[str, ...] = (),
    semesters: tuple[str, ...] = (),
) -> PullPlan:
    course_result = inspect_catalog(config, kind="courses", headless=True)
    courses = _selected_courses(course_result.courses, course_selectors, semesters)
    course_ids = {course.remote_id for course in courses}
    files: tuple[tuple[Course, RemoteFile], ...] = ()
    articles: tuple[tuple[Course, ContentItem], ...] = ()
    modules: tuple[tuple[Course, ModuleItem], ...] = ()
    if set(kinds) & {"files", "syllabus", "videos"}:
        result = inspect_catalog(config, kind="files", headless=True)
        files = tuple(pair for pair in result.files if pair[0].remote_id in course_ids)
    if "articles" in kinds:
        result = inspect_catalog(config, kind="articles", headless=True)
        articles = tuple(pair for pair in result.items if pair[0].remote_id in course_ids)
    if "videos" in kinds:
        result = inspect_catalog(config, kind="videos", headless=True)
        modules = tuple(pair for pair in result.modules if pair[0].remote_id in course_ids)
    return PullPlan(kinds, courses, files, articles, modules)


def plan_data(plan: PullPlan) -> dict[str, object]:
    known_video_bytes = sum(remote.size or 0 for _, remote in plan.uploaded_media)
    return {
        "kinds": list(plan.kinds),
        "courses": len(plan.courses),
        "files": len(plan.files),
        "articles": len(plan.articles),
        "syllabus_files": len(plan.syllabus_files),
        "video_items": len(plan.video_items) + len(plan.uploaded_media),
        "known_video_bytes": known_video_bytes,
        "video_size_complete": not plan.video_items,
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def _managed_write(
    store: StateStore,
    summary: PullSummary,
    *,
    artifact_type: str,
    course: Course,
    source_id: str,
    destination: Path,
    body: bytes,
    source_revision: str,
    force: bool,
) -> Path:
    existing = store.get_artifact(artifact_type, course.remote_id, source_id)
    if existing is not None and existing["local_path"]:
        destination = Path(str(existing["local_path"]))
    body_hash = _sha256_bytes(body)
    if existing is not None and destination.exists():
        local_hash = _sha256_file(destination)
        if existing["source_revision"] == source_revision and local_hash == existing["sha256"]:
            summary.unchanged += 1
            summary.artifacts.append(str(destination))
            return destination
        locally_edited = bool(existing["sha256"] and local_hash != existing["sha256"])
        if locally_edited and not force:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            conflict = destination.with_name(f"{destination.stem}.remote-{stamp}{destination.suffix}")
            _atomic_write(conflict, body)
            summary.conflicts += 1
            summary.artifacts.append(str(conflict))
            summary.warnings.append(
                {
                    "code": "LOCAL_EDIT_CONFLICT",
                    "message": "Local edits were preserved; the remote version was written beside them.",
                    "path": str(destination),
                    "remote_path": str(conflict),
                }
            )
            return conflict
        action = "updated"
    else:
        action = "created"
    _atomic_write(destination, body)
    store.record_artifact(
        artifact_type=artifact_type,
        course_id=course.remote_id,
        source_id=source_id,
        local_path=destination,
        source_revision=source_revision,
        sha256=body_hash,
        size_bytes=len(body),
        status="ok",
    )
    if action == "created":
        summary.created += 1
    else:
        summary.updated += 1
    summary.artifacts.append(str(destination))
    return destination


def _front_matter(course: Course, item: ContentItem) -> str:
    import yaml

    metadata = {
        "schema_version": "1",
        "content_type": item.kind,
        "content_id": item.remote_id,
        "title": item.title,
        "course_id": course.remote_id,
        "course_name": course.display_name,
        "semester": course.semester.semester_code if course.semester else None,
        "source_url": item.url,
        "published_at": item.published_at,
        "updated_at": item.updated_at,
    }
    return "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n"


def _download_article_assets(context: object, html: str, base_url: str, asset_dir: Path) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    asset_dir.mkdir(parents=True, exist_ok=True)
    for tag, attribute in (("img", "src"), ("source", "src"), ("a", "href")):
        for node in soup.find_all(tag):
            raw = node.get(attribute)
            if not isinstance(raw, str) or not raw:
                continue
            url = urljoin(base_url, raw)
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                continue
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
                }
            except (OSError, ValueError):
                continue
            if not addresses or any(
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                for address in addresses
            ):
                LOGGER.warning("blocked non-public article asset host=%s", parts.hostname)
                continue
            suffix = Path(parts.path).suffix.casefold()
            if tag == "a" and suffix not in {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".zip"}:
                continue
            try:
                response = context.request.get(url, timeout=45_000)
                if not 200 <= response.status < 300:
                    continue
                body = response.body()
                name = sanitize_component(Path(parts.path).name or f"asset-{len(body)}")
                destination = asset_dir / name
                if destination.exists() and _sha256_file(destination) != _sha256_bytes(body):
                    destination = asset_dir / f"{destination.stem}--{_sha256_bytes(body)[:8]}{destination.suffix}"
                _atomic_write(destination, body)
                node[attribute] = f"assets/{destination.name}"
            except Exception as exc:
                LOGGER.warning("could not localize article asset host=%s error=%s", parts.hostname, exc)
    for node in soup(["script", "style"]):
        node.decompose()
    return str(soup)


def _pull_articles(
    config: Config, plan: PullPlan, summary: PullSummary, *, root: Path, force: bool, dry_run: bool
) -> None:
    if not plan.articles:
        return
    if dry_run:
        summary.skipped += len(plan.articles)
        return
    from markdownify import markdownify

    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=True) as (context, page),
        StateStore(config.database_path) as store,
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        ensure_authenticated_page(config, context, page)
        for course, item in plan.articles:
            base = course_content_dir(
                root,
                course.display_name,
                course.remote_id,
                course.semester.semester_code if course.semester else None,
                "articles",
            )
            kind_dir = base / sanitize_component(item.kind)
            filename = f"{sanitize_component(item.title, limit=80)}--{sanitize_component(item.remote_id)}.md"
            html = item.body_html or ""
            localized = _download_article_assets(context, html, item.url, kind_dir / "assets")
            markdown = markdownify(localized, heading_style="ATX").strip()
            body = (_front_matter(course, item) + markdown + "\n").encode()
            revision = _sha256_bytes(
                json.dumps(
                    [item.title, item.updated_at, item.published_at, item.body_html],
                    ensure_ascii=False,
                ).encode()
            )
            with store.transaction():
                _managed_write(
                    store,
                    summary,
                    artifact_type="article",
                    course=course,
                    source_id=f"{item.kind}:{item.remote_id}",
                    destination=kind_dir / filename,
                    body=body,
                    source_revision=revision,
                    force=force,
                )


def _download_remote_artifact(
    downloader: AuthenticatedDownloader,
    store: StateStore,
    summary: PullSummary,
    *,
    artifact_type: str,
    course: Course,
    remote: RemoteFile,
    destination: Path,
) -> Path:
    existing = store.get_artifact(artifact_type, course.remote_id, remote.remote_id)
    if (
        existing is not None
        and existing["source_revision"] == remote.revision
        and Path(str(existing["local_path"])).is_file()
    ):
        summary.unchanged += 1
        summary.artifacts.append(str(existing["local_path"]))
        return Path(str(existing["local_path"]))
    result = downloader.download(remote, destination)
    store.record_artifact(
        artifact_type=artifact_type,
        course_id=course.remote_id,
        source_id=remote.remote_id,
        local_path=destination,
        source_revision=remote.revision,
        sha256=result.sha256,
        size_bytes=result.size,
        status="ok",
    )
    summary.created += 1
    summary.artifacts.append(str(destination))
    return destination


def _pull_files(
    config: Config, plan: PullPlan, summary: PullSummary, *, root: Path, dry_run: bool
) -> None:
    if "files" not in plan.kinds:
        return
    if dry_run:
        summary.skipped += len(plan.files)
        return
    sync_summary = SyncSummary(courses=len(plan.courses))
    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=True) as (context, page),
        StateStore(config.database_path) as store,
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        ensure_authenticated_page(config, context, page)
        user_agent = page.evaluate("navigator.userAgent")
        with AuthenticatedDownloader(
            context,
            user_agent=user_agent,
            timeout_seconds=config.timeout_seconds,
            retry_count=config.retry_count,
        ) as downloader:
            pull_config = replace(config, download_dir=root)
            for course, remote in plan.files:
                _sync_file(pull_config, store, downloader, course, remote, sync_summary)
    summary.created += sync_summary.downloaded
    summary.updated += sync_summary.updated
    summary.unchanged += sync_summary.unchanged
    summary.failed += sync_summary.failed


def _pull_syllabi(
    config: Config, plan: PullPlan, summary: PullSummary, *, root: Path, force: bool, dry_run: bool
) -> None:
    if "syllabus" not in plan.kinds:
        return
    if dry_run:
        summary.skipped += len(plan.courses) + len(plan.syllabus_files)
        return
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    syllabus_files: dict[str, list[RemoteFile]] = {}
    for course, remote in plan.syllabus_files:
        syllabus_files.setdefault(course.remote_id, []).append(remote)
    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=True) as (context, page),
        StateStore(config.database_path) as store,
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        ensure_authenticated_page(config, context, page)
        user_agent = page.evaluate("navigator.userAgent")
        with AuthenticatedDownloader(
            context,
            user_agent=user_agent,
            timeout_seconds=config.timeout_seconds,
            retry_count=config.retry_count,
        ) as downloader:
            for course in plan.courses:
                base = course_content_dir(
                    root,
                    course.display_name,
                    course.remote_id,
                    course.semester.semester_code if course.semester else None,
                    "syllabus",
                )
                base.mkdir(parents=True, exist_ok=True)
                source_html = course.syllabus_html or ""
                source_url = f"{course.url.rstrip('/')}/assignments/syllabus"
                soup = BeautifulSoup(source_html, "html.parser")
                iframe = soup.find("iframe", src=True)
                if iframe is not None:
                    candidate = urljoin(source_url, str(iframe["src"]))
                    try:
                        response = context.request.get(candidate, timeout=45_000)
                        if 200 <= response.status < 300:
                            source_html = response.text()
                            source_url = candidate
                    except Exception as exc:
                        summary.warnings.append(
                            {"code": "SYLLABUS_SOURCE_FAILED", "message": str(exc), "course_id": course.remote_id}
                        )
                if source_html:
                    item = ContentItem(
                        "official",
                        course.remote_id,
                        "syllabus",
                        "Official syllabus",
                        source_url,
                        body_html=source_html,
                    )
                    markdown = markdownify(source_html, heading_style="ATX").strip()
                    body = (_front_matter(course, item) + markdown + "\n").encode()
                    with store.transaction():
                        _managed_write(
                            store,
                            summary,
                            artifact_type="syllabus",
                            course=course,
                            source_id="official",
                            destination=base / "official.md",
                            body=body,
                            source_revision=_sha256_bytes(source_html.encode()),
                            force=force,
                        )
                    try:
                        render_page = context.new_page()
                        render_page.goto(source_url, wait_until="networkidle")
                        pdf = render_page.pdf(format="A4", print_background=True)
                        render_page.close()
                        with store.transaction():
                            _managed_write(
                                store,
                                summary,
                                artifact_type="syllabus_pdf",
                                course=course,
                                source_id="official-pdf",
                                destination=base / "official.pdf",
                                body=pdf,
                                source_revision=_sha256_bytes(source_html.encode()),
                                force=force,
                            )
                    except Exception as exc:
                        summary.warnings.append(
                            {"code": "SYLLABUS_PDF_FAILED", "message": str(exc), "course_id": course.remote_id}
                        )
                else:
                    summary.warnings.append(
                        {"code": "SYLLABUS_MISSING", "message": "No official syllabus body was available.", "course_id": course.remote_id}
                    )
                seen_hashes: dict[str, str] = {}
                sources: list[dict[str, object]] = []
                for remote in syllabus_files.get(course.remote_id, []):
                    destination = base / sanitize_component(remote.name)
                    try:
                        with store.transaction():
                            pulled = _download_remote_artifact(
                                downloader,
                                store,
                                summary,
                                artifact_type="syllabus_file",
                                course=course,
                                remote=remote,
                                destination=destination,
                            )
                        digest = _sha256_file(pulled)
                        if digest in seen_hashes:
                            pulled.unlink(missing_ok=True)
                            canonical = seen_hashes[digest]
                            with store.transaction():
                                store.record_artifact(
                                    artifact_type="syllabus_file",
                                    course_id=course.remote_id,
                                    source_id=remote.remote_id,
                                    local_path=Path(canonical),
                                    source_revision=remote.revision,
                                    sha256=digest,
                                    size_bytes=remote.size,
                                    status="deduplicated",
                                )
                            sources.append({"file_id": remote.remote_id, "path": canonical, "deduplicated": True})
                        else:
                            seen_hashes[digest] = str(pulled)
                            sources.append({"file_id": remote.remote_id, "path": str(pulled), "deduplicated": False})
                    except Exception as exc:
                        summary.failed += 1
                        sources.append({"file_id": remote.remote_id, "error": str(exc)})
                manifest = {
                    "schema_version": "1",
                    "course_id": course.remote_id,
                    "official_source": source_url if source_html else None,
                    "uploaded_sources": sources,
                }
                manifest_body = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                with store.transaction():
                    _managed_write(
                        store,
                        summary,
                        artifact_type="syllabus_manifest",
                        course=course,
                        source_id="manifest",
                        destination=base / "manifest.json",
                        body=manifest_body,
                        source_revision=_sha256_bytes(manifest_body),
                        force=force,
                    )


def _write_cookie_file(context: object, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="snuetl-cookies-", suffix=".txt", dir=state_dir, delete=False
    )
    with handle:
        handle.write("# Netscape HTTP Cookie File\n")
        for cookie in context.cookies():
            domain = str(cookie.get("domain") or "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expires = int(float(cookie.get("expires") or 0))
            name = str(cookie.get("name") or "").replace("\t", "")
            value = str(cookie.get("value") or "").replace("\t", "")
            handle.write(
                "\t".join((domain, include_subdomains, str(cookie.get("path") or "/"), secure, str(max(expires, 0)), name, value)) + "\n"
            )
    Path(handle.name).chmod(0o600)
    return Path(handle.name)


def _pull_videos(
    config: Config,
    plan: PullPlan,
    summary: PullSummary,
    *,
    root: Path,
    dry_run: bool,
    max_height: int | None,
    captions: bool,
    jobs: int,
) -> None:
    if "videos" not in plan.kinds:
        return
    if dry_run:
        summary.skipped += len(plan.video_items) + len(plan.uploaded_media)
        return
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - installation problem
        raise RuntimeError("yt-dlp is not installed; reinstall snuetl with dependencies") from exc

    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=True) as (context, page),
        StateStore(config.database_path) as store,
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        ensure_authenticated_page(config, context, page)
        cookie_file = _write_cookie_file(context, config.state_dir)
        try:
            user_agent = page.evaluate("navigator.userAgent")
            with AuthenticatedDownloader(
                context,
                user_agent=user_agent,
                timeout_seconds=config.timeout_seconds,
                retry_count=config.retry_count,
            ) as downloader:
                for course, remote in plan.uploaded_media:
                    base = course_content_dir(
                        root,
                        course.display_name,
                        course.remote_id,
                        course.semester.semester_code if course.semester else None,
                        "videos",
                    )
                    try:
                        with store.transaction():
                            _download_remote_artifact(
                                downloader,
                                store,
                                summary,
                                artifact_type="video_file",
                                course=course,
                                remote=remote,
                                destination=base / sanitize_component(remote.name),
                            )
                    except Exception as exc:
                        summary.failed += 1
                        summary.warnings.append(
                            {
                                "code": "VIDEO_FILE_FAILED",
                                "message": str(redact(exc)),
                                "course_id": course.remote_id,
                                "video_id": remote.remote_id,
                            }
                        )
            for course, item in plan.video_items:
                if item.locked:
                    summary.skipped += 1
                    summary.warnings.append(
                        {"code": "VIDEO_LOCKED", "message": item.title, "course_id": course.remote_id}
                    )
                    continue
                launch_url = item.external_url or item.html_url
                if not launch_url:
                    summary.skipped += 1
                    continue
                base = course_content_dir(
                    root,
                    course.display_name,
                    course.remote_id,
                    course.semester.semester_code if course.semester else None,
                    "videos",
                )
                stem = f"{sanitize_component(item.module_name, limit=50)}--{sanitize_component(item.title, limit=80)}--{sanitize_component(item.remote_id)}"
                base.mkdir(parents=True, exist_ok=True)
                revision = urlsplit(item.html_url or item.external_url or item.remote_id).path
                existing = store.get_artifact("video", course.remote_id, item.remote_id)
                if (
                    existing is not None
                    and existing["source_revision"] == revision
                    and Path(str(existing["local_path"])).is_file()
                ):
                    summary.unchanged += 1
                    summary.artifacts.append(str(existing["local_path"]))
                    continue
                captured: list[str] = []

                def observe(response: object) -> None:
                    try:
                        content_type = str(response.headers.get("content-type") or "").casefold()
                        suffix = Path(urlsplit(response.url).path).suffix.casefold()
                        if suffix in MEDIA_SUFFIXES or "mpegurl" in content_type or "dash+xml" in content_type:
                            captured.append(str(response.url))
                    except Exception:
                        pass

                page.on("response", observe)
                try:
                    try:
                        page.goto(launch_url, wait_until="domcontentloaded")
                        page.wait_for_timeout(5_000)
                        candidates = [page.url, *captured]
                        for frame in page.frames:
                            if frame.url and frame.url not in candidates:
                                candidates.append(frame.url)
                    except Exception as exc:
                        summary.failed += 1
                        summary.warnings.append(
                            {
                                "code": "VIDEO_LAUNCH_FAILED",
                                "message": str(redact(exc)),
                                "course_id": course.remote_id,
                                "video_id": item.remote_id,
                            }
                        )
                        continue
                finally:
                    page.remove_listener("response", observe)
                format_selector = (
                    "bestvideo+bestaudio/best"
                    if max_height is None
                    else f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"
                )
                options = {
                    "quiet": True,
                    "no_warnings": True,
                    "continuedl": True,
                    "cookiefile": str(cookie_file),
                    "format": format_selector,
                    "merge_output_format": "mp4",
                    "outtmpl": str(base / f"{stem}.%(ext)s"),
                    "writesubtitles": captions,
                    "writeautomaticsub": captions,
                    "subtitleslangs": ["all"],
                    "subtitlesformat": "vtt",
                    "concurrent_fragment_downloads": jobs,
                }
                error: Exception | None = None
                before = set(base.glob(f"{stem}.*"))
                for candidate in candidates:
                    try:
                        with yt_dlp.YoutubeDL(options) as ydl:
                            ydl.download([candidate])
                        error = None
                        break
                    except Exception as exc:
                        error = exc
                if error is not None:
                    message = str(redact(error))
                    code = "VIDEO_DRM_OR_UNSUPPORTED" if re.search(r"DRM|encrypted", message, re.IGNORECASE) else "VIDEO_DOWNLOAD_FAILED"
                    summary.failed += 1
                    summary.warnings.append(
                        {"code": code, "message": message, "course_id": course.remote_id, "video_id": item.remote_id}
                    )
                    continue
                produced = [path for path in set(base.glob(f"{stem}.*")) if path.is_file()]
                media = next((path for path in produced if path.suffix.casefold() in {".mp4", ".mkv", ".webm", ".m4a"}), None)
                if media is None:
                    summary.failed += 1
                    continue
                with store.transaction():
                    store.record_artifact(
                        artifact_type="video",
                        course_id=course.remote_id,
                        source_id=item.remote_id,
                        local_path=media,
                        source_revision=revision,
                        sha256=_sha256_file(media),
                        size_bytes=media.stat().st_size,
                        status="ok",
                    )
                if media in before:
                    summary.unchanged += 1
                else:
                    summary.created += 1
                summary.artifacts.append(str(media))
        finally:
            cookie_file.unlink(missing_ok=True)


def execute_pull(
    config: Config,
    plan: PullPlan,
    *,
    root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    max_height: int | None = 1080,
    captions: bool = True,
    jobs: int = 1,
) -> PullSummary:
    destination = (root or config.download_dir).expanduser().resolve()
    summary = PullSummary(courses=len(plan.courses))
    _pull_files(config, plan, summary, root=destination, dry_run=dry_run)
    _pull_articles(config, plan, summary, root=destination, force=force, dry_run=dry_run)
    _pull_syllabi(config, plan, summary, root=destination, force=force, dry_run=dry_run)
    _pull_videos(
        config,
        plan,
        summary,
        root=destination,
        dry_run=dry_run,
        max_height=max_height,
        captions=captions,
        jobs=jobs,
    )
    return summary
