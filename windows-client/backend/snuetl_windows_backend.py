"""Private JSON-RPC worker for the SNUETL Windows Cloud Files provider.

Secrets and remote URLs stay in this process and its private application-data
directory. The protocol emits one JSON object per line and never logs tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import traceback
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, TextIO
from urllib.parse import urljoin, urlsplit

import httpx
import yaml
from bs4 import BeautifulSoup
from markdownify import markdownify

import windows_auth as codex_desktop
import shutil
import subprocess
from snuetl import __version__
from snuetl.canvas_api import TOKEN_PATTERN, CanvasClient, CanvasToken, _http_json
from snuetl.config import Config
from snuetl.downloader import TokenDownloader
from snuetl.errors import AuthenticationRequired, DiscoveryError
from snuetl.models import ContentItem, Course, RemoteFile
from snuetl.token_discovery import TokenDiscoveryAdapter

PROTOCOL_VERSION = 1
SERVICE = "snuetl-windows"
PURPOSE = "snuetl-windows"
ALLOWED_HOSTS = ("myetl.snu.ac.kr", "etl.snu.ac.kr")
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
RESERVED_WINDOWS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def data_dir() -> Path:
    override = os.environ.get("SNUETL_WINDOWS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise BackendError("SYSTEM_CONFIG", "Windows LOCALAPPDATA is unavailable")
    return Path(root) / "SNUETL"


def _prepare_desktop_helper() -> None:
    # Reuse the already-tested browser/token flow while keeping this client's
    # metadata separate from the Codex skill installation.
    codex_desktop.data_dir = data_dir
    codex_desktop.SERVICE = SERVICE
    codex_desktop.PURPOSE = PURPOSE


def _config() -> Config:
    root = data_dir()
    return Config(
        base_url="https://etl.snu.ac.kr/login",
        download_dir=root,
        state_dir=root,
        browser_channel=None,
        browser_executable_path=None,
        headless=False,
        timeout_seconds=45,
        login_timeout_seconds=600,
        retry_count=2,
        excluded_course_ids=frozenset(),
        setup_complete=True,
        canvas_api_enabled=True,
    )


def _token() -> CanvasToken:
    _prepare_desktop_helper()
    token = codex_desktop._load_token()
    if token is None:
        raise BackendError("NOT_CONNECTED", "Connect an SNU eTL account first")
    try:
        expires = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackendError("ACCOUNT_DATA", "Saved account expiry is invalid") from exc
    if expires <= datetime.now(UTC):
        raise BackendError("TOKEN_EXPIRED", "Canvas access expired; reconnect the account")
    return token


def auth_status(verify: bool = False) -> dict[str, Any]:
    _prepare_desktop_helper()
    result = codex_desktop._status(verify=verify)
    if verify and result.get("valid") is False:
        result["ready"] = False
    if verify and result.get("valid") is True:
        codex_desktop._cleanup_credentials(keep=codex_desktop._load_token())
    if result.get("configured"):
        metadata = codex_desktop._load_metadata()
        if metadata:
            result["manual"] = metadata.token_id.startswith("manual:")
    return result


def addon_executable() -> Path:
    return data_dir() / "addons" / "signin" / "snuetl-signin.exe"


def capabilities() -> dict[str, Any]:
    return {"version": __version__, "api": True, "academic": True,
            "automatic_signin": addon_executable().is_file(), "browser_free": True}


def auth_auto() -> dict[str, Any]:
    executable = addon_executable()
    if not executable.is_file():
        raise BackendError("OPTIONAL_COMPONENT_MISSING", "Install Automatic sign-in in Settings, or paste a Canvas API token.")
    # The add-on is an independently frozen program, not a worker of this bundle.
    env = os.environ.copy()
    env.update(PYINSTALLER_RESET_ENVIRONMENT="1", PLAYWRIGHT_BROWSERS_PATH="0",
               SNUETL_WINDOWS_DATA_DIR=str(data_dir()))
    try:
        result = subprocess.run([str(executable)], capture_output=True, text=True, encoding="utf-8",
                                env=env, timeout=660,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise BackendError("SIGNIN_TIMEOUT", "Sign-in timed out. Close the sign-in browser and try again.") from exc
    if result.returncode:
        raise BackendError("SIGNIN_FAILED", "Automatic sign-in did not complete. Try again or use a manual token.")
    status = auth_status(verify=True)
    if not status.get("ready") or status.get("valid") is not True:
        raise BackendError("SIGNIN_FAILED", "Sign-in finished without a valid saved Canvas token. Try again or paste a manual token.")
    return status


def _validate_origin(origin: str) -> str:
    parts = urlsplit(origin)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port is not None:
        raise BackendError("INVALID_ORIGIN", "Use the official SNU eTL HTTPS address")
    return f"https://{parts.hostname}"


def auth_manual(value: str, origin: str = "https://myetl.snu.ac.kr") -> dict[str, Any]:
    token_value = value.strip()
    # Accept a copied HTTP header without storing or sending a duplicate prefix.
    token_value = re.sub(r"^(?:Authorization:\s*)?Bearer\s+", "", token_value, flags=re.IGNORECASE).strip()
    if not TOKEN_PATTERN.fullmatch(token_value):
        raise BackendError("INVALID_TOKEN", "Paste the complete Canvas token copied from SNU eTL. SNUETL handles the authorization header automatically. If the copied key is incomplete, copy it again from eTL.")
    preferred = _validate_origin(origin)
    origins = [
        preferred,
        *(f"https://{host}" for host in ALLOWED_HOSTS if f"https://{host}" != preferred),
    ]
    response: httpx.Response | None = None
    selected = preferred
    try:
        for candidate in origins:
            response = httpx.get(
                f"{candidate}/api/v1/users/self/profile",
                headers={"Authorization": f"Bearer {token_value}"},
                timeout=30,
                follow_redirects=False,
            )
            if response.status_code == 200:
                selected = candidate
                break
    except httpx.HTTPError as exc:
        raise BackendError("NETWORK", "Could not reach SNU eTL") from exc
    if response is None or response.status_code in {401, 403}:
        raise BackendError("TOKEN_REJECTED", "SNU eTL rejected this Canvas token")
    if response.status_code != 200:
        raise BackendError("CANVAS_ERROR", f"Canvas returned HTTP {response.status_code}")
    try:
        profile = _http_json(response)
        user_id = str(profile["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise BackendError("CANVAS_ERROR", "Canvas returned an invalid profile") from exc
    _prepare_desktop_helper()
    manual = CanvasToken(
        token_value,
        selected,
        user_id,
        f"manual:{uuid.uuid4()}",
        (datetime.now(UTC) + timedelta(days=3650)).isoformat(),
    )
    codex_desktop._save_token(manual)
    return {
        "connected": True,
        "origin": selected,
        "user_id": user_id,
        "expires_at": manual.expires_at,
        "manual": True,
    }


def auth_disconnect() -> dict[str, Any]:
    _prepare_desktop_helper()
    token = codex_desktop._load_token()
    if token is not None:
        codex_desktop._delete_token(token)
    else:
        codex_desktop._cleanup_credentials()
        codex_desktop._metadata_path().unlink(missing_ok=True)
    # Local disconnect never opens a browser; revoke tokens explicitly in Canvas.
    for name in ("academic.json", "manifest.json"):
        (data_dir() / name).unlink(missing_ok=True)
    return {"connected": False, "revoked": False}


def sanitize_component(value: str, fallback: str, limit: int = 100) -> str:
    value = unicodedata.normalize("NFC", value)
    value = INVALID_WINDOWS.sub("_", value)
    value = WHITESPACE.sub(" ", value).strip().rstrip(" .")
    if not value or value in {".", ".."}:
        value = fallback
    stem = value.split(".", 1)[0].upper()
    if stem in RESERVED_WINDOWS:
        value = f"_{value}"
    if len(value) > limit:
        suffix = Path(value).suffix
        keep = max(1, limit - len(suffix))
        value = value[:keep].rstrip(" .") + suffix
    return value or fallback


def _course_base(course: Course) -> PurePosixPath:
    semester = sanitize_component(
        course.semester.semester_code if course.semester else "unknown-semester",
        "unknown-semester",
    )
    course_name = sanitize_component(
        f"{course.display_name}--{course.remote_id}", f"course--{course.remote_id}"
    )
    return PurePosixPath(semester, course_name)


def _revision(*values: object) -> str:
    body = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _write_content_cache(body: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(body).hexdigest()
    target = data_dir() / "cache" / "content" / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
    return digest, str(target)


def _front_matter(course: Course, item: ContentItem) -> str:
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


def _markdown_body(course: Course, item: ContentItem) -> bytes:
    # Keep remote HTTPS links intact. This avoids background asset downloads and
    # lets the Markdown file itself remain small and deterministic.
    soup = BeautifulSoup(item.body_html or "", "html.parser")
    for tag, attribute in (("a", "href"), ("img", "src"), ("source", "src")):
        for node in soup.find_all(tag):
            raw = node.get(attribute)
            if isinstance(raw, str) and raw:
                node[attribute] = urljoin(item.url, raw)
    for node in soup(["script", "style"]):
        node.decompose()
    content = markdownify(str(soup), heading_style="ATX").strip()
    return (_front_matter(course, item) + content + "\n").encode("utf-8")


def _entry(
    *,
    relative_path: PurePosixPath,
    kind: str,
    course_id: str,
    source_id: str,
    revision: str,
    size: int,
    updated_at: str | None,
    content_type: str | None,
    download_url: str | None = None,
    cache_path: str | None = None,
) -> dict[str, Any]:
    public = {
        "relative_path": relative_path.as_posix(),
        "kind": kind,
        "course_id": course_id,
        "source_id": source_id,
        "revision": revision,
        "size": size,
        "updated_at": updated_at,
        "content_type": content_type,
    }
    return {**public, "download_url": download_url, "cache_path": cache_path}


def _remote_size(remote: RemoteFile, token: CanvasToken) -> int:
    if remote.size is not None:
        return remote.size

    origin_host = (urlsplit(token.origin).hostname or "").casefold()

    def restrict_authorization(request: httpx.Request) -> None:
        if (request.url.host or "").casefold() != origin_host:
            request.headers.pop("Authorization", None)

    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {token.value}", "Range": "bytes=0-0"},
            event_hooks={"request": [restrict_authorization]},
            timeout=30,
            follow_redirects=True,
        ) as client, client.stream("GET", remote.download_url) as response:
            if response.status_code not in {200, 206}:
                raise BackendError(
                    "FILE_METADATA",
                    f"Could not determine the size of {remote.name!r} (HTTP {response.status_code})",
                )
            content_range = response.headers.get("content-range", "")
            if "/" in content_range:
                total = content_range.rsplit("/", 1)[1]
                if total.isdigit():
                    return int(total)
            content_length = response.headers.get("content-length", "")
            if content_length.isdigit():
                return int(content_length)
    except httpx.HTTPError as exc:
        raise BackendError(
            "FILE_METADATA", f"Could not determine the size of {remote.name!r}"
        ) from exc
    raise BackendError("FILE_METADATA", f"SNU eTL did not report the size of {remote.name!r}")


def refresh_manifest() -> dict[str, Any]:
    token = _token()
    config = _config()
    entries: list[dict[str, Any]] = []
    with CanvasClient(config, token) as client:
        discovery = TokenDiscoveryAdapter(client)
        if not discovery.probe():
            raise BackendError("ACCOUNT_MISMATCH", "The Canvas token belongs to another account")
        courses = discovery.discover_courses()
        for course in courses:
            base = _course_base(course)
            for remote in discovery.discover_files(course):
                folders = [
                    sanitize_component(part, "folder")
                    for part in remote.folder_path
                    if part not in {"", "/"}
                ]
                name = sanitize_component(remote.name, f"file-{remote.remote_id}")
                entries.append(
                    _entry(
                        relative_path=base.joinpath("files", *folders, name),
                        kind="file",
                        course_id=course.remote_id,
                        source_id=remote.remote_id,
                        revision=remote.revision,
                        size=_remote_size(remote, token),
                        updated_at=remote.updated_at,
                        content_type=remote.content_type,
                        download_url=remote.download_url,
                    )
                )
            for item in discovery.discover_articles(course):
                body = _markdown_body(course, item)
                digest, cache_path = _write_content_cache(body)
                name = (
                    f"{sanitize_component(item.title, 'article', 80)}"
                    f"--{sanitize_component(item.remote_id, 'item', 40)}.md"
                )
                entries.append(
                    _entry(
                        relative_path=base / "articles" / item.kind / name,
                        kind=item.kind,
                        course_id=course.remote_id,
                        source_id=item.remote_id,
                        revision=digest,
                        size=len(body),
                        updated_at=item.updated_at,
                        content_type="text/markdown; charset=utf-8",
                        cache_path=cache_path,
                    )
                )
            if course.syllabus_html:
                syllabus = ContentItem(
                    "official",
                    course.remote_id,
                    "syllabus",
                    "Official syllabus",
                    f"{course.url.rstrip('/')}/assignments/syllabus",
                    body_html=course.syllabus_html,
                )
                body = _markdown_body(course, syllabus)
                digest, cache_path = _write_content_cache(body)
                entries.append(
                    _entry(
                        relative_path=base / "syllabus" / "Official syllabus.md",
                        kind="syllabus",
                        course_id=course.remote_id,
                        source_id="official",
                        revision=digest,
                        size=len(body),
                        updated_at=None,
                        content_type="text/markdown; charset=utf-8",
                        cache_path=cache_path,
                    )
                )

    entries.sort(key=lambda item: str(item["relative_path"]).casefold())
    manifest = {
        "schema_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "account": {"origin": token.origin, "user_id": token.user_id},
        "entries": entries,
    }
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, root / "manifest.json")
    live_cache = {
        Path(str(item["cache_path"])).resolve()
        for item in entries
        if item.get("cache_path")
    }
    content_cache = root / "cache" / "content"
    if content_cache.exists():
        for cached in content_cache.iterdir():
            if cached.is_file() and cached.resolve() not in live_cache:
                cached.unlink(missing_ok=True)
    return {
        **{key: value for key, value in manifest.items() if key != "entries"},
        "entries": [
            {key: value for key, value in item.items() if key not in {"download_url", "cache_path"}}
            for item in entries
        ],
    }


def _load_private_manifest() -> dict[str, Any]:
    try:
        value = json.loads((data_dir() / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackendError("NO_MANIFEST", "Refresh the SNU eTL catalog first") from exc
    if value.get("schema_version") != PROTOCOL_VERSION or not isinstance(value.get("entries"), list):
        raise BackendError("NO_MANIFEST", "The saved catalog is incompatible; refresh it")
    return value


def hydrate(identity: dict[str, Any]) -> dict[str, Any]:
    required = {"v", "kind", "course_id", "source_id", "revision"}
    if set(identity) != required or identity.get("v") != PROTOCOL_VERSION:
        raise BackendError("INVALID_IDENTITY", "The placeholder identity is invalid")
    manifest = _load_private_manifest()
    match = next(
        (
            item
            for item in manifest["entries"]
            if item.get("kind") == identity["kind"]
            and item.get("course_id") == identity["course_id"]
            and item.get("source_id") == identity["source_id"]
            and item.get("revision") == identity["revision"]
        ),
        None,
    )
    if match is None:
        raise BackendError("STALE_IDENTITY", "This placeholder is stale; refresh the catalog")
    target_dir = data_dir() / "cache" / "hydrate"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4()}.part"
    cache_path = match.get("cache_path")
    if cache_path:
        shutil.copyfile(str(cache_path), target)
    else:
        url = str(match.get("download_url") or "")
        if not url:
            raise BackendError("MISSING_CONTENT", "The remote file has no download URL")
        token = _token()
        remote = RemoteFile(
            remote_id=str(match["source_id"]),
            course_id=str(match["course_id"]),
            name=Path(str(match["relative_path"])).name,
            folder_path=(),
            download_url=url,
            size=int(match["size"]) if match.get("size") else None,
            updated_at=match.get("updated_at"),
            content_type=match.get("content_type"),
        )
        with TokenDownloader(
            token.origin, token.value, timeout_seconds=60, retry_count=2
        ) as downloader:
            downloader.download(remote, target)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(target), "size": target.stat().st_size, "sha256": digest}


def dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "ping":
        return {"protocol_version": PROTOCOL_VERSION}
    if method == "capabilities":
        return capabilities()
    if method == "academic.snapshot":
        from academic import snapshot
        return snapshot(data_dir(), _config(), _token(), refresh=bool(params.get("refresh")), preferences=params.get("preferences") or {})
    if method == "auth.status":
        return auth_status(bool(params.get("verify", False)))
    if method == "auth.auto":
        return auth_auto()
    if method == "auth.manual":
        return auth_manual(str(params.get("token", "")), str(params.get("origin") or "https://myetl.snu.ac.kr"))
    if method == "auth.disconnect":
        return auth_disconnect()
    if method == "manifest.refresh":
        return refresh_manifest()
    if method == "content.hydrate":
        identity = params.get("identity")
        if not isinstance(identity, dict):
            raise BackendError("INVALID_IDENTITY", "The placeholder identity is missing")
        return hydrate(identity)
    if method == "shutdown":
        return {"shutdown": True}
    raise BackendError("UNKNOWN_METHOD", f"Unknown backend method: {method}")


def serve(input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    # Hydration has its own bounded lane, so opening a file never waits behind
    # a whole course refresh. Account/catalog mutations remain serial.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    output_lock = Lock()
    def respond(request):
        request_id = request.get("id")
        try:
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise BackendError("INVALID_REQUEST", "params must be an object")
            result = dispatch(str(request["method"]), params)
            response = {"id": request_id, "ok": True, "result": result}
        except (BackendError, codex_desktop.DesktopError) as exc:
            response = {"id": request_id, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except AuthenticationRequired as exc:
            response = {"id": request_id, "ok": False, "error": {"code": "AUTHENTICATION_REQUIRED", "message": str(exc)}}
        except DiscoveryError as exc:
            response = {"id": request_id, "ok": False, "error": {"code": "CANVAS_ERROR", "message": str(exc)}}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = {"id": request_id, "ok": False, "error": {"code": "INTERNAL", "message": "The requested operation failed. Check the private backend log."}}
        with output_lock:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    with ThreadPoolExecutor(max_workers=1) as background, ThreadPoolExecutor(max_workers=2) as downloads:
        for raw in input_stream:
            try:
                request = json.loads(raw)
                if not isinstance(request, dict): raise ValueError()
            except ValueError:
                request = {"method": "invalid.request"}
            pool = downloads if request.get("method") == "content.hydrate" else background
            pool.submit(respond, request)
            if request.get("method") == "shutdown": break
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
