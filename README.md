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
- `/dep kick member department reason`
- `/dep infract member department action reason`
- `/dep ban member department reason`
- `/dep promote member department role reason`
- `/dep demote member department reason`
- `/dep division promote member department division role reason`
- `/dep division demote member department division role reason`
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
- If `REGISTER_GUILD_ID` or `DEPARTMENT_COMMAND_GUILD_IDS` is set, commands sync to those servers plus the configured department guilds on boot and show up quickly.
- If `REGISTER_GUILD_ID` is blank, commands sync globally and Discord can take a while to show them.

## Railway

Set these variables in Railway:

```env
DISCORD_TOKEN=your-bot-token
CLIENT_ID=your-application-client-id
REGISTER_GUILD_ID=
DEPARTMENT_COMMAND_GUILD_IDS=
OWNER_USER_IDS=
MOD_ROLE_IDS=
GLOBAL_BAN_GUILD_IDS=
GLOBAL_MESSAGE_CHANNEL_MAP=
DEPARTMENTS_CONFIG_PATH=departments.json
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
- `DEPARTMENTS_CONFIG_PATH` points to the department JSON file used by `/dep` commands.
- `DEPARTMENT_COMMAND_GUILD_IDS` can contain comma-separated main/server IDs where `/dep` commands are allowed to be run, even if the department itself belongs to another guild.

## Department Commands

- Department commands are defined in `depcmds.py`.
- I assumed each `/dep` command needs a target member, even though your shorthand omitted it.
- These commands are role-based department actions against the configured department guild, not Discord server bans.
- `/dep` can be run in the department guild itself or in any server listed in `DEPARTMENT_COMMAND_GUILD_IDS` (and `REGISTER_GUILD_ID` is also treated as an allowed command server).
- `kick` removes the configured department roles from the member.
- `ban` removes the configured department roles. If you still define an optional `ban_role_id`, the bot adds that too.
- `infract warn` and `infract strike` log the action. `infract terminate` removes configured department roles above the configured termination floor role.
- `promote` accepts one of the configured department roles, then assigns the full linked rank step for that role. If a rank step contains multiple roles, the bot gives all of them together.
- `demote` uses that same ordered `promotion_role_ids` list and moves the member down exactly one configured rank.
- `division promote` and `division demote` do the same thing for a configured division under the selected department.
- If `command_role_ids` is configured, the moderator must have every listed role in the main command server before the command will run. The bot uses `REGISTER_GUILD_ID` first, then falls back to a configured command guild, then the department guild if needed.

## Department Config

- Copy [departments.example.json](/C:/Users/heher/Documents/Playground/discord-global-mod-bot/departments.example.json) to `departments.json`, then replace the example IDs.
- Minimal fields:
  - `promotion_role_ids`: ordered from lowest rank to highest rank; each item can be a single role ID or a list of linked role IDs for one rank step. `/dep promote` and `/dep demote` move one step at a time.
  - `guild_id`: optional server restriction for that department
  - `termination_floor_role_id`: roles above this configured role are removed by `/dep infract terminate`
  - `log_channel_id`: where kick, ban, and infraction logs go
  - `promotion_channel_id`: where promotion and demotion embeds go
- Optional fields:
  - `label`: display name used in command output; if you omit it, the department key is used
  - `command_role_ids`: optional list of role IDs the moderator must all have in the main command server to run that department's `/dep` commands
  - `member_role_ids`: extra membership roles to remove during kick, ban, or terminate
  - `managed_role_ids`: extra department roles to remove during kick, ban, or terminate
  - `ban_role_id`: optional department blacklist role added by `/dep ban`
- Division fields:
  - `divisions`: optional object keyed by division name
  - each division can define `label`, `command_role_ids`, `promotion_role_ids`, `log_channel_id`, and `promotion_channel_id`
  - if a division omits channel IDs, it falls back to the parent department channels
- In `/dep promote`, choose the department first. The `role` field then autocompletes only the configured roles for that department. Picking any role from a linked bundle assigns the whole bundle.
- In `/dep division promote` and `/dep division demote`, choose the department first, then the division. The `role` field autocompletes only the configured roles for that division.
