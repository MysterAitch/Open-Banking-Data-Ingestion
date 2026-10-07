"""The recurring-payments page: what it says masked, and what only a request for values adds.

KNOWN ANSWERS, decided before the first run. The invented store holds one account whose
bank export lists a streaming subscription taken on the 3rd of each month from March to July
2026 at 41.37 and, in July, at 47.91 (a rise of 15.8 per cent), and one unrelated payment on
30 September. So the page finds exactly one thing: monthly, about the 3rd, five times, stopped
(August's was expected and the account has been read to September) and changed (up 15.8%).
The payee and both amounts are distinctive tokens that no other text on the page can hold.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from obdi import values_sitting
from obdi.cli import build_web_config
from obdi.ingest import import_file
from obdi.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
PAYEE = "Zephyrine Quokka Subscriptions"
USUAL, RISEN = "41.37", "47.91"


def _export(path, rows: list[tuple[str, str, str]]) -> None:
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    # The export's description is its Reference column, falling back to the counter party.
    lines += [f"{day},{payee},,CARD,-{amount},0" for day, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def served(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    _export(
        csv,
        [
            *((f"03/{m:02d}/2026", PAYEE, USUAL) for m in (3, 4, 5, 6)),
            ("03/07/2026", PAYEE, RISEN),
            ("30/09/2026", "Corner Bakery", "3.20"),
        ],
    )
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


@pytest.fixture
def served_empty(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db):
        pass
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def summary_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "recur-summary" in n.classes]
    return line.text()


class TestTheMaskedPage:
    def test_RecurringPage_WhenFetched_SaysWhatItFoundWithoutAnyPayeeOrAmount(self, served):
        response = httpx.get(f"{served}/recurring", timeout=60)

        assert response.status_code == 200
        assert summary_of(response.text) == (
            "1 recurring thing across 1 account: 1 payment, 0 transfers, 0 incomes, "
            "1 stopped, 1 changed"
        )
        text = response.text
        assert "monthly, about the 3rd" in text
        assert "5 times over 4 months" in text
        assert "up 15.8%" in text
        for hidden in ("quokka", USUAL, RISEN, "zephyrine"):
            assert hidden not in text.casefold()

    def test_RecurringPage_WhenFetched_SealsThePayeeAndTheAmount(self, served):
        text = httpx.get(f"{served}/recurring", timeout=60).text

        assert "£•••" in text
        assert "Xxxxxxxxx" in text or "xxxxxxxxx" in text

    def test_RecurringPage_WhenFetched_OffersToShowValuesByPostOnly(self, served):
        root = parse(httpx.get(f"{served}/recurring", timeout=60).text)

        forms = [f for f in elements(root, "form") if f.attrs.get("action") == "/recurring"]
        assert [f.attrs.get("method") for f in forms] == ["post"]

    def test_RecurringPage_WhenNothingRecurs_SaysSoQuietly(self, served_empty):
        text = httpx.get(f"{served_empty}/recurring", timeout=60).text

        assert "Nothing recurring was found" in text
        assert "recur-summary" not in text.split("</style>")[-1]


class TestShowingValues:
    def test_RecurringPage_WhenValuesAreRequested_ShowsPayeeAndAmountsAndIsNotKept(self, served):
        response = httpx.post(f"{served}/recurring", timeout=60)

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        assert "quokka subscriptions" in response.text.casefold()
        assert f"£{USUAL}, now £{RISEN}" in response.text

    def test_RecurringPage_WithAValuesSitting_ShowsValuesOnAPlainGet(self, served):
        cookie = values_sitting.issue(datetime.now(UTC))
        response = httpx.get(
            f"{served}/recurring", headers={"Cookie": f"{values_sitting.COOKIE}={cookie}"}
        )

        assert f"£{USUAL}, now £{RISEN}" in response.text

    def test_RecurringPage_WithAnExpiredOrForgedCookie_StaysMasked(self, served):
        response = httpx.get(
            f"{served}/recurring", headers={"Cookie": f"{values_sitting.COOKIE}=shown.1.forged"}
        )

        assert USUAL not in response.text
