---
name: find-existing-utility
description: "Use before adding a small helper (URL/string cleanup, option-name matching against Notion's selects) or a new extractor/writer path in this project. Searches src/recipebot/ by behaviour first, so a duplicate of an existing helper or flow doesn't get written. Not for the recipe-add flow itself, which is add-recipe-to-notion."
---

# find-existing-utility

`recipebot` is one flat package (`src/recipebot/`, under 1,000 lines):
duplication is a name away, not a directory away. This project's own history
shows the cost — `_snap_option` was later split into per-field matchers
because option-cleaning logic had drifted across call sites, and
`canonical_url` replaced ad hoc query-stripping that had crept into more than
one place.

## Search by behaviour, not by a guessed name

Before writing a new helper, enumerate what exists instead of grepping for the
name you'd invent for it:

```
rg -n '^(def |class )' src/recipebot/*.py
```

Then cross-check by operation (`.strip(`, `urlsplit`, `parse_qsl`, `dedupe`) so
a differently-named helper still surfaces.

Known normalization helpers to check before writing a sibling:
- URL canonicalization: `canonical_url`, `_kept_query` in `src/recipebot/models.py`
- Matching a value against Notion's existing select/multi-select options:
  `_snap_category`, `_clean_option_name`, `_known_spelling`,
  `_cuisine_option`, `_meal_options` in `src/recipebot/notion.py`

## A new extraction/write path, not just a helper

Before adding a new source type or Notion write, check whether `extract.py`,
`scrape.py`, `social.py`, or `notion.py` already has a sibling that does the
same shape of work for a different input. If the honest description of the
new code is "like `from_url` but for X", extract the shared part and call it
from both, instead of forking the flow.

## Where a new helper goes

Only add one when the enumerated surface has nothing that fits. This project
has no shared `utils` module — put a small generic helper in the module it
serves, next to the code that needs it, matching the existing layout.
