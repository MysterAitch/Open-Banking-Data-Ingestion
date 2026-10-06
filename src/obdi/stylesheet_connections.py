# ruff: noqa: E501
"""The Connections page's own rules, joined into the one stylesheet by `stylesheet`.

Written with the shared tokens only. The page is two short lists under two headings, so a source
is a name and one sentence, in the tone of what the sentence says: a consent with time is quiet
text, one running out is the warning colour, one that has ended is the failure colour. What needs
a person is a to-do row above both lists (`stylesheet_home`'s `.todo`), and the fold of controls
for the occasions that want them is as quiet as the evidence line.
"""

CONNECTIONS_STYLES = r"""
 .conn-need { margin: 0 0 var(--s4); }
 .conn-out, .conn-in { margin: var(--s4) 0; }
 .out-verdict { margin: 0 0 var(--s1); }
 .out-verdict.bad { color: var(--bad); }
 .out-verdict.warn { color: var(--warn); }
 .sources { list-style: none; margin: var(--s2) 0; padding: 0; }
 .source { padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .source p { margin: 0; }
 .source-name { font: 600 var(--text-base)/130% var(--serif); }
 .source-state { font: var(--text-md)/150% var(--sans); overflow-wrap: anywhere; }
 .source-state.warn { color: var(--warn); }
 .source-state.bad { color: var(--bad); }
 .conn-files { margin: var(--s2) 0 0; }
 details.manage { margin: var(--s4) 0 var(--s2); }
 details.manage > summary { min-height: var(--hit); font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
"""
