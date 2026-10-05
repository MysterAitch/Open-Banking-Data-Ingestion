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

from credit_union_documents import nine_accounts, pdf
from obdi.account_names import AccountsShown
from obdi.balance_anchors import record_stated_anchor
from obdi.connections import ConnectionStore
from obdi.family_anchors import Families
from obdi.identity import artefact_digest, content_key
from obdi.ingest import import_file, media_type_of, reconcile_batch
from obdi.models import RawArtefact, SourceTier, Transaction, TransactionStatus
from obdi.parsers.credit_union_pdf import section_key
from obdi.statement_listing_measure import (
    DayReading,
    Held,
    Link,
    StatementListing,
    StatementListingReport,
    _pair,
    statement_listing_report,
)
from obdi.statement_listing_page import statement_listing_html
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from section_harness import config, environment, keep
from statement_span_world import MONTHS, Spend, _ordinal, _pounds, feed, statement
from test_starling_statement import build_starling_pdf

D = date
NO_SPACES = Families({}, {}, {})
OPENING = 10000


#: One Space, `blind-pocket`, of `blind-main`, and likewise `blind2-pocket`. The Starling
#: statement's source is bound to neither Space, so it cannot see them (`Families.blind`).
FAMILIES = Families(
    {"blind-pocket": "blind-main", "blind2-pocket": "blind2-main"},
    {"truelayer-booked": frozenset({"blind-pocket", "blind2-pocket"})},
    {},
)


def _entity(store: Store, account: str, description_ends: str) -> str:
    (found,) = [
        t.entity_id
        for t in store.transactions_for_account(account)
        if t.description.endswith(description_ends)
    ]
    return found


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
        # a listed transaction folded into another that counts, in a plain account
        statement(
            store,
            root,
            "folded",
            D(2026, 2, 10),
            OPENING,
            [
                Spend(D(2026, 1, 20), "Fold Shop", 717),
                Spend(D(2026, 1, 25), "Keep Shop", 331),
                Spend(D(2026, 2, 5), "Other Shop", 457),
            ],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        feed(store, "folded-pocket", [Spend(D(2026, 1, 20), "Pocket Fold", 717)], digest="fp")
        # a listed transaction folded into another whose destination is not recorded
        statement(
            store,
            root,
            "folded-lost",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Lost Shop", 717), Spend(D(2026, 2, 5), "Kept Shop", 331)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        # history by its reason: one listed transaction reversed, one void
        statement(
            store,
            root,
            "history",
            D(2026, 2, 10),
            OPENING,
            [
                Spend(D(2026, 1, 12), "Rev Shop", 111),
                Spend(D(2026, 1, 20), "Void Shop", 222),
                Spend(D(2026, 1, 25), "Live Shop", 333),
                Spend(D(2026, 2, 5), "Alive Shop", 444),
            ],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        for name, status in (("Rev Shop", "reversed"), ("Void Shop", "void")):
            store.connection.execute(
                "UPDATE transactions SET status = ? WHERE account_id = 'history' "
                "AND description = ?",
                (status, name),
            )
        # a statement blind to the Space, one line of it folded into the Space's row
        starling_statement(
            store,
            root,
            "blind-main",
            D(2026, 1, 1),
            D(2026, 1, 31),
            500000,
            [
                (D(2026, 1, 5), "SHOP A", 4001),
                (D(2026, 1, 9), "SHOP B", 2503),
                (D(2026, 1, 12), "POCKET SHOP", 1507),
            ],
        )
        feed(store, "blind-pocket", [Spend(D(2026, 1, 12), "Pocket Row", 1507)], digest="bp")
        # a blind statement whose held amounts do not reach its closing balance
        starling_statement(
            store,
            root,
            "blind2-main",
            D(2026, 1, 1),
            D(2026, 1, 31),
            500000,
            [(D(2026, 1, 5), "SHOP C", 4001), (D(2026, 1, 9), "SHOP D", 2503)],
        )
        store.connection.execute(
            "UPDATE transactions SET amount_minor = amount_minor - 100 "
            "WHERE account_id = 'blind2-main' AND description LIKE '%SHOP D'"
        )
        # a day another source states a different closing balance for
        for ref, other_day, other_minor in (
            ("sameday-explained", D(2026, 2, 10), 777),
            ("sameday-unexplained", D(2026, 2, 10), 777),
            ("sameday-nextday", D(2026, 2, 11), 777),
        ):
            closing = statement(
                store,
                root,
                ref,
                D(2026, 2, 10),
                OPENING,
                [Spend(D(2026, 1, 20), "Quiet Shop", 717), Spend(D(2026, 2, 5), "Calm Shop", 331)],
                received=D(2026, 2, 10),
                previous_close=D(2026, 1, 10),
            )
            feed(store, ref, [Spend(other_day, "Late Shop", other_minor)], digest=f"late-{ref}")
            stated = -closing - (other_minor if ref != "sameday-unexplained" else 500)
            record_stated_anchor(
                store, ref, "2026-02-10", f"{stated / 100:.2f}", today=D(2026, 9, 1)
            )
        store.connection.commit()
        store.replace_space_folds(
            {
                _entity(store, "folded", "Fold Shop"): _entity(
                    store, "folded-pocket", "Pocket Fold"
                ),
                _entity(store, "blind-main", "POCKET SHOP"): _entity(
                    store, "blind-pocket", "Pocket Row"
                ),
            }
        )
        keep_statement_readings(store)
        store.connection.commit()
        # a statement whose reading was never kept
        statement(
            store,
            root,
            "unkept",
            D(2026, 2, 10),
            OPENING,
            [Spend(D(2026, 1, 20), "Hazel Shop", 717), Spend(D(2026, 2, 5), "Holly Shop", 331)],
            received=D(2026, 2, 10),
            previous_close=D(2026, 1, 10),
        )
        store.connection.execute(
            "DELETE FROM statement_readings WHERE digest IN "
            "(SELECT digest FROM raw_artefacts WHERE account_ref = 'unkept')"
        )
        store.replace_statement_folds([_entity(store, "folded-lost", "Lost Shop")])
        report = statement_listing_report(store, FAMILIES, sibling_accounts={})
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

    def test_Account_WhenTheFirstStatementPassesButAFeedHoldsEarlierDays_ItsDaysAreNotVerified(
        self, world
    ):
        account = world[1]["first"]
        only = account.statements[0]

        # Verified by what it lists, but two feed transactions precede its first day and no
        # statement lists them, so the days are not claimed (`agreement`, R1).
        assert only.passes is True
        assert only.unlisted_before == 2
        assert account.newly_verified == []


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
    def test_Statement_WhenItsOwnAmountsMissTheClosing_DoesNotReadWholeAndIsNoFault(self, world):
        # A statement the reader refused is never a known balance, so a fault of the account
        # cannot be said of it: it is cannot say, and the page blames the reading (round two).
        account = world[1]["unsummed"]
        february = account.statements[1]

        assert [s.closing for s in account.statements] == [D(2026, 1, 10), D(2026, 2, 10)]
        assert (february.read_whole, february.lines_listed, february.held) == (False, 1, None)
        assert (february.fails, february.cannot_say, february.fault) == (False, True, "")
        assert account.failing == []
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

        counts, counting, through = _pair(
            [(day, -241), (day, -241)],
            [(held.entity_id, day.isoformat())],
            {held.entity_id: held},
            "a",
            (),
        )

        assert counts == Held(2, same=1, repeated=1)
        assert (counting, through) == ({held.entity_id}, set())


class TestOverlappingStatements:
    def test_Statements_WhenALongerOneListsWhatAShorterOneLists_BothPassAndTheSharedAreCounted(
        self, world
    ):
        short, long = world[1]["overlap"].statements

        assert (short.passes, long.passes) == (True, True)
        assert short.held == Held(2, same=2, also_by_another=2)
        assert long.held == Held(3, same=3, also_by_another=2)
        # The two share transactions, so they are not consecutive and their balances conclude
        # nothing about money moving between them (`agreement`, R4).
        assert (long.link, long.shared) == (Link.OVERLAPS, 2)


class TestAStatementWithNoOpeningStated:
    def test_Statement_WhenItStatesNoOpening_CannotBeSaidEitherWayAndIsNoFault(self, world):
        account = world[1]["no-opening"]
        only = account.statements[0]

        assert (only.read_whole, only.as_held, only.link) == (None, None, Link.NO_OPENING)
        assert (only.passes, only.fails) == (False, False)
        assert account.failing == []


@pytest.fixture(scope="module")
def sectioned(tmp_path_factory):
    """Two accounts of one "all accounts" statement, each assigned its own section."""
    root = tmp_path_factory.mktemp("sectioned")
    with pytest.MonkeyPatch.context() as patch:
        environment(patch, root)
        db = root / "store.sqlite3"
        with Store(db):
            pass
        with Store(db) as store:
            artefact = keep(store, pdf(nine_accounts(), step=5.5), "all accounts.pdf")
        wired = config(db)
        wired.assign_statement_section(artefact, section_key("Regular Saver"), "credit-union-saver")
        wired.assign_statement_section(
            artefact, section_key("Personal -9.50%"), "credit-union-personal-loan"
        )
        with Store(db) as store:
            report = statement_listing_report(store, NO_SPACES, sibling_accounts={})
            yield {a.account: a for a in report.accounts}


class TestASectionOfAnAllAccountsStatement:
    """The nine-account document: the saver's section lists 3 transactions and the loan's 2.
    Read from the whole document's reading, which lists none of them, the saver was reported as
    not adding up with 0 transactions listed while its own 3 were held."""

    def test_Saver_WhenItsSectionIsAssigned_ReadsWholeFromItsOwnLines(self, sectioned):
        (only,) = sectioned["credit-union-saver"].statements

        assert (only.read_whole, only.lines_listed) == (True, 3)
        assert only.held == Held(3, same=3)
        assert (only.passes, only.fails) == (True, False)

    def test_Loan_WhenItsSectionIsAssigned_ReadsWholeFromItsOwnLines(self, sectioned):
        (only,) = sectioned["credit-union-personal-loan"].statements

        assert (only.read_whole, only.lines_listed) == (True, 2)
        assert only.held == Held(2, same=2)
        assert sectioned["credit-union-personal-loan"].failing == []

    def test_Readings_WhenTheDocumentsOwnReadingIsAnotherStatements_IsNeverBorrowed(self):
        from obdi.parsers.statement_reading import StatementReading
        from obdi.statement_listing_measure import _Readings
        from obdi.statement_terms import KeptPdf

        whole = StatementReading(
            statement_date=D(2026, 3, 1), closing_balance_minor=5, opening_balance_minor=5
        )
        readings = _Readings({}, {"d": KeptPdf("a", "d", "s", whole)}, frozenset())

        assert readings.of("a", "d", D(2026, 3, 1), 5) is whole
        assert readings.of("a", "d", D(2026, 3, 1), 6) is None
        assert readings.of("a", "d", D(2026, 4, 1), 5) is None
        assert readings.of("a", "other", D(2026, 3, 1), 5) is None


class TestAStatementWhoseReadingWasNeverKept:
    def test_Statement_WhenNoReadingIsKept_CannotBeSaidToReadWholeAndIsNoFault(self, world):
        account = world[1]["unkept"]
        (only,) = account.statements

        assert only.read_whole is None
        assert "no reading of this statement is kept" in only.read_note
        assert (only.lines_listed, only.outside_known) == (None, False)
        assert (only.passes, only.fails, only.cannot_say) == (False, False, True)
        assert account.failing == []

    def test_Statement_WhenNoReadingIsKept_StillHasItsHeldCheckFromWhatItsDocumentReported(
        self, world
    ):
        (only,) = world[1]["unkept"].statements

        assert only.held == Held(2, unamounted=2, amounts_stated=False)
        assert (only.as_held, only.held_verdict) == (True, True)


class TestAListedTransactionFoldedIntoAnother:
    def test_Statement_WhenALineIsFoldedIntoAnotherAccountsTransaction_IsHeldUnderAnotherAccount(
        self, world
    ):
        # The line is folded into a transaction of `folded-pocket`, which is not a Space of
        # `folded` (round two, decision 6): a statement that can see only its own account does not
        # account for it, so it does not add up by what it lists.
        (only,) = world[1]["folded"].statements

        assert only.held == Held(3, same=2, elsewhere=1)
        assert (only.as_held, only.through_folds, only.held_verdict) == (False, None, False)
        assert only.passes is False and only.fault == "held-elsewhere"
        assert [s.closing for s in world[1]["folded"].failing] == [D(2026, 2, 10)]

    def test_Statement_WhenAFoldedLinesDestinationIsNotRecorded_IsCannotSayAndNoFault(self, world):
        account = world[1]["folded-lost"]
        (only,) = account.statements

        assert only.held == Held(2, same=1, folded_unplaced=1)
        assert (only.as_held, only.through_folds, only.held_verdict) == (False, None, None)
        assert "destination is not recorded" in only.held_note
        assert (only.fails, only.cannot_say) == (False, True)
        assert account.failing == []


class TestHistoryByItsReason:
    def test_Statement_WhenItListsAReversedAndAVoidTransaction_SplitsThemAndIsAFault(self, world):
        account = world[1]["history"]
        (only,) = account.statements

        assert only.held == Held(4, same=2, reversed=1, void=1)
        assert (only.as_held, only.through_folds, only.held_verdict) == (False, None, False)
        assert [s.closing for s in account.failing] == [D(2026, 2, 10)]


class TestAStatementThatCannotSeeTheAccountsSpaces:
    def test_Statement_WhenItsLinesReachTheClosingThroughTheSpace_IsTestedWithItsSpaces(
        self, world
    ):
        (only,) = world[1]["blind-main"].statements

        assert only.held == Held(3, same=2, folded_through=1)
        assert (only.as_held, only.through_folds) == (False, True)
        assert (only.with_spaces, only.held_verdict, only.passes) == (True, True, True)

    def test_Statement_WhenItsLinesDoNotReachTheClosing_IsCannotSayAndNeverAFault(self, world):
        account = world[1]["blind2-main"]
        (only,) = account.statements

        assert only.held == Held(2, same=1, different=1)
        assert (only.as_held, only.held_verdict, only.with_spaces) == (False, None, False)
        assert "Spaces" in only.held_note
        assert (only.fails, only.cannot_say) == (False, True)
        assert account.failing == []

    def test_Statement_WhenTheSameShapeIsNotBlind_ThatFailureIsAFault(self, world):
        assert world[1]["merged"].statements[0].fails is True


class TestADayTwoSourcesStateDifferentBalancesFor:
    def test_Closing_WhenTheOtherBalanceDiffersByTheTransactionDatedThatDay_IsExplained(
        self, world
    ):
        (only,) = world[1]["sameday-explained"].statements

        found = only.day_conflict
        assert found is not None
        assert (found.day, found.verdict) == (D(2026, 2, 10), DayReading.SAME_DAY)
        assert (found.unlisted_that_day, found.unlisted_next_day) == (1, 0)

    def test_Closing_WhenTheOtherBalanceDiffersByAnotherAmount_IsNotExplained(self, world):
        (only,) = world[1]["sameday-unexplained"].statements

        found = only.day_conflict
        assert found is not None
        assert found.verdict is DayReading.NOT_EXPLAINED
        assert found.unlisted_that_day == 1

    def test_Closing_WhenTheOtherBalanceDiffersByTheTransactionDatedTheNextDay_SaysSoApart(
        self, world
    ):
        (only,) = world[1]["sameday-nextday"].statements

        found = only.day_conflict
        assert found is not None
        assert found.verdict is DayReading.NEXT_DAY
        assert (found.unlisted_that_day, found.unlisted_next_day) == (0, 1)

    def test_Closing_WhenNoOtherSourceDisagrees_HasNoConflictToExplain(self, world):
        assert column(world, "complete", "day_conflict") == [None] * 4

    def test_Page_WhenTheConflictIsExplained_SaysTheTwoBalancesAreForDifferentMoments(self, world):
        page = statement_listing_html(
            StatementListingReport([world[1]["sameday-explained"]]), AccountsShown()
        )
        text = " ".join(_text_of(page))

        assert "The statement closed before 1 transaction dated that day" in text
        assert "do not contradict each other" in text

    def test_Page_WhenTheConflictIsNotExplained_DoesNotSayThat(self, world):
        page = statement_listing_html(
            StatementListingReport([world[1]["sameday-unexplained"]]), AccountsShown()
        )

        assert "do not contradict each other" not in " ".join(_text_of(page))


class TestCannotSayIsNeverAFaultOnThePage:
    def test_Page_WhenAStatementCannotBeSaid_GivesItsReasonAndNamesNoFault(self, world):
        page = statement_listing_html(
            StatementListingReport([world[1]["folded-lost"]]), AccountsShown()
        )
        text = " ".join(_text_of(page))

        assert "No statement would be reported as a fault." in text
        assert "cannot say - 1 of the transactions it lists was folded" in text
        assert "(1 cannot say)" in text

    def test_Page_WhenAStatementReallyFails_NamesItAsAFault(self, world):
        page = statement_listing_html(
            StatementListingReport([world[1]["history"]]), AccountsShown()
        )

        assert "Would be reported as a real fault" in " ".join(_text_of(page))

    def test_Page_WhenAReadingIsUnavailable_SaysSoRatherThanThatEverythingIsInside(self, world):
        page = statement_listing_html(StatementListingReport([world[1]["unkept"]]), AccountsShown())
        text = " ".join(_text_of(page))

        assert "Every transaction it lists is dated inside its period" not in text
        assert "cannot say - its lines are not available" in text


class TestTheSmallThingsOnThePage:
    def test_Score_WhenOneStatementIsHeld_AgreesTheVerbAndThePronoun(self, world):
        page = statement_listing_html(StatementListingReport([world[1]["first"]]), AccountsShown())

        assert "1 of 1 statement adds up by what it lists" in " ".join(_text_of(page))

    def test_Score_WhenSeveralStatementsAreHeld_UsesThePlural(self, world):
        page = statement_listing_html(
            StatementListingReport([world[1]["complete"]]), AccountsShown()
        )

        assert "4 of 4 statements add up by what they list" in " ".join(_text_of(page))

    def test_Summary_OfEachFoldedDetail_HoldsNoLink(self, world):
        parsed = _Parsed()
        parsed.feed(statement_listing_html(world[2], AccountsShown()))

        assert parsed.details > 0
        assert parsed.links_in_summary == 0


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
        self.links_in_summary = 0
        self._in_code = False
        self._in_summary = False

    def handle_starttag(self, tag, attrs):
        if tag == "code":
            self._in_code = True
        if tag == "summary":
            self._in_summary = True
        if tag == "details":
            self.details += 1
        if tag == "a" and self._in_summary:
            self.links_in_summary += 1
        for name, value in attrs:
            self.attributes.append(f"{name}={value}")
            if tag == "a" and name == "href" and value:
                self.links.append(value)

    def handle_endtag(self, tag):
        if tag == "code":
            self._in_code = False
        if tag == "summary":
            self._in_summary = False

    def handle_data(self, data):
        self.text.append(data)
        if self._in_code:
            self.code.append(data)


def _text_of(page: str) -> list[str]:
    parsed = _Parsed()
    parsed.feed(page)
    return parsed.text
