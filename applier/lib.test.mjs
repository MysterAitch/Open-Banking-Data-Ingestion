import test from 'node:test';
import assert from 'node:assert/strict';

test('provisioning reuses an existing account by name and creates the rest', async () => {
  const { provisionAccounts } = await import('./lib.mjs');
  const created = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    createAccount: async (account) => {
      created.push(account.name);
      return `act-new-${created.length}`;
    },
  };

  const result = await provisionAccounts(client, [
    { canonical_id: 'halifax-current', label: 'halifax-current' },
    { canonical_id: 'starling-space-bills', label: 'starling-space-bills' },
  ]);

  assert.deepEqual(created, ['starling-space-bills']);
  assert.deepEqual(result.bindings, [
    { canonical_id: 'halifax-current', actual_account_id: 'act-1' },
    { canonical_id: 'starling-space-bills', actual_account_id: 'act-new-1' },
  ]);
  assert.ok(result.lines[0].includes('reused'));
  assert.ok(result.lines[1].includes('created'));
});

test('provisioning falls back to the canonical id when the label is blank', async () => {
  const { provisionAccounts } = await import('./lib.mjs');
  const client = {
    getAccounts: async () => [],
    createAccount: async (account) => `id-for-${account.name}`,
  };

  const result = await provisionAccounts(client, [
    { canonical_id: 'halifax-card', label: '   ' },
  ]);

  assert.deepEqual(result.bindings, [
    { canonical_id: 'halifax-card', actual_account_id: 'id-for-halifax-card' },
  ]);
});

test('applying imports per account and reports added and updated counts', async () => {
  const { applyAccounts } = await import('./lib.mjs');
  const calls = [];
  const client = {
    importTransactions: async (accountId, transactions) => {
      calls.push({ accountId, count: transactions.length });
      return { added: transactions.map((t) => t.imported_id), updated: [] };
    },
  };

  const result = await applyAccounts(client, {
    'act-1': [{ imported_id: 'a' }, { imported_id: 'b' }],
    'act-2': [{ imported_id: 'c' }],
  });

  assert.deepEqual(calls, [
    { accountId: 'act-1', count: 2 },
    { accountId: 'act-2', count: 1 },
  ]);
  assert.ok(result.lines.length >= 2);
});

function transferClient(rows, { payees, failOn } = {}) {
  const updates = [];
  return {
    updates,
    getPayees: async () =>
      payees ?? [
        { id: 'pay-to-1', transfer_acct: 'act-1' },
        { id: 'pay-to-2', transfer_acct: 'act-2' },
      ],
    getTransactions: async (accountId) => rows[accountId] ?? [],
    updateTransaction: async (id, fields) => {
      if (failOn === id) throw new Error('engine refused');
      updates.push([id, fields]);
      const target = Object.values(rows).flat().find((r) => r.id === id);
      Object.assign(target, fields);
    },
  };
}

const PAIR = [
  {
    debit: { account: 'act-1', imported_id: 'k-a:0', date: '2026-09-02', amount: -500 },
    credit: { account: 'act-2', imported_id: 'k-b:0', date: '2026-09-02', amount: 500 },
  },
];
const freshRows = (extra = {}) => ({
  'act-1': [{ id: 'row-a', imported_id: 'k-a:0', amount: -500, ...extra }],
  'act-2': [{ id: 'row-b', imported_id: 'k-b:0', amount: 500 }],
});

test('linking updates the debit row first, naming the credit account\'s transfer payee', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows());
  const result = await linkTransfers(client, PAIR);
  assert.deepEqual(client.updates, [
    ['row-a', { payee: 'pay-to-2', transfer_id: 'row-b' }],
    ['row-b', { payee: 'pay-to-1', transfer_id: 'row-a' }],
  ]);
  assert.equal(result.counts.linked, 1);
});

test('an account with no transfer payee is skipped and reported, never guessed', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows(), {
    payees: [{ id: 'pay-to-1', transfer_acct: 'act-1' }],
  });
  const result = await linkTransfers(client, PAIR);
  assert.equal(result.counts.skipped.no_transfer_payee, 1);
  assert.deepEqual(client.updates, []);
});

test('a reconciled leg is skipped and reported, not updated', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows({ reconciled: true }));
  const result = await linkTransfers(client, PAIR);
  assert.equal(result.counts.skipped.reconciled, 1);
  assert.deepEqual(client.updates, []);
});

const NAMED_PAIR = [
  {
    debit: {
      account: 'act-1',
      account_name: 'main-current',
      imported_id: 'k-a:0',
      date: '2026-09-02',
      amount: -500,
    },
    credit: {
      account: 'act-2',
      account_name: 'bills-space',
      imported_id: 'k-b:0',
      date: '2026-09-02',
      amount: 500,
    },
  },
];

test('a skipped pair is named by its two accounts, its date, and its reason, with no figure', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows({ reconciled: true }));
  const result = await linkTransfers(client, NAMED_PAIR);
  assert.deepEqual(result.counts.skipped_pairs, [
    {
      debit_account: 'main-current',
      credit_account: 'bills-space',
      date: '2026-09-02',
      reason: 'reconciled',
    },
  ]);
  assert.ok(!JSON.stringify(result.counts.skipped_pairs).includes('500'));
});

test('a pair whose rows disagree on the amount is named with that reason', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = freshRows();
  rows['act-2'][0].amount = 499;
  const result = await linkTransfers(transferClient(rows), NAMED_PAIR);
  assert.equal(result.counts.skipped_pairs[0].reason, 'amounts_not_opposite');
});

test('a pair that links is not listed among the skipped', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const result = await linkTransfers(transferClient(freshRows()), NAMED_PAIR);
  assert.equal(result.counts.linked, 1);
  assert.deepEqual(result.counts.skipped_pairs, []);
});

test('an envelope without account names falls back to the Actual account ids', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const result = await linkTransfers(transferClient(freshRows({ reconciled: true })), PAIR);
  assert.equal(result.counts.skipped_pairs[0].debit_account, 'act-1');
  assert.equal(result.counts.skipped_pairs[0].credit_account, 'act-2');
});

test('the named list is capped while the counts stay complete', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = { 'act-1': [], 'act-2': [] };
  const pairs = [];
  for (let n = 0; n < 60; n += 1) {
    rows['act-1'].push({ id: `a${n}`, imported_id: `k-a${n}:0`, amount: -5, reconciled: true });
    rows['act-2'].push({ id: `b${n}`, imported_id: `k-b${n}:0`, amount: 5 });
    pairs.push({
      debit: { account: 'act-1', imported_id: `k-a${n}:0`, date: '2026-09-02', amount: -5 },
      credit: { account: 'act-2', imported_id: `k-b${n}:0`, date: '2026-09-02', amount: 5 },
    });
  }
  const result = await linkTransfers(transferClient(rows), pairs);
  assert.equal(result.counts.skipped.reconciled, 60);
  assert.equal(result.counts.skipped_pairs.length, 50);
});

test('an update the engine refuses is counted as failed and named, and the next pair still runs', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = freshRows();
  rows['act-1'].push({ id: 'row-c', imported_id: 'k-c:0', amount: -700 });
  rows['act-2'].push({ id: 'row-d', imported_id: 'k-d:0', amount: 700 });
  const second = {
    debit: { account: 'act-1', imported_id: 'k-c:0', date: '2026-09-02', amount: -700 },
    credit: { account: 'act-2', imported_id: 'k-d:0', date: '2026-09-02', amount: 700 },
  };
  const client = transferClient(rows, { failOn: 'row-a' });
  const result = await linkTransfers(client, [...PAIR, second]);
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.linked, 1);
  assert.ok(result.lines.some((line) => line.includes('engine refused')));
});

// A stand-in for the pause between re-reads that records, rather than spends, it.
function recordedSleep() {
  const pauses = [];
  return { pauses, sleep: async (ms) => void pauses.push(ms) };
}

test('an update that leaves the rows unlinked is reported as a failed verification', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows());
  client.updateTransaction = async () => {};
  const { sleep } = recordedSleep();
  const result = await linkTransfers(client, PAIR, { sleep });
  assert.equal(result.counts.linked, 0);
  assert.equal(result.counts.failed, 1);
  assert.ok(result.lines.some((line) => line.includes('verification')));
});

// updateTransaction resolves before the change can be read, as it does for an
// opening row; the link becomes readable only once `becomesReadableAfter`
// re-reads have been made since the updates.
function slowLinkClient(rows, becomesReadableAfter) {
  const client = transferClient(rows);
  const pending = [];
  let readsSinceUpdate = 0;
  client.updateTransaction = async (id, fields) => {
    client.updates.push([id, fields]);
    pending.push([id, fields]);
    readsSinceUpdate = 0;
  };
  const read = client.getTransactions;
  client.getTransactions = async (accountId) => {
    if (pending.length && readsSinceUpdate >= becomesReadableAfter) {
      for (const [id, fields] of pending.splice(0)) {
        Object.assign(Object.values(rows).flat().find((r) => r.id === id), fields);
      }
    }
    readsSinceUpdate += 1;
    return read(accountId);
  };
  return client;
}

test('Linking_WhenLinkOnlyReadsBackOnTheSecondReRead_CountsLinkedNotFailed', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  // Each verification read touches two accounts, so n whole reads is 2n calls.
  const client = slowLinkClient(freshRows(), 4);
  const { pauses, sleep } = recordedSleep();
  const result = await linkTransfers(client, PAIR, {
    sleep,
    settle: { attempts: 5, intervalMs: 123 },
  });
  assert.equal(result.counts.linked, 1);
  assert.equal(result.counts.failed, 0);
  assert.deepEqual(pauses, [123, 123]);
  assert.ok(!result.lines.some((line) => line.includes('FAILED')));
});

test('Linking_WhenLinkNeverReadsBack_IsFailedAfterTheBoundedAttemptsAndNamed', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = freshRows();
  const client = transferClient(rows);
  client.updateTransaction = async () => {};
  let reads = 0;
  const read = client.getTransactions;
  client.getTransactions = async (accountId) => {
    reads += 1;
    return read(accountId);
  };
  const { pauses, sleep } = recordedSleep();
  const result = await linkTransfers(client, PAIR, {
    sleep,
    settle: { attempts: 3, intervalMs: 7 },
  });
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.linked, 0);
  assert.ok(result.lines.some((line) => line.includes('k-a:0 -> k-b:0') && line.includes('verification')));
  // The pre-link read, the first check, then three bounded re-reads: two accounts each.
  assert.equal(pauses.length, 3);
  assert.equal(reads, 2 + 2 + 3 * 2);
});

test('Linking_WhenLinkedAtOnce_CostsNoExtraWaitOrRead', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows());
  let reads = 0;
  const read = client.getTransactions;
  client.getTransactions = async (accountId) => {
    reads += 1;
    return read(accountId);
  };
  const { pauses, sleep } = recordedSleep();
  const result = await linkTransfers(client, PAIR, { sleep });
  assert.equal(result.counts.linked, 1);
  assert.deepEqual(pauses, []);
  assert.equal(reads, 4);
});

test('Linking_WhenOnePairLagsAndAnotherIsImmediate_BothCountLinkedAndNeitherFailed', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = freshRows();
  rows['act-1'].push({ id: 'row-c', imported_id: 'k-c:0', amount: -700 });
  rows['act-2'].push({ id: 'row-d', imported_id: 'k-d:0', amount: 700 });
  const second = {
    debit: { account: 'act-1', imported_id: 'k-c:0', date: '2026-09-02', amount: -700 },
    credit: { account: 'act-2', imported_id: 'k-d:0', date: '2026-09-02', amount: 700 },
  };
  const client = transferClient(rows);
  const apply = client.updateTransaction;
  const held = [];
  client.updateTransaction = async (id, fields) => {
    if (id === 'row-a' || id === 'row-b') held.push([id, fields]);
    else await apply(id, fields);
  };
  let rereads = 0;
  const read = client.getTransactions;
  client.getTransactions = async (accountId) => {
    rereads += 1;
    // Release the held pair on the third whole pass over the rows.
    if (rereads > 6) for (const [id, fields] of held.splice(0)) await apply(id, fields);
    return read(accountId);
  };
  const { sleep } = recordedSleep();
  const result = await linkTransfers(client, [...PAIR, second], {
    sleep,
    settle: { attempts: 5, intervalMs: 1 },
  });
  assert.equal(result.counts.linked, 2);
  assert.equal(result.counts.failed, 0);
});

// An opening row's reserved id, and a stand-in that applies updates at once.
const OPENING = 'obdi-opening:halifax-current';
const NO_WAIT = { timeoutMs: 0, intervalMs: 1 };

function openingClient(rows, { failOn, applyUpdates = true } = {}) {
  const updates = [];
  let reads = 0;
  return {
    updates,
    get reads() {
      return reads;
    },
    getTransactions: async (accountId) => {
      reads += 1;
      return rows[accountId] ?? [];
    },
    updateTransaction: async (id, fields) => {
      if (failOn === id) throw new Error('engine refused');
      updates.push([id, fields]);
      if (!applyUpdates) return;
      Object.assign(Object.values(rows).flat().find((r) => r.id === id), fields);
    },
  };
}

const ENTRY = { account: 'act-1', imported_id: OPENING, date: '2026-08-31', amount: 100000 };
const openingRows = (extra = {}) => ({
  'act-1': [{ id: 'row-o', imported_id: OPENING, amount: 100000, date: '2026-08-31', ...extra }],
});

test('an opening row that already matches is counted and costs no update', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient(openingRows());
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.equal(result.counts.already_right, 1);
  assert.equal(result.counts.corrected, 0);
  assert.deepEqual(client.updates, []);
});

test('a differing amount is corrected with an update naming only amount and date', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient(openingRows({ amount: 99999 }));
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.deepEqual(client.updates, [['row-o', { amount: 100000, date: '2026-08-31' }]]);
  assert.equal(result.counts.corrected, 1);
  assert.equal(result.counts.already_right, 0);
});

test('a differing date alone is corrected too', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient(openingRows({ date: '2026-08-01' }));
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.equal(result.counts.corrected, 1);
  assert.equal(client.updates.length, 1);
});

test('an opening row that is not in Actual is counted missing and never created', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient({ 'act-1': [] });
  client.importTransactions = async () => {
    throw new Error('must not create');
  };
  client.addTransactions = client.importTransactions;
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.equal(result.counts.missing, 1);
  assert.deepEqual(client.updates, []);
  assert.ok(result.lines.some((line) => line.includes('nothing was created')));
});

test('two rows carrying one opening id are reported ambiguous and neither is changed', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const rows = openingRows({ amount: 1 });
  rows['act-1'].push({ id: 'row-twin', imported_id: OPENING, amount: 2, date: '2026-08-31' });
  const client = openingClient(rows);
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.equal(result.counts.ambiguous, 1);
  assert.deepEqual(client.updates, []);
});

test('an entry that does not name an opening id is refused before any read or update', async () => {
  // The update is keyed by imported id, so an envelope naming a payment's id
  // would otherwise be a way to rewrite that payment.
  const { applyOpeningBalances } = await import('./lib.mjs');
  const paymentId = `${'a'.repeat(64)}:0`;
  const client = openingClient({
    'act-1': [{ id: 'row-p', imported_id: paymentId, amount: -5, date: '2026-09-01' }],
  });
  const result = await applyOpeningBalances(
    client,
    [{ account: 'act-1', imported_id: paymentId, date: '2026-09-01', amount: 7 }],
    NO_WAIT,
  );
  assert.equal(result.counts.refused, 1);
  assert.deepEqual(client.updates, []);
  assert.equal(client.reads, 0);
});

test('an update the engine refuses is counted as failed and named, and the next entry still runs', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const rows = openingRows({ amount: 1 });
  rows['act-2'] = [
    { id: 'row-q', imported_id: 'obdi-opening:other', amount: 1, date: '2026-08-31' },
  ];
  const client = openingClient(rows, { failOn: 'row-o' });
  const result = await applyOpeningBalances(
    client,
    [ENTRY, { account: 'act-2', imported_id: 'obdi-opening:other', date: '2026-08-31', amount: 5 }],
    NO_WAIT,
  );
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.corrected, 1);
  assert.ok(result.lines.some((line) => line.includes('engine refused')));
});

test('an update that leaves the row unchanged is reported as a failed verification', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient(openingRows({ amount: 99999 }), { applyUpdates: false });
  const result = await applyOpeningBalances(client, [ENTRY], NO_WAIT);
  assert.equal(result.counts.corrected, 0);
  assert.equal(result.counts.failed, 1);
  assert.ok(result.lines.some((line) => line.includes('verification')));
});

test('a correction that only becomes readable after a delay is waited for, not failed', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const rows = openingRows({ amount: 99999 });
  const client = openingClient(rows, { applyUpdates: false });
  client.updateTransaction = async (id, fields) => {
    setTimeout(() => Object.assign(rows['act-1'][0], fields), 30);
  };
  const result = await applyOpeningBalances(client, [ENTRY], { timeoutMs: 2000, intervalMs: 10 });
  assert.equal(result.counts.corrected, 1);
  assert.equal(result.counts.failed, 0);
});

test('with no opening entries nothing is read at all', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const client = openingClient({});
  const result = await applyOpeningBalances(client, [], NO_WAIT);
  assert.equal(client.reads, 0);
  assert.equal(result.counts.entries, 0);
  assert.deepEqual(result.lines, []);
});

test('split children carrying the opening id are not mistaken for the row', async () => {
  const { applyOpeningBalances } = await import('./lib.mjs');
  const rows = openingRows();
  rows['act-1'].push({ id: 'child', imported_id: OPENING, amount: 3, date: 'x', is_child: true });
  const result = await applyOpeningBalances(openingClient(rows), [ENTRY], NO_WAIT);
  assert.equal(result.counts.already_right, 1);
  assert.equal(result.counts.ambiguous, 0);
});
