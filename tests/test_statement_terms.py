"""Warning before a promotional rate reverts.

This is what the statement arm was for. A 0% balance transfer carries the
date it ends, and nothing else in the system knows that date: no feed
exposes it, and by the time the higher rate appears on a statement it has
already been charged. The useful response - move the balance or clear it -
takes weeks, so the notice is longer than the consent ladder's.

The balance is part of the CONDITION, not decoration on the message: a
reversion with nothing outstanding is not news, because the new rate
applies to nothing.

Terms are derived from the statements already held rather than stored, so
they cannot disagree with the evidence and import order cannot matter.
"""

from __future__ import annotations

from datetime import date

from obdi.account_observations import Observation, current_view
from obdi.ingest import import_file
from obdi.statement_terms import observations_from_statements, reversion_findings
from obdi.store import Store
from test_statement_shape import build_pdf


def statement(*, statement_date: str, promo_until: str, closing: str) -> bytes:
    return build_pdf(
        [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {statement_date}      Page No: 4 / 4",
            "Account credit limit:            3,000.00",
            "Balance brought forward from previous statement          1,000.00",
            "29th Jun    Some Shop Somewhere GB                          10.00",
            f"Balance {closing} Interest  0.000% to {promo_until}",
            f"Your new balance:                                        {closing}",
        ]
    )


def _land(store: Store, tmp_path, name: str, payload: bytes) -> None:
    path = tmp_path / name
    path.write_bytes(payload)
    import_file(store, path, account_id="santander-cc")


class TestTermsComeFromTheStatementsHeld:
    def test_APromotionalWindow_IsDerivedWithItsEndDate(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _land(
                store,
                tmp_path,
                "july.pdf",
                statement(
                    statement_date="11th July 2026",
                    promo_until="11-03-2027",
                    closing="1,010.00",
                ),
            )

            found = observations_from_statements(store)

            promotional = [
                item for item in found if item.kind == "promotional"
            ]
            assert len(promotional) == 1
            assert promotional[0].window_to == date(2027, 3, 11)
            assert promotional[0].observed_at == date(2026, 7, 11)

    def test_TheBalanceAndLimit_AreDerivedToo(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _land(
                store,
                tmp_path,
                "july.pdf",
                statement(
                    statement_date="11th July 2026",
                    promo_until="11-03-2027",
                    closing="1,010.00",
                ),
            )

            view = current_view(
                observations_from_statements(store), on=date(2026, 7, 20)
            )

            assert view["balance"] == "-101000", "owed, in the house convention"
            assert view["credit_limit"] == "300000"

    def test_TwoStatements_BothContribute_WhicheverArrivedFirst(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _land(
                store,
                tmp_path,
                "later.pdf",
                statement(
                    statement_date="11th July 2026",
                    promo_until="11-03-2027",
                    closing="1,010.00",
                ),
            )
            _land(
                store,
                tmp_path,
                "earlier.pdf",
                statement(
                    statement_date="11th June 2026",
                    promo_until="11-03-2027",
                    closing="1,010.00",
                ),
            )

            found = observations_from_statements(store)

            witnessed = sorted({item.observed_at for item in found})
            assert witnessed == [date(2026, 6, 11), date(2026, 7, 11)]
            # The later statement's balance is the current one, whichever
            # file was imported first.
            view = current_view(found, on=date(2026, 7, 20))
            assert view["balance"] == "-101000"


class TestTheReversionWarning:
    def _observations(self, *, owed: int, ends: date) -> list[Observation]:
        return [
            Observation(
                account_id="santander-cc",
                fact="balance",
                kind="",
                observed_at=date(2026, 7, 11),
                value=str(-owed),
            ),
            Observation(
                account_id="santander-cc",
                fact="rate",
                kind="promotional",
                observed_at=date(2026, 7, 11),
                value="0.0",
                window_from=date(2026, 7, 11),
                window_to=ends,
            ),
        ]

    def test_ANearReversion_WithABalance_Warns(self):
        found = reversion_findings(
            self._observations(owed=101000, ends=date(2027, 3, 11)),
            today=date(2027, 3, 1),
        )

        assert len(found) == 1
        _key, message, rung = found[0]
        assert "10 day(s)" in message
        assert "1010.00" in message
        assert rung == 3, "inside a fortnight"

    def test_TheWarningRises_AsTheDateApproaches(self):
        observations = self._observations(owed=101000, ends=date(2027, 3, 11))
        rungs = [
            reversion_findings(observations, today=day)[0][2]
            for day in (
                date(2027, 1, 20),
                date(2027, 2, 20),
                date(2027, 3, 1),
                date(2027, 3, 8),
            )
        ]

        assert rungs == [1, 2, 3, 4], "each threshold crossed is its own news"

    def test_AReversion_WithNothingOwed_IsNotNews(self):
        found = reversion_findings(
            self._observations(owed=0, ends=date(2027, 3, 11)),
            today=date(2027, 3, 1),
        )

        assert found == []

    def test_ADistantReversion_IsNotYetNews(self):
        found = reversion_findings(
            self._observations(owed=101000, ends=date(2027, 3, 11)),
            today=date(2026, 8, 1),
        )

        assert found == []

    def test_APassedReversion_IsNotWarnedAbout(self):
        found = reversion_findings(
            self._observations(owed=101000, ends=date(2027, 3, 11)),
            today=date(2027, 4, 1),
        )

        assert found == []
