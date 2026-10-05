"""The prototype's own rules: the NEW components, written with the product's tokens only.

Everything else on a prototype page is the product's real stylesheet (`obdi.stylesheet.STYLESHEET`),
which `build.py` puts in the page before this. No colour, face, or size is declared here that is
not one of its tokens, so light and dark both come from the product.

NEW COMPONENTS (each is marked NEW below):
  1. the one-row navigation (five destinations);
  2. the to-do row (`.todo`): what to do, for which account, why, how old, and its control;
  3. the trust bar (`.axis`, `.bar`, `.arow`, `.trust`): four fills for the four rungs of trust, on
     one 12-month scale, with the one-line answer it draws and the quiet line of evidence
     (`details.evidence`);
  4. the source strip (`.strip`): the trust bar with a lane beneath it for each source;
  5. the upload target (`.drop`) and the wanted list grouped by bank (`.bank`, `.want`);
  6. the one-line transaction (`.ptx`), which is today's row with the chips taken off the line.
"""

PROTO_CSS = """
 /* The page uses the width it is given: one column to 60rem, two from there, and from 76rem the
    lists inside the columns become single lines. */
 @media (min-width: 60rem) { body.proto { max-width: 64rem; } }
 @media (min-width: 76rem) { body.proto { max-width: 76rem; } }
 body.proto h2 { font-size: var(--text-lg); margin: var(--s5) 0 var(--s2); }
 body.proto h1 { margin: var(--s3) 0 var(--s1); }
 .wayout { margin: var(--s2) 0 0; }

 /* 1. NEW: five destinations in one row. */
 body.proto .sitenav ul { grid-template-columns: repeat(5, minmax(0, 1fr)); }
 body.proto .sitenav li:nth-child(n+5) a { border-top: 0; }

 /* An old date is the one thing in a quiet line that is not quiet. */
 .age { color: var(--warn); font-weight: 600; }
 .meta { margin: 0 0 var(--s2); font: var(--text-xs)/150% var(--sans); color: var(--ink-2); }
 .next { margin: var(--s1) 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .label { margin: var(--s4) 0 0; font: 700 var(--text-sm)/130% var(--sans); color: var(--ink-2); }

 /* 2. NEW: the to-do row. A rail says how pressing (red now, amber soon, grey when convenient;
    dashed where the need is inferred), then what to do, then one quiet line of which account, why,
    and how old. The control sits beside it on a list and beneath it where it leads a page. */
 .todos { list-style: none; margin: var(--s3) 0; padding: 0; display: grid; gap: var(--s3); }
 .todo { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: var(--s3); align-items: center;
         padding: 2px 0 2px var(--s3); border-left: var(--rail) solid var(--edge); }
 .todo.now { border-left-color: var(--bad); }
 .todo.soon { border-left-color: var(--warn); }
 .todo.guess { border-left-style: dashed; }
 /* An offer, not a demand: locking in what is already checked. Thinner rail, lighter title. */
 .todo.offer { border-left-color: var(--rule); }
 .todo.offer .todo-what { font-weight: 400; }
 .todo-text { min-width: 0; }
 .todo-what { margin: 0; font: 600 var(--text-base)/130% var(--serif); }
 .todo.now .todo-what { color: var(--bad); }
 .todo-why { margin: 2px 0 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .todo-why b { color: var(--ink); font-weight: 600; }
 .todo a.button, .todo button.button { margin: 0; min-height: var(--hit); padding: 0 var(--s3);
         font-size: var(--text-sm); white-space: nowrap; }
 .todo.lead { grid-template-columns: minmax(0, 1fr); row-gap: var(--s2); }
 .todo.lead a.button, .todo.lead button.button { min-height: var(--tap); font-size: 1rem; white-space: normal; }
 .todo-alt { grid-column: 1 / -1; margin: 0; display: flex; flex-wrap: wrap; gap: 0 var(--s4);
         font: var(--text-sm)/140% var(--sans); }
 .todo-alt a { display: inline-flex; align-items: center; min-height: var(--hit); }
 .todo details { grid-column: 1 / -1; }
 .todo details > summary { min-height: var(--hit); font-size: var(--text-sm); }
 .todo details p { margin: var(--s1) 0; font: var(--text-sm)/140% var(--sans); }
 .todo-form { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
         gap: var(--s2) var(--s3); margin: 0; }
 .todo-form.one { grid-template-columns: minmax(0, 1fr); }
 .todo-form label { display: block; }
 .todo-form button.button { grid-column: 1 / -1; }
 .pair { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--s2); }

 /* 3. NEW: THE TRUST BAR. How far an account can be trusted, drawn: four fills on ONE scale (the
    last twelve months, months named once), each a rung more solid than the one before it, so the
    ladder reads without colour: a bare line (nothing held), a hatch (held, not checked), an
    outline (checked), a solid (locked in). Two marks sit on it: red where the transactions do not
    add up or a locked stretch has changed, and a dashed amber block where a file is wanted. */
 .axis { position: relative; display: block; height: 1.25rem; font: var(--text-xs)/1 var(--sans); color: var(--ink-2); }
 .axis > span { position: absolute; top: 0; bottom: 0; display: flex; align-items: center;
         padding-left: 3px; border-left: var(--rule-weight) solid var(--rule); }
 .bar { position: relative; display: block; height: .75rem; }
 .bar::before { content: ""; position: absolute; left: 0; right: 0; top: 50%;
         border-top: var(--rule-weight) solid var(--edge); }
 .bar > i { position: absolute; top: 0; bottom: 0; }
 .b-locked { background: var(--ok); }
 .b-checked { background: var(--ok-bg); box-shadow: inset 0 0 0 1.5px var(--ok); }
 .b-held { background: repeating-linear-gradient(135deg, var(--edge) 0 1.5px, var(--paper) 1.5px 5px); }
 .b-bad { background: var(--bad); min-width: 5px; }
 .b-src { background: var(--ink-2); }
 .b-want { background: var(--paper); border: 1.5px dashed var(--warn); }
 .alist { list-style: none; margin: 0; padding: 0; border-top: var(--rule-weight) solid var(--rule); }
 .alist li { border-bottom: var(--rule-weight) solid var(--rule-2); }
 main a.arow { display: grid; grid-template-columns: minmax(0, 1fr) auto;
         grid-template-areas: "name flag" "bar bar" "trust trust"; gap: 3px var(--s2); align-items: baseline;
         min-height: var(--tap); padding: var(--s2) 0; color: var(--ink); text-decoration: none; }
 main a.arow::after { content: none; }
 .a-name { grid-area: name; font: 600 var(--text-base)/130% var(--serif); }
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

 /* The trust sentence: the one answer for an account. The part that needs him is the part in amber. */
 .trust { margin: var(--s2) 0 var(--s1); font: var(--text-base)/140% var(--serif); }
 .trust.bad { font: 600 var(--text-xl)/130% var(--serif); color: var(--bad); }
 .trust.bad::before { content: "\\2715\\00a0"; content: "\\2715\\00a0" / ""; }
 .trust.none { font: 600 var(--text-xl)/130% var(--serif); }
 .trust.none::before { content: "\\25CB\\00a0"; content: "\\25CB\\00a0" / ""; color: var(--warn); }
 .trust .sub { display: block; margin-top: var(--s1); font: 400 var(--text-md)/140% var(--sans); color: var(--ink); }

 /* The quiet line of evidence: it was looked at, and when. One muted line that opens to the detail. */
 details.evidence > summary { min-height: var(--hit); font: var(--text-xs)/140% var(--sans); color: var(--ink-2); }
 details.evidence > summary::before { width: .4rem; height: .4rem; border-color: var(--edge); }
 details.evidence p, details.evidence li { margin: 0 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 details.evidence ul { list-style: none; margin: 0; padding: 0; }

 /* 4. NEW: the source strip, the small version of the coverage timeline. The first lane is the
    trust bar; each lane after it is one source and the days it holds, on the same twelve months. */
 .strip { display: grid; grid-template-columns: 5.25rem minmax(0, 1fr); gap: var(--s2) var(--s2);
         align-items: center; margin: var(--s2) 0 0; }
 .strip .lane { font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .strip .lane.first { color: var(--ink); font-weight: 600; }
 .strip.mini { margin: var(--s1) 0 var(--s2); }

 /* 5. NEW: the upload target, and what is wanted, by bank. */
 .drop { margin: var(--s3) 0; padding: var(--s4); text-align: center; background: var(--card);
         border: var(--edge-weight) dashed var(--edge); border-radius: var(--radius); }
 .drop a.button, .drop button.button { margin: 0; }
 .drop p { margin: var(--s2) 0 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .drop.small { padding: var(--s3); }
 .bank { margin: var(--s4) 0 0; }
 .bank-name { display: flex; justify-content: space-between; align-items: baseline; gap: var(--s3); margin: 0;
         padding-bottom: var(--s1); border-bottom: var(--rule-weight) solid var(--rule);
         font: 600 var(--text-lg)/130% var(--serif); }
 .bank-count { font: 400 var(--text-sm)/130% var(--sans); color: var(--ink-2); white-space: nowrap; }
 .wants { list-style: none; margin: 0; padding: 0; }
 .want { display: grid; grid-template-columns: minmax(0, 1fr) auto;
         grid-template-areas: "period aside" "out out"; gap: 0 var(--s3); align-items: baseline;
         margin: var(--s2) 0 var(--s3); padding: 2px 0 2px var(--s3); border-left: var(--rail) solid var(--edge); }
 .want.guess { border-left-style: dashed; }
 .want-period { grid-area: period; font-size: var(--text-base); font-weight: 600; line-height: var(--leading-tight); }
 .want-out { grid-area: out; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .want-aside { grid-area: aside; font: var(--text-sm)/140% var(--sans); white-space: nowrap; }
 .quietlist { list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .quietlist li { padding: var(--s1) 0; }
 .quietlist b { color: var(--ink); font-weight: 600; }
 .files { list-style: none; margin: var(--s2) 0; padding: 0; }
 .files li { padding: var(--s2) 0; border-bottom: var(--rule-weight) solid var(--rule-2);
         font: var(--text-sm)/145% var(--sans); }
 .files li > span { display: block; color: var(--ink-2); }
 .settled { margin: var(--s3) 0; }

 /* 6. NEW: one transaction, one line: the day, the description, the figure, and one mark for
    whether a statement or the bank's own record has cleared it. Everything else is a tap away. */
 .ptx { list-style: none; margin: var(--s2) 0; padding: 0; border-top: var(--rule-weight) solid var(--rule); }
 .ptx li { display: grid; grid-template-columns: 3.5rem minmax(0, 1fr) auto 1rem; gap: 0 var(--s2);
         align-items: center; min-height: var(--hit); padding: var(--s1) 0;
         border-bottom: var(--rule-weight) solid var(--rule-2); }
 .ptx .when { font-size: var(--text-xs); color: var(--ink-2); }
 .ptx .fig { font-weight: 600; font-size: var(--text-sm); }
 .ptx .desc { overflow: hidden; white-space: nowrap; text-overflow: clip; }
 .ptx .src { display: none; font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .ptx .mk { font: 700 var(--text-sm)/1 var(--sans); text-align: center; }
 .ptx .mk.c { color: var(--ok); }
 .ptx .mk.u { color: var(--warn); }
 .ptx li.missing { grid-template-columns: minmax(0, 1fr); padding-left: var(--s3);
         border-left: var(--rail) solid var(--bad); font: var(--text-sm)/140% var(--sans); }
 .ptx li.missing b { color: var(--bad); }
 .txhead { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 0 var(--s4); }
 .txhead h2 { margin-bottom: 0; }
 .txcount { margin: 0 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .p-acct .sub { margin: 0 0 var(--s2); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .acct-wants { margin: var(--s3) 0 0; }
 .acct-wants > .who { margin: 0; font: 600 var(--text-base)/130% var(--serif); }
 .acct-wants > .who span { font: 400 var(--text-sm)/130% var(--sans); color: var(--ink-2); }
 .p-more > details { border-top: var(--rule-weight) solid var(--rule-2); }
 .p-more > details:last-child { border-bottom: var(--rule-weight) solid var(--rule-2); }
 .p-more > details p { margin: var(--s1) 0 var(--s3); font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .p-more { margin-top: var(--s5); }
 .morelist { list-style: none; margin: 0; padding: 0; border-top: var(--rule-weight) solid var(--rule); }
 .morelist li { padding: var(--s3) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .morelist a { font: 600 var(--text-base)/130% var(--serif); }
 .morelist p { margin: 2px 0 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 /* The key page: one rung to a row, its name, how it is drawn, what it means, what raises it. */
 .rungs { list-style: none; margin: 0; padding: 0; }
 .rung { padding: var(--s3) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .rung-name { margin: 0 0 var(--s2); font: 600 var(--text-base)/130% var(--serif); }
 .rung-means { margin: var(--s2) 0 0; font: var(--text-sm)/145% var(--sans); }
 .rung-next { margin: 2px 0 0; font: var(--text-sm)/145% var(--sans); color: var(--ink-2); }

 @media (min-width: 60rem) {
  .p-acct, .p-bring { display: grid; grid-template-columns: minmax(0, 24rem) minmax(0, 1fr); gap: 0 var(--s6);
         align-items: start; }
  .p-acct { grid-template-areas: "head head" "state txns" "more txns"; }
  .p-acct > .p-head { grid-area: head; }
  .p-acct > .p-state { grid-area: state; }
  .p-acct > .p-txns { grid-area: txns; }
  .p-acct > .p-more { grid-area: more; }
  .p-acct > .p-txns h2, .p-bring > .p-wanted > h2:first-child { margin-top: var(--s3); }
  body.proto .home { grid-template-columns: minmax(0, 24rem) minmax(0, 1fr); }
 }
 @media (min-width: 76rem) {
  main a.arow, .axis-row { grid-template-columns: 11rem minmax(0, 1fr) 10.5rem; gap: 2px var(--s4); align-items: center; }
  main a.arow { grid-template-areas: "name bar flag" ". trust trust"; }
  .axis-row > .axis { grid-column: 2; }
  .alist li.space { padding-left: 0; }
  .alist li.space .a-name { padding-left: var(--s4); }
  .want { grid-template-columns: 15rem minmax(0, 1fr) auto; grid-template-areas: "period out aside"; }
  .ptx li { grid-template-columns: 4rem minmax(0, 1fr) 11rem auto 1rem; gap: 0 var(--s4); }
  .ptx .src { display: block; }
 }
"""
