"""Payments to an account obdi holds nothing for, and the owner's declaration that it is theirs.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). Every account, sort code,
and number is invented.

  - Twelve monthly payments from "current" to an account number no held account has are ONE
    unheld account: twelve payments, ending "2222". A second number with the same ending is a
    second row, not part of the first.
  - A number that a confirmed pair ties to a held account is not unheld, and neither are the
    unpaired rows that state it.
  - Declaring the first number theirs, with a label, makes an external account carrying that
    number; its twelve rows are then named "held:external-..." and shown "your <label>", no
    proposal is made, and the second number is untouched.
  - The detector reads the twelve as a monthly TRANSFER to the declared account (it was a plain
    payee series before the declaration, and an incoming leg would have read as income).
  - A label of nothing but spaces is refused and writes nothing; a key no rows state is refused;
    the key of a number a pair has tied to a held account is refused; a name already declared is
    refused. A declaration never overrides a number the pairs have spoken for, even ambiguously.
  - A press of "Not mine" hides the row and declares nothing; offering again shows it.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.entities import (
    ACCOUNT_KEY_PREFIX,
    HELD_PREFIX,
    display_names,
    held_counterparts,
    name_rows,
)
from obdi.analysis.external_accounts import (
    EXTERNAL_REF_PREFIX,
    declare_external,
    dismiss,
    dismissed_keys,
    offer_again,
    unheld_accounts,
)
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import EntityRefused
from obdi.ingest.store import Store

FIRST = "778899-11112222"
SECOND = "778800-33332222"
HELD = "112233-12345678"
TODAY = date(2026, 10, 7)
MONTHS = [date(2026, m, 5) for m in range(1, 13)]


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


def twelve_to(identifier: str, *, prefix: str = "a", amount: int = -20000) -> list[Transaction]:
    return [
        leg(f"{prefix}{i}", "current", amount, when, f"REF {i} PAYMENT", party_account=identifier)
        for i, when in enumerate(MONTHS)
    ]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def named(rows, pairs=(), external=None):
    return name_rows(rows, list(pairs), external=external)[2]


class TestPaymentsToAnUnheldAccount:
    def test_UnheldAccounts_WhenTwelvePaymentsGoToOneNumber_IsOneAccountWithItsCountAndEnding(self):
        rows = twelve_to(FIRST)

        offered, declined = unheld_accounts(rows, named(rows))

        assert [(a.payments, a.ending) for a in offered] == [(12, "2222")]
        assert declined == 0
        assert offered[0].key.startswith(ACCOUNT_KEY_PREFIX)

    def test_UnheldAccounts_WhenTwoNumbersShareAnEnding_AreTwoAccountsMostPaymentsFirst(self):
        rows = [*twelve_to(FIRST), *twelve_to(SECOND, prefix="b")[:3]]

        offered, _declined = unheld_accounts(rows, named(rows))

        assert [(a.payments, a.ending) for a in offered] == [(12, "2222"), (3, "2222")]
        assert offered[0].key != offered[1].key

    def test_UnheldAccounts_WhenNoRowStatesAnAccount_IsEmpty(self):
        rows = [leg("x", "current", -100, TODAY, "CORNER SHOP")]

        assert unheld_accounts(rows, named(rows)) == ((), 0)

    def test_UnheldAccounts_WhenAConfirmedPairTiesTheNumberToAHeldAccount_DoesNotOfferIt(self):
        out = leg("out", "current", -100, MONTHS[0], "TO SAVINGS", party_account=HELD)
        arriving = leg("in", "savings", 100, MONTHS[0], "FROM CURRENT")
        later = [
            leg(f"l{i}", "current", -100, when, "TO SAVINGS", party_account=HELD)
            for i, when in enumerate(MONTHS[1:4])
        ]
        rows = [out, arriving, *later]

        offered, _declined = unheld_accounts(rows, named(rows, [("out", "in")]))

        assert offered == ()

    def test_UnheldAccounts_WhenTheOwnerDeclaredIt_IsNoLongerOffered(self):
        rows = twelve_to(FIRST)

        offered, _declined = unheld_accounts(
            rows, named(rows, external={FIRST: "external-aaaa"})
        )

        assert offered == ()


class TestDeclaringAnAccountMine:
    def test_Declaration_NamesTheTwelveRowsYourLabelWithNoProposal(self, store):
        rows = twelve_to(FIRST)
        before = named(rows)
        (offered,), _ = unheld_accounts(rows, before)

        said = declare_external(
            store, rows, before, {"account": [offered.key], "label": ["Partner joint"]}
        )

        (record,) = store.external_accounts()
        assert (record.identifier, record.label, record.external) == (
            FIRST, "Partner joint", True
        )
        assert str(record.ref).startswith(EXTERNAL_REF_PREFIX)
        assert "Partner joint" in said and "12 payments" in said and "2222" in said
        fields, _links, after = name_rows(rows, [], external=store.external_identifiers())
        assert {n.name for n in after} == {HELD_PREFIX + str(record.ref)}
        labels = display_names(fields, after, {str(record.ref): "Partner joint"})
        assert set(labels.values()) == {"your Partner joint"}

    def test_Declaration_LeavesTheSecondNumberWithTheSameEndingOffered(self, store):
        rows = [*twelve_to(FIRST), *twelve_to(SECOND, prefix="b")[:3]]
        first, second = unheld_accounts(rows, named(rows))[0]

        declare_external(
            store, rows, named(rows), {"account": [first.key], "label": ["Partner joint"]}
        )

        offered, _declined = unheld_accounts(
            rows, named(rows, external=store.external_identifiers())
        )
        assert [a.key for a in offered] == [second.key]

    def test_Declarations_OfTwoNumbersWithTheSameEnding_AreTwoAccounts(self, store):
        rows = [*twelve_to(FIRST), *twelve_to(SECOND, prefix="b")[:3]]
        first, second = unheld_accounts(rows, named(rows))[0]

        declare_external(store, rows, named(rows), {"account": [first.key], "label": ["One"]})
        declare_external(store, rows, named(rows), {"account": [second.key], "label": ["Two"]})

        assert {(r.label, r.identifier) for r in store.external_accounts()} == {
            ("One", FIRST),
            ("Two", SECOND),
        }
        assert len({str(r.ref) for r in store.external_accounts()}) == 2

    @pytest.mark.parametrize("label", ["", "   ", "\t "])
    def test_Declaration_WhenTheLabelIsEmpty_IsRefusedAndWritesNothing(self, store, label):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))

        with pytest.raises(EntityRefused, match="Say what to call"):
            declare_external(store, rows, named(rows), {"account": [offered.key], "label": [label]})

        assert store.external_accounts() == []

    def test_Declaration_WhenTheLabelIsAbsentFromTheForm_IsRefused(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))

        with pytest.raises(EntityRefused):
            declare_external(store, rows, named(rows), {"account": [offered.key]})

        assert store.external_accounts() == []

    def test_Declaration_WhenTheLabelIsTooLong_IsRefused(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))

        with pytest.raises(EntityRefused, match="longer than"):
            declare_external(
                store, rows, named(rows), {"account": [offered.key], "label": ["x" * 121]}
            )

    def test_Declaration_WhenNoRowsStateTheKey_IsRefused(self, store):
        rows = twelve_to(FIRST)

        unknown = ACCOUNT_KEY_PREFIX + "0" * 24
        with pytest.raises(EntityRefused, match=r"not held here now"):
            declare_external(store, rows, named(rows), {"account": [unknown], "label": ["X"]})

        assert store.external_accounts() == []

    def test_Declaration_WhenAPairHasTiedTheNumberToAHeldAccount_IsRefused(self, store):
        out = leg("out", "current", -100, MONTHS[0], "TO SAVINGS", party_account=HELD)
        arriving = leg("in", "savings", 100, MONTHS[0], "FROM CURRENT")
        rows = [out, arriving]
        unpaired = leg("u", "current", -100, MONTHS[1], "TO SAVINGS", party_account=HELD)
        key = named([unpaired])[0].name

        with pytest.raises(EntityRefused):
            declare_external(
                store,
                [*rows, unpaired],
                named([*rows, unpaired], [("out", "in")]),
                {"account": [key], "label": ["Not savings"]},
            )

        assert store.external_accounts() == []

    def test_Declaration_WhenPressedTwice_IsRefusedTheSecondTime(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))
        declare_external(store, rows, named(rows), {"account": [offered.key], "label": ["One"]})

        with pytest.raises(EntityRefused):
            declare_external(
                store,
                rows,
                named(rows, external=store.external_identifiers()),
                {"account": [offered.key], "label": ["Again"]},
            )

        assert [r.label for r in store.external_accounts()] == ["One"]


class TestADeclarationNeverOutranksAPair:
    def test_HeldCounterparts_WhenAPairTiesTheNumberToAHeldAccount_TheDeclarationIsIgnored(self):
        out = leg("out", "current", -100, MONTHS[0], "TO SAVINGS", party_account=HELD)
        arriving = leg("in", "savings", 100, MONTHS[0], "FROM CURRENT")
        unpaired = leg("u", "current", -100, MONTHS[1], "TO SAVINGS", party_account=HELD)

        found = held_counterparts(
            [out, arriving, unpaired], [("out", "in")], {HELD: "external-aaaa"}
        )

        assert found == ["savings", "current", "savings"]

    def test_HeldCounterparts_WhenPairsShowTheNumberLeadingToTwoAccounts_TheDeclarationIsIgnored(
        self,
    ):
        a = leg("a", "current", -100, MONTHS[0], "X", party_account=SECOND)
        b = leg("b", "savings", 100, MONTHS[0], "Y")
        c = leg("c", "current", -100, MONTHS[1], "X", party_account=SECOND)
        d = leg("d", "isa", 100, MONTHS[1], "Y")
        unpaired = leg("e", "current", -100, MONTHS[2], "X", party_account=SECOND)

        found = held_counterparts(
            [a, b, c, d, unpaired], [("a", "b"), ("c", "d")], {SECOND: "external-aaaa"}
        )

        assert found[-1] == ""

    def test_HeldCounterparts_WhenARowStatesNothing_IsNeverNamedByADeclaration(self):
        row = leg("x", "current", -100, MONTHS[0], "CORNER SHOP")

        assert held_counterparts([row], [], {"": "external-aaaa"}) == [""]


class TestTheDetectorReadsADeclaredAccountAsATransfer:
    def test_Detector_WhenTheAccountIsNotDeclared_FindsAPayeeSeries(self):
        found = find_recurring(twelve_to(FIRST), [], TODAY)

        assert [(s.is_transfer, s.other_account, s.held_account) for s in found] == [
            (False, "", "")
        ]

    def test_Detector_WhenTheAccountIsDeclared_FindsAMonthlyTransferToIt(self):
        found = find_recurring(twelve_to(FIRST), [], TODAY, external={FIRST: "external-aaaa"})

        assert [
            (s.is_transfer, s.is_income, s.other_account, s.held_account, s.count, s.cadence)
            for s in found
        ] == [(True, False, "external-aaaa", "external-aaaa", 12, "monthly")]

    def test_Detector_WhenMoneyComesInFromTheDeclaredAccount_IsATransferAndNotIncome(self):
        incoming = twelve_to(FIRST, amount=20000)

        before = find_recurring(incoming, [], TODAY)
        after = find_recurring(incoming, [], TODAY, external={FIRST: "external-aaaa"})

        assert [s.is_income for s in before] == [True]
        assert [(s.is_transfer, s.is_income) for s in after] == [(True, False)]


class TestWhenTheOwnerSaysNotMine:
    def test_NotMine_HidesTheAccountAndDeclaresNothing(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))

        said = dismiss(store, rows, named(rows), {"account": [offered.key]})

        assert "2222" in said
        assert store.external_accounts() == []
        assert unheld_accounts(rows, named(rows), dismissed_keys(store)) == ((), 1)

    def test_OfferAgain_ShowsTheDismissedAccountAgain(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))
        dismiss(store, rows, named(rows), {"account": [offered.key]})

        said = offer_again(store)

        assert "1 account" in said
        shown, declined = unheld_accounts(rows, named(rows), dismissed_keys(store))
        assert [a.key for a in shown] == [offered.key] and declined == 0

    def test_NotMine_WhenNoRowsStateTheKey_IsRefused(self, store):
        rows = twelve_to(FIRST)

        with pytest.raises(EntityRefused):
            dismiss(store, rows, named(rows), {"account": ["acct-nothing"]})

        assert dismissed_keys(store) == set()

    def test_Declaring_AfterSayingNotMine_ForgetsTheDecline(self, store):
        rows = twelve_to(FIRST)
        (offered,), _ = unheld_accounts(rows, named(rows))
        dismiss(store, rows, named(rows), {"account": [offered.key]})

        declare_external(store, rows, named(rows), {"account": [offered.key], "label": ["Mine"]})

        assert dismissed_keys(store) == set()
