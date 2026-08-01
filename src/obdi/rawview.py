"""Computed metadata over a raw payload: shape, presence, and spread.

Browsing rarely means "show me everything". The recurring questions are: what
shape is this payload, which fields are actually populated, and what range do
they cover? Presence counts answer "does this provider really send that
optional field?"; min and max per field answer the date span a window actually
returned and the extremes of any numeric column - the questions this project
keeps asking of its own data.

Read-only over bytes that are never modified: this is analysis of layer 0,
not a transformation of it.
"""

from __future__ import annotations

import json
from typing import Any

# One level of nesting, dotted. Deeper structures exist (and the payload view
# shows them verbatim); the summary stays shallow because a table of every
# leaf of a deep tree stops being a summary.
_MAX_DEPTH = 2


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _flatten(item: dict[str, Any], prefix: str = "", depth: int = 1) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    for key, value in item.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and depth < _MAX_DEPTH:
            pairs.extend(_flatten(value, f"{path}.", depth + 1))
        else:
            pairs.append((path, value))
    return pairs


def summarise(payload: bytes, media_type: str) -> dict[str, Any]:
    """Shape and per-field spread of a payload, without modifying a byte."""
    if media_type != "application/json":
        return {"kind": media_type, "bytes": len(payload), "fields": []}
    try:
        decoded = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return {"kind": "unparseable", "bytes": len(payload), "fields": []}

    if isinstance(decoded, dict) and isinstance(decoded.get("results"), list):
        items = [item for item in decoded["results"] if isinstance(item, dict)]
    elif isinstance(decoded, list):
        items = [item for item in decoded if isinstance(item, dict)]
    elif isinstance(decoded, dict):
        items = [decoded]
    else:
        items = []

    stats: dict[str, dict[str, Any]] = {}
    for item in items:
        for path, value in _flatten(item):
            entry = stats.setdefault(
                path, {"present": 0, "types": set(), "numbers": [], "strings": []}
            )
            entry["present"] += 1
            entry["types"].add(_type_name(value))
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                entry["numbers"].append(value)
            elif isinstance(value, str):
                entry["strings"].append(value)

    fields = []
    for path in sorted(stats):
        entry = stats[path]
        # A range over mixed types would be an invention, not an observation:
        # min("two", 1) has no meaning worth printing. Ranges are reported only
        # when every observed value shares one comparable type.
        minimum: Any = None
        maximum: Any = None
        if entry["numbers"] and not entry["strings"]:
            minimum, maximum = min(entry["numbers"]), max(entry["numbers"])
        elif entry["strings"] and not entry["numbers"]:
            minimum, maximum = min(entry["strings"]), max(entry["strings"])
        fields.append(
            {
                "path": path,
                "present": entry["present"],
                "types": sorted(entry["types"]),
                "min": minimum,
                "max": maximum,
            }
        )

    return {"kind": "json", "items": len(items), "bytes": len(payload), "fields": fields}
