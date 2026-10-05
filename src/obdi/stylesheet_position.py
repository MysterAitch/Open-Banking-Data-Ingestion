"""The Position page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens: no colour, face, or
size is declared in this file, and every rule leads with a class of this page's own. The
window control the page shares with other charts is `stylesheet_window`'s.
"""

POSITION_STYLES = """
 .position-ticks { margin: var(--s2) 0; }
"""
