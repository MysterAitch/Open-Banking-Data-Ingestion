"""A transfer between the household's own accounts is a transfer: its other party is an ACCOUNT.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). Every account, sort code,
and number is invented.

  - Twelve monthly transfers from "current" to "savings", twelve different references, each the
    leaving leg of a confirmed pair: ONE name, kind ACCOUNT, shown "your Savings", and nothing
    proposed for it. The same twelve credit legs in "savings" are one name shown "your Current".
  - A leg that states the account identifier (the savings account's) whose pair is confirmed
    teaches that the identifier IS the savings account: a later leg stating it, with no confirmed
    pair, is named "your Savings" too.
  - An identifier that confirmed pairs show leading to TWO accounts is ambiguous and names
    nothing; a row is never its own account's counterpart.
  - "Me" is never reached from a row: no owner entity is made, and no label is "me".
  - The detector keys a pair's legs on the pair, as before: the transfer series is unchanged by
    naming, and an unpaired leg that states the identifier is a payee series shown "your Savings".
"""

from __future__ import annotations

from datetime import date

from obdi.analysis.entities import (
    ACCOUNT,
    HELD_PREFIX,
    UNNAMED_PARTY,
    display_names,
    held_counterparts,
    name_origins,
    name_rows,
    view_of,
)
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction

SAVINGS_IDENTIFIER = "112233-12345678"
OTHER_IDENTIFIER = "445566-87654321"
TODAY = date(2026, 10, 7)
REFERENCES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def leg(
    entity: str,
    account: str,
    amount: int,
    when: date,
    description: str,
    *,
    party_account: str = "",
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        party_account=party_account,
        source="starling",
        tier=SourceTier.SYNTHETIC,
        entity_id=entity,
    )


def twelve_transfers(
    *, identifier: str = ""
) -> tuple[list[Transaction], list[tuple[str, str]]]:
    rows: list[Transaction] = []
    pairs: list[tuple[str, str]] = []
    for i, month in enumerate(REFERENCES):
        when = date(2026, 1 + i, 5)
        rows.append(
            leg(f"out{i}", "current", -20000, when, f"{month} SAVING", party_account=identifier)
        )
        rows.append(leg(f"in{i}", "savings", 20000, when, f"FROM CURRENT {month}"))
        pairs.append((f"out{i}", f"in{i}"))
    return rows, pairs


class TestTwelveTransfersToASavingsAccount:
    def test_Names_AreOneAccountNamedYourSavingsWithNoProposal(self):
        rows, pairs = twelve_transfers()

        fields, _links, named = name_rows(rows, pairs)
        leaving = [n for n, r in zip(named, rows, strict=True) if r.account_id == "current"]

        assert {n.name for n in leaving} == {HELD_PREFIX + "savings"}
        assert {n.kind for n in leaving} == {ACCOUNT}
        labels = display_names(fields, named, {"savings": "Savings", "current": "Current"})
        assert labels[HELD_PREFIX + "savings"] == "your Savings"
        assert labels[HELD_PREFIX + "current"] == "your Current"
        origins = name_origins(named)
        view = view_of(
            {n: o.rows for n, o in origins.items()}, [], origins=origins, labels=labels
        )
        assert view.proposals.groups == () and view.proposals.too_broad == ()
        assert view.suggestions == ()

    def test_Label_WhenNoLabelIsGiven_IsYourAndTheAccountsOwnNameNeverANumber(self):
        rows, pairs = twelve_transfers(identifier=SAVINGS_IDENTIFIER)

        fields, _links, named = name_rows(rows, pairs)

        labels = display_names(fields, named)
        assert set(labels.values()) == {"your savings", "your current"}
        assert all("12345678" not in label for label in labels.values())

    def test_Me_IsNeverReachedFromTheRows(self):
        rows, pairs = twelve_transfers()

        fields, _links, named = name_rows(rows, pairs)

        assert all("me" not in label.split() for label in display_names(fields, named).values())
        assert UNNAMED_PARTY not in display_names(fields, named).values()


class TestAnIdentifierTheHouseholdsPairsTeach:
    def test_LaterLeg_WhenItStatesTheSavingsIdentifier_IsToSavingsWithoutAConfirmedPair(self):
        rows, pairs = twelve_transfers(identifier=SAVINGS_IDENTIFIER)
        unpaired = leg(
            "late", "current", -20000, date(2026, 10, 5), "OCT SAVING",
            party_account=SAVINGS_IDENTIFIER,
        )

        held = held_counterparts([*rows, unpaired], pairs)

        assert held[-1] == "savings"

    def test_Identifier_WhenPairsShowItLeadingToTwoAccounts_NamesNothing(self):
        a = leg("a", "current", -100, date(2026, 1, 5), "X", party_account=OTHER_IDENTIFIER)
        b = leg("b", "savings", 100, date(2026, 1, 5), "Y")
        c = leg("c", "current", -100, date(2026, 2, 5), "X", party_account=OTHER_IDENTIFIER)
        d = leg("d", "isa", 100, date(2026, 2, 5), "Y")
        unpaired = leg("e", "current", -100, date(2026, 3, 5), "X", party_account=OTHER_IDENTIFIER)

        held = held_counterparts([a, b, c, d, unpaired], [("a", "b"), ("c", "d")])

        assert held[-1] == ""
        assert held[:4] == ["savings", "current", "isa", "current"]

    def test_Row_WhoseIdentifierIsItsOwnAccounts_IsNeverItsOwnCounterpart(self):
        a = leg("a", "current", -100, date(2026, 1, 5), "X", party_account=SAVINGS_IDENTIFIER)
        b = leg("b", "savings", 100, date(2026, 1, 5), "Y")
        inside_savings = leg(
            "s", "savings", -100, date(2026, 2, 5), "Z", party_account=SAVINGS_IDENTIFIER
        )

        held = held_counterparts([a, b, inside_savings], [("a", "b")])

        assert held[2] == ""

    def test_Pair_WhenBothLegsAreInOneAccount_IsNotATransferBetweenAccounts(self):
        a = leg("a", "current", -100, date(2026, 1, 5), "X")
        b = leg("b", "current", 100, date(2026, 1, 5), "Y")

        assert held_counterparts([a, b], [("a", "b")]) == ["", ""]

    def test_Rows_WhenNoPairIsConfirmed_AreNamedAsBefore(self):
        rows, _pairs = twelve_transfers(identifier=SAVINGS_IDENTIFIER)

        _fields, _links, named = name_rows(rows, [])

        assert not any(n.name.startswith(HELD_PREFIX) for n in named)
        assert {n.kind for n in named if n.name} <= {ACCOUNT, "description", "alias"}


class TestTheDetectorIsUnchangedForPairs:
    def test_TransferSeries_IsFoundByItsLegsWhateverTheNamesSay(self):
        rows, pairs = twelve_transfers(identifier=SAVINGS_IDENTIFIER)

        found = [s for s in find_recurring(rows, pairs, TODAY) if s.account == "current"]

        assert [(s.is_transfer, s.other_account, s.count) for s in found] == [
            (True, "savings", 12)
        ]
        assert found[0].held_account == ""

    def test_UnpairedLegsStatingTheIdentifier_AreOnePayeeSeriesOfTheHeldAccount(self):
        rows, pairs = twelve_transfers(identifier=SAVINGS_IDENTIFIER)
        # Confirm only the first pair, so the identifier is learned and the rest are unpaired.
        found = [
            s for s in find_recurring(rows, pairs[:1], TODAY)
            if s.account == "current" and not s.is_transfer
        ]

        assert [(s.held_account, s.count, s.cadence) for s in found] == [("savings", 11, "monthly")]
        assert found[0].shape == "your savings"
