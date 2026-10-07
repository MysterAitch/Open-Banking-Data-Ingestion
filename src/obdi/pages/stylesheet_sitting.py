# ruff: noqa: E501
"""The rules for showing values for a sitting, joined into the one stylesheet by `stylesheet`.

Written with the shared tokens: no colour, face, or size is declared in this file.

THE BANNER is a rail and a line, not a box: values being shown is a state to be reminded of on
every page, and a state that fills the screen on every page would be trained away. The rail is the
warning colour so that it reads as "something is open", and the text is plain ink so that the
page's own faults stay the loudest thing on it. Its control is a link-weight button, one tap tall.
"""

SITTING_STYLES = """
 .sitting { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--s3); margin: var(--s3) 0;
            padding: var(--s1) var(--s3); background: var(--card); border-left: var(--rail) solid var(--warn);
            font: var(--text-sm)/140% var(--sans); }
 .sitting > span { flex: 1 1 14rem; }
 .sitting form, form.sitting-press { margin: 0; }
 .sitting button.tap, form.sitting-press button.tap { padding: 0; }
"""
