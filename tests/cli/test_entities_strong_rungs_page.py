"""The Entities page over a store whose bank states each party's account and id.

KNOWN ANSWERS, decided before the first run. The people, sort codes, account numbers, and uids are
invented. The invented store's one account holds:

  Alex Rowan   twelve transfers out, twelve different references, stated "Alex Rowan" on seven
               and "A Rowan" on five, one account: ONE name by account
  Sam Okafor   three transfers to one account and two to another, both stated "Sam Okafor": TWO
               names by account, both shown "sam okafor"
  Oakmere      four card payments stating a merchant uid and "Oakmere Coffee": ONE name by uid

So four names are held; three are named by an account and one by the bank's own id, and the page
offers nothing to merge. Whether masked or shown, the page never prints an account number or a uid
as a name: it prints the readable name the same rows state.
"""

from __future__ import annotations

import re

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NUMBERS = ("55667788", "11223344", "99887766")
UIDS = ("uid-alex-7c1", "uid-sam-3d9", "uid-oak-5e2")


def item(uid: str, day: str, minor: int, **fields: object) -> dict[str, object]:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": f"{day}T09:15:00.000Z",
        "status": "SETTLED",
        **fields,
    }


def transfer(uid: str, day: str, reference: str, name: str, sort: str, number: str, party: str):
    return item(
        uid, day, 5000 + len(uid), source="FASTER_PAYMENTS_OUT", counterPartyType="PAYEE",
        counterPartyUid=party, counterPartyName=name, reference=reference,
        counterPartySubEntityIdentifier=sort, counterPartySubEntitySubIdentifier=number,
    )


def feed_items() -> list[dict[str, object]]:
    found = [
        transfer(
            f"a{i}", f"2026-{i + 1:02}-05", f"{month} RENT",
            "Alex Rowan" if i < 7 else "A Rowan", "201234", NUMBERS[0], UIDS[0],
        )
        for i, month in enumerate(MONTHS)
    ]
    found += [
        transfer(f"s{i}", f"2026-0{i + 1}-12", f"gift {i}", "Sam Okafor", "304050", NUMBERS[1],
                 UIDS[1])
        for i in range(3)
    ]
    found += [
        transfer(f"t{i}", f"2026-0{i + 5}-12", f"loan {i}", "Sam Okafor", "304050", NUMBERS[2],
                 "uid-sam-8b4")
        for i in range(2)
    ]
    found += [
        item(
            f"c{i}", f"2026-0{i + 1}-20", 350 + i, source="MASTER_CARD",
            counterPartyType="MERCHANT", counterPartyUid=UIDS[2],
            counterPartyName="Oakmere Coffee", reference=f"OAKMERE COFFEE {i}",
        )
        for i in range(4)
    ]
    return found


@pytest.fixture
def served(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    rows = [starling.to_transaction(i, account_id=ACCOUNT) for i in feed_items()]
    with Store(db) as store:
        reconcile_batch(store, [r for r in rows if r is not None], digest="feed")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def summary_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "ent-summary" in n.classes]
    return line.text()


def visible_text(page: str) -> str:
    return parse(page).text()


class TestTheMaskedPage:
    def test_Summary_CountsNamesByEveryKindAndOffersNothingToMerge(self, served):
        page = httpx.get(f"{served}/entities", timeout=60).text

        assert summary_of(page) == (
            "4 payee names across every account; 0 names gathered into 0 entities. "
            "Names: 3 from the other party's account number; "
            "1 from the bank's own id for the party. "
            "No group of names looks like one payee."
        )

    def test_Page_NeverPrintsAnAccountNumberOrAUid(self, served):
        page = httpx.get(f"{served}/entities", timeout=60).text

        for secret in (*NUMBERS, *UIDS, "201234", "304050", "uid-sam-8b4"):
            assert secret not in page
        assert "alex rowan" not in page.casefold()


class TestTheShownPage:
    def test_Page_PrintsReadableNamesNeverTheAccountNumbersOrUids(self, served):
        page = httpx.post(f"{served}/entities", timeout=60).text

        for secret in (*NUMBERS, "201234", "304050"):
            assert secret not in page
        text = visible_text(page)
        for uid in (*UIDS, "uid-sam-8b4"):
            assert uid not in text
        assert not re.search(r"\bacct-[0-9a-f]{24}\b", text)

    def test_Page_ListsEachPartyOnceUnderItsReadableName(self, served):
        page = httpx.post(f"{served}/entities", timeout=60).text
        labels = [
            n.text().strip()
            for n in elements(parse(page), "span")
            if "txt" in n.classes and n.text().strip()
        ]

        assert labels.count("alex rowan") == 1
        assert labels.count("a rowan") == 0
        assert labels.count("sam okafor") == 2
        assert labels.count("oakmere coffee") == 1

    def test_Page_SaysHowEachPartyWasNamedFromTheRowsItCovers(self, served):
        page = httpx.post(f"{served}/entities", timeout=60).text
        values = [
            i.attrs["value"]
            for i in elements(parse(page), "input")
            if i.attrs.get("name") == "shape"
        ]
        # A name's derivation is listed under an entity, so gather every party under one.
        text = visible_text(
            httpx.post(
                f"{served}/entities-merge", data={"name": "Everyone", "shape": values}, timeout=60
            ).text
        )

        assert "From the other party's account number:" in text
        assert "From the bank's own id for the party:" in text

    def test_Merge_WhenAPartyIsGatheredUnderAnEntity_KeepsItsReadableNameOnThePage(
        self, served
    ):
        page = httpx.post(f"{served}/entities", timeout=60).text
        values = [
            i.attrs["value"]
            for i in elements(parse(page), "input")
            if i.attrs.get("name") == "shape"
        ]
        assert len(values) == 4
        alex = next(v for v in values if v.startswith("acct-"))

        response = httpx.post(
            f"{served}/entities-merge", data={"name": "Landlord", "shape": [alex]}, timeout=60
        )

        assert "Merged 1 name into Landlord" in response.text
        after = visible_text(response.text)
        assert "Landlord" in after and "alex rowan" in after
        assert "55667788" not in response.text and alex not in visible_text(response.text)
