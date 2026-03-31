import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from depcmds import DepartmentRegistry, register_department_commands

load_dotenv()

DURATION_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)
DURATION_MULTIPLIERS = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}
PERMISSION_LABELS = {
    "ban_members": "Ban Members",
    "kick_members": "Kick Members",
    "moderate_members": "Moderate Members",
    "manage_messages": "Manage Messages",
    "manage_roles": "Manage Roles",
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def split_csv(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        result.add(int(item))
    return result


def parse_optional_id(value: str) -> Optional[int]:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return None


def parse_guild_channel_map(value: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue

        guild_id, separator, channel_id = item.partition(":")
        if separator != ":" or not guild_id.strip().isdigit() or not channel_id.strip().isdigit():
            raise RuntimeError(
                "GLOBAL_MESSAGE_CHANNEL_MAP must use the format guild_id:channel_id,guild_id:channel_id"
            )

        result[int(guild_id.strip())] = int(channel_id.strip())

    return result


def normalize_reason(value: Optional[str]) -> str:
    text = (value or "No reason provided").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:400]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_duration(value: str) -> Optional[timedelta]:
    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        return None

    amount, unit = match.groups()
    return int(amount) * DURATION_MULTIPLIERS[unit.lower()]


def format_duration(duration: timedelta) -> str:
    total_seconds = int(duration.total_seconds())
    for label, seconds in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if total_seconds % seconds == 0:
            return f"{total_seconds // seconds}{label}"
    return f"{total_seconds}s"


def summarize_exception(error: Exception) -> str:
    return str(error) or error.__class__.__name__


def build_global_ban_reason(entry: dict) -> str:
    reason = entry.get("reason", "No reason provided")
    message = (
        f"Global ban | by {entry['moderator_id']} | {entry['created_at']} | {reason}"
    )
    return message[:512]


def summarize_results(results: list[dict]) -> str:
    success = [result for result in results if result["status"] in {"banned", "unbanned"}]
    skipped = [result for result in results if result["status"] == "skipped"]
    failed = [result for result in results if result["status"] == "failed"]

    lines = [f"Succeeded in {len(success)} server(s)."]

    if skipped:
        lines.append(f"Skipped in {len(skipped)} server(s).")

    if failed:
        preview = " | ".join(
            f"{item['guild_name']}: {item['reason']}" for item in failed[:3]
        )
        if preview:
            lines.append(f"Failed in {len(failed)} server(s): {preview}")
        else:
            lines.append(f"Failed in {len(failed)} server(s).")

    return "\n".join(lines)


def format_target_scope(targeted_guilds: list[discord.Guild], missing_guild_ids: list[int]) -> str:
    lines = [f"Targeted {len(targeted_guilds)} server(s)."]

    if missing_guild_ids:
        missing = ", ".join(str(guild_id) for guild_id in missing_guild_ids[:10])
        suffix = "..." if len(missing_guild_ids) > 10 else ""
        lines.append(
            f"Could not reach {len(missing_guild_ids)} configured server(s): {missing}{suffix}"
        )

    return "\n".join(lines)


def summarize_message_results(results: list[dict]) -> str:
    sent = [result for result in results if result["status"] == "sent"]
    failed = [result for result in results if result["status"] == "failed"]
    missing_channels = [result for result in results if result["status"] == "missing_channel"]

    lines = [f"Sent in {len(sent)} server(s)."]

    if missing_channels:
        preview = ", ".join(item["guild_name"] for item in missing_channels[:5])
        suffix = "..." if len(missing_channels) > 5 else ""
        lines.append(
            f"Missing channel mapping in {len(missing_channels)} server(s): {preview}{suffix}"
        )

    if failed:
        preview = " | ".join(
            f"{item['guild_name']}: {item['reason']}" for item in failed[:3]
        )
        lines.append(f"Failed in {len(failed)} server(s): {preview}")

    return "\n".join(lines)


def format_ban_list(entries: list[dict]) -> str:
    if not entries:
        return "No global bans are stored yet."

    preview = []
    for index, entry in enumerate(entries[:20], start=1):
        short_reason = entry["reason"][:70]
        preview.append(
            f"{index}. {entry['user_id']} | {entry['created_at']} | {short_reason}"
        )

    suffix = ""
    if len(entries) > 20:
        suffix = f"\n...and {len(entries) - 20} more."

    return f"Stored global bans: {len(entries)}\n```\n" + "\n".join(preview) + suffix + "\n```"


def format_status_label(value: str) -> str:
    return value.replace("_", " ").title()


def build_global_ban_request_embed(request: dict) -> discord.Embed:
    status = request.get("status", "pending")
    color_map = {
        "pending": discord.Color.gold(),
        "approved": discord.Color.green(),
        "denied": discord.Color.red(),
        "cancelled": discord.Color.dark_grey(),
    }
    title_map = {
        "pending": "Global Ban Request",
        "approved": "Global Ban Request Approved",
        "denied": "Global Ban Request Denied",
        "cancelled": "Global Ban Request Cancelled",
    }

    embed = discord.Embed(
        title=title_map.get(status, "Global Ban Request"),
        color=color_map.get(status, discord.Color.blurple()),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Target",
        value=f"<@{request['user_id']}> (`{request['user_id']}`)",
        inline=False,
    )
    embed.add_field(
        name="Requested By",
        value=f"<@{request['requester_id']}> (`{request['requester_id']}`)",
        inline=False,
    )
    embed.add_field(
        name="Reason",
        value=str(request.get("reason", "No reason provided"))[:1024],
        inline=False,
    )
    embed.add_field(
        name="Proof",
        value=str(request.get("proof", "No proof provided"))[:1024],
        inline=False,
    )
    embed.add_field(
        name="Requested In",
        value=(
            f"{request.get('request_guild_name', 'Unknown Server')} "
            f"(`{request.get('request_guild_id', 'unknown')}`)"
        )[:1024],
        inline=False,
    )
    embed.add_field(name="Status", value=format_status_label(status), inline=True)
    embed.add_field(
        name="Requested At",
        value=str(request.get("created_at", "unknown"))[:1024],
        inline=True,
    )

    reviewer_id = request.get("reviewer_id")
    if reviewer_id:
        embed.add_field(
            name="Reviewed By",
            value=f"<@{reviewer_id}> (`{reviewer_id}`)",
            inline=False,
        )
    if request.get("reviewed_at"):
        embed.add_field(
            name="Reviewed At",
            value=str(request["reviewed_at"])[:1024],
            inline=False,
        )
    if request.get("review_note"):
        embed.add_field(
            name="Review Note",
            value=str(request["review_note"])[:1024],
            inline=False,
        )
    if request.get("result_summary"):
        embed.add_field(
            name="Result Summary",
            value=str(request["result_summary"])[:1024],
            inline=False,
        )

    embed.set_footer(text=f"Request ID: {request['request_id']}")
    return embed


@dataclass(frozen=True)
class BotConfig:
    token: str
    register_guild_id: Optional[int]
    department_command_guild_ids: set[int]
    owner_user_ids: set[int]
    mod_role_ids: set[int]
    global_ban_guild_ids: set[int]
    global_ban_log_channel_id: Optional[int]
    global_message_channel_map: dict[int, int]
    departments_config_path: Path
    data_file_path: Path

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            token=require_env("DISCORD_TOKEN"),
            register_guild_id=(
                int(os.getenv("REGISTER_GUILD_ID", "").strip())
                if os.getenv("REGISTER_GUILD_ID", "").strip()
                else None
            ),
            department_command_guild_ids=split_csv(os.getenv("DEPARTMENT_COMMAND_GUILD_IDS", "")),
            owner_user_ids=split_csv(os.getenv("OWNER_USER_IDS", "")),
            mod_role_ids=split_csv(os.getenv("MOD_ROLE_IDS", "")),
            global_ban_guild_ids=split_csv(os.getenv("GLOBAL_BAN_GUILD_IDS", "")),
            global_ban_log_channel_id=parse_optional_id(os.getenv("GLOBAL_BAN_LOG_CHANNEL_ID", "")),
            global_message_channel_map=parse_guild_channel_map(
                os.getenv("GLOBAL_MESSAGE_CHANNEL_MAP", "")
            ),
            departments_config_path=Path(
                os.getenv("DEPARTMENTS_CONFIG_PATH", "departments.json").strip()
                or "departments.json"
            ),
            data_file_path=Path(
                os.getenv("DATA_FILE_PATH", "data/moderation-store.json").strip()
                or "data/moderation-store.json"
            ),
        )


class ModerationStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.data = {"global_bans": {}, "global_ban_requests": {}}

    def load(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.save()
            return

        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        global_bans = payload.get("global_bans", {})
        if not isinstance(global_bans, dict):
            global_bans = {}
        global_ban_requests = payload.get("global_ban_requests", {})
        if not isinstance(global_ban_requests, dict):
            global_ban_requests = {}
        self.data = {
            "global_bans": global_bans,
            "global_ban_requests": global_ban_requests,
        }

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
        temp_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temp_path.replace(self.file_path)

    def get_global_ban(self, user_id: int) -> Optional[dict]:
        return self.data["global_bans"].get(str(user_id))

    def list_global_bans(self) -> list[dict]:
        entries = [
            {"user_id": user_id, **entry}
            for user_id, entry in self.data["global_bans"].items()
        ]
        entries.sort(key=lambda item: item["created_at"], reverse=True)
        return entries

    def set_global_ban(self, user_id: int, entry: dict) -> None:
        self.data["global_bans"][str(user_id)] = entry
        self.save()

    def remove_global_ban(self, user_id: int) -> Optional[dict]:
        removed = self.data["global_bans"].pop(str(user_id), None)
        if removed is not None:
            self.save()
        return removed

    def get_global_ban_request(self, request_id: str) -> Optional[dict]:
        return self.data["global_ban_requests"].get(request_id)

    def list_pending_global_ban_requests(self) -> list[dict]:
        entries = [
            request
            for request in self.data["global_ban_requests"].values()
            if request.get("status") == "pending" and request.get("request_message_id")
        ]
        entries.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return entries

    def set_global_ban_request(self, request_id: str, entry: dict) -> None:
        self.data["global_ban_requests"][request_id] = entry
        self.save()

    def update_global_ban_request(self, request_id: str, **updates: object) -> Optional[dict]:
        existing = self.data["global_ban_requests"].get(request_id)
        if existing is None:
            return None

        existing.update(updates)
        self.save()
        return existing

    def remove_global_ban_request(self, request_id: str) -> Optional[dict]:
        removed = self.data["global_ban_requests"].pop(request_id, None)
        if removed is not None:
            self.save()
        return removed


class GlobalBanRequestView(discord.ui.View):
    def __init__(self, bot: "GlobalModBot", request_id: str, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.request_id = request_id

        approve_button = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"gbanrequest:approve:{request_id}",
            disabled=disabled,
        )
        deny_button = discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"gbanrequest:deny:{request_id}",
            disabled=disabled,
        )
        approve_button.callback = self.approve_callback
        deny_button.callback = self.deny_callback
        self.add_item(approve_button)
        self.add_item(deny_button)

    async def approve_callback(self, interaction: discord.Interaction) -> None:
        await self.handle_review(interaction, "approved")

    async def deny_callback(self, interaction: discord.Interaction) -> None:
        await self.handle_review(interaction, "denied")

    async def handle_review(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.user.id not in self.bot.config.owner_user_ids:
            await interaction.response.send_message(
                "Only bot owners can review global ban requests.",
                ephemeral=True,
            )
            return

        request = self.bot.store.get_global_ban_request(self.request_id)
        if request is None:
            await interaction.response.send_message(
                "That global ban request no longer exists.",
                ephemeral=True,
            )
            return

        if request.get("status") != "pending":
            await interaction.response.send_message(
                f"This request has already been {request.get('status', 'processed')}.",
                ephemeral=True,
            )
            return

        if action == "approved":
            entry = {
                "reason": request["reason"],
                "moderator_id": str(interaction.user.id),
                "moderator_tag": str(interaction.user),
                "created_at": utc_now_iso(),
            }
            already_banned = self.bot.store.get_global_ban(int(request["user_id"])) is not None
            self.bot.store.set_global_ban(int(request["user_id"]), entry)
            results, targeted_guilds, missing_guild_ids = await self.bot.apply_global_ban_everywhere(
                int(request["user_id"]), entry
            )
            result_summary = (
                f"{format_target_scope(targeted_guilds, missing_guild_ids)}\n"
                f"{summarize_results(results)}"
            )
            review_note = (
                "Existing global ban entry updated from request."
                if already_banned
                else "Global ban issued from request."
            )
        else:
            result_summary = "Request denied. No global ban was applied."
            review_note = "Global ban request denied by owner."

        updated = self.bot.store.update_global_ban_request(
            self.request_id,
            status=action,
            reviewer_id=str(interaction.user.id),
            reviewer_tag=str(interaction.user),
            reviewed_at=utc_now_iso(),
            review_note=review_note,
            result_summary=result_summary,
        )
        assert updated is not None

        updated_embed = build_global_ban_request_embed(updated)
        disabled_view = GlobalBanRequestView(self.bot, self.request_id, disabled=True)
        await interaction.response.edit_message(embed=updated_embed, view=disabled_view)
        await interaction.followup.send(
            result_summary[:2000],
            ephemeral=True,
        )


class GlobalModBot(commands.Bot):
    def __init__(self, config: BotConfig, store: ModerationStore) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.store = store
        self.department_registry = DepartmentRegistry.from_path(
            config.departments_config_path
        )
        self._commands_registered = False
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        if not self._commands_registered:
            self.register_commands()
            self._commands_registered = True

        for request in self.store.list_pending_global_ban_requests():
            self.add_view(GlobalBanRequestView(self, request["request_id"]))

        sync_guild_ids = sorted(self.get_department_command_guild_ids(include_departments=True))
        if sync_guild_ids:
            for guild_id in sync_guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Synced {len(synced)} command(s) to guild {guild_id}.")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global command(s).")

    async def on_ready(self) -> None:
        if self.user is None:
            return
        print(f"Logged in as {self.user} in {len(self.guilds)} guild(s).")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.config.global_ban_guild_ids and guild.id not in self.config.global_ban_guild_ids:
            return

        results = []
        for entry in self.store.list_global_bans():
            results.append(await self.apply_global_ban_to_guild(guild, int(entry["user_id"]), entry))

        failures = sum(1 for result in results if result["status"] == "failed")
        print(
            f"Synced {len(results)} stored global ban(s) to {guild.name}. Failures: {failures}."
        )

    async def on_member_join(self, member: discord.Member) -> None:
        if self.config.global_ban_guild_ids and member.guild.id not in self.config.global_ban_guild_ids:
            return

        entry = self.store.get_global_ban(member.id)
        if entry is None:
            return

        try:
            await member.ban(reason=build_global_ban_reason(entry))
            print(f"Re-applied global ban for {member} in {member.guild.name}.")
        except Exception as error:
            print(f"Could not re-apply global ban for {member} in {member.guild.name}: {error}")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        print(f"Command error: {error}")
        message = "The command failed. Check the bot logs for details."
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def register_commands(self) -> None:
        register_department_commands(self)

        @self.tree.command(name="gban", description="Globally ban a user across every server this bot is in.")
        @app_commands.guild_only()
        @app_commands.describe(user="User to globally ban", reason="Reason for the global ban")
        async def gban(
            interaction: discord.Interaction, user: discord.User, reason: Optional[str] = None
        ) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            normalized_reason = normalize_reason(reason)
            entry = {
                "reason": normalized_reason,
                "moderator_id": str(interaction.user.id),
                "moderator_tag": str(interaction.user),
                "created_at": utc_now_iso(),
            }
            already_banned = self.store.get_global_ban(user.id) is not None
            self.store.set_global_ban(user.id, entry)
            results, targeted_guilds, missing_guild_ids = await self.apply_global_ban_everywhere(
                user.id, entry
            )
            prefix = (
                f"Updated the global ban for <@{user.id}>."
                if already_banned
                else f"Added <@{user.id}> to the global ban list."
            )
            response_text = (
                f"{prefix}\n\n"
                f"{format_target_scope(targeted_guilds, missing_guild_ids)}\n"
                f"{summarize_results(results)}"
            )
            log_embed = discord.Embed(
                title="Global Ban Updated" if already_banned else "Global Ban Issued",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
            )
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
            log_embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)
            log_embed.add_field(name="Reason", value=normalized_reason[:1024], inline=False)
            log_embed.add_field(
                name="Scope",
                value=format_target_scope(targeted_guilds, missing_guild_ids)[:1024],
                inline=False,
            )
            log_embed.add_field(
                name="Results",
                value=summarize_results(results)[:1024],
                inline=False,
            )
            log_notice = await self.send_global_ban_log(log_embed)
            if log_notice is not None:
                response_text += f"\nLog channel notice: {log_notice}"
            await interaction.edit_original_response(
                content=response_text
            )

        @self.tree.command(
            name="gbanrequest",
            description="Submit a global ban request for owner review.",
        )
        @app_commands.guild_only()
        @app_commands.describe(
            user="User to request a global ban for",
            reason="Reason for the global ban request",
            proof="Proof link or explanation for the request",
        )
        async def gbanrequest(
            interaction: discord.Interaction,
            user: discord.User,
            reason: str,
            proof: str,
        ) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            normalized_reason = normalize_reason(reason)
            normalized_proof = proof.strip() or "No proof provided"
            request_id = uuid4().hex[:12]
            request = {
                "request_id": request_id,
                "user_id": str(user.id),
                "requester_id": str(interaction.user.id),
                "requester_tag": str(interaction.user),
                "request_guild_id": str(interaction.guild_id or "unknown"),
                "request_guild_name": interaction.guild.name if interaction.guild else "Unknown Server",
                "reason": normalized_reason,
                "proof": normalized_proof[:1024],
                "created_at": utc_now_iso(),
                "status": "pending",
                "request_message_id": None,
            }
            self.store.set_global_ban_request(request_id, request)
            message, error = await self.send_global_ban_request(request)
            if error is not None or message is None:
                self.store.remove_global_ban_request(request_id)
                await interaction.edit_original_response(
                    content=f"Could not submit the global ban request: {error or 'Unknown error'}"
                )
                return

            updated_request = self.store.update_global_ban_request(
                request_id,
                request_message_id=str(message.id),
            )
            assert updated_request is not None
            await interaction.edit_original_response(
                content=(
                    f"Submitted a global ban request for <@{user.id}>.\n"
                    f"Request ID: `{request_id}`"
                )
            )

        @self.tree.command(name="ungban", description="Remove a user ID from the global ban list and unban them.")
        @app_commands.guild_only()
        @app_commands.describe(user_id="Discord user ID to globally unban", reason="Reason for removing the global ban")
        async def ungban(
            interaction: discord.Interaction, user_id: str, reason: Optional[str] = None
        ) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = user_id.strip()
            if not user_id.isdigit():
                await interaction.edit_original_response(content="`user_id` must be a Discord user ID.")
                return

            removed = self.store.remove_global_ban(int(user_id))
            if removed is None:
                await interaction.edit_original_response(
                    content=f"User ID `{user_id}` is not in the global ban list."
                )
                return

            normalized_reason = normalize_reason(reason)
            unban_reason = f"Global unban by {interaction.user.id} | {normalized_reason}"[:512]
            results, targeted_guilds, missing_guild_ids = await self.lift_global_ban_everywhere(
                int(user_id), unban_reason
            )
            response_text = (
                f"Removed `{user_id}` from the global ban list.\n\n"
                f"{format_target_scope(targeted_guilds, missing_guild_ids)}\n"
                f"{summarize_results(results)}"
            )
            log_embed = discord.Embed(
                title="Global Ban Removed",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            log_embed.add_field(name="User ID", value=f"`{user_id}`", inline=False)
            log_embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)
            log_embed.add_field(name="Reason", value=normalized_reason[:1024], inline=False)
            log_embed.add_field(
                name="Scope",
                value=format_target_scope(targeted_guilds, missing_guild_ids)[:1024],
                inline=False,
            )
            log_embed.add_field(
                name="Results",
                value=summarize_results(results)[:1024],
                inline=False,
            )
            log_notice = await self.send_global_ban_log(log_embed)
            if log_notice is not None:
                response_text += f"\nLog channel notice: {log_notice}"
            await interaction.edit_original_response(
                content=response_text
            )

        @self.tree.command(name="gbanlist", description="Show stored global bans.")
        @app_commands.guild_only()
        async def gbanlist(interaction: discord.Interaction) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.send_message(
                format_ban_list(self.store.list_global_bans()),
                ephemeral=True,
            )

        @self.tree.command(name="syncgbans", description="Re-apply all stored global bans to all servers.")
        @app_commands.guild_only()
        async def syncgbans(interaction: discord.Interaction) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            entries = self.store.list_global_bans()
            if not entries:
                await interaction.edit_original_response(content="No stored global bans to sync.")
                return

            results = []
            for entry in entries:
                batch_results, _, _ = await self.apply_global_ban_everywhere(
                    int(entry["user_id"]), entry
                )
                results.extend(batch_results)

            targeted_guilds, missing_guild_ids = self.get_target_guilds()
            response_text = (
                f"Re-applied {len(entries)} stored global ban(s).\n\n"
                f"{format_target_scope(targeted_guilds, missing_guild_ids)}\n"
                f"{summarize_results(results)}"
            )
            log_embed = discord.Embed(
                title="Global Ban Sync",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )
            log_embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)
            log_embed.add_field(name="Stored Entries", value=str(len(entries)), inline=False)
            log_embed.add_field(
                name="Scope",
                value=format_target_scope(targeted_guilds, missing_guild_ids)[:1024],
                inline=False,
            )
            log_embed.add_field(
                name="Results",
                value=summarize_results(results)[:1024],
                inline=False,
            )
            log_notice = await self.send_global_ban_log(log_embed)
            if log_notice is not None:
                response_text += f"\nLog channel notice: {log_notice}"

            await interaction.edit_original_response(
                content=response_text
            )

        @self.tree.command(name="ban", description="Ban a user from this server.")
        @app_commands.guild_only()
        @app_commands.describe(user="User to ban", reason="Reason for the ban")
        async def ban(
            interaction: discord.Interaction, user: discord.User, reason: Optional[str] = None
        ) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            assert interaction.guild is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            member = interaction.guild.get_member(user.id)

            if member is not None and not self.can_bot_moderate(member):
                await interaction.edit_original_response(
                    content=f"I cannot ban <@{user.id}> because of role hierarchy or missing permissions."
                )
                return

            try:
                await interaction.guild.ban(
                    discord.Object(id=user.id),
                    reason=f"Local ban by {interaction.user.id} | {normalize_reason(reason)}"[:512],
                )
            except Exception as error:
                await interaction.edit_original_response(
                    content=f"Could not ban <@{user.id}>: {summarize_exception(error)}"
                )
                return

            await interaction.edit_original_response(
                content=f"Banned <@{user.id}> from **{interaction.guild.name}**."
            )

        @self.tree.command(name="kick", description="Kick a user from this server.")
        @app_commands.guild_only()
        @app_commands.describe(user="User to kick", reason="Reason for the kick")
        async def kick(
            interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = None
        ) -> None:
            if not await self.ensure_access(interaction, "kick_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            if not self.can_bot_moderate(user):
                await interaction.edit_original_response(
                    content=f"I cannot kick <@{user.id}> because of role hierarchy or missing permissions."
                )
                return

            try:
                await user.kick(
                    reason=f"Kick by {interaction.user.id} | {normalize_reason(reason)}"[:512]
                )
            except Exception as error:
                await interaction.edit_original_response(
                    content=f"Could not kick <@{user.id}>: {summarize_exception(error)}"
                )
                return

            await interaction.edit_original_response(
                content=f"Kicked <@{user.id}> from **{user.guild.name}**."
            )

        @self.tree.command(name="timeout", description="Timeout a user in this server.")
        @app_commands.guild_only()
        @app_commands.describe(user="User to timeout", duration="Duration like 10m, 2h, or 3d", reason="Reason for the timeout")
        async def timeout(
            interaction: discord.Interaction,
            user: discord.Member,
            duration: str,
            reason: Optional[str] = None,
        ) -> None:
            if not await self.ensure_access(interaction, "moderate_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            parsed = parse_duration(duration)
            if parsed is None or parsed > timedelta(days=28):
                await interaction.edit_original_response(
                    content="Timeout duration must look like `10m`, `2h`, or `3d`, up to 28 days."
                )
                return

            if not self.can_bot_moderate(user):
                await interaction.edit_original_response(
                    content=f"I cannot timeout <@{user.id}> because of role hierarchy or missing permissions."
                )
                return

            try:
                await user.timeout(
                    parsed,
                    reason=f"Timeout by {interaction.user.id} | {normalize_reason(reason)}"[:512],
                )
            except Exception as error:
                await interaction.edit_original_response(
                    content=f"Could not timeout <@{user.id}>: {summarize_exception(error)}"
                )
                return

            await interaction.edit_original_response(
                content=f"Timed out <@{user.id}> for {format_duration(parsed)}."
            )

        @self.tree.command(name="purge", description="Bulk delete recent messages in this channel.")
        @app_commands.guild_only()
        @app_commands.describe(amount="How many messages to delete")
        async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
            if not await self.ensure_access(interaction, "manage_messages"):
                return

            channel = interaction.channel
            if channel is None or not hasattr(channel, "purge"):
                await interaction.response.send_message(
                    "This channel does not support bulk deletion.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                deleted = await channel.purge(limit=amount, bulk=True)
            except Exception as error:
                await interaction.edit_original_response(
                    content=f"Could not purge messages: {summarize_exception(error)}"
                )
                return

            await interaction.edit_original_response(
                content=(
                    f"Deleted {len(deleted)} message(s). Messages older than 14 days are skipped by Discord."
                )
            )

        @self.tree.command(name="globalmessage", description="Send a message to the configured channels in your target servers.")
        @app_commands.guild_only()
        @app_commands.describe(message="Message to send to every configured server")
        async def globalmessage(
            interaction: discord.Interaction, message: str
        ) -> None:
            if not await self.ensure_access(interaction, "manage_messages"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            target_guilds, missing_guild_ids = self.get_target_guilds()
            results = await self.send_global_message_everywhere(message, target_guilds)

            await interaction.edit_original_response(
                content=(
                    f"{format_target_scope(target_guilds, missing_guild_ids)}\n"
                    f"{summarize_message_results(results)}"
                )
            )

    async def ensure_access(self, interaction: discord.Interaction, permission_name: str) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self.send_ephemeral(interaction, "This command can only be used in a server.")
            return False

        member = interaction.user
        if member.id in self.config.owner_user_ids:
            return True

        if any(role.id in self.config.mod_role_ids for role in member.roles):
            return True

        if getattr(member.guild_permissions, permission_name):
            return True

        label = PERMISSION_LABELS[permission_name]
        requirements = [f"the **{label}** permission"]
        if self.config.mod_role_ids:
            requirements.append("a role listed in `MOD_ROLE_IDS`")
        if self.config.owner_user_ids:
            requirements.append("a user ID listed in `OWNER_USER_IDS`")

        await self.send_ephemeral(
            interaction,
            f"You need {' or '.join(requirements)} to use this command.",
        )
        return False

    async def send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def can_bot_moderate(self, member: discord.Member) -> bool:
        bot_member = member.guild.me
        if bot_member is None:
            return False
        if member.id == member.guild.owner_id:
            return False
        if member.id == bot_member.id:
            return False
        return bot_member.top_role > member.top_role

    async def get_member_if_present(
        self, guild: discord.Guild, user_id: int
    ) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            return None
        except discord.HTTPException:
            return None

    async def get_global_ban_blocker(
        self, guild: discord.Guild, user_id: int
    ) -> Optional[str]:
        bot_member = guild.me
        if bot_member is None:
            return "Bot member is not available in this server."

        if not bot_member.guild_permissions.ban_members:
            return "Bot is missing the Ban Members permission."

        target_member = await self.get_member_if_present(guild, user_id)
        if target_member is None:
            return None

        if target_member.id == guild.owner_id:
            return "Cannot ban the server owner."

        if target_member.id == bot_member.id:
            return "Cannot ban the bot itself."

        if bot_member.top_role <= target_member.top_role:
            return "Bot role is not above the target user's top role."

        return None

    async def apply_global_ban_to_guild(
        self, guild: discord.Guild, user_id: int, entry: dict
    ) -> dict:
        blocker = await self.get_global_ban_blocker(guild, user_id)
        if blocker is not None:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": blocker,
            }

        try:
            await guild.ban(discord.Object(id=user_id), reason=build_global_ban_reason(entry))
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "banned",
            }
        except Exception as error:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": summarize_exception(error),
            }

    async def lift_global_ban_from_guild(
        self, guild: discord.Guild, user_id: int, reason: str
    ) -> dict:
        bot_member = guild.me
        if bot_member is None:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": "Bot member is not available in this server.",
            }

        if not bot_member.guild_permissions.ban_members:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": "Bot is missing the Ban Members permission.",
            }

        try:
            try:
                await guild.fetch_ban(discord.Object(id=user_id))
            except discord.NotFound:
                return {
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "status": "skipped",
                }

            await guild.unban(discord.Object(id=user_id), reason=reason)
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "unbanned",
            }
        except Exception as error:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": summarize_exception(error),
            }

    async def send_global_message_to_guild(self, guild: discord.Guild, message: str) -> dict:
        channel_id = self.config.global_message_channel_map.get(guild.id)
        if channel_id is None:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "missing_channel",
            }

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception as error:
                return {
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "status": "failed",
                    "reason": summarize_exception(error),
                }

        if not hasattr(channel, "send"):
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": "Configured channel is not messageable.",
            }

        try:
            await channel.send(message)
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "sent",
            }
        except Exception as error:
            return {
                "guild_id": guild.id,
                "guild_name": guild.name,
                "status": "failed",
                "reason": summarize_exception(error),
            }

    async def send_global_ban_log(self, embed: discord.Embed) -> Optional[str]:
        channel_id = self.config.global_ban_log_channel_id
        if channel_id is None:
            return None

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as error:
                return summarize_exception(error)

        if not hasattr(channel, "send"):
            return "Configured global ban log channel is not messageable."

        try:
            await channel.send(embed=embed)
            return None
        except Exception as error:
            return summarize_exception(error)

    async def send_global_ban_request(
        self, request: dict
    ) -> tuple[Optional[discord.Message], Optional[str]]:
        channel_id = self.config.global_ban_log_channel_id
        if channel_id is None:
            return None, "GLOBAL_BAN_LOG_CHANNEL_ID is not configured."

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as error:
                return None, summarize_exception(error)

        if not hasattr(channel, "send"):
            return None, "Configured global ban log channel is not messageable."

        view = GlobalBanRequestView(self, request["request_id"])
        embed = build_global_ban_request_embed(request)
        try:
            message = await channel.send(embed=embed, view=view)
            return message, None
        except Exception as error:
            return None, summarize_exception(error)

    async def apply_global_ban_everywhere(
        self, user_id: int, entry: dict
    ) -> tuple[list[dict], list[discord.Guild], list[int]]:
        results = []
        target_guilds, missing_guild_ids = self.get_target_guilds()
        for guild in target_guilds:
            results.append(await self.apply_global_ban_to_guild(guild, user_id, entry))
        return results, target_guilds, missing_guild_ids

    async def lift_global_ban_everywhere(
        self, user_id: int, reason: str
    ) -> tuple[list[dict], list[discord.Guild], list[int]]:
        results = []
        target_guilds, missing_guild_ids = self.get_target_guilds()
        for guild in target_guilds:
            results.append(await self.lift_global_ban_from_guild(guild, user_id, reason))
        return results, target_guilds, missing_guild_ids

    async def send_global_message_everywhere(
        self, message: str, target_guilds: list[discord.Guild]
    ) -> list[dict]:
        results = []
        for guild in target_guilds:
            results.append(await self.send_global_message_to_guild(guild, message))
        return results

    def get_department_command_guild_ids(self, *, include_departments: bool = False) -> set[int]:
        guild_ids = set(self.config.department_command_guild_ids)
        if self.config.register_guild_id is not None:
            guild_ids.add(self.config.register_guild_id)

        if include_departments:
            for department in self.department_registry.departments.values():
                if department.guild_id is not None:
                    guild_ids.add(department.guild_id)

        return guild_ids

    def get_department_access_guild(
        self, interaction_guild_id: Optional[int], target_guild_id: Optional[int]
    ) -> Optional[discord.Guild]:
        candidate_ids: list[int] = []

        if self.config.register_guild_id is not None:
            candidate_ids.append(self.config.register_guild_id)

        if (
            interaction_guild_id is not None
            and interaction_guild_id in self.config.department_command_guild_ids
        ):
            candidate_ids.append(interaction_guild_id)

        for guild_id in sorted(self.config.department_command_guild_ids):
            if guild_id not in candidate_ids:
                candidate_ids.append(guild_id)

        if target_guild_id is not None and target_guild_id not in candidate_ids:
            candidate_ids.append(target_guild_id)

        for guild_id in candidate_ids:
            guild = self.get_guild(guild_id)
            if guild is not None:
                return guild

        return None

    def get_target_guilds(self) -> tuple[list[discord.Guild], list[int]]:
        if not self.config.global_ban_guild_ids:
            return list(self.guilds), []

        available_guilds = {guild.id: guild for guild in self.guilds}
        target_guilds = [
            available_guilds[guild_id]
            for guild_id in self.config.global_ban_guild_ids
            if guild_id in available_guilds
        ]
        missing_guild_ids = [
            guild_id
            for guild_id in self.config.global_ban_guild_ids
            if guild_id not in available_guilds
        ]
        return target_guilds, missing_guild_ids


def main() -> None:
    config = BotConfig.from_env()
    store = ModerationStore(config.data_file_path)
    store.load()

    bot = GlobalModBot(config, store)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
