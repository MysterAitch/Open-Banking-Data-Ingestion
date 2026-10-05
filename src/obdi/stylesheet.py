# ruff: noqa: E501
# The text below is CSS, and a rule reads best as one declaration block; wrapping
# it to the code's line length would make it harder to compare with the rules the
# tests read by shape.
"""The one stylesheet every page carries, inline, so a page renders from its own response.

DESIGN. A paper-coloured ground; serif for prose, verdicts, and headings; the
system sans for labels and chips; monospace with tabular numerals for figures,
dates, and ids. System font stacks only: no web font and no remote asset, so a
page needs nothing but itself.

EVERY colour, face, size, and space is a custom property defined once in the
token blocks at the top, with a light and a dark value. A change of direction is
a change of tokens; no colour literal appears anywhere below them. Colour means
ONE thing each:

- teal (`--ok`): verified;
- red (`--bad`): rows disagree, or an action failed;
- amber (`--warn`): unproven, or held back;
- grey (`--ink-2`, `--rule`): housekeeping;
- ink blue (`--act`): an action, a link, the place you are.

Every status also carries a glyph (see `.pill`), so it survives greyscale.

MUTED TEXT IS A COLOUR, NEVER OPACITY. Opacity composes when nested: a muted line
inside a muted block fell to 3.04:1. `tests/test_stylesheet.py` reads the tokens
and holds the contrast of every pairing the components use, in both schemes.

NO FIGURES OR THE WORD FOR MONEY HERE. This text is in every page, and pages that
must show no money are tested by searching them for a figure and for the word
itself, so neither may appear in the stylesheet. Write a size as a percentage, a
single decimal, or three decimals with a unit, never two decimals ending a
number. `tests/test_stylesheet.py` runs every such pattern over this text alone.

THE RULES THAT TESTS READ BY SHAPE keep their layout: `.sitenav a`, `.sitenav ul`,
`a.tap`, `a.button, button.button`, `label.tick`, and the `min-width: 60rem` media
block. Reformatting them breaks the tests that look for them, deliberately.

THE PAGES' OWN RULES LIVE BESIDE THIS FILE. A page whose layout needs rules that no
other page uses keeps them in a module of its own (`stylesheet_home`,
`stylesheet_account`, `stylesheet_actual`), each a string of CSS that uses these tokens
and declares no colour, face, or size of its own. They are joined to the shared rules
below into the one `STYLESHEET` every page carries, so the guards on the stylesheet
(contrast, no colour literal, no money-like text) cover them too.
"""

from .stylesheet_account import ACCOUNT_STYLES
from .stylesheet_actual import ACTUAL_STYLES
from .stylesheet_flags import FLAGS_STYLES
from .stylesheet_gaps import GAPS_STYLES
from .stylesheet_home import HOME_STYLES
from .stylesheet_position import POSITION_STYLES
from .stylesheet_sections import SECTION_STYLES
from .stylesheet_timeline import TIMELINE_STYLES
from .stylesheet_window import WINDOW_STYLES

SHARED_STYLES = """
 :root {
  color-scheme: light dark;
  --paper: #f1f4f2; --card: #fafcfb; --ink: #16202a; --ink-2: #4a5762;
  --rule: #c9d2ce; --rule-2: #dfe6e3; --edge: #7a8a84;
  --act: #1d3a86; --act-ink: #ffffff; --focus: #1d3a86;
  --ok: #0a6a56; --ok-bg: #d9eee7;
  --bad: #a5211b; --bad-bg: #f7dfdc;
  --warn: #7c4f00; --warn-bg: #f5e9cd;
  --serif: "Iowan Old Style", "Charter", "Palatino Linotype", Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --text-xs: .75rem; --text-sm: .875rem; --text-md: .9375rem; --text-base: 1.0625rem;
  --text-lg: 1.1875rem; --text-xl: 1.375rem; --text-2xl: 1.625rem;
  --leading-prose: 150%; --leading-tight: 130%;
  --s1: .25rem; --s2: .5rem; --s3: .75rem; --s4: 1rem; --s5: 1.5rem; --s6: 2.5rem;
  --radius: .375rem; --rule-weight: 1px; --rail: .25rem; --edge-weight: 2px;
  --focus-width: 3px; --focus-gap: 2px;
  --tap: 48px; --hit: 44px; --measure: 40rem;
 }
 @media (prefers-color-scheme: dark) {
  :root {
   --paper: #0e1412; --card: #151d1a; --ink: #e4ebe8; --ink-2: #9eaba5;
   --rule: #2e3a35; --rule-2: #232d29; --edge: #6a7b74;
   --act: #9db5ff; --act-ink: #0b1431; --focus: #9db5ff;
   --ok: #62d1b4; --ok-bg: #12332b;
   --bad: #ff958c; --bad-bg: #3b1a17;
   --warn: #e6b74a; --warn-bg: #33270c;
  }
 }
 html { -webkit-text-size-adjust: 100%; }
 * { box-sizing: border-box; }
 body { font-family: var(--serif); font-size: var(--text-base); line-height: var(--leading-prose);
        background: var(--paper); color: var(--ink); max-width: var(--measure);
        margin: 0 auto; padding: 0 var(--s4) var(--s5); overflow-wrap: break-word; }
 /* A long unbroken identity wraps instead of widening the page. The
    inherited break-word leaves a table's column sizing alone, so a wide
    table still scrolls inside its own container; grid items need an
    explicit zero minimum or their content sets the track's width. */
 .accounts > *, .system > *, .facts > * { min-width: 0; }
 h1, h2, h3, h4 { line-height: var(--leading-tight); }
 h1 { font-size: var(--text-2xl); font-weight: 600; margin: var(--s4) 0 var(--s3); }
 h2 { font-size: var(--text-xl); font-weight: 600; margin: var(--s5) 0 var(--s2); }
 h3 { font-family: var(--sans); font-size: var(--text-md); font-weight: 600;
      margin: var(--s5) 0 var(--s1); }
 h4 { font-family: var(--sans); font-size: var(--text-md); font-weight: 600; margin: var(--s4) 0 var(--s1); }
 p { margin: var(--s2) 0; }
 a { color: var(--act); text-decoration-thickness: var(--rule-weight); text-underline-offset: .2em; }
 code { font-family: var(--mono); font-size: .9em; background: var(--card);
        border: var(--rule-weight) solid var(--rule); padding: 0 var(--s1); border-radius: var(--s1);
        overflow-wrap: anywhere; }
 /* The ring is a token, on every control; nothing removes it without a
    replacement, and a test holds that. */
 :focus-visible { outline: var(--focus-width) solid var(--focus); outline-offset: var(--focus-gap); }
 /* Hidden until focused, so a keyboard user can jump past the navigation. */
 .skip:not(:focus), .visually-hidden:not(:focus) { position: absolute; width: 1px; height: 1px;
        padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
 .skip { position: absolute; left: var(--s2); top: var(--s2); z-index: 10; padding: var(--s3) var(--s4);
         background: var(--act); color: var(--act-ink); font: 700 var(--text-md) var(--sans);
         border-radius: var(--radius); }
 .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip-path: inset(50%); white-space: nowrap; }
 /* Controls: the primary action is the one filled thing on a page; the
    rest are outlined, and a destructive one is outlined in red. */
 a.button, button.button { display: flex; align-items: center; justify-content: center;
            padding: 0 var(--s4); margin: var(--s2) 0; border-radius: var(--radius);
            background: var(--act); color: var(--act-ink); text-decoration: none; text-align: center;
            border: var(--edge-weight) solid var(--act); font: 700 1rem/130% var(--sans); cursor: pointer; }
 /* The floor under every control, including the inline ones whose own padding
    is smaller. */
 a.button, button.button { min-height: var(--tap); }
 /* Secondary weight: still thumb-sized, but outlined so the one primary
    control on a page is the heaviest thing on it. */
 a.button.secondary, button.button.secondary { background: transparent; color: var(--act);
            border: var(--edge-weight) solid var(--act); }
 a.button.danger, button.button.danger, button.danger { background: transparent; color: var(--bad);
            border: var(--edge-weight) solid var(--bad); }
 /* A bare submit is the ACTION of the page it sits on, and it used to
    render as the browser's default control, directly above a full-width
    navigation link. Sized like the link below it, and outlined rather than
    filled so the doing and the leaving are still told apart. */
 form button:not(.button) { display: flex; align-items: center; justify-content: center; width: 100%;
            min-height: var(--tap); padding: 0 var(--s4); margin: var(--s2) 0 var(--s4);
            border-radius: var(--radius); font: 700 1rem/130% var(--sans); cursor: pointer;
            background: transparent; color: var(--act); border: var(--edge-weight) solid var(--act); }
 form button.danger:not(.button) { color: var(--bad); border-color: var(--bad); }
 .row { padding: var(--s3) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 /* A row's name and its onward link: the link moves to a line of its own
    whole, rather than breaking beside a long name. */
 .row-head { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--s4); }
 table { border-collapse: collapse; width: 100%; font: var(--text-sm)/140% var(--sans); }
 th, td { padding: var(--s2); text-align: left; border-bottom: var(--rule-weight) solid var(--rule-2);
          vertical-align: top; }
 th { color: var(--ink-2); font-weight: 600; border-bottom-color: var(--edge); }
 caption { text-align: left; font: 600 var(--text-md) var(--sans); padding: var(--s2) 0; }
 .scroll { overflow-x: auto; }
 /* Chips: one shape, a meaning each, and a glyph so colour is not alone.
    The glyph is decoration - the chip's own word says the same thing - so a
    screen reader is given an empty alternative where the browser supports one. */
 .pill { display: inline-block; max-width: 100%; padding: 0 .45rem; border-radius: var(--s1);
         border: var(--rule-weight) solid var(--edge); color: var(--ink-2); background: transparent;
         font: 600 var(--text-xs)/150% var(--sans); }
 /* In a chip the identifier keeps the chip's face: monospace made a row's chips wrap. */
 .pill code { background: none; border: 0; padding: 0; font: inherit; color: inherit; }
 .pill-ok { color: var(--ok); border-color: var(--ok); }
 .pill-bad { color: var(--bad); border-color: var(--bad); }
 .pill-warn { color: var(--warn); border-color: var(--warn); }
 .pill-ok::before { content: "\\2713\\00a0"; content: "\\2713\\00a0" / ""; }
 .pill-bad::before { content: "\\2715\\00a0"; content: "\\2715\\00a0" / ""; }
 .pill-warn::before { content: "\\25CB\\00a0"; content: "\\25CB\\00a0" / ""; }
 .muted { color: var(--ink-2); }
 .mono { font-family: var(--mono); font-size: .85em; font-variant-numeric: tabular-nums;
         overflow-wrap: anywhere; }
 /* A date or a figure is read whole. The monospace rule above breaks
    anywhere, which is right for a long identifier and split a date across
    three lines in a narrow table cell. Only the date or figure itself is
    wrapped in this, never a sentence. */
 .nowrap { white-space: nowrap; word-break: normal; }
 .warn { color: var(--warn); font-weight: 600; }
 .bad, .alarm { color: var(--bad); font-weight: 600; }
 /* A good result is said quietly: ordinary size and weight, a small --ok tick, no coloured
    sentence. The one rule for every page; a test reads each stylesheet for any that shouts. */
 .ok, .quiet-ok { color: var(--ink); font-weight: 400; }
 .ok strong, .quiet-ok strong { font-weight: inherit; }
 .ok::before, .quiet-ok::before { content: "\\2713\\00a0"; content: "\\2713\\00a0" / ""; color: var(--ok); }
 .dormant { color: var(--ink-2); }
 .box { margin: var(--s3) 0; padding: var(--s3); border: var(--rule-weight) solid var(--rule);
        border-radius: var(--radius); }
 /* The instance band: one calm line that says which deployment this is.
    Amber, because red is for a fault and a scratch copy is not one. */
 .band { margin: var(--s3) 0; padding: var(--s2) var(--s3); background: var(--warn-bg);
         color: var(--warn); font: var(--text-sm)/140% var(--sans); border-radius: var(--radius); }
 input, select, textarea { display: block; width: 100%; min-height: var(--tap); padding: var(--s2) var(--s3);
          font: 1rem/130% var(--sans); color: var(--ink); background: var(--card);
          border: var(--rule-weight) solid var(--edge); border-radius: var(--radius); }
 select { padding-right: var(--s2); }
 label { font-family: var(--sans); font-size: var(--text-sm); }
 /* Navigation: quiet text tabs in a grid, the current one told by weight and
    an underline. None is a button, so the page's own action stays the
    heaviest thing on it. */
 .sitenav ul { list-style: none; margin: 0 0 var(--s3); padding: 0; display: grid;
               grid-template-columns: repeat(4, minmax(0, 1fr));
               border-bottom: var(--rule-weight) solid var(--rule); }
 .sitenav li { min-width: 0; }
 .sitenav a { display: flex; align-items: center; justify-content: center; text-align: center;
              min-height: var(--hit); padding: 0 var(--s1); overflow-wrap: anywhere;
              border-bottom: 3px solid transparent; color: var(--ink-2); text-decoration: none;
              font: 500 var(--text-sm)/130% var(--sans); }
 .sitenav li:nth-child(n+5) a { border-top: var(--rule-weight) solid var(--rule-2); }
 .sitenav a[aria-current="page"] { color: var(--ink); font-weight: 700; border-bottom-color: var(--act); }
 /* A link that is not a button but is still a thumb-sized target. The hit
    area is a pseudo-element, so the link adds nothing to its line's height
    and a paragraph ending in one is no taller than the lines above it. */
 .linklist { list-style: none; margin: 0; padding: 0; }
 a.tap { position: relative; font-family: var(--sans); font-size: var(--text-md); font-weight: 600; }
 a.tap::after { content: ""; position: absolute; inset: -.75rem -.5rem; }
 /* Every other link in the page gets the same reach, for the same reason. */
 main a:not(.button):not(.tap) { position: relative; }
 main a:not(.button):not(.tap)::after { content: ""; position: absolute; inset: -.75rem -.5rem; }
 a.tap.nowrap { white-space: normal; }
 .overview h2 { margin: var(--s5) 0 var(--s2); }
 /* The Overview: what needs a person is the heaviest thing on the page, and a
    healthy answer is one calm line. Severity is a rail, not a box. */
 .attention { list-style: none; margin: var(--s2) 0; padding: 0; }
 .attention li { margin: var(--s4) 0; padding: var(--s1) 0 var(--s1) var(--s3);
                 border-left: var(--rail) solid var(--bad); }
 .attention li.soon { border-left-color: var(--warn); }
 .attention li.housekeeping { border-left-color: var(--rule); }
 .attention p { margin: var(--s1) 0; }
 .allclear { margin: var(--s2) 0; padding: var(--s1) 0 var(--s1) var(--s3);
             border-left: var(--rail) solid var(--ok); }
 .legend { font: var(--text-sm)/150% var(--sans); margin: var(--s2) 0; padding-left: var(--s4); }
 /* Accounts: one card each, so the same markup reads on a phone and sits two
    abreast where there is room. */
 .accounts { list-style: none; margin: var(--s3) 0; padding: 0; display: grid;
             grid-template-columns: repeat(auto-fill, minmax(min(17rem, 100%), 1fr)); gap: var(--s3); }
 .account { padding: var(--s3) var(--s4); background: var(--card);
            border: var(--rule-weight) solid var(--rule); border-radius: var(--radius); }
 .account p { margin: var(--s1) 0; }
 .account-name { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s2);
                 font-size: var(--text-lg); }
 .account-name > * { min-width: 0; overflow-wrap: anywhere; }
 .account-sources { display: flex; flex-wrap: wrap; gap: var(--s1); }
 .account-links { display: flex; flex-wrap: wrap; gap: var(--s2) var(--s5);
                  border-top: var(--rule-weight) solid var(--rule-2); padding-top: var(--s2); margin-top: var(--s3); }
 .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(9rem, 100%), 1fr));
          gap: var(--s2) var(--s4); margin: var(--s2) 0; }
 .facts dt { font: var(--text-xs)/140% var(--sans); color: var(--ink-2); }
 .facts dd { margin: 0; font-size: var(--text-md); overflow-wrap: anywhere; }
 .anchors { list-style: none; margin: var(--s2) 0; padding: 0; }
 .anchors li { padding: var(--s2) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .anchors p { margin: var(--s1) 0; }
 /* The headline figure of a position, an account, or an asset. */
 .figure { font: 700 var(--text-xl)/130% var(--serif); font-variant-numeric: tabular-nums lining-nums;
           margin: var(--s2) 0; }
 .chart { margin: var(--s3) 0; max-width: var(--measure); color: var(--ink); }
 /* A step between months: a text link beside the heading it steps, and a
    button only because stepping while values are shown must be a POST. It is
    set after the bare-submit rule above so it wins over it. */
 .monthnav { display: flex; flex-wrap: wrap; gap: 0 var(--s4); margin: 0 0 var(--s2); }
 .monthnav form { margin: 0; }
 form button.tap { display: inline-flex; align-items: center; width: auto; min-height: var(--hit);
            margin: 0; padding: 0 var(--s2); border: 0; background: none; color: var(--act);
            font: 600 var(--text-md) var(--sans); text-decoration: underline; }
 /* One transaction per item, so nothing sits in a sideways-scrolling table. */
 .txns { list-style: none; margin: var(--s2) 0; padding: 0; }
 .txns li { padding: var(--s3) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .txns p { margin: var(--s1) 0; overflow-wrap: anywhere; }
 .txn-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 var(--s3); }
 .pills { display: flex; flex-wrap: wrap; gap: var(--s1); align-items: baseline; }
 /* A disclosure: a chevron, and a summary a thumb can hit. The flex layout
    removes the browser's own marker, so the chevron stands in for it. */
 details summary { cursor: pointer; min-height: var(--tap); display: flex; align-items: center;
                   gap: var(--s2); color: var(--ink-2); font: var(--text-md)/140% var(--sans); list-style: none; }
 details summary::-webkit-details-marker { display: none; }
 details summary::before { content: ""; flex: none; width: .5rem; height: .5rem; margin: 0 .25rem;
                           border-right: var(--edge-weight) solid var(--act);
                           border-bottom: var(--edge-weight) solid var(--act); transform: rotate(-45deg); }
 details[open] > summary::before { transform: rotate(45deg); }
 pre { font: var(--text-sm)/150% var(--mono); margin: var(--s3) 0; padding: var(--s3);
       background: var(--card); border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
       overflow-x: auto; tab-size: 2; }
 /* A report that is sentences in a fixed block wraps in a proportional face;
    a block that is aligned columns keeps its fixed face and scrolls. */
 pre[style*="pre-wrap"] { font: var(--text-md)/150% var(--sans); overflow-wrap: anywhere; }
 input[type="checkbox"] { flex: none; width: 1.5rem; min-height: 0; height: 1.5rem; margin: 0 var(--s2) 0 0;
                          accent-color: var(--act); }
 /* A tick the thumb can hit: the whole row is the target, not the box. */
 label.tick { display: flex; align-items: center; gap: var(--s3); min-height: var(--hit);
              padding: var(--s1) 0; font-size: var(--text-md); }
 label.tick input { flex: none; width: 1.5rem; height: 1.5rem; margin: 0; }
 label.tick > span { min-width: 0; overflow-wrap: anywhere; }
 /* A fieldset will not shrink below its widest word unless told it may, and an
    account reference is one long word. */
 fieldset.chart-choice { border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
                         margin: var(--s2) 0; padding: var(--s1) var(--s3); min-width: 0; }
 /* What a page is for, in one or two sentences before anything else. */
 .lede { margin: var(--s1) 0 var(--s4); }
 /* The newest push and audit, readable at a glance. */
 .leadlines p { margin: var(--s2) 0; }
 /* The home page's System strip: five facts, each a link to its page. */
 .system { list-style: none; margin: var(--s2) 0; padding: 0; display: grid;
           grid-template-columns: repeat(auto-fit, minmax(min(14rem, 100%), 1fr)); gap: 0 var(--s5); }
 .fact { padding: var(--s2) 0; border-bottom: var(--rule-weight) solid var(--rule-2); }
 .fact p { margin: var(--s1) 0; }
 /* A row of outlined links, thumb-tall, wrapping on a narrow screen. */
 .linkrow { list-style: none; margin: var(--s2) 0; padding: 0; display: flex;
            flex-wrap: wrap; gap: var(--s2); }
 a.tap.outline { display: inline-flex; align-items: center; min-height: var(--hit);
                 border: var(--rule-weight) solid var(--edge); border-radius: var(--radius); padding: 0 var(--s3); }
 /* A one-off experiment sits apart from the repairs above it. */
 details.oneoff { margin-top: var(--s5); padding-top: var(--s2); border-top: var(--rule-weight) solid var(--rule); }
 footer { margin-top: var(--s5); color: var(--ink-2); font: var(--text-sm) var(--sans); }
 /* A page of cards uses a wide screen. Its loose prose and forms keep the
    narrow page's measure, since a line the full width is too long to read. */
 @media (min-width: 60rem) {
  body.wide { max-width: 64rem; }
  body.wide main > p, body.wide main > form, body.wide main > details, body.wide main > section > p,
  body.wide main > section > form, body.wide main > section > details { max-width: 40rem; }
 }
 @media (prefers-reduced-motion: no-preference) {
  details summary::before { transition: transform .15s; }
 }
"""

#: The one stylesheet a page carries: the shared rules, then each page's own.
STYLESHEET = (
    SHARED_STYLES + HOME_STYLES + ACCOUNT_STYLES + ACTUAL_STYLES + FLAGS_STYLES + SECTION_STYLES
    + WINDOW_STYLES + POSITION_STYLES + GAPS_STYLES + TIMELINE_STYLES
)
