from __future__ import annotations

from typing import Any, Protocol


class RequestLike(Protocol):
    headers: dict[str, str]


class ResponseLike(Protocol):
    status: int
    url: str
    headers: dict[str, str]
    request: RequestLike

    def json(self) -> Any: ...

    def text(self) -> str: ...


class RequestContextLike(Protocol):
    def get(self, url: str, **kwargs: object) -> ResponseLike: ...


class BrowserContextLike(Protocol):
    request: RequestContextLike

    def cookies(self) -> list[dict[str, Any]]: ...

    def clear_cookies(self, **kwargs: object) -> None: ...


class FrameLike(Protocol):
    url: str

    def locator(self, selector: str) -> Any: ...

    def content(self) -> str: ...


class BrowserPageLike(Protocol):
    url: str
    frames: list[FrameLike]

    def goto(self, url: str, **kwargs: object) -> Any: ...

    def wait_for_timeout(self, milliseconds: int) -> None: ...
