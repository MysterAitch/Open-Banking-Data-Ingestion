# Non-functional requirements

What must hold whatever the feature. Status words are in [README.md](README.md). Several of
these are rules the owner stated; his words and the date are quoted so the rule outlives the
conversation.

## Privacy and masking (PRIVACY)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| NF-PRIVACY-01 | Nothing that is a value reaches a GET: no amount, balance, total, payee, free-text description, reference, or rate. Counts, dates, source names, account names, directions, verdicts, and percentages of drift may. | DONE | `tests/test_get_routes_hold_no_stored_values.py`, `tests/test_masking.py`, `tests/test_import_masking.py` |
| NF-PRIVACY-02 | A value is shown only in answer to a deliberate press or under a sitting, both served `no-store`; the sitting is a signed session cookie capped at twelve hours; a request without it is masked exactly as before. | DONE | `tests/test_values_sitting.py` |
| NF-PRIVACY-03 | The assistant that helps build obdi reads live pages masked only, with tools that refuse any page not on an allow-list and never press a reveal; an unmasked read happens only by the owner's explicit, scoped, case-by-case grant for a named purpose. The owner, 2026-10-01 and 2026-10-05: "account balances and full details about transactions etc. are private and not for reading ... narrowly scoped and time restricted access for specific purposes like debugging is permissible on a case-by-case basis". | DONE (a rule of work) | memory `real-financial-values-are-not-for-reading` |
| NF-PRIVACY-04 | No real value is written into the repository, a test, a design note, a commit body, or a memory; development is against the synthetic corpus. | DONE (a rule of work) | `src/obdi/synthetic*.py`, `tests/large_store_corpus.py` |
| NF-PRIVACY-05 | A stored extraction holds a document's real text like the raw artefact it came from; a page shows only its masked shape. | DONE | `tests/test_statement_extraction_pages.py` |
| NF-PRIVACY-06 | An alert never quotes data that may have broken something; the message is withheld and the container log named. | DONE | `src/obdi/scheduler_status.py` (`WITHHELD`) |
| NF-PRIVACY-07 | A page served to a request from another site is refused for every mutating route (same-origin check on every POST). | DONE | `tests/test_web_hardening.py` |
| NF-PRIVACY-08 | The callback receiver silences its access log, since a redirect carries the authorisation code. | DONE | `src/obdi/callback.py` |

## Scope (SCOPE)

| Id | Requirement | Status |
|---|---|---|
| NF-SCOPE-01 | One owner, one household, one instance on one host on a private network. Several instances may run side by side (live, a restore target, a scratch copy) and must be told apart: a non-production instance carries a banner on every page; an unidentified one says so; the live one shows nothing, deliberately. | DONE - `tests/test_instance_identity.py` |
| NF-SCOPE-02 | Writes are rare and small (a trickle after the back-fill); the tolerance between an event at a bank and its appearance in obdi is days. Design follows from that. The owner, 2026-10-06. | DONE (a rule of design) |
| NF-SCOPE-03 | obdi is the master record; a downstream tool is a disposable read-only view. The owner, 2026-10-02: no hand work lives in Actual. | PARTIAL - rows pushed; categories, commitments, and targets not yet projected |

## Speed (SPEED)

| Id | Requirement | Status |
|---|---|---|
| NF-SPEED-01 | The objective, in the owner's terms on the real store: a warm page under 2 s; the first page after a deploy under 10 s. Measured 2026-10-06: 1.1-1.2 s warm; 14-20 s cold. There is no service-level agreement - one owner, no second party. | PARTIAL - the cold figure is outside the objective and is the trigger for the stored read model |
| NF-SPEED-02 | The stored read model (`docs/design/2026-10-read-model/design.md`, slices S1 to S5) waits for a measured trigger - a page over about five seconds warm on the real store, or a post-deploy first load the owner notices - and is not scheduled. The owner, 2026-10-06: "Pages taking a second or two to load is not the end of the world". | NOT YET, by decision |
| NF-SPEED-03 | The per-page statement budgets in `tests/test_ledger_speed.py` are shape detectors - measured counts plus headroom - that catch a reading growing by a statement per row; they are not an objective and do not move. | DONE - `tests/test_ledger_speed.py` |
| NF-SPEED-04 | A rebuild of the real store takes about 100 s with nothing reading (reconcile about 45 s); a rebuild slowed past that is first suspected of concurrent reads. The first rebuild after a derived table is added may be longer once. | DONE (measured; the admin page's "Recent rebuilds" table is the instrument) |
| NF-SPEED-05 | No read of the live site between a deploy's footer changing and the rebuild record appearing, beyond one cheap request a minute to each. | DONE (a rule of work) - memory `do-not-read-live-while-a-rebuild-runs` |

## The store, schema, and rollback (STORE)

| Id | Requirement | Status |
|---|---|---|
| NF-STORE-01 | Roll forward only. A store written by a newer version is refused by an older one; the schema version moves with every schema change and the changelog says so. The owner, 2026-10-04: "I don't intend to rollback, only roll forward ... if we can recreate it from the raw data then it's okay to break and fix it. Breaking silently and/or risking the underlying data is no bueno." | DONE - `tests/test_serving_over_a_newer_store.py`, `tests/test_schema_version_gate.py` |
| NF-STORE-02 | The rebuild promise: raw artefacts are immutable and the only input; the derived layer is rebuilt from them on every deploy; declared state survives. | DONE - `tests/test_rebuild.py`, `tests/test_large_store_rebuild.py`, `tests/test_review_decisions_survive_a_rebuild.py` |
| NF-STORE-03 | Nothing that regenerates the derived layer may touch declared state; tests that write to the store through a door other than the declared ones are listed and justified. | DONE - `tests/test_fixture_write_doors.py` |
| NF-STORE-04 | A memo is keyed on the standing epoch and never stored inside a transaction. | DONE - `tests/test_standing_epoch.py` |

## Release and deploy (RELEASE, DEPLOY)

| Id | Requirement | Status |
|---|---|---|
| NF-RELEASE-01 | One whole-suite run per change, by whoever merges, with nothing edited while it runs; the release's gate runs lint, types, and the suite again; a change that skips the suite is caught by the gate (it was, 0.4.346). | DONE - `docs/BUILDING.md`, `scripts/release.sh` |
| NF-RELEASE-02 | The changelog carries the version before its tag exists; a tag whose version is not in the committed file is refused; entries say why, not what. | DONE - `CHANGELOG.md` header, the `changelog-before-tag` guard |
| NF-RELEASE-03 | Releases are cut freely without asking; the deploy and anything host-side are the owner's. The owner, 2026-10-02. | DONE (a rule of work) - memory `release-freely-deploy-is-rogers` |
| NF-RELEASE-04 | A commit body carries the failure, the reasoning, the evidence, and the residue; no attribution lines. | DONE - `docs/BUILDING.md` |
| NF-DEPLOY-01 | A deploy runs a rebuild and a converge that refuses to finish while the rebuild's answer is not yet known; nothing is lost by waiting. | DONE (the owner's playbook) |
| NF-DEPLOY-02 | The live instance is reached only from the owner's network; the store itself is not reachable from the development machine. | DONE - memory `live-instance-is-http-only-from-this-machine` |

## Accessibility and the phone (ACCESS)

| Id | Requirement | Status |
|---|---|---|
| NF-ACCESS-01 | Every page is laid out for 390 px first, with no sideways scroll, a 16 px gutter, and measured budgets in screens for the pages that list many things (Bring in with ten files under about three screens; the ledger's thirty rows; the account page). | DONE - `tests/test_bring_in_scale.py`, `tests/test_phone_layout.py`, `tests/test_accounts_phone_layout.py`, `tests/test_account_page_scale.py` |
| NF-ACCESS-02 | Bars are `aria-hidden` and the sentence beside them says the same in words; a skip link leads to the content; tap targets meet the house minimum. | DONE - `tests/test_page_accessibility.py`, `tests/test_archive_account.py` (tap targets) |
| NF-ACCESS-03 | The dark scheme is kept while free and is given no effort; the owner does not use it. 2026-10-05. | DONE (a decision) - memory `dark-mode-is-low-priority` |
| NF-ACCESS-04 | A new page is photographed at 390 px over invented data and looked at before it ships; wider photographs are kept where cheap. | DONE (a rule of work) - `docs/BUILDING.md` |
| NF-ACCESS-05 | Clock times are shown on the owner's London clock; a time quoting a source's own stamp stays marked Z and says so. | DONE - `tests/test_times_on_the_owners_clock.py` |
