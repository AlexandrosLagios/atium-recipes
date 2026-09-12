# atium-recipes

Sync recipes from URLs, Instagram, and other sources into a Notion database.

## Running the bot

1. Copy `.env.example` to `.env` and fill in the four secrets
   (`TELEGRAM_TOKEN`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`,
   `TELEGRAM_ALLOWED_USER_ID`) and the two Notion data source IDs
   (`NOTION_RECIPES_DS`, `NOTION_INGREDIENTS_DS`).
2. Share the Notion integration with the Kitchen page so it can reach the
   Recipes and Ingredients data sources.
3. Start the bot: `docker compose up -d --build`.

The bot uses long polling, so the container opens no inbound port on the
host. It answers only the Telegram user whose ID is set in
`TELEGRAM_ALLOWED_USER_ID`; every other user is ignored.
