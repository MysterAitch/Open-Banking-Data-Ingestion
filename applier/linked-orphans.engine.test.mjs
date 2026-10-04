/**
 * Removing an orphaned import that is one leg of a linked transfer, against
 * the REAL budget engine (offline, one temporary budget per test; nothing
 * here touches a network).
 *
 * The situation is the one a sub-account ("Space") creates. MAIN once held
 * rows that merely repeated payments held under SPACE. obdi stopped counting
 * them, so in Actual they are orphans in MAIN - and two of them were linked
 * as transfers to NAT rows that obdi still expects. Deleting such an orphan
 * outright takes the NAT row with it (the first test measures that), so the
 * removal unlinks first.
 *
 * The household is invented. Expected answers were fixed before the first run:
 *
 *   MAIN  M1   -2500   expected, plain
 *         O1 -150000   ORPHAN, linked to N1 (dated a day later: the legs of a
 *                      transfer need not share a date)
 *         O2  -40000   ORPHAN, linked to N2
 *   NAT   N1 +150000   expected, linked to O1
 *         N2  +40000   expected, linked to O2
 *         N3    -700   expected, plain
 *   SPACE S1 -150000   expected, plain  (the payment N1 really pairs with)
 *         S2  -40000   expected, plain
 *
 *   before:  MAIN 3 rows, balance -192500; NAT 3 rows, 189300; SPACE 2 rows, -190000
 *   removal: MAIN 1 row, balance -2500; NAT and SPACE untouched in amount,
 *            date, account, imported id, and count; N1 and N2 no longer linked
 *   then linking S1-N1 and S2-N2: both pairs linked, every balance agrees.
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
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-linked-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`linked-orphans-${budgets}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        const nat = await api.createAccount({ name: 'NAT' }, 0);
        const space = await api.createAccount({ name: 'SPACE' }, 0);
        return await work({ main, nat, space });
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

const M1 = row(hexId('1'), '2026-09-01', -2500, 'Cafe');
const O1 = row(hexId('a'), '2026-09-07', -150000, 'Savings');
const O2 = row(hexId('b'), '2026-09-08', -40000, 'Savings');
const N1 = row(hexId('c'), '2026-09-08', 150000, 'Current');
const N2 = row(hexId('d'), '2026-09-09', 40000, 'Current');
const N3 = row(hexId('9'), '2026-09-02', -700, 'Shop');
const S1 = row(hexId('e'), '2026-09-07', -150000, 'Nationwide');
const S2 = row(hexId('f'), '2026-09-08', -40000, 'Nationwide');

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');
const find = async (accountId, r) =>
  (await readRows(accountId)).find((x) => x.imported_id === r.imported_id);

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

// The bank-owned facts of a row and where it lives: what "otherwise
// unchanged" means for a partner.
const facts = (r) => ({
  account: r.account,
  amount: r.amount,
  date: r.date,
  imported_id: r.imported_id,
  cleared: r.cleared,
});

const EXPECTED = (ctx) => ({
  [ctx.main]: [M1],
  [ctx.nat]: [N1, N2, N3],
  [ctx.space]: [S1, S2],
});

async function stocked(ctx, { link = true } = {}) {
  await applyAccounts(api, {
    [ctx.main]: [M1, O1, O2],
    [ctx.nat]: [N1, N2, N3],
    [ctx.space]: [S1, S2],
  });
  if (link) {
    await linkTransfers(api, [
      { debit: leg(ctx.main, O1), credit: leg(ctx.nat, N1) },
      { debit: leg(ctx.main, O2), credit: leg(ctx.nat, N2) },
    ]);
  }
}

const balances = async (ctx) => ({
  main: await api.getAccountBalance(ctx.main),
  nat: await api.getAccountBalance(ctx.nat),
  space: await api.getAccountBalance(ctx.space),
});

const run = (client, accounts, options) =>
  quietly(() => pruneAccounts(client, accounts, options));

const entryFor = (report, accountId) => report.find((e) => e.account_id === accountId);

// A client that behaves as the engine does except where told otherwise.
const overriding = (overrides) =>
  new Proxy(api, {
    get(target, property) {
      return Object.prototype.hasOwnProperty.call(overrides, property)
        ? overrides[property]
        : target[property];
    },
  });

test('Hazard_WhenALinkedLegIsDeletedWithoutUnlinking_TheOtherLegVanishesToo', async () => {
  // The reason the removal unlinks first. Measured on the pinned library:
  // the delete cascades through the DELETED row's own transfer_id, so it does
  // not help that the partner has already stopped pointing back.
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const orphan = await find(ctx.main, O1);
    const partner = await find(ctx.nat, N1);
    assert.equal(orphan.transfer_id, partner.id);

    await api.deleteTransaction(orphan.id);

    assert.equal(await settledCount(ctx.nat, 2), 2, 'N1 went with O1');
    assert.equal(await find(ctx.nat, N1), undefined);
  });
});

test('Hazard_WhenOnlyThePartnerIsUnlinkedAndTheLinkedLegDeleted_ThePartnerStillVanishes', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const orphan = await find(ctx.main, O1);
    const partner = await find(ctx.nat, N1);
    await api.updateTransaction(partner.id, { transfer_id: null, payee: null });
    await pause(300);

    await api.deleteTransaction(orphan.id);

    assert.equal(await settledCount(ctx.nat, 2), 2);
    assert.equal(await find(ctx.nat, N1), undefined);
  });
});

test('LinkedOrphans_WhenThePartnerIsExpected_OrphanGoesPartnerStaysUnlinkedAndUnchanged', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const natBefore = (await readRows(ctx.nat)).map(facts);
    const spaceBefore = (await readRows(ctx.space)).map(facts);
    assert.deepEqual(await balances(ctx), { main: -192500, nat: 189300, space: -190000 });

    const report = await run(api, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.removed, 2);
    assert.equal(entry.unlinked, 2);
    assert.equal(entry.stopped, undefined);
    assert.equal(await settledCount(ctx.main, 1), 1);
    await pause(500);
    assert.deepEqual((await readRows(ctx.main)).map((r) => r.imported_id), [M1.imported_id]);
    assert.deepEqual((await readRows(ctx.nat)).map(facts), natBefore, 'NAT rows are unchanged');
    assert.deepEqual((await readRows(ctx.space)).map(facts), spaceBefore);
    for (const r of await readRows(ctx.nat)) {
      assert.equal(r.transfer_id ?? null, null, 'no leg still points at a deleted row');
    }
    assert.deepEqual(await balances(ctx), { main: -2500, nat: 189300, space: -190000 });
  });
});

test('LinkedOrphans_WhenThePushRelinksAndThenTheRemovalRuns_EveryAccountAuditsClean', async () => {
  // The push releases N1 and N2 from the orphans and links them to the
  // payments obdi now pairs them with; the orphans are then plain rows, and
  // the removal deletes them with nothing left to unlink.
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const expected = EXPECTED(ctx);
    const pairs = [
      { debit: leg(ctx.space, S1), credit: leg(ctx.nat, N1) },
      { debit: leg(ctx.space, S2), credit: leg(ctx.nat, N2) },
    ];
    const before = await linkTransfers(api, pairs);
    assert.equal(before.counts.relinked, 2);
    assert.equal(before.counts.linked, 0);
    assert.deepEqual(before.counts.skipped, {});

    const report = await run(api, expected, { confirmed: { [ctx.main]: 2 } });
    assert.equal(entryFor(report, ctx.main).removed, 2);
    assert.equal(entryFor(report, ctx.main).unlinked, undefined);
    await settledCount(ctx.main, 1);
    const after = await linkTransfers(api, pairs);

    assert.equal(after.counts.already_linked, 2);
    assert.equal(after.counts.failed, 0);
    assert.deepEqual(after.counts.skipped, {});
    const audited = await auditAccounts(api, expected);
    for (const entry of audited) {
      assert.equal(entry.orphaned, 0, entry.name);
      assert.equal(entry.missing, 0, entry.name);
      assert.equal(entry.balance.agrees, true, entry.name);
    }
    const transfers = await auditTransfers(api, pairs);
    assert.equal(transfers.linked, 2);
    assert.equal(transfers.unlinked, 0);
    assert.deepEqual(await balances(ctx), { main: -2500, nat: 189300, space: -190000 });
  });
});

test('LinkedOrphans_WhenBothLegsAreOrphans_EachAccountRemovesItsOwnLegWithinItsOwnCount', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    // NAT no longer expects N1 and N2 either: both legs of each pair are orphans.
    const expected = { ...EXPECTED(ctx), [ctx.nat]: [N3] };

    const report = await run(api, expected, {
      confirmed: { [ctx.main]: 2, [ctx.nat]: 2 },
    });

    assert.equal(entryFor(report, ctx.main).removed, 2);
    assert.equal(entryFor(report, ctx.nat).removed, 2);
    assert.equal(await settledCount(ctx.main, 1), 1);
    assert.equal(await settledCount(ctx.nat, 1), 1);
    await pause(500);
    assert.deepEqual((await readRows(ctx.nat)).map((r) => r.imported_id), [N3.imported_id]);
    assert.equal((await readRows(ctx.space)).length, 2);
    assert.deepEqual(await balances(ctx), { main: -2500, nat: -700, space: -190000 });
  });
});

test('LinkedOrphans_WhenBothLegsAreOrphansButOnlyOneAccountIsConfirmed_ThePartnerIsUnlinkedNotDeleted', async () => {
  // A leg in another account is deleted only under that account's own count.
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const expected = { ...EXPECTED(ctx), [ctx.nat]: [N3] };

    // NAT was shown a count of 0, so its own removal refuses.
    const report = await run(api, expected, { confirmed: { [ctx.main]: 2, [ctx.nat]: 0 } });

    assert.equal(entryFor(report, ctx.main).removed, 2);
    assert.ok(entryFor(report, ctx.nat).refused);
    assert.equal(await settledCount(ctx.main, 1), 1);
    await pause(500);
    assert.equal((await readRows(ctx.nat)).length, 3, 'NAT rows stay: its count did not allow them to go');
    for (const r of await readRows(ctx.nat)) assert.equal(r.transfer_id ?? null, null);
  });
});

test('LinkedOrphans_WhenThePartnerCarriesNoObdiImportedId_OrphanIsLeftAndThePartnerIsNotTouched', async () => {
  await withBudget(async (ctx) => {
    await applyAccounts(api, { [ctx.main]: [M1, O1], [ctx.nat]: [N3] });
    const foreign = { ...row('FITID-0042', '2026-09-08', 150000, 'Other importer') };
    const hand = { date: '2026-09-08', amount: 150000, payee_name: 'By hand', cleared: true };
    await api.addTransactions(ctx.nat, [foreign]);
    await api.addTransactions(ctx.nat, [hand]);
    const natRows = await readRows(ctx.nat);
    const foreignRow = natRows.find((r) => r.imported_id === 'FITID-0042');
    const handRow = natRows.find((r) => !r.imported_id);
    const transfers = await api.getPayees();
    const toNat = transfers.find((p) => p.transfer_acct === ctx.nat).id;
    const toMain = transfers.find((p) => p.transfer_acct === ctx.main).id;
    const orphan = await find(ctx.main, O1);
    await api.updateTransaction(orphan.id, { payee: toNat, transfer_id: foreignRow.id });
    await api.updateTransaction(foreignRow.id, { payee: toMain, transfer_id: orphan.id });
    await pause(300);
    const expected = { [ctx.main]: [M1], [ctx.nat]: [N3] };
    const beforeNat = await readRows(ctx.nat);

    const report = await run(api, expected, { confirmed: { [ctx.main]: 1 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.removed, 0);
    assert.equal(entry.linked_left, 1);
    assert.deepEqual(entry.left, { partner_not_ours: 1 });
    assert.ok(entry.lines.some((line) => /not an obdi import/.test(line)));
    await pause(500);
    assert.equal((await readRows(ctx.main)).length, 2, 'the orphan stays');
    assert.deepEqual(await readRows(ctx.nat), beforeNat, 'the other importer\'s row is byte-for-byte as it was');
    assert.equal((await readRows(ctx.nat)).find((r) => r.id === foreignRow.id).transfer_id, orphan.id);
    assert.ok(handRow);

    // The person's own row as the partner, too.
    await api.updateTransaction(foreignRow.id, { transfer_id: null, payee: null });
    await api.updateTransaction(orphan.id, { payee: toNat, transfer_id: handRow.id });
    await api.updateTransaction(handRow.id, { payee: toMain, transfer_id: orphan.id });
    await pause(300);
    const beforeHand = await readRows(ctx.nat);

    const again = await run(api, expected, { confirmed: { [ctx.main]: 1 } });

    assert.equal(entryFor(again, ctx.main).removed, 0);
    assert.deepEqual(entryFor(again, ctx.main).left, { partner_not_ours: 1 });
    await pause(500);
    assert.deepEqual(await readRows(ctx.nat), beforeHand);
  });
});

test('LinkedOrphans_WhenThePartnerIsReconciled_OrphanIsLeftAndNothingChanges', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const partner = await find(ctx.nat, N1);
    await api.updateTransaction(partner.id, { reconciled: true });
    await pause(300);
    assert.equal((await find(ctx.nat, N1)).reconciled, true);
    const beforeMain = await readRows(ctx.main);
    const beforeNat = await readRows(ctx.nat);

    // O2's partner is not reconciled, so one of the two goes.
    const report = await run(api, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.removed, 1);
    assert.equal(entry.linked_left, 1);
    assert.deepEqual(entry.left, { reconciled: 1 });
    assert.equal(await settledCount(ctx.main, beforeMain.length - 1), beforeMain.length - 1);
    await pause(500);
    const afterNat = await readRows(ctx.nat);
    assert.deepEqual(afterNat.find((r) => r.id === partner.id), beforeNat.find((r) => r.id === partner.id));
    assert.ok(await find(ctx.main, O1), 'the orphan beside a reconciled partner stays');
    assert.equal(await find(ctx.main, O2), undefined);
  });
});

test('LinkedOrphans_WhenTheOrphanItselfIsReconciled_ItIsLeftWithItsPartnerLinked', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const orphan = await find(ctx.main, O1);
    await api.updateTransaction(orphan.id, { reconciled: true });
    await pause(300);
    const beforeNat = await readRows(ctx.nat);

    const report = await run(api, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    assert.deepEqual(entryFor(report, ctx.main).left, { reconciled: 1 });
    await pause(500);
    assert.ok(await find(ctx.main, O1));
    assert.equal((await find(ctx.nat, N1)).transfer_id, orphan.id, 'N1 is still linked to O1');
    assert.equal((await readRows(ctx.nat)).length, beforeNat.length);
  });
});

test('LinkedOrphans_WhenTheCountShownIsLowerThanWhatIsHeld_NothingIsUnlinkedOrDeleted', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const before = [await readRows(ctx.main), await readRows(ctx.nat)];

    const report = await run(api, EXPECTED(ctx), { confirmed: { [ctx.main]: 1 } });

    const entry = entryFor(report, ctx.main);
    assert.match(entry.refused, /holds 2, more than the 1 confirmed/);
    await pause(500);
    assert.deepEqual([await readRows(ctx.main), await readRows(ctx.nat)], before);
  });
});

test('LinkedOrphans_WhenTheDeleteFailsAfterTheUnlink_TheAccountStopsLoudlyAndThePartnerIsIntact', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const natBefore = (await readRows(ctx.nat)).map(facts);
    const client = overriding({
      deleteTransaction: async () => {
        throw new Error('the engine refused the delete');
      },
    });

    const report = await run(client, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    const entry = entryFor(report, ctx.main);
    assert.equal(entry.removed, 0);
    assert.match(entry.stopped, /the engine refused the delete/);
    assert.match(entry.stopped, /unlinked/);
    await pause(500);
    assert.equal((await readRows(ctx.main)).length, 3, 'no orphan was deleted');
    const orphans = [await find(ctx.main, O1), await find(ctx.main, O2)];
    assert.equal(
      orphans.filter((r) => r.transfer_id).length,
      1,
      'the first orphan was unlinked and stopped on; the second was never attempted',
    );
    assert.deepEqual((await readRows(ctx.nat)).map(facts), natBefore);
  });
});

// The orphan about to be deleted and the NAT row it was linked to.
const PARTNER_OF = new Map([[O1.imported_id, N1], [O2.imported_id, N2]]);
async function partnerOfDeleted(ctx, id) {
  const orphan = (await readRows(ctx.main)).find((r) => r.id === id);
  return find(ctx.nat, PARTNER_OF.get(orphan.imported_id));
}

// Exactly one orphan was reached; the other is still there, still linked.
async function assertSecondOrphanUntouched(ctx) {
  const rows = await readRows(ctx.main);
  const orphans = rows.filter((r) => [O1, O2].some((o) => o.imported_id === r.imported_id));
  assert.equal(orphans.length, 1, 'one orphan was deleted and the next was not attempted');
  assert.ok(orphans[0].transfer_id, 'the one left is still linked');
}

test('LinkedOrphans_WhenThePartnerIsFoundChangedAfterTheDelete_TheAccountStopsLoudly', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const client = overriding({
      deleteTransaction: async (id) => {
        const partner = await partnerOfDeleted(ctx, id);
        await api.deleteTransaction(id);
        await api.updateTransaction(partner.id, { amount: partner.amount + 1 });
      },
    });

    const report = await run(client, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    const entry = entryFor(report, ctx.main);
    assert.match(entry.stopped, /changed/);
    assert.equal(entry.removed, 1, 'the orphan that was deleted is counted; the next is not attempted');
    await pause(500);
    await assertSecondOrphanUntouched(ctx);
  });
});

test('LinkedOrphans_WhenThePartnerIsFoundMissingAfterTheDelete_TheAccountStopsLoudly', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const client = overriding({
      deleteTransaction: async (id) => {
        const partner = await partnerOfDeleted(ctx, id);
        await api.deleteTransaction(id);
        await api.deleteTransaction(partner.id);
      },
    });

    const report = await run(client, EXPECTED(ctx), { confirmed: { [ctx.main]: 2 } });

    assert.match(entryFor(report, ctx.main).stopped, /missing/);
    await pause(500);
    await assertSecondOrphanUntouched(ctx);
  });
});

test('Audit_WhenOrphansAreLinked_ItCountsHowManyWillGoAndHowManyStayByReason', async () => {
  await withBudget(async (ctx) => {
    await stocked(ctx);
    const foreign = row('FITID-0001', '2026-09-03', -5, 'Other importer');
    await api.addTransactions(ctx.main, [foreign]);
    const partner = await find(ctx.nat, N2);
    await api.updateTransaction(partner.id, { reconciled: true });
    await pause(300);

    const report = await auditAccounts(api, EXPECTED(ctx));

    const main = entryFor(report, ctx.main);
    assert.equal(main.orphaned, 3, 'two linked orphans and one foreign id');
    assert.equal(main.orphaned_will_go, 1);
    assert.deepEqual(main.orphaned_will_stay, { reconciled: 1, foreign: 1 });
  });
});
