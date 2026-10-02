/**
 * Long requests, seen from outside: does the heartbeat keep beating while
 * thousands of rows are worked through, and does the work say how far it has
 * got. The fakes resolve at once, as the real library's calls did when a
 * removal of 4,519 rows starved the keepalive for twelve minutes.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const hex = (seed) => seed.repeat(64).slice(0, 64);
const orphanId = (n) => `${hex('e')}:${n}`;

// Each call costs real wall time without ever waiting on I/O, so the loop is
// long enough for a timer to fall due and never idle enough to let it run.
function spin(ms) {
  const until = performance.now() + ms;
  while (performance.now() < until) {
    // busy
  }
}

function pruneFixture(counts) {
  const rows = {};
  const accounts = {};
  const known = [];
  for (const [accountId, count] of Object.entries(counts)) {
    known.push({ id: accountId, name: `name-of-${accountId}` });
    accounts[accountId] = [{ imported_id: `${hex('1')}:0` }];
    rows[accountId] = [
      { id: `${accountId}-kept`, imported_id: `${hex('1')}:0` },
      ...Array.from({ length: count }, (_, n) => ({
        id: `${accountId}-orphan-${n}`,
        imported_id: orphanId(n),
      })),
    ];
  }
  const deleted = [];
  const client = {
    getAccounts: async () => known,
    getTransactions: async (accountId) => rows[accountId],
    deleteTransaction: async (id) => {
      spin(0.1);
      deleted.push(id);
    },
  };
  return { client, accounts, deleted };
}

async function beatsDuring(work) {
  const { startKeepalive } = await import('./watcher.mjs');
  let beats = 0;
  const stop = startKeepalive(async () => {
    beats += 1;
  }, 5);
  try {
    await work();
  } finally {
    stop();
  }
  return beats;
}

test('Keepalive_WhenRemovalDeletesManyRowsThatResolveAtOnce_StillBeats', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const { client, accounts, deleted } = pruneFixture({ 'act-1': 400 });

  const beats = await beatsDuring(() => pruneAccounts(client, accounts));

  assert.equal(deleted.length, 400);
  assert.ok(beats >= 1, `the keepalive never ran during a 400-row removal (${beats} beats)`);
});

test('Keepalive_WhenLinkingManyPairsThatResolveAtOnce_StillBeats', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = { 'act-1': [], 'act-2': [] };
  const transfers = [];
  for (let n = 0; n < 200; n += 1) {
    rows['act-1'].push({ id: `a${n}`, imported_id: `a${n}:0`, amount: -100 });
    rows['act-2'].push({ id: `b${n}`, imported_id: `b${n}:0`, amount: 100 });
    transfers.push({
      debit: { account: 'act-1', imported_id: `a${n}:0`, date: '2026-09-02', amount: -100 },
      credit: { account: 'act-2', imported_id: `b${n}:0`, date: '2026-09-02', amount: 100 },
    });
  }
  const byId = new Map([...rows['act-1'], ...rows['act-2']].map((r) => [r.id, r]));
  const client = {
    getPayees: async () => [
      { id: 'pay-to-1', transfer_acct: 'act-1' },
      { id: 'pay-to-2', transfer_acct: 'act-2' },
    ],
    getTransactions: async (accountId) => rows[accountId],
    updateTransaction: async (id, fields) => {
      spin(0.1);
      Object.assign(byId.get(id), fields);
    },
  };

  let result;
  const beats = await beatsDuring(async () => {
    result = await linkTransfers(client, transfers);
  });

  assert.equal(result.counts.linked, 200);
  assert.ok(beats >= 1, `the keepalive never ran during a 200-pair link (${beats} beats)`);
});

test('Keepalive_WhenCorrectingManyOpeningRowsThatResolveAtOnce_StillBeats', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const rows = {};
  const openings = [];
  for (let n = 0; n < 400; n += 1) {
    const id = `obdi-opening:ref-${n}`;
    rows[`act-${n}`] = [{ id: `row-${n}`, imported_id: id, amount: 1, date: '2026-08-31' }];
    openings.push({ account: `act-${n}`, imported_id: id, amount: 2, date: '2026-08-31' });
  }
  const client = {
    getTransactions: async (accountId) => rows[accountId],
    updateTransaction: async (id, fields) => {
      spin(0.1);
      Object.assign(Object.values(rows).flat().find((r) => r.id === id), fields);
    },
  };

  const beats = await beatsDuring(() =>
    applyOpeningBalances(client, openings, { timeoutMs: 0, intervalMs: 1 }),
  );

  assert.ok(beats >= 1, `the keepalive never ran during 400 corrections (${beats} beats)`);
});

test('Removal_WhenRowsAreDeleted_ReportsRunningCountAgainstTheAccountsTotal', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const { client, accounts } = pruneFixture({ 'act-1': 3, 'act-2': 2 });
  const seen = [];

  await pruneAccounts(client, accounts, { onProgress: (p) => seen.push(p) });

  assert.deepEqual(seen, [
    { account_id: 'act-1', name: 'name-of-act-1', done: 1, total: 3 },
    { account_id: 'act-1', name: 'name-of-act-1', done: 2, total: 3 },
    { account_id: 'act-1', name: 'name-of-act-1', done: 3, total: 3 },
    { account_id: 'act-2', name: 'name-of-act-2', done: 1, total: 2 },
    { account_id: 'act-2', name: 'name-of-act-2', done: 2, total: 2 },
  ]);
});

test('Removal_WhenNothingIsOrphaned_ReportsNoProgressAndCompletes', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const { client, accounts } = pruneFixture({ 'act-1': 0 });
  const seen = [];

  const report = await pruneAccounts(client, accounts, { onProgress: (p) => seen.push(p) });

  assert.deepEqual(seen, []);
  assert.equal(report[0].removed, 0);
});

test('Removal_WhenNoProgressCallbackIsGiven_StillRemoves', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const { client, accounts, deleted } = pruneFixture({ 'act-1': 2 });

  await pruneAccounts(client, accounts);

  assert.equal(deleted.length, 2);
});

test('Linking_WhenPairsAreLinked_ReportsRunningCountAgainstTheTotal', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = { 'act-1': [], 'act-2': [] };
  const transfers = [];
  for (let n = 0; n < 3; n += 1) {
    rows['act-1'].push({ id: `a${n}`, imported_id: `a${n}:0`, amount: -100 });
    rows['act-2'].push({ id: `b${n}`, imported_id: `b${n}:0`, amount: 100 });
    transfers.push({
      debit: { account: 'act-1', imported_id: `a${n}:0`, date: '2026-09-02', amount: -100 },
      credit: { account: 'act-2', imported_id: `b${n}:0`, date: '2026-09-02', amount: 100 },
    });
  }
  const byId = new Map([...rows['act-1'], ...rows['act-2']].map((r) => [r.id, r]));
  const client = {
    getPayees: async () => [
      { id: 'pay-to-1', transfer_acct: 'act-1' },
      { id: 'pay-to-2', transfer_acct: 'act-2' },
    ],
    getTransactions: async (accountId) => rows[accountId],
    updateTransaction: async (id, fields) => Object.assign(byId.get(id), fields),
  };
  const seen = [];

  await linkTransfers(client, transfers, { onProgress: (p) => seen.push(p) });

  assert.deepEqual(seen, [
    { done: 1, total: 3 },
    { done: 2, total: 3 },
    { done: 3, total: 3 },
  ]);
});
