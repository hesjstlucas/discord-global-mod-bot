import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

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


@dataclass(frozen=True)
class BotConfig:
    token: str
    register_guild_id: Optional[int]
    owner_user_ids: set[int]
    mod_role_ids: set[int]
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
            owner_user_ids=split_csv(os.getenv("OWNER_USER_IDS", "")),
            mod_role_ids=split_csv(os.getenv("MOD_ROLE_IDS", "")),
            data_file_path=Path(
                os.getenv("DATA_FILE_PATH", "data/moderation-store.json").strip()
                or "data/moderation-store.json"
            ),
        )


class ModerationStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.data = {"global_bans": {}}

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
        self.data = {"global_bans": global_bans}

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


class GlobalModBot(commands.Bot):
    def __init__(self, config: BotConfig, store: ModerationStore) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.store = store
        self._commands_registered = False
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        if not self._commands_registered:
            self.register_commands()
            self._commands_registered = True

        if self.config.register_guild_id is not None:
            guild = discord.Object(id=self.config.register_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(
                f"Synced {len(synced)} command(s) to guild {self.config.register_guild_id}."
            )
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global command(s).")

    async def on_ready(self) -> None:
        if self.user is None:
            return
        print(f"Logged in as {self.user} in {len(self.guilds)} guild(s).")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        results = []
        for entry in self.store.list_global_bans():
            results.append(await self.apply_global_ban_to_guild(guild, int(entry["user_id"]), entry))

        failures = sum(1 for result in results if result["status"] == "failed")
        print(
            f"Synced {len(results)} stored global ban(s) to {guild.name}. Failures: {failures}."
        )

    async def on_member_join(self, member: discord.Member) -> None:
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
        @self.tree.command(name="gban", description="Globally ban a user across every server this bot is in.")
        @app_commands.guild_only()
        @app_commands.describe(user="User to globally ban", reason="Reason for the global ban")
        async def gban(
            interaction: discord.Interaction, user: discord.User, reason: Optional[str] = None
        ) -> None:
            if not await self.ensure_access(interaction, "ban_members"):
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            entry = {
                "reason": normalize_reason(reason),
                "moderator_id": str(interaction.user.id),
                "moderator_tag": str(interaction.user),
                "created_at": utc_now_iso(),
            }
            already_banned = self.store.get_global_ban(user.id) is not None
            self.store.set_global_ban(user.id, entry)
            results = await self.apply_global_ban_everywhere(user.id, entry)
            prefix = (
                f"Updated the global ban for <@{user.id}>."
                if already_banned
                else f"Added <@{user.id}> to the global ban list."
            )
            await interaction.edit_original_response(
                content=f"{prefix}\n\n{summarize_results(results)}"
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

            unban_reason = f"Global unban by {interaction.user.id} | {normalize_reason(reason)}"[:512]
            results = await self.lift_global_ban_everywhere(int(user_id), unban_reason)
            await interaction.edit_original_response(
                content=f"Removed `{user_id}` from the global ban list.\n\n{summarize_results(results)}"
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
                results.extend(await self.apply_global_ban_everywhere(int(entry["user_id"]), entry))

            await interaction.edit_original_response(
                content=f"Re-applied {len(entries)} stored global ban(s).\n\n{summarize_results(results)}"
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

    async def apply_global_ban_to_guild(
        self, guild: discord.Guild, user_id: int, entry: dict
    ) -> dict:
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

    async def apply_global_ban_everywhere(self, user_id: int, entry: dict) -> list[dict]:
        results = []
        for guild in self.guilds:
            results.append(await self.apply_global_ban_to_guild(guild, user_id, entry))
        return results

    async def lift_global_ban_everywhere(self, user_id: int, reason: str) -> list[dict]:
        results = []
        for guild in self.guilds:
            results.append(await self.lift_global_ban_from_guild(guild, user_id, reason))
        return results


def main() -> None:
    config = BotConfig.from_env()
    store = ModerationStore(config.data_file_path)
    store.load()

    bot = GlobalModBot(config, store)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
