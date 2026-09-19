# Telegram recipe bot spec

## Settled design, unchanged from the previous handoff

Capture is a Telegram bot on long polling, so the VPS opens no inbound port.
The handler drops every update whose `from.id` does not match the user's
Telegram ID, before it parses anything or spends money on an API call. This
check is a trust boundary. Do not make it optional.

Five extraction paths converge on one `Recipe` object:

| Input | Path | Confidence |
| --- | --- | --- |
| URL with JSON-LD | `recipe-scrapers`, no model | high |
| URL without JSON-LD | readability text, then the model | low |
| Instagram or TikTok URL | caption via `yt-dlp`, keyframes via `ffmpeg`, then the model's text and vision | low |
| Photo or screenshot | model vision | low |
| Pasted text | the model | low |

Run a fast model for text and vision, and escalate to a stronger model only
when a parse returns empty. The provider and the two model ids are
configuration, not code: `LLM_PROVIDER` selects `gemini` or `anthropic`, and
`LLM_MODEL_FAST` and `LLM_MODEL_STRONG` override the ids. The current default
is Gemini, `gemini-3.1-flash-lite` escalating to `gemini-3.7-flash`.

A high-confidence result writes to Notion at once and the bot replies with the
page link. A low-confidence result produces a preview message with Save and
Discard buttons and writes nothing until the user taps Save. Previews live in
memory; a restart forgets them and the user re-shares.

Deployment is one Python service, one docker-compose service, `ffmpeg` and
`yt-dlp` baked into the image, secrets in a compose env file on the VPS. The
GitHub repository is public, so never commit a secret.

Deliberately omitted, absent a failure that demands them: Whisper or any audio
transcription, a queue or Redis, a datastore of our own, persisted previews, a
web UI.

## The Notion schema, as actually built

The repository is public, so no Notion database ID, data source ID, or page ID
is committed here. The Kitchen hub page ID and the sample recipe page ID are
recorded in the maintainer's private project notes and are deliberately not
committed. The two data source IDs the bot reads at runtime come from the
environment: the Recipes data source is `NOTION_RECIPES_DS` and the
Ingredients data source is `NOTION_INGREDIENTS_DS`.

- Kitchen hub page: ID recorded in the maintainer's private project notes, not committed.
- Recipes database, data source `NOTION_RECIPES_DS`: database ID recorded in the maintainer's private project notes, not committed.
- Ingredients database, data source `NOTION_INGREDIENTS_DS`: database ID recorded in the maintainer's private project notes, not committed.
- Sample recipe page: ID recorded in the maintainer's private project notes, not committed.

Recipes properties: `Name` (title), `Source URL` (url, dedupe key), `Source`
(select: Web, Instagram, TikTok, YouTube, Photo, Text), `Cuisine` (select),
`Meal` (multi-select: Breakfast, Lunch, Dinner, Dessert, Snack, Side),
`Difficulty` (select: Easy, Hard), `Time (min)` (number), `Servings` (number),
`Rating` (select, one to five stars), `Ingredients` (relation),
`Missing count` (rollup), `Missing` (formula).

Ingredients properties: `Name` (title), `Category` (select: Vegetables and
aromatics; Sauces and condiments; Spices and seasonings; Staples; Protein;
Dairy and eggs), `In pantry` (checkbox), `Recipes` (relation), `Used in`
(rollup count).

Dropped against the older proposal: `Status` and `Diet`. An empty `Rating`
means not tried.

## Rules the bot must obey

- Ingredient rows are shopping level and singular. "Chicken", not "boneless
  chicken thighs". The quantity and the form stay in the page body only.
- Strip the query string and the fragment from `Source URL` before the dedupe
  query and before the write. The user's own sample carried `?utm_source=`.
  A `youtube.com/watch` URL is the one exception. Its `v` parameter carries the
  video's identity, so keep `v` and strip every other parameter. Without that
  exception every YouTube recipe collapses onto one dedupe key.
- Before every extraction, read the Ingredients names and the existing
  `Cuisine`, `Meal`, and `Category` options, and inject them into the prompt.
  Reuse an existing name wherever the ingredient matches; mint a new one only
  when the ingredient is genuinely new.
- A new ingredient row is created with `In pantry` unticked. Staples stay
  ticked forever, which is how salt and oil avoid counting as missing.
- When the model proposes a new ingredient that resembles an existing one, fold
  the question into the preview message. Offer Save, Save-and-merge, Discard.
  Do not create the row first and clean up later.
- `Time (min)` is total time including resting. The sample recipe is 745,
  because the pickle rests overnight.
- Set the source image as the page cover. The Gallery view depends on it.
- Page body layout, matching the sample page: `## Ingredients` as a bulleted
  list with quantities, `## Method` as a numbered list, `## Notes` left empty
  for the user, then a collapsed `Source text` toggle holding the caption,
  scraped body, or vision transcript.
- The database holds English. Where a Greek ingredient has no honest English
  equivalent, keep the transliterated term and add a gloss, for example
  `anthotyro (Greek whey cheese)`. Do not substitute an approximate name.
- Query `Source URL` before every write. If the URL exists, never create a
  second page: reply with the existing page link and offer to reimport it,
  by reading the site again or by re-running the extraction over the page's
  own `Source text`. A reimport rewrites that page in place.
