# ruff: noqa: E501
"""The long diagnostic pages' own rules (the Actual history, the fetch attempts, the period
reconciliation, the kept statements), joined into the one stylesheet by `stylesheet`.

Written with the shared tokens only. Each of those pages is a one-sentence purpose, a summary
that answers it, and the record beneath in folds: so the shapes are a plain list of short lines
(`.diag-lines`), a list of the summary's facts (`.diag-kinds`), and a ruled line that carries one
group's name and count at the head of a fold. A line that went wrong is in the failure colour and
a line that went well stays quiet, as everywhere.
"""

DIAGNOSTICS_STYLES = r"""
 .diag-purpose { margin: 0 0 var(--s3); }
 .diag-summary { margin: var(--s3) 0; }
 .diag-kinds, .diag-needs, .diag-lines { list-style: none; margin: var(--s2) 0; padding: 0; }
 .diag-kinds li, .diag-needs li { padding: var(--s1) 0; font: var(--text-md)/150% var(--sans); }
 .diag-lines li { padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); font: var(--text-md)/150% var(--sans); overflow-wrap: anywhere; }
 .diag-lines li.bad, .diag-needs li.bad { color: var(--bad); }
 .diag-lines li.muted { color: var(--ink-2); }
 .diag-detail { margin: var(--s4) 0; }
 .diag-detail > details { border-top: var(--rule-weight) solid var(--rule-2); }
 .diag-legend { margin: var(--s2) 0; }
 .diag-legend dt { font-weight: 600; margin-top: var(--s2); }
 .diag-legend dd { margin: 0; }
"""
