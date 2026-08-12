"""A household whose finances are KNOWN, exported as statements that are not.

Every feature that reads patterns across a corpus - recurring payments, coverage
gaps, transfer pairing, merchant normalisation - can only be checked against real
data by eye. Nobody knows the right answer for a real bank export, so a gap
detector run over one can be admired and not verified.

This inverts that. A world is generated first: accounts, a salary, standing
commitments, and the transfers between accounts. The ledger follows from the
world, so what SHOULD be derived is decided in advance and written to a manifest
beside the artefacts. The assertions then compare what the application derived
against what was planted.

FOUR DECISIONS, taken 2026-08-12 and recorded in the vault note, because each has
an obvious-looking alternative that is wrong:

  DESCRIPTORS CARRY NOISE. Upper case, trailing reference digits, card suffixes,
  a location tail, and the same merchant spelled differently by different
  issuers. A generator emitting clean names would flatter any normaliser rather
  than test it. The manifest records the INTENDED merchant beside each event, so
  the assertion is "these rows normalise to one payee" rather than "the text
  looks tidy".

  THE MANIFEST IS A FILE. The nightly fresh-slate job runs the real command line
  over a generated corpus in another process and asserts from outside, which an
  in-memory object cannot reach - and generating the corpus twice would be two
  generators drifting apart.

  SHAPE IS FIXED, CONTENT ROTATES. The same number of accounts, months and rows
  every time, so a timing series compares like with like; the merchants, amounts
  and days move with the seed.

  THE SEED IS RECORDED. A defect found against generated data is worth nothing if
  the corpus that found it cannot be rebuilt, and "it failed last Tuesday" is not
  a bug report. The seed is an input, it is written into the manifest, and
  anything asserting against a corpus should say the seed when it fails.

Stage 1 emits CSV only. The import path for it already exists, so the whole
pipeline is exercised end to end without a document renderer - which is what
makes this worth having before any of the later stages.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

#: Merchants a household actually repeats, with the shapes their descriptors
#: take. The tail is what makes a normaliser earn its keep: a reference number
#: that changes every month, a card suffix, a location.
_MERCHANTS = [
    ("Netflix", "NETFLIX.COM {ref}", -1099),
    ("Tesco", "TESCO STORES {ref} {town} GB", -4237),
    ("Thames Water", "THAMES WATER LTD DD {ref}", -3800),
    ("Spotify", "SPOTIFY UK {ref}", -1199),
    ("TfL", "TFL TRAVEL CH {ref}", -275),
]
_TOWNS = ["LONDON", "READING", "BRISTOL", "LEEDS"]

#: The ambiguous case, planted deliberately - see `_plant_ambiguity`. A weekly
#: standing order at a FIXED amount and an IDENTICAL descriptor, because that is
#: the only shape the review queue can be judged against. Its amount is chosen to
#: sit outside every drifting commitment's range so the two cannot interfere.
_STANDING_ORDER = (-2500, "GYM MEMBERSHIP SO REF 4471")
#: A one-off reported twice in the same statement, at an amount nothing else uses.
_DUPLICATE_REPORT = (-6789, "CARPET WORLD 8823 READING GB")


@dataclass(frozen=True)
class PlantedEvent:
    """One thing that happened, and what it was meant to be.

    `merchant` is the intent; `description` is what an issuer would print. The
    pair is the whole oracle for normalisation - without the intent recorded, a
    realistic descriptor is merely an untestable one.
    """

    account: str
    when: str
    amount_minor: int
    description: str
    merchant: str
    kind: str
    #: Set on both legs of an internal transfer, so a pairing can be checked
    #: against what was planted rather than against its own opinion.
    transfer_id: str = ""


@dataclass
class World:
    """The generated household, and everything true about it."""

    seed: int
    accounts: list[str]
    events: list[PlantedEvent] = field(default_factory=list)

    @property
    def transfer_pairs(self) -> list[tuple[str, str]]:
        """(debit description, credit description) for each planted transfer."""
        legs: dict[str, list[PlantedEvent]] = {}
        for event in self.events:
            if event.transfer_id:
                legs.setdefault(event.transfer_id, []).append(event)
        pairs = []
        for members in legs.values():
            if len(members) == 2:
                debit = min(members, key=lambda e: e.amount_minor)
                credit = max(members, key=lambda e: e.amount_minor)
                pairs.append((debit.description, credit.description))
        return pairs


def build_world(seed: int, months: int = 6) -> World:
    """A household over `months`, deterministic in shape and seeded in content.

    Two accounts, because one is not enough to have an internal transfer and the
    transfer is the case every real corpus gets wrong. Salary in, commitments
    out, and a monthly sweep to savings whose two legs are the same money seen
    twice - which is exactly what inflates spending when nothing pairs them.
    """
    # Deliberately the reproducible generator rather than a secure one: the whole
    # value of this module is that the same seed rebuilds the identical corpus,
    # which is the property a cryptographic source is designed NOT to have.
    # Nothing here protects anything.
    rng = random.Random(seed)  # noqa: S311
    world = World(seed=seed, accounts=["synthetic-current", "synthetic-savings"])

    for index in range(months):
        year = 2026 - (1 if index >= 8 else 0)
        month = ((index + 1) % 12) or 12
        payday = date(year, month, 28)

        world.events.append(
            PlantedEvent(
                account="synthetic-current",
                when=payday.isoformat(),
                amount_minor=rng.choice([248000, 251500, 249750]),
                description=f"SALARY {rng.randint(100000, 999999)} BACS",
                merchant="Employer",
                kind="income",
            )
        )

        for merchant, template, base in _MERCHANTS:
            day = min(rng.randint(2, 26), 28)
            # The amount drifts a little, as real ones do - a subscription rises,
            # a shop varies - so exact-amount matching cannot stand in for
            # recognising a recurring payment.
            amount = base - rng.randint(0, 300)
            world.events.append(
                PlantedEvent(
                    account="synthetic-current",
                    when=date(year, month, day).isoformat(),
                    amount_minor=amount,
                    description=template.format(
                        ref=rng.randint(1000, 9999), town=rng.choice(_TOWNS)
                    ),
                    merchant=merchant,
                    kind="spend",
                )
            )

        sweep = rng.choice([20000, 25000, 30000])
        transfer_id = f"sweep-{index}"
        moved = date(year, month, 27)
        world.events.append(
            PlantedEvent(
                account="synthetic-current",
                when=moved.isoformat(),
                amount_minor=-sweep,
                description=f"TRANSFER TO SAVINGS {rng.randint(100, 999)}",
                merchant="Internal transfer",
                kind="transfer",
                transfer_id=transfer_id,
            )
        )
        world.events.append(
            PlantedEvent(
                account="synthetic-savings",
                when=moved.isoformat(),
                amount_minor=sweep,
                description=f"FROM CURRENT {rng.randint(100, 999)}",
                merchant="Internal transfer",
                kind="transfer",
                transfer_id=transfer_id,
            )
        )

    _plant_ambiguity(world, rng, months)
    return world


def _plant_ambiguity(world: World, rng: random.Random, months: int) -> None:
    """The two shapes the review queue exists to tell apart.

    WITHOUT THIS THE CORPUS SCORES A PERFECT ZERO AND MEANS NOTHING BY IT.
    Measured 2026-08-12: the corpus as first built produced no review flags at
    all, which reads as a clean bill and is not one. The matcher only considers
    two rows ambiguous when they share an amount within a SEVEN DAY window, and
    everything planted above is monthly - roughly thirty days apart, so no two
    rows were ever candidates for each other and the queue was never consulted.
    The drifting amounts were a second reason but not the operative one, and the
    note in the vault said so wrongly until this was written.

    So both shapes are planted here, and they are deliberately hard to tell
    apart, because that is the entire difficulty:

      A WEEKLY STANDING ORDER, fixed amount, identical reference, exactly seven
      days apart. Every instalment after the first resembles a duplicate report
      and the matcher must NOT say so - roughly fifty flags a year for one
      commitment is what teaches a reader to ignore the queue. Two priors at a
      consistent interval establish the rhythm, so the expected outcome is one
      flag on the second instalment and silence for the remaining twenty-four.
      That is a deliberate price, not a defect: confirming a commitment once.

      ONE PAYMENT REPORTED TWICE in the same statement, identical in every
      field. This one SHOULD be flagged. It is the case that makes suppressing
      the standing order dangerous, and a corpus containing only the first shape
      would reward a matcher that simply never flags anything.

    The expected flag counts go in the manifest rather than only in a test, so
    the nightly job asserting from another process can hold obdi to them too.
    """
    amount, description = _STANDING_ORDER
    start = date(2026, 1, 5)
    for week in range(_weekly_instalments(months)):
        world.events.append(
            PlantedEvent(
                account="synthetic-current",
                when=date.fromordinal(start.toordinal() + week * 7).isoformat(),
                amount_minor=amount,
                # Identical every week. A standing order quotes one reference
                # for its life, which is exactly what makes it indistinguishable
                # from a repeated report on the facts alone.
                description=description,
                merchant="Gym",
                kind="standing-order",
            )
        )

    amount, description = _DUPLICATE_REPORT
    when = date(2026, 3, 11).isoformat()
    for _ in range(2):
        world.events.append(
            PlantedEvent(
                account="synthetic-current",
                when=when,
                amount_minor=amount,
                description=description,
                merchant="Carpet World",
                kind="duplicate-report",
            )
        )
    # Kept out of the seeded stream on purpose: this is the fixed part of the
    # shape, so it must not move when the seed does.
    del rng


def _weekly_instalments(months: int) -> int:
    """Enough to establish a rhythm and then some, without leaving the corpus.

    Three is the minimum that means anything - the third is the first that CAN
    be suppressed - so a short corpus still exercises the case rather than
    silently skipping it.
    """
    return max(3, months * 4 + 1)


def _statement_csv(world: World, account: str) -> str:
    """One account's whole period as a Starling personal export."""
    return _csv_of(e for e in world.events if e.account == account)


def _csv_of(events: Iterable[PlantedEvent]) -> str:
    """Events as a Starling personal export.

    That format because the application already reads it, which is the point of
    stage 1: the whole pipeline runs without a document renderer existing.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Date", "Counter Party", "Reference", "Type", "Amount (GBP)"])
    for event in sorted(events, key=lambda e: e.when):
        when = date.fromisoformat(event.when).strftime("%d/%m/%Y")
        writer.writerow(
            [
                when,
                event.merchant,
                event.description,
                "FASTER PAYMENT" if event.kind == "transfer" else "CARD PAYMENT",
                f"{event.amount_minor / 100:.2f}",
            ]
        )
    return buffer.getvalue()


@dataclass(frozen=True)
class Delivery:
    """One artefact as it would actually arrive, and what is wrong with it.

    Layer 4 of the design: which files get imported, in what order, and with
    what mistakes. These are RE-deliveries of rows that already exist, so each
    costs an import rather than a generation - which is why the adversarial
    cases are the cheap part of the generator rather than the expensive one.

    `belongs_to` and `deliver_as` are separate on purpose. When they differ the
    artefact is being imported against the wrong account, which is not a
    hypothetical: a mis-tapped picker put 1,571 statement rows in the wrong
    space, and every rebuild re-derived them wrong until they were refiled.
    """

    name: str
    #: The account whose rows the file actually holds.
    belongs_to: str
    #: The account it is delivered against. Differs from `belongs_to` only for
    #: the misfile case.
    deliver_as: str
    #: Inclusive month bounds, as they would appear on a statement header.
    covers: tuple[str, str]
    #: What is wrong with this delivery, or "" when nothing is.
    fault: str
    rows: int


def _months_of(events: Iterable[PlantedEvent]) -> list[str]:
    return sorted({event.when[:7] for event in events})


def _monzo_csv(events: Iterable[PlantedEvent]) -> str:
    """The same events as a Monzo export - a SECOND source for the same rows.

    Not because the household banks with Monzo, but because obdi already reads
    this format and a corpus with one source cannot exercise anything that
    compares sources. That is a real ceiling and not a small one: sibling
    attribution, destination doubt, date transpositions, export drift and stale
    feeds all work by disagreement between two doors onto the same account.

    The two formats differ in EVIDENCE, not only in name, which is the point.
    This one carries a stable transaction id per row and the Starling export
    does not, so the pair exercises the matcher's tier logic rather than merely
    giving it two spellings of the same thing.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Transaction ID", "Date", "Amount", "Description", "Name"])
    for index, event in enumerate(sorted(events, key=lambda e: e.when)):
        when = date.fromisoformat(event.when).strftime("%d/%m/%Y")
        writer.writerow(
            [
                # Stable across regenerations of the same seed, because a
                # source id that moved would make re-import look like new data.
                f"tx_{event.account}_{index:04d}",
                when,
                f"{event.amount_minor / 100:.2f}",
                event.description,
                event.merchant,
            ]
        )
    return buffer.getvalue()


def write_deliveries(world: World, out_dir: Path) -> list[Delivery]:
    """The adversarial deliveries, written beside the clean corpus.

    Deliberately NOT folded into the clean statements: a test that wants the
    honest corpus must be able to get it, and a corpus that is always damaged
    can only measure damage. These are extra files a test opts into.

    Two faults are planted, chosen because neither can be produced by the clean
    corpus and both have cost real money here:

      OVERLAPPING PERIODS. Two statements covering ranges that share months.
      Every row in the shared months arrives twice, from the same source, at the
      same amount and date - which is precisely the shape a genuine repeated
      payment takes. Occurrence numbering is what separates them, and this is
      the only case in the corpus that exercises it. The right answer is exact:
      importing both must leave the same rows as importing the whole period once.

      A MISFILED STATEMENT. One account's file delivered against another. obdi
      cannot know from the file alone, which is the point - the detection has to
      come from noticing that the rows it landed match rows another source filed
      somewhere else.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    deliveries: list[Delivery] = []

    current = [e for e in world.events if e.account == "synthetic-current"]
    months = _months_of(current)
    # A two-month overlap rather than one, so the shared region contains more
    # than a single statement boundary - a one-month overlap can be got right by
    # accident by anything that special-cases the first and last row.
    first_half = months[: len(months) // 2 + 1]
    second_half = months[len(months) // 2 - 1 :]

    for label, span in (("early", first_half), ("late", second_half)):
        rows = [e for e in current if e.when[:7] in span]
        name = f"synthetic-current-{label}.csv"
        (out_dir / name).write_text(_csv_of(rows), encoding="utf-8")
        deliveries.append(
            Delivery(
                name=name,
                belongs_to="synthetic-current",
                deliver_as="synthetic-current",
                covers=(span[0], span[-1]),
                fault=(
                    "overlaps the other half of this account by "
                    f"{len(set(first_half) & set(second_half))} month(s)"
                ),
                rows=len(rows),
            )
        )

    # In the OTHER format, which is what makes the misfile detectable at all.
    # A file uploaded against the wrong account is a second source landing where
    # it does not belong, and the detection is that its rows match rows the
    # first source filed under a sibling account. Delivered in the same format
    # it would simply be more rows from the same door, and nothing could
    # disagree with it - which is exactly what was measured before this existed.
    savings = [e for e in world.events if e.account == "synthetic-savings"]
    name = "synthetic-savings-misfiled.csv"
    (out_dir / name).write_text(_monzo_csv(savings), encoding="utf-8")
    deliveries.append(
        Delivery(
            name=name,
            belongs_to="synthetic-savings",
            deliver_as="synthetic-current",
            covers=(_months_of(savings)[0], _months_of(savings)[-1]),
            fault="delivered against synthetic-current, whose rows these are not",
            rows=len(savings),
        )
    )
    return deliveries


def write_corpus(world: World, out_dir: Path) -> dict[str, object]:
    """Write the statements and the manifest, and return the manifest.

    The manifest goes beside the artefacts rather than being returned only,
    because the job that will assert against this runs in another process.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {}
    for account in world.accounts:
        name = f"{account}.csv"
        (out_dir / name).write_text(_statement_csv(world, account), encoding="utf-8")
        files[account] = name

    deliveries = write_deliveries(world, out_dir)

    manifest: dict[str, object] = {
        # First, because it is the first thing anybody investigating needs.
        "seed": world.seed,
        "regenerate": (
            f"build_world(seed={world.seed}) then write_corpus - the shape is fixed, "
            "so this rebuilds the identical corpus"
        ),
        "accounts": world.accounts,
        "files": files,
        # The clean statements above are what a well-behaved bank sends. These
        # are the same rows arriving badly, and a test opts into them - a corpus
        # that is always damaged can only ever measure damage.
        #
        # `covers` is flattened to a list for the same reason transfer_pairs is:
        # JSON has no tuple, so a tuple here and a list on disk would mean an
        # assertion against the returned manifest is not an assertion about what
        # the nightly job reads. That exact drift appeared once already, in the
        # commit that introduced the manifest.
        "deliveries": [
            {**asdict(delivery), "covers": list(delivery.covers)}
            for delivery in deliveries
        ],
        "events": [asdict(event) for event in world.events],
        # As LISTS, matching what comes back out of the file. A tuple here and a
        # list on disk means an assertion against the returned manifest is not an
        # assertion about what the nightly job will read - which is the drift the
        # decision to make this a file exists to prevent, arriving inside the
        # thing that implements it. Caught by the test that compares the two.
        "transfer_pairs": [list(pair) for pair in world.transfer_pairs],
        # The planted ambiguity, with the RIGHT ANSWER stated. This is the part
        # a real corpus can never supply: over real statements the number of
        # review flags can be counted and not judged, because nobody knows which
        # of them were correct. Here both mistakes are visible - a matcher that
        # flags the whole standing order is noisy, and one that stays silent
        # through the duplicate has bought that silence too cheaply.
        "ambiguity": {
            "standing_order": {
                "description": _STANDING_ORDER[1],
                "instalments": sum(1 for e in world.events if e.kind == "standing-order"),
                "interval_days": 7,
                "expected_flags": 1,
                "why": (
                    "two priors at a consistent interval establish the rhythm, so "
                    "only the second instalment is genuinely undecidable"
                ),
            },
            "duplicate_report": {
                "description": _DUPLICATE_REPORT[1],
                "copies": sum(1 for e in world.events if e.kind == "duplicate-report"),
                "expected_flags": 1,
                "why": "one payment reported twice is the case the queue exists for",
            },
            "expected_flags_total": 2,
        },
        "totals": {
            "events": len(world.events),
            "transfers": len(world.transfer_pairs),
            "merchants": len({e.merchant for e in world.events}),
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
