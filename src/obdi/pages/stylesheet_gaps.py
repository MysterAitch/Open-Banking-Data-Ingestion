# ruff: noqa: E501
"""What the owner has set aside: the rules of the set-aside pages and of the fold on Bring in.

Written with the shared tokens only: no colour, face, or size is declared here. These rules are
NOT in the stylesheet every page carries: they are written into the pages that use them
(`web_marks.MARKS_STYLE_TAG`), because the balance chart's page-size bound sits within a few
hundred bytes of the limit and a rule that only a few pages need has no business in every page.
A contradicted mark is the one amber block on Bring in that is not a file wanted: it sits above
the accounts because it says an earlier decision of the owner's is wrong, and it does not shout,
for it is his own mark and one tap removes it.
"""

MARKS_STYLES = """
 @media (min-width: 60rem) {
  body.gaps-page { max-width: 56rem; }
 }
 .gaps-quiet { list-style: none; margin: 0; padding: 0; font: var(--text-md)/150% var(--sans); }
 .gaps-quiet-item { padding: var(--s2) 0; border-top: var(--rule-weight) solid var(--rule-2); }
 .gaps-quiet-head { margin: var(--s5) 0 var(--s2); font: 600 var(--text-lg)/var(--leading-tight) var(--serif); }
 .gaps-list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); }
 .gaps-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s2); }
 .gaps-kind { font: 600 var(--text-sm)/150% var(--sans); }
 .gaps-source { font: var(--text-sm)/150% var(--sans); color: var(--ink-2); }
 .gaps-range { font-size: var(--text-xl); font-weight: 600; line-height: var(--leading-tight); margin: var(--s2) 0 var(--s1); overflow-wrap: anywhere; }
 .gaps-why { font: var(--text-md)/150% var(--sans); color: var(--ink-2); }
 .gaps-links { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4); margin: var(--s2) 0 0; }
 .gaps-decide { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s4); margin: var(--s2) 0 0; }
 .gaps-ack, .gaps-undo { margin: 0; }
 .gaps-contradictions { list-style: none; margin: var(--s3) 0; padding: 0; display: grid; gap: var(--s3); }
 .gaps-contradicted { background: var(--warn-bg); color: var(--ink); border-left: var(--rail) solid var(--warn);
                      border-radius: var(--radius); padding: var(--s2) var(--s4); }
 .gaps-contradicted p { margin: var(--s1) 0; }
 .gaps-aside { margin-top: var(--s5); border-top: var(--rule-weight) solid var(--rule); }
 .gaps-sub { margin: var(--s4) 0 var(--s1); font: 600 var(--text-md)/var(--leading-tight) var(--sans); }
 .gaps-mark { background: var(--card); border: var(--rule-weight) solid var(--rule); border-radius: var(--radius);
              padding: var(--s3) var(--s4); border-left: var(--rail) solid var(--edge); }
 .gaps-mark.gaps-mark-contradicted { border-left-color: var(--warn); background: var(--warn-bg); }
 .gaps-mark.gaps-mark-supported { border-left-color: var(--ok); }
 .gaps-mark p { margin: var(--s1) 0; }
 .gaps-note { font: italic var(--text-base)/var(--leading-prose) var(--serif); overflow-wrap: anywhere; }
 .gaps-mark .gaps-range { font-size: var(--text-lg); }
 .pill.gaps-supported { background: var(--ok-bg); color: var(--ok); }
 .pill.gaps-contradicted { background: var(--warn-bg); color: var(--warn); border: var(--rule-weight) solid var(--warn); }
 .gaps-scope-lines { list-style: none; margin: var(--s2) 0; padding: 0; font: var(--text-md)/150% var(--sans); }
 .gaps-scope-line { padding: var(--s1) 0; }
 .gaps-reach .gaps-offer { margin: var(--s2) 0; padding: var(--s3) var(--s4); background: var(--card);
                           border: var(--rule-weight) solid var(--rule); border-radius: var(--radius); }
 .gaps-reach-line { margin: var(--s1) 0; font: var(--text-md)/150% var(--sans); }
 .gaps-form { margin: var(--s3) 0; max-width: var(--measure); }
 .gaps-field { display: block; margin: var(--s3) 0 var(--s1); font: var(--text-md)/140% var(--sans); }
 .gaps-field input, .gaps-field select, .gaps-field textarea { display: block; width: 100%; margin-top: var(--s1); }
 .gaps-dates { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(11rem, 100%), 1fr)); gap: 0 var(--s4); }
 .gaps-choice { border: var(--rule-weight) solid var(--rule); border-radius: var(--radius); margin: var(--s3) 0; padding: var(--s1) var(--s3); min-width: 0; }
 .gaps-kindrow { display: flex; align-items: flex-start; gap: var(--s3); padding: var(--s3) 0;
                 border-bottom: var(--rule-weight) solid var(--rule-2); min-height: var(--hit); }
 .gaps-kindrow:last-child { border-bottom: 0; }
 .gaps-kindrow input[type="radio"] { flex: none; width: 1.5rem; height: 1.5rem; margin: .1rem 0 0; accent-color: var(--act); }
 .gaps-kindrow > span { min-width: 0; overflow-wrap: anywhere; }
 .gaps-asserts { display: block; color: var(--ink-2); font: var(--text-sm)/150% var(--sans); }
 .gaps-evidence { display: block; margin-top: var(--s2); padding: var(--s1) var(--s3); font: var(--text-md)/150% var(--sans);
                  border-left: var(--rail) solid var(--edge); }
 .gaps-evidence.gaps-supported { border-left-color: var(--ok); color: var(--ink); }
 .gaps-evidence.gaps-contradicted { border-left-color: var(--warn); background: var(--warn-bg); color: var(--ink); }
 .gaps-evidence.gaps-untested { border-left-style: dashed; color: var(--ink-2); }
 .gaps-choice input.gaps-short { display: inline-block; width: 5rem; margin: 0 var(--s1); }
 /* The two decisions that sit beside a thumb-sized link are aligned to its text, not its padding. */
 .gaps-ack button.tap, .gaps-undo button.tap { padding-left: 0; }
 .gaps-choice input[type="number"], .gaps-choice input[type="date"] { min-height: var(--hit); padding: 0 var(--s2);
     background: var(--card); color: var(--ink); border: var(--rule-weight) solid var(--edge); border-radius: var(--radius); font: inherit; }
 .gaps-choice input[type="date"] { display: block; width: 100%; margin-top: var(--s1); }
"""
