# ruff: noqa: E501
"""The What to fetch next page's own rules, joined into the one stylesheet by `stylesheet`.

Written with the shared tokens only: no colour, face, or size is declared here, and every rule
leads with a class of this page's own.

THE SHAPE. An account is a heading and a list of slips, one for each file to fetch. A slip leads
with the dates the owner types into a bank's site, in the monospace face at the largest size the
page uses, because that is the one thing read off the screen and typed somewhere else; the
instruction and the reason follow in the page's ordinary type, the reason quieter. A rail on the
slip's edge tells fact from guess: solid where the store holds the evidence, dashed where the gap
is inferred from how regularly the statements arrive, so the two differ without a word being read.
"""

GAPS_STYLES = """
 .gaps-verdict { font: 600 var(--text-lg)/var(--leading-tight) var(--serif); }
 .gaps-account { margin: var(--s5) 0; }
 .gaps-name { margin: 0 0 var(--s2); font: 600 var(--text-xl)/var(--leading-tight) var(--serif); }
 .gaps-list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); }
 .gaps-item { background: var(--card); border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
              padding: var(--s3) var(--s4); border-left-width: var(--rail); border-left-color: var(--warn); }
 .gaps-item.gaps-inferred { border-left-style: dashed; }
 .gaps-item p { margin: var(--s1) 0; }
 .gaps-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s2); }
 .gaps-kind { font: 600 var(--text-sm)/150% var(--sans); }
 .gaps-source { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .gaps-item .gaps-range { font-size: var(--text-xl); font-weight: 600; line-height: var(--leading-tight); margin: var(--s2) 0 var(--s1); overflow-wrap: anywhere; }
 .gaps-do { font-size: var(--text-base); line-height: var(--leading-prose); }
 .gaps-why { font: var(--text-md)/150% var(--sans); color: var(--ink-2); }
 .gaps-actions, .gaps-links { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4); margin: var(--s2) 0 0; }
 .gaps-item .gaps-links { margin: var(--s1) 0 0; }
 .gaps-quiet-head { margin: var(--s5) 0 var(--s2); font: 600 var(--text-lg)/var(--leading-tight) var(--serif); }
 .gaps-quiet { list-style: none; margin: 0; padding: 0; font: var(--text-md)/150% var(--sans); }
 .gaps-quiet-item { padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .gaps-notes { margin-top: var(--s5); font-size: var(--text-sm); }
 @media (min-width: 60rem) {
  body.gaps-page { max-width: 56rem; }
 }
"""
