function summarizeError(error) {
  return error?.rawError?.message || error?.message || String(error);
}

function isUnknownBanError(error) {
  return error?.code === 10026 || `${error?.message || ""}`.includes("Unknown Ban");
}

export function buildGlobalBanReason(entry) {
  const reason = entry.reason || "No reason provided";
  return `Global ban | by ${entry.moderatorId} | ${entry.createdAt} | ${reason}`.slice(0, 512);
}

export async function applyGlobalBanToGuild(guild, userId, entry) {
  try {
    await guild.members.ban(userId, { reason: buildGlobalBanReason(entry) });

    return {
      guildId: guild.id,
      guildName: guild.name,
      status: "banned",
    };
  } catch (error) {
    return {
      guildId: guild.id,
      guildName: guild.name,
      status: "failed",
      reason: summarizeError(error),
    };
  }
}

export async function liftGlobalBanFromGuild(guild, userId, reason) {
  try {
    let existingBan = null;

    try {
      existingBan = await guild.bans.fetch(userId);
    } catch (error) {
      if (!isUnknownBanError(error)) {
        throw error;
      }
    }

    if (!existingBan) {
      return {
        guildId: guild.id,
        guildName: guild.name,
        status: "skipped",
      };
    }

    await guild.bans.remove(userId, reason);

    return {
      guildId: guild.id,
      guildName: guild.name,
      status: "unbanned",
    };
  } catch (error) {
    return {
      guildId: guild.id,
      guildName: guild.name,
      status: "failed",
      reason: summarizeError(error),
    };
  }
}

export async function applyGlobalBanEverywhere(client, userId, entry) {
  const results = [];

  for (const guild of client.guilds.cache.values()) {
    results.push(await applyGlobalBanToGuild(guild, userId, entry));
  }

  return results;
}

export async function liftGlobalBanEverywhere(client, userId, reason) {
  const results = [];

  for (const guild of client.guilds.cache.values()) {
    results.push(await liftGlobalBanFromGuild(guild, userId, reason));
  }

  return results;
}

export async function syncGlobalBansToGuild(guild, globalBans) {
  const results = [];

  for (const entry of globalBans) {
    results.push(await applyGlobalBanToGuild(guild, entry.userId, entry));
  }

  return results;
}

export async function syncGlobalBansEverywhere(client, globalBans) {
  const results = [];

  for (const guild of client.guilds.cache.values()) {
    results.push(...(await syncGlobalBansToGuild(guild, globalBans)));
  }

  return results;
}
