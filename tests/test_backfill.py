import httpx
from notion_client.errors import APIResponseError

from recipebot import backfill
from recipebot.llm import Tag
from recipebot.notion import Vocabulary
from recipebot.users import UserRecord

VOCAB = Vocabulary(
    ingredients={"Beef": "i1", "Rice noodle": "i2"},
    recipe_categories=["Noodles", "Stir-fry"],
    proteins=["Beef", "Vegetarian"],
)


def a_page(page_id, name, category=None, protein=None, ingredients=()):
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Cuisine": {"select": {"name": "Chinese"}},
            "Category": {"select": {"name": category} if category else None},
            "Protein": {"select": {"name": protein} if protein else None},
            "Ingredients": {"relation": [{"id": i} for i in ingredients]},
        },
    }


class FakePages:
    def __init__(self):
        self.updated = []

    def update(self, **kwargs):
        self.updated.append(kwargs)


class FakeStore:
    def __init__(self, pages, error=None, gone=False):
        self.pages = pages
        self.error = error
        self.gone = gone
        self.client = type("C", (), {"pages": FakePages()})()
        self.vocabulary_calls = 0

    def databases_gone(self):
        return self.gone

    def vocabulary(self):
        self.vocabulary_calls += 1
        if self.error:
            raise self.error
        return VOCAB

    def recipe_pages(self):
        return self.pages


class FakeExtractor:
    def __init__(self, tags):
        self.tags = tags
        self.items = []

    def classify(self, items, vocab):
        self.items.extend(items)
        return self.tags


def a_record(user_id=1):
    return UserRecord(
        telegram_user_id=user_id,
        notion_access_token="tok",
        notion_refresh_token=None,
        recipes_ds="ds-r",
        ingredients_ds="ds-i",
        workspace_name="Kitchen",
        connected_at=0,
    )


def unauthorized():
    return APIResponseError(
        code="unauthorized",
        status=401,
        message="revoked",
        headers=httpx.Headers(),
        raw_body_text="",
    )


def run(stores, tags, apply=True):
    lines = []
    extractor = FakeExtractor(tags)
    records = [a_record(user_id) for user_id in stores]
    backfill.run(records, extractor, apply, lambda r: stores[r.telegram_user_id], lines.append)
    return lines, extractor


def test_only_an_untagged_recipe_is_sent_with_its_ingredient_names():
    store = FakeStore(
        [
            a_page("r1", "Beef Ho Fun", ingredients=["i1", "i2"]),
            a_page("r2", "Pancakes", category="Stir-fry", protein="Vegetarian"),
        ]
    )

    _, extractor = run({1: store}, [])

    assert extractor.items == [
        {
            "page_id": "r1",
            "name": "Beef Ho Fun",
            "cuisine": "Chinese",
            "ingredients": ["Beef", "Rice noodle"],
        }
    ]


def test_vocabulary_runs_twice_so_a_property_added_by_the_first_call_has_its_options():
    store = FakeStore([])

    run({1: store}, [])

    assert store.vocabulary_calls == 2


def test_only_the_empty_property_is_filled_and_only_with_a_known_option():
    store = FakeStore([a_page("r1", "Beef Ho Fun", category="Stir-fry")])

    run({1: store}, [Tag(page_id="r1", category="Noodles", protein="Beef")])

    assert store.client.pages.updated == [
        {"page_id": "r1", "properties": {"Protein": {"select": {"name": "Beef"}}}}
    ]


def test_an_unknown_option_is_dropped_and_writes_nothing():
    store = FakeStore([a_page("r1", "Tacos")])

    run({1: store}, [Tag(page_id="r1", category="Tacos", protein="")])

    assert store.client.pages.updated == []


def test_a_dry_run_reports_the_tags_and_writes_nothing():
    store = FakeStore([a_page("r1", "Beef Ho Fun")])

    lines, _ = run({1: store}, [Tag(page_id="r1", category="Noodles", protein="Beef")], apply=False)

    assert store.client.pages.updated == []
    assert any("Beef Ho Fun" in line and "Noodles" in line and "Beef" in line for line in lines)


# A refresh outside the bot rotates the refresh token the bot stores, so the
# user is skipped and the bot refreshes them on their next message.
def test_a_revoked_user_is_skipped_and_the_next_user_still_runs():
    revoked = FakeStore([], error=unauthorized())
    current = FakeStore([a_page("r1", "Beef Ho Fun")])

    lines, _ = run({1: revoked, 2: current}, [Tag(page_id="r1", category="Noodles", protein="Beef")])

    assert any("skip" in line and "1" in line for line in lines)
    assert len(current.client.pages.updated) == 1


def test_a_user_whose_databases_are_gone_is_skipped():
    store = FakeStore([a_page("r1", "Beef Ho Fun")], gone=True)

    lines, extractor = run({1: store}, [])

    assert store.vocabulary_calls == 0
    assert extractor.items == []
    assert any("skip" in line for line in lines)


def test_any_other_failure_is_reported_and_the_next_user_still_runs():
    broken = FakeStore([], error=RuntimeError("boom"))
    current = FakeStore([a_page("r1", "Beef Ho Fun")])

    lines, _ = run({1: broken, 2: current}, [Tag(page_id="r1", category="Noodles", protein="Beef")])

    assert any("boom" in line for line in lines)
    assert len(current.client.pages.updated) == 1
