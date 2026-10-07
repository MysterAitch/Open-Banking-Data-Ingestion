# ruff: noqa: E501
"""The Entities page's own rules, joined into the one stylesheet by `stylesheet`.

Laid out for a phone first: a proposed group is one form (its name field, one tickable line per
name, one Merge press), and an entity is its name, its names one per line each with its own press,
and a rename field. Lines are kept tight so thirty names fit three phone screens
(`test_entities_phone_layout`). No colour, face, or size is declared here: the shared tokens only.
"""

ENTITIES_STYLES = """
 .ent-summary { font: 600 var(--text-md)/140% var(--sans); margin: var(--s3) 0 var(--s1); }
 .ent-group, .ent-entity { border-top: var(--rule-weight) solid var(--rule-2); padding: var(--s2) 0; }
 .ent-group h3, .ent-entity h3 { margin: 0; overflow-wrap: anywhere; }
 .ent-name-field { display: flex; align-items: center; gap: var(--s2); margin: var(--s1) 0; }
 .ent-name-field label { flex: 1 1 auto; display: flex; align-items: center; gap: var(--s2); min-width: 0; }
 .ent-name-field input { flex: 1 1 6rem; min-width: 0; }
 .ent-name-field button { flex: none; }
 .ent-names { list-style: none; margin: 0; padding: 0; }
 .ent-names li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--s3); padding: 0; line-height: 130%; }
 .ent-rows { margin-left: auto; }
 .ent-rows[open] { flex: 1 0 100%; order: 9; }
 .ent-rows > summary { cursor: pointer; text-align: right; min-height: var(--hit); display: flex; align-items: center; justify-content: flex-end; }
 .ent-tx { list-style: none; margin: 0; padding: 0; }
 .ent-tx li { display: block; padding: var(--s1) 0; overflow-wrap: anywhere; }
 .ent-tx a { display: block; }
 .ent-names label.tick { flex: 1 1 auto; min-height: var(--hit); }
 .ent-count { white-space: nowrap; font: var(--text-sm)/130% var(--sans); color: var(--ink-2); }
 .ent-why { font: var(--text-sm)/140% var(--sans); color: var(--ink-2); margin: var(--s1) 0 0; }
 .ent-names form { margin: 0; }
 .ent-names li > span.txt { min-width: 0; overflow-wrap: anywhere; }
 .ent-more > summary, .ent-fold > summary { padding: var(--s2) 0; cursor: pointer; }
 .ent-children { margin: var(--s2) 0 0 var(--s3); padding-left: var(--s3); border-left: 2px solid var(--rule); }
 .ent-fold select { max-width: 100%; }
"""
