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

import json

import pytest

from obdi.store import Store
from obdi.synthetic import build_world, write_corpus

SEED = 20260812


@pytest.fixture
def corpus(tmp_path):
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, tmp_path / "corpus")
    return tmp_path / "corpus", world, manifest


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
        assert manifest["totals"]["events"] == monthly + planted
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
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        for account in world.accounts:
            payload = (directory / f"{account}.csv").read_bytes()
            parser = detect(payload)
            rows = list(parser.parse(payload, account_id=account))
            with Store(store_path) as store:
                reconcile_batch(store, rows, digest=f"synthetic-{account}")

    def test_EveryPlantedEvent_ArrivesExactlyOnce(self, corpus, tmp_path):
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()

        assert len(derived) == manifest["totals"]["events"], (
            f"planted {manifest['totals']['events']} events and derived "
            f"{len(derived)} (seed {SEED})"
        )
        planted = {(e.account, e.when, e.amount_minor) for e in world.events}
        arrived = {
            (row.account_id, row.value_date.isoformat(), row.amount_minor)
            for row in derived
        }
        assert arrived == planted, (
            "what was derived differs from what was planted - only in "
            f"{sorted(arrived - planted)[:3]} and {sorted(planted - arrived)[:3]} "
            f"(seed {SEED})"
        )

    def test_ImportingTheSameCorpusTwice_AddsNothing(self, corpus, tmp_path):
        """The property every real import depends on, checkable here because the
        right answer is known: the same statement arriving again is the same
        payments, not more of them."""
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)
        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()
        assert len(derived) == manifest["totals"]["events"], (
            f"a second import of the same corpus produced {len(derived)} rows from "
            f"{manifest['totals']['events']} events (seed {SEED})"
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
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        for account in world.accounts:
            payload = (directory / f"{account}.csv").read_bytes()
            parser = detect(payload)
            rows = list(parser.parse(payload, account_id=account))
            with Store(store_path) as store:
                reconcile_batch(store, rows, digest=f"synthetic-{account}")

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
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        directory, _world, _ = corpus
        payload = (directory / "synthetic-current.csv").read_bytes()
        rows = list(detect(payload).parse(payload, account_id="synthetic-current"))
        months = sorted({row.value_date.strftime("%Y-%m") for row in rows})
        skipped = months[len(months) // 2]
        kept = [row for row in rows if row.value_date.strftime("%Y-%m") != skipped]
        assert len(kept) < len(rows), f"the month was not actually withheld (seed {SEED})"

        whole = tmp_path / "whole.sqlite3"
        with Store(whole) as store:
            reconcile_batch(store, rows, digest="synthetic-whole")
            complete = gaps(store.all_transactions())
        assert complete == [], (
            f"a corpus with nothing missing was reported as having "
            f"{[g.month for g in complete]} missing (seed {SEED})"
        )

        partial = tmp_path / "partial.sqlite3"
        with Store(partial) as store:
            reconcile_batch(store, kept, digest="synthetic-partial")
            reported = gaps(store.all_transactions())

        assert [gap.month for gap in reported] == [skipped], (
            f"withheld {skipped} and the detector reported "
            f"{[g.month for g in reported]} (seed {SEED})"
        )
        # Uncontradicted, because only one source ever had it. The distinction
        # matters: a gap another source can see is a fetch that failed, and one
        # nobody can see is a month to go and ask the bank for.
        assert reported[0].seen_in == ()


class TestTheAdversarialDeliveries:
    """Layer 4: the same rows arriving badly.

    Every case here is a RE-delivery of rows the corpus already holds, so they
    cost an import rather than a generation - which is why the adversarial half
    of the generator is the cheap half. Each maps to something that has actually
    happened rather than something imaginable.
    """

    def _land(self, store_path, payload: bytes, account: str, digest: str) -> None:
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        rows = list(detect(payload).parse(payload, account_id=account))
        with Store(store_path) as store:
            reconcile_batch(store, rows, digest=digest)

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
        payload = (directory / "synthetic-current.csv").read_bytes()
        self._land(whole, payload, "synthetic-current", "whole")
        with Store(whole) as store:
            expected = {
                (row.value_date.isoformat(), row.amount_minor, row.description)
                for row in store.all_transactions()
            }
            expected_count = len(store.all_transactions())

        split = tmp_path / "split.sqlite3"
        for delivery in overlapping:
            self._land(
                split,
                (directory / delivery["name"]).read_bytes(),
                delivery["deliver_as"],
                # A different digest each, because they ARE different artefacts -
                # giving them one digest would hide the case being tested.
                digest=delivery["name"],
            )
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
                store_path,
                (directory / f"{account}.csv").read_bytes(),
                account,
                digest=f"correctly-filed-{account}",
            )
        self._land(
            store_path,
            (directory / misfile["name"]).read_bytes(),
            misfile["deliver_as"],
            digest="misfiled",
        )

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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
