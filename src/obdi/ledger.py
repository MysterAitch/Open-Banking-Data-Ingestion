"""One account's transactions, month by month, with everything the store knows about them.

The question this answers is "what is in this account, and can I trust it".
It lists the merged rows, and beside each says which sources have sighted it,
whether the sources agree about its date, whether it is a transfer, whether a
person is being asked about it, and whether - and why not - Actual would be
sent it.

THE DATA HERE IS REAL VALUES. Whether a reader sees them is decided where the
record is rendered, by `masking.Disclosed`, from the declarations below: a
field is a value unless its type is `Structural[...]`. That is the whole
privacy design, so a new field belongs in the structural group only when a
reader who must not see the money may still see it.

DATED BY VALUE DATE, the date Actual is sent (`to_actual_transaction`) and the
date coverage and the merged key use. A row's sightings may have dated it
differently; that is reported per row, not used to place it.

WHAT THIS DOES NOT DETECT: a BOOKED row one source reported once and stopped
reporting in later fetches over the same dates. Void rows are the store's
record of a pending payment that vanished, and are listed. A booked row that
vanished leaves no record at all in the merged layer, so finding one needs the
fetch history, which this page does not read. The page says so rather than
letting silence read as a pass.

COST: a page costs a fixed number of statements however many rows the account
holds - see QUERIES_PER_PAGE. Nothing is fetched per row. Finding the opening
balance's anchors adds ANCHOR_QUERIES and, where the account has bank records or
held statements, one more per artefact not yet read.

THE OPENING BALANCE is derived in `balance_anchors`, which states how and what
its one weakness is; here it only joins the running position.

TYPED AND DERIVED ROWS are rows like any other here. A transaction a person typed
is a stored row (`typed_transactions`) marked by its origin. The unitemised
changes of an account tracked by its stated balances are not stored: they come
from `effective_opening`, are added to the stored rows before anything is summed
or listed, and are marked by their origin.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from .account_names import AccountsShown
from .accounts import AccountRef
from .agreement import Standing, standing_of
from .balance_anchors import (
    ASSUMED_NIL,
    CURRENCY,
    STATED,
    Anchor,
    EffectiveOpening,
    FamilyWalk,
    anchor_key,
    can_be_disregarded,
    effective_opening,
    place_among,
    removed_stated_anchors,
)
from .bank_balances import BANK_SOURCE
from .bank_balances import describe as describe_bank
from .clearing import ClearingView, cleared_by, clearing_counts
from .family_anchors import OPENED, Families
from .fault_explanation import WalkExplanation
from .fault_structure import StructureReport, account_report, walk_report
from .feed_statuses import FeedStatuses
from .identity_health import provider_ids_by_row, shared_identity_groups
from .join_basis import JoinCounts, SightingView, join_counts_of_bases, sighting_views
from .masking import Structural, Total
from .models import Transaction
from .namespaces import CASH_LEG_SOURCE, MANUAL_SOURCE, UNITEMISED_SOURCE
from .protection import Check, ProtectionView, check_span, protection_view
from .replay import ReplayError, to_actual_transaction, withheld_reason
from .round_up_accounts import RoundUpGaps
from .row_balances import balances_after
from .spaces import ArchiveNote
from .standing_data import statement_checks_for
from .statement_checks import StatementChecks
from .store import Store
from .typed_transactions import TypedEntry, typed_entries

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .movement_completeness import MovementCompleteness

#: Statements issued for one account that holds rows: its rows, the pairing
#: table, the sightings, the provider ids, the shared identities, the open
#: review flags, the two annotation kinds, the one read of every sighting's basis
#: (`join_basis.join_counts_of_bases`, from the read that gives each source's date), and the
#: two reads of the stated dates and the coded words its sources stated for the month's rows
#: alone (`Store.sighting_details_of`, which asks in chunks of 400, so a month of more rows adds
#: two for each chunk beyond the first; a month holding none issues neither). An account with
#: no rows adds the registry lookup that tells "declared but empty" from "unknown".
QUERIES_PER_PAGE = 11

#: Statements issued to look for the account's opening-balance anchors when it
#: has no TrueLayer records and no held statements: the stated balances, the
#: bank-record reconciliation (cards, rows, pending, sightings), the held
#: statement listing, the sections of "all accounts" statements assigned to
#: accounts, the declared kind that says whether the stated balances are
#: followed or checked, the record of stated balances removed, and the balances a person has
#: disregarded. Each TrueLayer artefact a row's balance has to be found in, and each held
#: statement not yet read, adds statements beyond this, so a page for such an account costs more
#: and the fixed figure is a floor.
ANCHOR_QUERIES = 10

#: Statements a page issues to find what an account's statements conclude by what they list
#: (`standing_data.statement_checks_for`, read by the verdict) once the store's checks are held:
#: the one read of the standing epoch that says they still are. The checks themselves are worked
#: out once per store state and shared by every page and account, held or not (see
#: `standing_data._CHECKS`), so this does not follow the number of statements in the store, which
#: made the page cost 618 statements instead of 77 on a store of 96 statements.
STATEMENT_CHECK_QUERIES = 1

#: What asking for the FAMILY reading adds to an account's page, on top of
#: ANCHOR_QUERIES, once `families_of` has been built (itself FAMILY_DISCOVERY_QUERIES
#: statements, once per request and shared by every account asked about): for a
#: main account with known Spaces, the held-statement listing again for the
#: per-day balances, the listing of held exports, the provider's account and
#: Space listings (creation date, categories), the feed requests held (how far
#: back the feed reaches), and one read of the rows of each Space. Each export
#: not yet read adds one more, as each statement does. An account with no known
#: Spaces adds nothing: ANCHOR_QUERIES holds. The two after the listings are
#: what the opened anchor costs (`family_anchors.opening_evidence`), and the
#: next is the one read of the account's rows and sightings that decides what
#: each blind source's balances mean (`balance_meaning`), read only when some
#: blind source states one, the next is the one read of the days those
#: sources gave the family's rows (`sighting_placement`), made only when a
#: source states a balance, and the last lists the feed artefacts that sighted
#: the account's rows, whose round-ups are counted
#: (`family_anchors.feed_round_ups`). Each such artefact not yet read adds one
#: more, once per process. The last is the confirmed transfer pairs, which say
#: whether each round-up leg and each Space leg has a partner
#: (`round_up_accounts`), and the very last is the listing of the balances the
#: bank landed (`bank_balances.landed_balances`), which any main account bound
#: to the bank's own feed pays, Spaces or not, and which is the one extra
#: statement a Starling account without Spaces adds to ANCHOR_QUERIES. None
#: depends on what the evidence turns out to say. Where a balance is judged,
#: the feed's own times for the recent rows (`bank_balances.FeedMoments`) add
#: one read of their sightings and one per feed artefact not yet read, once
#: per process.
FAMILY_QUERIES = 9
FAMILY_DISCOVERY_QUERIES = 2

#: What an account fed by the bank's own feed adds to any page of it: the feed artefacts
#: that sighted its rows, the feed uids of its rows, and the order the artefacts landed
#: in, from which each row's feed time is read (`feed_statuses`). Each such artefact
#: not yet read adds one more, once per process. An account the bank's feed does not
#: feed adds nothing.
FEED_TIME_QUERIES = 3

#: Sorts a row with no feed time, which is never compared with one that has a time.
_NO_TIME = datetime.min.replace(tzinfo=UTC)

#: What asking for the family reading adds to the page of a SPACE: the one read of the
#: landed Space listings, whose balance for the Space is a checkpoint of its own
#: (`bank_balances.landed_listing_balances`). Each listing not yet read adds nothing
#: further: its body is read once per process from the same statement.
SPACE_QUERIES = 1

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")

#: Symbols for the currencies the store is likely to hold. Any other currency
#: is shown as bare digits and its code travels in the row's own `currency`.
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


class LedgerRequestError(ValueError):
    """The caller asked for something that is not a month."""


@dataclass(frozen=True)
class LedgerWindow:
    """The days, both included, a ledger lists in place of a calendar month.

    `fall_back` is for a window nobody chose, the page's default: where the account holds
    nothing dated in it, the newest month is shown instead (and the ledger says so,
    `Ledger.window_fell_back`), since an account whose feed went quiet would otherwise open on
    a page of nothing. A window somebody chose is shown as chosen however empty it is.
    """

    first: date
    last: date
    fall_back: bool = False
    #: Where above 0, `first` moves back to the day the newest `at_least` transactions begin
    #: on, if that is earlier (`Ledger.window_widened`).
    at_least: int = 0


class LedgerData(Protocol):
    """What the page's data hook is called with: an account, a month ("" for the newest), and the
    window to list in place of the month where there is one."""

    def __call__(self, ref: str, month: str, window: LedgerWindow | None = None) -> Ledger: ...


@dataclass(frozen=True)
class Money:
    """An amount's magnitude as a person writes it. Direction is kept apart.

    The sign is structure and the figure is value, and a masked page shows one
    without the other, so they are never carried in the same string.
    """

    minor: int
    currency: str

    def __str__(self) -> str:
        whole, pence = divmod(abs(self.minor), 100)
        return f"{_SYMBOLS.get(self.currency, '')}{whole:,}.{pence:02d}"


def direction_of(minor: int) -> str:
    if minor > 0:
        return "in"
    return "out" if minor < 0 else "nil"


#: Where a row came from when it is not simply a feed's: a person typed it, or it
#: is the arithmetic of two stated balances.
ORIGIN_TYPED = "typed"
ORIGIN_UNITEMISED = "unitemised"


@dataclass(frozen=True)
class TypedLine:
    """One typed transaction, for the list from which it can be withdrawn."""

    #: Minted at entry and carrying nothing about the payment.
    entry_id: Structural[str]
    day: Structural[str]
    direction: Structural[str]
    withdrawn: Structural[bool]

    amount: Money
    description: str


@dataclass(frozen=True)
class TypedLines:
    #: The month on show, newest first, withdrawn ones included.
    lines: Structural[tuple[TypedLine, ...]]
    #: Live entries dated in other months, which are withdrawn from their own month.
    live_elsewhere: Structural[int]
    #: Entries withdrawn in all, whichever month.
    withdrawn_total: Structural[int]


@dataclass(frozen=True)
class UnitemisedLine:
    """One derived change: the movement between two stated balances no row explains."""

    #: The later stated balance's date, which dates the change.
    day: Structural[str]
    #: The stated balance it follows, or "" for none.
    since: Structural[str]
    direction: Structural[str]

    amount: Money


@dataclass(frozen=True)
class LedgerRow:
    #: "typed", "unitemised", or "" for a row a source reported.
    origin: Structural[str]
    dated: Structural[date]
    #: The date each source gave, where a source gave one: (source, ISO date).
    observed: Structural[tuple[tuple[str, str], ...]]
    direction: Structural[str]
    currency: Structural[str]
    status: Structural[str]
    #: Every source with a sighting of this row, not the one that wrote it last.
    sources: Structural[tuple[str, ...]]
    #: Sources that sighted the row under different dates.
    dates_differ: Structural[bool]
    #: Seen by one source in an account that more than one feeds.
    one_source: Structural[bool]
    #: "", "confirmed" (the other side is held), or "claimed" (it is not).
    transfer: Structural[str]
    transfer_other_account: Structural[str]
    review_open: Structural[bool]
    #: Why Actual is not sent this row, or "" when it is.
    withheld: Structural[str]
    #: The push builder refuses this row outright, which fails the whole push.
    unsendable: Structural[bool]
    #: Another row in the account holds the same content key and occurrence.
    shares_identity: Structural[bool]
    #: Distinct provider ids one source reported against this row, when more
    #: than one - a payment folded into it. 0 when there is no such fold.
    absorbed_ids: Structural[int]
    has_counterparty: Structural[bool]
    #: Who set the annotations ("human", "model", "rule"), or "".
    annotated_by: Structural[str]

    # Everything below is a VALUE: masked unless the reader asked for values.
    description: str
    counterparty: str
    amount: Money
    review_reason: str
    send_refusal: str
    category: str
    payee: str

    #: The sources that clear this row (`clearing`); empty for a row no authoritative listing holds.
    cleared_by: Structural[tuple[str, ...]] = ()
    #: The instant the newest landed feed states for the row's item, in UTC (`feed_statuses`);
    #: None for a row no feed item is its own. A time is structure, like a date.
    feed_at: Structural[datetime | None] = None
    #: Each source's sighting: the basis it joined on and every date it stated (`join_basis`).
    sightings: Structural[tuple[SightingView, ...]] = ()
    #: A stable key for the page to anchor the row by (`row_anchor`): derived from the row's id and
    #: carrying nothing of its amount, description, or date.
    anchor: Structural[str] = ""
    #: The balance the account held after this row, signed, or "" where no balance of the account
    #: counts the row (`row_balances.balances_after`). A balance: its size is hidden when masked.
    balance_after: Total[str] = ""
    #: The day the bank posted the row, as opposed to `dated`, the day it counted.
    booked: Structural[date | None] = None
    #: Where a confirmed transfer's other leg is listed: its row key and month (`OtherLeg`), and
    #: the other account's label ("" where it has none).
    transfer_other_anchor: Structural[str] = ""
    transfer_other_month: Structural[str] = ""
    transfer_other_label: Structural[str] = ""


def row_anchor(entity_id: str) -> str:
    """A short, stable, opaque key for a row, so a link can name the row without any value in it."""
    return hashlib.sha256(entity_id.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class MonthSummary:
    rows: Structural[int]
    per_source: Structural[tuple[tuple[str, int], ...]]
    multi_source: Structural[int]
    one_source: Structural[int]
    pending: Structural[int]
    void: Structural[int]
    #: Rows that copy a payment counted elsewhere: held under a Space, or the same
    #: money a statement itemises (`replay.WITHHELD_FOLDED` says both).
    folded: Structural[int]
    transfers_confirmed: Structural[int]
    transfers_claimed: Structural[int]
    review_open: Structural[int]
    would_send: Structural[int]
    withheld: Structural[int]
    withheld_by_reason: Structural[tuple[tuple[str, int], ...]]
    unsendable: Structural[int]
    #: Void and folded rows are history, not money, and are in neither sum.
    store_direction: Structural[str]
    sent_direction: Structural[str]
    sums_differ: Structural[bool]
    #: More than one currency among the rows, so the sums add unlike units.
    mixed_currency: Structural[bool]

    store_sum: Total[Money]
    sent_sum: Total[Money]
    #: Rows the account's authoritative sources list, and rows only a relay or a person reports.
    cleared: Structural[int] = 0
    uncleared: Structural[int] = 0


@dataclass(frozen=True)
class Position:
    #: The last day counted, ISO.
    through: Structural[str]
    rows_counted: Structural[int]
    #: Whether both figures start from the account's derived opening balance.
    #: When False they start from zero, which is a statement about the figures
    #: and not a claim that the account opened empty.
    opening_included: Structural[bool]
    store_direction: Structural[str]
    sent_direction: Structural[str]
    differs: Structural[bool]

    store_balance: Total[Money]
    sent_balance: Total[Money]


@dataclass(frozen=True)
class AnchorLine:
    """One anchor: a fact about the account's balance at the end of a day."""

    day: Structural[str]
    #: "stated", "bank", or "statement": where the fact was said.
    basis: Structural[str]
    #: The earliest anchor, the only one the opening balance is derived from.
    defines_opening: Structural[bool]
    #: "" for the defining anchor, otherwise "agrees" or "differs".
    verdict: Structural[str]
    balance_direction: Structural[str]
    #: Which way the anchor sits from what the rows predict, "nil" if it agrees.
    difference_direction: Structural[str]
    #: The source that states it, "" where none does.
    source: Structural[str]
    #: What the whole-account walk says of the same balance from the same source:
    #: "agrees", "differs", or "" where the walk does not hold it.
    walk: Structural[str]

    balance: Total[Money]
    #: Zero for the defining anchor and for one that agrees.
    difference: Total[Money]


@dataclass(frozen=True)
class SharedDayEntry:
    """One source's balance for a day on which several are stated."""

    day: Structural[str]
    stating: Structural[str]
    basis: Structural[str]
    disregarded: Structural[bool]
    #: The disregard names a balance that is no longer held, so it changes nothing.
    stale: Structural[bool] = False
    #: A button to disregard it is offered: only the kinds a disregard reaches
    #: (`balance_anchors.DISREGARDABLE_BASES`), and not one already disregarded.
    can_disregard: Structural[bool] = False
    #: Which of the balances under one key (day, whoever states it, basis) this is, so that two
    #: statements of one layout closing on one day can be told apart without the page holding a
    #: figure. The order is `balance_anchors.place_among`'s, which does not follow the figures'
    #: size (a page must not say which is lower) and does not move when one is disregarded.
    which: Structural[int] = 0


@dataclass(frozen=True)
class SharedDay:
    """The balances stated for one day by several sources, and whether the figures are equal.

    Equality is said and the figures are not: a GET page holds no balance. "equal" and "differ"
    compare the day-end balances still in use; "one" means only one of them still is, the rest
    having been disregarded.
    """

    day: Structural[str]
    entries: Structural[tuple[SharedDayEntry, ...]]
    figures: Structural[str]


@dataclass(frozen=True)
class RemovedBalance:
    """A stated balance a person removed: when it applied, when it went, and what it was."""

    day: Structural[str]
    source: Structural[str]
    removed_at: Structural[str]
    balance_direction: Structural[str]
    balance: Total[Money]
    #: The figure as the stating form takes it, so the values view can offer to state it again.
    restate_as: Total[str]


@dataclass(frozen=True)
class FamilyLine:
    """One family balance that the family's rows do not reproduce."""

    day: Structural[str]
    sources: Structural[tuple[str, ...]]
    difference_direction: Structural[str]

    balance: Total[Money]
    difference: Total[Money]


@dataclass(frozen=True)
class FamilyView:
    """The family's balances walked against the family's rows (`walk_family`)."""

    spaces: Structural[tuple[str, ...]]
    sources: Structural[tuple[str, ...]]
    anchors: Structural[int]
    #: Later balances the rows reproduce, and ones they do not.
    agreeing: Structural[int]
    differing: Structural[int]
    #: Printed balances refused for disagreeing with their own statement.
    refused_figures: Structural[int]
    withheld: Structural[str]
    #: ISO days, "" when nothing differs: the first balance the rows stop
    #: reproducing and the last one they reproduced before it.
    first_differing: Structural[str]
    last_agreeing: Structural[str]
    #: "constant" (one movement missing or surplus between those two days),
    #: "changing" (several), or "" when nothing differs.
    pattern: Structural[str]
    #: ISO days, earliest first, on which the difference from the rows changes
    #: (`FamilyWalk.changes`), and those of them that a transfer to a Space
    #: whose rows are not held explains.
    change_days: Structural[tuple[str, ...]]
    unheld_change_days: Structural[tuple[str, ...]]
    defining_day: Structural[str]
    lines: Structural[tuple[FamilyLine, ...]]
    #: The account's creation date when the provider states it, and the day of
    #: the nil anchor ("" without it, with `opening_missing` saying why).
    created_on: Structural[str]
    nil_day: Structural[str]
    opening_missing: Structural[str]
    #: A sentence saying whether the opening is nil and what that means for faults.
    opening_note: Structural[str]
    #: Counted rows dated before the account was created.
    before_opening: Structural[int]
    #: Transfer legs to a Space whose rows are not held, and the first one's date.
    unheld_legs: Structural[int]
    unheld_first: Structural[str]
    #: Of the Spaces those legs name, how many the provider refused when asked
    #: for their history and how many it answered with nothing, each with the
    #: newest date ("" for none).
    unheld_refused: Structural[int]
    unheld_refused_on: Structural[str]
    unheld_empty: Structural[int]
    unheld_empty_on: Structural[str]
    #: How the feed's round-ups stand, as counts (`family_anchors.RoundUpTally`).
    round_ups_carried: Structural[int]
    round_up_legs: Structural[int]
    round_up_legs_paired: Structural[int]
    round_ups_unreadable: Structural[int]
    #: The round-ups that did not become a paired leg (`round_up_accounts`).
    round_up_gaps: Structural[RoundUpGaps]
    #: Why each of the first changes happened, by exact arithmetic, and what the
    #: held exports are like (`fault_explanation`); every field of it is structural.
    explanation: Structural[WalkExplanation | None]
    #: What shape the changes take (`fault_structure`); a figure in it is a
    #: `Total`, so it is shown masked or not without a figure.
    structure: Structural[StructureReport | None]


@dataclass(frozen=True)
class MeaningLine:
    """What a source's own rows say its stated balance means (`balance_meaning`)."""

    source: Structural[str]
    #: Steps that tell the two readings apart, and how many each explains.
    steps: Structural[int]
    whole: Structural[int]
    main: Structural[int]
    #: "whole", "main", "both", "neither", or "undecided".
    verdict: Structural[str]


@dataclass(frozen=True)
class OpeningView:
    #: "none" (no anchor, so no opening balance), "derived", or "withheld"
    #: (anchors exist but an opening cannot be derived; `withheld` says why).
    state: Structural[str]
    withheld: Structural[str]
    #: The opening is the balance at the end of this ISO day.
    as_at: Structural[str]
    direction: Structural[str]
    #: Only one anchor, so the opening absorbs every missing or surplus row
    #: before it and nothing can tell.
    single_anchor: Structural[bool]
    unusable_statements: Structural[int]
    anchors: Structural[tuple[AnchorLine, ...]]
    #: The dates of the anchors a person stated, which are the ones that can
    #: be removed from the page.
    stated_days: Structural[tuple[str, ...]]
    #: Set for a main account with known Spaces and a family balance stated.
    family: Structural[FamilyView | None]
    #: The account is tracked by its stated balances alone, so a later stated
    #: balance is followed rather than checked (see `balance_anchors`).
    balance_only: Structural[bool]
    #: How each Space-blind source's balances were read, in counts.
    meanings: Structural[tuple[MeaningLine, ...]]
    #: The shape of the changes in the account's OWN stated balances, for an
    #: account whose chart is not the whole-account walk (`balance_chart`);
    #: None where the family walk is the one shown, or nothing differs.
    own_structure: Structural[StructureReport | None]
    #: Why each change among the account's own anchors happened
    #: (`EffectiveOpening.explanation`); None where there is nothing to explain.
    own_explanation: Structural[WalkExplanation | None]
    #: What the bank's own landed balances were and how they were read
    #: (`bank_balances.describe`), then what the newest says about the open
    #: differences (`BankReport.sayings`). Sentences of counts, days, and source
    #: names; empty where none was landed.
    bank_lines: Structural[tuple[str, ...]]
    bank_sayings: Structural[tuple[str, ...]]
    #: The days on which several sources state a balance, or a balance differs from the rows,
    #: with each source and whether the figures are equal (`SharedDay`). Capped at
    #: `SHARED_DAYS_LISTED`, days needing a look first; `shared_more` is the count not listed.
    shared_days: Structural[tuple[SharedDay, ...]]
    shared_more: Structural[int]
    #: The balances a person has disregarded, still shown and marked.
    disregarded: Structural[tuple[SharedDayEntry, ...]]

    opening: Total[Money]


@dataclass(frozen=True)
class Ledger:
    ref: Structural[str]
    label: Structural[str]
    #: "ok", "empty-month", "no-rows" (declared, nothing held), or "unknown".
    state: Structural[str]
    sources: Structural[tuple[str, ...]]
    actual_bound: Structural[bool]
    #: The month shown, or "" when the account holds nothing to show.
    month: Structural[str]
    previous_month: Structural[str]
    next_month: Structural[str]
    oldest_month: Structural[str]
    newest_month: Structural[str]
    summary: Structural[MonthSummary | None]
    position: Structural[Position | None]
    rows: Structural[tuple[LedgerRow, ...]]
    #: Set when the account is archived or the provider's listings say its Space
    #: has left. Handed in rather than read here, so a page still costs
    #: QUERIES_PER_PAGE statements for the ledger proper.
    archive: Structural[ArchiveNote | None] = None
    #: Absent only for an account nothing is known about.
    opening: Structural[OpeningView | None] = None
    #: What a person has typed into the account; empty for one that is unknown.
    typed: Structural[TypedLines | None] = None
    #: Stated balances removed and not stated again, newest removal first.
    removed_balances: Structural[tuple[RemovedBalance, ...]] = ()
    #: The changes derived from a balance-only account's stated balances.
    unitemised: Structural[tuple[UnitemisedLine, ...]] = ()
    #: Cleared and uncleared rows, per month and in all (`clearing`).
    clearing: Structural[ClearingView | None] = None
    #: How far the account is in agreement, and for a family the whole account too (`agreement`).
    standing: Structural[Standing | None] = None
    #: What each of the account's statements concludes by what it lists, and how many a
    #: calendar-day test would have reproduced (`statement_listing_measure.statement_checks`);
    #: None where the account holds no statement.
    statements: Structural[StatementChecks | None] = None
    #: The account's protection (`protection`), set by the caller that reads the declared state
    #: so that the ledger proper costs the statements it always did.
    protection: Structural[ProtectionView | None] = None
    #: The account's rows by how their sightings joined, over every month (`join_basis`).
    joins: Structural[JoinCounts | None] = None
    #: Whether the rows carry the balance after each (`LedgerRow.balance_after`): only where a known
    #: balance anchors the account, which a page without one says in "How this was checked".
    running_shown: Structural[bool] = False
    #: The sentence the verification says while a rebuild holds the derived layer
    #: (`rebuild_hold`), in place of the standing and the protection; empty otherwise.
    rebuilding: Structural[str] = ""
    #: Rows held in each month, oldest first, (ISO "YYYY-MM", count): what a month picker needs
    #: and read from the rows already in hand, so it costs no statement.
    month_counts: Structural[tuple[tuple[str, int], ...]] = ()
    #: The first and last day listed, ISO, when the rows are a window of days and not a calendar
    #: month (`LedgerWindow`); both empty for a month, whose `month` is then set instead.
    window_first: Structural[str] = ""
    window_last: Structural[str] = ""
    #: The default window held nothing, so the newest month is listed (`LedgerWindow.fall_back`).
    window_fell_back: Structural[bool] = False
    #: The window was widened past its days to take in this many transactions
    #: (`LedgerWindow.at_least`); 0 where its days held that many, or it was not asked to.
    window_widened: Structural[int] = 0
    #: What the page says the window is, such as "Last 30 days", set by the caller that read the
    #: request, and the one form field that carries the span on show into the next request
    #: (`ledger_scope`). Both are empty for a ledger built without a request.
    window_words: Structural[str] = ""
    scope: Structural[str] = ""


def family_view(walk: FamilyWalk | None) -> FamilyView | None:
    if walk is None:
        return None
    first, last = walk.first_differing, walk.last_agreeing
    changes = walk.changes
    return FamilyView(
        change_days=tuple(dict.fromkeys(c.day.isoformat() for c in changes)),
        unheld_change_days=tuple(dict.fromkeys(c.day.isoformat() for c in changes if c.unheld)),
        spaces=walk.spaces,
        sources=walk.sources,
        anchors=walk.anchors,
        agreeing=walk.agreeing,
        differing=len(walk.differing),
        refused_figures=walk.refused_figures,
        withheld=walk.withheld,
        first_differing=first.day.isoformat() if first else "",
        last_agreeing=last.day.isoformat() if last else "",
        pattern=(
            "" if walk.constant is None else "constant" if walk.constant else "changing"
        ),
        defining_day=(
            walk.opened.day.isoformat()
            if walk.opened
            else walk.readings[0].day.isoformat()
            if walk.readings
            else ""
        ),
        created_on=(
            walk.evidence.created.isoformat() if walk.evidence and walk.evidence.created else ""
        ),
        nil_day=walk.opened.day.isoformat() if walk.opened else "",
        opening_missing=walk.evidence.missing if walk.evidence else "",
        opening_note=walk.opening_note,
        before_opening=walk.before_opening,
        unheld_legs=walk.unheld.legs,
        unheld_first=walk.unheld.first.isoformat() if walk.unheld.first else "",
        unheld_refused=sum(1 for f in walk.unheld.fetches if f.outcome == "refused"),
        unheld_refused_on=max(
            (f.on.isoformat() for f in walk.unheld.fetches if f.outcome == "refused"), default=""
        ),
        unheld_empty=sum(1 for f in walk.unheld.fetches if f.outcome == "empty"),
        unheld_empty_on=max(
            (f.on.isoformat() for f in walk.unheld.fetches if f.outcome == "empty"), default=""
        ),
        round_ups_carried=walk.round_ups.carried,
        round_up_legs=walk.round_ups.legs,
        round_up_legs_paired=walk.round_ups.paired,
        round_ups_unreadable=walk.round_ups.unreadable,
        round_up_gaps=walk.round_up_gaps,
        explanation=walk.explanation,
        structure=walk_report(walk) if walk.readings else None,
        lines=tuple(
            FamilyLine(
                day=reading.day.isoformat(),
                sources=reading.sources,
                difference_direction=direction_of(reading.difference_minor or 0),
                balance=Money(reading.balance_minor, CURRENCY),
                difference=Money(reading.difference_minor or 0, CURRENCY),
            )
            for reading in walk.differing
        ),
    )


def _verdict(agrees: bool | None) -> str:
    return "" if agrees is None else "agrees" if agrees else "differs"


#: How many days on which sources state a balance are listed on the account page.
SHARED_DAYS_LISTED = 12

_NIL_BASES = (OPENED, ASSUMED_NIL)


def _ranks(opening: EffectiveOpening) -> dict[tuple[object, ...], int]:
    """Each balance's place among those under its key (day, whoever states it, basis): the ones
    still read that a disregard reaches, the ones disregarded, and the disregards no longer
    applying, all in ONE order (`place_among`), so a place does not move when one is disregarded."""
    anchors = [
        *(r.anchor for r in opening.readings if can_be_disregarded(r.anchor)),
        *opening.disregarded,
        *opening.stale_disregards,
    ]
    figures: dict[tuple[date, str, str], list[int]] = {}
    for a in anchors:
        figures.setdefault(anchor_key(a), []).append(a.balance_minor)
    ranks: dict[tuple[object, ...], int] = {}
    for key, found in figures.items():
        for place, figure in enumerate(place_among(found, key)):
            ranks[(*key, figure)] = place
    return ranks


def _disregards(opening: EffectiveOpening) -> tuple[SharedDayEntry, ...]:
    """Every balance disregarded, and every disregard whose balance is no longer held."""
    ranks = _ranks(opening)
    found = [
        SharedDayEntry(
            a.day.isoformat(),
            a.stating,
            a.basis,
            stale is False,
            stale,
            False,
            ranks[(*anchor_key(a), a.balance_minor)],
        )
        for stale, group in ((False, opening.disregarded), (True, opening.stale_disregards))
        for a in group
    ]
    return tuple(sorted(found, key=lambda e: (e.day, e.stating, e.basis, e.which)))


def _shared_days(opening: EffectiveOpening) -> tuple[tuple[SharedDay, ...], int]:
    """The days worth laying the sources side by side: several state a balance, or one differs
    from what the rows predict. Days needing a look come first, then newest first, and only the
    first `SHARED_DAYS_LISTED` are returned."""
    #: state of each balance: "read", "disregarded", or "stale" (no longer held).
    by_day: dict[date, list[tuple[Anchor, str]]] = {}
    for reading in opening.readings:
        if reading.anchor.basis not in _NIL_BASES:
            by_day.setdefault(reading.anchor.day, []).append((reading.anchor, "read"))
    differing = {r.anchor.day for r in opening.readings if r.agrees is False}
    for anchor in opening.disregarded:
        by_day.setdefault(anchor.day, []).append((anchor, "disregarded"))
    for anchor in opening.stale_disregards:
        by_day.setdefault(anchor.day, []).append((anchor, "stale"))

    def figures_of(day: date) -> str:
        in_use = [a for a, state in by_day[day] if state == "read" and a.at is None]
        figures = {a.balance_minor for a in in_use}
        return "one" if len(in_use) <= 1 else "equal" if len(figures) == 1 else "differ"

    def needs_a_look(day: date) -> bool:
        return figures_of(day) != "equal" or any(s != "read" for _, s in by_day[day])

    ranks = _ranks(opening)

    # Days that need a look come before the routine ones, where sources state one figure, so a
    # cap never hides a conflict behind them.
    chosen = sorted(
        (
            day
            for day, held in by_day.items()
            if len(held) > 1 or day in differing or any(s == "stale" for _, s in held)
        ),
        key=lambda day: (not needs_a_look(day), -day.toordinal()),
    )
    found = []
    for day in chosen[:SHARED_DAYS_LISTED]:
        held = by_day[day]
        found.append(
            SharedDay(
                day.isoformat(),
                tuple(
                    SharedDayEntry(
                        day.isoformat(),
                        a.stating,
                        a.basis,
                        state == "disregarded",
                        state == "stale",
                        state == "read" and can_be_disregarded(a),
                        ranks.get((*anchor_key(a), a.balance_minor), 0),
                    )
                    for a, state in sorted(
                        held, key=lambda h: (h[1], h[0].stating, h[0].basis, h[0].balance_minor)
                    )
                ),
                figures_of(day),
            )
        )
    return tuple(found), max(len(chosen) - SHARED_DAYS_LISTED, 0)


def opening_view(opening: EffectiveOpening) -> OpeningView:
    shared, shared_more = _shared_days(opening)
    lines = []
    walked = {
        (r.day, r.sources[0]): _verdict(r.agrees)
        for r in (opening.family.readings if opening.family else ())
    }
    for reading in opening.readings:
        anchor = reading.anchor
        difference = reading.difference_minor or 0
        lines.append(
            AnchorLine(
                day=anchor.day.isoformat(),
                basis=anchor.basis,
                defines_opening=reading.defines_opening,
                verdict=_verdict(reading.agrees),
                source=anchor.source,
                walk=walked.get((anchor.day, anchor.source), ""),
                balance_direction=direction_of(anchor.balance_minor),
                difference_direction=direction_of(difference),
                balance=Money(anchor.balance_minor, CURRENCY),
                difference=Money(difference, CURRENCY),
            )
        )
    derived = opening.opening_minor is not None and opening.as_at is not None
    return OpeningView(
        state=(
            "derived" if derived else "withheld" if opening.readings else "none"
        ),
        withheld=opening.withheld,
        as_at=opening.as_at.isoformat() if opening.as_at else "",
        direction=direction_of(opening.opening_minor or 0),
        single_anchor=opening.single_anchor,
        unusable_statements=opening.unusable_statements,
        anchors=tuple(lines),
        stated_days=tuple(
            r.anchor.day.isoformat() for r in opening.readings if r.anchor.basis == STATED
        ),
        family=family_view(opening.family),
        balance_only=opening.balance_only,
        meanings=tuple(
            MeaningLine(m.source, m.steps, m.whole, m.main, m.verdict) for m in opening.meanings
        ),
        own_structure=(
            account_report(opening)
            if not (opening.family and opening.family.readings) and opening.differing
            else None
        ),
        own_explanation=opening.explanation,
        bank_lines=describe_bank(opening.bank) if opening.bank is not None else (),
        bank_sayings=opening.bank.sayings if opening.bank is not None else (),
        shared_days=shared,
        shared_more=shared_more,
        disregarded=_disregards(opening),
        opening=Money(opening.opening_minor or 0, CURRENCY),
    )


def running_balance(
    opening_minor: int, rows: Iterable[Transaction], through: date | None = None
) -> int:
    """The balance by the store's own rows: the opening plus every row that is money.

    A void, folded, or reversed row is history and is never counted.

    Rows are counted when dated on or before `through` (all of them when None).
    The ledger's running position and the position page both call this, so the
    two cannot come to differ about what an account holds.
    """
    return opening_minor + sum(
        t.amount_minor
        for t in rows
        if not t.status.is_history
        and (through is None or t.value_date <= through)
    )


def parse_month(text: str) -> tuple[int, int]:
    found = _MONTH.match(text.strip())
    if found is None:
        raise LedgerRequestError("a month is written YYYY-MM")
    year, month = int(found.group(1)), int(found.group(2))
    if not 1 <= month <= 12 or year < 1:
        raise LedgerRequestError("a month is written YYYY-MM")
    return year, month


def _label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _neighbour(year: int, month: int, step: int) -> str:
    index = year * 12 + (month - 1) + step
    if index < 12:
        return ""
    return _label(index // 12, index % 12 + 1)


def _month_of(day: date) -> str:
    return _label(day.year, day.month)


def _month_end(year: int, month: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    return following - timedelta(days=1)


def _empty(
    ref: str, label: str, state: str, bound: bool, *, month: str = ""
) -> Ledger:
    return Ledger(
        ref=ref,
        label=label,
        state=state,
        sources=(),
        actual_bound=bound,
        month=month,
        previous_month="",
        next_month="",
        oldest_month="",
        newest_month="",
        summary=None,
        position=None,
        rows=(),
    )


class OpeningReader(Protocol):
    """What `effective_opening` is called with, for a caller that answers it from a held reading."""

    def __call__(
        self,
        store: Store,
        ref: str,
        rows: list[Transaction] | None = None,
        *,
        families: Families | None = None,
        explain_after: date | None = None,
    ) -> EffectiveOpening: ...


def build_ledger(
    store: Store,
    ref: str,
    month: str | None,
    *,
    bound: bool,
    label: str = "",
    archive: ArchiveNote | None = None,
    families: Families | None = None,
    movement: MovementCompleteness | None = None,
    explain_after: date | None = None,
    with_protection: bool = False,
    opening_reader: OpeningReader | None = None,
    window: LedgerWindow | None = None,
    names: AccountsShown | None = None,
) -> Ledger:
    """The account's ledger for one month, or the newest month when `month` is None, or for a
    window of days when `window` is given (which `month` is then ignored for). `names` is what
    the accounts are called (`account_names`), for the other leg of a transfer; nothing here
    decides a name.

    `opening_reader` stands in for `effective_opening` where the caller holds readings of the
    account's balances between requests (the page's data hook does: `cli.ledger_data`). It is
    called with exactly the arguments `effective_opening` would be.

    `bound` is whether the account has an Actual destination; it is passed in
    because the bindings live in a file the store does not read. `archive` is
    carried onto the result untouched. `families` is which accounts are Spaces
    of which and which sources are blind to them; without it every anchor is
    the account's own (see FAMILY_QUERIES for what asking costs).

    `movement` is the whole store's movement report, which the caller holds because reading
    it walks every artefact; without it the standing is from the known balances alone and
    says so (`Agreement.movement_checked`). `explain_after` is the end of a protected span,
    whose faults are not explained again (`effective_opening`).

    `with_protection` reads the account's protection (`protection`): the view of it is set on
    the result, and an INTACT protection supplies `explain_after`. A broken one does not, since
    the fault explanations are what a person reads to find out why it broke.
    """
    check = None
    record = store.protection_record(ref) if with_protection else None
    if record is not None:
        check = check_span(store, record)
        if check.intact and explain_after is None:
            explain_after = date.fromisoformat(str(record["through"]))
    built = _ledger_for(
        store,
        ref,
        month,
        bound=bound,
        label=label,
        families=families,
        movement=movement,
        explain_after=explain_after,
        with_protection=with_protection,
        check=check,
        opening_reader=opening_reader or effective_opening,
        window=window,
        names=names,
    )
    return replace(built, archive=archive, removed_balances=_removed_balances(store, ref, built))


def _removed_balances(store: Store, ref: str, built: Ledger) -> tuple[RemovedBalance, ...]:
    """The balances removed from the account, for the values view to offer back."""
    if built.state == "unknown":
        return ()
    return tuple(
        RemovedBalance(
            day=removed.day.isoformat(),
            source=removed.source,
            removed_at=removed.removed_at[:16].replace("T", " "),
            balance_direction=direction_of(removed.balance_minor),
            balance=Money(removed.balance_minor, CURRENCY),
            restate_as=f"{'-' if removed.balance_minor < 0 else ''}"
            f"{abs(removed.balance_minor) // 100}.{abs(removed.balance_minor) % 100:02d}",
        )
        for removed in removed_stated_anchors(store, ref)
    )


def _ledger_for(
    store: Store,
    ref: str,
    month: str | None,
    *,
    bound: bool,
    label: str,
    families: Families | None,
    movement: MovementCompleteness | None,
    explain_after: date | None,
    with_protection: bool,
    check: Check | None,
    opening_reader: OpeningReader,
    window: LedgerWindow | None,
    names: AccountsShown | None,
) -> Ledger:
    held = store.transactions_for_account(ref)
    members = [ref, *(families.spaces_of(ref) if families is not None else ())]
    if not held:
        declared = store.declared_account(AccountRef(ref)) is not None
        empty = _empty(ref, label, "no-rows" if declared else "unknown", bound)
        if not declared:
            return empty
        # An account declared before any money moved can still have had its
        # balance stated, and that is exactly the figure a new account needs.
        opening = effective_opening(
            store, ref, [], families=families, explain_after=explain_after
        )
        entries = typed_entries(store, ref)
        if not opening.unitemised:
            standing = standing_of(
                opening, members, movement, statement_checks_for(store, ref, families)
            )
            return replace(
                empty,
                opening=opening_view(opening),
                typed=typed_lines(entries, ""),
                standing=standing,
                protection=(
                    protection_view(store, ref, opening, [], standing, check=check)
                    if with_protection
                    else None
                ),
            )
        # A balance-only account holds no rows of its own, and what its stated
        # balances imply is its ledger.
    else:
        opening = opening_reader(
            store, ref, held, families=families, explain_after=explain_after
        )
        entries = typed_entries(store, ref)
    rows = [*held, *opening.unitemised]

    other_side = _confirmed_other_sides(store, ref)
    rows = [replace(t, transfer_confirmed=t.entity_id in other_side) for t in rows]

    sightings, bases = _sightings(store, ref)
    # A derived row has no source in the sense this list means: nothing fed it.
    account_sources = sorted(
        (
            {source for seen in sightings.values() for source in seen}
            | {t.source for t in rows if t.entity_id not in sightings}
        )
        - {UNITEMISED_SOURCE, CASH_LEG_SOURCE}
    )
    feed = FeedStatuses(store, [ref]) if BANK_SOURCE in account_sources else None
    # Typed rows are listed as a source but do not make an account "fed by
    # more than one": a typed row is not a feed another feed failed to match.
    feeds = [source for source in account_sources if source != MANUAL_SOURCE]
    identity_groups = {
        (key, occurrence) for _, key, occurrence, _ in shared_identity_groups(store, ref)
    }
    folded = _absorbed_ids(store, ref)
    open_reviews = {
        str(flag["entity_id"]): str(flag["reason"]) for flag in store.review_queue()
    }
    categories = store.annotations("category")
    payees = store.annotations("payee")

    per_month = Counter(_month_of(t.value_date) for t in rows)
    months_held = sorted(per_month)
    span = window
    fell_back = False
    widened = 0
    if span is not None and span.at_least:
        dated = sorted((t.value_date for t in rows if t.value_date <= span.last), reverse=True)
        if dated:
            taken = min(span.at_least, len(dated))
            if dated[taken - 1] < span.first:
                span, widened = replace(span, first=dated[taken - 1]), taken
    if span is not None and span.fall_back and not any(
        span.first <= t.value_date <= span.last for t in rows
    ):
        span, fell_back = None, True
    shown, previous_month, next_month = "", "", ""
    if span is not None:
        first, last = span.first, span.last
    else:
        if month is None or not month.strip():
            shown = months_held[-1]
        else:
            year, number = parse_month(month)
            shown = _label(year, number)
        year, number = parse_month(shown)
        first, last = date(year, number, 1), _month_end(year, number)
        # A step is offered only towards months that could hold something.
        # Offering "next" from the newest month led to a page saying the month
        # was empty, which reads as a gap and is only the future.
        previous_month = _neighbour(year, number, -1) if shown > months_held[0] else ""
        next_month = _neighbour(year, number, 1) if shown < months_held[-1] else ""

    # Only the rows on show are listed with what each sighting stated; every other row is
    # counted by the basis its sightings joined on, which is all `joins` reads of one.
    details = store.sighting_details_of(
        ref, [t.entity_id for t in rows if first <= t.value_date <= last]
    )

    built: list[tuple[Transaction, LedgerRow]] = []
    for t in rows:
        seen = sightings.get(t.entity_id) or {t.source: ""}
        withheld = withheld_reason(t, bound=bound) or ""
        unsendable, refusal = False, ""
        if not withheld:
            try:
                to_actual_transaction(t)
            except ReplayError as refused:
                unsendable, refusal = True, str(refused)
        category = categories.get(t.entity_id)
        payee = payees.get(t.entity_id)
        by = {
            held[1].split(":", 1)[0] for held in (category, payee) if held is not None
        }
        observed_dates = {day for day in seen.values() if day}
        clearing_sources = cleared_by(seen, str(t.status))
        origin = (
            ORIGIN_UNITEMISED
            if t.source == UNITEMISED_SOURCE
            else ORIGIN_TYPED
            if MANUAL_SOURCE in seen
            else ""
        )
        built.append(
            (
                t,
                LedgerRow(
                    origin=origin,
                    dated=t.value_date,
                    observed=tuple(
                        (source, day) for source, day in sorted(seen.items()) if day
                    ),
                    direction=direction_of(t.amount_minor),
                    currency=t.currency,
                    status=str(t.status),
                    sources=tuple(sorted(seen)),
                    dates_differ=len(seen) > 1 and len(observed_dates) > 1,
                    # What a person typed, or the account's own arithmetic, is not
                    # a feed that failed to report it.
                    one_source=(
                        len(feeds) > 1 and len(seen) == 1 and origin == ""
                    ),
                    transfer=(
                        "confirmed"
                        if t.transfer_confirmed
                        else "claimed"
                        if t.is_internal_transfer
                        else ""
                    ),
                    transfer_other_account=(
                        other_side[t.entity_id].account if t.entity_id in other_side else ""
                    ),
                    transfer_other_anchor=(
                        other_side[t.entity_id].anchor if t.entity_id in other_side else ""
                    ),
                    transfer_other_month=(
                        other_side[t.entity_id].month if t.entity_id in other_side else ""
                    ),
                    transfer_other_label=(
                        names.of(other_side[t.entity_id].account).label
                        if names is not None and t.entity_id in other_side
                        else ""
                    ),
                    booked=t.booking_date,
                    review_open=t.entity_id in open_reviews,
                    withheld=withheld,
                    unsendable=unsendable,
                    shares_identity=(
                        bool(t.content_key)
                        and (t.content_key, t.occurrence) in identity_groups
                    ),
                    absorbed_ids=folded.get(t.entity_id, 0),
                    has_counterparty=bool(t.counterparty),
                    annotated_by=",".join(sorted(by)),
                    description=t.description,
                    counterparty=t.counterparty,
                    amount=Money(t.amount_minor, t.currency),
                    review_reason=open_reviews.get(t.entity_id, ""),
                    send_refusal=refusal,
                    category=category[0] if category else "",
                    payee=payee[0] if payee else "",
                    cleared_by=clearing_sources,
                    feed_at=feed.time_of(t.entity_id) if feed is not None else None,
                    sightings=sighting_views(details.get(t.entity_id, ())),
                    anchor=row_anchor(t.entity_id),
                ),
            )
        )

    # THE ORDER OF A DAY'S ROWS, newest first: the rows the bank's feed gave a time come
    # before those it gave none, by that time, so a page lines up against the bank's app;
    # the rest keep the order they always had (booking date, then id). A row with no time
    # is never compared with one that has, which keeps the order a total one.
    in_month = sorted(
        (pair for pair in built if first <= pair[0].value_date <= last),
        key=lambda pair: (
            pair[0].value_date,
            pair[0].booking_date,
            pair[1].feed_at is not None,
            pair[1].feed_at or _NO_TIME,
            pair[0].entity_id,
        ),
        reverse=True,
    )
    head = opening.readings[0].anchor if opening.readings and not opening.withheld else None
    if head is not None:
        after = balances_after([t for t, _ in in_month], rows, head)
        in_month = [
            (t, replace(row, balance_after=_signed_balance(figure, t.currency)))
            for (t, row), figure in zip(in_month, after, strict=True)
        ]

    def totals(pairs: list[tuple[Transaction, LedgerRow]]) -> tuple[int, int, int]:
        """(store sum, sent sum, rows counted) over the pairs that are money."""
        money = [(t, row) for t, row in pairs if not t.status.is_history]
        store_sum = sum(t.amount_minor for t, _ in money)
        sent_sum = sum(
            t.amount_minor for t, row in money if not row.withheld and not row.unsendable
        )
        return store_sum, sent_sum, len(money)

    currency = rows[0].currency
    through = [pair for pair in built if pair[0].value_date <= last]
    _, pos_sent, counted = totals(through)
    # The opening is the balance BEFORE the first row, so it belongs in every
    # running position. It reaches Actual only on a bound account, as the
    # payload's one extra row, so the sent figure takes it on the same terms.
    opening_minor = opening.opening_minor
    pos_store = running_balance(opening_minor or 0, rows, last)
    if opening_minor is not None and bound:
        pos_sent += opening_minor
    position = Position(
        through=last.isoformat(),
        rows_counted=counted,
        opening_included=opening_minor is not None,
        store_direction=direction_of(pos_store),
        sent_direction=direction_of(pos_sent),
        differs=pos_store != pos_sent,
        store_balance=Money(pos_store, currency),
        sent_balance=Money(pos_sent, currency),
    )

    state = "ok" if in_month else "empty-month"
    summary = None
    if in_month:
        month_rows = [row for _, row in in_month]
        month_store, month_sent, _ = totals(in_month)
        per_source: dict[str, int] = {}
        for row in month_rows:
            for source in row.sources:
                per_source[source] = per_source.get(source, 0) + 1
        reasons: dict[str, int] = {}
        for row in month_rows:
            if row.withheld:
                reasons[row.withheld] = reasons.get(row.withheld, 0) + 1
        summary = MonthSummary(
            rows=len(month_rows),
            per_source=tuple(sorted(per_source.items())),
            multi_source=sum(1 for row in month_rows if len(row.sources) > 1),
            one_source=sum(1 for row in month_rows if row.one_source),
            pending=sum(1 for row in month_rows if row.status == "pending"),
            void=sum(1 for row in month_rows if row.status == "void"),
            folded=sum(1 for row in month_rows if row.status == "folded"),
            transfers_confirmed=sum(1 for r in month_rows if r.transfer == "confirmed"),
            transfers_claimed=sum(1 for r in month_rows if r.transfer == "claimed"),
            review_open=sum(1 for row in month_rows if row.review_open),
            would_send=sum(
                1 for row in month_rows if not row.withheld and not row.unsendable
            ),
            withheld=sum(1 for row in month_rows if row.withheld),
            withheld_by_reason=tuple(sorted(reasons.items())),
            unsendable=sum(1 for row in month_rows if row.unsendable),
            store_direction=direction_of(month_store),
            sent_direction=direction_of(month_sent),
            sums_differ=month_store != month_sent,
            mixed_currency=len({row.currency for row in month_rows}) > 1,
            store_sum=Money(month_store, currency),
            sent_sum=Money(month_sent, currency),
            cleared=sum(1 for row in month_rows if row.cleared_by),
            uncleared=sum(
                1
                for (t, row) in in_month
                if not t.status.is_history
                and row.origin != ORIGIN_UNITEMISED
                and not row.cleared_by
            ),
        )

    checks = statement_checks_for(store, ref, families)
    final_standing = standing_of(opening, members, movement, checks)
    return Ledger(
        ref=ref,
        label=label,
        state=state,
        sources=tuple(account_sources),
        actual_bound=bound,
        month=shown,
        previous_month=previous_month,
        next_month=next_month,
        window_first=span.first.isoformat() if span is not None else "",
        window_last=span.last.isoformat() if span is not None else "",
        window_fell_back=fell_back,
        window_widened=widened,
        oldest_month=months_held[0],
        newest_month=months_held[-1],
        month_counts=tuple((month, per_month[month]) for month in months_held),
        summary=summary,
        position=position,
        rows=tuple(row for _, row in in_month),
        opening=opening_view(opening),
        typed=typed_lines(entries, shown, (first, last) if span is not None else None),
        unitemised=unitemised_lines(opening),
        clearing=clearing_counts(
            (_month_of(t.value_date), bool(row.cleared_by))
            for t, row in built
            if not t.status.is_history and row.origin != ORIGIN_UNITEMISED
        ),
        standing=final_standing,
        statements=checks,
        protection=(
            protection_view(store, ref, opening, held, final_standing, check=check)
            if with_protection
            else None
        ),
        running_shown=head is not None,
        joins=join_counts_of_bases(
            (t.value_date, bases.get(t.entity_id, ()))
            for t, row in built
            if not t.status.is_history and row.origin == ""
        ),
    )


def _signed_balance(minor: int | None, currency: str) -> str:
    """A balance as printed beside a row: its sign kept, since a balance may be below nil."""
    if minor is None:
        return ""
    return f"{'-' if minor < 0 else ''}{Money(minor, currency)}"


def typed_lines(
    entries: Iterable[TypedEntry], month: str, window: tuple[date, date] | None = None
) -> TypedLines:
    """The typed entries of one month as the page lists them, and the rest as counts.

    Only the month on show is listed, so a mortgage typed into for years does
    not make the page grow with it, and a withdrawal is made from the month the
    entry is dated in.
    """
    shown: list[TypedLine] = []
    elsewhere = 0
    withdrawn = 0
    for entry in sorted(entries, key=lambda e: (e.day, e.entry_id), reverse=True):
        if entry.withdrawn:
            withdrawn += 1
        if (
            window[0] <= entry.day <= window[1]
            if window is not None
            else month and _month_of(entry.day) == month
        ):
            shown.append(
                TypedLine(
                    entry_id=entry.entry_id,
                    day=entry.day.isoformat(),
                    direction=direction_of(entry.amount_minor),
                    withdrawn=entry.withdrawn,
                    amount=Money(entry.amount_minor, CURRENCY),
                    description=entry.description,
                )
            )
        elif not entry.withdrawn:
            elsewhere += 1
    return TypedLines(
        lines=tuple(shown), live_elsewhere=elsewhere, withdrawn_total=withdrawn
    )


def unitemised_lines(opening: EffectiveOpening) -> tuple[UnitemisedLine, ...]:
    """Each derived change with the stated balance it follows, oldest first."""
    stated = sorted(r.anchor.day for r in opening.readings if r.anchor.basis == STATED)
    lines = []
    for row in sorted(opening.unitemised, key=lambda t: t.value_date):
        earlier = max((day for day in stated if day < row.value_date), default=None)
        lines.append(
            UnitemisedLine(
                day=row.value_date.isoformat(),
                since=earlier.isoformat() if earlier else "",
                direction=direction_of(row.amount_minor),
                amount=Money(row.amount_minor, CURRENCY),
            )
        )
    return tuple(lines)


@dataclass(frozen=True)
class OtherLeg:
    """The far side of a confirmed transfer, as far as a link to it needs."""

    account: str
    #: The other leg's row key (`row_anchor`) and the month it is dated in, which is where it is
    #: listed and may differ from the month of this leg.
    anchor: str
    month: str


def _confirmed_other_sides(store: Store, ref: str) -> dict[str, OtherLeg]:
    """entity id -> the OTHER leg of its confirmed pair, for this account's legs."""
    found: dict[str, OtherLeg] = {}
    for row in store.connection.execute(
        "SELECT p.debit_entity_id AS debit, p.credit_entity_id AS credit, "
        "       d.account_id AS debit_account, c.account_id AS credit_account, "
        "       d.value_date AS debit_day, c.value_date AS credit_day "
        "FROM transfer_pairs p "
        "JOIN transactions d ON d.entity_id = p.debit_entity_id "
        "JOIN transactions c ON c.entity_id = p.credit_entity_id "
        "WHERE d.account_id = ? OR c.account_id = ?",
        (ref, ref),
    ):
        if row["debit_account"] == ref:
            found[str(row["debit"])] = OtherLeg(
                str(row["credit_account"]),
                row_anchor(str(row["credit"])),
                str(row["credit_day"])[:7],
            )
        if row["credit_account"] == ref:
            found[str(row["credit"])] = OtherLeg(
                str(row["debit_account"]),
                row_anchor(str(row["debit"])),
                str(row["debit_day"])[:7],
            )
    return found


def _sightings(
    store: Store, ref: str
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    """(entity id -> source -> the earliest date that source gave it, or ''; entity id -> the
    basis each of its sightings joined on), from one read of the account's sightings."""
    seen: dict[str, dict[str, str]] = {}
    bases: dict[str, list[str]] = {}
    for row in store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.source AS source, "
        "       s.observed_date AS observed_date, s.basis AS basis "
        "FROM transaction_sources s "
        "JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ?",
        (ref,),
    ):
        by_source = seen.setdefault(str(row["entity_id"]), {})
        source, day = str(row["source"]), str(row["observed_date"] or "")
        by_source[source] = min(by_source[source], day) if source in by_source else day
        bases.setdefault(str(row["entity_id"]), []).append(str(row["basis"]))
    return seen, bases


def _absorbed_ids(store: Store, ref: str) -> dict[str, int]:
    """entity id -> the most distinct provider ids one source holds against it, if over one."""
    folded: dict[str, int] = {}
    for (_account, _source), by_entity in provider_ids_by_row(store, ref).items():
        for entity_id, ids in by_entity.items():
            if len(ids) > 1:
                folded[entity_id] = max(folded.get(entity_id, 0), len(ids))
    return folded
