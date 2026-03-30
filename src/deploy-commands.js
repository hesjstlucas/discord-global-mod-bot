import { REST, Routes } from "discord.js";
import { commands } from "./commands.js";
import { getDeployConfig } from "./config.js";

async function main() {
  const { token, clientId, registerGuildId } = getDeployConfig();
  const rest = new REST({ version: "10" }).setToken(token);
  const body = commands.map((command) => command.toJSON());

  if (registerGuildId) {
    await rest.put(Routes.applicationGuildCommands(clientId, registerGuildId), {
      body,
    });
    console.log(
      `Registered ${body.length} slash command(s) to guild ${registerGuildId}.`,
    );
    return;
  }

  await rest.put(Routes.applicationCommands(clientId), { body });
  console.log(`Registered ${body.length} global slash command(s).`);
  console.log("Global command updates can take up to an hour to appear.");
}

main().catch((error) => {
  console.error("Could not deploy commands.");
  console.error(error);
  process.exit(1);
});
