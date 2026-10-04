# ruff: noqa: E501
"""The home page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens:
no colour, face, or size is declared in this file.

THE SHAPE OF THE PAGE. A phone column read top to bottom: a verdict in the display serif with a
mark in the colour of its tone, four status lines, what needs attention by band, then one thin
row per account. Where there is room (`min-width: 60rem`) the verdict, status lines, and
attention sit in a column beside the accounts, because the accounts list is the long part.

A ROW IS ONE TAP. The whole account row is a link to the ledger; the disclosure of its other
facts is a 44px button at the row's end, placed over the link's reserved padding by absolute
position so a closed row costs no height for it. The rail is drawn by `proof_rail`, whose rules
are shared; only its place in the row is set here.
"""

HOME_STYLES = """
 /* The verdict is the page's heading; the h1 stays for a screen reader and for the title, and
    costs the first screen nothing. */
 main:has(.home) > h1 { position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip-path: inset(50%); white-space: nowrap; margin: 0; }
 .verdict { display: flex; align-items: flex-start; gap: var(--s3); margin: var(--s4) 0 var(--s2);
        font: 600 var(--text-2xl)/130% var(--serif); color: var(--ink); }
 .verdict span { min-width: 0; overflow-wrap: anywhere; text-wrap: balance; }
 .verdict.long { font-size: var(--text-lg); }
 .verdict::before { flex: none; width: 1.75rem; height: 1.75rem; margin-top: .1rem; border-radius: 50%;
        border: var(--edge-weight) solid currentColor; display: grid; place-items: center;
        font: 700 var(--text-md)/1 var(--sans); }
 .verdict.ok::before { content: "\\2713"; content: "\\2713" / ""; color: var(--ok); }
 .verdict.warn::before { content: "!"; content: "!" / ""; color: var(--warn); }
 .verdict.bad::before { content: "\\2715"; content: "\\2715" / ""; color: var(--bad); }
 .verdict-lede { margin: 0 0 var(--s2); font: var(--text-sm)/140% var(--sans); }

 /* Four status lines: a label and a chip on one line, the sentence beneath, the whole row a link. */
 .status { list-style: none; margin: var(--s3) 0 0; padding: 0; border-top: var(--rule-weight) solid var(--rule); }
 .status li { border-bottom: var(--rule-weight) solid var(--rule-2); }
 a.tap.status-row { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto;
        gap: var(--s1) var(--s2); align-items: center; min-height: var(--tap);
        padding: var(--s2) 1.75rem var(--s2) 0; color: var(--ink); text-decoration: none; }
 a.tap.status-row::after { content: none; }
 a.tap.status-row::before { content: ""; position: absolute; right: .5rem; top: 50%; width: .5rem; height: .5rem;
        margin-top: -.25rem; border-right: var(--edge-weight) solid var(--act);
        border-top: var(--edge-weight) solid var(--act); transform: rotate(45deg); }
 .status-label { font: 700 var(--text-md)/130% var(--sans); }
 .status-row .pill { justify-self: end; white-space: nowrap; }
 .status-sentence { grid-column: 1 / -1; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .tz { margin: var(--s1) 0 0; font: var(--text-xs)/140% var(--sans); text-align: right; }

 /* What needs attention, by tier (the shared `.band` is the instance banner, so this is not
    called that). The tier's heading carries the glyph; each item carries the rail. */
 .tier { margin: var(--s3) 0; }
 .tier-title, .tier > summary { margin: 0; font: 700 var(--text-md)/130% var(--sans); color: var(--ink); }
 .tier-title { display: flex; align-items: baseline; gap: var(--s2); }
 .tier-title::before { flex: none; }
 .tier-now .tier-title::before { content: "\\2715"; content: "\\2715" / ""; color: var(--bad); }
 .tier-soon .tier-title::before { content: "\\25CB"; content: "\\25CB" / ""; color: var(--warn); }
 .tier-housekeeping .tier-title::before { content: "\\2022"; content: "\\2022" / ""; color: var(--ink-2); }
 details.tier { border-left: var(--rail) solid var(--rule); padding-left: var(--s3); }
 details.tier-now { border-left-color: var(--bad); }
 details.tier-soon { border-left-color: var(--warn); }
 .tier .attention { margin: var(--s1) 0 0; }
 .tier .attention li { margin: var(--s2) 0; }
 .item-message { font: var(--text-base)/140% var(--serif); }
 .item-do { font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .notes { list-style: none; margin: var(--s3) 0 0; padding: 0; font: var(--text-sm)/140% var(--sans); }
 .notes li { margin: var(--s2) 0; }

 /* One row per account: name and chip, the rail, then the clause. The row is the link. */
 .accounts-list, .spaces { list-style: none; margin: 0; padding: 0; }
 .acct { position: relative; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .spaces { margin-left: var(--s3); padding-left: var(--s2); border-left: var(--edge-weight) solid var(--rule); }
 .spaces .acct:last-child { border-bottom: 0; }
 a.tap.acct-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--s1) var(--s2);
        align-items: center; min-height: var(--tap); padding: var(--s2) calc(var(--hit) + var(--s1)) var(--s2) 0;
        color: var(--ink); text-decoration: none; }
 a.tap.acct-row::after { content: none; }
 .acct-name { font: 600 var(--text-base)/130% var(--serif); overflow-wrap: anywhere; }
 .acct-space > a .acct-name { font-size: var(--text-md); }
 .acct-row .pill { justify-self: end; white-space: nowrap; }
 .acct-rail { grid-column: 1 / -1; display: block; }
 .acct-sub { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 0 var(--s2);
        font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .acct-ref { font-size: var(--text-xs); }
 .acct-more > summary { position: absolute; top: 0; right: 0; width: var(--hit); height: var(--hit);
        min-height: var(--hit); padding: 0; justify-content: center; }
 .acct-more > summary::before { margin: 0; }
 .acct-more .facts, .acct-more > p { margin-left: 0; }
 .spaces-archived { margin: var(--s1) 0 0 var(--s3); }
 .spaces-archived > summary { font-size: var(--text-sm); }

 /* Beside the accounts where there is room; the rest of the page spans both. */
 .home-rest { margin-top: var(--s5); }
 @media (min-width: 60rem) {
  .home { display: grid; grid-template-columns: minmax(0, 26rem) minmax(0, 1fr); gap: 0 var(--s6); align-items: start; }
  .home-rest { grid-column: 1 / -1; }
  .home-accounts h2 { margin-top: var(--s4); }
 }
"""
