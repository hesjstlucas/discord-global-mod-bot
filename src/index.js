import process from "node:process";
import { Client, Events, GatewayIntentBits, MessageFlags } from "discord.js";
import { buildGlobalBanReason, syncGlobalBansToGuild } from "./bans.js";
import { handleCommand } from "./commands.js";
import { getBotConfig } from "./config.js";
import { ModerationStore } from "./store.js";

async function main() {
  const config = getBotConfig();
  const store = new ModerationStore(config.dataFilePath);
  await store.init();

  const client = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMembers,
      GatewayIntentBits.GuildMessages,
    ],
  });

  client.once(Events.ClientReady, (readyClient) => {
    console.log(
      `Logged in as ${readyClient.user.tag}. Connected to ${readyClient.guilds.cache.size} guild(s).`,
    );
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) {
      return;
    }

    try {
      await handleCommand(interaction, { client, store, config });
    } catch (error) {
      console.error(`Command ${interaction.commandName} failed.`);
      console.error(error);

      const payload = {
        content: "The command failed. Check the bot logs for details.",
        flags: MessageFlags.Ephemeral,
      };

      if (interaction.deferred) {
        await interaction.editReply(payload).catch(() => {});
        return;
      }

      if (interaction.replied) {
        await interaction.followUp(payload).catch(() => {});
        return;
      }

      await interaction.reply(payload).catch(() => {});
    }
  });

  client.on(Events.GuildMemberAdd, async (member) => {
    const entry = store.getGlobalBan(member.id);

    if (!entry) {
      return;
    }

    try {
      await member.ban({ reason: buildGlobalBanReason(entry) });
      console.log(`Re-applied global ban for ${member.user.tag} in ${member.guild.name}.`);
    } catch (error) {
      console.error(
        `Could not re-apply global ban for ${member.user.tag} in ${member.guild.name}.`,
      );
      console.error(error);
    }
  });

  client.on(Events.GuildCreate, async (guild) => {
    const globalBans = store.listGlobalBans();

    if (globalBans.length === 0) {
      return;
    }

    const results = await syncGlobalBansToGuild(guild, globalBans);
    const failures = results.filter((result) => result.status === "failed");

    console.log(
      `Synced ${globalBans.length} global ban(s) to ${guild.name}. Failures: ${failures.length}.`,
    );
  });

  await client.login(config.token);
}

main().catch((error) => {
  console.error("Bot startup failed.");
  console.error(error);
  process.exit(1);
});
