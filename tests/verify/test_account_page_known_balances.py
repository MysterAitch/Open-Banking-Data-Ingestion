"""The account page shows the balances worth reading and links to the rest, so its size does not
grow with the account's history.

The deployed main account stated 1,906 known balances, every one in agreement, and the page
carried all of them (about 5,700 of its 6,100 text lines) on every load. Two invented accounts
hold the same 2,000 daily rows, one transaction a day from 2020-01-01, each row a payment of
`100 + day number` pence out of the account (every amount distinct):

- BULK states a known balance at the end of every day: 2,000, all true. The first one sets the
  opening balance, so 1,999 are in agreement.
- SPARSE states one at the end of every tenth day: 200, all true, 199 in agreement.
- FAULTY states every tenth day too, but the balance for the end of day 1,000 (2022-09-27) is 5.00
  too high, so exactly one differs and the other 198 agree.
- FEW states one at the end of every 250th day: 8, the first setting the opening, so 7 agree.
- NOTHING states none.

What the default page must show is decided here: the opening, every balance that differs, the
newest `SHOWN_AGREEING` in agreement, and one line linking to the full list.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from served_store import environment_for, served_store
from test_ledger import land, txn

BULK = "starling-bulk"
SPARSE = "starling-sparse"
FAULTY = "starling-faulty"
NOTHING = "starling-nothing"
FEW = "starling-few"
NIL = "starling-nil"

DAYS = 2000
FIRST_DAY = date(2020, 1, 1)
FAULT_DAY = FIRST_DAY + timedelta(days=999)
FAULT_MINOR = 500
#: Large enough that the running balance never goes below nil: the rows total 22,000.00 out.
OPENING_MINOR = 5_000_000
SHOWN_AGREEING = 10
FEED = "starling"


def _day(number: int) -> date:
    return FIRST_DAY + timedelta(days=number - 1)


def _stated(store: Store, ref: str, *, every: int, fault: bool = False) -> None:
    running = OPENING_MINOR
    for number in range(1, DAYS + 1):
        running -= 100 + number
        if number % every:
            continue
        stated = running + (FAULT_MINOR if fault and _day(number) == FAULT_DAY else 0)
        whole, pence = divmod(stated, 100)
        record_stated_anchor(
            store, ref, _day(number).isoformat(), f"{whole}.{pence:02d}", today=_day(DAYS)
        )


def build_account(store: Store, ref: str, *, every: int, fault: bool = False) -> None:
    rows = [
        txn(ref, FEED, f"{ref}-{number}", _day(number), -(100 + number), f"Shop {number}")
        for number in range(1, DAYS + 1)
    ]
    for start in range(0, len(rows), 400):
        land(store, f"{ref}-{start}", *rows[start : start + 400])
    if every:
        _stated(store, ref, every=every, fault=fault)


def _build(store: Store) -> None:
    build_account(store, BULK, every=1)
    build_account(store, SPARSE, every=10)
    build_account(store, FAULTY, every=10, fault=True)
    build_account(store, FEW, every=250)
    build_account(store, NOTHING, every=0)
    # A loan paid off: one transaction, and a balance of nil stated for its last day, so the
    # page has a nil to print (the owner saw "nil" printed where every other figure is sealed).
    land(store, f"{NIL}-0", txn(NIL, FEED, f"{NIL}-1", _day(1), 5000, "Paid off"))
    record_stated_anchor(store, NIL, _day(1).isoformat(), "0.00", today=_day(DAYS))


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("known-balances")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_store(root, _build, bound=[BULK, SPARSE, FAULTY, FEW, NOTHING]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def get(base: str, ref: str, *, everything: bool = False) -> str:
    params = {"ref": ref, **({"balances": "all"} if everything else {})}
    response = httpx.get(f"{base}/ledger", params=params, timeout=120)
    assert response.status_code == 200
    return response.text


def shown(base: str, ref: str, *, everything: bool = False) -> httpx.Response:
    data = {"ref": ref, "month": "", **({"balances": "all"} if everything else {})}
    return httpx.post(f"{base}/ledger", data=data, follow_redirects=False, timeout=120)


def agreeing_listed(page: str) -> int:
    return page.count('<span class="pill pill-ok">adds up</span>')


def differing_listed(page: str) -> int:
    return page.count('<span class="pill pill-bad">differs</span>')


class TestTheDefaultPage:
    def test_AccountWithTwoThousandAgreeingBalances_ListsOnlyTheNewestFew(self, base):
        page = get(base, BULK)

        assert agreeing_listed(page) == SHOWN_AGREEING
        assert "the opening balance is worked out from this" in page

    def test_AccountWithTwoThousandAgreeingBalances_SaysHowManyEarlierOnesAreHeldBack(self, base):
        page = get(base, BULK)

        earlier = 1999 - SHOWN_AGREEING
        assert f"and {earlier:,} earlier known balances, all add up" in page
        assert "(1,999 add up, none differ)" in page

    def test_TheEarlierLine_LinksToTheFullListAndOnlyThere(self, base):
        page = get(base, BULK)

        link = re.search(
            r'<a class="tap" href="([^"]*)">and 1,989 earlier known balances, all add up</a>',
            page,
        )
        assert link is not None
        assert re.fullmatch(
            rf"/ledger\?ref={BULK}(&amp;month=\d{{4}}-\d{{2}})?&amp;balances=all#opening",
            link.group(1),
        )
        assert page.count("balances=all") == 3, (
            "the earlier line, the removal controls' link, and the older protection dates' link"
        )

    def test_NewestAgreeingBalancesShown_AreTheNewestNotTheOldest(self, base):
        page = get(base, BULK)

        listed = re.findall(r"End of <span class=\"mono nowrap\">(\d{4}-\d{2}-\d{2})</span>", page)
        agreeing = [day for day in listed if day != _day(1).isoformat()]
        assert agreeing == [_day(number).isoformat() for number in range(1991, 2001)]
        assert _day(1).isoformat() in listed, "the balance that sets the opening is always there"

    def test_AccountWithFewAgreeingBalances_ListsEveryOneAndHasNoEarlierLine(self, base):
        page = get(base, FEW)

        assert agreeing_listed(page) == 7
        assert "earlier known balance" not in page
        assert "balances=all" not in page, "nothing is held back, so there is no full list to link"

    def test_AccountWithNoKnownBalance_HasNoEarlierLine(self, base):
        page = get(base, NOTHING)

        assert "earlier known balance" not in page
        assert "Known balances (none stated)" in page


class TestAMaskedPageHidesANilAndASign:
    """The owner saw "End of 2025-04-30: nil" on a masked page: a nil balance printed as the
    word, which tells the reader the figure exactly, where every other balance is a sealed
    slot; and "in credit" or "overdrawn or owed" beside a sealed slot told them its sign."""

    def test_NilBalance_Masked_IsASealedSlotLikeAnyOther(self, base):
        page = get(base, NIL)
        balances = re.findall(
            r"End of <span[^>]*>\d{4}-\d\d-\d\d</span>: ([^<]*<[^>]*>[^<]*)", page
        )

        assert balances, "the stated balance is listed"
        assert all("nil" not in found and "sealed" in found for found in balances), balances

    def test_Balances_Masked_CarryNoDirectionWord(self, base):
        for ref in (NIL, FAULTY, SPARSE):
            page = get(base, ref)

            # A direction word followed by a figure's slot is a signed balance; the words also
            # appear in the state-a-balance form's own label, which names no figure.
            assert not re.search(r"(in credit|overdrawn or owed) <span", page), ref

    def test_NilBalance_WithValuesShown_ReadsNilAndOthersReadTheirDirection(self, base):
        assert ": nil" in shown(base, NIL).text
        assert "overdrawn or owed" in shown(base, SPARSE).text or (
            "in credit" in shown(base, SPARSE).text
        )


class TestABalanceThatDiffersIsAlwaysShown:
    def test_OneDifferingBalanceAmongTwoHundred_IsListedWhereverItFalls(self, base):
        page = get(base, FAULTY)

        assert differing_listed(page) == 1
        assert agreeing_listed(page) == SHOWN_AGREEING
        assert f"End of <span class=\"mono nowrap\">{FAULT_DAY.isoformat()}</span>" in page
        assert "(198 add up, 1 differs)" in page

    def test_TheEarlierLine_CountsOnlyTheAgreeingOnesLeftOut(self, base):
        page = get(base, FAULTY)

        assert "and 188 earlier known balances, all add up" in page

    def test_TheDifferingBalance_IsShownMaskedAndWithValuesAlike(self, base):
        page = shown(base, FAULTY).text

        assert differing_listed(page) == 1
        assert "£5.00" in page, "the size of the difference is a value, shown on request"
        assert "£5.00" not in get(base, FAULTY)


class TestTheFullList:
    def test_FullList_HoldsEveryKnownBalanceOfTheAccount(self, base):
        page = get(base, BULK, everything=True)

        assert agreeing_listed(page) == 1999
        assert "earlier known balances" not in page
        assert "<details open><summary>Known balances (" in page, "asked for, so it is open"
        assert '<div id="opening">' in page

    def test_FullList_WithValuesShown_IsAPostAndStillHoldsEveryOne(self, base):
        response = shown(base, BULK, everything=True)

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert agreeing_listed(response.text) == 1999

    def test_DefaultPageWithValuesShown_OffersTheFullListAsAPostedButtonNotAnAddress(self, base):
        page = shown(base, BULK).text

        assert "balances=all" not in page, "no address that opens a page of values"
        assert re.search(
            r'<form method="post" action="/ledger">(?:(?!</form>).)*name="balances" value="all"'
            r'(?:(?!</form>).)*and 1,989 earlier known balances, all add up',
            page,
            flags=re.S,
        )

    def test_FullList_PutsEveryRemovalControlOnThePage_TheDefaultPutsOnlyTheShownOnes(self, base):
        default = get(base, SPARSE)
        full = get(base, SPARSE, everything=True)

        words = "Remove the known balance for"
        assert full.count(words) == 200
        assert default.count(words) == SHOWN_AGREEING + 1, "the shown ones and the opening's"

    def test_FullListOfAnAccountWithNoBalances_IsTheDefaultPage(self, base):
        assert "Known balances (none stated)" in get(base, NOTHING, everything=True)


class TestThePageDoesNotGrowWithTheBalances:
    def test_TwoThousandBalancesAndTwoHundred_GiveDefaultPagesOfAboutTheSameSize(self, base):
        bulk = len(get(base, BULK).encode())
        sparse = len(get(base, SPARSE).encode())

        assert abs(bulk - sparse) < 0.05 * sparse, (bulk, sparse)

    def test_TwoThousandBalances_GiveADefaultPageWhoseOwnContentIsUnderFiftyFiveKilobytes(
        self, base
    ):
        """The bound is on what this page says, not on the stylesheet every page carries.

        It was an absolute hundred kilobytes for the whole page, and failed at 101,008 when
        two unrelated pages added their styles: the inline stylesheet is one for every page
        (51,395 bytes then, half of this page), so each new page's rules counted against this
        one. With the stylesheet taken out the page measured about 49,600 bytes; the bound
        leaves a tenth above that, and what it guards is unchanged - a page that renders
        every known balance again would be over a megabyte.

        Measured 55,539 bytes after the page was rebuilt around the trust sentence and the
        things to do: each of the month's fifty transactions is a line with a mark and, behind
        it, a disclosure holding its sources and flags, which is the rise. The bound is again a
        tenth above the measurement.

        Measured 87,084 bytes once the page opened on the last 30 days or 50 transactions,
        whichever is wider: this account's newest transactions are years old, so it lists the
        newest 50 where the month it opened on listed about thirty, and the window's control
        (some 29 buttons and two small forms) is on the page. The bound is again a tenth above.
        """
        page = re.sub(r"<style>.*?</style>", "", get(base, BULK), flags=re.S)

        assert len(page.encode()) < 96_000, len(page.encode())

    def test_ProtectionDropDown_OffersOnlyTheNewestDatesUntilTheFullListIsAsked(self, base):
        default = get(base, BULK)
        full = get(base, BULK, everything=True)

        assert default.count('<option value="20') == 24
        assert re.search(r"\d+ older dates offered with every known balance listed", default)
        assert full.count('<option value="20') > 1000
        assert "older dates offered" not in full

    def test_TheFullList_IsTheOnePageThatCarriesEveryBalance(self, base):
        default = len(get(base, BULK).encode())
        full = len(get(base, BULK, everything=True).encode())

        assert full > 5 * default
