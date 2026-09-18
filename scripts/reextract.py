"""Re-run extraction over the Source text stored on each recipe page and
rewrite that page's body, so a prompt change reaches recipes that are already
saved. Run by hand, locally, with the real NOTION_TOKEN/NOTION_RECIPES_DS/
NOTION_INGREDIENTS_DS and the provider key in the environment.

Dry run by default; pass --apply to write. Name page ids to limit the run.

Two things the dry run will not warn you about twice: the body is replaced
wholesale, so anything hand-written on the page is lost, and a near-match
ingredient is merged into its existing row without asking, because a script has
nobody to ask."""
import argparse
import difflib
import os
import sys
from pathlib import Path

from notion_client import Client

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recipebot.config import DEFAULT_PROVIDER, PROVIDER_KEYS, Config  # noqa: E402
from recipebot.llm import Extractor, text_block  # noqa: E402
from recipebot.models import Recipe  # noqa: E402
from recipebot.notion import (  # noqa: E402
    CHILDREN_LIMIT,
    NotionStore,
    Vocabulary,
    _body_blocks,
    block_text as _text,
    page_children as _children,
    reconcile_ingredients,
)

# Only the provider fields matter here, so the rest carry values that make the
# frozen dataclass valid rather than values anything reads.
def _config() -> Config:
    provider = os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER
    return Config(
        telegram_token="",
        allowed_user_ids=frozenset({0}),
        notion_client_id="",
        notion_client_secret="",
        notion_redirect_uri="",
        oauth_callback_port=0,
        db_path="",
        llm_provider=provider,
        llm_api_key=os.environ[PROVIDER_KEYS[provider]],
        model_fast=os.environ.get("LLM_MODEL_FAST", ""),
        model_strong=os.environ.get("LLM_MODEL_STRONG", ""),
    )


def _body_text(blocks: list[dict]) -> list[str]:
    kinds = ("heading_2", "heading_3", "bulleted_list_item", "numbered_list_item")
    return [_text(b) for b in blocks if b["type"] in kinds]


def _ingredient_ids(store: NotionStore, vocab: Vocabulary, recipe: Recipe) -> list[str]:
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    categories = {item.name: item.category for item in recipe.ingredients}
    ids = list(plan.existing.values())
    undecided = list(plan.new)
    for proposed, target in plan.near.items():
        if target in vocab.ingredients:
            ids.append(vocab.ingredients[target])
        else:
            undecided.append(proposed)
    created: dict[str, str] = {}
    for name in undecided:
        key = name.strip().lower()
        if key not in created:
            created[key] = store.create_ingredient(
                name, categories.get(name, ""), vocab.categories
            )
        ids.append(created[key])
    return list(dict.fromkeys(ids))


def _rewrite(client: Client, page_id: str, blocks: list[dict], body: list[dict]) -> None:
    for block in blocks:
        client.blocks.delete(block_id=block["id"])
    for start in range(0, len(body), CHILDREN_LIMIT):
        client.blocks.children.append(
            block_id=page_id, children=body[start : start + CHILDREN_LIMIT]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_ids", nargs="*", help="limit the run to these pages")
    parser.add_argument("--apply", action="store_true", help="write the rewritten bodies")
    args = parser.parse_args()

    client = Client(auth=os.environ["NOTION_TOKEN"])
    store = NotionStore(
        client, os.environ["NOTION_RECIPES_DS"], os.environ["NOTION_INGREDIENTS_DS"]
    )
    extractor = Extractor.from_config(_config())
    vocab = store.vocabulary()

    pages, cursor = [], None
    while True:
        result = client.data_sources.query(
            data_source_id=store.recipes_ds, start_cursor=cursor, page_size=100
        )
        pages += result["results"]
        cursor = result.get("next_cursor")
        if not result.get("has_more"):
            break
    if args.page_ids:
        wanted = {pid.replace("-", "") for pid in args.page_ids}
        pages = [p for p in pages if p["id"].replace("-", "") in wanted]

    changed = 0
    for page in pages:
        title_rt = page["properties"]["Name"]["title"]
        title = title_rt[0]["plain_text"] if title_rt else "(untitled)"
        blocks = _children(client, page["id"])
        source_text = store.source_text(page["id"])
        if not source_text.strip():
            print(f"[skip] {title}: no stored source text")
            continue

        extracted = extractor.extract([text_block(source_text)], vocab)
        if not extracted or not extracted.ingredients or not extracted.method:
            print(f"[skip] {title}: extraction returned nothing usable")
            continue

        props = page["properties"]
        recipe = Recipe.from_extracted(
            extracted,
            source=(props.get("Source", {}).get("select") or {}).get("name", "Text"),
            source_url=props.get("Source URL", {}).get("url") or "",
            source_text=source_text,
        )
        body = _body_blocks(recipe)
        diff = list(
            difflib.unified_diff(
                _body_text(blocks), _body_text(body), lineterm="", n=0, fromfile=title
            )
        )
        if not diff:
            print(f"[same] {title}")
            continue

        changed += 1
        notes = [b for b in blocks if b["type"] == "bulleted_list_item"]
        print(f"\n[{'rewrite' if args.apply else 'would rewrite'}] {title}")
        print("\n".join(f"  {line}" for line in diff[2:]))
        if args.apply:
            _rewrite(client, page["id"], blocks, body)
            client.pages.update(
                page_id=page["id"],
                properties={
                    "Ingredients": {
                        "relation": [
                            {"id": pid} for pid in _ingredient_ids(store, vocab, recipe)
                        ]
                    }
                },
            )
            vocab = store.vocabulary()
        elif notes:
            print("  note: this page's bullets include anything hand-written, and go too")

    verb = "rewritten" if args.apply else "would change"
    print(f"\n{changed} of {len(pages)} recipes {verb}")
    if not args.apply and changed:
        print("re-run with --apply to write")


if __name__ == "__main__":
    main()
