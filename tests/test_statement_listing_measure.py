"""Testing a statement by what it LISTS: constructed statements, answers decided before the run.

A statement says "from this opening balance, THESE transactions, to this closing balance". Each
account below is a household's statements built through the doors a person's files use
(`statement_span_world`), every figure invented, and the answer for each is written beside it.
Card statements are Santander's; the current account is a Starling certified statement.

  complete    Jan, Feb, Mar, Apr 10th, two purchases each, all in their own period, every one held.
              Every check passes, the links meet, the first has no statement before it. By date
              the first cannot be said (nothing before it), the other three are reproduced.
  pending     Mar 10th, Apr 10th, May 10th. April lists a purchase dated 8 March, which was
              pending when March closed. April passes everything; 1 listed transaction lies before
              the previous closing, by 2 days. By date April's opening is NOT reproduced (the day
              it is placed on is before March closed), the failure the day-placement met.
  interest    One statement lists its interest charge FIRST and dates it LAST. Every check passes
              and nothing lies outside its period: a listing has no order.
  december    Jan to Apr 10th, April lists a purchase dated 20 December 2025, held and listed by
              nobody else. April passes; 1 listed transaction lies before the previous closing
              (10 March), 80 days away. The same for a current account (`december-current`, closing
              on the month's last day, so the previous closing is 31 March: 101 days).
  settled     The December purchase is also reported pending by a feed under its own id. Counted
              once: April lists 1 transaction, held with the same amount, nothing unlisted.
  gap         Jan and Feb, Mar missing, Apr whose opening is not Feb's closing (`gap-differs`) or
              is Feb's closing (`gap-meets`). April's own check passes either way, and would be
              newly verified; the link says "differs" or "meets".
  first       The first statement, a feed holding days before it. Passes; the earlier days are not
              beside it; it would be newly verified, as the first statement.
  missing     A statement lists 3 transactions and the store holds 2: read whole yes, held no.
  unsummed    A statement whose own amounts do not reach its closing balance: read whole no.
  merged      A listed transaction held with another amount: 1 different, held no.
  twice       A statement listing two identical lines holds two transactions, each listed once.
  duplicate   The same statement uploaded twice (different bytes): one statement, nothing double.
  overlap     A long statement listing what a shorter one lists: the shared transactions are
              also listed by another statement, each statement passes its own check.
  no-opening  A statement stating no opening balance: cannot say, neither a pass nor a fault.
"""

from __future__ import annotations

import calendar
import threading
from datetime import UTC, date, datetime, time
from html.parser import HTMLParser
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi.account_names import AccountsShown
from obdi.connections import ConnectionStore
from obdi.family_anchors import Families
from obdi.identity import artefact_digest, content_key
from obdi.ingest import import_file, media_type_of, reconcile_batch
from obdi.models import RawArtefact, SourceTier, Transaction, TransactionStatus
from obdi.statement_listing_measure import (
    Held,
    Link,
    StatementListing,
    _pair,
    statement_listing_report,
)
from obdi.statement_listing_page import statement_listing_html
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from statement_span_world import MONTHS, Spend, _ordinal, _pounds, feed, statement
from test_starling_statement import build_starling_pdf

D = date
NO_SPACES = Families({}, {}, {})
OPENING = 10000


def starling_statement(
    store: Store,
    root: Path,
    ref: str,
    first: date,
    last: date,
    opening: int,
    rows: list[tuple[date, str, int]],
) -> int:
    """A Starling statement; each row is `(day, text, minor)` with money out positive."""
    closing = opening - sum(minor for _, _, minor in rows)
    period = f"{first:%d/%m/%Y} - {last:%d/%m/%Y}"
    received = sum(-m for _, _, m in rows if m < 0)
    paid = sum(m for _, _, m in rows if m > 0)
    lines = [
        f"SUMMARY|{period}|{_pounds(opening)}|{_pounds(received)}|{_pounds(paid)}|"
        f"{_pounds(closing)}",
        "HEAD",
        f"OPENING|{_pounds(opening)}",
    ]
    for day, text, minor in rows:
        incoming, outgoing = ("", _pounds(minor)) if minor > 0 else (_pounds(-minor), "")
        lines.append(f"ROW|{day:%d/%m/%Y}|FASTER PAYMENT|{text}|{incoming}|{outgoing}|")
    lines.append("END")
    path = root / f"{ref}-{last.isoformat()}.pdf"
    path.write_bytes(build_starling_pdf(lines))
    import_file(store, path, account_id=ref)
    return closing


def landed_only(
    store: Store, root: Path, ref: str, name: str, lines: list[str], received: date
) -> None:
    """A document held in the raw layer without its transactions being read into the account."""
    path = root / f"{name}.pdf"
    path.write_bytes(build_pdf(lines))
    payload = path.read_bytes()
    store.land_artefact(
        RawArtefact(
            source="pdf",
            account_ref=ref,
            fetched_at=datetime.combine(received, time(12), UTC),
            media_type=media_type_of(payload, path),
            digest=artefact_digest(payload),
            payload=payload,
            origin=path.name,
        )
    )


def santander_lines(
    closing: date, opening: int | None, rows: list[Spend], stated_closing: int, prev: date
) -> list[str]:
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {_ordinal(closing)} {calendar.month_name[closing.month]} "
        f"{closing.year}      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
    ]
    if opening is not None:
        lines += [
            f"Previous balance as at {_ordinal(prev)} {calendar.month_name[prev.month]} "
            f"{prev.year}: £{_pounds(opening)}",
            f"Balance brought forward from previous statement          {_pounds(opening)}",
        ]
    lines += [
        *(
            f"{_ordinal(r.day)} {MONTHS[r.day.month - 1]} {r.payee}   {_pounds(r.minor)}"
            for r in rows
        ),
        f"Your new balance:                                        {_pounds(stated_closing)}",
    ]
    return lines


def chain(
    store: Store, root: Path, ref: str, closings: list[date], rows: list[list[Spend]]
) -> list[int]:
    """Consecutive card statements, each opening where the last closed."""
    owed = OPENING
    found = []
    for position, (closing, spends) in enumerate(zip(closings, rows, strict=True)):
        previous = closings[position - 1] if position else _month_before(closing)
        owed = statement(
            store, root, ref, closing, owed, spends, received=closing, previous_close=previous
        )
        found.append(owed)
    return found


def _month_before(day: date) -> date:
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(year, month, day.day)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("listing")
    with Store(root / "store.sqlite3") as store:
        # complete
        chain(
            store,
            root,
            "complete",
            [D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 4, 10)],
            [
                [Spend(D(2026, 1, 2), "Alpha Shop", 1237), Spend(D(2026, 1, 8), "Bravo Shop", 519)],
                [
                    Spend(D(2026, 1, 15), "Charlie Shop", 733),
                    Spend(D(2026, 2, 5), "Delta Shop", 301),
                ],
                [
                    Spend(D(2026, 2, 12), "Echo Shop", 449),
                    Spend(D(2026, 3, 1), "Foxtrot Shop", 227),
                ],
                [Spend(D(2026, 3, 15), "Golf Shop", 181), Spend(D(2026, 4, 2), "Hotel Shop", 653)],
            ],
        )
        # pending
        chain(
            store,
            root,
            "pending",
            [D(2026, 3, 10), D(2026, 4, 10), D(2026, 5, 10)],
            [
                [
                    Spend(D(2026, 2, 20), "India Shop", 1011),
                    Spend(D(2026, 3, 5), "Juliet Shop", 523),
                ],
                [Spend(D(2026, 3, 8), "Kilo Shop", 307), Spend(D(2026, 4, 2), "Lima Shop", 709)],
                [Spend(D(2026, 4, 20), "Mike Shop", 211)],
            ],
        )
        # interest: listed first, dated last
        chain(
            store,
            root,
            "interest",
            [D(2026, 3, 10), D(2026, 4, 10)],
            [
                [Spend(D(2026, 2, 20), "November Shop", 911)],
                [
                    Spend(D(2026, 4, 9), "Oscar Interest", 213),
                    Spend(D(2026, 3, 15), "Papa Shop", 417),
                    Spend(D(2026, 3, 28), "Quebec Shop", 319),
                ],
            ],
        )
        # december (card)
        chain(
            store,
            root,
            "december",
            [D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 4, 10)],
            [
                [
                    Spend(D(2025, 12, 15), "Romeo Shop", 611),
                    Spend(D(2026, 1, 5), "Sierra Shop", 413),
                ],
                [Spend(D(2026, 1, 20), "Tango Shop", 717)],
                [Spend(D(2026, 2, 25), "Uniform Shop", 331)],
                [
                    Spend(D(2025, 12, 20), "Victor Shop", 829),
                    Spend(D(2026, 4, 2), "Whisky Shop", 197),
                ],
            ],
        )
        # december (current account)
        owed = 500000
        for first, last, rows in [
            (D(2026, 1, 1), D(2026, 1, 31), [(D(2026, 1, 5), "RENT", 91103)]),
            (D(2026, 2, 1), D(2026, 2, 28), [(D(2026, 2, 6), "GAS", 12307)]),
            (D(2026, 3, 1), D(2026, 3, 31), [(D(2026, 3, 9), "WATER", 3109)]),
            (
                D(2026, 4, 1),
                D(2026, 4, 30),
                [(D(2025, 12, 20), "GIFT", 4513), (D(2026, 4, 3), "COUNCIL", 15301)],
            ),
        ]:
            owed = starling_statement(store, root, "december-current", first, last, owed, rows)
        # settled: the December purchase also reported pending by a feed under its own id
        chain(
            store,
            root,
            "settled",
            [D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 4, 10)],
            [
                [Spend(D(2026, 1, 5), "Xray Shop", 413)],
                [Spend(D(2026, 1, 20), "Yankee Shop", 717)],
                [Spend(D(2026, 2, 25), "Zulu Shop", 331)],
                [Spend(D(2025, 12, 20), "Amber Shop", 829)],
            ],
        )
        reconcile_batch(
            store,
            [
                Transaction(
                    account_id="settled",
                    amount_minor=-829,
                    currency="GBP",
                    value_date=D(2025, 12, 20),
                    booking_date=D(2025, 12, 20),
                    description="Amber Shop",
                    source="truelayer-pending",
                    source_id="pending-amber",
                    status=TransactionStatus.PENDING,
                    tier=SourceTier.AUTHORITATIVE,
                    content_key=content_key(
                        amount_minor=-829, value_date=D(2025, 12, 20), description="Amber Shop"
                    ),
                )
            ],
            digest="settled-pending",
        )
        # gap: Mar missing
        feb = chain(
            store,
            root,
            "gap-differs",
            [D(2026, 1, 10), D(2026, 2, 10)],
            [
                [Spend(D(2026, 1, 5), "Bronze Shop", 413)],
                [Spend(D(2026, 1, 20), "Copper Shop", 717)],
            ],
        )[-1]
        statement(
            store,
            root,
            "gap-differs",
            D(2026, 4, 10),
            feb + 1500,
            [Spend(D(2026, 3, 20), "Iron Shop", 221)],
            received=D(2026, 4, 10),
            previous_close=D(2026, 3, 10),
        )
        feb = chain(
            store,
            root,
            "gap-meets",
            [D(2026, 1, 10), D(2026, 2, 10)],
            [[Spend(D(2026, 1, 5), "Silver Shop", 413)], [Spend(D(2026, 1, 20), "Gold Shop", 717)]],
        )[-1]
        statement(
            store,
            root,
            "gap-meets",
            D(2026, 4, 10),
            feb,
            [Spend(D(2026, 3, 20), "Tin Shop", 221)],
            received=D(2026, 4, 10),
            previous_close=D(2026, 3, 10),
        )
        # first statement, a feed holding days before it
        feed(
            store,
            "first",
            [Spend(D(2025, 12, 20), "Cobalt Shop", 611), Spend(D(2026, 1, 3), "Nickel Shop", 413)],
            digest="first-feed",
        )
        statement(
            store,
            root,
            "first",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Zinc Shop", 717), Spend(D(2026, 2, 5), "Lead Shop", 331)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        # a transaction listed but missing from the store
        statement(
            store,
            root,
            "missing",
            D(2026, 2, 10),
            OPENING,
            [
                Spend(D(2026, 1, 20), "Ash Shop", 717),
                Spend(D(2026, 1, 25), "Birch Shop", 331),
                Spend(D(2026, 2, 5), "Cedar Shop", 457),
            ],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        store.connection.execute(
            "DELETE FROM transaction_sources WHERE entity_id IN "
            "(SELECT entity_id FROM transactions WHERE account_id = 'missing' "
            "AND description = 'Birch Shop')"
        )
        store.connection.execute(
            "DELETE FROM transactions WHERE account_id = 'missing' AND description = 'Birch Shop'"
        )
        # amounts that do not sum, landed and not read into the account
        statement(
            store,
            root,
            "unsummed",
            D(2026, 1, 10),
            OPENING,
            [Spend(D(2026, 1, 5), "Dill Shop", 311)],
            received=D(2026, 1, 10),
            previous_close=D(2025, 12, 10),
        )
        landed_only(
            store,
            root,
            "unsummed",
            "unsummed-feb",
            santander_lines(
                D(2026, 2, 10),
                OPENING + 311,
                [Spend(D(2026, 1, 20), "Fennel Shop", 523)],
                OPENING + 311 + 523 + 100,
                D(2026, 1, 10),
            ),
            D(2026, 2, 10),
        )
        # a listed transaction held with another amount
        statement(
            store,
            root,
            "merged",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Basil Shop", 717), Spend(D(2026, 2, 5), "Sage Shop", 331)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        store.connection.execute(
            "UPDATE transactions SET amount_minor = amount_minor - 100 "
            "WHERE account_id = 'merged' AND description = 'Sage Shop'"
        )
        # two identical lines
        statement(
            store,
            root,
            "twice",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Thyme Shop", 241), Spend(D(2026, 1, 20), "Thyme Shop", 241)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        # the same statement uploaded twice, as different bytes
        statement(
            store,
            root,
            "duplicate",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Mint Shop", 717), Spend(D(2026, 2, 5), "Rue Shop", 331)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        again = root / "duplicate-again.pdf"
        again.write_bytes(
            build_pdf(
                [
                    *santander_lines(
                        D(2026, 2, 10),
                        OPENING,
                        [
                            Spend(D(2026, 1, 20), "Mint Shop", 717),
                            Spend(D(2026, 2, 5), "Rue Shop", 331),
                        ],
                        OPENING + 717 + 331,
                        D(2026, 1, 10),
                    ),
                    "Customer service 0800 000 000",
                ]
            )
        )
        import_file(store, again, account_id="duplicate")
        # overlapping: a short statement and a longer one listing the same two transactions
        statement(
            store,
            root,
            "overlap",
            D(2026, 1, 10),
            OPENING,
            [Spend(D(2025, 12, 20), "Oak Shop", 717), Spend(D(2026, 1, 5), "Elm Shop", 331)],
            received=D(2026, 1, 10),
            previous_close=D(2025, 12, 10),
        )
        statement(
            store,
            root,
            "overlap",
            D(2026, 3, 10),
            OPENING,
            [
                Spend(D(2025, 12, 20), "Oak Shop", 717),
                Spend(D(2026, 1, 5), "Elm Shop", 331),
                Spend(D(2026, 2, 12), "Ivy Shop", 457),
            ],
            received=D(2026, 3, 10),
            previous_close=D(2025, 12, 10),
        )
        # no opening balance stated
        landed_only(
            store,
            root,
            "no-opening",
            "no-opening-jan",
            santander_lines(
                D(2026, 1, 10),
                None,
                [Spend(D(2026, 1, 5), "Moss Shop", 311)],
                OPENING + 311,
                D(2025, 12, 10),
            ),
            D(2026, 1, 10),
        )
        keep_statement_readings(store)
        store.connection.commit()
        report = statement_listing_report(store, NO_SPACES, sibling_accounts={})
        yield store, {a.account: a for a in report.accounts}, report


def statements_of(world, ref: str) -> list[StatementListing]:
    return world[1][ref].statements


def column(world, ref: str, name: str) -> list:
    return [getattr(s, name) for s in statements_of(world, ref)]


class TestACompleteRunOfCardStatements:
    def test_Statements_WhenEveryTransactionIsHeldInItsOwnPeriod_EveryCheckPasses(self, world):
        account = world[1]["complete"]

        assert len(account.statements) == 4
        assert column(world, "complete", "read_whole") == [True] * 4
        assert column(world, "complete", "as_held") == [True] * 4
        assert [s.held for s in account.statements] == [Held(2, same=2)] * 4
        assert account.passing == 4
        assert account.failing == []
        assert account.clean is True

    def test_Links_WhenEachStatementOpensWhereTheLastClosed_AreFirstThenMeet(self, world):
        assert column(world, "complete", "link") == [Link.FIRST, *[Link.MEETS] * 3]
        assert column(world, "complete", "between") == [None, True, True, True]

    def test_Statements_WhenNothingIsOutsideOrUnlisted_SayZero(self, world):
        assert column(world, "complete", "unlisted") == [0, 0, 0, 0]
        assert column(world, "complete", "outside_furthest") == [0, 0, 0, 0]

    def test_ByDate_WhenEveryOpeningSitsOnTheDayBefore_FirstCannotBeSaidOthersAreReproduced(
        self, world
    ):
        assert column(world, "complete", "by_date") == [None, True, True, True]


class TestAPurchasePendingAtOneClose:
    def test_Statement_WhenItListsAPurchaseDatedBeforeThePreviousClose_PassesAndNamesTheDistance(
        self, world
    ):
        april = statements_of(world, "pending")[1]

        assert (april.passes, april.outside_before, april.outside_after) == (True, 1, 0)
        assert april.outside_furthest == 2
        assert april.held == Held(2, same=2)
        assert april.link is Link.MEETS

    def test_ByDate_WhenAListedPurchaseIsDatedBeforeThePreviousClose_TheDayPlacementFails(
        self, world
    ):
        assert column(world, "pending", "by_date") == [None, False, True]

    def test_Account_WhenOnlyTheDayPlacementFails_NoStatementIsAFault(self, world):
        account = world[1]["pending"]

        assert account.passing == 3
        assert account.failing == []


class TestInterestPrintedFirstAndDatedLast:
    def test_Statement_WhenInterestIsListedFirstButDatedLast_PassesWithNothingOutsideItsPeriod(
        self, world
    ):
        second = statements_of(world, "interest")[1]

        assert second.passes is True
        assert second.held == Held(3, same=3)
        assert (second.outside_before, second.outside_after) == (0, 0)


class TestADecemberTransactionFirstListedInApril:
    def test_Card_WhenAnAprilStatementListsADecemberPurchase_PassesAndTheDistanceIs80Days(
        self, world
    ):
        april = statements_of(world, "december")[3]

        assert (april.passes, april.outside_before, april.outside_after) == (True, 1, 0)
        assert april.outside_furthest == 80
        assert april.link is Link.MEETS
        assert column(world, "december", "between") == [None, True, True, True]
        assert world[1]["december"].failing == []

    def test_CurrentAccount_WhenAnAprilStatementListsADecemberPayment_PassesAndTheDistanceIs101Days(
        self, world
    ):
        account = world[1]["december-current"]
        april = account.statements[3]

        assert len(account.statements) == 4
        assert (april.passes, april.outside_before) == (True, 1)
        assert april.outside_furthest == 101
        assert column(world, "december-current", "link") == [Link.FIRST, *[Link.MEETS] * 3]
        assert account.failing == []


class TestAPaymentReportedPendingThenListedBySettlement:
    def test_Statement_WhenAFeedAlsoReportedItPending_CountsItOnceAndLeavesNothingUnlisted(
        self, world
    ):
        store, accounts, _ = world
        april = accounts["settled"].statements[3]
        counting = [
            t
            for t in store.transactions_for_account("settled")
            if t.description == "Amber Shop" and not t.status.is_history
        ]

        assert len(counting) == 1
        assert april.held == Held(1, same=1)
        assert april.passes is True
        assert april.unlisted == 0


class TestAStatementAfterAMissingOne:
    def test_Statement_WhenItsOpeningDiffersFromThePreviousClosing_PassesAndIsNewlyVerified(
        self, world
    ):
        account = world[1]["gap-differs"]
        april = account.statements[2]

        assert (april.link, april.passes) == (Link.DIFFERS, True)
        assert april.between is False
        assert [(n.closing, n.link) for n in account.newly_verified] == [
            (D(2026, 1, 10), Link.FIRST),
            (D(2026, 4, 10), Link.DIFFERS),
        ]
        assert account.failing == []

    def test_Statement_WhenItsOpeningEqualsThePreviousClosing_IsTakenAsConsecutiveByEvidence(
        self, world
    ):
        april = world[1]["gap-meets"].statements[2]

        assert (april.link, april.passes) == (Link.MEETS, True)
        assert april.between is True


class TestTheFirstStatementWithAnotherSourceHoldingEarlierDays:
    def test_Statement_WhenFeedRowsPrecedeIt_PassesAndTheEarlierDaysAreNotBesideIt(self, world):
        account = world[1]["first"]
        only = account.statements[0]

        assert (only.link, only.passes, only.unlisted) == (Link.FIRST, True, 0)

    def test_Account_WhenTheFirstStatementPasses_ItsStretchIsNewlyVerifiedAsTheFirst(self, world):
        account = world[1]["first"]

        assert [(n.closing, n.link) for n in account.newly_verified] == [
            (D(2026, 2, 10), Link.FIRST)
        ]
        assert account.newly_verified[0].spans == ((D(2026, 1, 10), D(2026, 2, 10)),)


class TestAListedTransactionMissingFromTheStore:
    def test_Statement_WhenOneListedTransactionIsNotHeld_ReadsWholeButFailsTheHeldCheck(
        self, world
    ):
        only = world[1]["missing"].statements[0]

        assert only.read_whole is True
        assert only.held == Held(3, same=2, not_held=1)
        assert only.as_held is False
        assert only.fails is True
        assert [s.closing for s in world[1]["missing"].failing] == [D(2026, 2, 10)]


class TestAStatementWhoseAmountsDoNotSum:
    def test_Statement_WhenItsOwnAmountsMissTheClosing_DoesNotReadWholeAndIsAFault(self, world):
        account = world[1]["unsummed"]
        february = account.statements[1]

        assert [s.closing for s in account.statements] == [D(2026, 1, 10), D(2026, 2, 10)]
        assert (february.read_whole, february.lines_listed, february.held) == (False, 1, None)
        assert february.fails is True
        assert account.statements[0].passes is True

    def test_Statement_WhenItDoesNotReadWhole_TheLinkStillSaysWhetherTheBalancesMeet(self, world):
        assert world[1]["unsummed"].statements[1].link is Link.MEETS


class TestAListedTransactionHeldWithAnotherAmount:
    def test_Statement_WhenTwoSourcesMergedOnToAnotherAmount_CountsItDifferentAndFailsHeld(
        self, world
    ):
        only = world[1]["merged"].statements[0]

        assert only.held == Held(2, same=1, different=1)
        assert (only.read_whole, only.as_held) == (True, False)


class TestTwoIdenticalLinesAndDuplicateFiles:
    def test_Statement_WhenItListsTwoIdenticalLines_HoldsTwoTransactionsEachListedOnce(self, world):
        only = world[1]["twice"].statements[0]

        assert only.held == Held(2, same=2)
        assert only.passes is True

    def test_Account_WhenTheSameStatementIsUploadedTwice_IsOneStatementAndNothingIsDoubled(
        self, world
    ):
        account = world[1]["duplicate"]

        assert len(account.statements) == 1
        assert account.statements[0].held == Held(2, same=2)
        assert account.statements[0].passes is True

    def test_Pair_WhenTwoIdenticalLinesAreHeldOnce_CountsTheSecondAsListedMoreThanOnce(self):
        day = D(2026, 1, 20)
        held = Transaction(
            account_id="a",
            amount_minor=-241,
            currency="GBP",
            value_date=day,
            booking_date=day,
            description="x",
            source="s",
            source_id=None,
            tier=SourceTier.SYNTHETIC,
            content_key="k",
        )

        counts, counting = _pair(
            [(day, -241), (day, -241)],
            [(held.entity_id, day.isoformat())],
            {held.entity_id: held},
            "a",
            (),
        )

        assert counts == Held(2, same=1, repeated=1)
        assert counting == {held.entity_id}


class TestOverlappingStatements:
    def test_Statements_WhenALongerOneListsWhatAShorterOneLists_BothPassAndTheSharedAreCounted(
        self, world
    ):
        short, long = world[1]["overlap"].statements

        assert (short.passes, long.passes) == (True, True)
        assert short.held == Held(2, same=2, also_by_another=2)
        assert long.held == Held(3, same=3, also_by_another=2)
        assert long.link is Link.DIFFERS


class TestAStatementWithNoOpeningStated:
    def test_Statement_WhenItStatesNoOpening_CannotBeSaidEitherWayAndIsNoFault(self, world):
        account = world[1]["no-opening"]
        only = account.statements[0]

        assert (only.read_whole, only.as_held, only.link) == (None, None, Link.NO_OPENING)
        assert (only.passes, only.fails) == (False, False)
        assert account.failing == []


class TestTheSectionShowsNoFigure:
    @staticmethod
    def served(report) -> str:
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(Path("unused.json")),
            identity_health_text=lambda: "identity report",
            statement_listing_report=lambda: report,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health").text
        finally:
            httpd.shutdown()

    def test_Page_WhenTheHouseholdIsRendered_ShowsNoFigureInTextOrAttribute(self, world):
        parsed = _Parsed()
        parsed.feed(self.served(world[2]))
        visible = " ".join(parsed.text)
        attributes = " ".join(parsed.attributes)
        everything = f"{visible} {attributes}"

        assert "Statements by what they list" in visible
        assert "complete" in visible
        for private in (
            "12.37",
            "1237",
            "5.19",
            "7.33",
            "11.00",
            "10,000.00",
            "10000",
            "100.00",
            "829",
            "8.29",
            "913",
            "1,500",
            "15.00",
            "4.57",
            "457",
        ):
            assert private not in everything
        for payee in ("Alpha Shop", "Victor Shop", "Amber Shop", "GIFT", "RENT"):
            assert payee not in everything

    def test_Page_WhenTheHouseholdIsRendered_UsesTheAgreedWordsAndNoRetiredOnes(self, world):
        text = " ".join(_text_of(self.served(world[2]))).lower()

        for retired in ("anchor", "in agreement", "held back", " rows "):
            assert retired not in text.split("statements by what they list", 1)[1]
        assert "adds up" in text or "add up" in text

    def test_Section_WhenRendered_NamesTheSourceAsCodeAndLinksThePeriodPage(self, world):
        page = statement_listing_html(world[2], AccountsShown())
        parsed = _Parsed()
        parsed.feed(page)

        assert "santander-cc-pdf" in parsed.code
        assert any(link.startswith("/period-reconciliation?ref=") for link in parsed.links)
        assert parsed.details > 0

    def test_Section_WhenAnAccountPassesEverything_SaysSoInOneLine(self, world):
        page = statement_listing_html(world[2], AccountsShown())

        assert "Every statement passes every check." in " ".join(_text_of(page))

    def test_Section_WhenNoAccountHoldsAStatement_SaysThereIsNothingToTest(self):
        from obdi.statement_listing_measure import StatementListingReport

        page = statement_listing_html(StatementListingReport(), AccountsShown())

        assert "No account holds a statement" in " ".join(_text_of(page))


class _Parsed(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.attributes: list[str] = []
        self.code: list[str] = []
        self.links: list[str] = []
        self.details = 0
        self._in_code = False

    def handle_starttag(self, tag, attrs):
        if tag == "code":
            self._in_code = True
        if tag == "details":
            self.details += 1
        for name, value in attrs:
            self.attributes.append(f"{name}={value}")
            if tag == "a" and name == "href" and value:
                self.links.append(value)

    def handle_endtag(self, tag):
        if tag == "code":
            self._in_code = False

    def handle_data(self, data):
        self.text.append(data)
        if self._in_code:
            self.code.append(data)


def _text_of(page: str) -> list[str]:
    parsed = _Parsed()
    parsed.feed(page)
    return parsed.text
