# ruff: noqa: E501
"""The recurring-things page's own rules, joined into the one stylesheet by `stylesheet`.

One line to a series, laid out for a phone first: the name and the sealed figure on the first
line, what is known of its rhythm and what is the matter with it on the second. A series that
needs a look (stopped, changed) carries the page's one rail, the same amber the account page
puts beside a doubtful row, so the eye lands on those and the rest read as a quiet list.
No colour, face, or size is declared here: the shared tokens only.
"""

RECURRING_STYLES = """
 .recur-summary { font: 600 var(--text-md)/140% var(--sans); margin: var(--s3) 0; }
 .recur-account h2 { margin: var(--s2) 0 0; }
 .recur-list { list-style: none; margin: 0; padding: 0; }
 /* Tight on purpose: thirty lines have to fit three phone screens (`test_recurring_phone_layout`). */
 .recur-row { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
              gap: 0 var(--s3); padding: var(--s1) 0; border-top: var(--rule-weight) solid var(--rule-2);
              line-height: 130%; }
 .recur-row.recur-attn { border-left: var(--rail) solid var(--warn); padding-left: var(--s3); }
 .recur-name { min-width: 0; overflow-wrap: anywhere; }
 .recur-fig { white-space: nowrap; font-weight: 600; }
 .recur-how { flex: 1 0 100%; font: var(--text-sm)/140% var(--sans); color: var(--ink-2); }
 .recur-how .pill { margin-left: var(--s1); line-height: 130%; white-space: nowrap; }
"""
