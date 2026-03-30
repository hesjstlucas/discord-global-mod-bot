import {
  MessageFlags,
  PermissionFlagsBits,
  SlashCommandBuilder,
} from "discord.js";
import {
  applyGlobalBanEverywhere,
  liftGlobalBanEverywhere,
  syncGlobalBansEverywhere,
} from "./bans.js";

const DURATION_MULTIPLIERS = {
  s: 1_000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
};

function normalizeReason(value) {
  return (value?.trim() || "No reason provided").replace(/\s+/g, " ").slice(0, 400);
}

function parseDuration(input) {
  const match = /^(\d+)([smhdw])$/i.exec(input.trim());

  if (!match) {
    return null;
  }

  const [, amount, unit] = match;
  return Number(amount) * DURATION_MULTIPLIERS[unit.toLowerCase()];
}

function formatDuration(ms) {
  const units = [
    ["w", DURATION_MULTIPLIERS.w],
    ["d", DURATION_MULTIPLIERS.d],
    ["h", DURATION_MULTIPLIERS.h],
    ["m", DURATION_MULTIPLIERS.m],
    ["s", DURATION_MULTIPLIERS.s],
  ];

  for (const [label, multiplier] of units) {
    if (ms % multiplier === 0) {
      return `${ms / multiplier}${label}`;
    }
  }

  return `${ms}ms`;
}

function summarizeResults(results) {
  const succeeded = results.filter((result) =>
    ["banned", "unbanned"].includes(result.status),
  );
  const skipped = results.filter((result) => result.status === "skipped");
  const failures = results.filter((result) => result.status === "failed");

  const lines = [`Succeeded in ${succeeded.length} server(s).`];

  if (skipped.length > 0) {
    lines.push(`Skipped in ${skipped.length} server(s).`);
  }

  if (failures.length > 0) {
    const preview = failures
      .slice(0, 3)
      .map((failure) => `${failure.guildName}: ${failure.reason}`)
      .join(" | ");

    lines.push(
      `Failed in ${failures.length} server(s)${preview ? `: ${preview}` : "."}`,
    );
  }

  return lines.join("\n");
}

function getMemberRoleIds(interaction) {
  const roles = interaction.member?.roles;

  if (!roles) {
    return new Set();
  }

  if (Array.isArray(roles)) {
    return new Set(roles);
  }

  if (roles.cache) {
    return new Set(roles.cache.keys());
  }

  return new Set();
}

function hasAllowedRole(interaction, modRoleIds) {
  if (modRoleIds.size === 0) {
    return false;
  }

  const memberRoleIds = getMemberRoleIds(interaction);
  return [...modRoleIds].some((roleId) => memberRoleIds.has(roleId));
}

function permissionLabel(permission) {
  switch (permission) {
    case PermissionFlagsBits.BanMembers:
      return "Ban Members";
    case PermissionFlagsBits.KickMembers:
      return "Kick Members";
    case PermissionFlagsBits.ModerateMembers:
      return "Moderate Members";
    case PermissionFlagsBits.ManageMessages:
      return "Manage Messages";
    default:
      return "the required moderation permission";
  }
}

function hasCommandAccess(interaction, permission, config) {
  if (!interaction.inGuild()) {
    return false;
  }

  if (config.ownerUserIds.has(interaction.user.id)) {
    return true;
  }

  if (hasAllowedRole(interaction, config.modRoleIds)) {
    return true;
  }

  return interaction.memberPermissions?.has(permission) ?? false;
}

async function denyCommandAccess(interaction, permission, config) {
  const requirements = [
    `the **${permissionLabel(permission)}** permission`,
  ];

  if (config.modRoleIds.size > 0) {
    requirements.push("a role listed in `MOD_ROLE_IDS`");
  }

  if (config.ownerUserIds.size > 0) {
    requirements.push("a user ID listed in `OWNER_USER_IDS`");
  }

  const message = `You need ${requirements.join(", or ")} to use this command.`;

  if (interaction.deferred || interaction.replied) {
    await interaction.followUp({ content: message, flags: MessageFlags.Ephemeral });
    return;
  }

  await interaction.reply({ content: message, flags: MessageFlags.Ephemeral });
}

async function deferPrivate(interaction) {
  if (!interaction.deferred && !interaction.replied) {
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  }
}

function formatBanList(entries) {
  if (entries.length === 0) {
    return "No global bans are stored yet.";
  }

  const preview = entries
    .slice(0, 20)
    .map((entry, index) => {
      const shortReason = (entry.reason || "No reason provided").slice(0, 70);
      return `${index + 1}. ${entry.userId} | ${entry.createdAt} | ${shortReason}`;
    })
    .join("\n");

  const suffix =
    entries.length > 20 ? `\n...and ${entries.length - 20} more.` : "";

  return `Stored global bans: ${entries.length}\n\`\`\`\n${preview}${suffix}\n\`\`\``;
}

function ensureGuildContext(interaction) {
  return interaction.inGuild() && interaction.guild;
}

async function handleGlobalBan(interaction, context) {
  if (!hasCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const user = interaction.options.getUser("user", true);
  const reason = normalizeReason(interaction.options.getString("reason"));
  const alreadyBanned = context.store.getGlobalBan(user.id);
  const entry = {
    reason,
    moderatorId: interaction.user.id,
    moderatorTag: interaction.user.tag,
    createdAt: new Date().toISOString(),
  };

  await context.store.setGlobalBan(user.id, entry);
  const results = await applyGlobalBanEverywhere(context.client, user.id, entry);

  await interaction.editReply({
    content: [
      alreadyBanned
        ? `Updated the global ban for <@${user.id}>.`
        : `Added <@${user.id}> to the global ban list.`,
      summarizeResults(results),
    ].join("\n\n"),
  });
}

async function handleGlobalUnban(interaction, context) {
  if (!hasCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const userId = interaction.options.getString("user_id", true).trim();
  const reason = normalizeReason(interaction.options.getString("reason"));
  const removedEntry = await context.store.removeGlobalBan(userId);

  if (!removedEntry) {
    await interaction.editReply({
      content: `User ID \`${userId}\` is not in the global ban list.`,
    });
    return;
  }

  const results = await liftGlobalBanEverywhere(
    context.client,
    userId,
    `Global unban by ${interaction.user.id} | ${reason}`.slice(0, 512),
  );

  await interaction.editReply({
    content: [
      `Removed \`${userId}\` from the global ban list.`,
      summarizeResults(results),
    ].join("\n\n"),
  });
}

async function handleGlobalBanList(interaction, context) {
  if (!hasCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config);
    return;
  }

  await interaction.reply({
    content: formatBanList(context.store.listGlobalBans()),
    flags: MessageFlags.Ephemeral,
  });
}

async function handleGlobalSync(interaction, context) {
  if (!hasCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const entries = context.store.listGlobalBans();

  if (entries.length === 0) {
    await interaction.editReply({ content: "No stored global bans to sync." });
    return;
  }

  const results = await syncGlobalBansEverywhere(context.client, entries);

  await interaction.editReply({
    content: [
      `Re-applied ${entries.length} stored global ban(s).`,
      summarizeResults(results),
    ].join("\n\n"),
  });
}

async function handleLocalBan(interaction, context) {
  if (!ensureGuildContext(interaction)) {
    await interaction.reply({
      content: "This command can only be used in a server.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  if (!hasCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.BanMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const user = interaction.options.getUser("user", true);
  const reason = normalizeReason(interaction.options.getString("reason"));
  const member = await interaction.guild.members.fetch(user.id).catch(() => null);

  if (member && !member.bannable) {
    await interaction.editReply({
      content: `I cannot ban <@${user.id}> because of role hierarchy or missing permissions.`,
    });
    return;
  }

  await interaction.guild.members.ban(user.id, {
    reason: `Local ban by ${interaction.user.id} | ${reason}`.slice(0, 512),
  });

  await interaction.editReply({
    content: `Banned <@${user.id}> from **${interaction.guild.name}**.`,
  });
}

async function handleKick(interaction, context) {
  if (!ensureGuildContext(interaction)) {
    await interaction.reply({
      content: "This command can only be used in a server.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  if (!hasCommandAccess(interaction, PermissionFlagsBits.KickMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.KickMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const user = interaction.options.getUser("user", true);
  const reason = normalizeReason(interaction.options.getString("reason"));
  const member = await interaction.guild.members.fetch(user.id).catch(() => null);

  if (!member) {
    await interaction.editReply({
      content: "That user is not in this server.",
    });
    return;
  }

  if (!member.kickable) {
    await interaction.editReply({
      content: `I cannot kick <@${user.id}> because of role hierarchy or missing permissions.`,
    });
    return;
  }

  await member.kick(`Kick by ${interaction.user.id} | ${reason}`.slice(0, 512));

  await interaction.editReply({
    content: `Kicked <@${user.id}> from **${interaction.guild.name}**.`,
  });
}

async function handleTimeout(interaction, context) {
  if (!ensureGuildContext(interaction)) {
    await interaction.reply({
      content: "This command can only be used in a server.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  if (!hasCommandAccess(interaction, PermissionFlagsBits.ModerateMembers, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.ModerateMembers, context.config);
    return;
  }

  await deferPrivate(interaction);

  const user = interaction.options.getUser("user", true);
  const rawDuration = interaction.options.getString("duration", true);
  const reason = normalizeReason(interaction.options.getString("reason"));
  const durationMs = parseDuration(rawDuration);

  if (!durationMs || durationMs > 28 * 24 * 60 * 60 * 1000) {
    await interaction.editReply({
      content:
        "Timeout duration must use formats like `10m`, `2h`, or `3d`, up to 28 days.",
    });
    return;
  }

  const member = await interaction.guild.members.fetch(user.id).catch(() => null);

  if (!member) {
    await interaction.editReply({
      content: "That user is not in this server.",
    });
    return;
  }

  if (!member.moderatable) {
    await interaction.editReply({
      content: `I cannot timeout <@${user.id}> because of role hierarchy or missing permissions.`,
    });
    return;
  }

  await member.timeout(
    durationMs,
    `Timeout by ${interaction.user.id} | ${reason}`.slice(0, 512),
  );

  await interaction.editReply({
    content: `Timed out <@${user.id}> for ${formatDuration(durationMs)}.`,
  });
}

async function handlePurge(interaction, context) {
  if (!ensureGuildContext(interaction)) {
    await interaction.reply({
      content: "This command can only be used in a server.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  if (!hasCommandAccess(interaction, PermissionFlagsBits.ManageMessages, context.config)) {
    await denyCommandAccess(interaction, PermissionFlagsBits.ManageMessages, context.config);
    return;
  }

  const amount = interaction.options.getInteger("amount", true);
  const channel = interaction.channel;

  if (!channel || typeof channel.bulkDelete !== "function") {
    await interaction.reply({
      content: "This channel does not support bulk deletion.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await deferPrivate(interaction);

  const deleted = await channel.bulkDelete(amount, true);

  await interaction.editReply({
    content: `Deleted ${deleted.size} message(s). Messages older than 14 days are skipped by Discord.`,
  });
}

export const commands = [
  new SlashCommandBuilder()
    .setName("gban")
    .setDescription("Globally ban a user across every server this bot is in.")
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .setDMPermission(false)
    .addUserOption((option) =>
      option.setName("user").setDescription("User to globally ban").setRequired(true),
    )
    .addStringOption((option) =>
      option.setName("reason").setDescription("Reason for the global ban").setRequired(false),
    ),
  new SlashCommandBuilder()
    .setName("ungban")
    .setDescription("Remove a user ID from the global ban list and unban them.")
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .setDMPermission(false)
    .addStringOption((option) =>
      option
        .setName("user_id")
        .setDescription("Discord user ID to globally unban")
        .setRequired(true),
    )
    .addStringOption((option) =>
      option
        .setName("reason")
        .setDescription("Reason for removing the global ban")
        .setRequired(false),
    ),
  new SlashCommandBuilder()
    .setName("gbanlist")
    .setDescription("Show stored global bans.")
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .setDMPermission(false),
  new SlashCommandBuilder()
    .setName("syncgbans")
    .setDescription("Re-apply all stored global bans to all servers.")
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .setDMPermission(false),
  new SlashCommandBuilder()
    .setName("ban")
    .setDescription("Ban a user from this server.")
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .setDMPermission(false)
    .addUserOption((option) =>
      option.setName("user").setDescription("User to ban").setRequired(true),
    )
    .addStringOption((option) =>
      option.setName("reason").setDescription("Reason for the ban").setRequired(false),
    ),
  new SlashCommandBuilder()
    .setName("kick")
    .setDescription("Kick a user from this server.")
    .setDefaultMemberPermissions(PermissionFlagsBits.KickMembers)
    .setDMPermission(false)
    .addUserOption((option) =>
      option.setName("user").setDescription("User to kick").setRequired(true),
    )
    .addStringOption((option) =>
      option.setName("reason").setDescription("Reason for the kick").setRequired(false),
    ),
  new SlashCommandBuilder()
    .setName("timeout")
    .setDescription("Timeout a user in this server.")
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .setDMPermission(false)
    .addUserOption((option) =>
      option.setName("user").setDescription("User to timeout").setRequired(true),
    )
    .addStringOption((option) =>
      option
        .setName("duration")
        .setDescription("Duration like 10m, 2h, or 3d")
        .setRequired(true),
    )
    .addStringOption((option) =>
      option.setName("reason").setDescription("Reason for the timeout").setRequired(false),
    ),
  new SlashCommandBuilder()
    .setName("purge")
    .setDescription("Bulk delete recent messages in this channel.")
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages)
    .setDMPermission(false)
    .addIntegerOption((option) =>
      option
        .setName("amount")
        .setDescription("How many messages to delete")
        .setMinValue(1)
        .setMaxValue(100)
        .setRequired(true),
    ),
];

export async function handleCommand(interaction, context) {
  switch (interaction.commandName) {
    case "gban":
      await handleGlobalBan(interaction, context);
      return;
    case "ungban":
      await handleGlobalUnban(interaction, context);
      return;
    case "gbanlist":
      await handleGlobalBanList(interaction, context);
      return;
    case "syncgbans":
      await handleGlobalSync(interaction, context);
      return;
    case "ban":
      await handleLocalBan(interaction, context);
      return;
    case "kick":
      await handleKick(interaction, context);
      return;
    case "timeout":
      await handleTimeout(interaction, context);
      return;
    case "purge":
      await handlePurge(interaction, context);
      return;
    default:
      await interaction.reply({
        content: "Unknown command.",
        flags: MessageFlags.Ephemeral,
      });
  }
}
