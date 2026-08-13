"""The generated corpus is only useful if the application agrees with it.

Two halves, and both are needed. The generator is checked against itself - a
world with a known shape produces a manifest describing that shape - and then the
whole import path is run over the artefacts and its results compared against what
was planted. Neither half alone is worth much: a manifest nobody imports against
describes nothing, and an import nobody has a manifest for can only be admired.

This is stage 1 as scoped in the design note: CSV only, because the import path
for it already exists and so the pipeline runs end to end without a document
renderer. What it buys immediately is an oracle for the pattern features -
recurring payments and coverage gaps - which over real data can be checked by eye
and nothing else.

THE SEED IS IN EVERY FAILURE MESSAGE that could depend on generated content. A
defect found here is worth nothing if the corpus cannot be rebuilt.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest

from obdi.store import Store
from obdi.synthetic import build_world, write_corpus


def land(store_path, path, account: str):
    """Import one artefact through the door a person uses.

    NOT the reconcile function directly, which every test here used until
    2026-08-13. That path fills the derived layer and leaves the raw artefact
    layer empty; the application rebuilds from raw at startup; so a store built
    the short way is EMPTIED the moment it is served. Measured by loading this
    corpus into the running app: 70 rows to 0, reported as "VANISHED - check
    problems and layer 0".

    The assertions were not wrong - the matching logic was genuinely exercised -
    but they described a store shape the application destroys, and no test here
    could have noticed. Importing the file means what is asserted is a store the
    app can actually hold, and it is what makes test_TheCorpus_SurvivesARebuild
    meaningful rather than tautological.
    """
    from obdi.ingest import import_file

    with Store(store_path) as store:
        return import_file(store, Path(path), account_id=account)


def statement_without(source: Path, month: str, destination: Path) -> Path:
    """The same statement with one month's rows removed.

    A real partial artefact rather than a filtered row list, so it can go
    through the import door like any other file - which is the point: a month
    that was never delivered arrives as a file that does not contain it.
    """
    lines = source.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    year, number = month.split("-")
    kept = [row for row in rows if f"/{number}/{year}" not in row.split(",")[0]]
    destination.write_text("\n".join([header, *kept]) + "\n", encoding="utf-8")
    return destination


SEED = 20260812


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Built once for the module, because nothing here writes into it.

    Every test reads the corpus and writes its STORE into its own tmp_path, so
    they cannot see each other. Rebuilding per test was costing 24 world
    generations and 144 PDF renders for a directory none of them modify - which
    took this file from under two seconds to thirteen when the statements
    landed. If a test ever needs to alter an artefact it must copy it out
    first, exactly as the withheld-month and corrupted-balance cases already do.
    """
    directory = tmp_path_factory.mktemp("corpus-module") / "corpus"
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, directory)
    return directory, world, manifest


class TestTheGeneratedWorld:
    def test_TheManifest_DescribesEveryEventItPlanted(self, corpus):
        directory, world, manifest = corpus

        assert manifest["seed"] == SEED, "the corpus cannot be rebuilt without this"
        assert manifest["totals"]["events"] == len(world.events)
        # Six months, each with a salary, five commitments and a two-legged
        # sweep: the SHAPE is fixed even though the content moves with the seed.
        monthly = 6 * (1 + 5 + 2)
        # Plus the planted ambiguity, which is deliberately NOT seeded - the
        # review queue can only be judged against a fixed number of instalments.
        ambiguity = manifest["ambiguity"]
        planted = (
            ambiguity["standing_order"]["instalments"]
            + ambiguity["duplicate_report"]["copies"]
        )
        # And the card, which no CSV reports: four spends a month, plus a
        # payment in every month after the first, since the first has nothing
        # yet to clear.
        card = sum(1 for event in world.events if event.account == "synthetic-card")
        assert card == 6 * 4 + 5
        assert manifest["totals"]["events"] == monthly + planted + card
        assert manifest["totals"]["transfers"] == 6
        assert planted > 3, "no ambiguity planted, so the review queue asserts nothing"

        on_disk = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk == manifest, (
            "the manifest a later process would read differs from the one returned"
        )

    def test_Descriptors_CarryTheNoiseARealOneWould(self, corpus):
        """A generator emitting tidy names flatters a normaliser rather than
        testing it - so the same merchant must arrive looking different each
        time, with the intended name recorded separately."""
        _, world, _ = corpus

        netflix = [e for e in world.events if e.merchant == "Netflix"]
        assert len(netflix) == 6
        assert len({e.description for e in netflix}) == 6, (
            f"the same merchant produced identical descriptors (seed {SEED}) - "
            "nothing here would exercise normalisation"
        )
        assert all(e.description != e.merchant for e in netflix)

    def test_EveryTransfer_HasBothLegsAndTheyCancel(self, corpus):
        _, world, manifest = corpus

        assert manifest["transfer_pairs"], f"no transfers planted (seed {SEED})"
        by_id: dict[str, list] = {}
        for event in world.events:
            if event.transfer_id:
                by_id.setdefault(event.transfer_id, []).append(event)
        for transfer_id, legs in by_id.items():
            assert len(legs) == 2, f"{transfer_id} has {len(legs)} leg(s), seed {SEED}"
            assert sum(leg.amount_minor for leg in legs) == 0, (
                f"{transfer_id} does not cancel - the same money must appear as "
                f"one debit and one credit (seed {SEED})"
            )
            assert len({leg.account for leg in legs}) == 2


class TestWhatTheApplicationDerivesFromIt:
    def _import(self, store_path, directory, world) -> None:
        """Every statement through the ordinary import path."""
        # The CSV accounts only. The card is reached by statement alone, which
        # is what it exists to exercise, so walking every account and opening a
        # .csv would look for a file the generator deliberately never writes.
        for account in world.csv_accounts:
            land(store_path, directory / f"{account}.csv", account)

    def test_EveryPlantedEvent_ArrivesExactlyOnce(self, corpus, tmp_path):
        directory, world, _manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()

        # The CSV corpus only: this imports the exports, and the card arrives
        # by statement in TestTheGeneratedStatements instead.
        assert len(derived) == len(world.csv_events), (
            f"planted {len(world.csv_events)} events reachable by CSV and derived "
            f"{len(derived)} (seed {SEED})"
        )
        planted = {(e.account, e.when, e.amount_minor) for e in world.csv_events}
        arrived = {
            (row.account_id, row.value_date.isoformat(), row.amount_minor)
            for row in derived
        }
        assert arrived == planted, (
            "what was derived differs from what was planted - only in "
            f"{sorted(arrived - planted)[:3]} and {sorted(planted - arrived)[:3]} "
            f"(seed {SEED})"
        )

    def test_TheCorpus_SurvivesARebuild(self, corpus, tmp_path):
        """The property that failed silently until the app was actually run.

        obdi rebuilds the derived layers from raw at startup. A store whose raw
        layer is empty therefore rebuilds to NOTHING - and every test in this
        file built its store by calling the reconcile function directly, which
        fills the derived layer and leaves layer 0 empty. Serving one of them
        emptied it: 70 rows to 0, reported as VANISHED.

        Nothing here could have noticed, because nothing here rebuilt. So this
        asserts the survival directly rather than trusting that importing
        through the door is enough - the door could change.
        """
        from obdi.rebuild import rebuild_from_raw

        directory, world, _manifest = corpus
        store_path = tmp_path / "store.sqlite3"
        self._import(store_path, directory, world)

        with Store(store_path) as store:
            before = len(store.all_transactions())
            report = rebuild_from_raw(store)
            after = len(store.all_transactions())

        assert before == len(world.csv_events), (
            f"the corpus did not land before the rebuild was even tried "
            f"(seed {SEED})"
        )
        assert report.artefacts_replayed == len(world.csv_accounts), (
            f"the rebuild replayed {report.artefacts_replayed} artefact(s) from a "
            f"store built by importing {len(world.csv_accounts)} - the raw layer "
            f"is not being written (seed {SEED})"
        )
        assert after == before, (
            f"a rebuild took the store from {before} rows to {after}. "
            f"{report.account_changes} (seed {SEED})"
        )
        vanished = {
            account: change
            for account, change in report.account_changes.items()
            if change[1] == 0 and change[0] > 0
        }
        assert not vanished, f"accounts emptied by the rebuild: {vanished} (seed {SEED})"

    def test_ImportingTheSameCorpusTwice_AddsNothing(self, corpus, tmp_path):
        """The property every real import depends on, checkable here because the
        right answer is known: the same statement arriving again is the same
        payments, not more of them."""
        directory, world, _manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)
        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()
        assert len(derived) == len(world.csv_events), (
            f"a second import of the same corpus produced {len(derived)} rows from "
            f"{len(world.csv_events)} events (seed {SEED})"
        )

    def test_TheTransfersMoney_IsNotCountedAsSpending(self, corpus, tmp_path):
        """Both legs of a sweep are in the corpus, which is what inflates
        spending when nothing pairs them. The assertion is deliberately about
        the PLANTED truth: the transfers sum to zero, so any total that includes
        them and any total that excludes them differ by exactly nothing."""
        directory, world, _ = corpus
        store_path = tmp_path / "store.sqlite3"
        self._import(store_path, directory, world)

        transfer_total = sum(e.amount_minor for e in world.events if e.transfer_id)
        assert transfer_total == 0

        with Store(store_path) as store:
            derived = store.all_transactions()
        moved = [
            row
            for row in derived
            if "TRANSFER TO SAVINGS" in row.description or "FROM CURRENT" in row.description
        ]
        assert len(moved) == 12, (
            f"expected six sweeps as twelve rows, found {len(moved)} (seed {SEED})"
        )
        assert sum(row.amount_minor for row in moved) == 0


class TestThePatternFeaturesAgainstKnownAnswers:
    """The reason the generator exists.

    Over real statements these can be checked by eye and nothing else, because
    nobody knows how many internal transfers a real corpus contains. Here the
    answer was decided before the data was written, so a detector finding five or
    seven is wrong in a way real data cannot reveal.
    """

    def _imported(self, store_path, directory, world):
        # The CSV accounts only. The card is reached by statement alone, which
        # is what it exists to exercise, so walking every account and opening a
        # .csv would look for a file the generator deliberately never writes.
        for account in world.csv_accounts:
            land(store_path, directory / f"{account}.csv", account)

    def test_TransferPairing_FindsExactlyThePairsThatWerePlanted(
        self, corpus, tmp_path
    ):
        """Not "some pairs" - THESE pairs. The manifest names all six, so both
        halves of the failure are visible: a pairing that misses one, and a
        pairing that invents one out of two unrelated payments that happen to
        offset."""
        from obdi.ingest import pair_transfers_across_store

        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"
        self._imported(store_path, directory, world)

        with Store(store_path) as store:
            found = pair_transfers_across_store(store)
            by_entity = {
                row.entity_id: row.description for row in store.all_transactions()
            }
            paired = {
                tuple(sorted((by_entity[debit], by_entity[credit])))
                for debit, credit in store.connection.execute(
                    "SELECT debit_entity_id, credit_entity_id FROM transfer_pairs"
                )
            }

        planted = {tuple(sorted(pair)) for pair in manifest["transfer_pairs"]}
        assert found == len(planted), (
            f"planted {len(planted)} transfers, the pass confirmed {found} "
            f"(seed {SEED})"
        )
        assert paired == planted, (
            f"missed {sorted(planted - paired)[:2]}, invented "
            f"{sorted(paired - planted)[:2]} (seed {SEED})"
        )

    def test_TheRuleWritingWorklist_ShowsOneLinePerMerchantItCan(
        self, corpus, tmp_path
    ):
        """Whether one merchant becomes one line of work, or several.

        The generator records the INTENDED merchant beside every event for
        exactly this: over real statements you can see that a worklist looks
        tidy, but not whether its six Netflix rows became one entry or six.

        Both directions are asserted, and the second is the one that costs real
        money: a normaliser too weak makes somebody write six rules for one
        subscription, and one too aggressive quietly files a supermarket and a
        train fare under the same label.
        """
        from obdi.categorise import uncategorised_summary

        directory, world, _ = corpus
        store_path = tmp_path / "store.sqlite3"
        self._imported(store_path, directory, world)

        with Store(store_path) as store:
            worklist = uncategorised_summary(store, limit=100)

        # Group membership is not public, so over-merging is detected through
        # what IS: a group holding more distinct descriptions than the merchant
        # its example belongs to ever planted has swallowed another merchant's
        # rows. Checking the label by eye would not catch this - the label is
        # lossy by design and two merchants can share one.
        planted_descriptions = {
            merchant: {e.description for e in world.events if e.merchant == merchant}
            for merchant in {e.merchant for e in world.events}
        }
        for group in worklist.groups:
            owner = next(
                (e.merchant for e in world.events if e.description == group.example),
                None,
            )
            assert owner is not None, (
                f"worklist group {group.label!r} has an example that was never "
                f"planted: {group.example!r} (seed {SEED})"
            )
            assert group.distinct <= len(planted_descriptions[owner]), (
                f"the line for {owner} holds {group.distinct} distinct descriptions "
                f"but {owner} only ever produced {len(planted_descriptions[owner])} - "
                f"another merchant's rows are filed under it, so a rule written "
                f"here would mislabel them (seed {SEED})"
            )

        lines = {
            merchant: sum(
                1
                for group in worklist.groups
                if any(
                    event.description == group.example and event.merchant == merchant
                    for event in world.events
                )
            )
            for merchant in {event.merchant for event in world.events}
        }
        # A reference number that changes every month is per-instance noise and
        # must not split a merchant. These are the corpus's subscriptions.
        for merchant in ("Netflix", "Spotify", "Thames Water", "TfL"):
            assert lines[merchant] == 1, (
                f"{merchant} occupies {lines[merchant]} worklist lines - its "
                f"descriptors differ only in a reference number (seed {SEED})"
            )

        # A KNOWN AND ACCEPTED LIMIT, pinned so that changing it is a decision
        # rather than a surprise. Tesco's descriptor carries a town that varies,
        # which the stripping does not remove, so one shop occupies two lines.
        # Not treated as a defect: the worklist's label is lossy but its example
        # is matchable, and a rule written for the shop matches both - the cost
        # is an extra line to read, not a wrong rule. Stripping trailing words
        # would risk merging genuinely different merchants, which the assertion
        # above says is the more expensive mistake.
        assert lines["Tesco"] == 2, (
            f"Tesco now occupies {lines['Tesco']} worklist lines rather than 2. "
            f"If the stripping was widened deliberately, update this and check "
            f"nothing over-merged (seed {SEED})"
        )

    def test_TheReviewQueue_FlagsTheDuplicateAndNotTheStandingOrder(
        self, corpus, tmp_path
    ):
        """The measurement the generator was built to make possible.

        Over the real store 419 of 662 transactions are flagged, and that ratio
        can be deplored and not judged: nobody knows which of the 419 were
        right. Here the answer is planted. A weekly standing order must go quiet
        after its rhythm is established, and a payment reported twice must not -
        and a corpus containing only the first would reward a matcher that never
        flags anything at all.
        """
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"
        self._imported(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()
            queued = store.review_queue()
            described = {row.entity_id: row.description for row in derived}

        flagged = [described.get(str(entry["entity_id"]), "?") for entry in queued]
        expected = manifest["ambiguity"]

        duplicate = expected["duplicate_report"]["description"]
        assert flagged.count(duplicate) == expected["duplicate_report"]["expected_flags"], (
            f"a payment reported twice was flagged {flagged.count(duplicate)} time(s), "
            f"expected {expected['duplicate_report']['expected_flags']} - this is the "
            f"case the queue exists for (seed {SEED})"
        )

        order = expected["standing_order"]
        assert flagged.count(order["description"]) == order["expected_flags"], (
            f"a standing order of {order['instalments']} instalments produced "
            f"{flagged.count(order['description'])} flags, expected "
            f"{order['expected_flags']}: {order['why']} (seed {SEED})"
        )

        assert len(queued) == expected["expected_flags_total"], (
            f"{len(queued)} flags from {len(derived)} rows, expected "
            f"{expected['expected_flags_total']} - the surplus were "
            f"{sorted(set(flagged))} (seed {SEED})"
        )

    def test_AMonthNeverDelivered_ShowsAsAGapRatherThanAsQuiet(
        self, corpus, tmp_path
    ):
        """A delivery-level omission, which is the cheap half of the gap case:
        the statement exists and simply is not imported. What makes this
        checkable at all is that the corpus knows the month is missing - over
        real data an empty month and an unimported one look identical.

        THIS ASSERTS ON THE DETECTOR, not on the data. An earlier version
        checked which months were present in the store, which proves the corpus
        has a hole and says nothing about whether obdi reports one - and it was
        described as though it did. Both directions are here now, and the
        second is the one that keeps a report worth reading: a corpus imported
        whole must produce NO gaps at all.
        """
        from obdi.coverage import gaps

        directory, world, _ = corpus
        statement = directory / "synthetic-current.csv"
        months = sorted(
            {e.when[:7] for e in world.events if e.account == "synthetic-current"}
        )
        skipped = months[len(months) // 2]
        partial = statement_without(statement, skipped, tmp_path / "partial.csv")
        assert len(partial.read_text().splitlines()) < len(
            statement.read_text().splitlines()
        ), f"the month was not actually withheld (seed {SEED})"

        whole = tmp_path / "whole.sqlite3"
        land(whole, statement, "synthetic-current")
        with Store(whole) as store:
            complete = gaps(store.all_transactions())
        assert complete == [], (
            f"a corpus with nothing missing was reported as having "
            f"{[g.month for g in complete]} missing (seed {SEED})"
        )

        withheld = tmp_path / "withheld.sqlite3"
        land(withheld, partial, "synthetic-current")
        with Store(withheld) as store:
            reported = gaps(store.all_transactions())

        assert [gap.month for gap in reported] == [skipped], (
            f"withheld {skipped} and the detector reported "
            f"{[g.month for g in reported]} (seed {SEED})"
        )
        # Uncontradicted, because only one source ever had it. The distinction
        # matters: a gap another source can see is a fetch that failed, and one
        # nobody can see is a month to go and ask the bank for.
        assert reported[0].seen_in == ()


class TestAFileThatCorroboratesItself:
    """The balance walk, which had never run against known data.

    Every other check in obdi needs a second source to disagree with. This one
    asks whether the file's own arithmetic holds - each row's balance being the
    previous one plus that row's amount - and answers from nothing else. It also
    settles the SIGN CONVENTION from evidence: a chain that only closes when the
    amounts are negated says the issuer writes them the other way round.

    Both were unreachable until the corpus carried a balance column, and neither
    is reachable from a PDF at all: the structural read those checks consume is
    unavailable for that format, so a statement's opening and closing balances
    do not feed them. That was measured after being assumed wrongly.
    """

    def _verdicts(self, directory, manifest, name: str = ""):
        from obdi.parsers.uk_banks import detect
        from obdi.verification import verify_export

        delivery = next(
            item for item in manifest["deliveries"] if "corroborate itself" in item["fault"]
        )
        payload = (directory / (name or delivery["name"])).read_bytes()
        parsed = list(detect(payload).parse(payload, account_id="synthetic-current"))
        return {
            verdict.name: verdict
            for verdict in verify_export(payload, parsed, delivery["name"])
        }, delivery

    def test_ABalanceCarryingExport_PassesEveryCheckOnItsOwn(self, corpus):
        directory, _world, manifest = corpus

        verdicts, delivery = self._verdicts(directory, manifest)

        walk = verdicts["balance walk"]
        assert walk.ok, f"{walk.detail} (seed {SEED})"
        # One step between each pair of rows: a file of N rows offers N-1
        # chances for the arithmetic to disagree, and all of them are taken.
        assert f"{delivery['rows'] - 1} balance step(s)" in walk.detail, (
            f"expected {delivery['rows'] - 1} steps from {delivery['rows']} rows, "
            f"got {walk.detail!r} (seed {SEED})"
        )
        # The convention is a FINDING, not configuration - this is the only
        # place obdi decides which way round an issuer writes its amounts.
        assert "amounts as-is" in walk.detail, walk.detail

        sign = verdicts["sign"]
        assert sign.ok, f"{sign.detail} (seed {SEED})"
        assert verdicts["structure"].ok and verdicts["dates"].ok

    def test_ASingleWrongBalance_BreaksTheWalk(self, corpus, tmp_path):
        """The red proof, kept rather than run once.

        A check that has never failed has not been checked, and this one is
        arithmetic over a file that was built to be consistent - exactly the
        shape that passes for the wrong reason. So one balance is corrupted by
        a pound and the walk must notice.
        """
        directory, _world, manifest = corpus
        delivery = next(
            item for item in manifest["deliveries"] if "corroborate itself" in item["fault"]
        )
        lines = (directory / delivery["name"]).read_text(encoding="utf-8").splitlines()
        columns = lines[10].split(",")
        columns[-1] = f"{float(columns[-1]) + 1:.2f}"
        lines[10] = ",".join(columns)
        broken = tmp_path / "broken.csv"
        broken.write_text("\n".join(lines) + "\n", encoding="utf-8")

        verdicts, _ = self._verdicts(tmp_path, manifest, name="broken.csv")

        assert verdicts["balance walk"].ok is False, (
            f"a balance moved by a pound and the walk still passed: "
            f"{verdicts['balance walk'].detail!r} (seed {SEED})"
        )
        assert "break" in verdicts["balance walk"].detail


class TestTheGeneratedStatements:
    """PDFs, because a statement carries what no export does.

    The CSV accounts exercise matching and coverage. They cannot exercise the
    things a statement exists for - the opening and closing position, the
    credit limit - and they cannot exercise the balance walk at all: against
    every CSV here that check reports "n/a, no running-balance column", which is
    honest and is not coverage.
    """

    def test_EveryStatement_StatesBalancesThatWalk(self, corpus):
        """The check no CSV in this corpus can feed.

        A statement corroborates ITSELF: opening, plus everything that moved,
        equals closing. Nothing outside the file is needed, which is exactly
        what makes it worth having - a file that cannot be checked against
        anything else can still be checked against its own arithmetic.
        """
        from obdi.parsers.santander_pdf import read_statement
        from obdi.statement_shape import pdf_lines

        directory, _world, manifest = corpus
        statements = manifest["statements"]
        assert len(statements) == 6, (
            f"expected a statement a month, the manifest describes "
            f"{len(statements)} (seed {SEED})"
        )

        for planted in statements:
            reading = read_statement(
                [str(line) for line in pdf_lines(directory / planted["name"])]
            )
            assert reading.transactions, (
                f"{planted['name']} produced no transactions - it did not parse "
                f"(seed {SEED})"
            )
            # Stated as OWED and held as the negative position it is, which is
            # the inversion a reader has to undo and the reason a card was
            # chosen for this rather than another current account.
            assert reading.opening_balance_minor == -planted["opening_owed_minor"]
            assert reading.closing_balance_minor == -planted["closing_owed_minor"]

            moved = sum(row.amount_minor for row in reading.transactions)
            assert reading.opening_balance_minor + moved == reading.closing_balance_minor, (
                f"{planted['name']} does not walk: opens "
                f"{reading.opening_balance_minor}, moves {moved}, closes "
                f"{reading.closing_balance_minor} (seed {SEED})"
            )

    def test_AWrappedDescriptor_LosesTheWholeRow_AndTheStatementSaysSo(
        self, corpus
    ):
        """The quirk that breaks real parsers, and the evidence that catches it.

        A long payee name occupies two lines on a real statement. Neither half
        matches a transaction pattern on its own - the first has no amount, the
        second has no date - so the row is not truncated, it DISAPPEARS. That is
        the worst shape a parsing fault can take: the rows that remain look
        perfectly reasonable, and nothing counts what was offered, because the
        structural read that counts CSV rows is unavailable for PDFs.

        THE FILE STILL CARRIES THE EVIDENCE. The statement states what it should
        sum to, so a lost row breaks its own arithmetic - and this asserts both
        halves, because the second is what makes the first fixable: the walk
        holds for the intact statement and breaks for the wrapped one.

        This reads the parser DIRECTLY, which is deliberate: it pins what the
        reading contains, before any gate has an opinion about it. What obdi
        does with that evidence is asserted separately, in
        `test_AStatementMissingARow_IsRefused_RatherThanImportedShort` - and it
        has to be, because an earlier version of this docstring claimed the
        evidence sat unread for PDFs and was wrong. It sits unread by the
        BALANCE WALK, which needs a structural read only CSV has; the parser's
        own gate reads it at import and refuses. A test that never called that
        door could not tell the two apart.
        """
        from obdi.parsers.santander_pdf import read_statement
        from obdi.statement_shape import pdf_lines
        from obdi.synthetic import _WRAPPED_STATEMENT

        directory, _world, manifest = corpus

        def reading(name: str):
            return read_statement([str(line) for line in pdf_lines(directory / name)])

        intact = reading(manifest["statements"][1]["name"])
        wrapped = reading(_WRAPPED_STATEMENT)

        assert len(wrapped.transactions) < len(intact.transactions), (
            f"the wrapped statement parsed {len(wrapped.transactions)} rows "
            f"against {len(intact.transactions)} intact - nothing was lost, so "
            f"this asserts nothing (seed {SEED})"
        )
        # Lost rather than truncated: no row carries the head of the split
        # descriptor with the amount attached to it.
        assert not any(
            row.description.endswith("SARL") for row in wrapped.transactions
        ), "a half-descriptor was parsed as a transaction"

        def walks(statement) -> bool:
            moved = sum(row.amount_minor for row in statement.transactions)
            return statement.opening_balance_minor + moved == statement.closing_balance_minor

        assert walks(intact), f"the intact statement does not walk (seed {SEED})"
        assert not walks(wrapped), (
            f"a row vanished and the statement's own balances still reconcile, "
            f"so the file carries no evidence of the loss (seed {SEED})"
        )

    def test_AStatementMissingARow_IsRefused_RatherThanImportedShort(self, corpus):
        """What the evidence above is actually FOR, at the door a person uses.

        The test above proves a lost row breaks the statement's own arithmetic.
        This proves obdi acts on that: the wrapped statement is refused, and the
        intact statement of the same month goes through. Without both, "the file
        carries the evidence" is a fact about the file rather than a property of
        the system - and for a while it was believed to be exactly that, because
        every test read the parser directly and none called `parse`.

        Refusing is the right answer and not an obviously safe one: it means a
        statement obdi cannot fully read contributes NOTHING rather than most of
        itself. That is the trade the raw layer exists to make survivable - the
        bytes are kept, so a better parser reads them later without the document
        being fetched again.
        """
        from obdi.parsers.base import ParseError
        from obdi.parsers.uk_banks import detect
        from obdi.synthetic import _WRAPPED_STATEMENT

        directory, _world, manifest = corpus
        intact = manifest["statements"][1]["name"]

        def imported(name: str) -> list[object]:
            payload = (directory / name).read_bytes()
            return list(detect(payload).parse(payload, account_id="synthetic-card"))

        assert imported(intact), (
            f"the intact statement imported nothing, so refusing the wrapped one "
            f"would prove only that the parser refuses everything (seed {SEED})"
        )

        with pytest.raises(ParseError) as refusal:
            imported(_WRAPPED_STATEMENT)

        # The message has to name the discrepancy, or a person meeting it cannot
        # tell a lost row from a credit read as a spend - which is the whole
        # reason the gate reports rather than merely refusing.
        assert "opening balance" in str(refusal.value)
        assert "unexplained" in str(refusal.value)

    def test_TheSameMonthAcrossTwoPages_ReadsTheSameAsOnOne(self, corpus):
        """Page furniture must not be mistaken for the statement's own figures.

        A real issuer repeats its header at the top of every page, including a
        brought-forward line - and on page two that line carries the RUNNING
        figure, not the month's opening. Taking the last occurrence started the
        month from the wrong position: measured at 130.96 where the statement
        opened at 100.96.

        No row was lost and no total was wrong, which is exactly why it is
        worth a test. It made the statement's own arithmetic disagree with
        itself, discrediting the one check that needs nothing but the file -
        so the fault would have surfaced as "this statement does not
        reconcile" and sent somebody looking for a missing transaction that
        does not exist.
        """
        from obdi.parsers.santander_pdf import read_statement
        from obdi.statement_shape import pdf_lines
        from obdi.synthetic import _MULTIPAGE_STATEMENT

        directory, _world, manifest = corpus

        def reading(name: str):
            return read_statement([str(line) for line in pdf_lines(directory / name)])

        single = reading(manifest["statements"][1]["name"])
        paged = reading(_MULTIPAGE_STATEMENT)

        assert paged.opening_balance_minor == single.opening_balance_minor, (
            f"the two-page statement opens at {paged.opening_balance_minor} and "
            f"the same month on one page opens at {single.opening_balance_minor} "
            f"- a carried figure is being read as the opening (seed {SEED})"
        )
        assert paged.closing_balance_minor == single.closing_balance_minor
        assert len(paged.transactions) == len(single.transactions), (
            f"{len(paged.transactions)} rows across two pages against "
            f"{len(single.transactions)} on one - the repeated header is being "
            f"read as a transaction, or a row is lost at the break (seed {SEED})"
        )

        moved = sum(row.amount_minor for row in paged.transactions)
        assert paged.opening_balance_minor + moved == paged.closing_balance_minor, (
            f"the two-page statement does not reconcile with itself (seed {SEED})"
        )

    def test_ABalanceCarriedForward_IsWhereTheLastStatementClosed(self, corpus):
        """Consecutive statements agree with each other, which a single one
        cannot show. A month that opens somewhere other than where the last
        closed is the shape of a missing statement."""
        _directory, _world, manifest = corpus
        statements = manifest["statements"]

        for earlier, later in itertools.pairwise(statements):
            assert later["opening_owed_minor"] == earlier["closing_owed_minor"], (
                f"{later['month']} opens at {later['opening_owed_minor']} but "
                f"{earlier['month']} closed at {earlier['closing_owed_minor']} "
                f"(seed {SEED})"
            )
        assert any(s["closing_owed_minor"] for s in statements), (
            f"every statement closes at zero, so the walk is over a column of "
            f"zeroes and proves nothing (seed {SEED})"
        )

    def test_TheCardStatements_ImportThroughTheOrdinaryDoor(self, corpus, tmp_path):
        """The extraction path end to end, which only a PDF reaches."""
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"
        for planted in manifest["statements"]:
            land(store_path, directory / planted["name"], "synthetic-card")

        with Store(store_path) as store:
            derived = [
                row for row in store.all_transactions()
                if row.account_id == "synthetic-card"
            ]

        planted_rows = [e for e in world.events if e.account == "synthetic-card"]
        assert len(derived) == len(planted_rows), (
            f"planted {len(planted_rows)} card rows and derived {len(derived)} "
            f"(seed {SEED})"
        )
        # A spend leaves the account and a payment arrives, in the house
        # convention - the statement prints both as positive numbers and marks
        # only one, so getting this wrong inverts the whole account.
        assert sum(1 for row in derived if row.amount_minor > 0) == sum(
            1 for event in planted_rows if event.amount_minor > 0
        )


class TestTheAdversarialDeliveries:
    """Layer 4: the same rows arriving badly.

    Every case here is a RE-delivery of rows the corpus already holds, so they
    cost an import rather than a generation - which is why the adversarial half
    of the generator is the cheap half. Each maps to something that has actually
    happened rather than something imaginable.
    """

    def _land(self, store_path, path, account: str, _unused: str = "") -> None:
        """Through the import door. The digest is the artefact's own, computed
        from its bytes, rather than a label the caller invents - which is what
        makes two deliveries of the same file the same artefact."""
        land(store_path, path, account)

    def test_TwoStatementsWhoseMonthsOverlap_LeaveTheSameRowsAsOneWholePeriod(
        self, corpus, tmp_path
    ):
        """The case the clean corpus cannot produce, and the one that pays for
        occurrence numbering.

        Every row in the shared months arrives twice, from the same source, at
        the same amount and date - which is exactly the shape a genuine repeated
        payment takes, so a matcher cannot tell them apart on the facts. The
        right answer is known and exact: the same rows as importing the whole
        period once. Too few means real payments were swallowed as duplicates;
        too many means the overlap was counted twice.
        """
        directory, world, manifest = corpus
        overlapping = [
            delivery
            for delivery in manifest["deliveries"]
            if "overlaps" in delivery["fault"]
        ]
        assert len(overlapping) == 2, (
            f"expected two overlapping halves, the manifest describes "
            f"{len(overlapping)} (seed {SEED})"
        )
        # Without this the test can pass while testing nothing: two halves that
        # happen not to share any rows are just a whole period in two files, and
        # the assertions below would hold trivially.
        delivered = sum(delivery["rows"] for delivery in overlapping)
        whole_rows = sum(
            1 for event in world.events if event.account == "synthetic-current"
        )
        assert delivered > whole_rows, (
            f"the two halves deliver {delivered} rows for a {whole_rows}-row "
            f"account, so nothing is actually delivered twice (seed {SEED})"
        )

        whole = tmp_path / "whole.sqlite3"
        self._land(whole, directory / "synthetic-current.csv", "synthetic-current")
        with Store(whole) as store:
            expected = {
                (row.value_date.isoformat(), row.amount_minor, row.description)
                for row in store.all_transactions()
            }
            expected_count = len(store.all_transactions())

        split = tmp_path / "split.sqlite3"
        for delivery in overlapping:
            # Each file carries its own digest, computed from its bytes, so two
            # genuinely different artefacts are two artefacts without anybody
            # having to say so - and two deliveries of the SAME file are one.
            self._land(split, directory / delivery["name"], delivery["deliver_as"])
        with Store(split) as store:
            landed = store.all_transactions()
            actual = {
                (row.value_date.isoformat(), row.amount_minor, row.description)
                for row in landed
            }

        assert actual == expected, (
            f"overlapping deliveries derived different rows than one whole "
            f"period: missing {sorted(expected - actual)[:2]}, extra "
            f"{sorted(actual - expected)[:2]} (seed {SEED})"
        )
        assert len(landed) == expected_count, (
            f"{len(landed)} rows from two overlapping statements against "
            f"{expected_count} from the whole period - the overlap was "
            f"{'double counted' if len(landed) > expected_count else 'swallowed'} "
            f"(seed {SEED})"
        )

    def test_TheSameAccountFromTwoSources_MergesRatherThanDoubles(
        self, corpus, tmp_path
    ):
        """The strongest thing this corpus can assert, because it is the
        matcher's entire purpose.

        Two doors onto one account report the same payments. If they double, the
        household's spending is overstated by a whole statement; if they
        over-merge, real payments vanish. The planted answer is exact: the same
        number of entities as the account has events, no matter how many sources
        described them.

        Two disagreements are deliberately planted, because two identical files
        would test nothing the duplicate case did not - one payment settles a
        day later in the second source and must still be recognised as the same
        payment, and one is missing entirely, which is what a feed gap looks
        like.
        """
        directory, world, manifest = corpus
        second = next(
            delivery
            for delivery in manifest["deliveries"]
            if "second door" in delivery["fault"]
        )
        planted = [e for e in world.events if e.account == "synthetic-current"]
        assert second["rows"] < len(planted), (
            f"the second source holds {second['rows']} of {len(planted)} rows, so "
            f"nothing was withheld and the feed gap is not planted (seed {SEED})"
        )

        store_path = tmp_path / "store.sqlite3"
        self._land(
            store_path, directory / "synthetic-current.csv", "synthetic-current"
        )
        self._land(store_path, directory / second["name"], "synthetic-current")

        with Store(store_path) as store:
            derived = [
                row
                for row in store.all_transactions()
                if row.account_id == "synthetic-current"
            ]

        sources = {row.source for row in derived}
        assert len(sources) > 1, (
            f"only {sources} landed, so the two sources were not both imported "
            f"and merging is not being tested (seed {SEED})"
        )
        assert len(derived) == len(planted), (
            f"{len(planted)} payments described by two sources became "
            f"{len(derived)} rows. "
            + (
                "They doubled, so spending is overstated by a whole statement"
                if len(derived) > len(planted)
                else "Real payments were swallowed as duplicates"
            )
            + f" (seed {SEED})"
        )

        # The count alone cannot tell a merge from a second import that landed
        # nothing: both give 69. This is the discriminator - the second source
        # supplied the current facts for rows the first source had already
        # placed, so most entities now carry ITS name.
        by_source = Counter(row.source for row in derived)
        assert by_source["monzo-csv"] > 1, (
            f"the second source contributed nothing - entities are {dict(by_source)}, "
            f"so this counted a merge that never happened (seed {SEED})"
        )

        # The payment that settles a day late is ONE entity carrying the settled
        # date, not two rows a day apart. Asserted on the pair rather than the
        # count, because a matcher that dropped the later sighting entirely
        # would also leave 69 rows.
        shifted = [
            row.value_date.isoformat()
            for row in derived
            if row.value_date.isoformat()
            not in {event.when for event in planted}
        ]
        assert len(shifted) == 1, (
            f"expected exactly one payment to carry a date the first source "
            f"never showed, found {shifted} (seed {SEED})"
        )

    def test_ADayMonthTransposition_IsNamedWithBothDates(self, corpus, tmp_path):
        """The corruption every other check here is blind to.

        The amount is right, the payee is right, and the date is a perfectly
        real date - so moving a payment between months changes neither the count
        nor the sum, and count-and-total checks pass while the data is
        systematically wrong. Only two sources dating the same payment
        differently can reveal it, which is why this was unreachable until the
        corpus had a second door.

        The planted answer is exact, so both failures are visible: missing the
        transposition, and inventing one from two ordinary payments whose dates
        happen to mirror. The second is not hypothetical - the detector's own
        comment records a road charge paid on 01-04 AND 04-01 flooding it with
        six lines of coincidence on first firing.
        """
        from obdi.coverage import transpositions

        directory, _world, manifest = corpus
        planted = next(
            delivery
            for delivery in manifest["deliveries"]
            if "transposed" in delivery["fault"]
        )
        store_path = tmp_path / "store.sqlite3"
        self._land(
            store_path, directory / "synthetic-current.csv", "synthetic-current"
        )
        self._land(store_path, directory / planted["name"], "synthetic-current")

        with Store(store_path) as store:
            found = transpositions(store.all_transactions())

        assert len(found) == 1, (
            f"planted exactly one transposition and the detector reported "
            f"{[t.describe() for t in found]} (seed {SEED})"
        )
        # Both dates must appear in the report. A finding that names only one is
        # not actionable: the whole question is which of two real dates is right.
        both = {found[0].left_date.isoformat(), found[0].right_date.isoformat()}
        assert all(day in planted["fault"] for day in both), (
            f"the detector reported {sorted(both)}, which are not the dates the "
            f"manifest planted: {planted['fault']} (seed {SEED})"
        )
        assert found[0].left_date.day == found[0].right_date.month
        assert found[0].left_date.month == found[0].right_date.day

        # Rendered as money, like every other amount a person is shown. Found
        # by LOOKING at the page: this line leads the agreement report and was
        # the only amount on it without a currency symbol, sitting directly
        # above the same figure rendered as -£44.83 by the row beneath it.
        described = found[0].describe()
        assert "£" in described, (
            f"the alarm that leads the page renders its amount as a bare number: "
            f"{described!r} (seed {SEED})"
        )

    def test_OneFileUnderTwoNames_IsOneArtefactThatKnowsBoth(
        self, corpus, tmp_path
    ):
        """A second download, which a person produces by accident constantly.

        The browser appends "(1)" and the bytes are identical. The right answer
        is ONE artefact that knows both of its names - not two artefacts holding
        the same evidence, and not a second copy of every row. Both halves
        matter: collapsing to one artefact is what keeps the evidence single,
        and keeping both names is what lets somebody recognise the file they
        have on disk.
        """
        directory, _world, manifest = corpus
        copy = next(
            delivery
            for delivery in manifest["deliveries"]
            if "second download" in delivery["fault"]
        )
        original = directory / "synthetic-current.csv"
        assert (directory / copy["name"]).read_bytes() == original.read_bytes(), (
            f"the copy is not byte-identical, so any digest result below would "
            f"be explained by the bytes rather than by the naming (seed {SEED})"
        )

        store_path = tmp_path / "store.sqlite3"
        first = land(store_path, original, "synthetic-current")
        again = land(store_path, directory / copy["name"], "synthetic-current")

        assert first.artefact_new and not again.artefact_new, (
            f"the same bytes under a second name landed as a new artefact "
            f"(seed {SEED})"
        )
        assert again.inserted == 0 and again.matched == first.inserted, (
            f"a re-download added {again.inserted} row(s) and matched "
            f"{again.matched} of {first.inserted} (seed {SEED})"
        )

        with Store(store_path) as store:
            artefacts = store.connection.execute(
                "SELECT digest FROM raw_artefacts"
            ).fetchall()
            names = {
                row["origin"]
                for row in store.connection.execute(
                    "SELECT origin FROM artefact_origins"
                )
            }

        assert len(artefacts) == 1, (
            f"{len(artefacts)} artefacts hold the same bytes (seed {SEED})"
        )
        assert names == {original.name, copy["name"]}, (
            f"the artefact knows {sorted(names)} rather than both names it "
            f"arrived under (seed {SEED})"
        )

    def test_AMisfiledStatement_IsAttributedToTheAccountItsRowsBelongTo(
        self, corpus, tmp_path
    ):
        """A mis-tapped picker once put 1,571 statement rows in the wrong space,
        and every rebuild re-derived them wrong until they were refiled.

        obdi cannot tell from the file alone - nothing in a CSV says which
        account it belongs to. The detection is that the rows which landed match
        rows ANOTHER SOURCE filed under a sibling account, which is why the
        misfiled artefact is written in the second format: a file uploaded
        against the wrong account IS a second source arriving where it does not
        belong. Delivered in the same format it would just be more rows from the
        same door, with nothing able to disagree with it.

        The assertion is on the EVIDENCE, not on a count. A number that moved
        proves nothing about whether it moved for the right reason, and the
        attribution carries the sibling account and the matched date precisely
        so a nonsense match announces itself.
        """
        from obdi.coverage import agreements

        directory, _world, manifest = corpus
        misfile = next(
            delivery
            for delivery in manifest["deliveries"]
            if delivery["belongs_to"] != delivery["deliver_as"]
        )
        store_path = tmp_path / "store.sqlite3"

        # Both accounts' own statements first. The correct copy elsewhere is
        # what makes the misfile detectable rather than merely undetected, and
        # the destination's own rows are what the arriving source disagrees
        # WITH - without them there is one source in the account and nothing to
        # compare. Leaving that out is how this test first failed.
        for account in (misfile["belongs_to"], misfile["deliver_as"]):
            self._land(
                store_path, directory / f"{account}.csv", account
            )
        self._land(store_path, directory / misfile["name"], misfile["deliver_as"])

        with Store(store_path) as store:
            derived = store.all_transactions()

        landed = [row for row in derived if row.account_id == misfile["deliver_as"]]
        assert len(landed) >= misfile["rows"], (
            f"the misfiled statement did not land against {misfile['deliver_as']} "
            f"at all, so there is nothing to detect (seed {SEED})"
        )
        sources = {row.source for row in landed}
        assert len(sources) > 1, (
            f"{misfile['deliver_as']} holds only {sources}, so nothing can "
            f"disagree with the misfiled rows (seed {SEED})"
        )

        siblings = {
            source: [misfile["belongs_to"], misfile["deliver_as"]]
            for source in {row.source for row in derived}
        }
        found = agreements(derived, sibling_accounts=siblings)

        attributed = [
            attribution
            for agreement in found
            for attribution in agreement.attributed
            if attribution.sibling_account == misfile["belongs_to"]
        ]
        assert attributed, (
            f"{misfile['rows']} rows landed against {misfile['deliver_as']} while "
            f"identical rows sat under {misfile['belongs_to']}, and nothing was "
            f"attributed there. Agreements found: {found} (seed {SEED})"
        )
        # Every attribution names the account its row really belongs to - the
        # check that this found the misfile rather than some other disagreement.
        assert all(
            attribution.sibling_account == misfile["belongs_to"]
            for attribution in attributed
        )


class TestTheReportAPersonActuallyReads:
    """Every detector underneath this is now checked against a known answer.
    The page a person opens was not.

    These are not assertions about formatting. Each one is a claim the reader
    acts on: what to look at first, what to go and fetch, and whether a
    reassuring line means a check passed or never ran.
    """

    def _land(self, store_path, path, account: str, _unused: str = "") -> None:
        """Through the import door. The digest is the artefact's own, computed
        from its bytes, rather than a label the caller invents - which is what
        makes two deliveries of the same file the same artefact."""
        land(store_path, path, account)

    def _report_for(self, store_path):
        """Built exactly as the `coverage` command builds it.

        BY SIGHTING, not by stored row. The stored source is last-writer-wins
        after a merge, so grouping stored rows by source undercounts every
        payment a second source corroborated and then reports the shortfall as
        missing months. A test that assembled the page its own way would be
        asserting about a page the command never produces - which is what this
        first did, and two of these tests passed that way.
        """
        from obdi.coverage import agreements, coverage, gaps, report, transpositions

        with Store(store_path) as store:
            held = store.transactions_by_sighting()
        return report(coverage(held), agreements(held), gaps(held), transpositions(held))

    def test_ATransposition_IsPutAboveEverythingElse(self, corpus, tmp_path):
        """Ordering is the finding here, not decoration.

        A transposition is the one thing on this page that every other check
        passes while it is true - the counts tally and the totals agree - so a
        reader who meets it after two screens of healthy figures has already
        been told the data is fine.
        """
        directory, _world, manifest = corpus
        planted = next(
            delivery
            for delivery in manifest["deliveries"]
            if "transposed" in delivery["fault"]
        )
        store_path = tmp_path / "store.sqlite3"
        self._land(
            store_path,
            (directory / "synthetic-current.csv"),
            "synthetic-current",
            "first",
        )
        self._land(
            store_path,
            (directory / planted["name"]),
            "synthetic-current",
            "transposed",
        )

        page = self._report_for(store_path)

        assert "DATES DISAGREE" in page, (
            f"a planted transposition does not appear on the report at all "
            f"(seed {SEED})"
        )
        assert page.index("DATES DISAGREE") < page.index("What the store holds"), (
            "the transposition is reported BELOW the healthy figures, so a reader "
            f"meets it after being reassured (seed {SEED})"
        )

    def test_OneSourceOnly_SaysNothingWasComparedRatherThanNoDisagreements(
        self, corpus, tmp_path
    ):
        """The difference between a check that passed and one that never ran.

        A single source cannot be compared with anything. Reporting that as
        agreement would hand the reader confidence drawn from a comparison that
        did not happen - which is the same fault as a green test that never
        exercised its subject.
        """
        directory, _world, _ = corpus
        store_path = tmp_path / "store.sqlite3"
        self._land(
            store_path,
            (directory / "synthetic-current.csv"),
            "synthetic-current",
            "only",
        )

        page = self._report_for(store_path)

        assert "nothing was compared" in page, (
            f"a single-source store does not say so plainly (seed {SEED})"
        )
        assert "No disagreements" not in page

    def test_AMonthNobodyHas_ReadsAsQuietRatherThanAsAFileToFetch(
        self, corpus, tmp_path
    ):
        """The other absence, and the opposite advice.

        A month one source lacks and another holds is a file to go and fetch.
        A month NO source has is most likely the truth - the account was quiet -
        and telling somebody to download it sends them after nothing. The
        detector separates the two; this asserts the PAGE does, because the
        distinction only pays off in the words a person reads.

        Its sibling below covers the contradicted case, and both are needed: a
        report that says "fetch this" for every absence trains its reader to
        ignore it, and one that says "probably quiet" for every absence hides
        the fetchable ones.
        """
        directory, world, _manifest = corpus
        statement = directory / "synthetic-current.csv"
        months = sorted(
            {e.when[:7] for e in world.events if e.account == "synthetic-current"}
        )
        withheld = months[len(months) // 2]
        partial = statement_without(statement, withheld, tmp_path / "quiet.csv")

        store_path = tmp_path / "store.sqlite3"
        land(store_path, partial, "synthetic-current")

        page = self._report_for(store_path)

        assert "Empty months, but NO source has data for them" in page, (
            f"a month absent from the only source is not reported as an "
            f"uncontradicted gap (seed {SEED})"
        )
        assert "most likely the account was simply quiet" in page
        assert "Download those months and import them" not in page, (
            f"the page tells the reader to fetch a month no source has - there "
            f"is nothing to fetch (seed {SEED})"
        )

    def test_AMonthOneSourceWithheld_IsReportedAsAFileToFetch(
        self, corpus, tmp_path
    ):
        """The case a coverage report exists for, and it was masked until
        2026-08-13.

        The page is built from the per-sighting view, which lists each payment
        once per source that observed it. That view used to carry the STORED
        date, which is last-writer-wins after a merge - so a payment the first
        source saw in March, dated a day later by the second, counted towards
        the FIRST source's April. A source was credited with months it never
        reported, and a real hole disappeared.

        Now each sighting carries its own observed date, so the withheld month
        is absent from the source that withheld it, present in the source that
        has it, and reported as CONTRADICTED - which is what turns "a month is
        empty" into "go and fetch this file". The distinction is the whole
        point: a month every source agrees is empty is most likely the truth.
        """
        from obdi.coverage import gaps
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        directory, _world, manifest = corpus
        second = next(
            delivery
            for delivery in manifest["deliveries"]
            if "second door" in delivery["fault"]
        )

        store_path = tmp_path / "store.sqlite3"
        # A hole in the MIDDLE of the first source's period, not a short tail.
        # A source that simply stopped has no enclosed month and is correctly
        # not reported - an account falling out of use is the truth, not a gap.
        # Getting that wrong is how this test first failed.
        payload = (directory / "synthetic-current.csv").read_bytes()
        rows = list(detect(payload).parse(payload, account_id="synthetic-current"))
        months = sorted({row.value_date.strftime("%Y-%m") for row in rows})
        withheld = months[len(months) // 2]
        kept = [row for row in rows if row.value_date.strftime("%Y-%m") != withheld]
        assert len(kept) < len(rows)
        with Store(store_path) as store:
            reconcile_batch(store, kept, digest="first-with-a-hole")

        # The second source covers the whole period, so it can see that month.
        self._land(
            store_path, (directory / second["name"]), "synthetic-current", "second"
        )

        with Store(store_path) as store:
            held = store.transactions_by_sighting()

        months_seen: dict[str, set[str]] = {}
        for row in held:
            months_seen.setdefault(row.source, set()).add(
                row.value_date.strftime("%Y-%m")
            )
        assert len(months_seen) > 1, (
            f"only {set(months_seen)} sighted, so nothing is being tested "
            f"(seed {SEED})"
        )

        # A source is credited only with the months it actually reported. This
        # asserted the OPPOSITE until 2026-08-13, when sightings began carrying
        # their own observed date: before that a payment the first source saw in
        # March, dated a day later by the second, counted towards the first
        # source's April, and the gap below was masked entirely.
        assert withheld not in months_seen["starling-csv"], (
            f"starling-csv is credited with {withheld}, a month it never "
            f"reported - so its sightings are carrying the merged date again "
            f"(seed {SEED})"
        )
        assert withheld in months_seen["monzo-csv"], (
            f"the second source should hold {withheld}, or there is no "
            f"contradiction to find (seed {SEED})"
        )

        # And the gap is now REPORTED, with the source that contradicts it
        # named - which is what turns "a month is empty" into "fetch this file".
        contradicted = [gap for gap in gaps(held) if gap.month == withheld]
        assert contradicted, (
            f"{withheld} is missing from one source and present in another, and "
            f"no gap was reported: {[(g.source, g.month) for g in gaps(held)]} "
            f"(seed {SEED})"
        )
        assert any(gap.contradicted for gap in contradicted), (
            f"the gap for {withheld} was reported as unwitnessed, so it reads as "
            f"a quiet month rather than a file to fetch (seed {SEED})"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
