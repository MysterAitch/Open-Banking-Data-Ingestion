# ruff: noqa: E501
# The text below is CSS; a rule reads best as one declaration block, and the tests that guard
# the stylesheet read it by shape.
"""The hub pages' own rules (Bring in, Checks, Diagnostics), joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file. A hub is a ruled list, not a stack of cards: each row leads with
the page's name in the display serif and, where it has one, a chip carrying the state, so a
column of seven checks reads as seven verdicts at a glance and the chip survives greyscale by its
glyph. From 60rem the rows sit in two columns, because a row is one name and one sentence and a
single column of them at that width is mostly empty paper. The repairs stand apart in a bordered
block whose edge is the colour of what can go wrong.
"""

SECTION_STYLES = r"""
 .hub-rows { list-style: none; margin: var(--s3) 0; padding: 0; }
 .hub-row { padding: var(--s3) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .hub-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
             gap: var(--s1) var(--s3); }
 .hub-head .hub-name { font: 600 var(--text-lg)/var(--leading-tight) var(--serif); }
 /* A row that needs a person carries a rail in the colour of what it says; a row in order is
    left calm, so the eye is taken to the rows that are not. */
 .hub-row-bad { padding-left: var(--s3); border-left: var(--rail) solid var(--bad); }
 .hub-row-warn { padding-left: var(--s3); border-left: var(--rail) solid var(--warn); }
 .hub-line { margin: var(--s1) 0 0; }
 .hub-more { margin: var(--s2) 0 0; }
 .hub-age { margin-top: var(--s4); }
 .hub-list { list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-md)/150% var(--sans); }
 .hub-list li { padding: var(--s1) 0; }
 .formerly { margin: 0 0 var(--s3); font: var(--text-sm)/150% var(--sans); }
 /* The way out sits under the heading, so a page deep in a flow can leave without scrolling. */
 .wayout { margin: 0 0 var(--s3); }
 .wayout a { display: inline-flex; align-items: center; min-height: var(--hit); }
 /* The fetch timeline's choices: stacked on a phone, one row where there is width. */
 .timeline-choices { display: grid; gap: var(--s2) var(--s4); margin: var(--s3) 0; }
 .timeline-choices button:not(.button) { margin: 0; }
 .diag-accounts { margin: var(--s3) 0; }
 .diag-danger { margin: var(--s6) 0 var(--s4); padding: 0 var(--s4) var(--s3); border: var(--edge-weight) solid var(--bad);
                border-radius: var(--radius); }
 .diag-danger > h2 { color: var(--bad); }
 .diag-repair { margin-top: var(--s3); padding-top: var(--s2); border-top: var(--rule-weight) solid var(--rule); }
 .diag-repair h3 { margin: 0 0 var(--s1); }
 .diag-repair p { margin: var(--s1) 0 var(--s2); }
 @media (min-width: 60rem) {
  /* Seven destinations fit one row where there is width, so the strip is one line. */
  .sitenav ul { grid-template-columns: repeat(7, minmax(0, 1fr)); }
  .sitenav li:nth-child(n+5) a { border-top: 0; }
  .hub-rows { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 var(--s6); }
  .timeline-choices { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; align-items: end; }
  .timeline-choices button:not(.button) { width: auto; padding: 0 var(--s5); }
  .diag-accounts .hub-list { columns: 3; column-gap: var(--s5); }
  .diag-danger { max-width: 40rem; }
 }
"""
