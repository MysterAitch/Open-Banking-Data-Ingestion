# ruff: noqa: E501
"""Today's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file, so light and dark both come from the tokens.

THE SHAPE OF THE PAGE. A phone column read top to bottom: a verdict in the display serif with a
mark in the colour of its tone, one muted line of evidence, the things to do each with its
control, then one row per account with a bar on a scale shared by every account. Where there is
room (`min-width: 60rem`) the verdict, evidence, and things to do sit in a column beside the
accounts, because the accounts list is the long part; from 76rem each account is one line.

THE COMPONENTS (each cut from the redesign's prototype to what Today uses):
  1. the to-do row (`.todo`): a rail says how pressing (red now, amber soon, grey when
     convenient, dashed where the need is an inference), then what to do, one quiet line of
     which account and why and how old, and the control beside it;
  2. the trust bar (`.axis`, `.bar`, `.arow`): three fills for the three rungs, each a rung more
     solid than the one before so the ladder reads without colour (a hatch, an outline, a solid),
     on one twelve-month scale, with a red mark and a dashed block for what needs a person;
  3. the evidence line (`details.evidence`): one muted line that opens to the detail.
`trust_bar` writes the bar and names no colour; the classes are given their tokens here.
"""

HOME_STYLES = """
 /* The verdict is the page's heading; the h1 stays for a screen reader and for the title, and
    costs the first screen nothing. */
 main:has(.home) > h1 { position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip-path: inset(50%); white-space: nowrap; margin: 0; }
 .home .verdict { display: flex; align-items: flex-start; gap: var(--s3); margin: var(--s4) 0 var(--s2);
        font: 600 var(--text-2xl)/130% var(--serif); color: var(--ink); }
 .home .verdict span { min-width: 0; overflow-wrap: anywhere; text-wrap: balance; }
 .home .verdict.long { font-size: var(--text-lg); }
 .home .verdict::before { flex: none; width: 1.75rem; height: 1.75rem; margin-top: .1rem; border-radius: 50%;
        border: var(--edge-weight) solid currentColor; display: grid; place-items: center;
        font: 700 var(--text-md)/1 var(--sans); }
 .home .verdict.ok { font: 400 var(--text-md)/150% var(--sans); margin: var(--s3) 0 var(--s2); }
 .home .verdict.ok::before { content: "\\2713"; content: "\\2713" / ""; color: var(--ok);
        width: 1.25rem; height: 1.25rem; margin-top: 0; font: 700 var(--text-xs)/1 var(--sans); }
 .home .verdict.warn::before { content: "!"; content: "!" / ""; color: var(--warn); }
 .home .verdict.bad::before { content: "\\2715"; content: "\\2715" / ""; color: var(--bad); }
 .home .verdict-lede { margin: 0 0 var(--s2); font: var(--text-sm)/140% var(--sans); }
 .item-message { font: var(--text-base)/140% var(--serif); }

 /* An old date is the one thing in a quiet line that is not quiet. */
 .age { color: var(--warn); font-weight: 600; }

 /* The evidence line: it was looked at, and when. One muted line that opens to the detail. */
 details.evidence > summary { min-height: var(--hit); font: var(--text-xs)/140% var(--sans); color: var(--ink-2); }
 details.evidence > summary::before { width: .4rem; height: .4rem; border-color: var(--edge); }
 details.evidence p, details.evidence li { margin: 0 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 details.evidence ul { list-style: none; margin: 0; padding: 0; }

 /* 1. The to-do row. */
 .todos { list-style: none; margin: var(--s3) 0; padding: 0; display: grid; gap: var(--s3); }
 /* The control sits beside the words where there is room and drops beneath them where there is
    not (a narrow phone with enlarged text), which a fixed two-column grid could not do. */
 .todo { display: flex; flex-wrap: wrap; gap: var(--s2) var(--s3); align-items: center;
         padding: 2px 0 2px var(--s3); border-left: var(--rail) solid var(--edge); }
 .todo.now { border-left-color: var(--bad); }
 .todo.soon { border-left-color: var(--warn); }
 .todo.guess { border-left-style: dashed; }
 .todo-text { flex: 1 1 12rem; min-width: 0; }
 .todo-what { margin: 0; font: 600 var(--text-base)/130% var(--serif); overflow-wrap: anywhere; }
 .todo.now .todo-what { color: var(--bad); }
 .todo-why { margin: 2px 0 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); overflow-wrap: anywhere; }
 .todo-why b { color: var(--ink); font-weight: 600; }
 .todo a.button { flex: none; max-width: 100%; margin: 0; min-height: var(--hit); padding: 0 var(--s3);
         font-size: var(--text-sm); white-space: normal; text-align: center; }
 .lockline { margin: var(--s2) 0; font: var(--text-sm)/140% var(--sans); }

 /* 2. The trust bar: four marks of one scale. A bare line is nothing held; a hatch is held with
    nothing yet adding it up; an outline adds up; a solid is one a person accepted. The words
    avoid the plain word for the solid, which other pages' absence tests search this sheet for. */
 .axis { position: relative; display: block; height: 1.25rem; font: var(--text-xs)/1 var(--sans); color: var(--ink-2); }
 .axis > span { position: absolute; top: 0; bottom: 0; display: flex; align-items: center;
         padding-left: 3px; border-left: var(--rule-weight) solid var(--rule); }
 .bar { position: relative; display: block; height: .75rem; }
 .bar::before { content: ""; position: absolute; left: 0; right: 0; top: 50%;
         border-top: var(--rule-weight) solid var(--edge); }
 .bar > i { position: absolute; top: 0; bottom: 0; }
 .b-lock { background: var(--ok); }
 .b-adds { background: var(--ok-bg); box-shadow: inset 0 0 0 1.5px var(--ok); }
 .b-held { background: repeating-linear-gradient(135deg, var(--edge) 0 1.5px, var(--paper) 1.5px 5px); }
 .b-bad { background: var(--bad); min-width: 5px; }
 .b-want { background: var(--paper); border: 1.5px dashed var(--warn); min-width: 5px; }
 .bar > i.b-edge { left: 0; width: 3px; background: var(--ink-2); }
 .alist { list-style: none; margin: 0; padding: 0; border-top: var(--rule-weight) solid var(--rule); }
 .alist li { border-bottom: var(--rule-weight) solid var(--rule-2); }
 main a.arow { display: grid; grid-template-columns: minmax(0, 1fr) auto;
         grid-template-areas: "name flag" "bar bar" "trust trust"; gap: 3px var(--s2); align-items: baseline;
         min-height: var(--tap); padding: var(--s2) 0; color: var(--ink); text-decoration: none; }
 main a.arow::after { content: none; }
 .a-name { grid-area: name; font: 600 var(--text-base)/130% var(--serif); overflow-wrap: anywhere; }
 /* The one slot that is empty when the account asks nothing. */
 .a-flag { grid-area: flag; text-align: right; font: 600 var(--text-sm)/130% var(--sans); color: var(--warn); }
 .a-flag.bad { color: var(--bad); font-weight: 700; }
 .a-flag.bad::before { content: "\\2715\\00a0"; content: "\\2715\\00a0" / ""; }
 main a.arow > .bar { grid-area: bar; }
 .a-trust { grid-area: trust; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .alist li.space { padding-left: var(--s4); }
 .alist li.space .a-name { font-size: var(--text-md); font-weight: 500; }
 .axis-row { display: grid; grid-template-columns: minmax(0, 1fr); }
 .key { display: inline-block; position: relative; width: 1.75rem; height: .6rem; vertical-align: baseline;
         margin-right: var(--s2); }
 .key.k-line { height: 0; border-top: var(--rule-weight) solid var(--edge); vertical-align: middle; }
 .keylist { list-style: none; margin: 0 0 var(--s2); padding: 0; font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .keylist b { color: var(--ink); font-weight: 600; }
 .p-more { margin-top: var(--s5); }
 .p-more > details { border-top: var(--rule-weight) solid var(--rule-2); }
 .p-more > details:last-of-type { border-bottom: var(--rule-weight) solid var(--rule-2); }
 .p-more > details p { margin: var(--s1) 0 var(--s3); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }

 /* Beside the accounts where there is room; from 76rem each account is one line. */
 @media (min-width: 60rem) {
  .home { display: grid; grid-template-columns: minmax(0, 26rem) minmax(0, 1fr); gap: 0 var(--s6); align-items: start; }
  .home-accounts h2 { margin-top: var(--s4); }
 }
 @media (min-width: 76rem) {
  main a.arow, .axis-row { grid-template-columns: 11rem minmax(0, 1fr) 10.5rem; gap: 2px var(--s4); align-items: center; }
  main a.arow { grid-template-areas: "name bar flag" ". trust trust"; }
  .axis-row > .axis { grid-column: 2; }
  .alist li.space { padding-left: 0; }
  .alist li.space .a-name { padding-left: var(--s4); }
 }
"""
