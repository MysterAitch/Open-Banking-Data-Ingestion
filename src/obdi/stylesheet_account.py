# ruff: noqa: E501
"""An account's page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens:
no colour, face, or size is declared in this file.

THE SHAPE. On a phone the page is one column, in the order a person asks their questions: whose
account, how far it is verified, what holds it back, what can be done, then the month's
transactions, and everything else folded beneath them. From 60rem the same markup is two columns:
the state, the month, and the folded sections in a narrow one, and the transactions in a wide
one beside them, as a table-like list of date, description, figure, and sources.

SEALED. A masked figure or masked text carries `.sealed`, drawn as a hatched slot, and a shown
one is plain: the state of the page is visible at a glance without reading the banner. The
hatch is a rule colour at a fixed pitch, and the characters inside it are never hidden, so the
slot still says how long the thing is.

RAIL, NOT BOX. A row that is flagged or doubtful has a rail at its edge, as the Overview's
attention list does; the one tinted box on the page is the account's own hold, the thing to act on.
"""

ACCOUNT_STYLES = """
 /* THE PROOF RAIL'S RULES, SHARED: `proof_rail.rail_svg` names no colour and gives each part one
    of these classes, so every page that draws a rail uses this block as it stands. They belong
    in the shared rules once a second page carries one. */
 svg.rail { display: block; width: 100%; overflow: visible; }
 .rail-agree { fill: var(--ok); }
 .rail-break { fill: var(--bad); }
 .rail-hatch { stroke-width: 1.5px; fill: none; }
 .rail-hatch-unproven { stroke: var(--warn); }
 .rail-hatch-unknown { stroke: var(--edge); }
 .rail-mark { fill: var(--ink); }
 .rail-mark-broken { fill: var(--bad); }

 /* Where the account page begins: its four places stack on a phone. */
 .acct-grid { display: grid; grid-template-columns: minmax(0, 1fr); }
 .acct-grid > * { min-width: 0; }
 .ledger-page h1 { margin: var(--s4) 0 var(--s1); }
 .ledger-page h2 { font-size: var(--text-lg); margin: var(--s4) 0 var(--s2); }
 .ledger-page .sub { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); margin: var(--s1) 0; }
 .ledger-page .ref { margin: 0; font-size: var(--text-sm); color: var(--ink-2); }

 /* The rail, and the two dates it runs between. */
 .proof { margin: var(--s3) 0 var(--s2); }
 .rail-ends { display: flex; justify-content: space-between; font-size: var(--text-xs); color: var(--ink-2); }

 /* The verdict is the page's one display sentence. It is teal only where it is a claim of
    agreement with nothing held back, and carries a glyph beside the colour. */
 .verdict { font: 600 var(--text-xl)/130% var(--serif); margin: var(--s3) 0; }
 .verdict.clear { color: var(--ok); }
 .verdict.clear::before { content: "\\2713\\00a0"; content: "\\2713\\00a0" / ""; }
 .verdict.warn::before { content: "\\25CB\\00a0"; content: "\\25CB\\00a0" / ""; }

 /* The one tinted box: what holds the account back, and the way to the explanation. */
 .held { margin: var(--s3) 0; padding: var(--s3) var(--s4); background: var(--bad-bg); border-radius: var(--radius); }
 .held p { margin: var(--s1) 0; }
 .held strong { font: 600 var(--text-lg)/130% var(--serif); color: var(--bad); }
 .held strong::before { content: "\\2715\\00a0"; content: "\\2715\\00a0" / ""; }

 /* Protection: a quiet line, and the control for the state it describes. */
 .protect { margin: var(--s3) 0; }
 .protect-line { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); margin: var(--s1) 0; }
 .protect .button { margin: var(--s2) 0; }
 .shown { border: var(--edge-weight) solid currentColor; padding: var(--s3); border-radius: var(--radius); }

 /* Months. */
 .acct-month .monthnav { margin: 0 0 var(--s1); }
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

 /* A transaction: description and figure, then the date, then what is known of it. */
 .txns li.txn { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px var(--s3);
                padding: var(--s2) 0;
                grid-template-areas: "desc fig" "when more" "chips chips" "note note" "open open"; }
 .t-desc { grid-area: desc; overflow-wrap: anywhere; line-height: 130%; }
 .t-fig { grid-area: fig; align-self: start; font-weight: 600; text-align: right; }
 .t-when { grid-area: when; color: var(--ink-2); line-height: 130%; }
 .t-chips { grid-area: chips; }
 .txns li.txn > .t-note { grid-area: note; margin: 0; }
 /* The row's own disclosure shares the date's line while closed, so a month of rows is not a
    month of extra lines; its hit area is the full tap height, reached by negative margins that
    leave its layout height at one line. Open, it takes a line of its own. */
 .txns li.txn > .t-more:not([open]) { grid-area: more; }
 .txns li.txn > .t-more[open] { grid-area: open; }
 .txns li.txn > .t-more summary { min-height: var(--hit); font-size: var(--text-xs); justify-content: flex-end; }
 .txns li.txn > .t-more:not([open]) > summary { margin: -.75rem 0; }
 .txn.flagged { border-left: var(--rail) solid var(--bad); padding-left: var(--s3); }
 .txn.doubtful { border-left: var(--rail) solid var(--warn); padding-left: var(--s3); }

 /* A masked figure or text is a sealed slot; a shown one is plain. */
 .sealed { background: repeating-linear-gradient(135deg, var(--rule) 0 3px, transparent 3px 6px);
           border-radius: var(--s1); padding: 0 var(--s1); }
 /* Masked text is a pattern of letters, not a word; the fixed face says so, and the hatch is
    kept for figures so a page of descriptions does not read as a page of noise. */
 .txt.sealed { background: none; padding: 0; font-family: var(--mono); font-size: .9em;
               font-weight: 500; border-bottom: var(--rule-weight) dashed var(--edge); }

 /* The folded sections, and the one bordered place for what removes something. */
 .acct-more { margin-top: var(--s4); }
 .acct-more > details { border-top: var(--rule-weight) solid var(--rule-2); }
 details.danger-zone { margin: var(--s5) 0 0; padding: 0 var(--s4); border: var(--rule-weight) solid var(--bad); border-radius: var(--radius); }
 details.danger-zone button.button.secondary { color: var(--bad); border-color: var(--bad); }
 details.danger-zone > summary { color: var(--bad); font-weight: 600; }
 details.danger-zone > summary::before { border-color: var(--bad); }

 @media (min-width: 60rem) {
  body.ledger-page { max-width: 80rem; }
  .acct-grid { grid-template-columns: 32rem minmax(0, 1fr); column-gap: var(--s6); align-items: start;
               grid-template-areas: "head head" "state txns" "month txns" "more txns"; }
  .acct-head { grid-area: head; }
  .acct-state { grid-area: state; }
  .acct-month { grid-area: month; }
  .acct-txns { grid-area: txns; }
  .acct-more { grid-area: more; }
  .acct-txns h2 { margin-top: var(--s4); }
  .monthgrid { grid-template-columns: repeat(12, minmax(0, 1fr)); }
  .year { grid-template-columns: 3rem minmax(0, 1fr); }
  .txns li.txn { grid-template-columns: 10rem minmax(0, 1fr) auto minmax(9rem, 16rem) auto;
                 grid-template-areas: "when desc fig chips more" ". note note note note" ". open open open open";
                 align-items: baseline; }
  .t-chips { justify-content: flex-end; }
 }
"""
