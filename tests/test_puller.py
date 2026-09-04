import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from snuetl import puller
from snuetl.config import load_config
from snuetl.models import Course, ModuleItem
from snuetl.puller import (
    MINIMUM_VIDEO_BYTES,
    PullPlan,
    PullSummary,
    _activate_lcms_player,
    _append_media_candidate,
    _cms_media_candidate,
    _is_lcms_lecture_url,
    _learningx_content_id,
    _managed_write,
    _referer_for,
    plan_data,
)
from snuetl.state import StateStore


def test_managed_write_preserves_local_edits(tmp_path: Path) -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    destination = tmp_path / "article.md"
    summary = PullSummary()
    with StateStore(tmp_path / "state.db") as store, store.transaction():
        store.upsert_course(course)
        _managed_write(
            store,
            summary,
            artifact_type="article",
            course=course,
            source_id="page:1",
            destination=destination,
            body=b"remote one",
            source_revision="one",
            force=False,
        )
    destination.write_bytes(b"my local edit")
    with StateStore(tmp_path / "state.db") as store, store.transaction():
        conflict = _managed_write(
            store,
            summary,
            artifact_type="article",
            course=course,
            source_id="page:1",
            destination=destination,
            body=b"remote two",
            source_revision="two",
            force=False,
        )
    assert destination.read_bytes() == b"my local edit"
    assert conflict.read_bytes() == b"remote two"
    assert conflict != destination
    assert summary.conflicts == 1


class _ApiResponse:
    status = 200
    url = "https://lcms.snu.ac.kr/viewer/ssplayer/uniplayer_support/content.php"

    @staticmethod
    def text() -> str:
        return '<media method="progressive" target="all">https://edge.naverncp.com/path/[MEDIA_FILE]</media>'

    @staticmethod
    def json() -> dict[str, object]:
        return {"item_content_data": {"content_id": "cms-123"}}


class _Request:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _ApiResponse:
        self.calls.append((url, kwargs))
        return _ApiResponse()


class _BrowserContext:
    def __init__(self) -> None:
        self.request = _Request()

    @staticmethod
    def cookies() -> list[dict[str, str]]:
        return [{"name": "xn_api_token", "value": "fresh-token"}]


def test_lcms_metadata_resolves_progressive_video_with_required_referer() -> None:
    context = _BrowserContext()
    candidate = _cms_media_candidate(context, "cms-123")
    assert candidate == (
        "https://edge.naverncp.com/path/screen.mp4",
        "https://lcms.snu.ac.kr/",
    )
    assert _referer_for(candidate[0], "https://wrong.test/") == "https://lcms.snu.ac.kr/"


def test_learningx_attendance_api_uses_fresh_browser_token() -> None:
    context = _BrowserContext()
    course = Course("101", "Course", "https://myetl.snu.ac.kr/courses/101")
    item = ModuleItem(
        "module",
        "Week 1",
        "item",
        "101",
        "ExternalTool",
        "Lecture",
        external_url="https://myetl.snu.ac.kr/learningx/lecture_attendance/items/view/99",
    )
    assert _learningx_content_id(context, course, item) == "cms-123"
    url, kwargs = context.request.calls[0]
    assert url.endswith("/learningx/api/v1/courses/101/attendance_items/99")
    assert kwargs["headers"] == {"Authorization": "Bearer fresh-token"}


def test_lcms_preloader_is_rejected_but_naver_lecture_is_accepted() -> None:
    candidates: list[tuple[str, str]] = []

    _append_media_candidate(
        candidates,
        "https://lcms.snu.ac.kr/viewer/uniplayer/preloader.mp4",
        "https://lcms.snu.ac.kr/",
    )
    lecture = "https://snu-cms-object.edge.naverncp.com/contents/course/lecture/ssmovie.mp4"
    _append_media_candidate(candidates, lecture, "https://lcms.snu.ac.kr/")

    assert candidates == [(lecture, "https://lcms.snu.ac.kr/")]
    assert _is_lcms_lecture_url(lecture)


def test_lcms_player_is_started_until_real_lecture_source_appears() -> None:
    lecture = "https://snu-cms-object.edge.naverncp.com/course/lecture/ssmovie.mp4"

    class Frame:
        url = "https://lcms.snu.ac.kr/em/lecture"
        source = "https://lcms.snu.ac.kr/viewer/uniplayer/preloader.mp4"

        def locator(self, selector: str):
            frame = self

            class Locator:
                @staticmethod
                def count() -> int:
                    return 1

                def nth(self, _index: int):
                    return self

                @staticmethod
                def is_visible() -> bool:
                    return True

                @staticmethod
                def click(**_kwargs: object) -> None:
                    frame.source = lecture

                @staticmethod
                def evaluate_all(_script: str) -> list[str]:
                    return [frame.source] if selector == "video.vc-vplay-video1" else []

            return Locator()

    page = SimpleNamespace(frames=[Frame()], wait_for_timeout=lambda _milliseconds: None)

    assert _activate_lcms_player(page)


def test_selected_plan_data_omits_unselected_videos() -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    first = ModuleItem("module", "Week", "video-1", "101", "ExternalTool", "First")
    second = ModuleItem("module", "Week", "video-2", "101", "ExternalTool", "Second")
    plan = PullPlan(
        ("videos",),
        (course,),
        modules=((course, first), (course, second)),
        selected_video_ids=("video-1",),
    )

    assert [video["video_id"] for video in plan_data(plan)["videos"]] == ["video-1"]


def test_selected_video_reaches_downloader_and_reports_progress(tmp_path, monkeypatch) -> None:
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
    )
    course = Course("101", "Course", "https://lms.test/courses/101")
    item = ModuleItem(
        "module",
        "Week 1",
        "video-1",
        course.remote_id,
        "ExternalTool",
        "Lecture",
        html_url="https://lms.test/courses/101/modules/items/video-1",
    )
    plan = PullPlan(
        ("videos",),
        (course,),
        modules=((course, item),),
        selected_video_ids=(item.remote_id,),
    )
    placeholder = tmp_path / "placeholder.mp4"
    placeholder.write_bytes(b"preloader")
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.record_artifact(
            artifact_type="video",
            course_id=course.remote_id,
            source_id=item.remote_id,
            local_path=placeholder,
            source_revision="/courses/101/modules/items/video-1",
            sha256="placeholder",
            size_bytes=placeholder.stat().st_size,
            status="ok",
        )

    class Page:
        url = "https://lms.test/"
        frames: list[object] = []

        def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

        @staticmethod
        def evaluate(_script: str) -> str:
            return "test-agent"

        @staticmethod
        def wait_for_timeout(_milliseconds: int) -> None:
            pass

        @staticmethod
        def on(*_args: object) -> None:
            pass

        @staticmethod
        def remove_listener(*_args: object) -> None:
            pass

    class Context:
        @staticmethod
        def cookies() -> list[dict[str, str]]:
            return []

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://media.test/video.mp4"]
            assert self.options["noprogress"] is True
            hooks = self.options["progress_hooks"]
            for hook in hooks:
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "finished"})
            output = Path(str(self.options["outtmpl"]).replace("%(ext)s", "mp4"))
            output.write_bytes(b"v" * MINIMUM_VIDEO_BYTES)

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(puller, "profile_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        puller,
        "open_authenticated_browser",
        lambda *_args, **_kwargs: nullcontext((Context(), Page())),
    )
    monkeypatch.setattr(puller, "authenticated_entry_url", lambda _config: "https://lms.test/")
    monkeypatch.setattr(puller, "ensure_authenticated_page", lambda *_args: None)
    monkeypatch.setattr(
        puller,
        "AuthenticatedDownloader",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        puller,
        "_dom_media_candidates",
        lambda _page: ([("https://media.test/video.mp4", "https://lcms.snu.ac.kr/")], []),
    )
    monkeypatch.setattr(puller, "_learningx_content_id", lambda *_args: None)
    messages: list[str] = []

    summary = puller.execute_pull(config, plan, progress=messages.append)

    assert summary.updated == 1
    assert summary.created == 0
    assert summary.failed == 0
    assert "Replacing invalid placeholder: Lecture" in messages
    assert any(message.startswith("Starting download:") for message in messages)
    assert any(message.startswith("Downloading Lecture: 50%") for message in messages)
    assert any(path.endswith(".mp4") for path in summary.artifacts)
