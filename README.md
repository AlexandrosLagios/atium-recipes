# atium-recipes

Sync recipes from URLs, Instagram, and other sources into a Notion database.

## Running the bot

1. Copy `.env.example` to `.env` and fill in the secrets
   (`TELEGRAM_TOKEN`, `NOTION_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and the
   model provider's API key), and the two Notion data source IDs
   (`NOTION_RECIPES_DS`, `NOTION_INGREDIENTS_DS`).
2. Share the Notion integration with the Kitchen page so it can reach the
   Recipes and Ingredients data sources.
3. Start the bot: `docker compose up -d --build`.

The bot uses long polling, so the container opens no inbound port on the
host. It answers only the Telegram user whose ID is set in
`TELEGRAM_ALLOWED_USER_ID`; every other user is ignored.

## Choosing a model provider

`LLM_PROVIDER` selects the backend. It accepts `gemini` (the default) and
`anthropic`, and an unknown value stops the bot at startup.

Set the key for the provider you choose and leave the other unset:

| `LLM_PROVIDER` | Required key | Fast model | Strong model |
| --- | --- | --- | --- |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash-lite` | `gemini-2.5-flash` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` | `claude-sonnet-5` |

Every extraction starts on the fast model and escalates to the strong one
only when the fast model returns nothing usable. `LLM_MODEL_FAST` and
`LLM_MODEL_STRONG` override the two model ids, so a model change needs no
code change.
