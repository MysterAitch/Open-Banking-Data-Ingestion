"""The Entities page lists the rules the identified rows teach, and the owner decides them.

KNOWN ANSWERS, decided before the first run (every name, uid, and number invented). The store
holds 42 identified rows: two of one uid (Marlow Bakery, printing "MARLOW BAKERY HIGH STREET 123"
and "... 456", so both open "marlow bakery high street") and forty of forty other uids (a water
board, one row each, so no other row opens so). It also holds three statement rows with no
identifier: two printing "MARLOW BAKERY HIGH STREET LONDON GB" with different numbers (one shape,
two rows) and one printing "MARLOW BAKERY HIGH STREET YORK GB 1".

  - The rule has taught = 2, tested = 40. At the default confidence (300) it is OFFERED: the page
    counts "0 rules apply by default and 1 is offered unticked", says it is unable to confirm from
    the rows held, lists the two descriptions under a closed fold, and links none of the three
    statement rows.
  - Setting the confidence to 40 APPLIES it by default, linking the three rows (two shapes).
  - Ticking the offered rule applies it whatever the confidence says.
  - A support or confidence that means nothing (support 1, a word) is refused and changes nothing.
  - Pressing Not this on ONE inferred description, on the entity page of the party, withdraws the
    WHOLE rule: the other statement shape is named by its description again, and the page counts
    the withdrawal.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
MARLOW = "starling:uid-marlow"
KEY = f"{MARLOW}|marlow bakery high street"
YORK = "MARLOW BAKERY HIGH STREET YORK GB 1"


def row(day: date, minor: int, description: str, uid: str = "") -> Transaction:
    return Transaction(
        account_id=ACCOUNT,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        party_source_id=uid,
        source="starling" if uid else "statement",
        source_id=f"{description}-{day}" if uid else None,
        tier=SourceTier.AUTHORITATIVE if uid else SourceTier.SYNTHETIC,
    )


def world() -> list[Transaction]:
    rows = [
        row(date(2026, 3, 1), -450, "MARLOW BAKERY HIGH STREET 123", MARLOW),
        row(date(2026, 3, 8), -451, "MARLOW BAKERY HIGH STREET 456", MARLOW),
    ]
    rows += [
        row(date(2026, 2, 1 + n % 27), -1000 - n, f"ZEPHYR WATER BOARD {n}", f"starling:uid-z{n}")
        for n in range(40)
    ]
    rows += [
        row(date(2026, 4, 1), -452, "MARLOW BAKERY HIGH STREET LONDON GB 789"),
        row(date(2026, 4, 8), -453, "MARLOW BAKERY HIGH STREET LONDON GB 790"),
        row(date(2026, 4, 15), -454, YORK),
    ]
    return rows


@pytest.fixture
def served(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        reconcile_batch(store, world(), digest="feed")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def press(base: str, route: str, **fields) -> httpx.Response:
    return httpx.post(f"{base}{route}", data=fields, timeout=60)


def shown(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


def rules_text(page: str) -> str:
    return " ".join(
        " ".join(item.text().split())
        for item in elements(parse(page), "li")
        if "ent-learned-rule" in item.classes
    )


def counts_line(page: str) -> str:
    return next(
        " ".join(p.text().split())
        for p in elements(parse(page), "p")
        if "At these settings" in p.text()
    )


def weekly_world() -> list[Transaction]:
    """Eight weekly payments at Marlow from 2026-06-01: even weeks by its uid, odd weeks printed
    by a statement with no identifier, plus the forty other identified rows."""
    rows = []
    for week in range(8):
        day = date(2026, 6, 1) + timedelta(weeks=week)
        if week % 2 == 0:
            rows.append(row(day, -450, f"MARLOW BAKERY HIGH STREET {week}", MARLOW))
        else:
            # A penny apart: the same amount a week away from a feed row would be folded into it
            # as one payment seen by two sources, which is not the case being built.
            rows.append(row(day, -451, f"MARLOW BAKERY HIGH STREET LONDON GB {week}"))
    rows += [
        row(date(2026, 2, 1 + n % 27), -1000 - n, f"ZEPHYR WATER BOARD {n}", f"starling:uid-z{n}")
        for n in range(40)
    ]
    return rows


@pytest.fixture
def served_weekly(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        reconcile_batch(store, weekly_world(), digest="feed")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def kinds_on_recurring(base: str) -> list[str]:
    page = httpx.get(f"{base}/recurring", timeout=60).text
    return [
        " ".join(n.text().split())
        for n in elements(parse(page), "span")
        if "recur-kind" in n.classes
    ]


class TestARecurringSeriesBuiltOnInferredRows:
    def test_WhenTheRuleIsOnlyOffered_TheSeriesIsNotJoinedAndSaysNothingInferred(
        self, served_weekly
    ):
        kinds = kinds_on_recurring(served_weekly)

        assert not any("inferred" in kind for kind in kinds)

    def test_WhenTheRuleApplies_TheEightPaymentsAreOneSeriesAndFourAreSaidToBeInferred(
        self, served_weekly
    ):
        press(served_weekly, "/entities-rule-settings", support="2", confidence="40")

        kinds = kinds_on_recurring(served_weekly)

        assert any("8 payments, 4 of them inferred from the description" in k for k in kinds), kinds


BRANCHES = ("starling:uid-b-north", "starling:uid-b-quay", "starling:uid-b-hill")


def branch_world() -> list[Transaction]:
    """Twelve weekly card payments to one retailer stating one name across three branch uids:
    six at the first, four at the second, two at the third."""
    uids = [BRANCHES[0]] * 6 + [BRANCHES[1]] * 4 + [BRANCHES[2]] * 2
    return [
        Transaction(
            account_id=ACCOUNT,
            amount_minor=-300 - week,
            value_date=date(2026, 7, 6) + timedelta(weeks=week),
            booking_date=date(2026, 7, 6) + timedelta(weeks=week),
            description=f"BRANCHCO {week}",
            counterparty="Branchco",
            party_source_id=uids[week],
            source="starling",
            source_id=f"b{week}",
            tier=SourceTier.AUTHORITATIVE,
        )
        for week in range(12)
    ]


@pytest.fixture
def served_branches(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        reconcile_batch(store, branch_world(), digest="feed")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


class TestSplitIntoLocations:
    def company(self, base: str) -> str:
        press(base, "/entities-merge", name="Branchco Group", shape=list(BRANCHES))
        page = httpx.post(f"{base}/entities", timeout=60).text
        link = next(
            a.attrs["href"]
            for a in elements(parse(page), "a")
            if "/entity?id=" in a.attrs.get("href", "")
        )
        return link.split("id=")[1]

    def test_CompanyHoldingThreeBranchIds_OffersTheSplitAndMakesChildrenByUsage(
        self, served_branches
    ):
        entity = self.company(served_branches)
        page = httpx.post(f"{served_branches}/entity?id={entity}", timeout=60).text
        assert "Split into locations" in parse(page).text()

        done = press(served_branches, "/entity-split-locations", entity=entity)

        assert done.status_code == 200, done.text[:300]
        text = " ".join(parse(done.text).text().split())
        assert "Split Branchco Group into 2 locations under it, the commonest staying" in text
        assert "Branchco Group location 2" in text and "Branchco Group location 3" in text

    def test_SplitTwice_IsRefusedSinceOneIdIsLeft(self, served_branches):
        entity = self.company(served_branches)
        press(served_branches, "/entity-split-locations", entity=entity)

        again = press(served_branches, "/entity-split-locations", entity=entity)

        assert again.status_code == 400
        assert "fewer than two" in again.text

    def test_ALocationAlreadyUnderACompany_IsNotOfferedTheSplit(self, served_branches):
        entity = self.company(served_branches)
        press(served_branches, "/entity-split-locations", entity=entity)
        page = httpx.post(f"{served_branches}/entity?id={entity}", timeout=60).text

        assert "Split into locations" not in parse(page).text()


class TestAnOfferedRule:
    def test_RuleBelowTheDefaultConfidence_IsOfferedUntickedWithItsRowsUnderAClosedFold(
        self, served
    ):
        page = shown(served)

        assert counts_line(page) == (
            "At these settings 0 rules apply by default and 1 is offered unticked; "
            "0 openings are shared by two parties and teach nothing."
        )
        text = rules_text(page)
        assert "taught by 2, tested against 40, would link 3" in text
        assert "offered, unticked: unable to confirm from the rows held" in text
        assert "tested against only 40 other identified rows" in text
        assert " ".join(parse(page).text().split()).count("which they need not be") == 1, (
            "the bound's caveat is said once, at the head of the section"
        )
        assert "need not be" not in text, "and never per rule"
        folds = [d for d in elements(parse(page), "details") if "would link" in d.text()]
        assert len(folds) == 1 and "open" not in folds[0].attrs
        assert "MARLOW BAKERY HIGH STREET LONDON GB" in folds[0].text().upper().replace("  ", " ")

    def test_MaskedPage_SaysTheCountsAndNamesNoParty(self, served):
        page = httpx.get(f"{served}/entities", timeout=60).text

        assert "1 is offered unticked" in counts_line(page)
        assert "uid-marlow" not in page and "MARLOW BAKERY" not in page.upper()

    def test_Page_AtPhoneWidth_AddsNoLineLongerThanTheSentenceItSays(self, served):
        page = shown(served)

        assert len(rules_text(page)) < 1200


class TestTheRulesMeasuredPrecision:
    def test_Page_ShowsTheCountOfLaterIdentifiedRowsEvenAtZero(self, served):
        text = rules_text(shown(served))

        assert "confirmed by 0 later-identified rows" in text

    def test_RuleWithdrawnByALaterRowThatNamedAnotherParty_SaysSoAndLinksNothing(
        self, tmp_path, monkeypatch
    ):
        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            reconcile_batch(store, world(), digest="feed")
            store.set_preference("learned-rules.confidence", "40")
            store.set_preference("learned-rules.agreed:" + KEY, "3")
            store.set_preference("learned-rules.disagreed:" + KEY, "1")
        environment(monkeypatch, tmp_path)
        config = build_web_config(db)
        assert config is not None
        base, stop = serve_config(config)
        try:
            text = rules_text(shown(base))
        finally:
            stop()

        assert "withdrawn: 1 later-identified row named another party" in text
        assert "confirmed by 3 later-identified rows" in text
        assert "applied" not in text


class TestSettingsAndTicks:
    def test_RaisingNothingButLoweringConfidenceToTheTestedRows_AppliesTheRule(self, served):
        response = press(served, "/entities-rule-settings", support="2", confidence="40")

        assert response.status_code == 200
        assert "At these settings 1 rule applies by default and 0 are offered" in counts_line(
            response.text
        )
        text = rules_text(response.text)
        assert "links 3" in text and "applied by default" in text

    def test_RaisingConfidenceAgain_MovesTheRuleBackToOffered(self, served):
        press(served, "/entities-rule-settings", support="2", confidence="40")

        page = press(served, "/entities-rule-settings", support="2", confidence="41").text

        assert "0 rules apply by default and 1 is offered unticked" in counts_line(page)

    def test_RaisingSupportAboveTheRowsThatTaughtIt_UnlearnsTheRule(self, served):
        page = press(served, "/entities-rule-settings", support="3", confidence="40").text

        assert "Learned rules" not in page or rules_text(page) == ""

    def test_TickingTheOfferedRule_AppliesItWhateverTheConfidenceIs(self, served):
        page = press(served, "/entities-rule-tick", rule=KEY).text

        text = rules_text(page)
        assert "links 3" in text and "applied because you ticked it" in text
        raised = press(served, "/entities-rule-settings", support="2", confidence="5000").text
        assert "applied because you ticked it" in rules_text(raised)

    @pytest.mark.parametrize(
        ("support", "confidence"), [("1", "40"), ("2", "0"), ("many", "40"), ("2", "")]
    )
    def test_SettingsThatMeanNothing_AreRefusedAndChangeNothing(
        self, served, support, confidence
    ):
        response = press(served, "/entities-rule-settings", support=support, confidence=confidence)

        assert response.status_code == 400
        assert "whole numbers" in response.text
        assert "1 is offered unticked" in counts_line(shown(served))

    def test_TickingARuleTheRowsNoLongerTeach_IsRefused(self, served):
        response = press(served, "/entities-rule-tick", rule="starling:uid-gone|nothing at all")

        assert response.status_code == 400
        assert "no longer teach" in response.text


class TestKeepOnAnInferredRow:
    def test_Keep_PromotesTheRuleToADeclaredBeginsWithRuleWithItsOrigin(self, served):
        entity = TestNotThisOnAnInferredRow().gathered(served)

        kept = press(
            served,
            "/entity-link-keep",
            entity=entity,
            shape="marlow bakery high street london gb",
        )

        assert kept.status_code == 200, kept.text[:300]
        page = httpx.post(f"{served}/entity?id={entity}", timeout=60).text
        text = " ".join(parse(page).text().split())
        today = datetime.now(UTC).date().isoformat()
        assert f"learned from 2 identified rows, kept by you on {today}" in text
        assert "begin" in text
        assert "kept as a rule of this entity" in text

    def test_Keep_PressedAgain_IsRefusedAndNotThisIsRefusedOnADeclaredLine(self, served):
        entity = TestNotThisOnAnInferredRow().gathered(served)
        fields = {"entity": entity, "shape": "marlow bakery high street london gb"}
        press(served, "/entity-link-keep", **fields)

        again = press(served, "/entity-link-keep", **fields)
        refuse = press(served, "/entity-link-refuse", **fields)

        assert again.status_code == 400 and "already kept" in again.text
        assert refuse.status_code == 400 and "kept" in refuse.text

    def test_KeptRule_SurvivesTheConfidenceRisingAboveItsTestedRows(self, served):
        entity = TestNotThisOnAnInferredRow().gathered(served)
        press(
            served,
            "/entity-link-keep",
            entity=entity,
            shape="marlow bakery high street london gb",
        )

        press(served, "/entities-rule-settings", support="2", confidence="5000")

        page = httpx.post(f"{served}/entity?id={entity}", timeout=60).text
        assert "kept by you on" in " ".join(parse(page).text().split())


class TestNotThisOnAnInferredRow:
    def gathered(self, base: str) -> str:
        press(base, "/entities-rule-settings", support="2", confidence="40")
        merged = press(base, "/entities-merge", name="Marlow Bakery", shape=MARLOW)
        assert merged.status_code == 200, merged.text[:300]
        entity = httpx.post(f"{base}/entities", timeout=60).text
        link = next(
            a.attrs["href"]
            for a in elements(parse(entity), "a")
            if "/entity?id=" in a.attrs.get("href", "")
        )
        return link.split("id=")[1]

    def test_NotThisOnOneInferredRow_WithdrawsTheWholeRuleEndToEnd(self, served):
        entity = self.gathered(served)
        page = httpx.post(f"{served}/entity?id={entity}", timeout=60).text
        assert "inferred from an opening" in parse(page).text(), "listed as learned lines"

        refused = press(
            served,
            "/entity-link-refuse",
            entity=entity,
            shape="marlow bakery high street london gb",
        )

        assert refused.status_code == 200, refused.text[:300]
        after = shown(served)
        assert "1 withdrawn by you" in counts_line(after)
        names = httpx.post(f"{served}/entity?id={entity}", timeout=60).text
        assert "inferred from an opening" not in parse(names).text(), "none is linked now"
