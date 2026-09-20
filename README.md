# atium-recipes

Sync recipes from URLs, Instagram, and other sources into a Notion database.

## Running the bot

1. Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN`,
   `TELEGRAM_ALLOWED_USER_IDS` (a comma-separated list of Telegram user
   IDs), `TELEGRAM_OWNER_ID` (your own ID, which must also be in that
   list), `NOTION_CLIENT_ID`/`NOTION_CLIENT_SECRET`/`NOTION_REDIRECT_URI`
   (from a Notion public integration, install scope "Selected workspaces
   only"), and the model provider's API key.
2. Start the bot: `docker compose up -d --build`.
3. Each allowed friend messages the bot and taps **Connect Notion**. The bot
   creates a Recipes and an Ingredients database under whatever page they
   share, and confirms in Telegram once it is done.
4. The confirmation asks which language to write in, English or Greek. The
   choice covers both the recipes and the bot's own replies, and `/language`
   changes it later. Until the choice is made the bot follows the Telegram
   client's own language. Cuisine, Meal, Difficulty and Category stay in
   English whatever the choice, because they are the Notion databases' select
   options.

Share a link the bot already saved and it offers to reimport that page:
**Refetch link** reads the site again, **Reuse saved text** runs the
extraction over the text stored on the page, and **Keep** changes nothing.
A reimport rewrites the page in place, so its rating and its created time
survive and its body is replaced.

The bot uses Telegram long polling, so it opens no inbound port for
Telegram traffic. It does open one local port for the Notion OAuth
callback, proxied over HTTPS by the VPS's shared reverse proxy; see
[.claude/skills/vps-connection/SKILL.md](.claude/skills/vps-connection/SKILL.md).
It answers only an allowed Telegram user; every other user is ignored
before any of their messages are read. Send `/disconnect` to remove your
stored Notion connection at any point. Each
connected user's Notion access and refresh tokens are stored in a SQLite
file on the `recipebot-data` volume.

## Allowing a user

A user is allowed from either of two places, and the bot admits the union of
both:

- `TELEGRAM_ALLOWED_USER_IDS` in `.env`, read once at startup. This is the
  bootstrap list, and it is the one that survives a lost database.
- The `allowed_users` table on the `recipebot-data` volume, which the owner
  edits from Telegram and which survives a rebuild.

`TELEGRAM_OWNER_ID` names the single user who may run these commands. Every
other user, allowed or not, is refused:

| Command | What it does |
| --- | --- |
| `/allow <id>` | Adds an ID to the table. |
| `/deny <id>` | Removes an ID from the table. It never removes an ID that `.env` lists, and it never removes the owner. |
| `/allowed` | Lists both sources, marking the `.env` ones. |

`/deny` removes access only. It leaves the person's stored Notion connection
alone, so re-allowing them does not make them reconnect.

`deploy/allow-telegram-user.sh <id>` edits the `.env` list instead, over SSH
from a machine with Tailscale up. It is the bootstrap and recovery path, for
when the bot is down or the owner is not allowed yet. Every routine grant goes
through `/allow`, which needs no laptop.

## Choosing a model provider

`LLM_PROVIDER` selects the backend. It accepts `gemini` (the default) and
`anthropic`, and an unknown value stops the bot at startup.

Set the key for the provider you choose and leave the other unset:

| `LLM_PROVIDER` | Required key | Fast model | Strong model |
| --- | --- | --- | --- |
| `gemini` | `GEMINI_API_KEY` | `gemini-3.1-flash-lite` | `gemini-3.7-flash` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` | `claude-sonnet-5` |

Every extraction starts on the fast model and escalates to the strong one
only when the fast model returns nothing usable. `LLM_MODEL_FAST` and
`LLM_MODEL_STRONG` override the two model ids, so a model change needs no
code change.

## Deployment

The bot runs on a Hetzner VPS as a Docker Compose service. A cron entry there
runs `deploy/deploy.sh` every two minutes. The script does nothing until
`origin/main` moves, then it asks the GitHub checks API whether every check run
on that commit finished green, and only then fast-forwards and rebuilds. A red
or still-running commit waits for the next tick.

A merge to `main` therefore reaches the VPS on its own, within about two
minutes of the `test` workflow going green. Read
`.claude/skills/vps-connection/SKILL.md` to reach the box or to deploy by hand.
