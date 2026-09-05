from __future__ import annotations

from dataclasses import dataclass

from .config import DiscordSettings


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str


def authorize_discord_interaction(
    settings: DiscordSettings,
    *,
    guild_id: int | None,
    channel_id: int | None,
    user_id: int,
) -> AccessDecision:
    if not settings.enabled or not settings.configured:
        return AccessDecision(False, "Discord control is not enabled")
    if guild_id is None:
        return AccessDecision(False, "Direct messages are not accepted")
    if guild_id != settings.guild_id:
        return AccessDecision(False, "This server is not bound to snuetl")
    if channel_id != settings.channel_id:
        return AccessDecision(False, "Use the channel bound during snuetl discord setup")
    if user_id not in settings.owner_ids:
        return AccessDecision(False, "Your Discord account is not an authorized owner")
    return AccessDecision(True, "authorized")
