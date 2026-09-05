from __future__ import annotations

import asyncio
import getpass
import secrets
import stat
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlencode

import discord
from discord import app_commands
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import Config, DiscordSettings, default_config_path, save_config
from .credentials import load_credentials
from .discord_config import load_discord_token, save_discord_token
from .runtime import is_container_runtime
from .scheduler import (
    discord_service_is_active,
    discord_service_is_enabled,
    install_discord_service,
    linger_status,
    restart_discord_service,
    set_discord_service_enabled,
)
from .ui import console

DISCORD_DEVELOPER_PORTAL_URL = "https://discord.com/developers/applications"
DISCORD_SETUP_DOCS_URL = "https://docs.discord.com/developers/quick-start/getting-started"
REQUIRED_DISCORD_PERMISSIONS = (
    ("View Channel", "view_channel", 1024, "See the one channel bound to snuetl"),
    ("Send Messages", "send_messages", 2048, "Post progress and final results"),
    ("Embed Links", "embed_links", 16384, "Display readable status and result cards"),
    (
        "Read Message History",
        "read_message_history",
        65536,
        "Keep command responses usable across reconnects",
    ),
)
DISCORD_PERMISSIONS = sum(permission[2] for permission in REQUIRED_DISCORD_PERMISSIONS)


def invite_url(application_id: int) -> str:
    return "https://discord.com/oauth2/authorize?" + urlencode(
        {
            "client_id": str(application_id),
            "scope": "bot applications.commands",
            "permissions": str(DISCORD_PERMISSIONS),
            "integration_type": "0",
        }
    )


def discord_setup_guide() -> dict[str, object]:
    """Return the stable, secret-free setup checklist used by humans and agents."""
    return {
        "developer_portal": DISCORD_DEVELOPER_PORTAL_URL,
        "official_guide": DISCORD_SETUP_DOCS_URL,
        "application": [
            "Open the Developer Portal and choose New Application.",
            "On General Information, copy Application ID (not Public Key or Client Secret).",
            "On Bot, choose Reset Token, copy the new token, and paste it only into snuetl.",
        ],
        "installation": {
            "context": "Guild Install",
            "scopes": ["bot", "applications.commands"],
            "permissions_integer": DISCORD_PERMISSIONS,
            "permissions": [
                {"name": name, "bit": bit, "reason": reason}
                for name, _attribute, bit, reason in REQUIRED_DISCORD_PERMISSIONS
            ],
        },
        "bot_settings": {
            "require_oauth2_code_grant": False,
            "privileged_gateway_intents": {
                "presence": False,
                "server_members": False,
                "message_content": False,
            },
        },
        "security": [
            "Use a dedicated private server channel.",
            "Never paste the bot token into Discord, chat, a shell command, or source control.",
            "If the token is exposed, Reset Token in the Developer Portal and rerun setup.",
            "Administrator and Manage Server are not required bot permissions.",
        ],
    }


def print_discord_setup_guide() -> None:
    console.print(
        Panel.fit(
            "[bold blue]Connect snuetl to Discord[/bold blue]\n"
            "You need a Discord account and permission to manage the target server.\n"
            "Keep this terminal open; the final pairing code expires after 10 minutes.",
            border_style="blue",
        )
    )
    console.print("\n[bold]1. Create the application[/bold]")
    console.print(
        f"Open: [link={DISCORD_DEVELOPER_PORTAL_URL}]{DISCORD_DEVELOPER_PORTAL_URL}[/link]"
    )
    console.print(
        "Choose [bold]New Application[/bold], enter a name such as [cyan]snuetl[/cyan], then create it."
    )
    console.print(
        "On [bold]General Information[/bold], copy [bold]Application ID[/bold]. "
        "Do not use Public Key or Client Secret."
    )
    console.print("\n[bold]2. Issue the bot token[/bold]")
    console.print(
        "Open [bold]Bot[/bold] in the application sidebar. Under [bold]Token[/bold], choose "
        "[bold]Reset Token[/bold] (or copy the token if Discord still offers it), complete "
        "Discord's confirmation, and copy the token."
    )
    console.print(
        "[yellow]The token is a password.[/yellow] Paste it only into the hidden terminal "
        "prompt below. If it was exposed anywhere, reset it before continuing."
    )
    console.print("\n[bold]3. Check installation and bot settings[/bold]")
    console.print(
        "On [bold]Installation[/bold], enable [bold]Guild Install[/bold]. snuetl's generated "
        "invite requests the [cyan]bot[/cyan] and [cyan]applications.commands[/cyan] scopes."
    )
    console.print(
        "On [bold]Bot[/bold], leave [bold]Require OAuth2 Code Grant[/bold] off. All three "
        "Privileged Gateway Intents can remain off: Presence, Server Members, and Message Content."
    )
    permissions = Table(
        title=f"Only these bot permissions are used (integer {DISCORD_PERMISSIONS})"
    )
    permissions.add_column("Permission")
    permissions.add_column("Why")
    for name, _attribute, _bit, reason in REQUIRED_DISCORD_PERMISSIONS:
        permissions.add_row(name, reason)
    console.print(permissions)
    console.print(
        "[green]Do not grant Administrator, Manage Server, Manage Channels, Manage Roles, "
        "or Manage Messages.[/green]"
    )
    console.print(f"[dim]Official Discord walkthrough: {DISCORD_SETUP_DOCS_URL}[/dim]\n")


def print_discord_setup_success(data: dict[str, object]) -> None:
    owner_values = data.get("owner_ids")
    owners = (
        ", ".join(str(value) for value in owner_values)
        if isinstance(owner_values, list)
        else "unknown"
    )
    console.print(
        Panel.fit(
            "[bold green]Discord setup complete[/bold green]\n"
            f"Server: {data['guild_id']}\n"
            f"Channel: {data['channel_id']}\n"
            f"Authorized owner: {owners}\n"
            f"Service: {data['service_path']}",
            border_style="green",
        )
    )
    console.print("In the bound channel, try [cyan]/snuetl status[/cyan].")
    console.print("On this machine, inspect the daemon with [cyan]snuetl discord status[/cyan].")
    linger = data.get("linger")
    if isinstance(linger, dict) and not linger.get("enabled"):
        console.print(
            "[yellow]For the bot to remain online after SSH/logout, enable the user service "
            'with:[/yellow]\n  sudo loginctl enable-linger "$USER"'
        )


def _prompt_application_id(default: int | None) -> int:
    while True:
        if default is None:
            value = Prompt.ask("Paste Application ID from General Information")
        else:
            value = Prompt.ask(
                "Paste Application ID from General Information",
                default=str(default),
            )
        value = value.strip()
        if value.isascii() and value.isdecimal() and int(value) > 0:
            return int(value)
        console.print("[red]Application ID must be the positive numeric ID shown by Discord.[/red]")


def _missing_permission_names(permissions: object) -> list[str]:
    return [
        name
        for name, attribute, _bit, _reason in REQUIRED_DISCORD_PERMISSIONS
        if not bool(getattr(permissions, attribute, False))
    ]


async def _validate_binding(
    token: str, application_id: int, guild_id: int, channel_id: int
) -> None:
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents, application_id=application_id)
    gateway: asyncio.Task[None] | None = None
    try:
        await client.login(token)
        actual = client.application.id if client.application is not None else None
        if actual is not None and actual != application_id:
            raise RuntimeError(
                f"Discord token belongs to application {actual}, not {application_id}"
            )
        gateway = asyncio.create_task(client.connect(reconnect=False))
        await asyncio.wait_for(client.wait_until_ready(), timeout=30)
        guild = client.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("The Discord bot is not a member of the configured server")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or guild.me is None:
            raise RuntimeError(
                "The configured channel must be a standard server text channel visible to the bot"
            )
        permissions = channel.permissions_for(guild.me)
        missing = _missing_permission_names(permissions)
        if missing:
            raise RuntimeError(
                "The Discord bot is missing channel permissions: "
                + ", ".join(missing)
                + ". Re-open the generated invite or update this channel's permission overrides."
            )
    finally:
        await client.close()
        if gateway is not None:
            await asyncio.gather(gateway, return_exceptions=True)


def discord_status(config: Config) -> dict[str, object]:
    linger = linger_status()
    return {
        "enabled": config.discord.enabled,
        "configured": config.discord.configured,
        "application_id": config.discord.application_id,
        "guild_id": config.discord.guild_id,
        "channel_id": config.discord.channel_id,
        "owner_ids": sorted(config.discord.owner_ids),
        "token_present": load_discord_token(config) is not None,
        "service_enabled": discord_service_is_enabled(),
        "service_active": discord_service_is_active(),
        "service_manager": "docker_compose" if is_container_runtime() else "systemd",
        "permissions_integer": DISCORD_PERMISSIONS,
        "required_permissions": [item[0] for item in REQUIRED_DISCORD_PERMISSIONS],
        "invite_url": invite_url(config.discord.application_id)
        if config.discord.application_id
        else None,
        "linger": asdict(linger),
    }


async def _claim_binding(token: str, application_id: int, code: str) -> tuple[int, int, int]:
    claimed: asyncio.Future[tuple[int, int, int]] = asyncio.get_running_loop().create_future()
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents, application_id=application_id)
    tree = app_commands.CommandTree(client)
    group = app_commands.Group(name="snuetl", description="Control this snuetl installation")

    @group.command(name="claim", description="Bind this private channel to snuetl")
    @app_commands.describe(code="One-time code printed by snuetl discord")
    async def claim(interaction: discord.Interaction, code: str) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(
                "Claims must be made in a server channel.", ephemeral=True
            )
            return
        if not secrets.compare_digest(code.strip(), claim_code):
            await interaction.response.send_message(
                "That claim code is invalid or expired.", ephemeral=True
            )
            return
        channel = interaction.channel
        member = interaction.guild.me if interaction.guild is not None else None
        if not isinstance(channel, discord.TextChannel) or member is None:
            await interaction.response.send_message(
                "Use /snuetl claim in a standard server text channel, not a DM, thread, forum, or voice channel.",
                ephemeral=True,
            )
            return
        permissions = channel.permissions_for(member)
        missing = _missing_permission_names(permissions)
        if missing:
            await interaction.response.send_message(
                "Missing permissions in this channel: "
                + ", ".join(missing)
                + ". Update the channel override for the bot role, then retry.",
                ephemeral=True,
            )
            return
        if claimed.done():
            await interaction.response.send_message(
                "This one-time claim has already been used.", ephemeral=True
            )
            return
        claimed.set_result((interaction.guild_id, interaction.channel_id, interaction.user.id))
        await interaction.response.send_message(
            "This channel is now bound to snuetl.", ephemeral=True
        )

    claim_code = code

    registered_guilds: set[int] = set()

    async def register_claim(guild: discord.Guild) -> None:
        if guild.id in registered_guilds:
            return
        try:
            tree.add_command(group, guild=guild, override=True)
            await tree.sync(guild=guild)
        except (discord.HTTPException, app_commands.CommandAlreadyRegistered) as exc:
            console.print(
                f"Could not register /snuetl claim in server {guild.id}: {exc}",
                style="red",
                markup=False,
            )
            return
        registered_guilds.add(guild.id)
        console.print(
            f"Connected to {guild.name}. /snuetl claim is ready.",
            style="green",
            markup=False,
            highlight=False,
        )

    @client.event
    async def on_ready() -> None:
        for guild in client.guilds:
            await register_claim(guild)

    @client.event
    async def on_guild_join(guild: discord.Guild) -> None:
        await register_claim(guild)

    try:
        await client.login(token)
    except discord.LoginFailure as exc:
        raise RuntimeError(
            "Discord rejected the bot token. Open Developer Portal > Bot > Reset Token, "
            "copy the new token, and rerun snuetl discord."
        ) from exc
    actual = client.application.id if client.application is not None else None
    if actual is not None and actual != application_id:
        await client.close()
        raise RuntimeError(
            f"The token belongs to application {actual}, but Application ID {application_id} was entered."
        )
    gateway = asyncio.create_task(client.connect(reconnect=False))
    try:
        return await asyncio.wait_for(claimed, timeout=600)
    except TimeoutError as exc:
        raise RuntimeError("Discord claim expired after 10 minutes; run setup again") from exc
    finally:
        await client.close()
        await asyncio.gather(gateway, return_exceptions=True)


def configure_discord(
    config: Config,
    *,
    config_path: Path | None,
    token_file: Path | None = None,
    application_id: int | None = None,
    guild_id: int | None = None,
    channel_id: int | None = None,
    owner_ids: tuple[int, ...] = (),
    confirmed: bool = False,
) -> tuple[Config, dict[str, object]]:
    if not config.setup_complete:
        raise RuntimeError("Complete 'snuetl setup' before configuring Discord")
    if load_credentials(config) is None:
        raise RuntimeError("Save SNU credentials with 'snuetl login' before configuring Discord")
    noninteractive = (
        token_file is not None
        or any(value is not None for value in (application_id, guild_id, channel_id))
        or bool(owner_ids)
    )
    if noninteractive:
        if not confirmed:
            raise RuntimeError("Non-interactive Discord setup requires --yes")
        if token_file is None:
            raise RuntimeError("Non-interactive Discord setup requires --token-file")
        try:
            mode = stat.S_IMODE(token_file.stat().st_mode)
            if mode & 0o077:
                raise RuntimeError("Discord token file must be owner-only (chmod 600)")
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Cannot read Discord token file: {exc}") from exc
        if application_id is None or guild_id is None or channel_id is None or not owner_ids:
            raise RuntimeError(
                "Non-interactive setup requires --application-id, --guild-id, --channel-id, and --owner-id"
            )
        if min(application_id, guild_id, channel_id, *owner_ids) <= 0:
            raise RuntimeError(
                "Discord application, server, channel, and owner IDs must be positive"
            )
        app_id = application_id
        binding = (guild_id, channel_id, tuple(int(value) for value in owner_ids))
        asyncio.run(_validate_binding(token, app_id, guild_id, channel_id))
    else:
        print_discord_setup_guide()
        app_id = _prompt_application_id(config.discord.application_id)
        existing = load_discord_token(config)
        if existing and Confirm.ask(
            "Use the existing securely stored bot token?",
            default=True,  # type: ignore[arg-type]
        ):
            token = existing
            console.print("[green]Using the existing token; it will not be displayed.[/green]")
        else:
            token = getpass.getpass(
                "Paste bot token from Developer Portal > Bot > Token (input hidden): "
            ).strip()
        if not token or any(character.isspace() for character in token):
            raise ValueError(
                "The bot token is empty or contains whitespace. Reset/copy it again from "
                "Developer Portal > Bot."
            )
        claim_code = secrets.token_urlsafe(8)
        url = invite_url(app_id)
        console.print("\n[bold]4. Install the bot in the target server[/bold]")
        console.print(
            "Open this generated least-privilege invite, choose [bold]Add to server[/bold], "
            "select the target server, then choose [bold]Authorize[/bold]. You need Discord's "
            "[bold]Manage Server[/bold] permission to install it."
        )
        console.print(url, markup=False, highlight=False)
        console.print("\n[bold]5. Choose and secure the single channel[/bold]")
        console.print(
            "Create or choose a normal text channel such as [cyan]#snuetl[/cyan]. For stricter "
            "Discord-side isolation, open [bold]Server Settings > Roles[/bold], select the bot "
            "role, and turn its four permissions off server-wide. Then open "
            "[bold]Edit Channel > Permissions[/bold], add that role, and explicitly allow only:"
        )
        console.print(
            "  View Channel · Send Messages · Embed Links · Read Message History",
            markup=False,
        )
        console.print(
            "snuetl also rejects every command from other channels in code, even if Discord "
            "role settings still let the bot see them."
        )
        console.print("\n[bold]6. Claim the channel[/bold]")
        console.print(
            "In that text channel, enter [cyan]/snuetl claim[/cyan]. Discord will show a "
            "[cyan]code[/cyan] field; paste this one-time value:",
        )
        console.print(f"  {claim_code}", markup=False, highlight=False)
        console.print(
            "[dim]Waiting up to 10 minutes. If the slash command is not visible yet, wait for "
            "the green “claim is ready” message in this terminal, then type / again.[/dim]"
        )
        guild, channel, owner = asyncio.run(_claim_binding(token, app_id, claim_code))
        binding = (guild, channel, (owner,))

    guild, channel, owners = binding
    configured = replace(
        config,
        discord=DiscordSettings(
            enabled=True,
            application_id=app_id,
            guild_id=guild,
            channel_id=channel,
            owner_ids=frozenset(owners),
        ),
    )
    path = config_path or default_config_path()
    save_config(configured, path)
    save_discord_token(configured, token)
    service_path = install_discord_service(
        path,
        state_dir=configured.state_dir,
        download_dir=configured.download_dir,
        write_dirs=tuple(route.destination for route in configured.directory_routes),
    )
    return configured, {
        **discord_status(configured),
        "service_path": str(service_path) if service_path else "Docker Compose supervisor",
        "invite_url": invite_url(app_id),
    }


def change_discord_owner(config: Config, user_id: int, *, add: bool) -> Config:
    if user_id <= 0:
        raise ValueError("Discord user ID must be positive")
    owners = set(config.discord.owner_ids)
    if add:
        owners.add(user_id)
    else:
        if user_id not in owners:
            raise ValueError("Discord owner ID is not configured")
        if len(owners) == 1:
            raise ValueError("Cannot remove the final Discord owner")
        owners.remove(user_id)
    return replace(config, discord=replace(config.discord, owner_ids=frozenset(owners)))


def restart_discord_if_enabled(config: Config) -> None:
    if config.discord.enabled and discord_service_is_enabled():
        restart_discord_service()


def toggle_discord(config: Config, enabled: bool, config_path: Path | None) -> Config:
    if not config.discord.configured:
        raise RuntimeError("Discord has not been configured; run snuetl discord")
    updated = replace(config, discord=replace(config.discord, enabled=enabled))
    path = config_path or default_config_path()
    save_config(updated, path)
    if enabled:
        install_discord_service(
            path,
            state_dir=updated.state_dir,
            download_dir=updated.download_dir,
            write_dirs=tuple(route.destination for route in updated.directory_routes),
        )
    else:
        set_discord_service_enabled(False)
    return updated
