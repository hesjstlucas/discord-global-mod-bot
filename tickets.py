from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot import GlobalModBot

TICKET_QUEUE_GENERAL = "general_support"
TICKET_QUEUE_HIGHRANK = "highrank_support"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: Optional[str], *, fallback: str, limit: int = 400) -> str:
    text = (value or fallback).strip()
    if not text:
        text = fallback
    return " ".join(text.split())[:limit]


def channel_slug(value: str) -> str:
    raw = "".join(char.lower() if char.isalnum() else "-" for char in value)
    parts = [part for part in raw.split("-") if part]
    return "-".join(parts) or "ticket"


@dataclass(frozen=True)
class TicketQueueConfig:
    key: str
    label: str
    description: str
    support_role_ids: set[int]


def get_ticket_queues(bot: GlobalModBot) -> dict[str, TicketQueueConfig]:
    return {
        TICKET_QUEUE_GENERAL: TicketQueueConfig(
            key=TICKET_QUEUE_GENERAL,
            label="General Support",
            description="General questions, concerns, comments.",
            support_role_ids=set(bot.config.ticket_general_support_role_ids),
        ),
        TICKET_QUEUE_HIGHRANK: TicketQueueConfig(
            key=TICKET_QUEUE_HIGHRANK,
            label="Highrank Support",
            description="Reporting, blacklist appeals, escalated concerns.",
            support_role_ids=set(bot.config.ticket_highrank_support_role_ids),
        ),
    }


def get_ticket_queue(bot: GlobalModBot, key: str) -> Optional[TicketQueueConfig]:
    return get_ticket_queues(bot).get(key)


def all_ticket_support_role_ids(bot: GlobalModBot) -> set[int]:
    role_ids: set[int] = set()
    for queue in get_ticket_queues(bot).values():
        role_ids.update(queue.support_role_ids)
    return role_ids


def build_panel_embeds(bot: GlobalModBot) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    banner_url = bot.config.ticket_panel_banner_url
    if banner_url:
        banner = discord.Embed(color=discord.Color.green())
        banner.set_image(url=banner_url)
        embeds.append(banner)

    queues = get_ticket_queues(bot)
    info = discord.Embed(
        title=f"{bot.config.ticket_brand_name} Support",
        description=(
            f"Thank you for contacting the {bot.config.ticket_brand_name} support team.\n"
            "Please choose the support queue that fits your issue best."
        ),
        color=discord.Color.green(),
    )
    info.add_field(
        name=queues[TICKET_QUEUE_GENERAL].label,
        value=queues[TICKET_QUEUE_GENERAL].description,
        inline=True,
    )
    info.add_field(
        name=queues[TICKET_QUEUE_HIGHRANK].label,
        value=queues[TICKET_QUEUE_HIGHRANK].description,
        inline=True,
    )
    info.set_footer(text=bot.config.ticket_footer_text)
    embeds.append(info)
    return embeds


def build_ticket_embed(bot: GlobalModBot, ticket: dict) -> discord.Embed:
    queue = get_ticket_queue(bot, str(ticket.get("queue_key", TICKET_QUEUE_GENERAL)))
    queue_label = queue.label if queue is not None else "Unknown Queue"
    embed = discord.Embed(
        title=f"{bot.config.ticket_brand_name} Support",
        description=(
            f"Thank you for contacting {bot.config.ticket_brand_name} Support. "
            "A staff member will assist you as soon as possible."
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Ticket Queue", value=queue_label, inline=False)
    embed.add_field(
        name="Ticket Information",
        value=(
            f"Opened by <@{ticket['owner_id']}>\n"
            f"Ticket #{ticket.get('ticket_number', 'unknown')}\n"
            f"Channel ID: `{ticket['channel_id']}`"
        ),
        inline=False,
    )
    claimant_id = ticket.get("claimed_by_id")
    embed.add_field(
        name="Claimed By",
        value=(f"<@{claimant_id}>" if claimant_id else "Unclaimed"),
        inline=False,
    )
    image_url = bot.config.ticket_channel_image_url or bot.config.ticket_panel_banner_url
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=bot.config.ticket_footer_text)
    return embed


def build_ticket_log_embed(title: str, ticket: dict, *, reason: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Channel", value=f"<#{ticket['channel_id']}> (`{ticket['channel_id']}`)", inline=False)
    embed.add_field(name="Owner", value=f"<@{ticket['owner_id']}> (`{ticket['owner_id']}`)", inline=False)
    embed.add_field(
        name="Queue",
        value=str(ticket.get("queue_label") or ticket.get("queue_key") or "Unknown"),
        inline=False,
    )
    if ticket.get("claimed_by_id"):
        embed.add_field(
            name="Claimed By",
            value=f"<@{ticket['claimed_by_id']}> (`{ticket['claimed_by_id']}`)",
            inline=False,
        )
    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
    return embed


def build_role_mentions(role_ids: set[int]) -> Optional[str]:
    if not role_ids:
        return None
    return " ".join(f"<@&{role_id}>" for role_id in sorted(role_ids))


def get_ticket_category_id(bot: GlobalModBot, queue_key: str) -> Optional[int]:
    if queue_key == TICKET_QUEUE_GENERAL and bot.config.ticket_general_category_id is not None:
        return bot.config.ticket_general_category_id
    if queue_key == TICKET_QUEUE_HIGHRANK and bot.config.ticket_highrank_category_id is not None:
        return bot.config.ticket_highrank_category_id
    return bot.config.ticket_category_id


def ticket_channel_allow() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        use_application_commands=True,
    )


def ticket_channel_deny() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=False,
        send_messages=False,
        read_message_history=False,
    )


async def send_ticket_log(bot: GlobalModBot, embed: discord.Embed) -> Optional[str]:
    return await bot.send_embed_to_channel_id(bot.config.ticket_log_channel_id, embed)


async def get_ticket_text_channel(
    bot: GlobalModBot, channel_id: int
) -> Optional[discord.TextChannel]:
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    if channel is not None:
        return None
    try:
        fetched = await bot.fetch_channel(channel_id)
    except Exception:
        return None
    return fetched if isinstance(fetched, discord.TextChannel) else None


async def update_ticket_control_message(bot: GlobalModBot, channel: discord.TextChannel, ticket: dict) -> None:
    message_id = ticket.get("control_message_id")
    if not message_id:
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        return

    await message.edit(
        embed=build_ticket_embed(bot, ticket),
        view=TicketControlView(bot, channel.id),
    )


async def sync_ticket_channel_permissions(
    bot: GlobalModBot,
    channel: discord.TextChannel,
    ticket: dict,
) -> None:
    guild = channel.guild
    active_queue = get_ticket_queue(bot, str(ticket.get("queue_key", TICKET_QUEUE_GENERAL)))
    active_role_ids = set(active_queue.support_role_ids if active_queue is not None else set())
    all_role_ids = all_ticket_support_role_ids(bot)

    for role_id in all_role_ids:
        role = guild.get_role(role_id)
        if role is None:
            continue
        overwrite = ticket_channel_allow() if role_id in active_role_ids else ticket_channel_deny()
        await channel.set_permissions(role, overwrite=overwrite)

    claimant_id = ticket.get("claimed_by_id")
    for member_id_text in {
        claimant_id,
        ticket.get("previous_claimed_by_id"),
    }:
        if not member_id_text:
            continue
        member = await bot.get_member_if_present(guild, int(member_id_text))
        if member is None:
            continue
        if str(member.id) == str(claimant_id):
            await channel.set_permissions(member, overwrite=ticket_channel_allow())
        else:
            await channel.set_permissions(member, overwrite=None)


async def close_ticket_channel(
    bot: GlobalModBot,
    channel: discord.TextChannel,
    ticket: dict,
    *,
    closer: discord.abc.User,
    reason: str,
) -> Optional[str]:
    queue = get_ticket_queue(bot, str(ticket.get("queue_key", TICKET_QUEUE_GENERAL)))
    ticket["queue_label"] = queue.label if queue is not None else ticket.get("queue_label", "Unknown")
    log_embed = build_ticket_log_embed("Ticket Closed", ticket, reason=reason)
    log_embed.add_field(name="Closed By", value=f"{closer.mention} (`{closer.id}`)", inline=False)
    log_notice = await send_ticket_log(bot, log_embed)

    try:
        await channel.send(
            f"Closing this ticket in 5 seconds.\nReason: {reason[:400]}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass

    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer.id} | {reason}"[:512])
    except Exception as error:
        error_text = str(error)
        if log_notice:
            return f"{log_notice} | Channel delete failed: {error_text}"
        return error_text

    bot.store.remove_ticket_requests_for_channel(channel.id)
    bot.store.remove_ticket(channel.id)
    return log_notice


async def create_ticket_for_user(
    bot: GlobalModBot,
    interaction: discord.Interaction,
    queue_key: str,
) -> tuple[Optional[discord.TextChannel], Optional[str]]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return None, "Tickets can only be opened from a server."

    configured_guild_id = bot.get_ticket_guild_id() or interaction.guild.id
    if interaction.guild.id != configured_guild_id:
        return None, "Tickets can only be opened in the configured ticket server."

    existing = bot.store.find_open_ticket_by_owner(interaction.guild.id, interaction.user.id)
    if existing is not None:
        existing_channel = await get_ticket_text_channel(bot, int(existing["channel_id"]))
        if existing_channel is not None:
            return existing_channel, None
        bot.store.remove_ticket(int(existing["channel_id"]))

    queue = get_ticket_queue(bot, queue_key)
    if queue is None:
        return None, "That ticket queue is not configured."

    ticket_number = bot.store.next_ticket_number()
    guild = interaction.guild
    channel_name = f"{channel_slug(queue.label)}-{ticket_number:04d}"

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: ticket_channel_deny(),
        interaction.user: ticket_channel_allow(),
    }

    bot_member = guild.me
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_channels=True,
            manage_messages=True,
            use_application_commands=True,
        )

    for role_id in all_ticket_support_role_ids(bot):
        role = guild.get_role(role_id)
        if role is None:
            continue
        overwrites[role] = ticket_channel_allow() if role_id in queue.support_role_ids else ticket_channel_deny()

    category = None
    category_id = get_ticket_category_id(bot, queue.key)
    if category_id is not None:
        maybe_category = guild.get_channel(category_id)
        if maybe_category is None:
            try:
                maybe_category = await guild.fetch_channel(category_id)
            except Exception:
                maybe_category = None
        if isinstance(maybe_category, discord.CategoryChannel):
            category = maybe_category

    try:
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user.id} in {queue.label}"[:512],
        )
    except Exception as error:
        return None, str(error)

    ticket_entry = {
        "channel_id": str(channel.id),
        "guild_id": str(guild.id),
        "owner_id": str(interaction.user.id),
        "owner_tag": str(interaction.user),
        "ticket_number": ticket_number,
        "queue_key": queue.key,
        "queue_label": queue.label,
        "claimed_by_id": None,
        "claimed_by_tag": None,
        "created_at": utc_now_iso(),
        "status": "open",
        "control_message_id": None,
    }
    bot.store.set_ticket(channel.id, ticket_entry)
    control_view = TicketControlView(bot, channel.id)
    bot.add_view(control_view)

    ping_content = build_role_mentions(queue.support_role_ids)
    try:
        control_message = await channel.send(
            content=ping_content,
            embed=build_ticket_embed(bot, ticket_entry),
            view=control_view,
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
    except Exception:
        control_message = None

    if control_message is not None:
        updated = bot.store.update_ticket(channel.id, control_message_id=str(control_message.id))
        if updated is not None:
            ticket_entry = updated

    log_embed = build_ticket_log_embed("Ticket Opened", ticket_entry)
    log_notice = await send_ticket_log(bot, log_embed)
    if log_notice is not None:
        try:
            await channel.send(
                f"Ticket log notice: {log_notice}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    return channel, None


async def resolve_ticket_for_interaction(
    bot: GlobalModBot,
    interaction: discord.Interaction,
) -> tuple[Optional[dict], Optional[discord.TextChannel]]:
    if interaction.guild is None or interaction.channel is None:
        await bot.send_ephemeral(interaction, "This command can only be used in a ticket channel.")
        return None, None

    if not isinstance(interaction.channel, discord.TextChannel):
        await bot.send_ephemeral(interaction, "This command can only be used in a text ticket channel.")
        return None, None

    ticket = bot.store.get_ticket(interaction.channel.id)
    if ticket is None or ticket.get("status", "open") != "open":
        await bot.send_ephemeral(interaction, "This channel is not an open ticket.")
        return None, None

    return ticket, interaction.channel


class TicketCloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why is this ticket being closed?",
        required=True,
        max_length=400,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, bot: GlobalModBot, channel_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ticket = self.bot.store.get_ticket(self.channel_id)
        channel = await get_ticket_text_channel(self.bot, self.channel_id)
        if ticket is None or channel is None:
            await interaction.response.send_message("That ticket is no longer open.", ephemeral=True)
            return

        reason_text = normalize_text(self.reason.value, fallback="No reason provided")
        await interaction.response.send_message(
            f"Closing ticket <#{self.channel_id}>.",
            ephemeral=True,
        )
        notice = await close_ticket_channel(
            self.bot,
            channel,
            ticket,
            closer=interaction.user,
            reason=reason_text,
        )
        if notice:
            try:
                await interaction.followup.send(
                    f"Close log notice: {notice}",
                    ephemeral=True,
                )
            except Exception:
                pass


class TicketPanelSelect(discord.ui.Select):
    def __init__(self, bot: GlobalModBot) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=queue.label,
                description=queue.description[:100],
                value=queue.key,
            )
            for queue in get_ticket_queues(bot).values()
        ]
        super().__init__(
            placeholder="Choose a support queue",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket:panel:queue",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        queue_key = self.values[0]
        channel, error = await create_ticket_for_user(self.bot, interaction, queue_key)
        if error is not None:
            await interaction.edit_original_response(content=f"Could not open a ticket: {error}")
            return
        assert channel is not None
        await interaction.edit_original_response(content=f"Your ticket is ready: {channel.mention}")


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: GlobalModBot) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect(bot))


class TicketControlView(discord.ui.View):
    def __init__(self, bot: GlobalModBot, channel_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id

        claim = discord.ui.Button(
            label="Claim Ticket",
            style=discord.ButtonStyle.success,
            custom_id=f"ticket:claim:{channel_id}",
        )
        unclaim = discord.ui.Button(
            label="Unclaim Ticket",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket:unclaim:{channel_id}",
        )
        close = discord.ui.Button(
            label="Close Ticket",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticket:close:{channel_id}",
        )
        claim.callback = self.claim_callback
        unclaim.callback = self.unclaim_callback
        close.callback = self.close_callback
        self.add_item(claim)
        self.add_item(unclaim)
        self.add_item(close)

    async def claim_callback(self, interaction: discord.Interaction) -> None:
        ticket = self.bot.store.get_ticket(self.channel_id)
        channel = await get_ticket_text_channel(self.bot, self.channel_id)
        if ticket is None or channel is None:
            await interaction.response.send_message("That ticket is no longer open.", ephemeral=True)
            return

        previous_claimant_id = ticket.get("claimed_by_id")
        updated = self.bot.store.update_ticket(
            self.channel_id,
            claimed_by_id=str(interaction.user.id),
            claimed_by_tag=str(interaction.user),
            previous_claimed_by_id=previous_claimant_id,
        )
        assert updated is not None
        await sync_ticket_channel_permissions(self.bot, channel, updated)
        updated.pop("previous_claimed_by_id", None)
        self.bot.store.update_ticket(self.channel_id, previous_claimed_by_id=None)
        await update_ticket_control_message(self.bot, channel, updated)
        await interaction.response.send_message(
            f"{interaction.user.mention} has claimed this ticket.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        log_embed = build_ticket_log_embed("Ticket Claimed", updated)
        log_embed.add_field(
            name="Claimed By",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=False,
        )
        await send_ticket_log(self.bot, log_embed)

    async def unclaim_callback(self, interaction: discord.Interaction) -> None:
        ticket = self.bot.store.get_ticket(self.channel_id)
        channel = await get_ticket_text_channel(self.bot, self.channel_id)
        if ticket is None or channel is None:
            await interaction.response.send_message("That ticket is no longer open.", ephemeral=True)
            return

        previous_claimant_id = ticket.get("claimed_by_id")
        if not previous_claimant_id:
            await interaction.response.send_message(
                "This ticket is not currently claimed.",
                ephemeral=True,
            )
            return
        if str(interaction.user.id) != str(previous_claimant_id):
            await interaction.response.send_message(
                "Only the current claimant can unclaim this ticket.",
                ephemeral=True,
            )
            return

        updated = self.bot.store.update_ticket(
            self.channel_id,
            claimed_by_id=None,
            claimed_by_tag=None,
            previous_claimed_by_id=previous_claimant_id,
        )
        assert updated is not None
        await sync_ticket_channel_permissions(self.bot, channel, updated)
        updated.pop("previous_claimed_by_id", None)
        self.bot.store.update_ticket(self.channel_id, previous_claimed_by_id=None)
        await update_ticket_control_message(self.bot, channel, updated)
        await interaction.response.send_message(
            f"{interaction.user.mention} has unclaimed this ticket.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        log_embed = build_ticket_log_embed("Ticket Unclaimed", updated)
        log_embed.add_field(
            name="Unclaimed By",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=False,
        )
        await send_ticket_log(self.bot, log_embed)

    async def close_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketCloseReasonModal(self.bot, self.channel_id))


class TicketRequestPromptView(discord.ui.View):
    def __init__(self, bot: GlobalModBot, prompt_id: str, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.prompt_id = prompt_id

        accept = discord.ui.Button(
            label="Accept",
            style=discord.ButtonStyle.success,
            custom_id=f"ticket:request:accept:{prompt_id}",
            disabled=disabled,
        )
        deny = discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticket:request:deny:{prompt_id}",
            disabled=disabled,
        )
        accept.callback = self.accept_callback
        deny.callback = self.deny_callback
        self.add_item(accept)
        self.add_item(deny)

    async def accept_callback(self, interaction: discord.Interaction) -> None:
        await self.handle_response(interaction, accepted=True)

    async def deny_callback(self, interaction: discord.Interaction) -> None:
        await self.handle_response(interaction, accepted=False)

    async def handle_response(self, interaction: discord.Interaction, *, accepted: bool) -> None:
        prompt = self.bot.store.get_ticket_request_prompt(self.prompt_id)
        if prompt is None:
            await interaction.response.send_message("That ticket request prompt is no longer active.", ephemeral=True)
            return

        if str(interaction.user.id) != str(prompt.get("owner_id")):
            await interaction.response.send_message(
                "Only the ticket opener can answer that prompt.",
                ephemeral=True,
            )
            return

        channel_id = int(prompt["channel_id"])
        ticket = self.bot.store.get_ticket(channel_id)
        channel = await get_ticket_text_channel(self.bot, channel_id)
        if ticket is None or channel is None:
            self.bot.store.remove_ticket_request_prompt(self.prompt_id)
            await interaction.response.send_message("That ticket is no longer open.", ephemeral=True)
            return

        status_text = "Still needs help" if accepted else "No longer needs help"
        embed = discord.Embed(
            title="Ticket Support Check-In",
            description=f"Asked <@{prompt['owner_id']}> if they still needed help.",
            color=discord.Color.gold() if accepted else discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Response", value=status_text, inline=False)
        embed.add_field(name="Answered By", value=f"{interaction.user.mention}", inline=False)
        disabled_view = TicketRequestPromptView(self.bot, self.prompt_id, disabled=True)
        await interaction.response.edit_message(embed=embed, view=disabled_view)
        self.bot.store.remove_ticket_request_prompt(self.prompt_id)

        if accepted:
            await channel.send(
                f"{interaction.user.mention} confirmed they still need help.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            log_embed = build_ticket_log_embed("Ticket Request Accepted", ticket)
            log_embed.add_field(name="Answered By", value=f"{interaction.user.mention}", inline=False)
            await send_ticket_log(self.bot, log_embed)
            return

        await channel.send(
            f"{interaction.user.mention} said they no longer need help. Closing the ticket.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await close_ticket_channel(
            self.bot,
            channel,
            ticket,
            closer=interaction.user,
            reason="Ticket opener said they no longer needed help after a support check-in.",
        )


def register_ticket_views(bot: GlobalModBot) -> None:
    bot.add_view(TicketPanelView(bot))
    for ticket in bot.store.list_open_tickets():
        try:
            channel_id = int(ticket["channel_id"])
        except (KeyError, TypeError, ValueError):
            continue
        bot.add_view(TicketControlView(bot, channel_id))

    for prompt in bot.store.list_active_ticket_requests():
        prompt_id = str(prompt.get("prompt_id", "")).strip()
        if not prompt_id:
            continue
        bot.add_view(TicketRequestPromptView(bot, prompt_id))


def register_ticket_commands(bot: GlobalModBot) -> None:
    ticket_group = app_commands.Group(name="ticket", description="Support ticket commands.")

    @ticket_group.command(name="panel", description="Post the support ticket panel.")
    @app_commands.guild_only()
    @app_commands.describe(channel="Channel to post the panel in")
    async def ticket_panel(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not await bot.ensure_access(interaction, "manage_messages"):
            return

        if interaction.guild is None:
            await bot.send_ephemeral(interaction, "This command can only be used in a server.")
            return

        configured_guild_id = bot.get_ticket_guild_id() or interaction.guild.id
        if interaction.guild.id != configured_guild_id:
            await bot.send_ephemeral(interaction, "Use this command in the configured ticket server.")
            return

        target_channel = channel or interaction.channel
        if target_channel is None or not hasattr(target_channel, "send"):
            await bot.send_ephemeral(interaction, "Pick a messageable text channel for the panel.")
            return

        if channel is None and bot.config.ticket_panel_channel_id is not None:
            configured_channel = interaction.guild.get_channel(bot.config.ticket_panel_channel_id)
            if configured_channel is None:
                try:
                    configured_channel = await interaction.guild.fetch_channel(
                        bot.config.ticket_panel_channel_id
                    )
                except Exception:
                    configured_channel = None
            if isinstance(configured_channel, discord.TextChannel):
                target_channel = configured_channel

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await target_channel.send(
                embeds=build_panel_embeds(bot),
                view=TicketPanelView(bot),
            )
        except Exception as error:
            await interaction.edit_original_response(content=f"Could not post the ticket panel: {error}")
            return

        await interaction.edit_original_response(content=f"Posted the ticket panel in {target_channel.mention}.")

    @ticket_group.command(name="request", description="Ask the ticket opener if they still need help.")
    @app_commands.guild_only()
    async def ticket_request(interaction: discord.Interaction) -> None:
        ticket, channel = await resolve_ticket_for_interaction(bot, interaction)
        if ticket is None or channel is None:
            return

        active_prompt = bot.store.get_active_ticket_request_for_channel(channel.id)
        if active_prompt is not None:
            await bot.send_ephemeral(interaction, "There is already an active support check-in for this ticket.")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        prompt_id = uuid4().hex[:12]
        prompt_entry = {
            "prompt_id": prompt_id,
            "channel_id": str(channel.id),
            "owner_id": str(ticket["owner_id"]),
            "requested_by_id": str(interaction.user.id),
            "created_at": utc_now_iso(),
            "status": "pending",
            "message_id": None,
        }
        bot.store.set_ticket_request_prompt(prompt_id, prompt_entry)
        embed = discord.Embed(
            title="Ticket Support Check-In",
            description=(
                f"<@{ticket['owner_id']}>, do you still need help with this ticket?\n"
                "Accept = you still need help.\n"
                "Deny = you do not need help anymore and the ticket will be closed."
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=bot.config.ticket_footer_text)
        view = TicketRequestPromptView(bot, prompt_id)
        try:
            message = await channel.send(
                content=f"<@{ticket['owner_id']}>",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except Exception as error:
            bot.store.remove_ticket_request_prompt(prompt_id)
            await interaction.edit_original_response(content=f"Could not send the support check-in: {error}")
            return

        bot.store.update_ticket_request_prompt(prompt_id, message_id=str(message.id))
        bot.add_view(view)
        await interaction.edit_original_response(content="Sent the support check-in prompt.")

    @ticket_group.command(name="close", description="Close the current ticket.")
    @app_commands.guild_only()
    async def ticket_close(interaction: discord.Interaction) -> None:
        ticket, channel = await resolve_ticket_for_interaction(bot, interaction)
        if ticket is None or channel is None:
            return

        await interaction.response.send_modal(TicketCloseReasonModal(bot, channel.id))

    @ticket_group.command(name="escalate", description="Move the current ticket to another support queue.")
    @app_commands.guild_only()
    @app_commands.describe(target="Target queue")
    @app_commands.choices(
        target=[
            app_commands.Choice(name="General Support", value=TICKET_QUEUE_GENERAL),
            app_commands.Choice(name="Highrank Support", value=TICKET_QUEUE_HIGHRANK),
        ]
    )
    async def ticket_escalate(
        interaction: discord.Interaction,
        target: app_commands.Choice[str],
    ) -> None:
        ticket, channel = await resolve_ticket_for_interaction(bot, interaction)
        if ticket is None or channel is None:
            return

        if ticket.get("queue_key") == target.value:
            await bot.send_ephemeral(interaction, f"This ticket is already in {target.name}.")
            return

        queue = get_ticket_queue(bot, target.value)
        if queue is None:
            await bot.send_ephemeral(interaction, "That support queue is not configured.")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        updated = bot.store.update_ticket(
            channel.id,
            queue_key=queue.key,
            queue_label=queue.label,
        )
        assert updated is not None
        category_id = get_ticket_category_id(bot, queue.key)
        if category_id is not None:
            maybe_category = channel.guild.get_channel(category_id)
            if maybe_category is None:
                try:
                    maybe_category = await channel.guild.fetch_channel(category_id)
                except Exception:
                    maybe_category = None
            if isinstance(maybe_category, discord.CategoryChannel) and channel.category_id != maybe_category.id:
                try:
                    await channel.edit(
                        category=maybe_category,
                        reason=f"Ticket escalated to {queue.label} by {interaction.user.id}"[:512],
                    )
                except Exception as error:
                    await interaction.edit_original_response(
                        content=f"Escalated queue, but could not move the channel category: {error}"
                    )
                    return
        await sync_ticket_channel_permissions(bot, channel, updated)
        await update_ticket_control_message(bot, channel, updated)

        ping_content = build_role_mentions(queue.support_role_ids)
        if ping_content:
            await channel.send(
                content=ping_content,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        await channel.send(
            f"{interaction.user.mention} escalated this ticket to **{queue.label}**.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        log_embed = build_ticket_log_embed("Ticket Escalated", updated)
        log_embed.add_field(name="Escalated By", value=f"{interaction.user.mention}", inline=False)
        log_embed.add_field(name="New Queue", value=queue.label, inline=False)
        log_notice = await send_ticket_log(bot, log_embed)

        response = f"Escalated this ticket to **{queue.label}**."
        if log_notice is not None:
            response += f"\nTicket log notice: {log_notice}"
        await interaction.edit_original_response(content=response)

    bot.tree.add_command(ticket_group)
