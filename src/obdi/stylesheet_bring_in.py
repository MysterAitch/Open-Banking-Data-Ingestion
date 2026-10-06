# ruff: noqa: E501
"""Bring in's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file. The to-do row, the trust bar, and the evidence line are Today's
(`stylesheet_home`); this adds the upload target, the account's block of wanted files, and the
strip of lanes beneath the bar.

THE SHAPE. A phone column: the upload target and the one evidence line first, then the files
wanted, grouped by account. An account is its name, how far it can be trusted in a sentence, its
bar with the statements and exports lanes beneath on the shared twelve months, and one quiet line
for each file wanted (what, the days, why in a few words, its age, and a link to set it aside).
From 60rem the target and what an upload settled sit in a column beside the wanted list, which
is the long part.
"""

BRING_IN_STYLES = """
 .bi { display: grid; grid-template-columns: minmax(0, 1fr); }
 .bi > * { min-width: 0; }
 .bi-drop form { margin: var(--s2) 0; }
 .bi-drop input[type="file"] { padding: var(--s2) var(--s3); }
 .bi-drop p { margin: var(--s1) 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .bi-drop button.button { margin: var(--s2) 0 0; width: 100%; }
 .bi-scoped { font: 600 var(--text-md)/130% var(--sans); color: var(--ink); }
 .bi-notice { margin: var(--s2) 0; padding: var(--s1) 0 var(--s1) var(--s3); border-left: var(--rail) solid var(--warn); font: var(--text-md)/140% var(--sans); }
 .bi-settled { margin: var(--s1) 0; }
 .bi-outcomes { list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-sm)/140% var(--sans); }
 .bi-outcomes li { margin: var(--s1) 0; overflow-wrap: anywhere; }
 .bi-ask { margin: var(--s3) 0; }
 .bi-ask .todo-form { grid-template-columns: minmax(0, 1fr); }
 .bi-ask .todo-form p { margin: 0; }
 .bi-ask .todo-form select, .bi-ask .todo-form input { width: 100%; }
 .bi-ask .todo { flex-direction: column; align-items: stretch; }
 .bi-ask .todo-text { flex: none; }
 .bi-assign { margin: var(--s3) 0; }
 .bi-assign h3 { margin: var(--s2) 0 var(--s1); font: 600 var(--text-md)/130% var(--sans); }
 .bi-assign-lead { margin: 0 0 var(--s2); }
 .bi-assign-list { list-style: none; margin: 0; padding: 0; }
 .bi-assign-file { padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .bi-assign-name { margin: 0 0 var(--s1); font: var(--text-sm)/130% var(--sans); overflow-wrap: anywhere; }
 .bi-assign-file select { width: 100%; min-height: var(--hit); }
 .bi-guess { margin: var(--s1) 0 0; font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .bi-preview { margin: 0 0 var(--s1); font: var(--text-xs)/140% var(--sans); color: var(--ink-2); }
 .bi-assign-bar { position: sticky; bottom: 0; padding: var(--s2) 0; background: var(--paper); border-top: var(--rule-weight) solid var(--rule); }
 .bi-assign-bar button.button { width: 100%; margin: 0; }
 .bi-fold { margin: var(--s3) 0; }
 .bi-fold > summary { min-height: var(--hit); display: flex; align-items: center; font: 600 var(--text-md)/130% var(--sans); cursor: pointer; }
 .bi-wanted h2 { margin: var(--s4) 0 var(--s2); font-size: var(--text-lg); }
 .bi-account { margin: var(--s3) 0 var(--s4); padding-top: var(--s2); border-top: var(--rule-weight) solid var(--rule-2); }
 .bi-who { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 0 var(--s3); margin: 0; font: 600 var(--text-base)/130% var(--serif); }
 .bi-who .muted { font: 400 var(--text-xs)/130% var(--sans); }
 .bi-who .bi-upload { margin-left: auto; font: var(--text-sm)/130% var(--sans); }
 .bi-trust { margin: var(--s1) 0 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .bi-strip { display: grid; grid-template-columns: 5.25rem minmax(0, 1fr); gap: var(--s1) var(--s2); align-items: center; margin: var(--s2) 0; }
 .bi-strip .axis { overflow: hidden; }
 .bi-strip .lane { font: var(--text-xs)/130% var(--sans); color: var(--ink-2); }
 .bi-strip .lane.first { color: var(--ink); font-weight: 600; }
 .bi-files { list-style: none; margin: 0; padding: 0; }
 .bi-file { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 0 var(--s3); padding: var(--s1) 0 var(--s1) var(--s3); border-left: var(--rail) solid var(--warn); margin: var(--s2) 0; }
 .bi-file.guess { border-left-style: dashed; }
 .bi-what { flex: 1 1 100%; margin: 0; font: var(--text-md)/130% var(--sans); }
 .bi-why { flex: 1 1 auto; margin: 0; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .bi-aside { font: var(--text-sm)/130% var(--sans); }
 .bi-door { display: flex; align-items: center; min-height: var(--hit); }
 .bi-links { margin: var(--s2) 0; font: var(--text-sm)/150% var(--sans); }
 @media (min-width: 60rem) {
  .bi { grid-template-columns: minmax(0, 24rem) minmax(0, 1fr); gap: 0 var(--s6); align-items: start; }
  .bi-wanted h2 { margin-top: var(--s2); }
 }
"""
