"""Whether every payment still has a row, and every row an identity of its own.

Two faults in the merged layer are invisible from inside it. Two rows sharing
one identity look like two ordinary rows. Two payments folded into one row
look like one ordinary row. Neither raises a review flag, because both are
recorded as the matcher doing its job.

Both leave evidence elsewhere, and this module counts it. Identities are
compared directly. Folding is read from the sightings: every provider id a
row was ever sighted under is kept, so a row sighted under two settled ids
from one source has absorbed a payment, and comparing the ids a source
reported with the rows holding them gives the number that have no row of
their own.

It reports COUNTS AND ACCOUNT NAMES ONLY, deliberately. The question "is
anything missing" can then be asked by somebody who may know that a fault
exists without seeing the money it concerns, and the answer can be pasted
into a note or a ticket as it stands.

This module only reports. Whether a count is a fault in the matcher, and
which rule should change, is decided from the number - the same order of
operations as review_report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .plural import agree, plural
from .store import FOLDED_SIGHTING_PREFIX, Store

#: Artefact sources whose records name a payment by an id it will NOT keep.
#: A pending snapshot's id is replaced when the payment settles, so one
#: payment legitimately carries two ids, and counting the pending one would
#: report every settled payment as two.
#: Sources whose ids survive settlement need no entry: there a second id on
#: one row is always a second payment.
PENDING_SNAPSHOT_SOURCES = ("truelayer-pending",)


@dataclass(frozen=True)
class SharedIdentity:
    """Rows in one account that cannot be told apart downstream."""

    account_id: str
    #: Distinct (content key, occurrence) pairs held by more than one row.
    identities: int
    #: The rows holding them.
    rows: int


@dataclass(frozen=True)
class ProviderIdTally:
    """One source's own count of an account's payments, against the rows."""

    account_id: str
    source: str
    #: Distinct provider ids the source reported outside a pending snapshot.
    reported: int
    #: Distinct rows those ids are recorded against.
    held: int
    #: Rows recorded against more than one such id.
    #: History rather than loss: a row keeps the ids it once absorbed even
    #: after the absorbed payment has regained a row of its own.
    absorbing_rows: int
    #: Payments the source reported that have no row of their own.
    folded: int = 0
    #: Rows beyond the payments the source reported: a payment held twice.
    surplus: int = 0
    #: Of `folded`, how many are proven to be separate payments: the provider
    #: named the id beside the row's other id in ONE response.
    #: The rest were never named together, which is what a provider giving one
    #: payment a new id looks like, and is not proof either way.
    folded_listed_together: int = 0


@dataclass
class IdentityHealth:
    shared: list[SharedIdentity] = field(default_factory=list)
    tallies: list[ProviderIdTally] = field(default_factory=list)

    @property
    def folded(self) -> int:
        return sum(tally.folded for tally in self.tallies)

    @property
    def surplus(self) -> int:
        return sum(tally.surplus for tally in self.tallies)

    @property
    def folded_listed_together(self) -> int:
        return sum(tally.folded_listed_together for tally in self.tallies)

    def describe(self) -> str:
        lines = ["Rows sharing an identity (content key + occurrence):"]
        if not self.shared:
            lines.append("  no rows share an identity")
        for shared in self.shared:
            lines.append(
                f"  {shared.account_id}: {plural(shared.identities, 'identity', 'identities')} "
                f"shared by {plural(shared.rows, 'row')}"
            )

        lines.append("")
        lines.append("Provider ids reported, against the rows that hold them:")
        if not self.tallies:
            lines.append(
                "  no provider ids are held, so there is nothing to compare - "
                "this is not a pass"
            )
            return "\n".join(lines)
        for tally in self.tallies:
            line = (
                f"  {tally.account_id} via {tally.source}: "
                f"{tally.reported} reported, {tally.held} held"
            )
            if tally.folded:
                apart = tally.folded - tally.folded_listed_together
                line += (
                    f" - {tally.folded} with no row of their own: "
                    f"{tally.folded_listed_together} listed in one response beside "
                    "the id that holds the row, so a separate payment; "
                    f"{apart} never listed beside it, as when a provider "
                    "renumbers one payment"
                )
            if tally.surplus:
                line += f" - {plural(tally.surplus, 'more row')} than ids"
            if tally.absorbing_rows:
                line += (
                    f" ({plural(tally.absorbing_rows, 'row')} "
                    f"{agree(tally.absorbing_rows, 'has')} been sighted under "
                    "more than one id)"
                )
            lines.append(line)
        if self.folded:
            proven = self.folded_listed_together
            lines.append(
                f"  TOTAL: {plural(self.folded, 'provider id')} "
                f"{agree(self.folded, 'has')} no row of "
                f"{agree(self.folded, 'its')} own. "
                f"{proven} {agree(proven, 'is')} proven separate payments folded into "
                f"another row; {self.folded - proven} "
                f"{agree(self.folded - proven, 'was')} never listed beside the id that holds "
                "the row, which is what one payment given a new id looks like"
            )
        else:
            lines.append("  every provider id reported has a row of its own")
        if self.surplus:
            lines.append(
                f"  TOTAL: {plural(self.surplus, 'payment')} {agree(self.surplus, 'is')} "
                "held by more than one "
                "row - the same provider id is the only id of two rows, so the "
                "payment is counted twice"
            )
        return "\n".join(lines)


def _groups(rows: dict[str, set[str]]) -> list[tuple[list[str], set[str]]]:
    """Rows and provider ids as the sightings connect them: (entities, ids).

    Counted within each group, never across the whole account: a payment
    folded away in March and a payment held twice in June are two faults, and
    totals alone would let one hide the other.
    """
    group_of: dict[str, str] = {}

    def find(node: str) -> str:
        while group_of.setdefault(node, node) != node:
            group_of[node] = group_of[group_of[node]]
            node = group_of[node]
        return node

    for entity, ids in rows.items():
        for provider_id in ids:
            group_of[find(f"row:{entity}")] = find(f"id:{provider_id}")

    members: dict[str, tuple[list[str], set[str]]] = {}
    for entity, ids in rows.items():
        entities, held_ids = members.setdefault(find(f"row:{entity}"), ([], set()))
        entities.append(entity)
        held_ids.update(ids)
    return list(members.values())


def _folded_and_surplus(rows: dict[str, set[str]]) -> tuple[int, int]:
    """How many payments lack a row, and how many rows are one too many.

    Within a group, more ids than rows is payments without a row of their
    own, and more rows than ids is a payment held more than once.
    """
    groups = _groups(rows)
    folded = sum(max(len(ids) - len(entities), 0) for entities, ids in groups)
    surplus = sum(max(len(entities) - len(ids), 0) for entities, ids in groups)
    return folded, surplus


def _listed_together(store: Store, ids: set[str]) -> bool:
    """Whether one landed response names two of these provider ids.

    Read from the raw payloads, because the sightings cannot say: a row keeps
    one sighting per response, so two ids of one row arriving together leave
    only one of them on record.
    A pending snapshot is set aside, as it is when the ids are counted.
    """
    placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
    ordered = sorted(ids)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            found = store.connection.execute(
                "SELECT 1 FROM raw_artefacts "  # noqa: S608
                "WHERE instr(payload, CAST(? AS BLOB)) > 0 "
                "AND instr(payload, CAST(? AS BLOB)) > 0 "
                # Placeholders only - the interpolation builds "?,?", never data.
                f"AND source NOT IN ({placeholders}) LIMIT 1",
                (first, second, *PENDING_SNAPSHOT_SOURCES),
            ).fetchone()
            if found is not None:
                return True
    return False


def _folded_listed_together(store: Store, rows: dict[str, set[str]]) -> int:
    """Of the payments with no row of their own, how many are proven separate.

    Only groups that hold a fold are read, so a healthy store costs nothing.
    """
    proven = 0
    for entities, ids in _groups(rows):
        folded = len(ids) - len(entities)
        if folded <= 0:
            continue
        together = sum(
            1
            for entity in entities
            if len(rows[entity]) > 1 and _listed_together(store, rows[entity])
        )
        proven += min(folded, together)
    return proven


def shared_identity_groups(
    store: Store, account_id: str | None = None
) -> list[tuple[str, str, int, int]]:
    """(account, content key, occurrence, rows) for every identity held twice.

    The one statement of what "sharing an identity" means, read by the report
    below and by anything that must say which rows are affected. `account_id`
    narrows the question to one account; None asks about all of them.
    """
    rows = store.connection.execute(
        "SELECT account_id, content_key, occurrence, COUNT(*) AS n "
        "FROM transactions "
        "WHERE content_key IS NOT NULL AND content_key != '' "
        "AND (? IS NULL OR account_id = ?) "
        "GROUP BY account_id, content_key, occurrence HAVING COUNT(*) > 1 "
        "ORDER BY account_id, content_key, occurrence",
        (account_id, account_id),
    ).fetchall()
    return [
        (
            str(row["account_id"]),
            str(row["content_key"]),
            int(row["occurrence"]),
            int(row["n"]),
        )
        for row in rows
    ]


def provider_ids_by_row(
    store: Store, account_id: str | None = None
) -> dict[tuple[str, str], dict[str, set[str]]]:
    """(account, source) -> entity id -> the provider ids that row holds.

    One entry per (transaction, source, provider id), pending snapshots set
    aside. Joined to transactions so a sighting whose row is gone counts on
    neither side: it would otherwise read as a payment reported and lost,
    which is the orphan check's finding and not this one's. A row holding
    more than one id from one source has absorbed a payment.
    """
    placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
    sighted = store.connection.execute(
        "SELECT DISTINCT t.account_id AS account_id, s.source AS source, "  # noqa: S608
        "       s.entity_id AS entity_id, s.source_id AS source_id "
        "FROM transaction_sources s "
        "JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE s.source_id IS NOT NULL AND s.source_id != '' "
        # A sighting copied onto a Space row names no provider id of its own.
        "AND s.source_id NOT LIKE ? "
        "AND (? IS NULL OR t.account_id = ?) "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM raw_artefacts a "
        "  WHERE a.digest = s.artefact_digest "
        # Placeholders only - the interpolation builds "?,?", never data.
        f"  AND a.source IN ({placeholders})"
        ")",
        (FOLDED_SIGHTING_PREFIX + "%", account_id, account_id, *PENDING_SNAPSHOT_SOURCES),
    ).fetchall()

    ids_by_row: dict[tuple[str, str], dict[str, set[str]]] = {}
    for row in sighted:
        feed = (str(row["account_id"]), str(row["source"]))
        ids_by_row.setdefault(feed, {}).setdefault(str(row["entity_id"]), set()).add(
            str(row["source_id"])
        )
    return ids_by_row


def identity_health(store: Store) -> IdentityHealth:
    report = IdentityHealth()

    by_account: dict[str, list[int]] = {}
    for account_id, _key, _occurrence, held_rows in shared_identity_groups(store):
        by_account.setdefault(account_id, []).append(held_rows)
    report.shared = [
        SharedIdentity(account_id=account_id, identities=len(held), rows=sum(held))
        for account_id, held in sorted(by_account.items())
    ]

    for (account_id, source), rows in sorted(provider_ids_by_row(store).items()):
        folded, surplus = _folded_and_surplus(rows)
        report.tallies.append(
            ProviderIdTally(
                account_id=account_id,
                source=source,
                reported=len(set().union(*rows.values())),
                held=len(rows),
                absorbing_rows=sum(1 for ids in rows.values() if len(ids) > 1),
                folded=folded,
                surplus=surplus,
                folded_listed_together=(
                    _folded_listed_together(store, rows) if folded else 0
                ),
            )
        )
    return report
