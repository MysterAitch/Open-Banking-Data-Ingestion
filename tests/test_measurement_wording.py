"""The Identity health measurement sentences obey the wording rules every page is held to.

The page-walk tests render the invented store, which holds no export rows and no declined item,
so they never reach these sentences. The same patterns are applied here to the sentences
rendered over invented corpora that do, and to the page's own words: "transaction" for a held
thing, "row" for a line a source lists, and never "(s)".

KNOWN ANSWER: no internal word, no shouted word beyond a status the bank states, no ISO instant,
no "(s)", and no value or payee, in the text of either measurement.
"""

from __future__ import annotations

import json
import re

from consecutive_days_corpus import consecutive_payments
from late_settlement_corpus import ORDERS, household
from obdi import rebuild
from obdi.account_names import name_text
from obdi.exact_rule_measure import exact_rule_report
from obdi.page_words import INTERNAL_ON_PAGES
from obdi.providers import starling
from obdi.store import Store
from round_up_corpus import card_payment
from test_absorbed_rows import arrive
from test_cash_transfers import world
from test_cash_withdrawal_measure import measured
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_page_wording_emphasis import ACRONYMS, SHOUTING
from test_page_wording_times import CLOCK_WITH_ZONE_SUFFIX, ISO_INSTANT
from test_space_attribution import MAP


def settlement_text(tmp_path) -> str:
    order = ("feed", "aggregator", "export")
    store = household(tmp_path, order, consecutive_payments(7), linked=True)
    try:
        return exact_rule_report(store, MAP).describe()
    finally:
        store.close()


def declined_text(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(rebuild, "void_declined_items", lambda store: None, raising=False)
    with Store(tmp_path / "declined.sqlite3") as store:
        land_evidence(store)
        for number, status in enumerate(("SETTLED", "DECLINED")):
            items = [card_payment(f"f-{n}", f"Shop {n}", 100 + n, n, status=status) for n in (3, 4)]
            arrive(
                store,
                starling.artefact_for(
                    json.dumps({"feedItems": items}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-0{number + 2}T00:00:00Z",
                ),
            )
        return exact_rule_report(store, MAP).describe()


def offences(text: str) -> list[str]:
    found = [m.group(0) for m in INTERNAL_ON_PAGES.finditer(text)]
    found += [w for w in SHOUTING.findall(text) if w not in ACRONYMS]
    found += ISO_INSTANT.findall(text) + CLOCK_WITH_ZONE_SUFFIX.findall(text)
    found += re.findall(r"\(s\)", text)
    return found


class TestTheMeasurementReadsLikeThePage:
    def test_Settlement_WhenRenderedOverTheWeek_UsesNoRetiredWordOrShout(self, tmp_path):
        text = settlement_text(tmp_path)

        assert "settlement day" in text
        assert offences(text) == []

    def test_NoRowStatus_WhenRenderedOverADeclinedItem_UsesNoRetiredWordOrShout(
        self, tmp_path, monkeypatch
    ):
        text = declined_text(tmp_path, monkeypatch)

        assert "DECLINED: 2, dated" in text
        assert offences(text) == []

    def test_CashWithdrawals_WhenRenderedOverTheHousehold_UseNoRetiredWordOrShout(self, tmp_path):
        text = measured(tmp_path, ORDERS[0]).describe()

        assert "cash withdrawals by what a source states" in text
        assert offences(text) == []

    def test_StatementOpenings_WhenRenderedOverTheHousehold_UseNoRetiredWordOrShout(
        self, tmp_path
    ):
        from obdi.statement_opening_measure import statement_opening_report
        from test_statement_opening_measure import NO_SPACES, WITH_A_SPACE, build

        with Store(tmp_path / "openings.sqlite3") as store:
            build(store, tmp_path)
            text = "\n".join(
                [
                    *statement_opening_report(store, NO_SPACES).sentences(),
                    *statement_opening_report(store, WITH_A_SPACE).sentences(),
                ]
            )

        assert "opening balance" in text and "In agreement from" in text
        assert offences(text) == []
        assert not re.search(r"anchor|segment|frontier", text, re.I)

    def test_CashTransfers_WhenTheLegsAreMadeAndTheAccountIsNamedCash_ReadAsThePageShowsThem(
        self, tmp_path
    ):
        store = world(tmp_path, ORDERS[0], rebuild=True)
        try:
            text = exact_rule_report(store, MAP).describe()
        finally:
            store.close()

        shown = name_text(text, {"cash": "Cash"})

        assert "5 transfers made by the rule are held: 4 withdrawals and 1 deposit" in shown
        assert offences(shown) == []
        assert shown.count("Cash (cash)") == 1
        assert "the cash account" in shown

    def test_CashWithdrawals_WhenOneOfEachCountIsOne_ReadAsSingularSentences(self, tmp_path):
        with Store(tmp_path / "one.sqlite3") as store:
            land_evidence(store)
            arrive(
                store,
                starling.artefact_for(
                    json.dumps(
                        {"feedItems": [card_payment("f-1", "Shop", 100, 3, sourceSubType="ATM")]}
                    ).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
                ),
            )
            text = exact_rule_report(store, MAP).describe()

        assert "1 stored transaction that is not history is a cash withdrawal" in text
        assert "(s)" not in text
        assert offences(text) == []
