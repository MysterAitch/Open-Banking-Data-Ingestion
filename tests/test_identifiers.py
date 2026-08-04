"""Matching one real account across sources that know different things."""

from __future__ import annotations

import pathlib
import re

import pytest

from obdi.identifiers import (
    STRONG,
    WEAK,
    AccountLastFour,
    AccountNumber,
    CardLastFour,
    Claim,
    Iban,
    UkAccount,
    best_match,
    claims_from_file_hints,
    claims_from_starling_identifiers,
    claims_from_truelayer_account,
    claims_from_truelayer_card,
    derive,
)


class TestSourcesClaimWhatTheyKnow:
    def test_ATrueLayerAccount_ProvesItsSortCodeAndNumber(self):
        claims = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "20-00-00"}}
        )

        strong = [c for c in claims if c.strength == STRONG]
        assert isinstance(strong[0].identifier, UkAccount)
        assert strong[0].identifier.reveal() == "200000:12345678"

    def test_ATrueLayerCard_ProvesOnlyItsLastFour_AndSaysSoWeakly(self):
        claims = claims_from_truelayer_card({"partial_card_number": "8484"})

        assert len(claims) == 1
        assert isinstance(claims[0].identifier, CardLastFour)
        assert claims[0].strength == WEAK

    def test_StarlingIdentifiers_ProveTheSameThingsAsTrueLayer(self):
        claims = claims_from_starling_identifiers(
            {
                "accountIdentifier": "12345678",
                "bankIdentifier": "608371",
                "iban": "GB33 STRL 6083 7112 3456 78",
            }
        )

        kinds = {type(c.identifier) for c in claims}
        assert UkAccount in kinds
        assert Iban in kinds

    def test_ASourceThatKnowsNothing_ClaimsNothing(self):
        assert claims_from_truelayer_account({"account_number": {}}) == []
        assert claims_from_truelayer_card({}) == []
        assert claims_from_file_hints({}) == []


class TestStrongerClaimsContainWeakerOnes:
    def test_AFullNumber_AlsoProvesItsLastFour(self):
        claims = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "200000"}}
        )

        assert any(
            isinstance(c.identifier, AccountLastFour)
            and c.identifier.reveal() == "5678"
            for c in claims
        )

    def test_DerivationIsIdempotent_AndOrdersStrongestFirst(self):
        claims = derive(
            claims_from_truelayer_account(
                {"account_number": {"number": "12345678", "sort_code": "200000"}}
            )
        )

        assert derive(claims) == claims
        assert claims[0].strength == STRONG
        assert claims[-1].strength == WEAK


class TestTheTypesPreventConflation:
    """The bug this design exists to make unsayable.

    Four digits off a card and four off an account are unrelated numbers.
    Open Banking makes it concrete: only card ACCOUNTS appear on the
    cards endpoint and a debit card is not an account, so the two can
    never describe the same identifier.
    """

    def test_CardDigitsAndAccountDigits_AreNotEqual_EvenWhenIdentical(self):
        assert AccountLastFour("5678") != CardLastFour("5678")
        assert Claim(AccountLastFour("5678")) != Claim(CardLastFour("5678"))

    def test_ACardEndingTheSameDigitsAsAnAccount_DoesNotMatchIt(self):
        account = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "608371"}}
        )
        card = claims_from_truelayer_card({"partial_card_number": "5678"})

        assert best_match(account, card) is None

    def test_TwoViewsOfOneCard_StillMatchWeakly(self):
        card = claims_from_truelayer_card({"partial_card_number": "8484"})
        export = claims_from_file_hints({"card_last_four": "8484"})

        matched = best_match(card, export)

        assert matched is not None
        assert isinstance(matched.identifier, CardLastFour)
        assert matched.strength == WEAK


class TestMatchingAcrossSources:
    def _truelayer(self):
        return claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "608371"}}
        )

    def test_TheSameAccountThroughTwoApis_MatchesOnSortCodeAndNumber(self):
        starling = claims_from_starling_identifiers(
            {"accountIdentifier": "12345678", "bankIdentifier": "60-83-71"}
        )

        matched = best_match(self._truelayer(), starling)

        assert matched is not None
        assert isinstance(matched.identifier, UkAccount)
        assert matched.strength == STRONG

    def test_AnExportKnowingOnlyFourAccountDigits_MatchesWeakly(self):
        export = claims_from_file_hints({"account_number": "5678"})

        matched = best_match(self._truelayer(), export)

        assert matched is not None
        assert isinstance(matched.identifier, AccountLastFour)
        assert matched.strength == WEAK

    def test_DifferentAccounts_DoNotMatch(self):
        other = claims_from_starling_identifiers(
            {"accountIdentifier": "87654321", "bankIdentifier": "608371"}
        )

        assert best_match(self._truelayer(), other) is None

    def test_WhenAStrongAndAWeakClaimBothAgree_TheStrongOneIsReported(self):
        twin = claims_from_starling_identifiers(
            {"accountIdentifier": "12345678", "bankIdentifier": "608371"}
        )

        matched = best_match(self._truelayer(), twin)

        assert matched is not None and matched.strength == STRONG


class TestAnIdentifierHasNoReadableForm:
    """Masking is not politeness here - it is the only rendering there is,
    so a log line or a traceback cannot leak a number by accident."""

    @pytest.mark.parametrize(
        "identifier",
        [
            AccountNumber("12345678"),
            UkAccount("608371:12345678"),
            Iban("GB33STRL60837112345678"),
            AccountLastFour("5678"),
            CardLastFour("8484"),
        ],
    )
    def test_NoRenderingEverShowsMoreThanFourDigits(self, identifier):
        """The invariant, stated as the thing that actually matters: four
        digits is the agreed disclosure, so a rendering that carries five
        consecutive digits has leaked whatever it was masking. For the
        last-four types the value IS the mask, which is the point."""
        rendered = [
            str(identifier),
            repr(identifier),
            f"{identifier}",
            format(identifier),
        ]

        for text in rendered:
            assert not re.search(r"\d{5,}", text), (
                f"{type(identifier).__name__} leaked via {text!r}"
            )

    def test_TheMaskNamesWhichNumberItMeans(self):
        assert CardLastFour("8484").masked() == "card ending 8484"
        assert AccountLastFour("5678").masked() == "account ending 5678"

    def test_ATracebackCannotCarryTheNumber(self):
        """The realistic leak: a value formatted into a log or an error."""
        number = AccountNumber("12345678")

        message = f"could not resolve {number!r} while binding"

        assert "12345678" not in message
        assert "ending 5678" in message


class TestRevealIsTheOnlyDoorAndItIsGreppable:
    def test_TheRenderingLayerNeverReachesForTheRawValue(self):
        """reveal() is deliberately the single escape hatch, so "who reads
        the real number" is one grep. The web layer must never be an
        answer to that grep."""
        src = pathlib.Path(__file__).resolve().parent.parent / "src" / "obdi"
        offenders = {}
        for path in src.rglob("*.py"):
            if path.name == "identifiers.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"\.reveal\(\)", text):
                offenders[path.name] = text.count(".reveal()")

        assert "web.py" not in offenders, (
            f"the web layer called reveal() - rendering must use masked(): {offenders}"
        )
