"""The two index pages: the questions the reports answer, and the evidence behind them.

Each destination gets one sentence saying what question its page answers,
condensed from that page's own introductory text so that the index cannot
promise more than the page delivers. A report is a derived judgement about the
store; evidence is the raw material and the record of how it arrived.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .callback import render_page

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_BACK = '<p><a class="button" href="/">Back to overview</a></p>'

#: (route, title, the question the page answers).
REPORTS: tuple[tuple[str, str, str], ...] = (
    (
        "/agreements",
        "Cross-source agreement",
        "Where do two sources describing the same account disagree over the period "
        "they share, including transposed dates and months another source contradicts?",
    ),
    (
        "/identity-health",
        "Identity health",
        "Do any rows share one identity, and has a payment a provider reported been "
        "folded into another payment's row? Counts and account names only.",
    ),
    (
        "/balance-reconciliation",
        "Balance reconciliation",
        "Do the rows held for each day add up to the bank's own movement and "
        "closing balances? Figures are masked until asked for.",
    ),
    (
        "/balance-walk",
        "Balance walk",
        "Do consecutive running balances differ by exactly the amounts held between "
        "them, or did money move that no held transaction explains?",
    ),
    (
        "/date-lag",
        "Settlement lag",
        "How often does the date a payment settled fall in a different week or month "
        "from the day it happened?",
    ),
    (
        "/review-report",
        "Review queue report",
        "What are the open review flags made of: how many are already proven to be "
        "two payments, and which accounts, sources, and ages hold the rest?",
    ),
)

EVIDENCE: tuple[tuple[str, str, str], ...] = (
    (
        "/artefacts",
        "Raw artefacts",
        "What exactly did each provider send? Every payload landed, newest first, "
        "which everything else derives from.",
    ),
    (
        "/attempts",
        "Fetch attempts",
        "What was asked of each provider and what did it answer, refusals included?",
    ),
    (
        "/fetch-timeline",
        "Fetch timeline",
        "How has the fetch strategy played out over time, with each ask drawn as a "
        "bar over the history it asked about?",
    ),
    (
        "/statement-shape",
        "Statement shape",
        "What layout does a PDF statement have? Its shape is shown with every value "
        "masked, and the file is kept as evidence.",
    ),
    (
        "/statements",
        "Kept statements",
        "Which statements have been kept, whose is each, and which are waiting only "
        "for an account?",
    ),
    (
        "/spaces",
        "Historical Spaces",
        "Which Starling Spaces once moved money but are no longer listed by the "
        "bank, recovered from the feed?",
    ),
)


def index_html(intro: str, entries: tuple[tuple[str, str, str], ...]) -> str:
    items = "".join(
        f'<li class="row"><a class="tap" href="{html.escape(route)}"><strong>'
        f"{html.escape(title)}</strong></a><br>"
        f'<span class="muted">{html.escape(question)}</span></li>'
        for route, title, question in entries
    )
    return f'<p class="muted">{html.escape(intro)}</p><ul class="linklist">{items}</ul>{_BACK}'


class IndexPages:
    """The index routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _reports_index(self) -> None:
        self._respond(
            200,
            render_page(
                "Reports",
                index_html(
                    "Judgements about what the store holds: where sources agree, "
                    "where rows are doubtful, and whether the arithmetic closes.",
                    REPORTS,
                ),
            ),
        )

    def _evidence_index(self) -> None:
        self._respond(
            200,
            render_page(
                "Evidence",
                index_html(
                    "The raw material and the record of how it arrived, for "
                    "checking any report against its source.",
                    EVIDENCE,
                ),
            ),
        )
