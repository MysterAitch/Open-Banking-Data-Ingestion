"""The window control, read and drawn without any page around it.

Two charts share this control, so what it reads and says is checked here against the days
it is given and not through either page. Today is passed in. Worked out before the first run:

    nothing sent                        everything, key "all", no refusal
    window=m12                          a 12-month length, key "m12"
    window=between, 2026-02-01 to       refused, in the sentence naming both days
      2026-01-31
    window=other, count "abc"           refused: the length must be a whole number
    window=nonsense, held "d90"         taken as keeping: key "d90"
    window=nonsense, held "nonsense"    taken as keeping, and what is kept is everything
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.date_window import Anchor, Period, Unit
from obdi.stylesheet_position import POSITION_STYLES
from obdi.stylesheet_window import WINDOW_STYLES
from obdi.window_control import WINDOW_FIELDS, window_choice, window_controls

TODAY = date(2026, 10, 4)
HELD = date(2019, 1, 5)


def read(**fields: str):
    return window_choice(fields, today=TODAY, held_from=HELD)


class TestReadingTheChoice:
    def test_NothingSent_IsEverythingAndIsNotRefused(self):
        choice = read()

        assert (choice.key, choice.spec, choice.refusal) == ("all", None, "")

    def test_ALength_IsReadAsThatLength(self):
        choice = read(window="m12")

        assert choice.key == "m12"
        assert choice.spec is not None
        assert (choice.spec.count, choice.spec.unit) == (12, Unit.MONTHS)
        assert choice.spec.anchor is Anchor.ENDING_TODAY

    @pytest.mark.parametrize(
        ("key", "period"),
        [
            ("this-week", Period.THIS_WEEK),
            ("last-week", Period.LAST_WEEK),
            ("last-4-weeks", Period.LAST_4_WEEKS),
        ],
    )
    def test_AWeekPeriod_IsReadAsThatNamedPeriod(self, key, period):
        choice = read(window=key)

        assert choice.spec is not None and choice.spec.period is period

    def test_TheWeekPeriods_AreInTheFoldAndNotAmongTheOneTapChips(self):
        page = window_controls(read(), today=TODAY.isoformat())
        before_fold, fold = page.split('<details class="window-more"')

        for key in ("this-week", "last-week", "last-4-weeks"):
            assert f'value="{key}"' in fold
            assert f'value="{key}"' not in before_fold

    def test_TwoDaysTheWrongWayRound_AreRefusedInASentenceNamingBoth(self):
        choice = read(window="between", window_from="2026-02-01", window_to="2026-01-31")

        assert choice.spec is None
        assert "2026-02-01" in choice.refusal and "2026-01-31" in choice.refusal

    def test_ALengthThatIsNotANumber_IsRefusedAndTheKeepingChoiceIsUnchanged(self):
        choice = read(window="other", window_count="abc", window_held="d90")

        assert choice.refusal == "The length must be a whole number, such as 12."
        assert choice.held == "d90"

    def test_ANameThatIsNoneOfOurs_IsTakenAsKeepingTheHeldChoice(self):
        assert read(window="nonsense", window_held="d90").key == "d90"

    def test_AHeldChoiceThatIsNoneOfOurs_IsTakenAsEverything(self):
        assert read(window="nonsense", window_held="nonsense").key == "all"

    def test_FieldsOutsideTheWindowsOwn_AreNeverEchoed(self):
        choice = read(window="m3", ref="account:main", other="x")

        assert set(choice.fields) <= set(WINDOW_FIELDS)

    def test_NothingHeld_StillRefusesAChoiceThatCannotBeAWindow(self):
        choice = window_choice(
            {"window": "between", "window_from": "2026-02-01", "window_to": "2026-01-31"},
            today=TODAY,
            held_from=None,
        )

        assert choice.spec is None and choice.refusal


class TestDrawingTheControl:
    def test_ANote_SitsInsideTheControlAndNoneIsAddedWithoutOne(self):
        choice = read()

        with_note = window_controls(choice, today=TODAY.isoformat(), note="<p>Say this.</p>")
        without = window_controls(choice, today=TODAY.isoformat())

        assert "<p>Say this.</p>" in with_note
        assert "<p>Say this.</p>" not in without

    def test_TheWindowHeld_TravelsBackAsAHiddenField(self):
        page = window_controls(read(window="m12"), today=TODAY.isoformat())

        assert '<input type="hidden" name="window_held" value="m12">' in page


class TestTheStyles:
    def test_TheControlsRules_AreTheSharedModulesAndNotAnyPagesOwn(self):
        assert ".window-chip" in WINDOW_STYLES
        assert ".window-chip" not in POSITION_STYLES
