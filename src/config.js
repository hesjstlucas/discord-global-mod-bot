import path from "node:path";
import process from "node:process";
import dotenv from "dotenv";

dotenv.config();

function requireEnv(name) {
  const value = process.env[name]?.trim();

  if (!value) {
    throw new Error(`${name} is required.`);
  }

  return value;
}

function splitCsv(value) {
  return new Set(
    (value ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export function getBotConfig() {
  return {
    token: requireEnv("DISCORD_TOKEN"),
    ownerUserIds: splitCsv(process.env.OWNER_USER_IDS),
    modRoleIds: splitCsv(process.env.MOD_ROLE_IDS),
    dataFilePath: path.resolve(
      process.cwd(),
      process.env.DATA_FILE_PATH?.trim() || "data/moderation-store.json",
    ),
  };
}

export function getDeployConfig() {
  return {
    token: requireEnv("DISCORD_TOKEN"),
    clientId: requireEnv("CLIENT_ID"),
    registerGuildId: process.env.REGISTER_GUILD_ID?.trim() || null,
  };
}
