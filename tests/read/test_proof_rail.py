"""The proof rail: dates in, segments out, and one inline SVG that names no colour.

Every expectation is a known answer decided with the input, in days from a fixed origin, so a
fault shows as a disagreement and not as a picture to be judged. Nothing here reads the clock.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from obdi.read.proof_rail import (
    AGREE,
    BREAK,
    UNKNOWN,
    UNPROVEN,
    Rail,
    RailMark,
    RailSegment,
    build_rail,
    rail_svg,
    rail_text,
)

ORIGIN = date(2024, 1, 1)
TODAY = date(2024, 12, 31)


def at(days: int) -> date:
    return ORIGIN + timedelta(days=days)


def rail(**given: object) -> Rail:
    base: dict[str, object] = {
        "first": at(0),
        "known_from": None,
        "known_to": None,
        "through": None,
        "held_day": None,
        "protected_through": None,
        "protection_broken": False,
        "today": TODAY,
    }
    base.update(given)
    return build_rail(**base)  # type: ignore[arg-type]


def shape(built: Rail) -> list[tuple[str, int, int]]:
    return [(s.kind, (s.start - ORIGIN).days, (s.end - ORIGIN).days) for s in built.segments]


def test_Rail_WithNoKnownBalance_IsOneUnknownStretchFromTheFirstRowToToday() -> None:
    built = rail()
    assert shape(built) == [(UNKNOWN, 0, 365)]
    assert built.protected is None


def test_Rail_WithNothingAtAll_IsAnEmptyStretchEndingToday() -> None:
    built = rail(first=None)
    assert built.start == built.end == TODAY
    assert shape(built) == [(UNKNOWN, 365, 365)]


def test_Rail_WithOneKnownBalance_ShowsUnknownOnBothSidesAndNothingProven() -> None:
    built = rail(known_from=at(100), known_to=at(100))
    assert shape(built) == [(UNKNOWN, 0, 100), (UNKNOWN, 100, 365)]
    assert not any(s.kind in (AGREE, UNPROVEN) for s in built.segments)


def test_Rail_InAgreementThroughTheLastKnownBalance_IsSolidFromFirstToLastKnown() -> None:
    built = rail(known_from=at(30), known_to=at(300), through=at(300))
    assert shape(built) == [(UNKNOWN, 0, 30), (AGREE, 30, 300), (UNKNOWN, 300, 365)]


def test_Rail_HeldBackInTheMiddle_BreaksThereAndLeavesTheRestUnproven() -> None:
    built = rail(known_from=at(30), known_to=at(300), through=at(120), held_day=at(150))
    assert shape(built) == [
        (UNKNOWN, 0, 30),
        (AGREE, 30, 120),
        (UNPROVEN, 120, 300),
        (UNKNOWN, 300, 365),
        (BREAK, 150, 150),
    ]


def test_Rail_HeldBackBeforeAnyAgreement_HasNoSolidStretch() -> None:
    built = rail(known_from=at(30), known_to=at(300), through=None, held_day=at(60))
    assert shape(built) == [
        (UNKNOWN, 0, 30),
        (UNPROVEN, 30, 300),
        (UNKNOWN, 300, 365),
        (BREAK, 60, 60),
    ]


def test_Rail_ProtectedPartWay_MarksTheDayWithinTheSolidStretch() -> None:
    built = rail(
        known_from=at(30), known_to=at(300), through=at(300), protected_through=at(200)
    )
    assert built.protected == RailMark(at(200), broken=False)


def test_Rail_WithABrokenProtection_MarksItAsBrokenAndNotAsAbsent() -> None:
    built = rail(
        known_from=at(30),
        known_to=at(300),
        through=at(300),
        protected_through=at(200),
        protection_broken=True,
    )
    assert built.protected == RailMark(at(200), broken=True)
    assert "but broken" in rail_text(built)


def test_Rail_ProtectedThroughALaterDayThanToday_IsKeptWithinTheBar() -> None:
    built = rail(known_from=at(30), known_to=at(300), through=at(300), protected_through=at(900))
    assert built.protected == RailMark(TODAY, broken=False)


def test_Rail_WhenTheFirstRowIsAfterTheFirstKnownBalance_StartsAtTheKnownBalance() -> None:
    built = rail(first=at(50), known_from=at(10), known_to=at(20), through=at(20))
    assert built.start == at(10)


def test_Rail_WhenKnownBalancesEndAfterToday_ExtendsToTheLastKnownDay() -> None:
    built = rail(known_from=at(30), known_to=at(400), through=at(400))
    assert built.end == at(400)
    assert shape(built) == [(UNKNOWN, 0, 30), (AGREE, 30, 400)]


@pytest.mark.parametrize(
    "inconsistent",
    [
        {"known_from": at(10), "known_to": at(5)},
        {"known_from": at(10)},
        {"known_from": at(10), "known_to": at(20), "through": at(30)},
        {"through": at(30)},
    ],
)
def test_Rail_WithDatesThatCannotBelongToOneAccount_FailsLoudly(
    inconsistent: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        rail(**inconsistent)


def test_RailSvg_NamesNoColourAndCarriesAClassForEachPart() -> None:
    built = rail(
        known_from=at(30),
        known_to=at(300),
        through=at(120),
        held_day=at(150),
        protected_through=at(100),
    )
    svg = rail_svg(built, uid="acct")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", svg.replace("url(#", "url("))
    for expected in ("rail-agree", "rail-unproven", "rail-unknown", "rail-break", "rail-mark"):
        assert expected in svg


def test_RailSvg_HasATextAlternativeThatSaysWhatTheBarShows() -> None:
    built = rail(known_from=at(30), known_to=at(300), through=at(120), held_day=at(150))
    svg = rail_svg(built)
    assert 'role="img"' in svg
    assert "<title" in svg
    text = rail_text(built)
    assert "The transactions stop adding up at 2024-05-30." in text
    assert "protected" not in text.casefold(), "an account nothing protects says nothing of it"
    assert "The transactions add up to the known balances from 2024-01-31 to 2024-04-30." in text
    assert (
        "From 2024-04-30 to 2024-10-27 the transactions are not shown to add up to the known "
        "balance for 2024-10-27."
    ) in text


def test_RailSvg_WithTwoRailsOnOnePage_GivesEachItsOwnPatternIds() -> None:
    built = rail(known_from=at(30), known_to=at(30))
    one, two = rail_svg(built, uid="a"), rail_svg(built, uid="b")
    assert 'id="a-unknown"' in one and 'id="b-unknown"' in two
    assert 'id="a-unknown"' not in two


def test_RailSvg_PlacesASegmentByItsShareOfTheSpan() -> None:
    built = Rail(
        at(0), at(100), (RailSegment(AGREE, at(25), at(75)),), None
    )
    svg = rail_svg(built)
    assert 'x="25.000%"' in svg
    assert 'width="50.000%"' in svg


def test_RailSvg_WithAnEscapedUid_CannotBreakOutOfTheAttribute() -> None:
    svg = rail_svg(rail(), uid='x"><script>')
    assert "<script>" not in svg
