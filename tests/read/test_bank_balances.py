"""The bank's own present balance is an anchor, judged against the rows as they stood at its moment.

Starling states the account's balance with every pull. Every other balance the
store tests comes from an export or a statement, so where those and the feed
part company nothing could say which is wrong about the money. The bank's own
figure can: if the rows reproduce it, the feed is right and the other source
omits a payment; if the rows are short of it by exactly a payment, the feed
holds one the bank does not count.

The household and its known answers are in `bank_balance_corpus`. Each scenario
below changes one thing and says what the bank's figure must then show.
"""

from __future__ import annotations

import html

import pytest

from bank_balance_corpus import (
    CLEARED_20,
    CLEARED_22,
    CLEARED_25,
    CLEARED_25_MORNING,
    EXPORT_ROWS,
    TOTAL_20,
    TOTAL_22,
    TOTAL_25,
    at,
    balance_body,
    bills_items,
    household,
    item,
    main_items,
)
from obdi.ingest.family_anchors import families_of
from obdi.verify import bank_balances
from obdi.verify.balance_anchors import BANK, effective_opening
from test_export_dating import render as render_escaped
from test_space_attribution import MAIN, MAP


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(balances, **kwargs):
        store = household(tmp_path, balances, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


def render(store) -> str:
    """The masked ledger page with its entities decoded, so a sentence reads as written."""
    return html.unescape(render_escaped(store))


def opening_of(store):
    return effective_opening(store, MAIN, families=families_of(store, MAP))


def bank_own(opening):
    return [r for r in opening.readings if r.anchor.source == bank_balances.BANK_SOURCE]


def bank_whole(opening):
    assert opening.family is not None
    return list(opening.family.bank_readings)


def full_day():
    """Three balances: the 20th's evening, the 25th before the refund, the 25th's evening."""
    return [
        (at(20, 20), balance_body(CLEARED_20, TOTAL_20)),
        (at(25, 9), balance_body(CLEARED_25_MORNING, TOTAL_22)),
        (at(25, 20), balance_body(CLEARED_25, TOTAL_25)),
    ]


class TestACompleteFeed:
    def test_BankBalances_WhenFeedIsComplete_MainAndWholeAccountBothAgree(self, make):
        opening = opening_of(make(full_day(), export=EXPORT_ROWS))

        own = bank_own(opening)
        assert [r.anchor.balance_minor for r in own] == [CLEARED_20, CLEARED_25_MORNING, CLEARED_25]
        assert all(r.anchor.basis == BANK and r.agrees is True for r in own)
        whole = bank_whole(opening)
        assert [r.balance_minor for r in whole] == [TOTAL_20, TOTAL_22, TOTAL_25]
        assert all(r.agrees is True for r in whole)

    def test_BankBalances_WhenFetchedBeforeALaterRowWasPulled_JudgedOnlyAgainstRowsUpToThen(
        self, make
    ):
        # The refund is dated the 25th at 11:00 and the balance was fetched at 09:00 that day.
        # By day alone the refund would count and the balance would differ by its size.
        opening = opening_of(make([(at(25, 9), balance_body(CLEARED_25_MORNING, TOTAL_22))]))

        (own,) = bank_own(opening)
        assert own.anchor.balance_minor == CLEARED_25_MORNING
        assert own.agrees is True
        (whole,) = bank_whole(opening)
        assert whole.agrees is True

    def test_BankBalances_WhenTwoLandOnOneDay_EachIsJudgedAtItsOwnMoment(self, make):
        opening = opening_of(make(full_day()[1:], export=EXPORT_ROWS))

        own = bank_own(opening)
        assert [r.anchor.day.day for r in own] == [25, 25]
        assert [r.anchor.balance_minor for r in own] == [CLEARED_25_MORNING, CLEARED_25]
        assert [r.agrees for r in own] == [True, True]

    def test_BankBalances_WhenAPaymentSettledAfterTheFetch_TheClearedFigureStillAgrees(self, make):
        # Paid on the 22nd at 20:00 and settled on the 24th: the 22nd's evening balance was
        # fetched at 21:00, when the payment was still pending and so not in the cleared figure.
        late = item("m-late", -1500, at(22, 20), settled=at(24, 8))
        opening = opening_of(
            make(
                [(at(22, 21), balance_body(CLEARED_22, TOTAL_22))],
                main=[*main_items(), late],
            )
        )

        (own,) = bank_own(opening)
        assert own.agrees is True

    def test_BankBalances_WhenAPaymentIsPendingAtTheFetch_ClearedExcludesItAndEffectiveCarriesIt(
        self, make
    ):
        pending = item("m-card", -1500, at(23, 7), status="PENDING")
        store = make(
            [(at(23, 8), balance_body(CLEARED_22, TOTAL_22, pending=-1500))],
            main=[*main_items(), pending],
        )

        opening = opening_of(store)

        (own,) = bank_own(opening)
        assert own.agrees is True
        assert opening.bank is not None
        assert opening.bank.pending_tested == 1
        assert opening.bank.pending_included == 1


class TestTheBankDisagreesWithTheFeed:
    def test_BankBalances_WhenTheFeedHoldsAPaymentTheBankDoesNotCount_BothDifferByExactlyIt(
        self, make
    ):
        ghost = item("m-ghost", -4000, at(10), name="Ghost")
        export = list(EXPORT_ROWS)
        opening = opening_of(
            make(
                [(at(22, 20), balance_body(CLEARED_22, TOTAL_22))],
                main=[*main_items(), ghost],
                export=export,
            )
        )

        (own,) = bank_own(opening)
        (whole,) = bank_whole(opening)
        # The bank states 4000 more than the rows predict: the rows hold a payment it does not.
        assert own.difference_minor == 4000
        assert whole.difference_minor == 4000
        # The export, which omits the ghost too, differs by the same amount.
        assert opening.family is not None
        assert {r.difference_minor for r in opening.family.differing} == {4000}

    def test_BankBalances_WhenTheExportOmitsARealPayment_BankAgreesWhileTheExportDiffers(
        self, make
    ):
        export = [r for r in EXPORT_ROWS if r.name != "Garage"]
        opening = opening_of(make(full_day(), export=export))

        assert all(r.agrees is True for r in bank_own(opening))
        assert all(r.agrees is True for r in bank_whole(opening))
        assert opening.family is not None
        # The export's balances on and after the 22nd are 9000 above what the rows predict.
        assert {r.difference_minor for r in opening.family.differing} == {9000}


class TestWhatTheBalancePayloadMeans:
    def test_Meaning_WhenTotalLessClearedIsWhatTheSpacesHold_ClearedIsMainAndTotalIsWhole(
        self, make
    ):
        opening = opening_of(make(full_day()))

        assert opening.bank is not None
        assert opening.bank.meaning == bank_balances.MAIN_AND_WHOLE
        assert (opening.bank.meaning_agreeing, opening.bank.meaning_tested) == (3, 3)

    def test_Meaning_WhenTheSpacesRowsDoNotAccountForTheTotal_NoBalanceIsAnAnchor(self, make):
        # The Space's water payment is missing from what is held, so the Spaces' rows are
        # 5000 more than they were and the total less cleared cannot equal them.
        short = [i for i in bills_items() if i["feedItemUid"] != "b-water"]
        opening = opening_of(
            make([(at(22, 20), balance_body(CLEARED_22, TOTAL_22))], bills=short)
        )

        assert opening.bank is not None
        assert opening.bank.meaning == bank_balances.UNREAD
        assert bank_own(opening) == []
        assert bank_whole(opening) == []
        assert opening.bank.landed == 1
        assert opening.bank.judged == 0

    def test_Meaning_WhenTheClearedFigureIsTheWholeAccountsToo_OnlyTheWholeAnchorIsGiven(
        self, make
    ):
        # A payload whose plain figures already include the Spaces: cleared equals total.
        opening = opening_of(make([(at(22, 20), balance_body(TOTAL_22, TOTAL_22))]))

        assert opening.bank is not None
        assert opening.bank.meaning == bank_balances.WHOLE_ONLY
        assert bank_own(opening) == []
        (whole,) = bank_whole(opening)
        assert whole.agrees is True


class TestAPayloadThatCannotBeRead:
    @pytest.mark.parametrize(
        ("body", "why"),
        [
            (balance_body(CLEARED_22, TOTAL_22, currency="EUR"), "not in GBP"),
            (balance_body(CLEARED_22, TOTAL_22, omit=("clearedBalance",)), "clearedBalance"),
            (
                balance_body(CLEARED_22, TOTAL_22, omit=("totalClearedBalance",)),
                "totalClearedBalance",
            ),
            (b'{"clearedBalance": {}}', "clearedBalance"),
            (b"not json", "not JSON"),
        ],
        ids=["euro", "no-cleared", "no-total", "empty-figure", "garbage"],
    )
    def test_BankBalances_WhenAPayloadIsUnreadable_GivesNoAnchorAndSaysWhy(self, make, body, why):
        opening = opening_of(make([(at(22, 20), body)], export=EXPORT_ROWS))

        assert bank_own(opening) == []
        assert bank_whole(opening) == []
        assert opening.bank is not None
        assert opening.bank.landed == 0
        assert len(opening.bank.refused) == 1
        assert why in opening.bank.refused[0]

    def test_BankBalances_WhenOneOfTwoIsUnreadable_TheOtherStillAnchors(self, make):
        opening = opening_of(
            make(
                [
                    (at(22, 20), balance_body(CLEARED_22, TOTAL_22)),
                    (at(25, 20), balance_body(CLEARED_25, TOTAL_25, currency="USD")),
                ],
                export=EXPORT_ROWS,
            )
        )

        assert len(bank_own(opening)) == 1
        assert len(opening.bank.refused) == 1


class TestHowManyAreJudged:
    def test_BankBalances_WhenMoreThanTheNewestFewAreHeld_OnlyTheNewestAreAnchors(
        self, make
    ):
        many = [
            (at(22, 12 + n // 2, 30 * (n % 2)), balance_body(CLEARED_22 + n, TOTAL_22 + n))
            for n in range(bank_balances.JUDGED + 3)
        ]
        opening = opening_of(make(many))

        assert opening.bank is not None
        assert opening.bank.landed == bank_balances.JUDGED + 3
        assert opening.bank.judged == bank_balances.JUDGED
        assert len(bank_own(opening)) == bank_balances.JUDGED

    def test_Parsing_WhenTheSameBalanceIsAskedForTwice_IsReadOnceInTheProcess(
        self, make, monkeypatch
    ):
        store = make([(at(22, 20), balance_body(CLEARED_22, TOTAL_22))])
        opening_of(store)
        reads = []
        real = bank_balances._read_payload
        monkeypatch.setattr(
            bank_balances, "_read_payload", lambda payload: reads.append(1) or real(payload)
        )

        opening_of(store)

        assert reads == []


class TestWhatThePageSays:
    def test_Page_WhenBankAgreesAndTheExportDiffers_SaysTheDifferencesAreTheExports(self, make):
        export = [r for r in EXPORT_ROWS if r.name != "Garage"]

        page = render(make(full_day(), export=export))

        assert (
            "The bank's own balance, stated at the end of 2026-09-25, is the one the rows add "
            "up to, so the 1 difference against starling-csv is starling-csv's" in page
        )

    def test_Page_WhenBankAndExportDifferByTheSame_SaysTheRowsHoldWhatNeitherCounts(self, make):
        ghost = item("m-ghost", -4000, at(10), name="Ghost")

        page = render(
            make(
                [(at(22, 20), balance_body(CLEARED_22, TOTAL_22))],
                main=[*main_items(), ghost],
                export=EXPORT_ROWS,
            )
        )

        assert (
            "The bank's own balance, stated at the end of 2026-09-22, and starling-csv differ "
            "from the rows by the same amount" in page
        )
        assert "the store holds something that neither counts" in page

    def test_Page_WhenBankAndExportDifferByDifferentAmounts_SaysTheyAreNotOneFault(self, make):
        ghost = item("m-ghost", -4000, at(10), name="Ghost")
        export = [r for r in EXPORT_ROWS if r.name != "Garage"]

        page = render(
            make(
                [(at(22, 20), balance_body(CLEARED_22, TOTAL_22))],
                main=[*main_items(), ghost],
                export=export,
            )
        )

        assert "by a different amount from starling-csv, so they are not one fault" in page

    def test_Page_SaysWhichReadingEachFigureTookAndByWhatTest(self, make):
        page = render(make(full_day(), export=EXPORT_ROWS))

        assert (
            "In 3 of 3 balances the total less the cleared figure equals what the Spaces' rows "
            "sum to" in page
        )
        assert "so the cleared figure is read as the main account's own" in page

    def test_Page_WhenAPayloadIsRefused_CountsItAndSaysWhy(self, make):
        page = render(
            make([(at(22, 20), balance_body(CLEARED_22, TOTAL_22, currency="EUR"))])
        )

        assert "1 balance the bank stated could not be used" in page
        assert "not in GBP" in page

    def test_Page_NeverCarriesAFigureFromTheBalance(self, make):
        page = render(
            make(
                [(at(22, 20), balance_body(321507, 353511))],
                main=[*main_items(), item("m-ghost", -4000, at(10), name="Ghost")],
            )
        )

        for figure in ("3215.07", "3,215.07", "321507", "353511", "3535.11"):
            assert figure not in page


class TestTheOtherPagesUseTheSameAnchors:
    def test_Position_WhenTheBankAnchorsAgree_TheBalanceIsCountedOnce(self, make):
        from datetime import date

        from obdi.read.position import read_position

        store = make(full_day(), export=EXPORT_ROWS)

        position = read_position(
            store, today=date(2026, 9, 30), families=families_of(store, MAP)
        )

        account = next(
            a for group in position.groups for a in group.accounts if a.ref == MAIN
        )
        assert account.balance is not None
        assert account.balance.minor == CLEARED_25
        assert account.checks_differ == 0
        # Every anchor but the one defining the opening is one check, however many bases state it.
        assert account.checks_agree == len(opening_of(store).readings) - 1

    def test_Walk_WhenTheBankAnchorsAgree_TheyAreNotChangesInTheFamilysDifference(self, make):
        opening = opening_of(make(full_day(), export=EXPORT_ROWS))

        assert opening.family is not None
        assert opening.family.changes == ()
        # The export's own cuts: the day before its first row and each day it lists rows.
        assert len(opening.family.readings) == 8
