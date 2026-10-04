"""One invented morning on the household of `round_up_corpus`, with the answer decided first.

The owner's account of a real morning was: a card payment DECLINED at 05:57, a
top-up into the account at 05:58, and the same merchant's payment going through at
06:11. This is that sequence on invented merchants and figures, on day 9 of
September 2026 (summer time, so the feed's UTC is an hour behind the bank's app:
the times below are the feed's, and the app shows one hour later than each).

    time in the feed (UTC)   what                               item status   makes a row
    04:57                    Cafe, card, 2468 out               DECLINED      no
    04:58                    Friend, faster payment, 5000 in    SETTLED       yes
    05:11                    Cafe, card, 3699 out               SETTLED       yes

The export lists the top-up and omits the Cafe payment, as the deployed export
omits two real payments, so the export's balance sits above the rows by 3699 from
day 9 on.

KNOWN ANSWERS, decided before the first run:

    balance                      the DECLINED item moves nothing: the household's balance
                                 moves by 5000 less 3699 and by no more
    the difference               the export is above the rows by 3699 from Sep 9 on
                                 (measured: the first run of the prediction named Sep 10
                                 and 12 only, and missed that the export's own row on
                                 Sep 9 gives it a balance to compare on that day)
    the one change               at the end of Sep 9, explained by the single row the
                                 export does not list: the Cafe payment
    items that are not rows      one: the DECLINED item, of the same recipient as the Cafe
                                 row and of a different size, 14 minutes before it

The times are chosen so that London's clock reads the sequence the owner read:
05:57, 05:58, and 06:11, an hour after each of the feed's (`LONDON`).
"""

from __future__ import annotations

from typing import Any

from round_up_corpus import card_payment
from test_export_cuts import Row

DECLINED_AT = "2026-09-09T04:57:00.000Z"
TOP_UP_AT = "2026-09-09T04:58:00.000Z"
PAYMENT_AT = "2026-09-09T05:11:00.000Z"

#: How the sequence reads on the bank's app, which shows London time.
LONDON = ("05:57", "05:58", "06:11")

DECLINED_MINOR = 2468
TOP_UP_MINOR = 5000
PAYMENT_MINOR = 3699

#: The figures and words that must not appear on any masked page.
FIGURES = ("2468", "24.68", "5000", "50.00", "3699", "36.99")
WORDS = ("Cafe", "Friend")


def declined_attempt(**more: Any) -> dict[str, Any]:
    return card_payment(
        "f-declined",
        "Cafe",
        DECLINED_MINOR,
        9,
        status="DECLINED",
        transactionTime=DECLINED_AT,
        **more,
    )


def top_up(**more: Any) -> dict[str, Any]:
    return card_payment(
        "f-topup-in",
        "Friend",
        TOP_UP_MINOR,
        9,
        direction="IN",
        source="FASTER_PAYMENTS_IN",
        transactionTime=TOP_UP_AT,
        **more,
    )


def cafe_payment(**more: Any) -> dict[str, Any]:
    return card_payment(
        "f-cafe", "Cafe", PAYMENT_MINOR, 9, transactionTime=PAYMENT_AT, **more
    )


#: The top-up as the export lists it; the Cafe payment is absent from the export.
TOP_UP_LISTED = Row("Friend", TOP_UP_MINOR, 9, 9)


def morning() -> list[dict[str, Any]]:
    """The three items, in the order the feed returned them: the payment first."""
    return [cafe_payment(), top_up(), declined_attempt()]
