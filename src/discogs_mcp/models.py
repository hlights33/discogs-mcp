"""Trim Discogs responses down to what is useful in Claude's context."""

from __future__ import annotations

import re
from typing import Any

NOISE_KEYS = {"resource_url", "thumbnail_url", "uri150", "data_quality", "submitter", "contributors"}
IMAGE_KEYS = {"images", "thumb", "cover_image", "image", "uri", "avatar_url", "banner_url"}
_DISAMBIGUATOR = re.compile(r"\s+\(\d+\)$")


def strip_noise(obj: Any, *, keep_images: bool = False) -> Any:
    """Recursively drop resource URLs, image URIs and empty values."""
    drop = NOISE_KEYS if keep_images else NOISE_KEYS | IMAGE_KEYS
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in drop:
                continue
            v = strip_noise(v, keep_images=keep_images)
            if v in (None, "", [], {}):
                continue
            out[k] = v
        return out
    if isinstance(obj, list):
        return [strip_noise(v, keep_images=keep_images) for v in obj]
    return obj


def clean_name(name: str | None) -> str:
    """'Aphex Twin (2)' -> 'Aphex Twin'. Discogs appends numbers to disambiguate artists."""
    return _DISAMBIGUATOR.sub("", name or "").strip()


def artist_credit(artists: list[dict[str, Any]] | None) -> str:
    """Render a Discogs artist list ('A', join='&', 'B') as a single credit string."""
    if not artists:
        return ""
    parts = []
    for a in artists:
        parts.append(clean_name(a.get("anv") or a.get("name")))
        join = (a.get("join") or "").strip()
        if join:
            parts.append(" , " if join == "," else f" {join} ")
    return "".join(parts).replace(" , ", ", ").strip(" ,&")


def format_summary(formats: list[dict[str, Any]] | None) -> str:
    """[{name: Vinyl, qty: 2, descriptions: [LP, Album]}] -> '2×Vinyl (LP, Album)'."""
    out = []
    for f in formats or []:
        qty = str(f.get("qty") or "1")
        label = f"{qty}×{f.get('name', '')}" if qty not in ("1", "") else f.get("name", "")
        desc = [*(f.get("descriptions") or [])]
        if f.get("text"):
            desc.append(f["text"])
        out.append(f"{label} ({', '.join(desc)})" if desc else label)
    return "; ".join(out)


def labels_summary(labels: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {"id": lb.get("id"), "name": clean_name(lb.get("name")), "catno": lb.get("catno")}
        for lb in labels or []
    ]


def basic_release(info: dict[str, Any]) -> dict[str, Any]:
    """Shape a collection/wantlist `basic_information` block."""
    return strip_noise(
        {
            "release_id": info.get("id"),
            "master_id": info.get("master_id") or None,
            "artist": artist_credit(info.get("artists")),
            "title": info.get("title"),
            "year": info.get("year") or None,
            "labels": labels_summary(info.get("labels")),
            "format": format_summary(info.get("formats")),
            "genres": info.get("genres"),
            "styles": info.get("styles"),
        }
    )


def search_result(r: dict[str, Any]) -> dict[str, Any]:
    return strip_noise(
        {
            "id": r.get("id"),
            "type": r.get("type"),
            "title": r.get("title"),
            "year": r.get("year"),
            "country": r.get("country"),
            "format": ", ".join(r.get("format") or []) or None,
            "label": (r.get("label") or [None])[0],
            "catno": r.get("catno"),
            "barcode": (r.get("barcode") or [])[:2],
            "master_id": r.get("master_id") or None,
            "have": (r.get("community") or {}).get("have"),
            "want": (r.get("community") or {}).get("want"),
        }
    )


def _track(t: dict[str, Any]) -> dict[str, Any]:
    out = {
        "position": t.get("position"),
        "title": t.get("title"),
        "duration": t.get("duration"),
        "artist": artist_credit(t.get("artists")) or None,
        "credits": [f"{clean_name(c.get('name'))} ({c.get('role')})" for c in t.get("extraartists") or []],
    }
    if t.get("type_") and t["type_"] != "track":
        out["type"] = t["type_"]
    if t.get("sub_tracks"):
        out["sub_tracks"] = [_track(s) for s in t["sub_tracks"]]
    return strip_noise(out)


def release_detail(r: dict[str, Any], *, keep_images: bool = False) -> dict[str, Any]:
    community = r.get("community") or {}
    out = {
        "id": r.get("id"),
        "master_id": r.get("master_id"),
        "artist": artist_credit(r.get("artists")),
        "title": r.get("title"),
        "year": r.get("year") or None,
        "released": r.get("released"),
        "country": r.get("country"),
        "labels": labels_summary(r.get("labels")),
        "format": format_summary(r.get("formats")),
        "genres": r.get("genres"),
        "styles": r.get("styles"),
        "tracklist": [_track(t) for t in r.get("tracklist") or []],
        "credits": [
            {"name": clean_name(c.get("name")), "role": c.get("role"), "tracks": c.get("tracks") or None}
            for c in r.get("extraartists") or []
        ],
        "companies": [
            f"{c.get('entity_type_name')}: {clean_name(c.get('name'))}"
            + (f" ({c['catno']})" if c.get("catno") else "")
            for c in r.get("companies") or []
        ],
        "identifiers": [
            {"type": i.get("type"), "value": i.get("value"), "description": i.get("description")}
            for i in r.get("identifiers") or []
        ],
        "notes": r.get("notes"),
        "community": {
            "have": community.get("have"),
            "want": community.get("want"),
            "rating": (community.get("rating") or {}).get("average"),
            "rating_count": (community.get("rating") or {}).get("count"),
        },
        "num_for_sale": r.get("num_for_sale"),
        "lowest_price": r.get("lowest_price"),
        "url": r.get("uri"),
    }
    if keep_images:
        out["images"] = [i.get("uri") for i in r.get("images") or []]
    return strip_noise(out, keep_images=True)


def master_detail(m: dict[str, Any], *, keep_images: bool = False) -> dict[str, Any]:
    out = {
        "id": m.get("id"),
        "main_release": m.get("main_release"),
        "most_recent_release": m.get("most_recent_release"),
        "artist": artist_credit(m.get("artists")),
        "title": m.get("title"),
        "year": m.get("year"),
        "genres": m.get("genres"),
        "styles": m.get("styles"),
        "tracklist": [_track(t) for t in m.get("tracklist") or []],
        "num_for_sale": m.get("num_for_sale"),
        "lowest_price": m.get("lowest_price"),
        "url": m.get("uri"),
    }
    if keep_images:
        out["images"] = [i.get("uri") for i in m.get("images") or []]
    return strip_noise(out, keep_images=True)


def version_summary(v: dict[str, Any]) -> dict[str, Any]:
    stats = (v.get("stats") or {}).get("community") or {}
    return strip_noise(
        {
            "id": v.get("id"),
            "title": v.get("title"),
            "label": v.get("label"),
            "catno": v.get("catno"),
            "country": v.get("country"),
            "released": v.get("released"),
            "format": v.get("format"),
            "major_formats": v.get("major_formats"),
            "have": stats.get("in_collection"),
            "want": stats.get("in_wantlist"),
        }
    )


def member_list(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        strip_noise({"id": i.get("id"), "name": clean_name(i.get("name")), "active": i.get("active")})
        for i in items or []
    ]


def artist_detail(a: dict[str, Any], *, keep_images: bool = False) -> dict[str, Any]:
    out = {
        "id": a.get("id"),
        "name": clean_name(a.get("name")),
        "real_name": a.get("realname"),
        "profile": a.get("profile"),
        "name_variations": a.get("namevariations"),
        "aliases": member_list(a.get("aliases")),
        "groups": member_list(a.get("groups")),
        "members": member_list(a.get("members")),
        "urls": a.get("urls"),
        "url": a.get("uri"),
    }
    if keep_images:
        out["images"] = [i.get("uri") for i in a.get("images") or []]
    return strip_noise(out, keep_images=True)


def label_detail(lb: dict[str, Any], *, keep_images: bool = False) -> dict[str, Any]:
    parent = lb.get("parent_label")
    out = {
        "id": lb.get("id"),
        "name": clean_name(lb.get("name")),
        "profile": lb.get("profile"),
        "contact_info": lb.get("contact_info"),
        "parent_label": {"id": parent.get("id"), "name": clean_name(parent.get("name"))} if parent else None,
        "sublabels": member_list(lb.get("sublabels")),
        "urls": lb.get("urls"),
        "url": lb.get("uri"),
    }
    if keep_images:
        out["images"] = [i.get("uri") for i in lb.get("images") or []]
    return strip_noise(out, keep_images=True)


def discography_item(r: dict[str, Any]) -> dict[str, Any]:
    stats = (r.get("stats") or {}).get("community") or {}
    return strip_noise(
        {
            "id": r.get("id"),
            "type": r.get("type"),
            "main_release": r.get("main_release"),
            "artist": r.get("artist"),
            "title": r.get("title"),
            "year": r.get("year"),
            "role": r.get("role"),
            "label": r.get("label"),
            "catno": r.get("catno"),
            "format": r.get("format"),
            "status": r.get("status"),
            "have": stats.get("in_collection"),
            "want": stats.get("in_wantlist"),
        }
    )


def price(p: dict[str, Any] | None) -> str | None:
    if not p or p.get("value") is None:
        return None
    return f"{p['value']:.2f} {p.get('currency', '')}".strip()
