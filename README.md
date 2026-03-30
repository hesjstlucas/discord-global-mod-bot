# Discord Global Mod Bot

A Discord moderation bot with slash commands for ban, kick, timeout, purge, and a cross-server `gban` system.

## Important limitation

Discord bots do **not** receive user IP addresses from the Discord API, so a real IP ban is not possible from a Discord bot alone. This project implements the practical version instead:

- `gban` stores a global ban entry by **Discord user ID**
- the bot bans that user in every server it is in
- if the bot joins a new server later, it syncs the stored global bans there too
- if a globally banned user joins a server, the bot immediately bans them again

If you control a separate website or game server, you can connect that system yourself and add your own IP logic there. Discord itself does not expose IPs to bots.

## Commands

- `/gban user reason` Ban a user across every server the bot is in and add them to the global ban list.
- `/ungban user_id reason` Remove a user from the global ban list and lift bans where possible.
- `/gbanlist` Show stored global bans.
- `/syncgbans` Re-apply all stored global bans across all servers.
- `/ban user reason` Standard single-server ban.
- `/kick user reason` Standard single-server kick.
- `/timeout user duration reason` Timeout a member. Duration format examples: `10m`, `2h`, `3d`.
- `/purge amount` Bulk delete up to 100 recent messages in the current channel.

## Access model

- A user can run a moderation command if any one of these is true:
  - their user ID is listed in `OWNER_USER_IDS`
  - they have a role listed in `MOD_ROLE_IDS`
  - they already have the matching Discord permission for that command
- Commands are intentionally left visible in Discord, and the bot enforces access at runtime. This is required so `MOD_ROLE_IDS` can grant access even when a role does not have the native Discord permission bit.
- Command permission mapping:
  - `gban`, `ungban`, `gbanlist`, `syncgbans`, `ban`: `Ban Members`
  - `kick`: `Kick Members`
  - `timeout`: `Moderate Members`
  - `purge`: `Manage Messages`

## Hosting

- The current bot is an always-on Gateway bot. That is what lets it catch member joins and immediately re-apply global bans.
- Vercel is not a good host for this version because Vercel Functions do not act as WebSocket servers, and Discord sends join events like `Guild Member Add` over the Gateway.
- Discord does support slash commands over HTTP interactions, so a reduced Vercel version is possible, but it would lose the instant re-ban-on-join behavior unless you move that logic to a different always-on service.
- For full behavior, use a host that can keep a long-running Node process alive continuously.

## Setup

1. Create a bot in the Discord Developer Portal.
2. Under bot settings, enable the **Server Members Intent**.
3. Invite the bot with these scopes:
   - `bot`
   - `applications.commands`
4. Give the bot the permissions it needs:
   - `Ban Members`
   - `Kick Members`
   - `Moderate Members`
   - `Manage Messages`
   - `Read Message History`
   - `View Channels`
5. Copy `.env.example` to `.env` and fill in your values.
6. Install dependencies:

```bash
npm install
```

7. Register slash commands:

```bash
npm run deploy
```

If `REGISTER_GUILD_ID` is set, commands register to that server for fast updates. If it is blank, commands register globally and may take a while to appear.

8. Start the bot:

```bash
npm start
```

## Files

- `src/index.js` Bot startup and event wiring
- `src/commands.js` Slash command definitions and handlers
- `src/bans.js` Cross-server global ban application helpers
- `src/store.js` JSON-backed persistence

## Notes

- Global bans are stored in `data/moderation-store.json` by default.
- The bot can only ban or unban where its role is high enough and it has the required server permissions.
- Purge uses Discord bulk delete rules, so messages older than 14 days will be skipped automatically.
- `MOD_ROLE_IDS` should contain Discord role IDs separated by commas.
