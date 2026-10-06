"""The account's edit page lists the same windows, with a row to add one where it is seen, through
the existing declare-and-save path; a window saved there appears in the account page's fold.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from account_about_world import (
    BARE,
    FAR,
    INFERRED_CHANGED,
    INFERRED_KEPT,
    SAME,
    TERMS,
    ahead,
    serve,
    set_environment,
    shown,
    words,
)
from page_dom import elements, parse


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-about")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with serve(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(root, monkeypatch)


def edit(base: str, ref: str) -> str:
    response = httpx.get(f"{base}/edit-account", params={"ref": ref}, timeout=60)
    assert response.status_code == 200
    return response.text


def save(base: str, ref: str, **fields: str) -> httpx.Response:
    """The edit form as a browser posts it: an account's own fields, then whatever the test sets,
    which for most is the row that adds a window. The dates are the ones the account holds
    (`DATES`), so a save that is not about dates leaves them as they were."""
    form = {
        "original_ref": ref,
        "ref": ref,
        "label": ref.title(),
        "kind": "",
        "parent": "",
        "opened": "",
        "closed": "",
        **DATES.get(ref, {}),
        **fields,
    }
    return httpx.post(f"{base}/save-account", data=form, timeout=60)


DATES = {
    TERMS: {"kind": "credit-card", "opened": "2020-03-01", "closed": "2031-01-01"},
    INFERRED_KEPT: {"opened": "2020-01-01", "closed": "2031-06-01"},
    INFERRED_CHANGED: {"opened": "2020-01-01", "closed": "2031-06-01"},
}


def test_Edit_ListsTheWindowsAlreadyDeclaredWithTheirFiguresSealed(base):
    page = edit(base, TERMS)

    windows = [n.text() for n in elements(parse(page), "li") if "window" in n.classes]
    assert len(windows) == 3
    assert "22.9" not in page and "4,321" not in page


def test_Edit_OffersAnAddAWindowRowOfTermKindFigureFromAndTo(base):
    page = edit(base, BARE)

    for name in ("window_term", "window_kind", "window_figure", "window_from", "window_to"):
        assert f'name="{name}"' in page
    assert "Add a window" in page


def test_Edit_ARateWindowAddedThere_AppearsInTheFold(base):
    response = save(
        base,
        BARE,
        window_term="rate",
        window_kind="purchases",
        window_figure="12.5",
        window_from=ahead(-10).isoformat(),
        window_to=ahead(300).isoformat(),
    )

    assert response.status_code == 200
    said = words(shown(base, BARE))
    assert "Purchases rate 12.5%" in said and "current" in said
    assert "Nothing beyond its name is declared" not in said


def test_Edit_ALimitWindowAddedThere_AppearsInTheFoldAndKeepsTheTermsAlreadyDeclared(base):
    response = save(
        base,
        TERMS,
        window_term="limit",
        window_kind="overdraft",
        window_figure="250.00",
        window_from=ahead(-5).isoformat(),
        window_to="",
    )

    assert response.status_code == 200
    said = words(shown(base, TERMS))
    assert "Overdraft limit £250.00" in said
    assert "22.9%" in said and "£4321.00" in said


def test_Edit_AFigureThatIsNotANumber_IsRefusedAndNothingIsSaved(base):
    before = words(shown(base, SAME))

    response = save(base, SAME, window_term="rate", window_kind="purchases", window_figure="lots")

    assert response.status_code == 400
    assert words(shown(base, SAME)) == before


def test_Edit_AWindowThatEndsBeforeItBegins_IsRefusedAndNothingIsSaved(base):
    before = words(shown(base, SAME))

    response = save(
        base,
        SAME,
        window_term="rate",
        window_kind="purchases",
        window_figure="3",
        window_from=ahead(10).isoformat(),
        window_to=ahead(5).isoformat(),
    )

    assert response.status_code == 400
    assert words(shown(base, SAME)) == before


def test_Edit_ARowLeftEmpty_ChangesNoWindow(base):
    before = words(shown(base, FAR))

    assert save(base, FAR).status_code == 200

    assert words(shown(base, FAR)) == before


def test_Edit_AFigureWithNoTerm_IsRefusedRatherThanGuessed(base):
    assert save(base, FAR, window_figure="4").status_code == 400


def test_Edit_ARateBelowNilOrNotFinite_IsRefused(base):
    for figure in ("-1", "nan", "inf"):
        response = save(base, FAR, window_term="rate", window_figure=figure)

        assert response.status_code == 400, figure


def test_Edit_ALimitWithPenceBeyondPennies_IsRefused(base):
    response = save(base, FAR, window_term="limit", window_figure="10.005")

    assert response.status_code == 400


def test_Edit_RenamingAnAccountWhoseDatesWereInferred_KeepsSayingTheyWereInferred(base):
    response = save(base, INFERRED_KEPT, label="A new name")

    assert response.status_code == 200
    said = words(shown(base, INFERRED_KEPT))
    assert "Opened 2020-01-01 - inferred from the first and last movement" in said
    assert "Closed 2031-06-01 - inferred from the first and last movement" in said


def test_Edit_MovingADateOfAnAccountWhoseDatesWereInferred_MakesBothStated(base):
    response = save(base, INFERRED_CHANGED, closed="2031-07-01")

    assert response.status_code == 200
    said = words(shown(base, INFERRED_CHANGED))
    assert "Opened 2020-01-01 - stated" in said and "Closed 2031-07-01 - stated" in said


def test_Declare_AnAccountWithAWindowInTheSameForm_HasTheWindowFromTheStart(base):
    response = httpx.post(
        f"{base}/save-account",
        data={
            "ref": "new-card",
            "label": "New Card",
            "window_term": "limit",
            "window_kind": "credit",
            "window_figure": "1500.00",
            "window_from": ahead(-1).isoformat(),
        },
        timeout=60,
    )

    assert response.status_code == 200
    assert "Credit limit £1500.00" in words(shown(base, "new-card"))
