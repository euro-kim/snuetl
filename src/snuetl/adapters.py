"""Compatibility façade for LMS discovery adapters.

Concrete API and DOM implementations live in separate modules so changes to one
backend can be tested and reviewed without coupling it to the other.
"""

from __future__ import annotations

from typing import Any

from . import canvas_adapter as _canvas
from .discovery import DiscoveryCoordinator
from .dom_adapter import DomAdapter
from .errors import AdapterUnavailable

CanvasApiAdapter = _canvas.CanvasApiAdapter
origin_of = _canvas.origin_of
_parse_next_link = _canvas._parse_next_link
_response_json = _canvas._response_json

__all__ = [
    "AdapterUnavailable",
    "CanvasApiAdapter",
    "DiscoveryService",
    "DomAdapter",
    "origin_of",
]


class DiscoveryService(DiscoveryCoordinator):
    def __init__(self, context: Any, page: Any, *, timeout_seconds: float):
        self.context = context
        self.page = page
        self.origin = origin_of(page.url)
        super().__init__(
            CanvasApiAdapter(context, self.origin, timeout_seconds=timeout_seconds),
            DomAdapter(context, page, self.origin, timeout_seconds),
        )
