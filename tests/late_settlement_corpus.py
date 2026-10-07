"""Payments the bank's export dates by SETTLEMENT, with the answer decided first.

The `round_up_corpus` household (a main account and one Space, September 2026,
amounts in pence) plus payments whose settlement falls on another day than the
payment, in the three sources the way each one dates them:

    the bank's feed     the payment's own day, and a `settlementTime`
    the bank's export   the settlement day, with a correct running balance
    the aggregator      the payment's own day

All amounts and names are invented.

    LATE SETTLEMENT. On 14 September six card payments are made:
        Abroad Shop, -4210 at 10:18 and -1780 at 10:19, both settled on
        20 January 2027 in one second (127 days later);
        Bakery -520, Tailor -640, Florist -1150, Cobbler -905, each settled on
        15 September.
      The export lists the four on 15 September and the two on 20 January.
      KNOWN ANSWER: six stored payments, each sighted by all three sources,
      none counted twice; every stated balance of the export agrees with the
      rows, on every day between the payments and their settlement too.
      Without the rule: eight rows, and the family over by the two late
      payments (5990) from 14 September on.

    EQUAL PAYMENTS, NEAR. Two payments of one size to one payee, made on
    16 and 19 September and settled on 18 and 19 September. The export lists
    them on 18 and 19 September; the variant lists both on 19 September, with
    the settlement times to match.
      KNOWN ANSWER: two rows, each sighted by the feed and the export, the
      aggregator's two on the payments' own days; every balance agrees.

    A STRANGER. An export row of the size and date of a feed payment's
    settlement, to another payee, with no partner anywhere near it.
"""

from __future__ import annotations

import itertools
import json
import pathlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from obdi.core.models import RawArtefact
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.ingest.space_attribution import fold_space_copies
from obdi.ingest.store import Store
from round_up_corpus import SPACE_FEED_ORIGIN, card_payment, main_feed, space_feed
from test_family_anchors import CSV_HEADER, FEED_ORIGIN, land_evidence
from test_space_attribution import BILLS, MAIN, MAP

ARRIVALS = ("feed", "export", "aggregator")
ORDERS = list(itertools.permutations(ARRIVALS))

#: What the household's own export lists, in the order it lists it.
HOUSEHOLD_EXPORT = (
    ("Deposit", 100000, date(2026, 9, 1)),
    ("Coffee", -350, date(2026, 9, 3)),
    ("Grocer", -2310, date(2026, 9, 5)),
    ("Book", -1000, date(2026, 9, 5)),
    ("Taxi", -1275, date(2026, 9, 8)),
    ("Hotel", -3000, date(2026, 9, 10)),
    ("Lunch", -800, date(2026, 9, 12)),
)

LATE_DAY = date(2027, 1, 20)
LATE_FIRST, LATE_SECOND = 4210, 1780
NEXT_DAY_PAYMENTS = (("Bakery", 520), ("Tailor", 640), ("Florist", 1150), ("Cobbler", 905))
LATE_PAYMENTS_TOTAL = LATE_FIRST + LATE_SECOND

EQUAL_AMOUNT = 2000


@dataclass(frozen=True)
class Payment:
    """One payment as the three sources date it."""

    uid: str
    name: str
    minor: int
    made: str
    settled: str | None
    listed: date | None
    #: The aggregator's own day for it, or None when it does not report it.
    reported: int | None = None
    #: Fields the aggregator states for it beyond the common ones, replacing any of them.
    #: A key whose value is None is left out, so an item can state no id at all.
    stated: dict[str, Any] = field(default_factory=dict)
    #: False for a payment only the other sources report.
    in_feed: bool = True
    #: The status the bank's feed gives the item.
    feed_status: str = "SETTLED"

    def feed_item(self) -> dict[str, Any]:
        day = int(self.made[8:10])
        item = card_payment(self.uid, self.name, self.minor, day, status=self.feed_status)
        item["transactionTime"] = self.made
        if self.settled is not None:
            item["settlementTime"] = self.settled
        return item


def late_settlement_payments() -> list[Payment]:
    late = [
        Payment(
            "f-late-1",
            "Abroad Shop",
            LATE_FIRST,
            "2026-09-14T10:18:00.000Z",
            "2027-01-20T02:44:19.000Z",
            LATE_DAY,
            14,
        ),
        Payment(
            "f-late-2",
            "Abroad Shop",
            LATE_SECOND,
            "2026-09-14T10:19:00.000Z",
            "2027-01-20T02:44:19.000Z",
            LATE_DAY,
            14,
        ),
    ]
    others = [
        Payment(
            f"f-next-{name.lower()}",
            name,
            minor,
            f"2026-09-14T10:{20 + position:02}:00.000Z",
            "2026-09-15T03:00:00.000Z",
            date(2026, 9, 15),
            14,
        )
        for position, (name, minor) in enumerate(NEXT_DAY_PAYMENTS)
    ]
    return [*late, *others]


def equal_payments(*, both_listed_on_the_later_day: bool = False) -> list[Payment]:
    return [
        Payment(
            "f-equal-1",
            "Landlord",
            EQUAL_AMOUNT,
            "2026-09-16T10:00:00.000Z",
            "2026-09-19T03:00:00.000Z"
            if both_listed_on_the_later_day
            else "2026-09-18T03:00:00.000Z",
            date(2026, 9, 19) if both_listed_on_the_later_day else date(2026, 9, 18),
            16,
        ),
        Payment(
            "f-equal-2",
            "Landlord",
            EQUAL_AMOUNT,
            "2026-09-19T10:00:00.000Z",
            "2026-09-19T03:00:00.000Z",
            date(2026, 9, 19),
            19,
        ),
    ]


def export_text(listed: list[tuple[str, int, date]]) -> str:
    """The export's file: `listed` in date order, the balance moving with every row."""
    balance = 0
    lines = [CSV_HEADER]
    for name, minor, day in sorted(listed, key=lambda row: row[2]):
        balance += minor
        lines.append(
            f"{day:%d/%m/%Y},{name},{name},FASTER PAYMENT,{minor / 100:.2f},{balance / 100:.2f},"
        )
    return "\n".join(lines) + "\n"


def aggregator_item(payment: Payment, *, link: bool = False) -> dict[str, Any]:
    """The aggregator's report of a payment.

    With `link`, it also states the feed item's own uid, as a real one does:
    at the top level and again under `meta`.
    """
    assert payment.reported is not None
    item: dict[str, Any] = {
        "transaction_id": f"volatile-{payment.uid}",
        "normalised_provider_transaction_id": f"tl-{payment.uid}",
        "timestamp": f"2026-09-{payment.reported:02}T10:00:00Z",
        "description": payment.name.upper(),
        "amount": f"{-payment.minor / 100:.2f}",
        "currency": "GBP",
        "transaction_type": "DEBIT",
    }
    if link:
        item["provider_transaction_id"] = payment.uid
        item["meta"] = {"provider_id": payment.uid}
    item.update(payment.stated)
    return {key: value for key, value in item.items() if value is not None}


def household(
    directory: pathlib.Path,
    order: tuple[str, ...],
    payments: list[Payment],
    *,
    extra_listed: tuple[tuple[str, int, date], ...] = (),
    extra_feed: tuple[dict[str, Any], ...] = (),
    rebuild: bool = False,
    linked: bool = False,
    aggregator_reversed: bool = False,
) -> Store:
    """The household with `payments` added, each source arriving in `order` through the
    door a live pull or import uses, and then (when `rebuild`) the whole store rebuilt from raw.

    A rebuild replays in arrival order (`arrival_order`), so a rebuilt store keeps the
    order a scenario names.
    """
    store = Store(directory / "household.sqlite3")
    land_evidence(store)
    listed = [
        *HOUSEHOLD_EXPORT,
        *((p.name, -p.minor, p.listed) for p in payments if p.listed is not None),
        *extra_listed,
    ]
    path = directory / "export.csv"
    path.write_text(export_text(listed), encoding="utf-8")

    def arrive(artefact: RawArtefact, account_ref: str) -> None:
        store.land_artefact(artefact)
        reconcile_batch(
            store,
            parse_artefact_transactions(
                artefact.source, artefact.payload, account_ref, artefact.digest
            ),
            digest=artefact.digest,
            space_blind=families_of(store, MAP).blind_in,
        )
        fold_space_copies(store, MAP)

    def feed() -> None:
        items = [*main_feed(), *(p.feed_item() for p in payments if p.in_feed), *extra_feed]
        body = json.dumps({"feedItems": items}).encode()
        main = starling.artefact_for(
            body,
            account_id="starling:cat-main",
            kind="feed",
            origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
        )
        arrive(main, MAIN)
        space = starling.artefact_for(
            json.dumps({"feedItems": space_feed()}).encode(),
            account_id="starling:cat-bills",
            kind="feed",
            origin=f"{SPACE_FEED_ORIGIN}?changesSince=2026-09-01T00:00:00Z",
        )
        arrive(space, BILLS)

    def export() -> None:
        import_file(store, path, account_id=MAIN, account_map=MAP)

    def aggregate() -> None:
        reported = [
            aggregator_item(p, link=linked) for p in payments if p.reported is not None
        ]
        if aggregator_reversed:
            reported.reverse()
        arrive(
            truelayer.artefact_for(
                json.dumps({"results": reported}).encode(), account_id="tl-main", kind="booked"
            ),
            MAIN,
        )

    steps = {"feed": feed, "export": export, "aggregator": aggregate}
    for name in order:
        steps[name]()
    if rebuild:
        assert rebuild_from_raw(store, account_map=MAP).problems == []
    return store
