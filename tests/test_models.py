"""Unit tests for discogs_mcp.models: trimming noise/images, credit joining, format summaries."""

from __future__ import annotations

from discogs_mcp import models


def test_strip_noise_drops_resource_urls_and_images_by_default():
    obj = {
        "id": 1,
        "resource_url": "https://api.discogs.com/releases/1",
        "thumbnail_url": "x",
        "data_quality": "Correct",
        "submitter": {"username": "x"},
        "contributors": [{"username": "x"}],
        "images": [{"uri": "https://example/1.jpg"}],
        "thumb": "https://example/thumb.jpg",
        "cover_image": "https://example/cover.jpg",
        "nested": {"uri": "https://example/x.jpg", "keep": "value"},
    }
    out = models.strip_noise(obj)
    assert "resource_url" not in out
    assert "thumbnail_url" not in out
    assert "data_quality" not in out
    assert "submitter" not in out
    assert "contributors" not in out
    assert "images" not in out
    assert "thumb" not in out
    assert "cover_image" not in out
    assert "uri" not in out["nested"]
    assert out["nested"]["keep"] == "value"
    assert out["id"] == 1


def test_strip_noise_keeps_images_when_requested():
    obj = {"images": [{"uri": "https://example/1.jpg"}], "resource_url": "x"}
    out = models.strip_noise(obj, keep_images=True)
    assert out["images"] == [{"uri": "https://example/1.jpg"}]
    assert "resource_url" not in out


def test_strip_noise_drops_empty_values():
    obj = {"a": None, "b": "", "c": [], "d": {}, "e": "keep", "f": 0, "g": False}
    out = models.strip_noise(obj)
    # falsy-but-meaningful values (0, False) must survive; only None/""/[]/{} are dropped
    assert out == {"e": "keep", "f": 0, "g": False}


def test_strip_noise_recurses_into_lists():
    obj = {"items": [{"resource_url": "x", "id": 1}, {"resource_url": "y", "id": 2}]}
    out = models.strip_noise(obj)
    assert out["items"] == [{"id": 1}, {"id": 2}]


def test_clean_name_strips_discogs_disambiguator():
    assert models.clean_name("Aphex Twin (2)") == "Aphex Twin"
    assert models.clean_name("Boards of Canada") == "Boards of Canada"
    assert models.clean_name(None) == ""
    assert models.clean_name("") == ""


def test_artist_credit_joins_single_artist():
    assert models.artist_credit([{"name": "Aphex Twin (2)"}]) == "Aphex Twin"


def test_artist_credit_prefers_anv_over_name():
    assert models.artist_credit([{"name": "Richard D. James", "anv": "AFX"}]) == "AFX"


def test_artist_credit_joins_multiple_with_join_word():
    artists = [
        {"name": "A", "join": "&"},
        {"name": "B (2)", "join": ""},
    ]
    assert models.artist_credit(artists) == "A & B"


def test_artist_credit_comma_join():
    artists = [
        {"name": "A", "join": ","},
        {"name": "B", "join": "&"},
        {"name": "C", "join": ""},
    ]
    assert models.artist_credit(artists) == "A, B & C"


def test_artist_credit_empty():
    assert models.artist_credit(None) == ""
    assert models.artist_credit([]) == ""


def test_format_summary_single_with_qty_and_descriptions():
    formats = [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}]
    assert models.format_summary(formats) == "2×Vinyl (LP, Album)"


def test_format_summary_qty_one_omits_multiplier():
    formats = [{"name": "CD", "qty": "1", "descriptions": ["Album"]}]
    assert models.format_summary(formats) == "CD (Album)"


def test_format_summary_no_descriptions():
    formats = [{"name": "CD", "qty": "1"}]
    assert models.format_summary(formats) == "CD"


def test_format_summary_includes_text():
    formats = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"], "text": "Clear vinyl"}]
    assert models.format_summary(formats) == "Vinyl (LP, Clear vinyl)"


def test_format_summary_joins_multiple_formats():
    formats = [{"name": "Vinyl", "qty": "1"}, {"name": "CD", "qty": "1"}]
    assert models.format_summary(formats) == "Vinyl; CD"


def test_format_summary_empty():
    assert models.format_summary(None) == ""
    assert models.format_summary([]) == ""


def test_basic_release_shapes_collection_block():
    info = {
        "id": 1001,
        "master_id": 5001,
        "artists": [{"name": "Aphex Twin (2)"}],
        "title": "SAW 85-92",
        "year": 1992,
        "labels": [{"id": 50, "name": "Apollo (3)", "catno": "AMB 3922", "resource_url": "x"}],
        "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}],
        "genres": ["Electronic"],
        "styles": ["Ambient"],
        "resource_url": "x",
    }
    out = models.basic_release(info)
    assert out["release_id"] == 1001
    assert out["artist"] == "Aphex Twin"
    assert out["format"] == "2×Vinyl (LP, Album)"
    assert out["labels"] == [{"id": 50, "name": "Apollo", "catno": "AMB 3922"}]
    assert "resource_url" not in out


def test_release_detail_strips_images_unless_requested():
    r = {
        "id": 1,
        "artists": [{"name": "A"}],
        "title": "T",
        "images": [{"uri": "https://example/1.jpg"}],
        "resource_url": "x",
    }
    out = models.release_detail(r)
    assert "images" not in out
    out2 = models.release_detail(r, keep_images=True)
    assert out2["images"] == ["https://example/1.jpg"]


def test_price_formats_value_and_currency():
    assert models.price({"value": 25.5, "currency": "USD"}) == "25.50 USD"


def test_price_none_cases():
    assert models.price(None) is None
    assert models.price({"currency": "USD"}) is None
