"""Focused authentication boundary backed by the legacy browser façade."""

from .browser import (
    authenticated_entry_url,
    browser_looks_authenticated,
    canvas_profile_is_authenticated,
    ensure_authenticated_page,
    interactive_login,
    persist_auth_state,
)

__all__ = [
    "authenticated_entry_url",
    "browser_looks_authenticated",
    "canvas_profile_is_authenticated",
    "ensure_authenticated_page",
    "interactive_login",
    "persist_auth_state",
]
