"""Types for data that arrives from outside, and how to narrow it safely.

A decoded JSON payload is the Python equivalent of TypeScript's `any`: the
type checker knows nothing, so every field access is unchecked and a provider
changing a type is invisible until something downstream misbehaves.

`JsonObject` is deliberately `dict[str, object]` rather than `dict[str, Any]`.
`object` behaves like TypeScript's `unknown` - it accepts anything on the way
in and permits nothing on the way out without a narrowing step. `Any` would
silently pass every use site and defeat the point of checking at all.

The accessors below are that narrowing step, in one place, so a provider
returning a number where a string was expected fails at the boundary with a
message naming the field rather than somewhere further in.
"""

from __future__ import annotations

from typing import TypeAlias

#: A decoded JSON object. Untrusted and unnarrowed by design.
JsonObject: TypeAlias = dict[str, object]


class JsonShapeError(ValueError):
    """A payload did not have the shape the caller required."""


def as_object(value: object, *, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise JsonShapeError(f"{field}: expected an object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def as_list_of_objects(value: object, *, field: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise JsonShapeError(f"{field}: expected a list, got {type(value).__name__}")
    return [as_object(item, field=f"{field}[]") for item in value]


def text(payload: JsonObject, key: str, *, default: str = "") -> str:
    """A string field, tolerating absence but not a wrong type.

    Numbers are NOT coerced: a provider that starts sending an identifier as a
    number rather than a string has changed its contract, and quietly stringing
    it would hide that until the ids stopped matching.
    """
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise JsonShapeError(f"{key}: expected a string, got {type(value).__name__}")
    return value


def whole_number(payload: JsonObject, key: str) -> int | None:
    """An integer field, refusing a float.

    `bool` is excluded explicitly because it is a subclass of `int` in Python,
    so a stray `true` would otherwise pass as 1.
    """
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise JsonShapeError(f"{key}: expected a whole number, got {type(value).__name__}")
    return value


def nested(payload: JsonObject, key: str) -> JsonObject:
    """A nested object, or an empty one when absent."""
    value = payload.get(key)
    if value is None:
        return {}
    return as_object(value, field=key)


def rows(payload: JsonObject, key: str) -> list[JsonObject]:
    """A list of objects under `key`, or an empty list when absent."""
    value = payload.get(key)
    if value is None:
        return []
    return as_list_of_objects(value, field=key)
