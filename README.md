# atium-recipes

Sync recipes from URLs, Instagram, and other sources into a Notion database.

## Running the bot

1. Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN`,
   `TELEGRAM_ALLOWED_USER_IDS` (a comma-separated list of Telegram user
   IDs), `NOTION_CLIENT_ID`/`NOTION_CLIENT_SECRET`/`NOTION_REDIRECT_URI`
   (from a Notion public integration, install scope "Selected workspaces
   only"), and the model provider's API key.
2. Start the bot: `docker compose up -d --build`.
3. Each allowed friend messages the bot and taps **Connect Notion**. The bot
   creates a Recipes and an Ingredients database under whatever page they
   share, and confirms in Telegram once it is done.

The bot uses Telegram long polling, so it opens no inbound port for
Telegram traffic. It does open one local port for the Notion OAuth
callback, proxied over HTTPS by the VPS's shared reverse proxy; see
[.claude/skills/vps-connection/SKILL.md](.claude/skills/vps-connection/SKILL.md).
It answers only a Telegram user whose ID is in `TELEGRAM_ALLOWED_USER_IDS`;
every other user is ignored before any of their messages are read.

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
