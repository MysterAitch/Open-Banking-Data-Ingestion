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
    DATED,
    ENDING,
    FAR,
    INFERRED_CHANGED,
    INFERRED_KEPT,
    OLD_DIFFERS,
    QUIET,
    SAME,
    TERMS,
    ahead,
    serve,
    set_environment,
    shown,
    windows_of,
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


def browser_form(page: str) -> dict[str, str]:
    """The fields a browser would post for the edit page's form as served: every input as it
    stands, a select at its chosen option (or its first), a tick box only where it carries
    `checked`."""
    form = next(
        f for f in elements(parse(page), "form") if f.attrs.get("action") == "/save-account"
    )
    sent: dict[str, str] = {}
    for node in form.descendants():
        name = node.attrs.get("name")
        if not name:
            continue
        if node.tag == "input":
            if node.attrs.get("type") == "checkbox" and "checked" not in node.attrs:
                continue
            sent[name] = node.attrs.get("value", "")
        elif node.tag == "select":
            options = [o for o in node.descendants() if o.tag == "option"]
            chosen = next((o for o in options if "selected" in o.attrs), options[0])
            sent[name] = chosen.attrs.get("value", "")
    return sent


def window_lines(base: str, ref: str) -> list[str]:
    return windows_of(shown(base, ref))


def test_Edit_EachDeclaredWindowCarriesItsOwnFieldsAndARemoveTick(base):
    page = edit(base, TERMS)
    sent = browser_form(page)
    count = len(window_lines(base, TERMS))

    assert count >= 3 and sent["window_count"] == str(count)
    for index in range(count):
        for field in ("kind", "figure", "from", "to"):
            assert f"w{index}_{field}" in sent
        assert f'name="w{index}_remove"' in page


def test_Edit_AWindowsFigureIsNeverServedBackIntoItsField(base):
    sent = browser_form(edit(base, TERMS))

    assert all(sent[f"w{index}_figure"] == "" for index in range(int(sent["window_count"])))


def test_Edit_ChangingAWindowsEndDate_ShowsTheNewEndInTheFold(base):
    sent = browser_form(edit(base, ENDING))
    assert sent["w0_to"] == ahead(20).isoformat()
    sent["w0_to"] = ahead(200).isoformat()

    response = httpx.post(f"{base}/save-account", data=sent, timeout=60)

    assert response.status_code == 200
    (line,) = window_lines(base, ENDING)
    assert ahead(200).isoformat() in line
    assert ahead(20).isoformat() not in line
    assert "5.5%" in line


def test_Edit_TypingANewFigureForAWindow_ReplacesItAndALeftBlankFigureKeepsIt(base):
    sent = browser_form(edit(base, FAR))
    sent["w0_figure"] = "7.25"
    assert httpx.post(f"{base}/save-account", data=sent, timeout=60).status_code == 200
    assert "7.25%" in window_lines(base, FAR)[0]

    again = browser_form(edit(base, FAR))
    assert httpx.post(f"{base}/save-account", data=again, timeout=60).status_code == 200
    assert "7.25%" in window_lines(base, FAR)[0]


def test_Edit_TickingRemoveOnAWindow_LeavesTheOthersAndDropsThatOne(base):
    before = window_lines(base, TERMS)
    sent = browser_form(edit(base, TERMS))
    promotional = next(i for i, line in enumerate(before) if line.startswith("Promotional"))
    sent[f"w{promotional}_remove"] = "yes"

    response = httpx.post(f"{base}/save-account", data=sent, timeout=60)

    assert response.status_code == 200
    after = window_lines(base, TERMS)
    assert len(after) == len(before) - 1
    assert not any(line.startswith("Promotional") for line in after)
    assert all(line in before for line in after)


def test_Edit_RemovingTheOnlyWindow_LeavesTheAccountDeclaringNone(base):
    sent = browser_form(edit(base, QUIET))
    sent["w0_remove"] = "yes"

    assert httpx.post(f"{base}/save-account", data=sent, timeout=60).status_code == 200

    assert window_lines(base, QUIET) == []
    assert "None are declared" in edit(base, QUIET)


def test_Edit_ASaveWithNoChange_LeavesTheWindowsAndTheDatesBasisAsTheyWere(base):
    before = words(shown(base, TERMS))
    sent = browser_form(edit(base, TERMS))

    assert httpx.post(f"{base}/save-account", data=sent, timeout=60).status_code == 200

    assert words(shown(base, TERMS)) == before
    assert "Closed 2031-01-01 - inferred from the first and last movement" in before


def test_Edit_AWindowMovedToEndBeforeItBegins_IsRefusedAndNothingIsSaved(base):
    before = words(shown(base, OLD_DIFFERS))
    sent = browser_form(edit(base, OLD_DIFFERS))
    sent["w0_from"] = ahead(10).isoformat()
    sent["w0_to"] = ahead(5).isoformat()

    response = httpx.post(f"{base}/save-account", data=sent, timeout=60)

    assert response.status_code == 400
    assert words(shown(base, OLD_DIFFERS)) == before


def test_Edit_AnEditedFigureThatIsNotANumber_IsRefusedAndNothingIsSaved(base):
    before = words(shown(base, OLD_DIFFERS))
    sent = browser_form(edit(base, OLD_DIFFERS))
    sent["w0_figure"] = "lots"

    assert httpx.post(f"{base}/save-account", data=sent, timeout=60).status_code == 400
    assert words(shown(base, OLD_DIFFERS)) == before


def test_Edit_AFormServedBeforeTheWindowsChanged_IsRefusedRatherThanAppliedToTheWrongWindow(base):
    sent = browser_form(edit(base, DATED))
    sent["window_count"] = "2"
    sent["w0_remove"] = "yes"

    response = httpx.post(f"{base}/save-account", data=sent, timeout=60)

    assert response.status_code == 409


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
