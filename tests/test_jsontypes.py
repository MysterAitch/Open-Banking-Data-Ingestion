"""Narrowing untrusted payloads at the boundary.

`JsonObject` is `dict[str, object]` rather than `dict[str, Any]` deliberately.
`object` behaves like TypeScript's `unknown`: it accepts anything inbound and
permits nothing outbound without a narrowing step, so a provider changing a
field's type fails here, named, rather than misbehaving somewhere further in.
"""

import pytest

from obdi.jsontypes import (
    JsonShapeError,
    as_list_of_objects,
    as_object,
    nested,
    rows,
    text,
    whole_number,
)


class TestObjects:
    def test_Payload_WhenAnObject_Accepted(self):
        assert as_object({"a": 1}, field="root") == {"a": 1}

    def test_Payload_WhenNotAnObject_RefusedNamingTheField(self):
        with pytest.raises(JsonShapeError, match="root"):
            as_object([1, 2], field="root")

    def test_Payload_WhenAListOfObjects_Accepted(self):
        assert as_list_of_objects([{"a": 1}], field="results") == [{"a": 1}]

    def test_Payload_WhenListContainsSomethingElse_Refused(self):
        with pytest.raises(JsonShapeError):
            as_list_of_objects([{"a": 1}, "not an object"], field="results")


class TestStrings:
    def test_Field_WhenAString_Returned(self):
        assert text({"id": "abc"}, "id") == "abc"

    def test_Field_WhenAbsent_DefaultUsed(self):
        assert text({}, "id", default="none") == "none"

    def test_Field_WhenANumber_RefusedRatherThanCoerced(self):
        # A provider that starts sending an identifier as a number has changed
        # its contract. Quietly stringing it would hide that until ids silently
        # stopped matching.
        with pytest.raises(JsonShapeError, match="id"):
            text({"id": 12345}, "id")


class TestWholeNumbers:
    def test_Field_WhenAnInteger_Returned(self):
        assert whole_number({"minorUnits": 1499}, "minorUnits") == 1499

    def test_Field_WhenAbsent_ReturnsNothing(self):
        assert whole_number({}, "minorUnits") is None

    def test_Field_WhenAFloat_Refused(self):
        # Money must never arrive as a float.
        with pytest.raises(JsonShapeError):
            whole_number({"minorUnits": 14.99}, "minorUnits")

    def test_Field_WhenABoolean_Refused(self):
        # bool subclasses int in Python, so a stray true would otherwise pass
        # as the number one.
        with pytest.raises(JsonShapeError):
            whole_number({"minorUnits": True}, "minorUnits")


class TestNestedAccess:
    def test_Field_WhenNestedObjectPresent_Returned(self):
        assert nested({"amount": {"currency": "GBP"}}, "amount") == {"currency": "GBP"}

    def test_Field_WhenNestedObjectAbsent_EmptyObject(self):
        assert nested({}, "amount") == {}

    def test_Field_WhenNestedIsNotAnObject_Refused(self):
        with pytest.raises(JsonShapeError):
            nested({"amount": "1499"}, "amount")

    def test_Field_WhenRowsPresent_Returned(self):
        assert rows({"results": [{"id": "a"}]}, "results") == [{"id": "a"}]

    def test_Field_WhenRowsAbsent_EmptyList(self):
        assert rows({}, "results") == []
