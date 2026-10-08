"""Where the account the other party was paid at counts as a stated party, and how an entity that
holds such a party is shown.

KNOWN ANSWERS, decided before the first run (invented accounts and names).

  - An account whose three statement rows state the other party's account and no name is NOT
    described: the account rung of the ladder names them, as the Entities page counts them. The
    same three rows with no account stated are described, all three.
  - An entity holding the account of "alex rowan" is listed on its page under "Account" as
    "alex rowan", the label the rows give it. Neither the digest key the account is held under nor
    the digits of the number it came from is anywhere in the page, masked or not.
  - A key with no label held for it (an entity still holding a party whose payments have gone) is
    listed as "a party no name is held for", and a raw account number is listed by its ending.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from obdi.analysis.entities import (
    UNNAMED_PARTY,
    NameOrigin,
    entity_page_of,
    is_identifier_key,
    name_of,
    name_shown,
)
from obdi.analysis.party_stated import party_stated_by_account
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.entity_records import ACCOUNT, Entity, Identifier
from obdi.ingest.store import Store
from obdi.pages.web_entity import render_entity
from party_stated_world import STATEMENT_SOURCE, _land, _transaction

NUMBER = "20000012345678"
KEY = name_of("jan rent", "", account=NUMBER).name


def _statement_account(store: Store, ref: str, *, account: str) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref))
    _land(
        store,
        ref,
        STATEMENT_SOURCE,
        [
            replace(
                _transaction(
                    ref,
                    date(2026, 3, 1 + 7 * n),
                    n,
                    source=STATEMENT_SOURCE,
                    counterparty="",
                    description=f"RENT REF {n}",
                ),
                party_account=account,
            )
            for n in range(3)
        ],
    )


class TestAnAccountRowsStateButNoName:
    def test_PartyStated_WhenRowsStateTheOtherPartysAccount_AreNotDescribed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _statement_account(store, "withacct", account="20-00-00 12345678")

            found = party_stated_by_account(store)["withacct"]

        assert found.described == 0 and found.transactions == 3

    def test_PartyStated_WhenRowsStateNeitherAccountNorName_CallsAllDescribed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _statement_account(store, "plain", account="")

            found = party_stated_by_account(store)["plain"]

        assert found.described == 3 and found.transactions == 3


def _visible(html: str) -> str:
    """The page without the values of its form fields: the key is what a press posts back, a
    digest and not the number, and is no part of what a reader is shown."""
    return html.replace(f'value="{KEY}"', 'value=""')


def _entity_page(labels: dict[str, str], value: str, origins: dict[str, NameOrigin]):
    entity = Entity(
        1, "Housemate", None, (value,), identifiers=(Identifier(ACCOUNT, value, support=5),)
    )
    page = entity_page_of(1, [entity], [], {value: 5}, {}, origins, labels)
    assert page is not None
    return page


class TestAnEntityHoldingAnAccountKey:
    def test_Key_IsADigestAndNotTheNumber(self):
        assert is_identifier_key(KEY) and NUMBER not in KEY

    def test_EntityPage_ListsTheAccountByTheLabelItsRowsGive_NotByKeyOrNumber(self):
        page = _entity_page({KEY: "alex rowan"}, KEY, {KEY: NameOrigin(account=5)})

        html = _visible(render_entity(page, unmasked=True).decode("utf-8"))

        assert "alex rowan" in html
        assert KEY not in html and NUMBER not in html

    def test_EntityPage_WhenNoLabelIsHeldForTheKey_SaysNoNameIsHeld(self):
        page = _entity_page({}, KEY, {KEY: NameOrigin(account=5)})

        html = _visible(render_entity(page, unmasked=True).decode("utf-8"))

        assert UNNAMED_PARTY in html and KEY not in html

    def test_NameShown_ForAKeyWithNoLabel_IsNeverTheKey(self):
        assert name_shown(KEY, ACCOUNT) == UNNAMED_PARTY

    def test_NameShown_ForARawNumber_IsItsEndingOnly(self):
        assert name_shown(NUMBER, ACCOUNT) == "ending 5678"
        assert name_shown("200000-12345678", ACCOUNT) == "ending 5678"

    def test_NameShown_ForALabelOfAnAccountKindParty_IsTheLabelAsItIs(self):
        assert name_shown("alex rowan", ACCOUNT) == "alex rowan"
