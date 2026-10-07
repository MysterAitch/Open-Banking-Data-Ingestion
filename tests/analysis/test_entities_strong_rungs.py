"""A party stated by its account or the bank's own id is NAMED by that identifier and SHOWN by a
readable name.

KNOWN ANSWERS, decided before the first run. Every person, sort code, account number, and uid is
invented.

  - Twelve monthly transfers to Alex Rowan's account (twelve different references; the bank states
    "Alex Rowan" on seven and "A Rowan" on five): ONE name, kind ACCOUNT, shown as "alex rowan"
    (the stated name most rows state).
  - Two people who share a stated name and have different accounts are TWO names, both shown as
    "sam okafor": identity is the account, the label is only what they are called.
  - A card payment stating a merchant uid and a name is named by the uid (SOURCE_ID beats
    STATED_NAME), shown by the stated name; four payments to it are one name.
  - An account beats a uid on the same row.
  - A statement row stating only "JAN RENT" joins the account-named party when a feed row
    carrying both the account and "JAN RENT" exists; where the same description went to two
    accounts it joins neither.
  - A party's key is not its account number or its uid shown as text: no label is ever the number
    or the uid.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from obdi.analysis.entities import (
    ACCOUNT,
    ALIAS,
    DESCRIPTION,
    SOURCE_ID,
    STATED_NAME,
    STATED_PREFIX,
    UNNAMED_PARTY,
    EntitiesView,
    Fields,
    display_names,
    is_identifier_key,
    learned_links,
    name_origins,
    name_readings,
    names_of,
    view_of,
)

ALEX = "201234-55667788"
SAM_ONE = "304050-11223344"
SAM_TWO = "304050-99887766"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def alex_rows() -> list[Fields]:
    return [
        Fields(f"{month} RENT", "Alex Rowan" if i < 7 else "A Rowan", ALEX, "starling:uid-alex")
        for i, month in enumerate(MONTHS)
    ]


class TestAnAccountIsOneParty:
    def test_Transfers_WhenTwelveReferencesAndTwoSpellingsShareOneAccount_AreOneNamedParty(self):
        rows = alex_rows()

        named = names_of(rows)

        assert len({n.name for n in named}) == 1
        assert {n.kind for n in named} == {ACCOUNT}
        assert display_names(rows, named) == {named[0].name: "alex rowan"}

    def test_Transfers_WithoutTheAccount_AreTwoNamesByTheirStatedSpelling(self):
        # The same twelve rows with the account withheld: this is what the page said before the
        # account reached the row, and why the strong rung exists.
        rows = [Fields(r.description, r.counterparty) for r in alex_rows()]

        assert {n.name for n in names_of(rows)} == {"alex rowan", "a rowan"}

    def test_Parties_WhenTwoPeopleShareAStatedNameButNotAnAccount_AreTwoNames(self):
        rows = [
            Fields("RENT", "Sam Okafor", SAM_ONE),
            Fields("GAS", "Sam Okafor", SAM_ONE),
            Fields("RENT", "Sam Okafor", SAM_TWO),
        ]

        named = names_of(rows)
        labels = display_names(rows, named)

        assert len({n.name for n in named}) == 2
        assert set(labels.values()) == {"sam okafor"}

    def test_Account_WhenSpeltWithSpacesOrHyphens_IsTheSameParty(self):
        first = names_of([Fields("RENT", "", "20-12-34 55667788")])[0]
        second = names_of([Fields("RENT", "", "201234-55667788")])[0]

        assert first.name == second.name

    def test_Key_IsNeitherTheAccountNumberNorSomethingThatLooksLikeAName(self):
        key = names_of([Fields("RENT", "Alex", ALEX)])[0].name

        assert "55667788" not in key and "201234" not in key
        assert is_identifier_key(key)
        assert not is_identifier_key("alex rowan")
        assert not is_identifier_key("rent")


class TestASourceIdIsOneParty:
    def test_CardPayments_WhenTheyStateAUidAndAName_AreNamedByTheUidAndShownByTheName(self):
        rows = [Fields(f"OAKMERE COFFEE {n}", "Oakmere Coffee", "", "starling:uid-oak") for n in
                range(4)]

        named = names_of(rows)

        assert {n.kind for n in named} == {SOURCE_ID}
        assert len({n.name for n in named}) == 1
        assert display_names(rows, named) == {named[0].name: "oakmere coffee"}

    def test_Row_WhenItStatesAnAccountAndAUid_TheAccountNamesIt(self):
        named = names_of([Fields("RENT", "Alex Rowan", ALEX, "starling:uid-alex")])[0]

        assert named.kind == ACCOUNT

    def test_Row_WhenItStatesOnlyAName_IsStillNamedByIt(self):
        assert names_of([Fields("RENT", "Alex Rowan")])[0].kind == STATED_NAME

    def test_Label_WhenNoRowStatesAName_IsTheDescriptionShapeNeverTheIdentifier(self):
        rows = [
            Fields("RENT 0101", "", ALEX),
            Fields("RENT 0201", "", ALEX),
            Fields("FEB", "", ALEX),
        ]

        labels = display_names(rows, names_of(rows))

        assert list(labels.values()) == ["rent"]

    def test_Label_WhenNothingReadableIsStated_IsTheUnnamedPartyNotTheIdentifier(self):
        rows = [Fields("12 34", "99", ALEX)]

        labels = display_names(rows, names_of(rows))

        assert list(labels.values()) == [UNNAMED_PARTY]

    def test_Labels_AreNeverAnAccountNumberOrAUid(self):
        rows = [*alex_rows(), Fields("COFFEE", "Oakmere", "", "starling:uid-oak")]

        for label in display_names(rows, names_of(rows)).values():
            assert "55667788" not in label and "uid-" not in label and ":" not in label


class TestAStatementRowJoinsThePartyItsDescriptionWasSeenWith:
    def test_Statement_WhenAFeedRowCarriesTheAccountAndTheSameDescription_JoinsTheParty(self):
        rows = [
            Fields("JAN RENT", "Alex Rowan", ALEX),
            Fields("JAN RENT", ""),
            Fields("JAN RENT", ""),
        ]

        named = names_of(rows)

        assert len({n.name for n in named}) == 1
        assert [n.kind for n in named] == [ACCOUNT, ALIAS, ALIAS]
        assert named[1].linked_by == ACCOUNT
        assert named[1].support == 1

    def test_Statement_WhenTheDescriptionWentToTwoAccounts_JoinsNeither(self):
        rows = [
            Fields("RENT", "Sam Okafor", SAM_ONE),
            Fields("RENT", "Sam Okafor", SAM_TWO),
            Fields("RENT", ""),
        ]

        named = names_of(rows)

        assert named[2].kind == DESCRIPTION
        assert named[2].name == "rent"

    def test_Statement_WhenNoRowCarriesBoth_StaysDescribed(self):
        rows = [Fields("FEB RENT", "Alex Rowan", ALEX), Fields("JAN RENT", "")]

        assert names_of(rows)[1].kind == DESCRIPTION


class TestAStatedNameStandsForThePartyItWasStatedWith:
    """The measured fault: an export states "Oakmere Coffee" for the years the feed does not, the
    feed states the same name with the merchant's uid, and the uid made the feed's months one name
    and the export's months another (40 names became 78 on the invented large store)."""

    def test_Names_WhenAFeedAndAnExportStateOneName_AreOnePartyAndTheExportRowsSayHow(self):
        rows = [Fields("OAKMERE COFFEE 1", "Oakmere Coffee", "", "starling:uid-oak")] * 6 + [
            Fields("OAKMERE COFFEE", "Oakmere Coffee")
        ] * 6

        named = names_of(rows)

        assert len({n.name for n in named}) == 1
        assert Counter(n.kind for n in named) == {SOURCE_ID: 6, ALIAS: 6}
        linked = [n for n in named if n.kind == ALIAS]
        assert {(n.linked_by, n.support, n.via) for n in linked} == {
            (SOURCE_ID, 6, "oakmere coffee")
        }

    def test_Names_WhenTwoPeopleShareAStatedName_AStatedOnlyRowJoinsNeither(self):
        rows = [
            Fields("RENT", "Sam Okafor", SAM_ONE),
            Fields("RENT", "Sam Okafor", SAM_TWO),
            Fields("RENT", "Sam Okafor"),
        ]

        named = names_of(rows)

        assert named[2].kind == STATED_NAME
        assert named[2].name == "sam okafor"
        assert len({n.name for n in named}) == 3

    def test_Names_WhenNoRowStatedTheNameWithAnIdentifier_NothingIsInvented(self):
        rows = [Fields("COFFEE", "Oakmere Coffee")] * 3

        assert not [key for key in learned_links(rows) if key.startswith(STATED_PREFIX)]
        assert {n.kind for n in names_of(rows)} == {STATED_NAME}

    def test_Label_OfAPartyJoinedThroughItsStatedName_IsThatName(self):
        rows = [
            Fields("COFFEE", "Oakmere Coffee", "", "starling:uid-oak"),
            Fields("X", "Oakmere Coffee"),
        ]

        named = names_of(rows)

        assert display_names(rows, named) == {named[0].name: "oakmere coffee"}

    def test_Derivation_OfAStatedRowJoinedToAnIdentifier_SaysSoInTheWordsOfBothKinds(self):
        from obdi.analysis.entities import Covered, derivation_of

        def covered(kind: str, via: str, linked_by: str) -> Covered:
            return Covered(
                date(2026, 1, 1), "current", "", -100, "GBP", "COFFEE", "a", "Oakmere Coffee",
                kind, via, 6, linked_by,
            )

        found = derivation_of(
            "oakmere coffee", [covered(ALIAS, "oakmere coffee", SOURCE_ID)]
        )

        assert found.source == (
            "bank's merchant name, named by the bank's own id for the party "
            "through 6 payments seen by both"
        )
        assert found.printed == ("Oakmere Coffee",)

    def test_Derivation_OfADescriptionJoinedToAnAccount_SaysTheDescriptionWasLinked(self):
        from obdi.analysis.entities import Covered, derivation_of

        row = Covered(
            date(2026, 1, 1), "current", "", -100, "GBP", "JAN RENT", "a", "",
            ALIAS, "rent", 3, ACCOUNT,
        )

        assert derivation_of("alex rowan", [row]).source == (
            "description, named by the other party's account number "
            "through 3 payments seen by both"
        )


class TestTheSummaryCountsNamesByEveryKind:
    def test_Origins_CountAccountAndUidNamedRowsAndSayTheStrongestKind(self):
        rows = [*alex_rows(), *[Fields("COFFEE", "Oakmere", "", "starling:uid-oak")] * 3,
                Fields("PLAIN SHOP", "")]

        origins = name_origins(names_of(rows))
        by_kind = Counter(o.kind for o in origins.values())

        assert by_kind == {ACCOUNT: 1, SOURCE_ID: 1, DESCRIPTION: 1}
        assert sum(o.account for o in origins.values()) == 12
        assert sum(o.source_id for o in origins.values()) == 3
        assert sum(o.rows for o in origins.values()) == 16

    def test_Readings_LeaveOutNamesThatAreIdentifiers(self):
        rows = alex_rows()

        assert name_readings(rows, names_of(rows)) == {}


class TestThePageOffersNothingForAParty:
    def view(self) -> EntitiesView:
        rows = [
            *alex_rows(),
            Fields("RENT", "Sam Okafor", SAM_ONE),
            Fields("RENT", "Sam", SAM_TWO),
        ]
        named = names_of(rows)
        origins = name_origins(named)
        counts = {name: origin.rows for name, origin in origins.items()}
        return view_of(counts, [], origins=origins, labels=display_names(rows, named))

    def test_View_OffersNoGroupForNamesThatAreIdentifiers(self):
        view = self.view()

        assert len(view.counts) == 3
        assert view.proposals.groups == () and view.proposals.too_broad == ()

    def test_View_PrintsALabelForAnIdentifierAndNeverTheIdentifier(self):
        view = self.view()

        shown = {view.label(name) for name in view.counts}

        assert shown == {"alex rowan", "sam okafor", "sam"}

    def test_View_WhenAnIdentifierHasNoLabel_PrintsTheUnnamedPartyNotTheKey(self):
        key = names_of([Fields("RENT", "", ALEX)])[0].name

        assert EntitiesView({}, (), view_of({}, []).proposals).label(key) == UNNAMED_PARTY
