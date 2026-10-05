# ruff: noqa: E501
"""The coverage timeline's rules, joined into the one stylesheet by `stylesheet`.

Written with the shared tokens: no colour, face, or size is declared here beyond what a token
carries, every rule leads with a class of the timeline's own (`cov-`), and no figure of two
decimals ends a number. The stylesheet carries no opacity at all (muted is a colour, and a test
holds that), so how faint a fill is rides on the element as an attribute, from one table in
`web_coverage_timeline`. A chart element's colour comes from a class so that the dark scheme
reaches it: green is verified, amber is look, red is rows that disagree, and the blue ink that
is the colour of an action is also the colour of "a source reaches here", which is not
verification and so is never green.
"""

TIMELINE_STYLES = """
 .cov-verdict { font-size: var(--text-lg); margin: var(--s2) 0 var(--s3); }
 .cov-frame { display: flex; align-items: flex-start; margin: var(--s3) 0; border: var(--rule-weight) solid var(--rule); border-radius: var(--radius); background: var(--card); }
 .cov-labels { flex: 0 0 auto; width: 7rem; font: var(--text-xs)/120% var(--sans); border-right: var(--rule-weight) solid var(--rule); }
 .cov-label { display: flex; flex-direction: column; justify-content: center; padding: 0 var(--s2); border-bottom: var(--rule-weight) solid var(--rule-2); overflow: hidden; }
 .cov-label small { font: var(--text-xs)/120% var(--mono); color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; }
 .cov-label a { color: inherit; }
 .cov-scroll { flex: 1 1 0; min-width: 0; overflow-x: auto; }
 .cov-scroll svg { display: block; max-width: none; }
 .cov-fit { flex: 1 1 0; min-width: 0; }
 .cov-fit svg { display: block; width: 100%; height: auto; }
 .cov-svg text { font: var(--text-xs) var(--sans); fill: var(--ink); }
 .cov-svg .cov-dim { fill: var(--ink-2); }
 .cov-rule { stroke: var(--rule-2); stroke-width: 1; }
 .cov-rule-year { stroke: var(--edge); stroke-width: 1; }
 .cov-today { stroke: var(--act); stroke-width: 2; }
 .cov-hatch-line { stroke: var(--rule); stroke-width: 1; }
 .cov-band-held { fill: var(--warn); }
 .cov-band-conflict { fill: var(--bad); }
 .cov-band-protected { fill: var(--ink-2); }
 .cov-ver-agrees { fill: var(--ok); }
 .cov-ver-held { fill: var(--warn); }
 .cov-ver-none { fill: var(--rule); }
 .cov-bar { fill: var(--act); }
 .cov-bar-possible { fill: var(--act); stroke: var(--act); stroke-width: 1; stroke-dasharray: 3 2; }
 .cov-listed { fill: var(--act); }
 .cov-edge-stated { stroke: var(--act); stroke-width: 3; }
 .cov-edge-asked { stroke: var(--act); stroke-width: 2; }
 .cov-edge-meets { stroke: var(--act); stroke-width: 2; stroke-dasharray: 1 3; }
 .cov-edge-observed { stroke: var(--act); stroke-width: 2; stroke-dasharray: 3 3; }
 .cov-next { margin: var(--s1) 0 var(--s2); padding-left: var(--s5); font: var(--text-md)/140% var(--sans); }
 .cov-next a { display: inline-flex; align-items: center; min-height: var(--hit); }
 .cov-keybox { margin: var(--s2) 0; }
 .cov-keybox summary { min-height: var(--hit); display: flex; align-items: center; }
 .cov-notch { stroke: var(--ink); stroke-width: 1; }
 .cov-gap { fill: none; stroke: var(--warn); stroke-width: 2; stroke-dasharray: 5 3; }
 .cov-unavailable-line { stroke: var(--rule); stroke-width: 1; }
 .cov-expected-mark { fill: var(--card); stroke: var(--ink-2); stroke-width: 1; }
 .cov-seam-red { fill: var(--bad); stroke: var(--bad); stroke-width: 1; }
 .cov-seam-amber { fill: var(--card); stroke: var(--warn); stroke-width: 2; }
 .cov-known-ok { stroke: var(--ok); stroke-width: 2; }
 .cov-known-bad { stroke: var(--bad); stroke-width: 2; }
 .cov-known-conflict { stroke: var(--warn); stroke-width: 2; }
 .cov-mark-red { fill: var(--bad); stroke: var(--bad); stroke-width: 1; }
 .cov-mark-amber { fill: var(--warn); stroke: var(--warn); stroke-width: 1; }
 .cov-mark-amber-hollow { fill: var(--card); stroke: var(--warn); stroke-width: 2; }
 .cov-svg a:target .cov-focus, .cov-svg a:focus .cov-focus { stroke-width: 4; }
 .cov-svg a { cursor: pointer; }
 .cov-key { display: flex; flex-wrap: wrap; gap: var(--s2) var(--s4); list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-sm)/130% var(--sans); }
 .cov-key li { display: flex; align-items: center; gap: var(--s2); }
 .cov-key svg { flex: none; }
 .cov-key-edge { margin: var(--s2) 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .cov-entries h3 { margin-top: var(--s4); }
 .cov-entry { padding: var(--s2) var(--s2); margin: 0; border-left: var(--edge-weight) solid var(--rule); font: var(--text-md)/140% var(--sans); }
 .cov-entry:target { background: var(--warn-bg); border-left-color: var(--warn); }
 .cov-entry p { margin: var(--s1) 0; }
 .cov-links { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s3); margin: var(--s1) 0 0; padding: 0; list-style: none; font-size: var(--text-sm); }
 .cov-links a { display: inline-flex; align-items: center; min-height: var(--hit); }
 .cov-household { list-style: none; margin: var(--s3) 0; padding: 0; }
 .cov-household li { margin: var(--s2) 0; }
 .cov-lane-name { font: 600 var(--text-sm) var(--sans); margin: 0 0 var(--s1); }
 .cov-compact summary { min-height: var(--hit); display: flex; align-items: center; }
 @media (min-width: 40rem) {
  .cov-labels { width: 9rem; }
 }
"""
