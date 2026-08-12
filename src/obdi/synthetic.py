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

    return world


def _statement_csv(world: World, account: str) -> str:
    """One account's events as a Starling personal export.

    That format because the application already reads it, which is the point of
    stage 1: the whole pipeline runs without a document renderer existing.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Date", "Counter Party", "Reference", "Type", "Amount (GBP)"])
    for event in sorted(
        (e for e in world.events if e.account == account), key=lambda e: e.when
    ):
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

    manifest: dict[str, object] = {
        # First, because it is the first thing anybody investigating needs.
        "seed": world.seed,
        "regenerate": (
            f"build_world(seed={world.seed}) then write_corpus - the shape is fixed, "
            "so this rebuilds the identical corpus"
        ),
        "accounts": world.accounts,
        "files": files,
        "events": [asdict(event) for event in world.events],
        # As LISTS, matching what comes back out of the file. A tuple here and a
        # list on disk means an assertion against the returned manifest is not an
        # assertion about what the nightly job will read - which is the drift the
        # decision to make this a file exists to prevent, arriving inside the
        # thing that implements it. Caught by the test that compares the two.
        "transfer_pairs": [list(pair) for pair in world.transfer_pairs],
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
