/**
 * Clearing an account that expects nothing, against the REAL budget engine.
 *
 * The same offline setup as opening.engine.test.mjs: the pinned library is
 * initialised with no server and each test gets its own temporary budget.
 * Nothing here touches a network.
 *
 * The household is invented. MAIN holds seven rows:
 *   four obdi-shaped rows with no link          (P1..P4: the candidates)
 *   one row the person typed in                 (no imported id)
 *   one row carrying another importer's id      (FITID-0001)
 *   one obdi-shaped row that is a leg of a linked transfer (LEG_MAIN)
 * POT holds the other leg of that transfer (LEG_POT) and an ordinary row
 * (P5), and still expects both. The expectations were fixed before the first
 * run:
 *   - MAIN expecting nothing, no confirmed count      -> skipped, 7 rows stay
 *   - MAIN expecting nothing, confirmed 3 (< 4)       -> refused "holds 4, 3", 7 rows stay
 *   - MAIN expecting nothing, confirmed 4 (= 4)       -> removed 4; hand, foreign, linked leg
 *                                                        and POT's leg all stay (3 rows in MAIN)
 *   - MAIN expecting P1 only, confirmed 2 (< 3)       -> refused "holds 3, 2", 7 rows stay
 *   - MAIN expecting P1 only, confirmed 3 (= 3)       -> removed 3; P1 and the other three kinds stay
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { pruneAccounts } from './audit.mjs';
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
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-clear-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`clear-test-${budgets}`, async () => {});
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

const hexId = (digit, n = 0) => `${digit.repeat(64)}:${n}`;

const row = (importedId, date, amount, payee) => ({
  imported_id: importedId,
  date,
  amount,
  payee_name: payee,
  cleared: true,
});

const P = [1, 2, 3, 4].map((n) =>
  row(hexId(String(n)), `2026-09-0${n}`, -1000 * n, `Shop ${n}`),
);
const P5 = row(hexId('5'), '2026-09-05', -5000, 'Cafe');
const FOREIGN = row('FITID-0001', '2026-09-06', -777, 'Other importer');
const LEG_MAIN = row(hexId('c'), '2026-09-07', -150000, 'Savings pot');
const LEG_POT = row(hexId('f'), '2026-09-07', 150000, 'Current account');
const HAND = { date: '2026-09-08', amount: -4242, payee_name: 'Cash', cleared: true };

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');

async function stocked({ main, pot }) {
  await applyAccounts(api, {
    [main]: [...P, FOREIGN, LEG_MAIN],
    [pot]: [P5, LEG_POT],
  });
  await api.addTransactions(main, [HAND]);
  await linkTransfers(api, [{ debit: leg(main, LEG_MAIN), credit: leg(pot, LEG_POT) }]);
}

async function importedIds(accountId) {
  return (await readRows(accountId)).map((r) => r.imported_id ?? null).sort();
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The engine applies a delete after the call has resolved, so a read that
// follows one waits for the condition the test is about.
async function settledCount(accountId, count, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let rows = await readRows(accountId);
  while (rows.length !== count && Date.now() < deadline) {
    await pause(50);
    rows = await readRows(accountId);
  }
  return rows.length;
}

const EXPECTS_NOTHING = (ctx) => ({ [ctx.main]: [], [ctx.pot]: [P5, LEG_POT] });
const EXPECTS_P1 = (ctx) => ({ [ctx.main]: [P[0]], [ctx.pot]: [P5, LEG_POT] });

const run = (ctx, accounts, options) =>
  quietly(() => pruneAccounts(api, accounts, options));

const entryFor = (report, accountId) => report.find((e) => e.account_id === accountId);

test('ClearEmpty_WhenNoConfirmedCountIsGiven_NothingIsDeletedAndTheAccountIsSkipped', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_NOTHING(ctx), {});

    assert.ok(entryFor(report, ctx.main).skipped);
    assert.equal(entryFor(report, ctx.main).removed, undefined);
    await pause(500);
    assert.equal((await readRows(ctx.main)).length, 7);
    assert.equal((await readRows(ctx.pot)).length, 2);
  });
});

test('ClearEmpty_WhenTheConfirmedCountIsLowerThanTheRowsHeld_NothingIsDeletedAndBothNumbersAreReported', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_NOTHING(ctx), { clear_empty: { [ctx.main]: 3 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.holds, 4);
    assert.equal(entry.confirmed, 3);
    assert.match(entry.refused, /holds 4, more than the 3 confirmed/);
    assert.equal(entry.removed, undefined);
    await pause(500);
    assert.equal((await readRows(ctx.main)).length, 7);
    assert.equal((await readRows(ctx.pot)).length, 2);
  });
});

test('ClearEmpty_WhenTheConfirmedCountIsRight_OnlyObdisUnlinkedRowsGoAndEverythingElseStays', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const potBefore = await importedIds(ctx.pot);

    const report = await run(ctx, EXPECTS_NOTHING(ctx), { clear_empty: { [ctx.main]: 4 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.removed, 4);
    assert.equal(entry.linked_left, 1);
    assert.equal(entry.foreign_ids, 1);
    assert.equal(await settledCount(ctx.main, 3), 3);
    await pause(500);
    assert.deepEqual(await importedIds(ctx.main), [null, 'FITID-0001', LEG_MAIN.imported_id].sort());
    assert.deepEqual(await importedIds(ctx.pot), potBefore, 'the other account is untouched');
    const legRow = (await readRows(ctx.main)).find((r) => r.imported_id === LEG_MAIN.imported_id);
    assert.ok(legRow.transfer_id, 'the linked leg is still linked to its partner');
  });
});

test('ClearEmpty_WhenTheConfirmedCountExceedsTheRowsHeld_ThePresentRowsGo', async () => {
  // The confirmed count is a ceiling: the audit counts every orphan, linked
  // legs and foreign ids included, so it normally exceeds what is deletable.
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_NOTHING(ctx), { clear_empty: { [ctx.main]: 6 } });

    assert.equal(entryFor(report, ctx.main).removed, 4);
    assert.equal(await settledCount(ctx.main, 3), 3);
  });
});

test('ClearEmpty_WhenOneAccountIsNamed_TheOtherAccountsAreNotPruned', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    await api.addTransactions(ctx.pot, [
      { ...row(hexId('9'), '2026-09-09', -99, 'Stale'), cleared: true },
    ]);
    assert.equal((await readRows(ctx.pot)).length, 3);

    const report = await run(ctx, EXPECTS_NOTHING(ctx), { clear_empty: { [ctx.main]: 4 } });

    assert.equal(entryFor(report, ctx.pot), undefined);
    await settledCount(ctx.main, 3);
    await pause(500);
    assert.equal((await readRows(ctx.pot)).length, 3, 'the stale POT row was not the press');
  });
});

test('Confirmed_WhenAnOrdinaryAccountHoldsMoreOrphansThanWereShown_NothingIsDeleted', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_P1(ctx), { confirmed: { [ctx.main]: 2 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.holds, 3);
    assert.equal(entry.confirmed, 2);
    assert.ok(entry.refused);
    await pause(500);
    assert.equal((await readRows(ctx.main)).length, 7);
  });
});

test('Confirmed_WhenAnOrdinaryAccountHoldsNoMoreThanWasShown_TheOrphansGo', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_P1(ctx), { confirmed: { [ctx.main]: 3 } });

    assert.equal(entryFor(report, ctx.main).removed, 3);
    assert.equal(await settledCount(ctx.main, 4), 4);
    await pause(500);
    assert.deepEqual(
      await importedIds(ctx.main),
      [null, 'FITID-0001', LEG_MAIN.imported_id, P[0].imported_id].sort(),
    );
    assert.equal((await readRows(ctx.pot)).length, 2);
  });
});

test('Confirmed_WhenNoCountWasShownForTheAccount_TheOrdinaryPruneRunsAsBefore', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);

    const report = await run(ctx, EXPECTS_P1(ctx), { confirmed: { [ctx.pot]: 5 } });

    assert.equal(entryFor(report, ctx.main).removed, 3);
    assert.equal(entryFor(report, ctx.pot).removed, 0);
    assert.equal(await settledCount(ctx.main, 4), 4);
  });
});
