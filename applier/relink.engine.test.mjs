/**
 * A push that re-links a transfer whose partner changed, against the REAL
 * budget engine (offline, one temporary budget per test; nothing here touches
 * a network).
 *
 * The situation is the one a rebuild creates: obdi's pairing of a leg changed,
 * so Actual holds A linked to B while the request now says A pairs with C.
 * Expected answers were fixed before the first run:
 *
 *   MAIN  A -150000 (linked to B, request says C)
 *   POT   B +150000 (A's old partner), C +150000 (A's new partner, unlinked)
 *
 *   after: A and C are one transfer; B is an ordinary unlinked row that still
 *          exists; no row was created or deleted; every balance is as before;
 *          the result counts one re-link, and no link and no already-linked.
 *
 * Rows carry ids of obdi's real shape (64 hex digits, a colon, a counter)
 * because the rule asks whether a row is obdi's own by its imported id.
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts, pruneAccounts } from './audit.mjs';
import { applyAccounts, linkTransfers } from './lib.mjs';
import { auditTransfers } from './transfers.mjs';

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
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-relink-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`relink-${budgets}`, async () => {});
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
const B = row(hexId('b'), '2026-09-02', 150000, 'Current account');
const C = row(hexId('c'), '2026-09-02', 150000, 'Current account');
const A2 = row(hexId('d'), '2026-09-03', -150000, 'Savings pot');
const B2 = row(hexId('e'), '2026-09-03', 150000, 'Current account');
const A3 = row(hexId('f'), '2026-09-04', -150000, 'Savings pot');
const B3 = row(hexId('1'), '2026-09-04', 150000, 'Current account');
const STRANGER = row('somebody-elses-id', '2026-09-02', 150000, 'Somebody');

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

const pair = (ctx, debit, credit) => ({
  debit: leg(ctx.main, debit),
  credit: leg(ctx.pot, credit),
});

const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// What the budget holds, with Actual's own ids replaced by the imported id of
// whatever each row points at, so the answer can be written down in advance.
async function shape(ctx) {
  const rows = [...(await readRows(ctx.main)), ...(await readRows(ctx.pot))];
  const byId = new Map(rows.map((r) => [r.id, r.imported_id]));
  return {
    links: Object.fromEntries(
      rows
        .map((r) => [r.imported_id, r.transfer_id ? (byId.get(r.transfer_id) ?? 'UNKNOWN ROW') : null])
        .sort(([x], [y]) => String(x).localeCompare(String(y))),
    ),
    count: rows.length,
    main: await api.getAccountBalance(ctx.main),
    pot: await api.getAccountBalance(ctx.pot),
  };
}

async function stocked(ctx, accounts, initialPairs) {
  await applyAccounts(api, {
    [ctx.main]: accounts.main,
    [ctx.pot]: accounts.pot,
  });
  const linked = await linkTransfers(api, initialPairs);
  assert.equal(linked.counts.linked, initialPairs.length);
  assert.equal(linked.counts.failed, 0);
}

function spied() {
  const updates = [];
  return {
    updates,
    client: new Proxy(api, {
      get(target, property) {
        if (property === 'updateTransaction') {
          return async (...args) => {
            updates.push(args);
            return target.updateTransaction(...args);
          };
        }
        return target[property];
      },
    }),
  };
}

test('TransferRelink_WhenALegsPartnerChangedAndTheOldPartnerIsObdis_LegLinksToTheNewPartnerAndTheOldIsLeftPlain', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [B, C] }, [pair(ctx, A, B)]);
    const before = await shape(ctx);
    assert.equal(before.links[A.imported_id], B.imported_id);

    const result = await linkTransfers(api, [pair(ctx, A, C)]);

    assert.deepEqual(
      { ...result.counts, skipped_pairs: undefined, relinked_pairs: undefined },
      {
        pairs: 1,
        linked: 0,
        relinked: 1,
        already_linked: 0,
        skipped: {},
        skipped_pairs: undefined,
        relinked_pairs: undefined,
        failed: 0,
      },
    );
    assert.deepEqual(result.counts.relinked_pairs, [
      { debit_account: ctx.main, credit_account: ctx.pot, date: A.date },
    ]);
    assert.deepEqual(await shape(ctx), {
      links: {
        [A.imported_id]: C.imported_id,
        [B.imported_id]: null,
        [C.imported_id]: A.imported_id,
      },
      count: 3,
      main: before.main,
      pot: before.pot,
    });
    assert.ok(result.lines.some((line) => line.includes('re-linked')));
  });
});

test('TransferRelink_WhenTheOldPartnerIsAnOrphanTheRequestDoesNotExpect_ItIsUnlinkedNotDeletedAndTheRemovalOwnsIt', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [B, C] }, [pair(ctx, A, B)]);
    const expected = { [ctx.main]: [A], [ctx.pot]: [C] };

    const result = await linkTransfers(api, [pair(ctx, A, C)]);

    assert.equal(result.counts.relinked, 1);
    const shaped = await shape(ctx);
    assert.equal(shaped.count, 3, 'the orphan is not deleted by a push');
    assert.equal(shaped.links[B.imported_id], null);
    const audited = await auditAccounts(api, expected);
    assert.equal(audited.find((e) => e.account_id === ctx.pot).orphaned, 1);
    assert.equal(audited.find((e) => e.account_id === ctx.pot).orphaned_will_go, 1);

    const report = await quietly(() =>
      pruneAccounts(api, expected, { confirmed: { [ctx.pot]: 1 } }),
    );

    const entry = report.find((e) => e.account_id === ctx.pot);
    assert.equal(entry.removed, 1);
    assert.equal(entry.unlinked, undefined, 'it was already a plain row, so nothing needed unlinking');
    for (let i = 0; i < 100 && (await readRows(ctx.pot)).length !== 1; i += 1) await pause(50);
    assert.deepEqual((await readRows(ctx.pot)).map((r) => r.imported_id), [C.imported_id]);
    assert.equal(await api.getAccountBalance(ctx.pot), 150000);
  });
});

test('TransferRelink_WhenTheOldPartnerHasNoObdiImportedId_PairIsRefusedWithTheReasonAndNothingChanges', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [STRANGER, C] }, [
      { debit: leg(ctx.main, A), credit: leg(ctx.pot, STRANGER) },
    ]);
    const before = await shape(ctx);
    const { updates, client } = spied();

    const result = await linkTransfers(client, [pair(ctx, A, C)]);

    assert.equal(result.counts.skipped.linked_elsewhere, 1);
    assert.equal(result.counts.relinked, 0);
    assert.equal(result.counts.linked, 0);
    assert.deepEqual(updates, []);
    assert.deepEqual(await shape(ctx), before);
  });
});

test('TransferRelink_WhenTheOldPartnerIsAHandMadeRowWithNoImportedId_PairIsRefusedAndNothingChanges', async () => {
  await withBudget(async (ctx) => {
    await applyAccounts(api, { [ctx.main]: [A], [ctx.pot]: [C] });
    const handMade = await api.addTransactions(ctx.pot, [
      { date: '2026-09-02', amount: 150000, cleared: true },
    ]);
    assert.equal(handMade, 'ok');
    const mine = (await readRows(ctx.pot)).find((r) => !r.imported_id);
    const mainRow = (await readRows(ctx.main))[0];
    const payees = await api.getPayees();
    const toPot = payees.find((p) => p.transfer_acct === ctx.pot).id;
    await api.updateTransaction(mainRow.id, { payee: toPot, transfer_id: mine.id });
    // The engine applies an update after the call resolves, so the setup is
    // read until it shows before the test relies on it.
    for (let i = 0; i < 100; i += 1) {
      const now = (await readRows(ctx.main))[0];
      if (now.transfer_id === mine.id) break;
      await pause(50);
    }
    const before = await shape(ctx);
    assert.equal((await readRows(ctx.main))[0].transfer_id, mine.id);
    const { updates, client } = spied();

    const result = await linkTransfers(client, [pair(ctx, A, C)]);

    assert.equal(result.counts.skipped.linked_elsewhere, 1);
    assert.deepEqual(updates, []);
    assert.deepEqual(await shape(ctx), before);
  });
});

test('TransferRelink_WhenTheNewPartnerIsLinkedToAnotherObdiRow_ItIsReleasedAndBothPairsEndConsistent', async () => {
  await withBudget(async (ctx) => {
    // A-B linked and A2-C linked; the request wants A-C only.
    await stocked(ctx, { main: [A, A2], pot: [B, C] }, [pair(ctx, A, B), pair(ctx, A2, C)]);

    const result = await linkTransfers(api, [pair(ctx, A, C)]);

    assert.equal(result.counts.relinked, 1);
    assert.equal(result.counts.failed, 0);
    assert.deepEqual((await shape(ctx)).links, {
      [A.imported_id]: C.imported_id,
      [A2.imported_id]: null,
      [B.imported_id]: null,
      [C.imported_id]: A.imported_id,
    });
  });
});

const SWAP_PAIRS = (ctx) => [pair(ctx, A, B2), pair(ctx, A2, B)];

for (const [order, pick] of [
  ['InRequestOrder', (pairs) => pairs],
  ['InReverseOrder', (pairs) => [...pairs].reverse()],
]) {
  test(`TransferRelink_WhenTwoLinkedPairsSwapPartners_${order}_BothEndLinkedAsRequested`, async () => {
    await withBudget(async (ctx) => {
      // A-B and A2-B2 are linked; the request wants A-B2 and A2-B.
      await stocked(ctx, { main: [A, A2], pot: [B, B2] }, [pair(ctx, A, B), pair(ctx, A2, B2)]);
      const before = await shape(ctx);

      const result = await linkTransfers(api, pick(SWAP_PAIRS(ctx)));

      assert.equal(result.counts.relinked, 2);
      assert.equal(result.counts.linked, 0);
      assert.equal(result.counts.failed, 0);
      assert.deepEqual(result.counts.skipped, {});
      assert.deepEqual(await shape(ctx), {
        links: {
          [A.imported_id]: B2.imported_id,
          [A2.imported_id]: B.imported_id,
          [B.imported_id]: A2.imported_id,
          [B2.imported_id]: A.imported_id,
        },
        count: 4,
        main: before.main,
        pot: before.pot,
      });
      const again = await linkTransfers(api, SWAP_PAIRS(ctx));
      assert.equal(again.counts.already_linked, 2);
    });
  });
}

const ROTATION = (ctx) => [pair(ctx, A, B2), pair(ctx, A2, B3), pair(ctx, A3, B)];

for (const [order, pick] of [
  ['InRequestOrder', (pairs) => pairs],
  ['InReverseOrder', (pairs) => [...pairs].reverse()],
  ['InShuffledOrder', (pairs) => [pairs[1], pairs[2], pairs[0]]],
]) {
  test(`TransferRelink_WhenThreeLinkedPairsRotatePartners_${order}_AllThreeEndLinkedAsRequested`, async () => {
    await withBudget(async (ctx) => {
      await stocked(ctx, { main: [A, A2, A3], pot: [B, B2, B3] }, [
        pair(ctx, A, B),
        pair(ctx, A2, B2),
        pair(ctx, A3, B3),
      ]);
      const before = await shape(ctx);

      const result = await linkTransfers(api, pick(ROTATION(ctx)));

      assert.equal(result.counts.relinked, 3);
      assert.equal(result.counts.failed, 0);
      assert.deepEqual(await shape(ctx), {
        links: {
          [A.imported_id]: B2.imported_id,
          [A2.imported_id]: B3.imported_id,
          [A3.imported_id]: B.imported_id,
          [B.imported_id]: A3.imported_id,
          [B2.imported_id]: A.imported_id,
          [B3.imported_id]: A2.imported_id,
        },
        count: 6,
        main: before.main,
        pot: before.pot,
      });
    });
  });
}

test('TransferRelink_WhenARequestRelinksOnePairAndLeavesAnotherAlone_TheOtherCostsNoUpdate', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A, A2], pot: [B, C, B2] }, [pair(ctx, A, B), pair(ctx, A2, B2)]);
    const { updates, client } = spied();

    const result = await linkTransfers(client, [pair(ctx, A, C), pair(ctx, A2, B2)]);

    assert.equal(result.counts.relinked, 1);
    assert.equal(result.counts.already_linked, 1);
    const touched = new Set(updates.map(([id]) => id));
    const rows = [...(await readRows(ctx.main)), ...(await readRows(ctx.pot))];
    const idOf = (r) => rows.find((x) => x.imported_id === r.imported_id).id;
    assert.equal(touched.has(idOf(A2)), false);
    assert.equal(touched.has(idOf(B2)), false);
  });
});

test('TransferRelink_WhenANeededPartnerIsReconciled_PairIsRefusedAsReconciledAndNothingChanges', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [B, C] }, [pair(ctx, A, B)]);
    const b = (await readRows(ctx.pot)).find((r) => r.imported_id === B.imported_id);
    await api.updateTransaction(b.id, { reconciled: true });
    for (let i = 0; i < 100; i += 1) {
      const now = (await readRows(ctx.pot)).find((r) => r.id === b.id);
      if (now.reconciled) break;
      await pause(50);
    }
    const before = await shape(ctx);
    const { updates, client } = spied();

    const result = await linkTransfers(client, [pair(ctx, A, C)]);

    assert.equal(result.counts.skipped.reconciled, 1);
    assert.equal(result.counts.relinked, 0);
    assert.deepEqual(updates, []);
    assert.deepEqual(await shape(ctx), before);
  });
});

test('TransferRelink_WhenTheLegItselfIsReconciled_PairIsRefusedAsReconciled', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [B, C] }, [pair(ctx, A, B)]);
    const a = (await readRows(ctx.main))[0];
    await api.updateTransaction(a.id, { reconciled: true });
    for (let i = 0; i < 100; i += 1) {
      if ((await readRows(ctx.main))[0].reconciled) break;
      await pause(50);
    }
    const before = await shape(ctx);

    const result = await linkTransfers(api, [pair(ctx, A, C)]);

    assert.equal(result.counts.skipped.reconciled, 1);
    assert.deepEqual(await shape(ctx), before);
  });
});

test('TransferRelink_WhenAnotherPairInTheRequestIsAlreadyLinkedOnTheSameRow_TheRelinkIsRefusedAndTheEstablishedPairStays', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A, A2], pot: [B, C] }, [pair(ctx, A, B)]);

    // The request says both A-B (already so) and A2-B: B cannot be both.
    const result = await linkTransfers(api, [pair(ctx, A, B), pair(ctx, A2, B)]);

    assert.equal(result.counts.already_linked, 1);
    assert.equal(result.counts.relinked, 0);
    assert.equal(result.counts.skipped.linked_elsewhere, 1);
    assert.equal((await shape(ctx)).links[A.imported_id], B.imported_id);
  });
});

test('Audit_WhenAPairWouldBeRelinked_TheAuditCountsItUnlinkedAndTheCountsAreUnchangedInShape', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx, { main: [A], pot: [B, C] }, [pair(ctx, A, B)]);

    const pairs = await auditTransfers(api, [pair(ctx, A, C)]);

    assert.deepEqual(pairs, {
      pairs: 1,
      linked: 0,
      unlinked: 1,
      leg_missing: 0,
      by_account: {
        [ctx.main]: { pairs: 1, linked: 0 },
        [ctx.pot]: { pairs: 1, linked: 0 },
      },
    });
  });
});
