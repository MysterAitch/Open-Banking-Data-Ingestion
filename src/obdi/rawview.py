"""Computed metadata over a raw payload: shape, presence, spread, and pattern.

Browsing rarely means "show me everything". The recurring questions are: what
shape is this payload, which fields are actually populated, what do they
contain, and do the fields agree with each other? Concretely:

- a field with a handful of values wants those values counted, not ranged;
- an opaque identifier wants its shape described (cardinality, length,
  common prefix, recognisable format) - a lexicographic min/max of ids is
  noise;
- date-like fields keep their range, because that range is the window
  evidence this project keeps reasoning from;
- the sign of the amount cross-tabulated against each categorical field is
  the sign-convention check that every parser bug of this project has
  eventually come down to;
- presence patterns ("this field is absent exactly when that one says
  TRANSFER") surface provider semantics nothing documents;
- items per month makes a gap in the data visible as a missing bar.

Deliberately NOT here: spending analytics (amount histograms, category
totals). Those belong to the budgeting app; this page is evidence about the
payload, not insight about the money.

Read-only over bytes that are never modified: this is analysis of layer 0,
not a transformation of it.
"""

from __future__ import annotations

import json
import re
from typing import Any

# One level of nesting, dotted. Deeper structures exist (and the payload view
# shows them verbatim); the summary stays shallow because a table of every
# leaf of a deep tree stops being a summary.
_MAX_DEPTH = 2

# A field with this many values or fewer is a category: enumerate and tally.
_ENUM_MAX = 10

# Presence patterns are only claimed for groups big enough to mean something.
_LINK_MIN_SUBSET = 5

_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "uuid",
        re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
    ),
    ("hex", re.compile(r"^[0-9a-f]{16,}$")),
    ("numeric", re.compile(r"^\d+$")),
    ("iso-datetime", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")),
)


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


def _common_prefix(values: list[str]) -> str:
    if len(values) < 2:
        return ""
    first, last = min(values), max(values)
    prefix = []
    for a, b in zip(first, last, strict=False):
        if a != b:
            break
        prefix.append(a)
    return "".join(prefix)


def _string_format(values: list[str]) -> str | None:
    for name, pattern in _FORMATS:
        if all(pattern.match(v) for v in values):
            return name
    return None


def _field_entry(path: str, entry: dict[str, Any]) -> dict[str, Any]:
    strings: list[str] = entry["strings"]
    numbers: list[float] = entry["numbers"]
    tallies: dict[str, int] = entry["tallies"]
    distinct = len(tallies)
    date_like = bool(strings) and all(_DATE_LIKE.match(s) for s in strings)

    minimum: Any = None
    maximum: Any = None
    if numbers and not strings:
        minimum, maximum = min(numbers), max(numbers)
    elif strings and not numbers and (date_like or distinct <= _ENUM_MAX):
        # A range over identifiers or free text is an invention; over dates
        # and small categories it is an observation.
        minimum, maximum = min(strings), max(strings)

    field: dict[str, Any] = {
        "path": path,
        "present": entry["present"],
        "types": sorted(entry["types"]),
        "distinct": distinct,
        "min": minimum,
        "max": maximum,
    }
    if 0 < distinct <= _ENUM_MAX:
        field["values"] = [
            {"value": value, "count": count}
            for value, count in sorted(tallies.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    elif strings and not date_like:
        lengths = [len(s) for s in strings]
        field["length"] = {"min": min(lengths), "max": max(lengths)}
        prefix = _common_prefix(strings)
        if len(prefix) >= 2:
            field["prefix"] = prefix
        fmt = _string_format(strings)
        if fmt:
            field["format"] = fmt
    return field


def _sign(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def summarise(payload: bytes, media_type: str) -> dict[str, Any]:
    """Shape and per-field analysis of a payload, without modifying a byte."""
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

    flats = [dict(_flatten(item)) for item in items]

    stats: dict[str, dict[str, Any]] = {}
    for flat in flats:
        for path, value in flat.items():
            entry = stats.setdefault(
                path,
                {"present": 0, "types": set(), "numbers": [], "strings": [], "tallies": {}},
            )
            entry["present"] += 1
            entry["types"].add(_type_name(value))
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                entry["numbers"].append(value)
                key = json.dumps(value)
            elif isinstance(value, str):
                entry["strings"].append(value)
                key = value
            else:
                continue
            entry["tallies"][key] = entry["tallies"].get(key, 0) + 1

    fields = [_field_entry(path, stats[path]) for path in sorted(stats)]
    by_field = {f["path"]: f for f in fields}

    categoricals = [
        f["path"]
        for f in fields
        if f.get("values") and "string" in f["types"] and f["path"] != "amount"
    ]

    # Sign agreement: does the categorical say what the sign says? Every
    # parser sign bug this project has had would show here as a mixed row.
    sign_by: list[dict[str, Any]] = []
    if "amount" in by_field and by_field["amount"]["types"] == ["number"]:
        for cat in categoricals:
            counts: dict[str, dict[str, int]] = {}
            for flat in flats:
                amount = flat.get("amount")
                value = flat.get(cat)
                if isinstance(amount, bool) or not isinstance(amount, int | float):
                    continue
                if not isinstance(value, str):
                    continue
                row = counts.setdefault(value, {"positive": 0, "negative": 0, "zero": 0})
                row[_sign(amount)] += 1
            sign_by.extend(
                {"field": cat, "value": value, **row}
                for value, row in sorted(counts.items())
            )

    # Presence patterns: a partially-present field that is ALWAYS or NEVER
    # present for one category value is provider semantics worth surfacing
    # ("internal transfers carry no provider reference").
    presence_links: list[dict[str, Any]] = []
    partials = [
        f["path"] for f in fields if 0 < f["present"] < len(items)
    ]
    for partial in partials:
        for cat in categoricals:
            if cat == partial:
                continue
            groups: dict[str, tuple[int, int]] = {}
            for flat in flats:
                value = flat.get(cat)
                if not isinstance(value, str):
                    continue
                present, total = groups.get(value, (0, 0))
                groups[value] = (present + (partial in flat), total + 1)
            for value, (present, total) in sorted(groups.items()):
                if total >= _LINK_MIN_SUBSET and present in (0, total):
                    presence_links.append(
                        {
                            "field": partial,
                            "by": cat,
                            "value": value,
                            "present": present,
                            "total": total,
                            "overall_present": by_field[partial]["present"],
                        }
                    )

    # Items per month from the timestamp-shaped field: a missing month is a
    # missing bar, which is how "June is not there" becomes visible.
    def _date_shaped(f: dict[str, Any]) -> bool:
        return bool(
            f["present"] == len(items)
            and not f.get("length")
            and f["types"] == ["string"]
            and f["min"]
            and _DATE_LIKE.match(str(f["min"]))
        )

    date_field = next(
        (f["path"] for f in fields if f["path"] == "timestamp" or _date_shaped(f)),
        None,
    )
    months: dict[str, int] = {}
    if date_field:
        for flat in flats:
            value = flat.get(date_field)
            if isinstance(value, str) and _DATE_LIKE.match(value):
                months[value[:7]] = months.get(value[:7], 0) + 1
    by_month = [{"month": m, "count": c} for m, c in sorted(months.items())]

    return {
        "kind": "json",
        "items": len(items),
        "bytes": len(payload),
        "fields": fields,
        "sign_by": sign_by,
        "presence_links": presence_links,
        "by_month": by_month,
    }


def settlement_lag_report(rows: list[dict[str, object]]) -> dict[str, object]:
    """Lag between economic and settlement time, and what it crosses.

    Input rows are raw Starling feed items (each carries transactionTime
    and settlementTime). A payment's week is its ISO week; a lag "crosses"
    when the two stamps fall in different weeks or months - the exact
    cases where week-to-week or month-boundary reporting would file the
    payment under the wrong period if only settlement were recorded.
    """
    from datetime import datetime

    lags: dict[str, int] = {}
    week_crossings = 0
    month_crossings = 0
    measured = 0
    for row in rows:
        raw_txn = str(row.get("transactionTime", "") or "")
        raw_settle = str(row.get("settlementTime", "") or "")
        if not raw_txn or not raw_settle:
            continue
        try:
            happened = datetime.fromisoformat(raw_txn.replace("Z", "+00:00"))
            settled = datetime.fromisoformat(raw_settle.replace("Z", "+00:00"))
        except ValueError:
            continue
        measured += 1
        lag_days = (settled.date() - happened.date()).days
        bucket = (
            "same-day"
            if lag_days <= 0
            else f"{lag_days}d" if lag_days <= 3 else "4d+"
        )
        lags[bucket] = lags.get(bucket, 0) + 1
        if happened.isocalendar()[:2] != settled.isocalendar()[:2]:
            week_crossings += 1
        if (happened.year, happened.month) != (settled.year, settled.month):
            month_crossings += 1
    return {
        "measured": measured,
        "lags": dict(sorted(lags.items())),
        "week_crossings": week_crossings,
        "month_crossings": month_crossings,
    }
