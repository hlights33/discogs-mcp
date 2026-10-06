"""Compact, realistically-shaped Discogs API payload builders for tests."""

from __future__ import annotations

from typing import Any


def artist(name: str = "Aphex Twin (2)", id: int = 45, **kw: Any) -> dict:
    return {"id": id, "name": name, "anv": "", "join": "", "role": "", "resource_url": "x", **kw}


def label_ref(name: str = "Warp Records", id: int = 50, catno: str = "WARP69") -> dict:
    return {"id": id, "name": name, "catno": catno, "resource_url": "x"}


def format_block(name: str = "Vinyl", qty: str = "2", descriptions=None, text: str | None = None) -> dict:
    out = {"name": name, "qty": qty, "descriptions": descriptions or ["LP", "Album"]}
    if text:
        out["text"] = text
    return out


def basic_information(
    release_id: int = 1001,
    master_id: int | None = 5001,
    title: str = "Selected Ambient Works 85-92",
    year: int = 1992,
    artists=None,
    labels=None,
    formats=None,
    genres=None,
    styles=None,
) -> dict:
    return {
        "id": release_id,
        "master_id": master_id,
        "title": title,
        "year": year,
        "artists": artists if artists is not None else [artist()],
        "labels": labels if labels is not None else [label_ref()],
        "formats": formats if formats is not None else [format_block()],
        "genres": genres if genres is not None else ["Electronic"],
        "styles": styles if styles is not None else ["Ambient", "IDM"],
        "resource_url": "https://api.discogs.com/releases/1001",
        "thumb": "https://example/thumb.jpg",
        "cover_image": "https://example/cover.jpg",
    }


def search_result(
    id: int = 1001,
    type: str = "release",
    title: str = "Aphex Twin - Selected Ambient Works 85-92",
    year: str = "1992",
    **kw: Any,
) -> dict:
    out = {
        "id": id,
        "type": type,
        "title": title,
        "year": year,
        "country": "UK",
        "format": ["Vinyl", "LP", "Album"],
        "label": ["Apollo"],
        "catno": "AMB 3922",
        "barcode": ["5021603992123"],
        "master_id": 5001,
        "master_url": "x",
        "resource_url": "x",
        "thumb": "https://example/thumb.jpg",
        "community": {"want": 500, "have": 1000},
    }
    out.update(kw)
    return out


def search_response(results=None, page: int = 1, pages: int = 1, items: int = 1, per_page: int = 25) -> dict:
    return {
        "pagination": {"page": page, "pages": pages, "per_page": per_page, "items": items, "urls": {}},
        "results": results if results is not None else [search_result()],
    }


def track(position: str = "A1", title: str = "Xtal", duration: str = "4:51", artists=None, extraartists=None) -> dict:
    out = {"position": position, "title": title, "duration": duration, "type_": "track"}
    if artists:
        out["artists"] = artists
    if extraartists:
        out["extraartists"] = extraartists
    return out


def release_detail(
    id: int = 1001,
    master_id: int = 5001,
    title: str = "Selected Ambient Works 85-92",
    year: int = 1992,
    country: str = "UK",
    tracklist=None,
    identifiers=None,
    notes: str | None = "Reissue.",
) -> dict:
    return {
        "id": id,
        "master_id": master_id,
        "title": title,
        "year": year,
        "released": f"{year}-00-00",
        "country": country,
        "artists": [artist()],
        "labels": [label_ref()],
        "formats": [format_block()],
        "genres": ["Electronic"],
        "styles": ["Ambient", "IDM"],
        "tracklist": tracklist if tracklist is not None else [track(), track(position="A2", title="Tha")],
        "extraartists": [{"name": "Richard D. James", "role": "Producer", "tracks": ""}],
        "companies": [{"entity_type_name": "Pressed By", "name": "MPO", "catno": ""}],
        "identifiers": identifiers if identifiers is not None else [
            {"type": "Barcode", "value": "5 021603 992123", "description": ""}
        ],
        "notes": notes,
        "community": {"have": 1000, "want": 500, "rating": {"average": 4.5, "count": 300}},
        "num_for_sale": 12,
        "lowest_price": 25.5,
        "uri": "https://www.discogs.com/release/1001",
        "resource_url": "x",
        "images": [{"uri": "https://example/img1.jpg"}],
    }


def master_detail(id: int = 5001, title: str = "Selected Ambient Works 85-92", year: int = 1992) -> dict:
    return {
        "id": id,
        "main_release": 1001,
        "most_recent_release": 1001,
        "artists": [artist()],
        "title": title,
        "year": year,
        "genres": ["Electronic"],
        "styles": ["Ambient"],
        "tracklist": [track()],
        "num_for_sale": 12,
        "lowest_price": 25.5,
        "uri": "https://www.discogs.com/master/5001",
        "resource_url": "x",
    }


def version(id: int = 1001, title: str = "Selected Ambient Works 85-92 (2xLP)") -> dict:
    return {
        "id": id,
        "title": title,
        "label": "Apollo",
        "catno": "AMB 3922",
        "country": "UK",
        "released": "1992",
        "format": "Vinyl, LP, Album",
        "major_formats": ["Vinyl"],
        "stats": {"community": {"in_collection": 1000, "in_wantlist": 500}},
        "resource_url": "x",
        "thumb": "x",
    }


def artist_detail(id: int = 45, name: str = "Aphex Twin (2)") -> dict:
    return {
        "id": id,
        "name": name,
        "realname": "Richard D. James",
        "profile": "Electronic musician.",
        "namevariations": ["AFX", "Aphex Twin"],
        "aliases": [{"id": 46, "name": "AFX", "resource_url": "x"}],
        "groups": [],
        "members": [],
        "urls": ["https://aphextwin.warp.net/"],
        "uri": "https://www.discogs.com/artist/45",
        "resource_url": "x",
    }


def discography_item(id: int = 1001, title: str = "Selected Ambient Works 85-92") -> dict:
    return {
        "id": id,
        "type": "release",
        "main_release": 1001,
        "artist": "Aphex Twin",
        "title": title,
        "year": 1992,
        "role": "Main",
        "label": "Apollo",
        "catno": "AMB 3922",
        "format": "Vinyl, LP, Album",
        "status": "Accepted",
        "stats": {"community": {"in_collection": 1000, "in_wantlist": 500}},
        "resource_url": "x",
    }


def label_detail(id: int = 50, name: str = "Warp Records") -> dict:
    return {
        "id": id,
        "name": name,
        "profile": "UK electronic label.",
        "contact_info": "",
        "parent_label": None,
        "sublabels": [],
        "urls": ["https://warp.net/"],
        "uri": "https://www.discogs.com/label/50",
        "resource_url": "x",
    }


def identity(id: int = 1, username: str = "tester") -> dict:
    return {"id": id, "username": username, "resource_url": "x", "consumer_name": "discogs-mcp"}


def profile(username: str = "tester") -> dict:
    return {
        "username": username,
        "name": "Test User",
        "location": "Nowhere",
        "registered": "2010-01-01T00:00:00",
        "num_collection": 2,
        "num_wantlist": 1,
        "num_lists": 0,
        "curr_abbr": "USD",
        "buyer_rating": "100.0",
        "seller_rating": "100.0",
        "releases_contributed": 0,
        "uri": "https://www.discogs.com/user/tester",
        "avatar_url": "x",
    }


def collection_folder(id: int = 1, name: str = "Uncategorized", count: int = 2) -> dict:
    return {"id": id, "name": name, "count": count, "resource_url": "x"}


def collection_field(id: int = 1, name: str = "Media Condition", options=None) -> dict:
    return {
        "id": id,
        "name": name,
        "type": "dropdown",
        "options": options or ["Mint (M)", "Near Mint (NM or M-)", "Very Good Plus (VG+)"],
        "position": 1,
        "public": True,
    }


def collection_item(
    instance_id: int = 9001,
    release_id: int = 1001,
    folder_id: int = 1,
    rating: int = 4,
    date_added: str = "2024-01-01T00:00:00-08:00",
    notes=None,
    basic=None,
) -> dict:
    return {
        "instance_id": instance_id,
        "id": release_id,
        "folder_id": folder_id,
        "rating": rating,
        "date_added": date_added,
        "notes": notes if notes is not None else [{"field_id": 1, "value": "Near Mint (NM or M-)"}],
        "basic_information": basic if basic is not None else basic_information(release_id=release_id),
    }


def want_item(release_id: int = 2002, rating: int = 0, notes: str | None = None, basic=None) -> dict:
    return {
        "id": release_id,
        "rating": rating,
        "notes": notes,
        "date_added": "2024-02-01T00:00:00-08:00",
        "basic_information": basic if basic is not None else basic_information(
            release_id=release_id, title="Druqks", master_id=5002
        ),
    }


def price(value: float = 25.5, currency: str = "USD") -> dict:
    return {"value": value, "currency": currency}


def stats_response(lowest=None, num_for_sale: int = 12) -> dict:
    return {"lowest_price": lowest if lowest is not None else price(), "num_for_sale": num_for_sale, "blocked_from_sale": False}


def price_suggestions() -> dict:
    return {
        "Mint (M)": price(40.0),
        "Near Mint (NM or M-)": price(32.0),
        "Very Good Plus (VG+)": price(20.0),
    }


def collection_value() -> dict:
    return {"minimum": "$100.00", "median": "$150.00", "maximum": "$200.00"}


def paged(items: list, key: str, page: int = 1, pages: int = 1, per_page: int = 50) -> dict:
    return {
        "pagination": {"page": page, "pages": pages, "items": len(items), "per_page": per_page, "urls": {}},
        key: items,
    }
