# ruff: noqa: E501
"""The Position page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file, and every rule leads with a class of this page's own.

THE SHAPE. The chart's window is chosen with the thumb, so the common windows are buttons that
send the form themselves: one tap, no select to open and no second press. They wrap two to a
row on a phone and sit in one row from 40rem, each at least a tap high. The window in force is
the filled one (`aria-pressed`), told by fill and not by colour alone, since a filled chip is
also a heavier one. Everything else - a length to type, two days to give - is folded into a
disclosure that the page opens when the window in force is one of those, so the form is a
short strip above the chart and not a wall of fields.
"""

POSITION_STYLES = """
 .position-window { border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
                    margin: var(--s2) 0; padding: var(--s1) var(--s3) var(--s3); min-width: 0; }
 .position-window > legend { font: 600 var(--text-md) var(--sans); padding: 0 var(--s1); }
 .position-now { margin: 0 0 var(--s1); font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .position-chips { display: flex; flex-wrap: wrap; gap: var(--s2); margin: var(--s2) 0; }
 .position-chips button.position-chip { flex: 1 1 calc(50% - var(--s2)); width: auto; margin: 0;
            min-height: var(--hit); padding: 0 var(--s3); font: 600 var(--text-md)/130% var(--sans); }
 .position-chips button.position-chip[aria-pressed="true"] { background: var(--act); color: var(--act-ink); }
 .position-more { margin: var(--s2) 0 0; }
 .position-more .position-chips button.position-chip { flex: 1 1 calc(33% - var(--s2)); padding: 0 var(--s2);
            font-size: var(--text-sm); }
 .position-other, .position-between { border: var(--rule-weight) solid var(--rule-2); border-radius: var(--radius);
            margin: var(--s3) 0 0; padding: var(--s1) var(--s3) var(--s2); min-width: 0; }
 .position-other > legend, .position-between > legend { font: 600 var(--text-sm) var(--sans);
            color: var(--ink-2); padding: 0 var(--s1); }
 .position-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 var(--s3); }
 .position-cell { min-width: 0; margin: var(--s1) 0; }
 .position-cell > label { display: block; margin: 0 0 var(--s1); color: var(--ink-2); }
 .position-refused { margin: var(--s2) 0; }
 .position-ticks { margin: var(--s2) 0; }
 @media (min-width: 40rem) {
  .position-chips button.position-chip { flex: 0 1 auto; min-width: 8rem; }
 }
"""
