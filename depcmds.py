from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot import GlobalModBot


def normalize_department_key(value: str) -> str:
    collapsed = []
    last_was_sep = False

    for char in value.strip().lower():
        if char.isalnum():
            collapsed.append(char)
            last_was_sep = False
            continue

        if not last_was_sep:
            collapsed.append("_")
        last_was_sep = True

    return "".join(collapsed).strip("_")


def parse_id_set(value: object) -> set[int]:
    if not isinstance(value, list):
        return set()

    result: set[int] = set()
    for item in value:
        if isinstance(item, int):
            result.add(item)
        elif isinstance(item, str) and item.strip().isdigit():
            result.add(int(item.strip()))
    return result


def parse_id_step(value: object) -> tuple[int, ...]:
    if isinstance(value, dict):
        value = value.get("role_ids")

    raw_items = value if isinstance(value, list) else [value]
    result: list[int] = []
    seen: set[int] = set()

    for item in raw_items:
        role_id = parse_optional_id(item)
        if role_id is None or role_id in seen:
            continue
        result.append(role_id)
        seen.add(role_id)

    return tuple(result)


def parse_id_steps(value: object) -> tuple[tuple[int, ...], ...]:
    if not isinstance(value, list):
        return ()

    result: list[tuple[int, ...]] = []
    for item in value:
        step = parse_id_step(item)
        if step and (not result or result[-1] != step):
            result.append(step)

    return tuple(result)


def parse_optional_id(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


@dataclass(frozen=True)
class DepartmentConfig:
    key: str
    label: str
    guild_id: Optional[int]
    member_role_ids: set[int]
    promotion_steps: tuple[tuple[int, ...], ...]
    managed_role_ids: set[int]
    log_channel_id: Optional[int]
    promotion_channel_id: Optional[int]
    ban_role_id: Optional[int]
    termination_floor_role_id: Optional[int]

    @property
    def active_role_ids(self) -> set[int]:
        return set(self.member_role_ids) | self.promotion_role_id_set | set(self.managed_role_ids)

    @property
    def promotion_role_id_set(self) -> set[int]:
        return {role_id for step in self.promotion_steps for role_id in step}

    @property
    def all_role_ids(self) -> set[int]:
        result = set(self.active_role_ids)
        if self.ban_role_id is not None:
            result.add(self.ban_role_id)
        return result


class DepartmentRegistry:
    def __init__(self, path: Path, departments: dict[str, DepartmentConfig]) -> None:
        self.path = path
        self.departments = departments

    @classmethod
    def from_path(cls, path: Path) -> "DepartmentRegistry":
        if not path.exists():
            print(f"Department config not found at {path}. /dep commands will stay inactive until you add it.")
            return cls(path, {})

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            print(f"Could not load department config {path}: {error}")
            return cls(path, {})

        raw_departments = payload.get("departments", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_departments, dict):
            return cls(path, {})

        departments: dict[str, DepartmentConfig] = {}
        for raw_key, raw_value in raw_departments.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, dict):
                continue

            key = normalize_department_key(raw_key)
            if not key:
                continue

            label = str(raw_value.get("label") or raw_key).strip() or raw_key
            department = DepartmentConfig(
                key=key,
                label=label,
                guild_id=parse_optional_id(raw_value.get("guild_id")),
                member_role_ids=parse_id_set(raw_value.get("member_role_ids")),
                promotion_steps=parse_id_steps(raw_value.get("promotion_role_ids")),
                managed_role_ids=parse_id_set(raw_value.get("managed_role_ids")),
                log_channel_id=parse_optional_id(raw_value.get("log_channel_id")),
                promotion_channel_id=parse_optional_id(raw_value.get("promotion_channel_id")),
                ban_role_id=parse_optional_id(raw_value.get("ban_role_id")),
                termination_floor_role_id=parse_optional_id(
                    raw_value.get("termination_floor_role_id")
                ),
            )
            departments[key] = department

        return cls(path, departments)

    def get(self, value: str) -> Optional[DepartmentConfig]:
        normalized = normalize_department_key(value)
        if normalized in self.departments:
            return self.departments[normalized]

        for department in self.departments.values():
            if normalize_department_key(department.label) == normalized:
                return department

        return None

    def autocomplete(self, current: str) -> list[app_commands.Choice[str]]:
        normalized = normalize_department_key(current)
        matches = []

        for department in self.departments.values():
            if not normalized or normalized in department.key or normalized in normalize_department_key(
                department.label
            ):
                matches.append(
                    app_commands.Choice(name=department.label[:100], value=department.key)
                )

        matches.sort(key=lambda item: item.name)
        return matches[:25]


def format_role_names(roles: list[discord.Role]) -> str:
    if not roles:
        return "none"
    return ", ".join(role.name for role in roles)


def format_role_ids(role_ids: list[int]) -> str:
    if not role_ids:
        return "none"
    return ", ".join(f"`{role_id}`" for role_id in role_ids)


def build_department_embed(
    *,
    title: str,
    color: discord.Color,
    department: DepartmentConfig,
    member: discord.Member,
    moderator: discord.abc.User,
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Department", value=department.label, inline=True)
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Moderator", value=f"{moderator.mention}", inline=True)
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    return embed


async def resolve_message_channel(
    guild: discord.Guild, channel_id: Optional[int]
) -> tuple[Optional[discord.abc.Messageable], Optional[str]]:
    if channel_id is None:
        return None, "No channel configured."

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception as error:
            return None, str(error)

    if not hasattr(channel, "send"):
        return None, "Configured channel is not messageable."

    return channel, None


async def send_embed_to_channel(
    guild: discord.Guild, channel_id: Optional[int], embed: discord.Embed
) -> Optional[str]:
    channel, error = await resolve_message_channel(guild, channel_id)
    if channel is None:
        return error

    try:
        await channel.send(embed=embed)
        return None
    except Exception as error:
        return str(error)


def get_member_department_roles(
    member: discord.Member, department: DepartmentConfig, *, include_ban_role: bool = False
) -> list[discord.Role]:
    role_ids = set(department.active_role_ids)
    if include_ban_role and department.ban_role_id is not None:
        role_ids.add(department.ban_role_id)

    return [role for role in member.roles if role.id in role_ids]


def resolve_step_roles(
    guild: discord.Guild, step: tuple[int, ...]
) -> tuple[list[discord.Role], list[int]]:
    roles: list[discord.Role] = []
    missing_role_ids: list[int] = []

    for role_id in step:
        role = guild.get_role(role_id)
        if role is None:
            missing_role_ids.append(role_id)
            continue
        roles.append(role)

    return roles, missing_role_ids


def get_member_rank_index(member: discord.Member, department: DepartmentConfig) -> Optional[int]:
    member_role_ids = {role.id for role in member.roles}
    matched_index: Optional[int] = None

    for index, step in enumerate(department.promotion_steps):
        if any(role_id in member_role_ids for role_id in step):
            matched_index = index

    return matched_index


def get_step_index_for_role(department: DepartmentConfig, role_id: int) -> Optional[int]:
    for index, step in enumerate(department.promotion_steps):
        if role_id in step:
            return index

    return None


def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False
    return bot_member.top_role > role and not role.managed


def collect_unmanageable_roles(guild: discord.Guild, roles: list[discord.Role]) -> list[discord.Role]:
    return [role for role in roles if not bot_can_manage_role(guild, role)]


async def resolve_department_for_interaction(
    bot: GlobalModBot,
    interaction: discord.Interaction,
    department_name: str,
) -> Optional[tuple[DepartmentConfig, discord.Guild]]:
    department = bot.department_registry.get(department_name)
    if department is None:
        await bot.send_ephemeral(
            interaction,
            f"Department `{department_name}` was not found in `{bot.department_registry.path.name}`.",
        )
        return None

    if interaction.guild is None:
        await bot.send_ephemeral(interaction, "This command can only be used in a server.")
        return None

    target_guild_id = department.guild_id or interaction.guild.id
    target_guild = bot.get_guild(target_guild_id)
    if target_guild is None:
        await bot.send_ephemeral(
            interaction,
            f"I am not connected to the configured guild `{target_guild_id}` for {department.label}.",
        )
        return None

    allowed_command_guild_ids = bot.get_department_command_guild_ids()
    if interaction.guild.id != target_guild.id and interaction.guild.id not in allowed_command_guild_ids:
        await bot.send_ephemeral(
            interaction,
            (
                f"{department.label} can only be used in **{target_guild.name}** or one of the "
                "configured department command servers."
            ),
        )
        return None

    return department, target_guild


async def resolve_department_member(
    bot: GlobalModBot,
    interaction: discord.Interaction,
    target_guild: discord.Guild,
    user: discord.abc.User,
) -> Optional[discord.Member]:
    target_member = await bot.get_member_if_present(target_guild, user.id)
    if target_member is not None:
        return target_member

    await bot.send_ephemeral(
        interaction,
        f"{user.mention} is not a member of **{target_guild.name}**.",
    )
    return None


async def autocomplete_department(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, discord.Client) or not hasattr(bot, "department_registry"):
        return []
    return bot.department_registry.autocomplete(current)


async def autocomplete_department_role(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, discord.Client) or not hasattr(bot, "department_registry"):
        return []

    department_value = getattr(interaction.namespace, "department", "")
    if not isinstance(department_value, str) or not department_value.strip():
        return []

    department = bot.department_registry.get(department_value)
    if department is None:
        return []

    target_guild_id = department.guild_id or (interaction.guild.id if interaction.guild is not None else None)
    if target_guild_id is None:
        return []

    target_guild = bot.get_guild(target_guild_id)
    if target_guild is None:
        return []

    current_lower = current.strip().lower()
    matches: list[app_commands.Choice[str]] = []
    seen_role_ids: set[int] = set()

    for step in department.promotion_steps:
        for role_id in step:
            if role_id in seen_role_ids:
                continue

            role = target_guild.get_role(role_id)
            if role is None:
                continue

            seen_role_ids.add(role_id)
            if current_lower and current_lower not in role.name.lower():
                continue

            matches.append(app_commands.Choice(name=role.name[:100], value=str(role.id)))

    return matches[:25]


def register_department_commands(bot: GlobalModBot) -> None:
    dep_group = app_commands.Group(name="dep", description="Department moderation commands")

    @dep_group.command(name="kick", description="Remove a member from a department.")
    @app_commands.describe(
        member="Member to remove from the department",
        department="Department name",
        reason="Reason for the department kick",
    )
    @app_commands.autocomplete(department=autocomplete_department)
    async def dep_kick(
        interaction: discord.Interaction,
        member: discord.User,
        department: str,
        reason: str,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_roles"):
            return

        resolved = await resolve_department_for_interaction(bot, interaction, department)
        if resolved is None:
            return
        dept, target_guild = resolved

        target_member = await resolve_department_member(bot, interaction, target_guild, member)
        if target_member is None:
            return

        if not bot.can_bot_moderate(target_member):
            await bot.send_ephemeral(
                interaction,
                f"I cannot manage {target_member.mention} because of role hierarchy or missing permissions.",
            )
            return

        roles_to_remove = get_member_department_roles(target_member, dept)
        if not roles_to_remove:
            await bot.send_ephemeral(
                interaction,
                f"{target_member.mention} does not have any configured roles for {dept.label}.",
            )
            return

        unmanageable = collect_unmanageable_roles(target_guild, roles_to_remove)
        if unmanageable:
            await bot.send_ephemeral(
                interaction,
                f"I cannot remove these roles: {format_role_names(unmanageable)}.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"Department kick | {dept.label} | by {interaction.user.id} | {reason}"[:512]
        await target_member.remove_roles(*roles_to_remove, reason=audit_reason)

        embed = build_department_embed(
            title="Department Kick",
            color=discord.Color.orange(),
            department=dept,
            member=target_member,
            moderator=interaction.user,
            reason=reason,
        )
        embed.add_field(name="Removed Roles", value=format_role_names(roles_to_remove), inline=False)
        log_error = await send_embed_to_channel(target_guild, dept.log_channel_id, embed)

        message = (
            f"Removed {target_member.mention} from {dept.label} in **{target_guild.name}**.\n"
            f"Removed roles: {format_role_names(roles_to_remove)}."
        )
        if log_error is not None:
            message += f"\nLog channel notice: {log_error}"

        await interaction.edit_original_response(content=message)

    @dep_group.command(name="ban", description="Ban a member from a department.")
    @app_commands.describe(
        member="Member to department-ban",
        department="Department name",
        reason="Reason for the department ban",
    )
    @app_commands.autocomplete(department=autocomplete_department)
    async def dep_ban(
        interaction: discord.Interaction,
        member: discord.User,
        department: str,
        reason: str,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_roles"):
            return

        resolved = await resolve_department_for_interaction(bot, interaction, department)
        if resolved is None:
            return
        dept, target_guild = resolved

        target_member = await resolve_department_member(bot, interaction, target_guild, member)
        if target_member is None:
            return

        if not bot.can_bot_moderate(target_member):
            await bot.send_ephemeral(
                interaction,
                f"I cannot manage {target_member.mention} because of role hierarchy or missing permissions.",
            )
            return

        roles_to_remove = get_member_department_roles(target_member, dept)
        unmanageable = collect_unmanageable_roles(target_guild, roles_to_remove)
        if unmanageable:
            await bot.send_ephemeral(
                interaction,
                f"I cannot remove these roles: {format_role_names(unmanageable)}.",
            )
            return

        ban_role = target_guild.get_role(dept.ban_role_id) if dept.ban_role_id is not None else None
        if dept.ban_role_id is not None and ban_role is None:
            await bot.send_ephemeral(
                interaction,
                f"{dept.label} is missing its configured department ban role.",
            )
            return

        if ban_role is not None and not bot_can_manage_role(target_guild, ban_role):
            await bot.send_ephemeral(
                interaction,
                f"I cannot assign the configured ban role `{ban_role.name}`.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"Department ban | {dept.label} | by {interaction.user.id} | {reason}"[:512]

        if roles_to_remove:
            await target_member.remove_roles(*roles_to_remove, reason=audit_reason)

        added_ban_role = False
        if ban_role is not None and ban_role not in target_member.roles:
            await target_member.add_roles(ban_role, reason=audit_reason)
            added_ban_role = True

        embed = build_department_embed(
            title="Department Ban",
            color=discord.Color.red(),
            department=dept,
            member=target_member,
            moderator=interaction.user,
            reason=reason,
        )
        embed.add_field(name="Removed Roles", value=format_role_names(roles_to_remove), inline=False)
        embed.add_field(
            name="Ban Role",
            value=ban_role.name if added_ban_role and ban_role is not None else "none",
            inline=False,
        )
        log_error = await send_embed_to_channel(target_guild, dept.log_channel_id, embed)

        message = f"Department-banned {target_member.mention} from {dept.label} in **{target_guild.name}**."
        if roles_to_remove:
            message += f"\nRemoved roles: {format_role_names(roles_to_remove)}."
        if added_ban_role and ban_role is not None:
            message += f"\nAdded role: {ban_role.name}."
        if log_error is not None:
            message += f"\nLog channel notice: {log_error}"

        await interaction.edit_original_response(content=message)

    @dep_group.command(name="infract", description="Log or apply a department infraction.")
    @app_commands.describe(
        member="Member receiving the infraction",
        department="Department name",
        action="Infraction action",
        reason="Reason for the infraction",
    )
    @app_commands.autocomplete(department=autocomplete_department)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="warn", value="warn"),
            app_commands.Choice(name="strike", value="strike"),
            app_commands.Choice(name="terminate", value="terminate"),
        ]
    )
    async def dep_infract(
        interaction: discord.Interaction,
        member: discord.User,
        department: str,
        action: app_commands.Choice[str],
        reason: str,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_roles"):
            return

        resolved = await resolve_department_for_interaction(bot, interaction, department)
        if resolved is None:
            return
        dept, target_guild = resolved

        target_member = await resolve_department_member(bot, interaction, target_guild, member)
        if target_member is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        action_value = action.value
        removed_roles: list[discord.Role] = []

        if action_value == "terminate":
            if not bot.can_bot_moderate(target_member):
                await interaction.edit_original_response(
                    content=(
                        f"I cannot manage {target_member.mention} because of role hierarchy or missing permissions."
                    )
                )
                return

            if dept.termination_floor_role_id is None:
                await interaction.edit_original_response(
                    content=f"{dept.label} is missing `termination_floor_role_id` in the department config."
                )
                return

            floor_role = target_guild.get_role(dept.termination_floor_role_id)
            if floor_role is None:
                await interaction.edit_original_response(
                    content=f"{dept.label} is missing its configured termination floor role."
                )
                return

            removable = [
                role
                for role in get_member_department_roles(target_member, dept)
                if role.position > floor_role.position
            ]
            unmanageable = collect_unmanageable_roles(target_guild, removable)
            if unmanageable:
                await interaction.edit_original_response(
                    content=f"I cannot remove these roles: {format_role_names(unmanageable)}."
                )
                return

            audit_reason = (
                f"Department terminate | {dept.label} | by {interaction.user.id} | {reason}"[:512]
            )
            if removable:
                await target_member.remove_roles(*removable, reason=audit_reason)
                removed_roles = removable

        embed = build_department_embed(
            title=f"Department {action_value.title()}",
            color=discord.Color.gold() if action_value != "terminate" else discord.Color.dark_red(),
            department=dept,
            member=target_member,
            moderator=interaction.user,
            reason=reason,
        )
        if action_value == "terminate":
            embed.add_field(
                name="Removed Roles",
                value=format_role_names(removed_roles),
                inline=False,
            )
        log_error = await send_embed_to_channel(target_guild, dept.log_channel_id, embed)

        message = (
            f"Logged a {action_value} infraction for {target_member.mention} in {dept.label} "
            f"on **{target_guild.name}**."
        )
        if removed_roles:
            message += f"\nRemoved roles: {format_role_names(removed_roles)}."
        if log_error is not None:
            message += f"\nLog channel notice: {log_error}"

        await interaction.edit_original_response(content=message)

    @dep_group.command(name="promote", description="Promote a member within a department.")
    @app_commands.describe(
        member="Member to promote",
        department="Department name",
        role="Department role to assign",
        reason="Reason for the promotion",
    )
    @app_commands.autocomplete(
        department=autocomplete_department,
        role=autocomplete_department_role,
    )
    async def dep_promote(
        interaction: discord.Interaction,
        member: discord.User,
        department: str,
        role: str,
        reason: str,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_roles"):
            return

        resolved = await resolve_department_for_interaction(bot, interaction, department)
        if resolved is None:
            return
        dept, target_guild = resolved

        if not dept.promotion_steps:
            await bot.send_ephemeral(
                interaction,
                f"{dept.label} does not have any configured promotion ranks.",
            )
            return

        if not role.isdigit():
            await bot.send_ephemeral(
                interaction,
                "Choose a role from the department role suggestions.",
            )
            return

        target_member = await resolve_department_member(bot, interaction, target_guild, member)
        if target_member is None:
            return

        if not bot.can_bot_moderate(target_member):
            await bot.send_ephemeral(
                interaction,
                f"I cannot manage {target_member.mention} because of role hierarchy or missing permissions.",
            )
            return

        target_index = get_step_index_for_role(dept, int(role))
        if target_index is None:
            await bot.send_ephemeral(
                interaction,
                f"That role is not an allowed promotion role for {dept.label}.",
            )
            return

        current_index = get_member_rank_index(target_member, dept)
        previous_roles: list[discord.Role] = []
        previous_step: Optional[tuple[int, ...]] = None

        if current_index is not None:
            previous_step = dept.promotion_steps[current_index]
            previous_roles, previous_missing = resolve_step_roles(target_guild, previous_step)
            if previous_missing:
                await bot.send_ephemeral(
                    interaction,
                    f"{dept.label} is missing current rank role IDs: {format_role_ids(previous_missing)}.",
                )
                return

        target_step = dept.promotion_steps[target_index]

        target_roles, missing_target_role_ids = resolve_step_roles(target_guild, target_step)
        if missing_target_role_ids:
            await bot.send_ephemeral(
                interaction,
                (
                    f"The next configured {dept.label} rank is missing role IDs: "
                    f"{format_role_ids(missing_target_role_ids)}."
                ),
            )
            return

        unmanageable_target_roles = collect_unmanageable_roles(target_guild, target_roles)
        if unmanageable_target_roles:
            await bot.send_ephemeral(
                interaction,
                f"I cannot assign these roles: {format_role_names(unmanageable_target_roles)}.",
            )
            return

        if current_index == target_index and all(role in target_member.roles for role in target_roles):
            await bot.send_ephemeral(
                interaction,
                f"{target_member.mention} already has the selected {dept.label} rank roles.",
            )
            return

        target_role_ids = set(target_step)
        roles_to_remove = [
            current_role
            for current_role in target_member.roles
            if current_role.id in dept.promotion_role_id_set and current_role.id not in target_role_ids
        ]
        roles_to_add = [role for role in target_roles if role not in target_member.roles]
        unmanageable = collect_unmanageable_roles(target_guild, roles_to_remove)
        if unmanageable:
            await bot.send_ephemeral(
                interaction,
                f"I cannot remove these promotion roles: {format_role_names(unmanageable)}.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"Department promote | {dept.label} | by {interaction.user.id} | {reason}"[:512]

        if roles_to_remove:
            await target_member.remove_roles(*roles_to_remove, reason=audit_reason)
        if roles_to_add:
            await target_member.add_roles(*roles_to_add, reason=audit_reason)

        embed = build_department_embed(
            title="Department Promotion",
            color=discord.Color.green(),
            department=dept,
            member=target_member,
            moderator=interaction.user,
            reason=reason,
        )
        if previous_roles:
            embed.add_field(
                name="Previous Rank Roles",
                value=format_role_names(previous_roles),
                inline=False,
            )
        embed.add_field(name="Assigned Roles", value=format_role_names(target_roles), inline=False)
        if roles_to_remove:
            embed.add_field(
                name="Removed Previous Roles",
                value=format_role_names(roles_to_remove),
                inline=False,
            )

        channel_error = await send_embed_to_channel(
            target_guild, dept.promotion_channel_id, embed
        )
        if dept.log_channel_id is not None and dept.log_channel_id != dept.promotion_channel_id:
            await send_embed_to_channel(target_guild, dept.log_channel_id, embed)

        if previous_step is None:
            message = (
                f"Assigned {target_member.mention} their first {dept.label} rank roles in "
                f"**{target_guild.name}**: "
                f"{format_role_names(target_roles)}."
            )
        elif current_index == target_index:
            message = (
                f"Updated {target_member.mention}'s {dept.label} rank roles in "
                f"**{target_guild.name}** to "
                f"{format_role_names(target_roles)}."
            )
        else:
            message = (
                f"Promoted {target_member.mention} in {dept.label} on **{target_guild.name}** from "
                f"{format_role_names(previous_roles)} to {format_role_names(target_roles)}."
            )
        if roles_to_remove:
            message += f"\nRemoved previous roles: {format_role_names(roles_to_remove)}."
        if channel_error is not None:
            message += f"\nPromotion channel notice: {channel_error}"

        await interaction.edit_original_response(content=message)

    @dep_group.command(name="demote", description="Demote a member by one department rank.")
    @app_commands.describe(
        member="Member to demote",
        department="Department name",
        reason="Reason for the demotion",
    )
    @app_commands.autocomplete(department=autocomplete_department)
    async def dep_demote(
        interaction: discord.Interaction,
        member: discord.User,
        department: str,
        reason: str,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_roles"):
            return

        resolved = await resolve_department_for_interaction(bot, interaction, department)
        if resolved is None:
            return
        dept, target_guild = resolved

        target_member = await resolve_department_member(bot, interaction, target_guild, member)
        if target_member is None:
            return

        if not bot.can_bot_moderate(target_member):
            await bot.send_ephemeral(
                interaction,
                f"I cannot manage {target_member.mention} because of role hierarchy or missing permissions.",
            )
            return

        current_index = get_member_rank_index(target_member, dept)
        if current_index is None:
            await bot.send_ephemeral(
                interaction,
                f"{target_member.mention} does not have a configured {dept.label} rank role.",
            )
            return

        if current_index == 0:
            await bot.send_ephemeral(
                interaction,
                f"{target_member.mention} is already at the lowest configured {dept.label} rank.",
            )
            return

        current_step = dept.promotion_steps[current_index]
        current_roles, missing_current_role_ids = resolve_step_roles(target_guild, current_step)
        if missing_current_role_ids:
            await bot.send_ephemeral(
                interaction,
                f"{dept.label} is missing current rank role IDs: {format_role_ids(missing_current_role_ids)}.",
            )
            return

        target_step = dept.promotion_steps[current_index - 1]
        target_roles, missing_target_role_ids = resolve_step_roles(target_guild, target_step)
        if missing_target_role_ids:
            await bot.send_ephemeral(
                interaction,
                f"{dept.label} is missing the next-lower rank role IDs: {format_role_ids(missing_target_role_ids)}.",
            )
            return

        unmanageable_target_roles = collect_unmanageable_roles(target_guild, target_roles)
        if unmanageable_target_roles:
            await bot.send_ephemeral(
                interaction,
                f"I cannot assign these roles: {format_role_names(unmanageable_target_roles)}.",
            )
            return

        target_role_ids = set(target_step)
        roles_to_remove = [
            current_member_role
            for current_member_role in target_member.roles
            if current_member_role.id in dept.promotion_role_id_set
            and current_member_role.id not in target_role_ids
        ]
        roles_to_add = [role for role in target_roles if role not in target_member.roles]
        unmanageable = collect_unmanageable_roles(target_guild, roles_to_remove)
        if unmanageable:
            await bot.send_ephemeral(
                interaction,
                f"I cannot remove these promotion roles: {format_role_names(unmanageable)}.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"Department demote | {dept.label} | by {interaction.user.id} | {reason}"[:512]

        if roles_to_remove:
            await target_member.remove_roles(*roles_to_remove, reason=audit_reason)
        if roles_to_add:
            await target_member.add_roles(*roles_to_add, reason=audit_reason)

        embed = build_department_embed(
            title="Department Demotion",
            color=discord.Color.blurple(),
            department=dept,
            member=target_member,
            moderator=interaction.user,
            reason=reason,
        )
        embed.add_field(
            name="Previous Rank Roles",
            value=format_role_names(current_roles),
            inline=False,
        )
        embed.add_field(name="New Rank Roles", value=format_role_names(target_roles), inline=False)
        if roles_to_remove:
            embed.add_field(
                name="Removed Previous Roles",
                value=format_role_names(roles_to_remove),
                inline=False,
            )

        channel_error = await send_embed_to_channel(target_guild, dept.promotion_channel_id, embed)
        if dept.log_channel_id is not None and dept.log_channel_id != dept.promotion_channel_id:
            await send_embed_to_channel(target_guild, dept.log_channel_id, embed)

        message = (
            f"Demoted {target_member.mention} in {dept.label} on **{target_guild.name}** from "
            f"{format_role_names(current_roles)} to {format_role_names(target_roles)}."
        )
        if roles_to_remove:
            message += f"\nRemoved previous roles: {format_role_names(roles_to_remove)}."
        if channel_error is not None:
            message += f"\nPromotion channel notice: {channel_error}"

        await interaction.edit_original_response(content=message)

    bot.tree.add_command(dep_group)
