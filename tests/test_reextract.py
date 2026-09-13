import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "reextract", Path(__file__).parent.parent / "scripts" / "reextract.py"
)
reextract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reextract)


# The script diffs blocks the API returned against blocks built locally, and
# only the first shape carries plain_text.
def test_block_text_reads_both_the_api_shape_and_the_locally_built_shape():
    from_api = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {"plain_text": "200 g ", "text": {"content": "200 g "}},
                {"plain_text": "Chicken", "text": {"content": "Chicken"}},
            ]
        },
    }
    built = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": "200 g Chicken"}}]},
    }

    assert reextract._text(from_api) == "200 g Chicken"
    assert reextract._text(built) == "200 g Chicken"


def test_body_text_keeps_headings_and_list_items_and_drops_the_source_toggle():
    blocks = [
        {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Ingredients"}]}},
        {"type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "Sauce"}]}},
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"plain_text": "80 ml Soy sauce"}]},
        },
        {"type": "toggle", "toggle": {"rich_text": [{"plain_text": "Source text"}]}},
    ]

    assert reextract._body_text(blocks) == ["Ingredients", "Sauce", "80 ml Soy sauce"]
