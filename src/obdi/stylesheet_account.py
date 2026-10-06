# ruff: noqa: E501
"""An account's page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file. The to-do row, the trust bar, and the key are Today's
(`stylesheet_home`); this adds the sentence, the strip of lanes, and the month's transactions.

THE SHAPE. On a phone the page is one column in the order a person asks their questions: whose
account, how far it can be trusted, what to do about it, the month's transactions, and everything
else in five folds beneath them. From 60rem the state and the things to do sit in a narrow column
beside the transactions, which fill a wide one.

SEALED. A masked figure or masked text carries `.sealed`, drawn as a hatched slot, and a shown
one is plain: the state of the page is visible at a glance without reading the banner. The
hatch is a rule colour at a fixed pitch, and the characters inside it are never hidden, so the
slot still says how long the thing is.

RAIL, NOT BOX. A transaction that is flagged or doubtful has a rail at its edge, as the things to
do have; no box is tinted on this page.
"""

ACCOUNT_STYLES = """
 /* Where the account page begins: its four places stack on a phone. */
 .acct-grid { display: grid; grid-template-columns: minmax(0, 1fr); }
 .acct-grid > * { min-width: 0; }
 .ledger-page h1 { margin: var(--s3) 0 var(--s1); }
 .ledger-page h2 { font-size: var(--text-lg); margin: var(--s5) 0 var(--s2); }
 .ledger-page .sub { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); margin: var(--s1) 0; }
 .meta { margin: 0 0 var(--s2); font: var(--text-xs)/150% var(--sans); color: var(--ink-2); }
 .next { margin: var(--s1) 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }

 /* The trust sentence: ordinary where all is well. The one that needs the reader is large, with
    a glyph so it reads in greyscale, and the part that asks something is the amber part. */
 .trust { margin: var(--s2) 0 var(--s1); font: var(--text-base)/140% var(--serif); }
 .trust.bad { font: 600 var(--text-xl)/130% var(--serif); color: var(--bad); }
 .trust.bad::before { content: "\\2715\\00a0"; content: "\\2715\\00a0" / ""; }
 .trust.none { font: 600 var(--text-xl)/130% var(--serif); }
 .trust.none::before { content: "\\25CB\\00a0"; content: "\\25CB\\00a0" / ""; color: var(--warn); }
 .trust .sub { display: block; margin-top: var(--s1); font: 400 var(--text-md)/140% var(--sans); color: var(--ink); }

 /* The strip: the trust lane over one lane per source, on the same twelve months. The whole
    strip is the link to the full timeline. */
 main a.strip { display: grid; grid-template-columns: 5.25rem minmax(0, 1fr); gap: var(--s2);
         align-items: center; margin: var(--s2) 0 var(--s3); color: inherit; text-decoration: none; }
 .strip .axis { overflow: hidden; }
 .strip .lane { font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .strip .lane.first { color: var(--ink); font-weight: 600; }
 .strip .ends { display: flex; justify-content: space-between; font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .b-src { background: var(--ink-2); }

 /* Things to do, on this page: the words, then the control under them. An offer is quieter. */
 .acct-state .todo { display: grid; grid-template-columns: minmax(0, 1fr); row-gap: var(--s2); align-items: start; }
 .acct-state .todo a.button, .acct-state .todo button.button { min-height: var(--tap); font-size: 1rem; white-space: normal; }
 .acct-state .todo.offer { border-left-color: var(--rule); }
 .acct-state .todo.offer .todo-what { font-weight: 400; }
 .acct-state .todo form { margin: 0; }
 .acct-state .todo form button:not(.button) { margin: 0; }
 .todo-form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--s2) var(--s3); }
 .todo-form label { display: block; }
 .todo-form p { grid-column: 1 / -1; margin: 0; }
 .acct-state details { margin: var(--s1) 0; }

 /* The month: its heading and the steps beside it, then one line of what it holds. */
 .txhead { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 0 var(--s4); }
 .txhead h2 { margin-bottom: 0; }
 .acct-month .monthnav { margin: 0; }
 .txcount { margin: var(--s1) 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 details.months > summary { min-height: var(--hit); }
 .year { display: grid; grid-template-columns: 3rem minmax(0, 1fr); align-items: center; gap: var(--s2); margin: var(--s1) 0; }
 .year-label { color: var(--ink-2); }
 .monthgrid { list-style: none; margin: 0; padding: 0; display: grid;
              grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--s1); }
 .monthgrid li { min-width: 0; }
 .monthgrid li > a.tap::after { content: none; }
 .monthgrid li > a.tap, .monthgrid li > button.tap, .absent {
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                min-height: var(--hit); padding: 0 var(--s1); border-radius: var(--radius);
                font: 600 var(--text-sm)/120% var(--sans); text-decoration: none; }
 .monthgrid li > a.tap, .monthgrid li > button.tap { width: 100%; margin: 0; cursor: pointer;
                color: var(--act); border: var(--rule-weight) solid var(--rule); background: var(--card); }
 .monthgrid li > [aria-current="true"] { border: var(--edge-weight) solid var(--act); color: var(--ink); }
 .absent { color: var(--ink-2); border: var(--rule-weight) dashed var(--rule); font-weight: 400; }
 .count { font: var(--text-xs)/120% var(--mono); color: var(--ink-2); }
 .shown { border: var(--edge-weight) solid currentColor; padding: var(--s3); border-radius: var(--radius); }

 /* A transaction is one line: the day, the description, the figure, and one mark for whether
    something has cleared it. The line is the summary of the transaction's own disclosure, so its
    hit area is the whole line and everything else is a tap away. */
 .txns li.txn { padding: 0; }
 .t-row, .txns li.txn summary.t-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--s2);
                padding: var(--s1) 0; min-height: var(--hit); color: inherit; font: inherit; }
 .txns li.txn summary.t-row::before { display: none; }
 .t-when.mono { flex: none; color: var(--ink-2); font-size: var(--text-xs); }
 .t-desc { flex: 1 1 7rem; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
 details[open] > summary .t-desc { white-space: normal; overflow-wrap: anywhere; }
 .t-fig { flex: none; font-weight: 600; font-size: var(--text-sm); text-align: right; }
 /* The balance after the row: set beneath the amount's weight, in the muted ink, and holding its
    width where a row has none so the figures beneath stay in line. */
 .t-bal { flex: none; min-width: 4.25rem; font-size: var(--text-xs); text-align: right; }
 /* With the balance on the line the description may shrink further before the line wraps: a
    description is cut to an ellipsis and opens in full, where a wrapped line costs height on
    every row. */
 .t-running .t-desc { flex-basis: 3rem; }
 .mk { flex: none; width: 1rem; font: 700 var(--text-sm)/1 var(--sans); text-align: center; color: var(--ink-2); }
 .mk.c { color: var(--ok); }
 .mk.u { color: var(--warn); }
 .t-extra { padding: 0 0 var(--s2); font: var(--text-sm)/140% var(--sans); }
 .t-extra p { margin: var(--s1) 0; overflow-wrap: anywhere; }
 /* A copy of money counted elsewhere is history: the same line in the muted ink with a lighter
    figure, listed after the counted ones inside its own disclosure. */
 .acct-txns details.folded-rows { margin: var(--s3) 0 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .acct-txns details.folded-rows > summary { min-height: var(--hit); color: var(--ink-2); }
 .txns li.txn.folded { color: var(--ink-2); }
 .txns li.txn.folded .t-fig, .txns li.txn.folded .t-desc strong { font-weight: 400; }
 .txn.flagged { border-left: var(--rail) solid var(--bad); padding-left: var(--s3); }
 .txn.doubtful { border-left: var(--rail) solid var(--warn); padding-left: var(--s3); }

 /* A masked figure or text is a sealed slot; a shown one is plain. */
 .sealed { background: repeating-linear-gradient(135deg, var(--rule) 0 3px, transparent 3px 6px);
           border-radius: var(--s1); padding: 0 var(--s1); }
 /* Masked text is a pattern of letters, not a word; the fixed face says so, and the hatch is
    kept for figures so a page of descriptions does not read as a page of noise. */
 .txt.sealed { background: none; padding: 0; font-family: var(--mono); font-size: .9em;
               font-weight: 500; border-bottom: var(--rule-weight) dashed var(--edge); }

 /* The five folds, and the parts of one fold. */
 .ledger-more { margin-top: var(--s4); }
 .ledger-more > details { border-top: var(--rule-weight) solid var(--rule-2); }
 .ledger-more > details:last-of-type { border-bottom: var(--rule-weight) solid var(--rule-2); }
 .ledger-more > details > summary, .acct-month > details > summary, .acct-txns > details > summary { min-height: var(--hit); }
 .part { margin: var(--s3) 0 0; padding-top: var(--s1); border-top: var(--rule-weight) solid var(--rule-2); }
 .part h3 { margin: var(--s2) 0 var(--s1); }
 details.ledger-danger button.button.secondary { color: var(--bad); border-color: var(--bad); }

 @media (min-width: 60rem) {
  body.ledger-page { max-width: 64rem; }
  /* The transactions span the rows beside them and are far taller than those rows, and a grid
     shares a spanning item's height out among the rows it spans: the last row is the one that
     stretches, or the left-hand sections drift a screen apart. */
  .acct-grid { grid-template-columns: 24rem minmax(0, 1fr); column-gap: var(--s6); align-items: start;
               grid-template-areas: "head head" "state txns" "more txns";
               grid-template-rows: auto auto 1fr; }
  .acct-head { grid-area: head; }
  .acct-state { grid-area: state; }
  .acct-txns { grid-area: txns; }
  .ledger-more { grid-area: more; }
  .acct-txns .txhead h2 { margin-top: var(--s3); }
  .monthgrid { grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 2px; }
  .monthgrid li > a.tap, .monthgrid li > button.tap, .absent { padding: 0; font-size: var(--text-xs); }
 }
 @media (min-width: 76rem) {
  body.ledger-page { max-width: 76rem; }
  .t-row, .txns li.txn summary.t-row { column-gap: var(--s4); }
  .t-fig { min-width: 8rem; }
 }
"""
