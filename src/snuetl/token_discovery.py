"""Use a personal Canvas token with the existing catalog data mapping."""

from __future__ import annotations

from typing import Any

from .canvas_adapter import CanvasApiAdapter
from .canvas_api import CanvasClient
from .errors import AdapterUnavailable, DiscoveryError


class TokenDiscoveryAdapter(CanvasApiAdapter):
    def __init__(self, client: CanvasClient):
        self.client = client
        self.origin = client.token.origin
        self.strict_details = True

    def _get_pages(self, path: str) -> list[Any]:
        try:
            return self.client.pages(path)
        except DiscoveryError as exc:
            if "endpoint is unavailable" in str(exc):
                raise AdapterUnavailable(str(exc)) from exc
            raise

    def _get_object(self, path: str) -> dict[str, Any]:
        try:
            value = self.client.request("GET", path)
        except DiscoveryError as exc:
            if "endpoint is unavailable" in str(exc):
                raise AdapterUnavailable(str(exc)) from exc
            raise
        if not isinstance(value, dict):
            raise AdapterUnavailable("Canvas API returned an unexpected object")
        return value

    def probe(self) -> bool:
        profile = self._get_object("/api/v1/users/self/profile")
        return str(profile.get("id")) == self.client.token.user_id
