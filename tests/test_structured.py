import pytest

from app.services.structured import StructuredError, flatten, locate_block

BLOCKS = [
    {"id": "h1", "type": "heading", "level": 1, "text": "Summer launch"},
    {"id": "p1", "type": "paragraph", "text": "India's #1 fastest delivery, guaranteed."},
    {"id": "l1", "type": "list", "items": ["Fast", "Light"]},
    {"id": "i1", "type": "image", "alt": "", "url": "https://cdn.example/banner.png"},
    {"id": "i2", "type": "image", "asset_index": 0},
    {"id": "b1", "type": "button", "label": "Click here", "url": "https://acme.example/buy"},
]


def test_flatten_produces_the_text_the_checks_read():
    text, block_map = flatten(BLOCKS)
    assert text.split("\n\n") == ["# Summer launch", "India's #1 fastest delivery, guaranteed.", "- Fast\n- Light",
                                  "![](https://cdn.example/banner.png)", "[Click here](https://acme.example/buy)"]
    assert [b["id"] for b in block_map] == ["h1", "p1", "l1", "i1", "b1"]  # the uploaded-asset image makes no text
    assert all(text[b["start"]:b["end"]] for b in block_map)


def test_locate_block_follows_the_draft_until_it_stops_lining_up():
    text, block_map = flatten(BLOCKS)
    ids = [b["id"] for b in block_map]
    offset = text.index("#1 fastest")
    assert locate_block(text, ids, offset) == "p1"
    assert locate_block(text + "\n\nextra paragraph", ids, offset) is None


@pytest.mark.parametrize("bad", [[], [{"type": "paragraph", "text": "  "}], [{"type": "video"}], [{"type": "image"}]])
def test_invalid_blocks_are_rejected(bad):
    with pytest.raises(StructuredError):
        flatten(bad)
