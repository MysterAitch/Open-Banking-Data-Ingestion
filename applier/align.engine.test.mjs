/**
 * One job that brings Actual into line with obdi, against the REAL budget
 * engine (offline, one temporary budget per test; nothing here touches a
 * network).
 *
 * The household is invented. Expected answers were fixed before the first run:
 *
 *   MAIN  A -150000  expected, linked to B in Actual, the request pairs it with C
 *         D    -300  expected, plain
 *         H   -2500  ORPHAN, obdi holds it as history (it is in the request's history list)
 *         U    -700  ORPHAN, obdi holds nothing with that identity
 *   POT   B +150000  expected, A's old partner
 *         C +150000  expected, A's new partner
 *
 *   scope explained, ceiling 1:  steps push, audit, prune, audit_final in that order;
 *     A-C linked and B plain; H deleted, U kept and counted as left; the final audit
 *     finds one orphan (U), unknown; complete.
 *   scope all, ceiling 2:        H and U both deleted; the final audit is clean.
 *   an orphan linked to an expected row the request pairs with nothing:
 *     the removal unlinks it, so the push runs again before the final audit.
 *   a push that fails to link, or that creates an account, or a removal that is
 *   refused: the job stops there and says at which step, and no later step ran.
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { alignBudget } from './align.mjs';
import { applyAccounts, linkTransfers } from './lib.mjs';

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

let budgets = 0;

async function withBudget(work) {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-align-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`align-${budgets}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        const pot = await api.createAccount({ name: 'POT' }, 0);
        return await work({ main, pot });
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

const hexId = (digit) => `${digit.repeat(64)}:0`;

const row = (importedId, date, amount, payee) => ({
  imported_id: importedId,
  date,
  amount,
  payee_name: payee,
  cleared: true,
});

const A = row(hexId('a'), '2026-09-02', -150000, 'Savings pot');
const D = row(hexId('d'), '2026-09-03', -300, 'Shop');
const H = row(hexId('e'), '2026-09-04', -2500, 'Cafe');
const U = row(hexId('f'), '2026-09-05', -700, 'Kiosk');
const B = row(hexId('b'), '2026-09-02', 150000, 'Current account');
const C = row(hexId('c'), '2026-09-02', 150000, 'Current account');
const O = row(hexId('1'), '2026-09-02', -150000, 'Savings pot');
const N = row(hexId('2'), '2026-09-02', 150000, 'Current account');

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function settledCount(accountId, count) {
  for (let i = 0; i < 200; i += 1) {
    const rows = await readRows(accountId);
    if (rows.length === count) return rows.length;
    await pause(50);
  }
  return (await readRows(accountId)).length;
}

async function importedIds(accountId) {
  return (await readRows(accountId)).map((r) => r.imported_id).sort();
}

const sorted = (...rows) => rows.map((r) => r.imported_id).sort();

async function stocked(ctx) {
  await applyAccounts(api, {
    [ctx.main]: [A, D, H, U],
    [ctx.pot]: [B, C],
  });
  const linked = await linkTransfers(api, [{ debit: leg(ctx.main, A), credit: leg(ctx.pot, B) }]);
  assert.equal(linked.counts.linked, 1);
}

const request = (ctx, overrides = {}) => ({
  provision: [],
  accounts: { [ctx.main]: [A, D], [ctx.pot]: [B, C] },
  transfers: [{ debit: leg(ctx.main, A), credit: leg(ctx.pot, C) }],
  openings: [],
  history: [H.imported_id],
  confirmed: { [ctx.main]: 1, [ctx.pot]: 0 },
  scope: { [ctx.main]: 'explained', [ctx.pot]: 'all' },
  ...overrides,
});

const run = (client, req, options) => quietly(() => alignBudget(client, req, options));

const stepNames = (outcome) => outcome.steps.map((s) => s.step);
const stepResult = (outcome, name) => outcome.steps.find((s) => s.step === name)?.result;

const linkOf = async (ctx, r, accountId) => {
  const rows = [...(await readRows(ctx.main)), ...(await readRows(ctx.pot))];
  const found = rows.find((x) => x.imported_id === r.imported_id && x.account === accountId);
  if (!found?.transfer_id) return null;
  return rows.find((x) => x.id === found.transfer_id)?.imported_id ?? 'UNKNOWN ROW';
};

test('Align_WhenScopeIsExplained_RelinksRemovesTheExplainedOrphanAndLeavesTheUnknownOne', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const outcome = await run(api, request(ctx));

    assert.equal(outcome.complete, true);
    assert.equal(outcome.stopped_at, null);
    assert.deepEqual(stepNames(outcome), ['push', 'audit', 'prune', 'audit_final']);
    assert.equal(stepResult(outcome, 'push').transfers.relinked, 1);
    assert.equal(await linkOf(ctx, A, ctx.main), C.imported_id);
    assert.equal(await linkOf(ctx, B, ctx.pot), null);
    const audit = stepResult(outcome, 'audit').accounts.find((a) => a.account_id === ctx.main);
    assert.equal(audit.orphaned, 2);
    assert.deepEqual(audit.orphaned_explained, { history: 1, elsewhere: 0, unknown: 1 });
    const prune = stepResult(outcome, 'prune').accounts.find((a) => a.account_id === ctx.main);
    assert.equal(prune.removed, 1);
    assert.equal(prune.unexplained_left, 1);
    assert.equal(await settledCount(ctx.main, 3), 3);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D, U));
    const final = stepResult(outcome, 'audit_final').accounts.find((a) => a.account_id === ctx.main);
    assert.equal(final.orphaned, 1);
    assert.deepEqual(final.orphaned_explained, { history: 0, elsewhere: 0, unknown: 1 });
    assert.deepEqual(await importedIds(ctx.pot), sorted(B, C));
  });
});

test('Align_WhenScopeIsAll_RemovesTheUnknownOrphanTooAndTheFinalAuditIsClean', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const outcome = await run(
      api,
      request(ctx, {
        confirmed: { [ctx.main]: 2, [ctx.pot]: 0 },
        scope: { [ctx.main]: 'all', [ctx.pot]: 'all' },
      }),
    );

    assert.equal(outcome.complete, true);
    assert.equal(await settledCount(ctx.main, 2), 2);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D));
    for (const entry of stepResult(outcome, 'audit_final').accounts) {
      assert.equal(entry.orphaned, 0, entry.name);
      assert.equal(entry.missing, 0, entry.name);
      assert.equal(entry.balance.agrees, true, entry.name);
    }
  });
});

test('Align_WhenAnAccountIsNotInTheScope_ItsOrphansAreNotTouched', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const outcome = await run(
      api,
      request(ctx, { confirmed: { [ctx.pot]: 0 }, scope: { [ctx.pot]: 'all' } }),
    );

    assert.equal(outcome.complete, true);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D, H, U));
    assert.match(outcome.steps.find((s) => s.step === 'prune').skipped, /no orphan/);
  });
});

test('Align_WhenTheRemovalUnlinksALegItKeeps_ThePushRunsAgainBeforeTheFinalAudit', async () => {
  await withBudget(async (ctx) => {
    await applyAccounts(api, { [ctx.main]: [O, D], [ctx.pot]: [N] });
    await linkTransfers(api, [{ debit: leg(ctx.main, O), credit: leg(ctx.pot, N) }]);

    const outcome = await run(
      api,
      request(ctx, {
        accounts: { [ctx.main]: [D], [ctx.pot]: [N] },
        transfers: [],
        history: [O.imported_id],
        confirmed: { [ctx.main]: 1, [ctx.pot]: 0 },
      }),
    );

    assert.equal(outcome.complete, true);
    assert.deepEqual(stepNames(outcome), ['push', 'audit', 'prune', 'push_again', 'audit_final']);
    const prune = stepResult(outcome, 'prune').accounts.find((a) => a.account_id === ctx.main);
    assert.equal(prune.removed, 1);
    assert.equal(prune.unlinked, 1);
    assert.equal(await settledCount(ctx.main, 1), 1);
    assert.deepEqual(await importedIds(ctx.pot), sorted(N));
    assert.equal(await linkOf(ctx, N, ctx.pot), null);
  });
});

test('Align_WhenTheAuditFindsNoOrphans_NoRemovalIsAttemptedAndItIsSaidSo', async () => {
  await withBudget(async (ctx) => {
    await applyAccounts(api, { [ctx.main]: [A, D], [ctx.pot]: [B, C] });

    const outcome = await run(api, request(ctx, { confirmed: { [ctx.main]: 0, [ctx.pot]: 0 } }));

    assert.equal(outcome.complete, true);
    assert.deepEqual(stepNames(outcome), ['push', 'audit', 'prune', 'audit_final']);
    const prune = outcome.steps.find((s) => s.step === 'prune');
    assert.equal(prune.result, undefined);
    assert.match(prune.skipped, /no orphan/);
  });
});

test('Align_WhenALinkFailsInThePush_StopsAtThePushAndNoLaterStepRuns', async () => {
  await withBudget(async (ctx) => {
    await applyAccounts(api, {
      [ctx.main]: [A, D, H, U],
      [ctx.pot]: [B, C],
    });
    const failing = new Proxy(api, {
      get(target, property) {
        if (property === 'updateTransaction') {
          return async (id, fields) => {
            if (fields && 'transfer_id' in fields) throw new Error('the engine refused');
            return target.updateTransaction(id, fields);
          };
        }
        return target[property];
      },
    });

    const outcome = await run(failing, request(ctx));

    assert.equal(outcome.complete, false);
    assert.equal(outcome.stopped_at, 'push');
    assert.match(outcome.stopped, /1 transfer pair\(s\) failed to link/);
    assert.deepEqual(stepNames(outcome), ['push']);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D, H, U), 'no removal ran');
  });
});

test('Align_WhenThePushCreatesAnAccount_StopsAfterItBecauseItsRowsRideTheNextPush', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const outcome = await run(
      api,
      request(ctx, { provision: [{ canonical_id: 'new-account', label: 'New account' }] }),
    );

    assert.equal(outcome.complete, false);
    assert.equal(outcome.stopped_at, 'push');
    assert.match(outcome.stopped, /1 account\(s\) were created/);
    assert.deepEqual(stepNames(outcome), ['push']);
    assert.equal(outcome.bindings.length, 1);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D, H, U));
  });
});

test('Align_WhenTheCeilingIsBelowWhatTheAccountHolds_TheRemovalIsRefusedAndTheJobStopsThere', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const outcome = await run(api, request(ctx, { confirmed: { [ctx.main]: 0, [ctx.pot]: 0 } }));

    assert.equal(outcome.complete, false);
    assert.equal(outcome.stopped_at, 'prune');
    assert.match(outcome.stopped, /more than the 0 confirmed/);
    assert.deepEqual(stepNames(outcome), ['push', 'audit', 'prune']);
    assert.deepEqual(await importedIds(ctx.main), sorted(A, D, H, U));
  });
});

test('Align_WhenThePushThrows_TheStepIsRecordedAsFailedAndTheJobStopsThere', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const unreachable = new Proxy(api, {
      get(target, property) {
        if (property === 'importTransactions') {
          return async () => {
            throw new Error('the server went away');
          };
        }
        return target[property];
      },
    });

    const outcome = await run(unreachable, request(ctx));

    assert.equal(outcome.complete, false);
    assert.equal(outcome.stopped_at, 'push');
    assert.deepEqual(stepNames(outcome), ['push']);
    assert.equal(stepResult(outcome, 'push').ok, false);
    assert.ok(stepResult(outcome, 'push').error.length > 0);
    assert.equal(outcome.stopped, stepResult(outcome, 'push').error);
  });
});
