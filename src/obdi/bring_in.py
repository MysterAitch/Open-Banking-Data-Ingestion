"""What Bring in says, as data: the files wanted by account, the heading that counts them, the kind
of file an upload is, and what an upload settled.

NO PAGE, NO STORE, NO FIGURE. Dates, counts, names, and words in, the same out, so the page that
draws it says the same thing masked or not (`web_bring_in`).

WANTED FILES ARE THE GAPS THAT NAME A FILE. `fetch_gaps` already decided what is wanted and
`fetch_marks` what the owner set aside; `todo.gap_todos` already splits a gap into the files it
stands for and dates each. This reads those, leaves out the gaps a person answers by stating a
balance (an account page's to-do, not a file), and keeps what a set-aside needs: the source the
mark is made against, which a statement never carries.

AN UPLOAD IS ONE OF TWO KINDS because the two have different doors behind them: a statement is a
PDF, kept as evidence before anyone says whose it is; an export is a CSV or QIF, previewed
against the account it is for before anything is stored. Which it is comes from the file alone,
and which account it is for comes from the file in neither case: nothing a bank prints in either
names an account the store knows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .fetch_gaps import Basis, FetchReport, GapKind
from .plural import plural
from .standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    verification_of,
)
from .statement_span import STATEMENT_SOURCES
from .todo import gap_todos, lockable

#: The gaps a person answers by stating a balance, which is an account page's to-do and not a
#: file to bring in.
BALANCE_KINDS = frozenset({GapKind.NO_BALANCE, GapKind.AUTOMATIC_ONLY, GapKind.ONE_BALANCE})
_EXPORT_KINDS = frozenset({GapKind.EXPORT_STOPS, GapKind.EXPORT_MONTHS})

#: A few words for why each kind of file is wanted. The full reasoning is on the account's page
#: and in `fetch_gaps`; a row here says it in the space beside a date.
_WHY = {
    GapKind.NEWER_STATEMENT: "out",
    GapKind.HOLE_BETWEEN: "no statement lists it",
    GapKind.NOTHING_BEFORE: "nothing earlier tests these days",
    GapKind.EXPORT_STOPS: "the export stops short",
    GapKind.EXPORT_MONTHS: "the export holds none of these months",
    GapKind.FLAG_SETTLE: "settles a flagged transaction",
}


@dataclass(frozen=True)
class WantedFile:
    """One file wanted, for one account, covering the days `first` to `last`."""

    account: str
    kind: GapKind
    first: date
    last: date
    #: The day it has been waiting since, for its age; None where the source does not say.
    since: date | None
    #: The source a set-aside is made against: an export's own, "" for any statement.
    source: str
    #: Where the days are an inference and not a fact held: drawn dashed.
    guess: bool

    @property
    def export(self) -> bool:
        return self.kind in _EXPORT_KINDS

    @property
    def words(self) -> str:
        """Why it is wanted, in a few words and without a date."""
        if self.kind is GapKind.HOLE_BETWEEN and self.guess:
            return "probably missing"
        return _WHY[self.kind]


def files_wanted(report: FetchReport | None) -> tuple[WantedFile, ...]:
    """Every file wanted, most urgent account first, one per file.

    A Space is never listed: it has no statement or export of its own, and is fetched with its
    parent (`fetch_gaps` gives it no gap, and this leaves out any that did).
    """
    if report is None:
        return ()
    found: list[WantedFile] = []
    for outlook in report.accounts:
        if outlook.space_of:
            continue
        for gap in outlook.gaps:
            if gap.kind in BALANCE_KINDS:
                continue
            source = "" if gap.source in STATEMENT_SOURCES else gap.source
            for todo in gap_todos(gap):
                if todo.days is None:
                    continue
                found.append(
                    WantedFile(
                        gap.account,
                        gap.kind,
                        todo.days[0],
                        todo.days[1],
                        todo.since,
                        source,
                        gap.basis is Basis.INFERRED,
                    )
                )
    return tuple(found)


def by_account(files: Iterable[WantedFile]) -> dict[str, tuple[WantedFile, ...]]:
    """The files grouped by account, the accounts in the order they first appear."""
    grouped: dict[str, list[WantedFile]] = {}
    for item in files:
        grouped.setdefault(item.account, []).append(item)
    return {ref: tuple(items) for ref, items in grouped.items()}


def wanted_heading(files: Iterable[WantedFile], *, still: bool = False) -> str:
    """The page's count: "Wanted: 5 statements and 1 export for 4 accounts", or "Still wanted:
    ..." once an upload has settled some."""
    listed = tuple(files)
    exports = sum(1 for item in listed if item.export)
    statements = len(listed) - exports
    parts = []
    if statements:
        parts.append(plural(statements, "statement"))
    if exports:
        parts.append(plural(exports, "export"))
    accounts = len({item.account for item in listed})
    lead = "Still wanted" if still else "Wanted"
    return f"{lead}: {' and '.join(parts)} for {plural(accounts, 'account')}"


class UploadKind(StrEnum):
    STATEMENT = "statement"
    EXPORT = "export"


def upload_kind(filename: str, payload: bytes) -> UploadKind:
    """Which door a file goes through: a PDF is a statement, anything else is an export.

    The name decides first and the file's own first bytes second, so a statement saved without
    its extension is still read as one.
    """
    if filename.lower().endswith(".pdf") or payload.lstrip()[:5] == b"%PDF-":
        return UploadKind.STATEMENT
    return UploadKind.EXPORT


def _through(item: AccountStanding | None) -> date | None:
    return None if item is None else item.standing.own.through


def settled_sentence(
    name: str, before: AccountStanding | None, after: AccountStanding | None
) -> str:
    """What a file settled about an account, in the trust sentence's own terms.

    "Everyday card now adds up to the known balances to 2026-09-10; it was 2026-07-10." Said only
    where the standing could be read after, and quietly where nothing moved. A file that makes an
    account stop adding up says that, in the same words the account's page uses.
    """
    if after is None:
        return ""
    verdict = verification_of(after)
    if verdict == DOES_NOT_ADD_UP:
        held = after.standing.own.held
        where = f" from {held.day.isoformat()}" if held is not None else ""
        return f"{name} {DOES_NOT_ADD_UP}{where}."
    through = _through(after)
    if through is None:
        return f"{name} has {NOTHING_TO_CHECK_AGAINST}."
    was = _through(before)
    reached = f"{ADDS_UP} to the known balances to {through.isoformat()}"
    if was == through:
        return f"{name} {reached}, as before."
    if was is None:
        return f"{name} now {reached}; it had {NOTHING_TO_CHECK_AGAINST}."
    return f"{name} now {reached}; it was {was.isoformat()}."


def newly_lockable(
    before: Mapping[str, AccountStanding], after: Mapping[str, AccountStanding]
) -> tuple[str, ...]:
    """The accounts that have days to lock in after an upload and had none before."""
    return tuple(
        sorted(
            ref for ref, item in after.items() if lockable(item) and not lockable(before.get(ref))
        )
    )
