---
name: add-recipe-to-notion
description: "Use when the user wants to add a recipe to this project's Notion recipe collection: a link to a recipe site, an Instagram/TikTok/YouTube post, a photo of a recipe, or pasted text, plus a request to save/add it. Extracts the recipe, matches or creates shopping-level Ingredients rows, and writes a Recipes page with an ingredient list, a numbered method, and a Notes section. Not for browsing, editing, or rating an existing recipe, and not the only path: an in-app importer in this project may also add recipes directly."
---

# add-recipe-to-notion

Adds one recipe to the Notion recipe collection described in this project's
`notion-data-model` memory: a Recipes database and an Ingredients database,
linked by a relation, reached through the user's connected Notion MCP server
(tool names look like `mcp__<server>__notion-*`).

Databases (fetch each URL first to confirm the schema still matches; option
lists and property names can drift):
- Recipes: `https://app.notion.com/p/65b68fe361e84f4fbc92cc32229032d5`
- Ingredients: `https://app.notion.com/p/ca66c06074374dd49d5431362d5070b0`

## Steps

1. **Extract the recipe.** For a web URL, fetch it and pull: title, cuisine,
   meal type, servings, total time in minutes (include any resting/soaking/
   marinating), difficulty, the ingredient list with quantities, and numbered
   method steps. For Instagram/TikTok/YouTube, read the caption/description;
   for a photo, read the text in the image; for pasted text, parse it
   directly. The source changes how you get the text, not what you do with
   it afterward.

2. **Dedupe.** Strip the query string and fragment from the source URL, then
   query the Recipes data source for a row whose Source URL matches. If one
   exists, stop and tell the user instead of creating a duplicate: point
   them at the existing page.

3. **Resolve ingredients.** For each ingredient, reduce it to a shopping-level
   singular name: drop prep words and quantities, keep the product identity
   ("dried shiitake mushrooms" -> "Shiitake mushroom", "neutral cooking oil"
   -> "Cooking oil", "4 cloves garlic, minced" -> "Garlic"). Query the
   Ingredients data source for a matching Name (case-insensitively; reuse an
   existing row rather than creating a near-duplicate). For anything missing,
   create a new Ingredients row:
   - `Category`: one of the data source's existing select options only. Look
     at how similar ingredients are already categorized (oils and sauces
     under the same category as other oils and sauces, fresh produce under
     the same category as other fresh produce): never invent a new option.
   - `In pantry`: `__YES__` for oils, sauces, and staples a kitchen almost
     always has stocked (matches the pattern of existing rows for the same
     category); `__NO__` for anything perishable or recipe-specific. This is
     a shopping-list default, not a claim about this user's actual pantry.
     When unsure, default to `__NO__` so the item shows up on the shopping
     list rather than silently disappearing from it.

4. **Create the Recipes page**, parented at the Recipes data source, with:
   - `Name`, `Source URL` (stripped, as in step 2), `Source` (`Web`,
     `Instagram`, `TikTok`, `YouTube`, `Photo`, or `Text`, matching the
     source type)
   - `Cuisine` and `Meal`: pick from the data source's existing select /
     multi-select options only. If nothing fits well, choose the closest
     existing option rather than adding a new one, and say so in your report.
   - `Difficulty` (`Easy` or `Hard`), `Time (min)`, `Servings`
   - `Ingredients`: the relation array of every matched/created Ingredient
     page URL from step 3

   Page body (Notion-flavored Markdown):
   - A bulleted ingredient list with the real quantities from the source
     (not the shopping-level names from step 3, which are for the relation)
   - A numbered method
   - A `## Notes` section for substitutions, tips, or dietary swaps mentioned
     in the source
   - A collapsed toggle holding the raw extracted text, for a source that
     turns out to be mis-parsed later:
     ```
     <details>
     <summary>Source text</summary>
     	...
     </details>
     ```

5. **Report back**: the new recipe page URL, and which ingredients were
   newly created versus reused.
