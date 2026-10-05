# ruff: noqa: E501
# The text below is CSS; a rule reads best as one declaration block, and the tests that guard
# the stylesheet read it by shape.
"""The Actual page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour,
face, or size is declared in this file. The page's one verdict is the loudest thing on it
when it calls for something, in the display serif, and quiet when it says Actual agrees;
it has a rail in the colour of what it says; the steps behind it are a
real sequence and so a vertical one; the controls that delete stand in one bordered block.
"""

ACTUAL_STYLES = r"""
 /* The verdict: a serif sentence, a rail in the colour of its meaning, and a glyph, so it
    reads in greyscale. Teal verified, red disagreement or failure, amber unproven or held. */
 .actual-main .verdict { margin: var(--s4) 0 var(--s3); padding: var(--s1) 0 var(--s1) var(--s4);
            border-left: var(--rail) solid var(--rule); }
 .actual-main .verdict h2 { margin: 0 0 var(--s2); font: 600 var(--text-2xl)/var(--leading-tight) var(--serif); }
 .actual-main .verdict h2::before { margin-right: var(--s2); font-family: var(--sans); }
 .actual-main .verdict > p { margin: var(--s1) 0; }
 /* Agreement is said in ordinary type beside a teal tick, not at display size: a loud "all
    is well" competes with the states that need the reader (see the account page's verdict). */
 .actual-main .verdict-ok { border-left-color: var(--ok); }
 .actual-main .verdict-ok h2 { font: 600 var(--text-md)/var(--leading-tight) var(--sans); color: var(--ink); }
 .actual-main .verdict-ok h2::before { content: "\2713"; content: "\2713" / ""; color: var(--ok); }
 .actual-main .verdict-bad { border-left-color: var(--bad); }
 .actual-main .verdict-bad h2 { color: var(--bad); }
 .actual-main .verdict-bad h2::before { content: "\2715"; content: "\2715" / ""; }
 .actual-main .verdict-warn { border-left-color: var(--warn); }
 .actual-main .verdict-warn h2 { color: var(--warn); }
 .actual-main .verdict-warn h2::before { content: "\25CB"; content: "\25CB" / ""; }
 .actual-main .verdict-quiet h2::before { content: "\2022"; content: "\2022" / ""; color: var(--ink-2); }
 /* The remedy sits inside the verdict that calls for it: not a box of its own. */
 .remedy { margin: var(--s3) 0 0; }
 .remedy details summary { min-height: var(--hit); }
 /* The presses: what a push is about to do, then the three buttons, the named one filled. */
 .presses { margin: var(--s4) 0; }
 .precheck { margin: 0 0 var(--s1); color: var(--ink-2); font: var(--text-sm)/150% var(--sans); }
 .presses form { margin: 0; }
 .presses form > p { margin: 0 0 var(--s2); font: var(--text-sm)/140% var(--sans); }
 /* The steps: a rail with a node for each, filled where the step is done. The word beside
    each node carries the state, so the node is only the picture of it. */
 .chain-block h2 { margin-top: var(--s4); }
 .chain-block > p { margin: 0; }
 .chain { list-style: none; margin: var(--s2) 0; padding: 0; }
 .step { position: relative; margin: 0; padding: 0 0 var(--s3) var(--s5); }
 .step::before { content: ""; position: absolute; left: 0; top: .3rem; width: 1rem; height: 1rem;
                 border-radius: 50%; border: var(--edge-weight) solid var(--edge); background: var(--paper); }
 .step::after { content: ""; position: absolute; left: .4375rem; top: 1.5rem; bottom: .25rem; width: var(--edge-weight);
                background: var(--rule); }
 .step:last-child { padding-bottom: 0; }
 .step:last-child::after { content: none; }
 .step-ok::before { border-color: var(--ok); background: var(--ok); }
 .step-bad::before { border-color: var(--bad); background: var(--bad); }
 .step-warn::before { border-color: var(--warn); background: var(--warn-bg); }
 .step-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s2); margin: 0;
              font: var(--text-md)/140% var(--sans); }
 .step-detail { margin: var(--s1) 0 0; color: var(--ink-2); font-size: var(--text-md); }
 /* Results: the audit as a sentence, the accounts that differ open, the rest behind a fold. */
 .newest-audit h2, .earlier h2 { margin-top: var(--s5); }
 .audit-head { margin: 0; font: var(--text-md)/140% var(--sans); }
 .audit-summary { margin: var(--s1) 0 var(--s3); font: 600 var(--text-lg)/var(--leading-tight) var(--serif); }
 .differ { margin: var(--s3) 0; padding: var(--s1) 0 var(--s1) var(--s3); border-left: var(--rail) solid var(--bad); }
 .differ h4 { margin: 0 0 var(--s1); font-size: var(--text-base); }
 .differ ul { margin: var(--s1) 0; padding-left: var(--s4); }
 .differ li { margin: var(--s1) 0; }
 .differ details { margin-top: var(--s1); }
 .agree-list { list-style: none; margin: 0; padding: 0; font: var(--text-sm)/150% var(--sans); }
 .agree-list li { padding: var(--s1) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .recent { list-style: none; margin: 0; padding: 0; }
 .recent > li { border-top: var(--rule-weight) solid var(--rule-2); }
 .recent summary { flex-wrap: wrap; gap: var(--s1) var(--s2); }
 /* The foot: the two controls that delete from Actual, together, bordered, and nothing else
    beside them. */
 .danger-zone { margin: var(--s6) 0 var(--s4); padding: 0 var(--s4) var(--s3); border: var(--edge-weight) solid var(--bad);
                border-radius: var(--radius); }
 .danger-zone > h2 { color: var(--bad); }
 .danger-item { margin-top: var(--s3); padding-top: var(--s2); border-top: var(--rule-weight) solid var(--rule); }
 .danger-what { margin: 0 0 var(--s1); }
 /* The emptying's summary is a bold name and a parenthesis, which a flex row cannot shrink
    below the sum of its longest words at large text. */
 .danger-zone summary { flex-wrap: wrap; gap: 0 var(--s2); }
 /* A text link in the results is a full thumb-height target, not only the reach of its pseudo-element. */
 .actual-side a.tap { display: inline-flex; align-items: center; min-height: var(--hit); }
 /* The page's two columns, where there is room: the verdict and what to press on the left,
    what the audit found on the right. */
 @media (min-width: 64rem) {
  .actual-layout { display: grid; grid-template-columns: minmax(0, 26rem) minmax(0, 1fr);
                   gap: 0 var(--s6); align-items: start; }
  .actual-main, .actual-side { min-width: 0; }
  .actual-side .newest-audit h2:first-child { margin-top: var(--s4); }
 }
"""
