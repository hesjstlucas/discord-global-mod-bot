# Discord Global Mod Bot

A Python Discord moderation bot with slash commands for ban, kick, timeout, purge, and a cross-server `gban` system.

## Important limitation

Discord bots do **not** receive user IP addresses from the Discord API, so a real IP ban is not possible from a Discord bot alone. This project implements the practical version instead:

- `gban` stores a global ban entry by **Discord user ID**
- the bot bans that user in every server it is in
- if the bot joins a new server later, it syncs the stored global bans there too
- if a globally banned user joins a server, the bot immediately bans them again

## Commands

- `/gban user reason`
- `/ungban user_id reason`
- `/gbanlist`
- `/syncgbans`
- `/globalmessage message`
- `/ban user reason`
- `/kick user reason`
- `/timeout user duration reason`
- `/purge amount`

## Access model

- A user can run a moderation command if any one of these is true:
  - their user ID is listed in `OWNER_USER_IDS`
  - they have a role listed in `MOD_ROLE_IDS`
  - they already have the matching Discord permission for that command
- Commands stay visible in Discord and the bot enforces access at runtime.
- Permission mapping:
  - `gban`, `ungban`, `gbanlist`, `syncgbans`, `ban`: `Ban Members`
  - `globalmessage`, `purge`: `Manage Messages`
  - `kick`: `Kick Members`
  - `timeout`: `Moderate Members`

## Setup

1. Create a bot in the Discord Developer Portal.
2. In `Bot`, enable `Server Members Intent`.
3. Turn off `Requires OAuth2 Code Grant`.
4. In `Installation`, enable these scopes:
   - `bot`
   - `applications.commands`
5. Give the bot these permissions:
   - `Ban Members`
   - `Kick Members`
   - `Moderate Members`
   - `Manage Messages`
   - `Read Message History`
   - `View Channels`
6. Copy `.env.example` to `.env` and fill in your values.
7. Install dependencies:

```bash
py -m pip install -r requirements.txt
```

8. Start the bot:

```bash
py bot.py
```

## Command syncing

- This Python version syncs slash commands automatically when the bot starts.
- If `REGISTER_GUILD_ID` is set, commands sync to that server on boot and show up quickly.
- If `REGISTER_GUILD_ID` is blank, commands sync globally and Discord can take a while to show them.

## Railway

Set these variables in Railway:

```env
DISCORD_TOKEN=your-bot-token
CLIENT_ID=your-application-client-id
REGISTER_GUILD_ID=
OWNER_USER_IDS=
MOD_ROLE_IDS=
GLOBAL_BAN_GUILD_IDS=
GLOBAL_MESSAGE_CHANNEL_MAP=
DATA_FILE_PATH=/app/data/moderation-store.json
```

Then:

1. Attach a Railway volume mounted at `/app/data`
2. Set the start command to `python bot.py`
3. Deploy

## Files

- `bot.py` Bot runtime, slash commands, and global ban logic
- `requirements.txt` Python dependencies

## Notes

- Global bans are stored in `data/moderation-store.json` by default.
- The bot can only ban, unban, kick, or timeout where its role is high enough.
- Timeout duration format examples: `10m`, `2h`, `3d`.
- Purge skips messages older than 14 days because Discord bulk delete does.
- `GLOBAL_BAN_GUILD_IDS` can contain comma-separated server IDs. If set, global ban commands only apply to those servers.
- `GLOBAL_MESSAGE_CHANNEL_MAP` uses `guild_id:channel_id,guild_id:channel_id`. `/globalmessage` sends to those channels in the targeted guilds.
