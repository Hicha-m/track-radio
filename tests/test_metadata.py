from app.services.metadata import parse_icy_block, split_title


def test_parse_icy_block():
    block = b"StreamTitle='Aya Nakamura - Djadja';\x00\x00"
    assert parse_icy_block(block) == "Aya Nakamura - Djadja"


def test_parse_empty_icy_block():
    assert parse_icy_block(b"StreamTitle='';\x00") is None


def test_split_title_only_on_separator():
    assert split_title("  Artist  -  Track  ") == ("Artist", "Track")
    assert split_title("A title-only value") == (None, "A title-only value")


def test_split_title_removes_skyrock_internal_identifier():
    assert split_title("Josas - Deliciosa §7713267") == ("Josas", "Deliciosa")
