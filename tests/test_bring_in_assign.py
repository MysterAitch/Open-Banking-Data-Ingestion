"""Bring in, when several statements are uploaded at once and wait for an account.

The owner uploaded ten statements and got ten separate forms, each read in alone. The scenes
below are that upload, over invented documents, and every answer was decided before the first run.

The household: `up-card` and `other-card` (both Santander, both with two earlier statements read
in, `Up-card-2025-08.pdf`, `Up-card-2025-09.pdf`, `Other-card-2025-09.pdf`), and the credit union's
"all accounts" document, whose two sections ("Regular Saver", "Holiday Pot") an earlier document
assigned to `credit-union-saver` and `credit-union-pot`.

The ten files uploaded together, with no account given:

  * three credit union documents (months 7, 8, 9), two accounts each: six rows, each pre-selected
    from the heading it prints ("Regular Saver" to the saver, "Holiday Pot" to the pot), none
    read in;
  * `Up-card-2026-08.pdf` and `Up-card-2026-09.pdf`: Santander, which both cards share, so the
    reader cannot choose; the name is an earlier file's with another year, so `up-card` is
    pre-selected for both, with its reason;
  * `Other-card-2026-09.pdf`: likewise `other-card`;
  * `Santander-2026-08.pdf` and `Santander-2026-09.pdf`: the reader cannot choose and the names
    match nothing, so no account is selected;
  * `Notes-a.pdf` and `Notes-b.pdf`: no reader reads them and no name matches, so none either.

So the one form lists thirteen rows (six sections and seven statements), nine with an account
selected, and nothing guessed is read in.

The scene that filed a year's rows under a closed loan on the real store is `TestTheHeadingLeads`:
two single-account credit union documents went to the saver and two to the loan, so their
sibling documents were split; a new one headed "Regular Saver" must be the saver, and never the
loan, and one whose heading was given both accounts must be neither.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from coverage_page_world import repeated_lines
from credit_union_documents import Move, document, pdf, section
from fetch_gaps_world import MONTHS, _pounds, ordinal
from obdi.accounts import AccountRecord, AccountRef
from obdi.cli import build_web_config
from obdi.parsers.credit_union_pdf import section_key
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from section_harness import serve_config
from served_store import served_store

D = date
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")
#: Planted in every invented document: a payee that must never reach a page as first served.
PLANTED_PAYEE = "Zzyzx Wombat Traders"
SAVER, POT = "credit-union-saver", "credit-union-pot"
SAVER_KEY, POT_KEY = section_key("Regular Saver"), section_key("Holiday Pot")


def santander(closing: date, minor: int, *, owed: int = 10000) -> bytes:
    when = date.fromordinal(closing.toordinal() - 5)
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {ordinal(closing)} {MONTHS[closing.month - 1]} {closing.year}"
        "      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
        f"Balance brought forward from previous statement          {_pounds(owed)}",
        f"{ordinal(when)} {MONTHS[when.month - 1]} {PLANTED_PAYEE}   {_pounds(minor)}",
        f"Your new balance:                                        {_pounds(owed + minor)}",
    ]
    return build_pdf(lines)


def credit_union(month: int) -> bytes:
    """Two accounts' sections for `month` of 2025, each carrying its own opening balance."""
    period = f"01/{month:02d}/2025 to 28/{month:02d}/2025"
    day = f"05/{month:02d}/2025"
    return pdf(
        document(
            section("Regular Saver", 10000 * month, [Move(day, "DD Lodgement", 500 + month)],
                    period=period),
            section("Holiday Pot", 2000 * month, [Move(day, "Internet Transfer", 100 * month)],
                    period=period),
        ),
        step=5.5,
    )


def credit_union_new_account(month: int) -> bytes:
    """A document whose second section's label no earlier document assigned."""
    period = f"01/{month:02d}/2025 to 28/{month:02d}/2025"
    day = f"05/{month:02d}/2025"
    return pdf(
        document(
            section("Regular Saver", 10000 * month, [Move(day, "DD Lodgement", 500 + month)],
                    period=period),
            section("Brand New Account", 700, [Move(day, "Internet Transfer", 100)],
                    period=period),
        ),
        step=5.5,
    )


def letter(topic: str) -> bytes:
    return build_pdf([f"An invented letter about {topic}", "It holds no statement at all."])


def part(name: str, payload: bytes) -> tuple[str, tuple[str, bytes, str]]:
    return ("file", (name, payload, "application/pdf"))


def ten_files() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        part("All-accounts-2025-07.pdf", credit_union(7)),
        part("All-accounts-2025-08.pdf", credit_union(8)),
        part("All-accounts-2025-09.pdf", credit_union(9)),
        part("Up-card-2026-08.pdf", santander(D(2026, 8, 10), 1211)),
        part("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)),
        part("Other-card-2026-09.pdf", santander(D(2026, 9, 11), 1433)),
        part("Santander-2026-08.pdf", santander(D(2026, 8, 12), 1544)),
        part("Santander-2026-09.pdf", santander(D(2026, 9, 12), 1655)),
        part("Notes-a.pdf", letter("the first topic")),
        part("Notes-b.pdf", letter("the second topic")),
    ]


def _declare(store: Store) -> None:
    for ref, label in (
        ("up-card", "Up card"),
        ("other-card", "Other card"),
        (SAVER, "Regular saver"),
        (POT, "Holiday pot"),
    ):
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))


def _read_in_earlier(base: str, root: Path) -> None:
    """Three earlier Santander statements read in, and one credit union document whose two
    sections were each given an account."""
    for name, closing, minor, account in (
        ("Up-card-2025-08.pdf", D(2025, 8, 10), 900, "up-card"),
        ("Up-card-2025-09.pdf", D(2025, 9, 10), 950, "up-card"),
        ("Other-card-2025-09.pdf", D(2025, 9, 11), 800, "other-card"),
    ):
        said = httpx.post(
            f"{base}/bring-in",
            data={"account": account},
            files=[part(name, santander(closing, minor))],
            timeout=120,
        )
        assert f"{name}: read in to" in flat(parse(said.text)), said.text[:400]
    httpx.post(f"{base}/bring-in", files=[part("All-accounts-2025-06.pdf", credit_union(6))],
               timeout=120)
    wired = build_web_config(root / "store.sqlite3")
    assert wired is not None and wired.kept_statements is not None
    assert wired.assign_statement_section is not None
    earlier = next(
        item for item in wired.kept_statements() if item["origin"] == "All-accounts-2025-06.pdf"
    )
    for key, account in ((SAVER_KEY, SAVER), (POT_KEY, POT)):
        said = wired.assign_statement_section(int(str(earlier["id"])), key, account)
        assert "assigned to" in said, said


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    with served_store(tmp_path, _declare, bound=[]) as base:
        _read_in_earlier(base, tmp_path)
        yield base, tmp_path


def flat(node: Node) -> str:
    return re.sub(r" ([.,:;])", r"\1", node.text())


def kept(root: Path) -> dict[str, dict[str, object]]:
    wired = build_web_config(root / "store.sqlite3")
    assert wired is not None and wired.kept_statements is not None
    return {str(item["origin"]): item for item in wired.kept_statements()}


def assign_form(page: Node) -> Node:
    forms = [f for f in elements(page, "form") if f.attrs.get("action") == "/statements-assign"]
    assert len(forms) == 1, f"{len(forms)} forms read statements in"
    return forms[0]


def rows_of(form: Node) -> dict[str, Node]:
    """Each row the form asks about, by how it names itself: the file name, and for one account
of a document of several, ", account " and the heading it prints."""
    found = {}
    for item in elements(form, "li"):
        if "bi-assign-file" in item.classes:
            named = next(p for p in elements(item, "p") if "bi-assign-name" in p.classes)
            found[flat(named)] = item
    return found


def whole(rows: dict[str, Node]) -> list[str]:
    """The rows that are whole statements."""
    return sorted(name for name in rows if ", account " not in name)


def sectioned(rows: dict[str, Node], month: str) -> dict[str, Node]:
    """The rows of one credit union document, by the heading they print."""
    prefix = f"All-accounts-2025-{month}.pdf, account "
    return {name[len(prefix):]: item for name, item in rows.items() if name.startswith(prefix)}


def chosen(item: Node) -> str:
    select = next(elements(item, "select"))
    picked = [o.attrs.get("value", "") for o in elements(select, "option") if "selected" in o.attrs]
    return picked[0] if picked else ""


def select_of(item: Node) -> Node:
    return next(elements(item, "select"))


def upload_ten(base: str) -> httpx.Response:
    return httpx.post(f"{base}/bring-in", files=ten_files(), timeout=300)


class TestTenStatementsUploadedAtOnce:
    def test_Upload_OfTenStatementsOfThreeKinds_AsksOnceWithOneControlForEveryRowThatNeedsAnAccount(
        self, served
    ):
        base, _ = served
        page = parse(upload_ten(base).text)

        form = assign_form(page)
        rows = rows_of(form)

        assert whole(rows) == [
            "Notes-a.pdf", "Notes-b.pdf", "Other-card-2026-09.pdf", "Santander-2026-08.pdf",
            "Santander-2026-09.pdf", "Up-card-2026-08.pdf", "Up-card-2026-09.pdf",
        ]
        for month in ("07", "08", "09"):
            assert sorted(sectioned(rows, month)) == ["Holiday Pot", "Regular Saver"]
        assert len(rows) == 13
        assert [b.text() for b in elements(form, "button")] == ["Read them all in"]
        assert not [
            f for f in elements(page, "form") if f.attrs.get("action") == "/statement-assign"
        ]

    def test_Upload_GivesEachRowItsOwnChooserNamedByItsKeptIdAndSectionToken(self, served):
        base, root = served
        rows = rows_of(assign_form(parse(upload_ten(base).text)))
        held = kept(root)

        notes = held["Notes-a.pdf"]
        assert select_of(rows["Notes-a.pdf"]).attrs["name"] == f"account-{notes['id']}"
        saver = sectioned(rows, "07")["Regular Saver"]
        token = held["All-accounts-2025-07.pdf"]["sections"][0]["token"]  # type: ignore[index]
        assert select_of(saver).attrs["name"] == (
            f"section-{held['All-accounts-2025-07.pdf']['id']}-{token}"
        )

    def test_AllAccountsDocuments_ArePreSelectedFromTheHeadingEachSectionPrints(self, served):
        base, _ = served
        rows = rows_of(assign_form(parse(upload_ten(base).text)))

        for month in ("07", "08", "09"):
            found = sectioned(rows, month)
            assert chosen(found["Regular Saver"]) == SAVER
            assert chosen(found["Holiday Pot"]) == POT
            assert "went to Regular saver" in flat(found["Regular Saver"])

    def test_Upload_PreSelectsTheAccountWhereTheNameIsAnEarlierFilesWithAnotherYear(self, served):
        base, _ = served
        rows = rows_of(assign_form(parse(upload_ten(base).text)))

        assert chosen(rows["Up-card-2026-08.pdf"]) == "up-card"
        assert chosen(rows["Up-card-2026-09.pdf"]) == "up-card"
        assert chosen(rows["Other-card-2026-09.pdf"]) == "other-card"

    def test_PreSelection_SaysBesideTheChooserWhyItWasMadeAndThatItIsAGuess(self, served):
        base, _ = served
        page = parse(upload_ten(base).text)
        rows = rows_of(assign_form(page))

        said = flat(rows["Up-card-2026-09.pdf"])
        assert "Named like the 2025 statement Up-card-2025-09.pdf, which went to Up card" in said
        assert "is a guess from earlier statements" in flat(assign_form(page))
        assert "Other card" in flat(rows["Other-card-2026-09.pdf"])

    def test_Files_NothingMatches_HaveAnEmptyChooserAndSayNothingOfAGuess(self, served):
        base, _ = served
        rows = rows_of(assign_form(parse(upload_ten(base).text)))

        for name in ("Santander-2026-08.pdf", "Santander-2026-09.pdf", "Notes-a.pdf",
                     "Notes-b.pdf"):
            assert chosen(rows[name]) == "", name
            assert "went to" not in flat(rows[name]), name

    def test_Upload_ReadsInNothingThatWasOnlyGuessed(self, served):
        base, root = served
        upload_ten(base)
        held = kept(root)

        for name in ("Up-card-2026-08.pdf", "Up-card-2026-09.pdf", "Other-card-2026-09.pdf",
                     "Santander-2026-08.pdf", "Santander-2026-09.pdf", "Notes-a.pdf",
                     "Notes-b.pdf"):
            assert held[name]["account_ref"] == "(unassigned)", name
        for month in ("07", "08", "09"):
            sections = held[f"All-accounts-2025-{month}.pdf"]["sections"]
            assert [s["account"] for s in sections] == ["", ""]  # type: ignore[union-attr]

    def test_EachChooser_OffersEveryAccountAndTheEmptyChoice(self, served):
        base, _ = served
        item = rows_of(assign_form(parse(upload_ten(base).text)))["Notes-a.pdf"]
        options = [
            (o.attrs.get("value", ""), o.text()) for o in elements(select_of(item), "option")
        ]

        assert options[0] == ("", "choose an account...")
        assert {value for value, _ in options[1:]} >= {"up-card", "other-card", SAVER, POT}

    def test_ResultsPage_StatesNoFigureNoPayeeAndNoLineMoreThanTwice(self, served):
        base, _ = served
        response = upload_ten(base)
        page = parse(response.text)
        visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)
        # A chooser lists the same options each time, and a reason is data that happens to
        # agree between files of one account, and a preview is what each document is, which two
        # documents of one issuer share; none is a sentence said once for every file.
        for node in list(elements(page, "select")):
            node.children.clear()
        for node in list(elements(page, "p")):
            if "bi-guess" in node.classes or "bi-preview" in node.classes:
                node.children.clear()

        assert MONEY_FIGURE.search(visible) is None
        assert PLANTED_PAYEE not in response.text
        assert "<script" not in response.text
        assert repeated_lines(page) == {}


class TestPressingOnceForAMixOfFiles:
    def press(self, base: str, root: Path, choices: dict[str, str]) -> httpx.Response:
        """Press the form with `choices`: a file name, or "file name|heading" for one account of
        a document of several, to the account chosen ("" leaves it)."""
        held = kept(root)
        data = {}
        for key, account in choices.items():
            name, _, heading = key.partition("|")
            if heading:
                parts = held[name]["sections"]
                token = next(
                    p["token"] for p in parts if p["label"] == heading  # type: ignore[union-attr]
                )
                data[f"section-{held[name]['id']}-{token}"] = account
            else:
                data[f"account-{held[name]['id']}"] = account
        return httpx.post(f"{base}/statements-assign", data=data, timeout=300)

    MIX: ClassVar[dict[str, str]] = {
        "Up-card-2026-08.pdf": "up-card",
        "Up-card-2026-09.pdf": "up-card",
        "Other-card-2026-09.pdf": "other-card",
        "Santander-2026-09.pdf": "other-card",
        "Santander-2026-08.pdf": "",
        "Notes-a.pdf": "",
        "Notes-b.pdf": "",
    }

    def test_Press_ReadsInEveryChosenFileToItsOwnAccountAndKeepsTheRest(self, served):
        base, root = served
        upload_ten(base)

        response = self.press(base, root, self.MIX)
        held = kept(root)
        said = flat(parse(response.text))

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert held["Up-card-2026-08.pdf"]["account_ref"] == "up-card"
        assert held["Up-card-2026-09.pdf"]["account_ref"] == "up-card"
        assert held["Other-card-2026-09.pdf"]["account_ref"] == "other-card"
        assert held["Santander-2026-09.pdf"]["account_ref"] == "other-card"
        for left in ("Santander-2026-08.pdf", "Notes-a.pdf", "Notes-b.pdf"):
            assert held[left]["account_ref"] == "(unassigned)", left
        assert "Up-card-2026-08.pdf: read in to Up card" in said
        assert "Santander-2026-09.pdf: read in to Other card" in said

    def test_Press_ListsTheFilesLeftWithoutAnAccountAgainWithNoGuess(self, served):
        base, root = served
        upload_ten(base)

        page = parse(self.press(base, root, self.MIX).text)
        rows = rows_of(assign_form(page))

        assert sorted(rows) == ["Notes-a.pdf", "Notes-b.pdf", "Santander-2026-08.pdf"]
        assert all(chosen(item) == "" for item in rows.values())
        assert "went to" not in flat(assign_form(page))

    def test_Press_WhenEveryFileIsChosen_AsksNoQuestionAfterwards(self, served):
        base, root = served
        upload_ten(base)
        everything = dict.fromkeys(self.MIX, "up-card")

        page = parse(self.press(base, root, everything).text)

        assert not [
            f for f in elements(page, "form") if f.attrs.get("action") == "/statements-assign"
        ]
        assert "7 files received" in flat(page)

    def test_Press_WithNoFileChosen_ReadsNothingInAndKeepsAllOfThem(self, served):
        base, root = served
        upload_ten(base)

        page = parse(self.press(base, root, dict.fromkeys(self.MIX, "")).text)

        assert len(rows_of(assign_form(page))) == 7
        assert {item["account_ref"] for name, item in kept(root).items() if name in self.MIX} == {
            "(unassigned)"
        }

    def test_Press_WhenOneFileIsRefused_StillReadsTheOthersAndSaysWhyOfThatOne(self, served):
        base, root = served
        upload_ten(base)

        page = parse(self.press(base, root, {**self.MIX, "Notes-a.pdf": "up-card"}).text)
        said = flat(page)

        assert "Notes-a.pdf: not read in" in said
        assert kept(root)["Up-card-2026-08.pdf"]["account_ref"] == "up-card"
        assert kept(root)["Notes-a.pdf"]["account_ref"] == "(unassigned)"

    def test_Press_NamingAnAccountNobodyDeclared_IsRefusedAndReadsNothing(self, served):
        base, root = served
        upload_ten(base)

        response = self.press(base, root, {**self.MIX, "Notes-b.pdf": "no-such-account"})

        assert response.status_code == 400
        assert "Nothing was read in" in flat(parse(response.text))
        assert kept(root)["Up-card-2026-08.pdf"]["account_ref"] == "(unassigned)"

    def test_Press_ForAccountsOfDocumentsOfSeveralAccounts_ReadsEachInToItsOwnAccount(
        self, served
    ):
        base, root = served
        upload_ten(base)

        response = self.press(base, root, {
            "All-accounts-2025-07.pdf|Regular Saver": SAVER,
            "All-accounts-2025-07.pdf|Holiday Pot": POT,
            "All-accounts-2025-08.pdf|Regular Saver": SAVER,
            "All-accounts-2025-08.pdf|Holiday Pot": "",
            "Up-card-2026-08.pdf": "up-card",
        })
        held = kept(root)
        said = flat(parse(response.text))

        assert [s["account"] for s in held["All-accounts-2025-07.pdf"]["sections"]] == [  # type: ignore[union-attr]
            SAVER, POT
        ]
        assert [s["account"] for s in held["All-accounts-2025-08.pdf"]["sections"]] == [  # type: ignore[union-attr]
            SAVER, ""
        ]
        assert held["Up-card-2026-08.pdf"]["account_ref"] == "up-card"
        assert "account Regular Saver: read in to Regular saver" in said
        left = rows_of(assign_form(parse(response.text)))
        assert sorted(left) == ["All-accounts-2025-08.pdf, account Holiday Pot"]

    def test_Press_ForASectionAlreadyReadIn_NeverMovesItToAnotherAccount(self, served):
        base, root = served
        upload_ten(base)
        self.press(base, root, {"All-accounts-2025-07.pdf|Regular Saver": SAVER})

        page = parse(self.press(base, root, {
            "All-accounts-2025-07.pdf|Regular Saver": POT
        }).text)

        sections = kept(root)["All-accounts-2025-07.pdf"]["sections"]
        assert sections[0]["account"] == SAVER  # type: ignore[index]
        assert "already held, under Regular saver" in flat(page)

    def test_Press_NamingAFileThatIsNotKept_IsRefusedAndReadsNothing(self, served):
        base, root = served
        upload_ten(base)

        response = httpx.post(
            f"{base}/statements-assign",
            data={"account-999999": "up-card", **{
                f"account-{kept(root)['Notes-a.pdf']['id']}": "up-card"}},
            timeout=60,
        )

        assert response.status_code == 400
        assert kept(root)["Notes-a.pdf"]["account_ref"] == "(unassigned)"

    def test_Press_ForAFileAlreadyReadInElsewhere_NeverMovesItToAnotherAccount(self, served):
        base, root = served
        earlier = kept(root)["Up-card-2025-09.pdf"]

        page = parse(httpx.post(
            f"{base}/statements-assign",
            data={f"account-{earlier['id']}": "other-card"},
            timeout=60,
        ).text)

        assert kept(root)["Up-card-2025-09.pdf"]["account_ref"] == "up-card"
        assert "Up-card-2025-09.pdf: already held, under Up card" in flat(page)


class TestOneFileAndTheUnchangedWays:
    def test_Upload_OfOneStatementNobodyGuessed_AsksWithOneChooserAndAButtonForOne(self, served):
        base, _ = served
        page = parse(httpx.post(f"{base}/bring-in", files=[part("Notes-a.pdf", letter("a"))],
                                timeout=60).text)

        form = assign_form(page)

        assert [b.text() for b in elements(form, "button")] == ["Read it in"]
        assert len(rows_of(form)) == 1

    def test_Upload_OfOneStatementWhoseReaderAndNameAreKnown_PreSelectsItAndReadsNothingIn(
        self, served
    ):
        base, root = served
        page = parse(httpx.post(
            f"{base}/bring-in",
            files=[part("Other-card-2026-09.pdf", santander(D(2026, 9, 11), 1433))],
            timeout=60,
        ).text)

        item = rows_of(assign_form(page))["Other-card-2026-09.pdf"]

        assert chosen(item) == "other-card"
        assert kept(root)["Other-card-2026-09.pdf"]["account_ref"] == "(unassigned)"

    def test_Statement_GivenAnAccountThroughTheOldSingleDoor_IsStillReadIn(self, served):
        base, root = served
        httpx.post(f"{base}/bring-in",
                   files=[part("Fresh.pdf", santander(D(2026, 9, 10), 1322))], timeout=60)
        ident = kept(root)["Fresh.pdf"]["id"]

        answer = httpx.post(f"{base}/statement-assign",
                            data={"artefact": str(ident), "account": "up-card"}, timeout=60)

        assert answer.status_code == 200
        assert kept(root)["Fresh.pdf"]["account_ref"] == "up-card"

    def test_BulkDoor_WithOneAccountForManyStatements_StillReadsThemAllIn(self, served):
        base, root = served
        httpx.post(f"{base}/bring-in", files=[
            part("Up-card-2026-08.pdf", santander(D(2026, 8, 10), 1211)),
            part("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)),
        ], timeout=60)
        held = kept(root)
        ids = ",".join(str(held[name]["id"]) for name in ("Up-card-2026-08.pdf",
                                                         "Up-card-2026-09.pdf"))

        answer = httpx.post(f"{base}/statements-assign",
                            data={"artefacts": ids, "account": "up-card"}, timeout=60)

        assert "Statements read in" in flat(parse(answer.text))
        assert kept(root)["Up-card-2026-08.pdf"]["account_ref"] == "up-card"


class TestDocumentsOfSeveralAccounts:
    def test_Document_WithASectionNobodyHasAssigned_IsLeftAloneAndSaysWhere(self, served):
        base, root = served
        page = parse(httpx.post(
            f"{base}/bring-in",
            files=[part("All-accounts-new.pdf", credit_union_new_account(7))],
            timeout=120,
        ).text)

        rows = rows_of(assign_form(page))
        sections = kept(root)["All-accounts-new.pdf"]["sections"]

        new = rows["All-accounts-new.pdf, account Brand New Account"]
        assert chosen(new) == "" and "went to" not in flat(new)
        assert chosen(rows["All-accounts-new.pdf, account Regular Saver"]) == SAVER
        assert not any(s["account"] for s in sections)  # type: ignore[union-attr]

    def test_Document_UploadedAgainAfterItWasReadIn_IsSaidToBeHeldAlready(self, served):
        base, root = served
        document_ = part("All-accounts-2025-07.pdf", credit_union(7))
        httpx.post(f"{base}/bring-in", files=[document_], timeout=120)
        TestPressingOnceForAMixOfFiles().press(base, root, {
            "All-accounts-2025-07.pdf|Regular Saver": SAVER,
            "All-accounts-2025-07.pdf|Holiday Pot": POT,
        })

        said = flat(parse(httpx.post(f"{base}/bring-in", files=[document_], timeout=120).text))

        assert "All-accounts-2025-07.pdf: already held, under Holiday pot and Regular saver" in said


def saver_only(month: int) -> bytes:
    """A year in which only the saver was open: one section, so the reader reads it whole."""
    period = f"01/{month:02d}/2025 to 28/{month:02d}/2025"
    day = f"05/{month:02d}/2025"
    return pdf(document(section("Regular Saver", 10000 * month,
                                [Move(day, "DD Lodgement", 500 + month)], period=period)),
               step=5.5)


def loan_only(month: int) -> bytes:
    period = f"01/{month:02d}/2025 to 28/{month:02d}/2025"
    day = f"05/{month:02d}/2025"
    return pdf(document(section("Personal -9.50%", -50000 - 1000 * month,
                                [Move(day, "tx", 15500)], period=period, loan=True)),
               step=5.5)


LOAN = "credit-union-loan"


@pytest.fixture
def served_split(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """Four single-account credit union documents already read in: two to the saver, two to the
    closed loan. Siblings by reader and issuer, split between two accounts."""

    def declare(store: Store) -> None:
        _declare(store)
        store.declare_account(AccountRecord(ref=AccountRef(LOAN), label="Closed loan"))

    with served_store(tmp_path, declare, bound=[]) as base:
        for name, payload, account in (
            ("All-accounts-2021.pdf", saver_only(1), SAVER),
            ("All-accounts-2022.pdf", saver_only(2), SAVER),
            ("All-accounts-2023.pdf", loan_only(3), LOAN),
            ("All-accounts-2024.pdf", loan_only(4), LOAN),
        ):
            said = httpx.post(f"{base}/bring-in", data={"account": account},
                              files=[part(name, payload)], timeout=120)
            assert f"{name}: read in to" in flat(parse(said.text)), said.text[:600]
        yield base, tmp_path


class TestTheHeadingLeads:
    def test_SingleSectionDocument_WhoseSiblingsWereSplitBetweenTwoAccounts_IsTheHeadingsAccount(
        self, served_split
    ):
        base, _ = served_split
        page = parse(httpx.post(f"{base}/bring-in", files=[
            part("All-accounts-2026.pdf", saver_only(9))], timeout=120).text)

        item = rows_of(assign_form(page))["All-accounts-2026.pdf"]

        assert chosen(item) == SAVER
        assert chosen(item) != LOAN
        assert "went to Regular saver" in flat(item)

    def test_SingleSectionDocument_OfTheOtherHeading_IsTheOtherAccount(self, served_split):
        base, _ = served_split
        page = parse(httpx.post(f"{base}/bring-in", files=[
            part("All-accounts-2026.pdf", loan_only(9))], timeout=120).text)

        assert chosen(rows_of(assign_form(page))["All-accounts-2026.pdf"]) == LOAN

    def test_SingleSectionDocument_WhoseHeadingWasGivenTwoAccounts_HasNoGuessAndNamesBoth(
        self, served_split
    ):
        base, _ = served_split
        httpx.post(f"{base}/bring-in", data={"account": POT},
                   files=[part("All-accounts-2020.pdf", saver_only(5))], timeout=120)
        page = parse(httpx.post(f"{base}/bring-in", files=[
            part("All-accounts-2026.pdf", saver_only(9))], timeout=120).text)

        item = rows_of(assign_form(page))["All-accounts-2026.pdf"]

        assert chosen(item) == ""
        assert "Holiday pot and Regular saver" in flat(item)

    def test_SingleSectionDocument_ReadsNothingInUntilThePress(self, served_split):
        base, root = served_split
        httpx.post(f"{base}/bring-in", files=[part("All-accounts-2026.pdf", saver_only(9))],
                   timeout=120)

        assert kept(root)["All-accounts-2026.pdf"]["account_ref"] == "(unassigned)"

class TestSeveralFilesToOneAccount:
    def test_ScopedUpload_OfThreeStatements_ReadsEachInAtOnceAndListsWhatEachSettled(
        self, served
    ):
        base, root = served
        response = httpx.post(
            f"{base}/bring-in",
            data={"account": "up-card"},
            files=[
                part("June.pdf", santander(D(2026, 6, 10), 1100)),
                part("July.pdf", santander(D(2026, 7, 10), 1200)),
                part("August.pdf", santander(D(2026, 8, 10), 1300)),
            ],
            timeout=120,
        )
        page = parse(response.text)
        said = flat(page)

        assert "3 files received" in said
        for name in ("June.pdf", "July.pdf", "August.pdf"):
            assert f"{name}: read in to Up card" in said
            assert kept(root)[name]["account_ref"] == "up-card"
        assert not [
            f for f in elements(page, "form") if f.attrs.get("action") == "/statements-assign"
        ]

    def test_ScopedPage_OffersAFieldThatTakesSeveralFilesAndSaysEachIsReadInAtOnce(self, served):
        base, _ = served
        page = parse(httpx.get(f"{base}/bring-in?account=up-card", timeout=60).text)
        form = next(f for f in elements(page, "form") if f.attrs.get("action") == "/bring-in")
        box = next(i for i in elements(form, "input") if i.attrs.get("type") == "file")

        assert "multiple" in box.attrs
        assert "Each is read in to this account." in flat(form)

    def test_ScopedUpload_WhenOneOfThreeCannotBeRead_StillReadsTheOtherTwo(self, served):
        base, _ = served
        response = httpx.post(
            f"{base}/bring-in",
            data={"account": "up-card"},
            files=[
                part("June.pdf", santander(D(2026, 6, 10), 1100)),
                part("Notes-a.pdf", b"not a pdf at all"),
                part("August.pdf", santander(D(2026, 8, 10), 1300)),
            ],
            timeout=120,
        )
        said = flat(parse(response.text))

        assert "June.pdf: read in to Up card" in said
        assert "August.pdf: read in to Up card" in said
        assert "Notes-a.pdf: not read in" in said


class TestWhenTheGuessCannotBeMade:
    def test_Upload_WhenTheKeptListingFails_StillKeepsEveryFileAndSaysNoGuessWasMade(
        self, tmp_path: Path
    ):
        with served_store(tmp_path, _declare, bound=[]) as base:
            _read_in_earlier(base, tmp_path)
            wired = build_web_config(tmp_path / "store.sqlite3")
            assert wired is not None and wired.kept_statements is not None

            def failing() -> list[dict[str, object]]:
                raise RuntimeError("the listing is broken")

            address, stop = serve_config(replace(wired, kept_statements=failing))
            try:
                page = parse(httpx.post(f"{address}/bring-in", files=[
                    part("Other-card-2026-09.pdf", santander(D(2026, 9, 11), 1433)),
                ], timeout=60).text)
            finally:
                stop()

        said = flat(page)
        assert "No account could be suggested" in said
        item = rows_of(assign_form(page))["Other-card-2026-09.pdf"]
        assert chosen(item) == ""
