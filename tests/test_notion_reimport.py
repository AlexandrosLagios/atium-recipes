from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore, Vocabulary, _body_blocks

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1"}, cuisines=["Chinese"], meals=["Dinner"], categories=["Protein"]
)


def a_recipe(**overrides) -> Recipe:
    defaults = dict(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=[Ingredient(name="Chicken")],
        method=["Brown.", "Simmer."],
        source="Web",
        source_url="https://example.com/braise",
        source_text="200 g chicken",
    )
    return Recipe(**{**defaults, **overrides})


def a_toggle(block_id: str, text: str) -> dict:
    return {
        "id": block_id,
        "type": "toggle",
        "toggle": {"rich_text": [{"plain_text": text, "text": {"content": text}}]},
    }


def a_paragraph(block_id: str, text: str) -> dict:
    return {
        "id": block_id,
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": text, "text": {"content": text}}]},
    }


class FakeChildren:
    def __init__(self, tree, calls):
        self.tree = tree
        self.calls = calls
        self.appended = []

    def list(self, **kwargs):
        return {
            "results": self.tree.get(kwargs["block_id"], []),
            "has_more": False,
            "next_cursor": None,
        }

    def append(self, **kwargs):
        self.calls.append("append")
        self.appended.append(kwargs)
        return {}


class FakeBlocks:
    def __init__(self, tree):
        self.calls = []
        self.children = FakeChildren(tree, self.calls)
        self.deleted = []

    def delete(self, **kwargs):
        self.calls.append("delete")
        self.deleted.append(kwargs["block_id"])


class FakePages:
    def __init__(self):
        self.updated = []
        self.created = []

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs["page_id"], "url": "https://notion.so/existing"}

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new", "url": "https://notion.so/new"}


class FakeClient:
    def __init__(self, tree=None):
        self.pages = FakePages()
        self.blocks = FakeBlocks(tree or {})


def a_store(tree=None) -> tuple[NotionStore, FakeClient]:
    client = FakeClient(tree)
    return NotionStore(client, "ds-recipes", "ds-ingredients"), client


def test_source_text_reads_the_paragraphs_under_the_source_text_toggle():
    tree = {
        "page-1": [a_paragraph("b1", "Ingredients"), a_toggle("b2", "Source text")],
        "b2": [a_paragraph("b3", "200 g chicken"), a_paragraph("b4", "soy sauce")],
    }
    store, _ = a_store(tree)

    assert store.source_text("page-1") == "200 g chicken\nsoy sauce"


def test_source_text_is_empty_when_the_page_has_no_toggle():
    store, _ = a_store({"page-1": [a_paragraph("b1", "Ingredients")]})

    assert store.source_text("page-1") == ""


def test_update_recipe_rewrites_the_properties_of_the_same_page():
    store, client = a_store({"page-1": [a_paragraph("b1", "stale")]})

    url = store.update_recipe("page-1", a_recipe(name="Braised chicken"), VOCAB)

    assert url == "https://notion.so/existing"
    assert client.pages.created == []
    assert len(client.pages.updated) == 1
    properties = client.pages.updated[0]["properties"]
    assert properties["Name"]["title"][0]["text"]["content"] == "Braised chicken"
    assert properties["Ingredients"]["relation"] == [{"id": "p1"}]


def test_update_recipe_replaces_the_old_body():
    store, client = a_store({"page-1": [a_paragraph("b1", "stale"), a_paragraph("b2", "older")]})

    store.update_recipe("page-1", a_recipe(), VOCAB)

    written = [b for call in client.blocks.children.appended for b in call["children"]]
    assert written == _body_blocks(a_recipe())
    assert client.blocks.deleted == ["b1", "b2"]


# A failure between the two leaves both bodies on the page, which loses nothing.
def test_update_recipe_appends_the_new_body_before_deleting_the_old_one():
    store, client = a_store({"page-1": [a_paragraph("b1", "stale")]})

    store.update_recipe("page-1", a_recipe(), VOCAB)

    assert client.blocks.calls.index("append") < client.blocks.calls.index("delete")


# An absent property means "unchanged" to an update, so a value the new
# extraction dropped has to be named to go.
def test_update_recipe_clears_a_cuisine_and_a_keeps_the_new_extraction_dropped():
    store, client = a_store({"page-1": [a_paragraph("b1", "stale")]})

    store.update_recipe("page-1", a_recipe(cuisine="", keeps_days=0), VOCAB)

    properties = client.pages.updated[0]["properties"]
    assert properties["Cuisine"] == {"select": None}
    assert properties["Keeps (days)"] == {"number": None}
    assert properties["Source URL"] == {"url": "https://example.com/braise"}


def test_update_recipe_writes_a_cuisine_and_a_keeps_the_new_extraction_found():
    store, client = a_store({"page-1": [a_paragraph("b1", "stale")]})

    store.update_recipe("page-1", a_recipe(cuisine="Chinese", keeps_days=4), VOCAB)

    properties = client.pages.updated[0]["properties"]
    assert properties["Cuisine"] == {"select": {"name": "Chinese"}}
    assert properties["Keeps (days)"] == {"number": 4}
