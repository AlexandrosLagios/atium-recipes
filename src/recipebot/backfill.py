"""Fill Category and Protein on every connected user's saved recipes that
lack them. Run inside the container, where the users' tokens live:

    docker compose exec recipebot python -m recipebot.backfill
    docker compose exec recipebot python -m recipebot.backfill --apply

Dry run by default. A value a user already set is never touched, so a second
run only fills what the first one left empty."""
import argparse

from notion_client.errors import APIResponseError

from .config import Config
from .llm import Extractor
from .notion import NotionStore, classified_properties
from .users import UserStore

_FIELDS = ("Category", "Protein")


def _select(page: dict, prop: str) -> str:
    return ((page["properties"].get(prop) or {}).get("select") or {}).get("name", "")


def _item(page: dict, names: dict[str, str]) -> dict:
    props = page["properties"]
    return {
        "page_id": page["id"],
        "name": "".join(span["plain_text"] for span in props["Name"]["title"]),
        "cuisine": _select(page, "Cuisine"),
        "ingredients": [
            names[rel["id"]] for rel in props["Ingredients"]["relation"] if rel["id"] in names
        ],
    }


def _backfill_user(store, extractor, apply: bool, out) -> int:
    # The first call adds a property the user lacks; the schema it read
    # predates that, so only the second call sees the new options.
    store.vocabulary()
    vocab = store.vocabulary()
    names = {page_id: name for name, page_id in vocab.ingredients.items()}
    untagged = {
        page["id"]: page
        for page in store.recipe_pages()
        if not all(_select(page, prop) for prop in _FIELDS)
    }
    if not untagged:
        return 0
    items = {page_id: _item(page, names) for page_id, page in untagged.items()}
    changed = 0
    for tag in extractor.classify(list(items.values()), vocab):
        page = untagged.get(tag.page_id)
        if page is None:
            continue
        properties = {
            prop: value
            for prop, value in classified_properties(tag.category, tag.protein, vocab).items()
            if not _select(page, prop)
        }
        out(f"  {items[tag.page_id]['name']}: {tag.category or '-'} / {tag.protein or '-'}")
        if not properties:
            continue
        changed += 1
        if apply:
            store.client.pages.update(page_id=tag.page_id, properties=properties)
    return changed


def run(records, extractor, apply: bool, store_for=NotionStore.from_user, out=print) -> None:
    changed = 0
    for record in records:
        user = record.telegram_user_id
        out(f"user {user} ({record.workspace_name})")
        store = store_for(record)
        try:
            if store.databases_gone():
                out(f"skip user {user}: databases are gone")
                continue
            changed += _backfill_user(store, extractor, apply, out)
        except Exception as exc:
            # A refresh here would rotate the refresh token the bot stores, so
            # an expired user is left for the bot to refresh on their next
            # message, and a second run covers them.
            if isinstance(exc, APIResponseError) and exc.status == 401:
                out(f"skip user {user}: Notion token expired")
            else:
                out(f"failed user {user}: {exc}")
    verb = "written" if apply else "would be written (re-run with --apply)"
    out(f"{len(records)} users, {changed} recipes {verb}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the tags to Notion")
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    run(UserStore(cfg.db_path).all(), Extractor.from_config(cfg), args.apply)


if __name__ == "__main__":
    main()
