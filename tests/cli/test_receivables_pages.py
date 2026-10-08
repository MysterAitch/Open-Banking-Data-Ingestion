"""A coffee that is also a reclaimable expense (plan.md worked example E), through the served pages.

KNOWN ANSWERS, decided before the first run (2026-10-20 unless a test says otherwise). The owner
paid 4.20 at a coffee shop on 10-03 and says the organisation he volunteers for owes it, labelled
"volunteering":
  - Marked, it is owed by the organisation, expected 28 days on (2026-10-31): not yet late, so
    Today says nothing and This month and Position list it.
  - On 2026-11-05 it is late and Today asks, with the name and the figure sealed.
  - 4.20 from the organisation on 10-15 closes it: nothing is owed and the label's year says 4.20
    reimbursed, 0.00 owed. The same amount from anyone else closes nothing.
  - Closed by hand it needs a reason, which is kept, and it cannot be closed twice.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

from flow_world import pin
from landing import import_file
from obdi.cli import build_web_config
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.entity_records import STATED_NAME, Identifier
from obdi.ingest.store import Store
from obdi.read.ledger import row_anchor
from obdi.verify.balance_anchors import record_stated_anchor
from section_harness import environment, serve_config
from this_month_world import write_export

ACCOUNT = "current-main"
CHARITY = "Brindlewick Volunteers"
OTHER = "Fenwick Club"
COFFEE = "Corner Coffee"
TODAY = date(2026, 10, 20)


def text_of(html: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return re.sub(r" ([.,;)])", r"\1", spaced).replace("( ", "(")


@contextmanager
def served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    today: date = TODAY,
    transfer_from: str = "",
    known_entity: bool = True,
) -> Iterator[tuple[str, Path, str, str]]:
    """Yields the address, the store, and the anchors of the coffee and of money received."""
    pin(monkeypatch, today)
    db = tmp_path / "store.sqlite3"
    rows = [
        (date(2026, 10, 3), COFFEE, "-4.20"),
        (date(2026, 10, 4), "Corner Bakery", "-3.20"),
        (date(2026, 10, 6), "Brindlewick Payroll", "250.00"),
    ]
    if transfer_from:
        rows.append((date(2026, 10, 15), transfer_from, "4.20"))
    with Store(db) as store:
        store.declare_account(
            AccountRecord(ref=AccountRef(ACCOUNT), kind="current", label="Everyday")
        )
        write_export(tmp_path / "a.csv", sorted(rows))
        import_file(store, tmp_path / "a.csv", account_id=ACCOUNT)
        record_stated_anchor(store, ACCOUNT, "2026-10-19", "1000.00", today=today)
        if known_entity:
            for name in (CHARITY, OTHER):
                store.create_entity(name, [Identifier(STATED_NAME, name.casefold())])
        held = {t.description: t for t in store.all_transactions()}
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield (
            base,
            db,
            row_anchor(held[COFFEE].entity_id),
            row_anchor(held["Brindlewick Payroll"].entity_id),
        )
    finally:
        stop()


def declare(base: str, anchor: str, **fields: str) -> httpx.Response:
    data = {"anchor": anchor, "debtor": CHARITY, "label": "volunteering", "month": ""}
    data.update(fields)
    return httpx.post(f"{base}/receivable-declare", data=data, timeout=60)


def month_shown(base: str) -> str:
    return text_of(httpx.post(f"{base}/this-month", timeout=60).text)


def month_masked(base: str) -> str:
    return text_of(httpx.get(f"{base}/this-month", timeout=60).text)


def today_masked(base: str) -> str:
    return text_of(httpx.get(f"{base}/", timeout=60).text)


def position_shown(base: str) -> str:
    return text_of(httpx.post(f"{base}/position", timeout=60).text)


class TestDeclaringOnTheRow:
    def test_Press_WhenTheCoffeeIsMarkedOwed_IsAnsweredWithTheMaskedLedgerAndNoNameOrAmount(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            response = declare(base, coffee)

        page = text_of(response.text)
        assert response.status_code == 200
        assert "Marked as owed to you." in page
        assert CHARITY not in page
        assert "4.20" not in page

    def test_ThisMonth_WhenMarkedAndNotYetLate_ListsWhoOwesWhatForWhatByWhenAndTheLabelsYear(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            page = month_shown(base)

        assert f"{CHARITY} owes £4.20 for volunteering, expected by 2026-10-31" in page
        assert "late" not in page.split("Owed to you")[1].split("Labels")[0]
        assert "volunteering this year: £4.20, of which £0.00 reimbursed, £4.20 owed" in page

    def test_Position_WhenMarked_SumsWhatIsOwedToYouAndNamesWhoOwesIt(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            page = position_shown(base)

        assert "Owed to you £4.20" in page
        assert f"{CHARITY}: £4.20 for volunteering, expected by 2026-10-31" in page

    def test_Today_WhenMarkedButNotYetPastTheExpectedDay_SaysNothing(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            page = today_masked(base)

        assert "Owed to you" not in page

    def test_Today_WhenPastTheExpectedDay_AsksWithTheNameAndFigureSealed(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, today=date(2026, 11, 5)) as (base, _, coffee, _income):
            declare(base, coffee)
            page = today_masked(base)

        assert f"Owed to you: {MASKED_TOTAL}" in page
        assert CHARITY not in page
        assert "4.20" not in page

    def test_ThisMonth_WhenMasked_CarriesNeitherTheNameNorTheAmountNorTheLabel(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            page = month_masked(base)

        assert "Owed to you" in page
        assert CHARITY not in page
        assert "4.20" not in page
        assert "volunteering" not in page

    def test_Ledger_WhenMarked_TheRowSaysWhatWasDeclaredAndOnlyOnTheShownPageWhoOwesIt(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            shown = text_of(httpx.post(f"{base}/ledger", data={"ref": ACCOUNT}, timeout=60).text)
            masked = text_of(httpx.get(f"{base}/ledger", params={"ref": ACCOUNT}, timeout=60).text)

        assert f"Owed back {CHARITY} owes £4.20 (volunteering), expected by 2026-10-31" in shown
        assert "Owed back" in masked
        assert CHARITY not in masked

    def test_Ledger_WhenNothingIsMarked_EachPaymentOffersTheOnePressAndMoneyInDoesNot(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _, coffee, income):
            html = httpx.get(f"{base}/ledger", params={"ref": ACCOUNT}, timeout=60).text

        assert f'name="anchor" value="{coffee}"' in html
        assert f'value="{income}"' not in html.replace(f"anchor-{income}", "")
        assert html.count('form="owed-form"') >= 4


class TestMetByATransfer:
    def test_ThisMonth_WhenTheOrganisationPaidTheAmount_NothingIsOwedAndTheLabelSaysReimbursed(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, transfer_from=CHARITY) as (base, _, coffee, _income):
            declare(base, coffee)
            page = month_shown(base)

        assert "Owed to you" not in page
        assert "volunteering this year: £4.20, of which £4.20 reimbursed, £0.00 owed" in page

    def test_ThisMonth_WhenSomeoneElsePaidTheSameAmount_ItIsStillOwed(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, transfer_from=OTHER) as (base, _, coffee, _income):
            declare(base, coffee)
            page = month_shown(base)

        assert f"{CHARITY} owes £4.20" in page

    def test_ThisMonth_WhenTheOrganisationIsNewlyNamedAndNoPaymentIsGatheredUnderIt_StaysOwed(
        self, tmp_path, monkeypatch
    ):
        with served(
            tmp_path, monkeypatch, transfer_from=CHARITY, known_entity=False
        ) as (base, _, coffee, _income):
            response = declare(base, coffee)
            page = month_shown(base)

        assert "The entity is new" in text_of(response.text)
        assert f"{CHARITY} owes £4.20" in page


class TestClosingByHand:
    def close(self, base: str, **fields: str) -> httpx.Response:
        data = {"receivable": "1", "how": "written-off", "reason": "forgiven by the committee"}
        data.update(fields)
        return httpx.post(f"{base}/receivable-close", data=data, timeout=60)

    def test_Close_WhenWrittenOffWithAReason_IsNoLongerOwedAndTheLabelCountsItWrittenOff(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db, coffee, _income):
            declare(base, coffee)
            response = self.close(base)
            page = month_shown(base)
            with Store(db) as store:
                (kept,) = store.receivables()

        assert "Closed; the reason is kept." in text_of(response.text)
        assert kept.closed_reason == "forgiven by the committee"
        assert "Owed to you" not in page
        assert "£4.20 written off" in page

    def test_Close_WhenTheReasonIsMissing_IsRefusedAndStillOwed(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            response = self.close(base, reason=" ")
            page = month_shown(base)

        assert response.status_code == 400
        assert "Say why it is closed" in text_of(response.text)
        assert f"{CHARITY} owes £4.20" in page

    def test_Close_WhenPressedTwice_TheSecondIsRefused(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            self.close(base)
            second = self.close(base, how="received-elsewhere")

        assert second.status_code == 400

    def test_Close_WhenThePressWasMadeOnTheMaskedPage_IsAnsweredMasked(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            response = self.close(base, how="received-elsewhere")

        assert CHARITY not in text_of(response.text)
        assert "4.20" not in text_of(response.text)

    def test_ThisMonth_WhenMasked_OffersBothWaysToCloseWithoutAnyValue(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee)
            html = httpx.get(f"{base}/this-month", timeout=60).text

        assert 'action="/receivable-close"' in html
        assert 'value="written-off"' in html and 'value="received-elsewhere"' in html


class TestRefusals:
    def test_Press_WhenTheRowIsMoneyIn_IsRefusedAndNothingIsKept(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db, _coffee, income):
            response = declare(base, income)
            with Store(db) as store:
                assert store.receivables() == []

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "bad",
        [
            {"anchor": "not-a-row"},
            {"debtor": " "},
            {"amount": "9.99"},
            {"amount": "0"},
            {"amount": "four pounds"},
            {"expected": "next week"},
            {"expected": "2026-09-01"},
        ],
    )
    def test_Press_WhenTheFormIsNotSound_IsRefusedAndNothingIsKept(
        self, tmp_path, monkeypatch, bad
    ):
        with served(tmp_path, monkeypatch) as (base, db, coffee, _income):
            fields = {k: v for k, v in bad.items() if k != "anchor"}
            response = declare(base, bad.get("anchor", coffee), **fields)
            with Store(db) as store:
                assert store.receivables() == []

        assert response.status_code == 400

    def test_Press_WhenAPartOfThePaymentIsOwed_OwesOnlyThatPart(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _, coffee, _income):
            declare(base, coffee, amount="2.10")
            page = month_shown(base)

        assert f"{CHARITY} owes £2.10" in page

    def test_Press_WhenPressedTwiceOnOneRow_TheSecondIsRefused(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db, coffee, _income):
            declare(base, coffee)
            second = declare(base, coffee)
            with Store(db) as store:
                assert len(store.receivables()) == 1

        assert second.status_code == 400
