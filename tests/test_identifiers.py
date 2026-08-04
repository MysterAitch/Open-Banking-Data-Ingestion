"""Matching one real account across sources that know different things."""

from __future__ import annotations

from obdi.identifiers import (
    STRONG,
    WEAK,
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
        assert strong[0].kind == "uk-account"
        assert strong[0].value == "200000:12345678"

    def test_ATrueLayerCard_ProvesOnlyItsLastFour_AndSaysSoWeakly(self):
        """The provider never returns a full card number, so a card match
        can never be more than a proposal."""
        claims = claims_from_truelayer_card({"partial_card_number": "8484"})

        assert len(claims) == 1
        assert claims[0].kind == "card-last-4"
        assert claims[0].value == "8484"
        assert claims[0].strength == WEAK

    def test_StarlingIdentifiers_ProveTheSameThingsAsTrueLayer(self):
        claims = claims_from_starling_identifiers(
            {
                "accountIdentifier": "12345678",
                "bankIdentifier": "608371",
                "iban": "GB33 STRL 6083 7112 3456 78",
            }
        )

        kinds = {c.kind for c in claims}
        assert "uk-account" in kinds
        assert "iban" in kinds

    def test_ASourceThatKnowsNothing_ClaimsNothing(self):
        assert claims_from_truelayer_account({"account_number": {}}) == []
        assert claims_from_truelayer_card({}) == []
        assert claims_from_file_hints({}) == []


class TestStrongerClaimsContainWeakerOnes:
    def test_AFullNumber_AlsoProvesItsLastFour(self):
        """Why a source that knows everything can still meet one that
        knows almost nothing."""
        claims = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "200000"}}
        )

        assert any(c.kind == "account-last-4" and c.value == "5678" for c in claims)

    def test_DerivationIsIdempotent_AndOrdersStrongestFirst(self):
        claims = derive(
            claims_from_truelayer_account(
                {"account_number": {"number": "12345678", "sort_code": "200000"}}
            )
        )
        again = derive(claims)

        assert claims == again
        assert claims[0].strength == STRONG
        assert claims[-1].strength == WEAK


class TestMatchingAcrossSources:
    def _truelayer(self):
        return claims_from_truelayer_account(
            {
                "account_number": {"number": "12345678", "sort_code": "608371"},
                "display_name": "Personal",
            }
        )

    def test_TheSameAccountThroughTwoApis_MatchesOnSortCodeAndNumber(self):
        """The case the whole exercise exists for: Starling's own API and
        TrueLayer's view of the same account."""
        starling = claims_from_starling_identifiers(
            {"accountIdentifier": "12345678", "bankIdentifier": "60-83-71"}
        )

        matched = best_match(self._truelayer(), starling)

        assert matched is not None
        assert matched.kind == "uk-account"
        assert matched.strength == STRONG

    def test_AnExportKnowingOnlyFourDigitsOfTheAccount_StillMatches_ButWeakly(self):
        """A bank export that names four digits of the ACCOUNT should
        still find it - and must report itself as the weak thing it is."""
        export = claims_from_file_hints({"account_number": "5678"})

        matched = best_match(self._truelayer(), export)

        assert matched is not None
        assert matched.kind == "account-last-4"
        assert matched.strength == WEAK

    def test_DifferentAccounts_DoNotMatch(self):
        other = claims_from_starling_identifiers(
            {"accountIdentifier": "87654321", "bankIdentifier": "608371"}
        )

        assert best_match(self._truelayer(), other) is None

    def test_WhenBothAStrongAndAWeakClaimAgree_TheStrongOneIsReported(self):
        """Two accounts at one bank could share four digits by accident;
        the reader needs to know the match rests on more than that."""
        twin = claims_from_starling_identifiers(
            {"accountIdentifier": "12345678", "bankIdentifier": "608371"}
        )

        matched = best_match(self._truelayer(), twin)

        assert matched is not None and matched.strength == STRONG


class TestNumbersAreNeverRendered:
    def test_AClaimMasksItselfToFourDigits(self):
        claims = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "608371"}}
        )

        rendered = [c.masked() for c in claims]

        assert all("12345678" not in text for text in rendered)
        assert all("608371" not in text for text in rendered)
        assert any(text.endswith("5678") for text in rendered)

    def test_AnIbanMasksToItsTail(self):
        claims = claims_from_starling_identifiers(
            {"iban": "GB33 STRL 6083 7112 3456 78"}
        )
        iban = next(c for c in claims if c.kind == "iban")

        assert iban.masked() == "IBAN ending 5678"
        assert "STRL" not in iban.masked()


class TestCardAndAccountDigitsAreDifferentNamespaces:
    """A credit card ending 5678 and a current account ending 5678 are
    unrelated numbers. Open Banking makes this concrete: only CREDIT card
    accounts appear on the cards endpoint, and a debit card is not an
    account at all - so the two kinds of "last four" can never describe
    the same identifier and must never match.
    """

    def test_ACardEndingTheSameFourDigitsAsAnAccount_DoesNotMatchIt(self):
        account = claims_from_truelayer_account(
            {"account_number": {"number": "12345678", "sort_code": "608371"}}
        )
        card = claims_from_truelayer_card({"partial_card_number": "5678"})

        assert best_match(account, card) is None

    def test_TwoCardsSharingFourDigits_StillMatchWeakly(self):
        """The genuine weak case survives: a credit-card export naming
        four digits still finds its card."""
        card = claims_from_truelayer_card({"partial_card_number": "8484"})
        export = claims_from_file_hints({"card_last_four": "8484"})

        matched = best_match(card, export)

        assert matched is not None
        assert matched.kind == "card-last-4"
        assert matched.strength == WEAK

    def test_EachKindSaysWhichNumberItMeans_WhenRendered(self):
        card = claims_from_truelayer_card({"partial_card_number": "8484"})[0]
        account = next(
            c
            for c in claims_from_truelayer_account(
                {"account_number": {"number": "12345678", "sort_code": "608371"}}
            )
            if c.kind == "account-last-4"
        )

        assert card.masked() == "card ending 8484"
        assert account.masked() == "account ending 5678"
