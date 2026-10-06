# ruff: noqa: E501
"""The Position page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file, and every rule leads with a class of this page's own. The
window control the page shares with other charts is `stylesheet_window`'s.

The counted accounts are drawn with Today's list (`.alist`, `.arow`, the trust bar), so what is
here is only what the figure adds: its own trust line, the accounts resting on nothing checked,
and the balance an unmasked row carries in the slot Today gives an account's flag.
"""

POSITION_STYLES = """
 .position-ticks { margin: var(--s2) 0; }
 /* The figure's trust line is ordinary text; the accounts resting on nothing checked are the
    warning colour, since they are what the figure is least sure of. */
 .pos-rests { margin: var(--s1) 0; }
 .pos-unchecked { margin: var(--s1) 0; color: var(--warn); }
 /* An unmasked row's balance sits where Today puts an account's flag, on the name's line. */
 .pos-fig { grid-area: flag; text-align: right; font: 600 var(--text-sm)/130% var(--sans); }
 .pos-left-out { list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-md)/150% var(--sans); }
 .pos-left-out li { padding: var(--s1) 0; overflow-wrap: anywhere; }
"""
