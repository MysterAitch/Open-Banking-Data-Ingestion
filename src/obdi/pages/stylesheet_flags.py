# ruff: noqa: E501
"""The review-flags page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file, and every rule leads with a class of this page's own.

THE SHAPE. A card is one question, drawn to be answered from where the evidence ends: the rows
being compared stand first, each a date and a status with how it was listed beneath, then what
the evidence says either way, then the answers. On a phone the answers are full-width and at
least a tap high and stacked; from 60rem they sit side by side. The amber rail at the card's
edge is the same doubt the account page marks on a flagged row, so a flag looks like one
everywhere it is shown.
"""

FLAGS_STYLES = """
 .flag-queue { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s4); margin: var(--s3) 0; }
 .flag-queue > * { min-width: 0; }
 .flag-lead { font: 600 var(--text-lg)/130% var(--serif); margin: var(--s3) 0 var(--s1); }
 .flag-card { background: var(--card); border: var(--rule-weight) solid var(--rule);
              border-left: var(--rail) solid var(--warn); border-radius: var(--radius);
              padding: var(--s3) var(--s4); }
 .flag-title { font: 600 var(--text-lg)/130% var(--serif); margin: 0; }
 .flag-n { display: block; font: 500 var(--text-xs)/150% var(--mono); color: var(--ink-2); }
 .flag-rows { list-style: none; margin: 0; padding: 0; }
 .flag-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--s2);
             padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .flag-row.flag-this { border-top: 0; padding-top: 0; }
 .flag-tag { font: 600 var(--text-sm)/150% var(--sans); min-width: 4rem; }
 .flag-this .flag-tag { color: var(--ink); }
 .flag-day { font-weight: 600; }
 .flag-gap { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .flag-how { flex: 1 0 100%; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .flag-values { flex: 1 0 100%; display: flex; justify-content: space-between; gap: var(--s3); }
 .flag-fig { font-weight: 600; }
 .flag-desc { overflow-wrap: anywhere; text-align: right; }
 .flag-says { margin: var(--s2) 0 0; padding-left: var(--s3); border-left: var(--rule-weight) solid var(--rule); }
 .flag-sub { font: 600 var(--text-sm)/130% var(--sans); color: var(--ink-2); margin: 0 0 var(--s1); }
 .flag-says p { margin: var(--s1) 0; font-size: var(--text-md); line-height: 140%; }
 .flag-doubt { margin: var(--s2) 0; font-size: var(--text-md); }
 .flag-answers { display: grid; grid-template-columns: minmax(0, 1fr); column-gap: var(--s3); margin-top: var(--s3);
                 border-top: var(--rule-weight) solid var(--rule-2); padding-top: var(--s2); }
 .flag-answers form p { margin: var(--s1) 0; }
 .flag-settled { margin: var(--s4) 0 var(--s2); }
 .flag-answered { list-style: none; margin: 0; padding: 0; }
 .flag-done { display: grid; grid-template-columns: minmax(0, 1fr) 6rem; align-items: center; gap: var(--s3);
              padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); font-size: var(--text-md); }
 .flag-done form p { margin: 0; }
 .flag-done button { min-height: var(--hit); }
 @media (min-width: 60rem) {
  body.flag-page { max-width: 64rem; }
  .flag-answers { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flag-answers form:only-child { grid-column: 1 / -1; max-width: 24rem; }
 }
"""
