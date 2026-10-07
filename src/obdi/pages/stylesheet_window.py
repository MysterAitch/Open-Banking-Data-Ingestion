# ruff: noqa: E501
"""The window control's rules, shared by every chart that offers one, joined into the one
stylesheet by `stylesheet`.

Written with the shared tokens: no colour, face, or size is declared in this file, and every
rule leads with a class of the control's own (`window_control` writes the markup).

THE SHAPE. The chart's window is chosen with the thumb, so the common windows are buttons that
send the form themselves: one tap, no select to open and no second press. They wrap two to a
row on a phone and sit in one row from 40rem, each at least a tap high. The window in force is
the filled one (`aria-pressed`), told by fill and not by colour alone, since a filled chip is
also a heavier one. Everything else - a length to type, two days to give - is folded into a
disclosure that the page opens when the window in force is one of those, so the form is a
short strip above the chart and not a wall of fields.
"""

WINDOW_STYLES = """
 .window-control { border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
                    margin: var(--s2) 0; padding: var(--s1) var(--s3) var(--s3); min-width: 0; }
 .window-control > legend { font: 600 var(--text-md) var(--sans); padding: 0 var(--s1); }
 .window-now { margin: 0 0 var(--s1); font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .window-chips { display: flex; flex-wrap: wrap; gap: var(--s2); margin: var(--s2) 0; }
 .window-chips button.window-chip { flex: 1 1 calc(50% - var(--s2)); width: auto; margin: 0;
            min-height: var(--hit); padding: 0 var(--s3); font: 600 var(--text-md)/130% var(--sans); }
 .window-chips button.window-chip[aria-pressed="true"] { background: var(--act); color: var(--act-ink); }
 .window-more { margin: var(--s2) 0 0; }
 .window-more .window-chips button.window-chip { flex: 1 1 calc(33% - var(--s2)); padding: 0 var(--s2);
            font-size: var(--text-sm); }
 .window-other, .window-between { border: var(--rule-weight) solid var(--rule-2); border-radius: var(--radius);
            margin: var(--s3) 0 0; padding: var(--s1) var(--s3) var(--s2); min-width: 0; }
 .window-other > legend, .window-between > legend { font: 600 var(--text-sm) var(--sans);
            color: var(--ink-2); padding: 0 var(--s1); }
 .window-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 var(--s3); }
 .window-cell { min-width: 0; margin: var(--s1) 0; }
 .window-cell > label { display: block; margin: 0 0 var(--s1); color: var(--ink-2); }
 .window-refused { margin: var(--s2) 0; }
 @media (min-width: 40rem) {
  .window-chips button.window-chip { flex: 0 1 auto; min-width: 8rem; }
 }
"""
