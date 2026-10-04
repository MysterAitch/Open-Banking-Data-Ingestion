"""An account's page's own rules, joined into the one stylesheet by `stylesheet`.

Only what no other page uses belongs here, written with the shared tokens:
no colour, face, or size is declared in this file.
"""

ACCOUNT_STYLES = """
 /* THE PROOF RAIL'S RULES, SHARED: `proof_rail.rail_svg` names no colour and gives each part one
    of these classes, so every page that draws a rail uses this block as it stands. They belong
    in the shared rules once a second page carries one. */
 svg.rail { display: block; width: 100%; overflow: visible; }
 .rail-agree { fill: var(--ok); }
 .rail-break { fill: var(--bad); }
 .rail-hatch { stroke-width: 1.5px; fill: none; }
 .rail-hatch-unproven { stroke: var(--warn); }
 .rail-hatch-unknown { stroke: var(--edge); }
 .rail-mark { fill: var(--ink); }
 .rail-mark-broken { fill: var(--bad); }
"""
