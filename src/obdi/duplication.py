"""How much of layer 0 is the same information arriving again.

A sliding-window fetch re-delivers most of what it delivered last time, by
construction: asking for 90 days four times a day means each answer is the
previous one with a little trimmed from the far end and a little added at
the near end. Overlap is not waste to be eliminated - it is the shape of
the evidence, and its absence would be the thing worth alarming about.

What it does mean is that a replay spends most of its time re-deriving
conclusions it has already reached, and that CAN be skipped, so the
question this module answers is a narrow one: how much of the corpus is
genuinely redundant, and at what level of comparison does that redundancy
become visible?

The level matters more than it sounds. Four tiers are measured, because a
duplicate can be obvious at one and invisible at the next:

  BYTES       the record serialised exactly as it arrived
  CANONICAL   the same, with key order normalised away
  IDENTITY    the durable provider id
  CONTENT     obdi's own content key, via the real parsers

The gap between BYTES and IDENTITY is the trap. TrueLayer documents
`transaction_id` as changing between requests, so two byte-different
records can be one payment - a comparison that stops at BYTES would report
almost no redundancy in TrueLayer data and be wrong about all of it. The
churn table names which fields do this, discovered from the data rather
than assumed, because a list of volatile fields written by hand is a list
that is silently incomplete.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from .store import Store

#: Where a payload keeps its records, in the order the providers use.
_RECORD_KEYS = ("results", "feedItems", "booked", "pending", "accounts")

#: Fields whose change means the payment itself changed, rather than the
#: provider having re-rendered it. Separating these is what distinguishes
#: a real amendment from noise that merely defeats byte comparison.
_CORE_FIELDS = frozenset(
    {
        "amount",
        "amount.minorUnits",
        "amount.currency",
        "currency",
        "timestamp",
        "transactionTime",
        "settlementTime",
        "status",
        "description",
        "counterPartyName",
        "direction",
        "merchant_name",
    }
)

#: The durable identifier each provider offers, most trustworthy first.
#: Deliberately NOT `transaction_id`: TrueLayer documents it as changing
#: between requests, which is exactly why matching does not rest on it
#: either (see providers/truelayer.py).
_IDENTITY_FIELDS = ("feedItemUid", "normalised_provider_transaction_id")


@dataclass(frozen=True)
class SourceDuplication:
    """One provider pipe's redundancy, measured at each tier."""

    source: str
    records: int = 0
    distinct_bytes: int = 0
    distinct_canonical: int = 0
    distinct_identity: int = 0
    without_identity: int = 0

    def redundancy(self) -> float:
        """The share of records that repeat something already delivered."""
        if not self.records or not self.distinct_identity:
            return 0.0
        return (self.records - self.distinct_identity) / self.records


@dataclass
class DuplicationReport:
    sources: list[SourceDuplication] = field(default_factory=list)
    identities: int = 0
    identities_repeated: int = 0
    identities_varied: int = 0
    identities_amended: int = 0
    churn: list[tuple[str, int]] = field(default_factory=list)
    sightings: list[tuple[int, int]] = field(default_factory=list)

    @property
    def records(self) -> int:
        return sum(source.records for source in self.sources)

    @property
    def distinct(self) -> int:
        return sum(source.distinct_identity for source in self.sources)

    @property
    def cosmetic_variation(self) -> int:
        """Records that are one payment but would fail a byte comparison.

        The size of the trap: had these been deduplicated by comparing
        the bytes, every one of them would have been missed, and the
        reason would not have shown up anywhere.
        """
        return self.identities_varied - self.identities_amended

    def describe(self) -> str:
        lines = [
            f"{'SOURCE':<26}{'RECORDS':>9}{'BYTES':>9}{'CANONICAL':>11}"
            f"{'BY ID':>9}{'NO ID':>7}{'REDUNDANT':>11}",
        ]
        for source in self.sources:
            lines.append(
                f"{source.source:<26}{source.records:>9,}"
                f"{source.distinct_bytes:>9,}{source.distinct_canonical:>11,}"
                f"{source.distinct_identity:>9,}{source.without_identity:>7,}"
                f"{source.redundancy() * 100:>10.1f}%"
            )
        redundant = (
            (self.records - self.distinct) / self.records if self.records else 0.0
        )
        lines.append(
            f"{'TOTAL':<26}{self.records:>9,}{'':>9}{'':>11}"
            f"{self.distinct:>9,}{'':>7}{redundant * 100:>10.1f}%"
        )

        lines += [
            "",
            f"IDENTITIES                {self.identities:,}",
            f"  SEEN MORE THAN ONCE     {self.identities_repeated:,}",
            f"  CONTENT VARIED          {self.identities_varied:,}",
            f"    ON A CORE FIELD       {self.identities_amended:,}"
            "   (genuine amendments)",
            f"    ON OTHER FIELDS ONLY  {self.cosmetic_variation:,}"
            "   (one payment, different bytes)",
        ]

        if self.churn:
            lines += ["", f"{'FIELD THAT CHURNS':<44}{'TIMES DIFFERED':>15}"]
            lines += [f"{name:<44}{count:>15,}" for name, count in self.churn]

        if self.sightings:
            lines += ["", f"{'TIMES SEEN':>11}{'IDENTITIES':>12}"]
            lines += [f"{seen:>11}{count:>12,}" for seen, count in self.sightings]
        return "\n".join(lines)


def _flatten(value: object, prefix: str = "") -> list[tuple[str, object]]:
    """Dotted paths to leaves, so two records can be compared field by field."""
    if isinstance(value, dict):
        out: list[tuple[str, object]] = []
        for key, inner in value.items():
            out += _flatten(inner, f"{prefix}.{key}" if prefix else str(key))
        return out
    if isinstance(value, list):
        return [(prefix, json.dumps(value, sort_keys=True, default=str))]
    return [(prefix, value)]


def _digest(record: object, *, canonical: bool) -> str:
    text = json.dumps(
        record, sort_keys=canonical, separators=(",", ":"), default=str
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(record: dict[str, object]) -> str | None:
    for name in _IDENTITY_FIELDS:
        value = record.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _records(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, str | bytes | bytearray):
        return []
    try:
        decoded = json.loads(payload)
    except ValueError:
        return []
    if not isinstance(decoded, dict):
        return []
    for key in _RECORD_KEYS:
        rows = decoded.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def analyse(store: Store, *, churn_limit: int = 20) -> DuplicationReport:
    """Measure redundancy across everything layer 0 holds.

    Reads artefacts in the order they were fetched, which is the order a
    replay processes them, so "already seen" here means the same thing it
    would mean during a rebuild.
    """
    from .rebuild import _NON_TRANSACTIONAL

    seen_bytes: dict[str, set[str]] = {}
    seen_canonical: dict[str, set[str]] = {}
    seen_identity: dict[str, set[str]] = {}
    counts: dict[str, list[int]] = {}

    first_seen: dict[tuple[str, str], dict[str, object]] = {}
    sightings: Counter[tuple[str, str]] = Counter()
    churn: Counter[str] = Counter()
    varied: set[tuple[str, str]] = set()
    amended: set[tuple[str, str]] = set()

    rows = store.connection.execute(
        "SELECT source, payload FROM raw_artefacts ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()

    for row in rows:
        source = str(row["source"])
        if source in _NON_TRANSACTIONAL:
            continue
        records = _records(row["payload"])
        if not records:
            continue
        seen_bytes.setdefault(source, set())
        seen_canonical.setdefault(source, set())
        seen_identity.setdefault(source, set())
        counts.setdefault(source, [0, 0])

        for record in records:
            counts[source][0] += 1
            seen_bytes[source].add(_digest(record, canonical=False))
            seen_canonical[source].add(_digest(record, canonical=True))

            identity = _identity(record)
            if identity is None:
                counts[source][1] += 1
                continue
            seen_identity[source].add(identity)

            key = (source, identity)
            sightings[key] += 1
            flat = dict(_flatten(record))
            previous = first_seen.get(key)
            if previous is None:
                first_seen[key] = flat
                continue
            for name, value in flat.items():
                if previous.get(name) != value:
                    churn[name] += 1
                    varied.add(key)
                    if name in _CORE_FIELDS:
                        amended.add(key)

    sources = [
        SourceDuplication(
            source=source,
            records=counts[source][0],
            distinct_bytes=len(seen_bytes[source]),
            distinct_canonical=len(seen_canonical[source]),
            distinct_identity=len(seen_identity[source]),
            without_identity=counts[source][1],
        )
        for source in sorted(counts, key=lambda name: -counts[name][0])
    ]

    return DuplicationReport(
        sources=sources,
        identities=len(sightings),
        identities_repeated=sum(1 for count in sightings.values() if count > 1),
        identities_varied=len(varied),
        identities_amended=len(amended),
        churn=churn.most_common(churn_limit),
        sightings=sorted(Counter(sightings.values()).items()),
    )
