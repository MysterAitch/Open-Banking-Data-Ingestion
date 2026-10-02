import assert from 'node:assert/strict';
import { test } from 'node:test';

import { partitionAccount, summariseAudit } from './audit.mjs';

const expected = [
  { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
  { imported_id: 'ck-2:0', date: '2026-07-02', amount: -250 },
  { imported_id: 'ck-3:0', date: '2026-07-03', amount: 5000 },
];

test('matching rows are present, absent rows are missing', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
  ]);
  assert.equal(result.present, 1);
  assert.deepEqual(result.missing, ['ck-2:0', 'ck-3:0']);
  assert.equal(result.orphaned.length, 0);
  assert.equal(result.diverged.length, 0);
});

test('an unexpected imported_id is orphaned - provably ours, listed', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-other:0', date: '2026-06-01', amount: -900 },
  ]);
  assert.deepEqual(result.orphaned, [
    { imported_id: 'ck-other:0', date: '2026-06-01', amount: -900 },
  ]);
});

test('rows without imported_id are the person\'s own - counted, never listed', () => {
  const result = partitionAccount(expected, [
    { imported_id: null, date: '2026-07-04', amount: -1234, payee: 'private' },
    { date: '2026-07-05', amount: 999 },
  ]);
  assert.equal(result.human, 2);
  assert.equal(result.orphaned.length, 0);
});

test('an edited amount or date diverges; categories and payees never do', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -101 },
    { imported_id: 'ck-2:0', date: '2026-07-09', amount: -250 },
    { imported_id: 'ck-3:0', date: '2026-07-03', amount: 5000, category: 'x', notes: 'y' },
  ]);
  assert.equal(result.diverged.length, 2);
  assert.equal(result.present, 3);
  assert.deepEqual(result.missing, []);
});

test('split children are skipped - the parent carries the id and the total', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100, is_parent: true },
    { imported_id: null, date: '2026-07-01', amount: -60, is_child: true },
    { imported_id: null, date: '2026-07-01', amount: -40, is_child: true },
  ]);
  assert.equal(result.present, 1);
  assert.equal(result.human, 0);
});

test('summary carries counts in full and caps the samples', () => {
  const missing = Array.from({ length: 30 }, (_, i) => `ck-${i}:0`);
  const summary = summariseAudit({
    expected: 30,
    present: 0,
    missing,
    orphaned: [],
    human: 2,
    diverged: [],
  });
  assert.equal(summary.missing, 30);
  assert.equal(summary.missing_sample.length, 10);
  assert.equal(summary.human, 2);
});

test('accounts existing in Actual but bound to nothing are named as strays', async () => {
  const { auditAccounts } = await import('./audit.mjs');
  const client = {
    getAccounts: async () => [
      { id: 'act-1', name: 'halifax-current-account' },
      { id: 'act-stray', name: 'Mr Roger Howell (halifax)' },
    ],
    getAccountBalance: async () => 0,
    getTransactions: async (id) =>
      id === 'act-stray'
        ? [
            { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
            { imported_id: null, date: '2026-07-01', amount: -60, is_child: true },
          ]
        : [],
  };

  const report = await auditAccounts(client, { 'act-1': [] });

  const stray = report.find((entry) => entry.unbound_in_actual);
  assert.equal(stray.name, 'Mr Roger Howell (halifax)');
  assert.equal(stray.rows, 1);
  assert.ok(report.find((entry) => entry.account_id === 'act-1' && !entry.unbound_in_actual));
});

const hex = (seed) => seed.repeat(64).slice(0, 64);

// The selection with nothing linked to look up: a context whose client is
// never asked anything.
async function chosen(expected, rows) {
  const { choosePrunable, createLinkContext } = await import('./audit.mjs');
  return choosePrunable(createLinkContext({}), 'act-1', expected, rows);
}

test('prunable rows are ours-and-unexpected only; yours are untouchable', async () => {
  const expected = new Set([`${hex('1')}:0`, `${hex('2')}:0`]);
  const rows = [
    { id: 'a', imported_id: `${hex('1')}:0` },
    { id: 'b', imported_id: `${hex('e')}:0` },
    { id: 'c', imported_id: null },
    { id: 'd', imported_id: `${hex('e')}:1`, is_child: true },
  ];
  const { prunable, left } = await chosen(expected, rows);
  assert.deepEqual(prunable, [{ id: 'b', imported_id: `${hex('e')}:0` }]);
  assert.deepEqual(left, {});
});

test('an imported id that is not obdi-shaped is untouchable, like no id at all', async () => {
  const { isObdiImportedId } = await import('./audit.mjs');
  const expected = new Set([`${hex('1')}:0`]);
  const rows = [
    { id: 'a', imported_id: 'FITID-20260803-0001' },
    { id: 'b', imported_id: 'ck-stale:0' },
    { id: 'c', imported_id: `${hex('a')}:0`.toUpperCase() },
    { id: 'd', imported_id: `${hex('e')}:2` },
  ];
  const { prunable, left } = await chosen(expected, rows);
  assert.deepEqual(prunable, [{ id: 'd', imported_id: `${hex('e')}:2` }]);
  assert.deepEqual(left, { foreign: 3 }, 'counted as orphaned by the audit, never removed');
  assert.equal(isObdiImportedId(`${hex('e')}:2`), true);
  assert.equal(isObdiImportedId('FITID-20260803-0001'), false);
});

test('prune reports the foreign-id rows it deliberately left alone', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const deleted = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => [
      { id: 'a', imported_id: `${hex('1')}:0` },
      { id: 'b', imported_id: `${hex('e')}:0` },
      { id: 'c', imported_id: 'FITID-20260803-0001' },
      { id: 'd', imported_id: null },
    ],
    deleteTransaction: async (id) => deleted.push(id),
  };

  const report = await pruneAccounts(client, {
    'act-1': [{ imported_id: `${hex('1')}:0` }],
  });

  assert.deepEqual(deleted, ['b']);
  assert.equal(report[0].removed, 1);
  assert.equal(report[0].foreign_ids, 1);
});

test('an orphan linked to a leg that cannot be found is left in place and reported, never deleted', async () => {
  // Deleting one leg of a linked pair makes Actual delete the other leg too,
  // so a link that cannot be inspected is not one that can be undone.
  const { pruneAccounts } = await import('./audit.mjs');
  const deleted = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => [
      { id: 'kept', imported_id: `${hex('1')}:0` },
      { id: 'plain-orphan', imported_id: `${hex('e')}:0`, transfer_id: null },
      { id: 'linked-orphan', imported_id: `${hex('f')}:0`, transfer_id: 'partner-row' },
    ],
    deleteTransaction: async (id) => deleted.push(id),
  };

  const report = await pruneAccounts(client, {
    'act-1': [{ imported_id: `${hex('1')}:0` }],
  });

  assert.deepEqual(deleted, ['plain-orphan']);
  assert.equal(report[0].removed, 1);
  assert.equal(report[0].linked_left, 1);
});

test('prune says nothing about linked orphans when there are none', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => [
      { id: 'kept', imported_id: `${hex('1')}:0`, transfer_id: 'its-partner' },
      { id: 'plain-orphan', imported_id: `${hex('e')}:0` },
    ],
    deleteTransaction: async () => {},
  };

  const report = await pruneAccounts(client, {
    'act-1': [{ imported_id: `${hex('1')}:0` }],
  });

  assert.equal(report[0].removed, 1);
  assert.equal('linked_left' in report[0], false);
});

test('an empty expected set is refused, never pruned blind', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => {
      throw new Error('must not even read when refusing');
    },
    deleteTransaction: async () => {
      throw new Error('must not delete');
    },
  };
  const report = await pruneAccounts(client, { 'act-1': [] });
  assert.ok(report[0].skipped);
});


// A client holding a fixed set of rows that records every delete.
function pruneClient(rows) {
  const deleted = [];
  let reads = 0;
  return {
    deleted,
    reads: () => reads,
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => {
      reads += 1;
      return rows;
    },
    deleteTransaction: async (id) => deleted.push(id),
  };
}

const THREE_OURS = () => [
  { id: 'a', imported_id: `${hex('a')}:0` },
  { id: 'b', imported_id: `${hex('b')}:0` },
  { id: 'c', imported_id: `${hex('c')}:0` },
  { id: 'linked', imported_id: `${hex('d')}:0`, transfer_id: 'partner' },
  { id: 'child', imported_id: `${hex('e')}:0`, is_child: true },
  { id: 'foreign', imported_id: 'FITID-1' },
  { id: 'hand', imported_id: null },
];

test('an empty expected set named in clear_empty removes only obdi-shaped unlinked rows', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = pruneClient(THREE_OURS());

  const report = await pruneAccounts(client, { 'act-1': [] }, { clear_empty: { 'act-1': 3 } });

  assert.deepEqual(client.deleted, ['a', 'b', 'c']);
  assert.equal(report[0].removed, 3);
  assert.equal(report[0].linked_left, 1);
  assert.equal(report[0].foreign_ids, 1);
});

test('an empty expected set not named in clear_empty is skipped with the original wording', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = pruneClient(THREE_OURS());

  const report = await pruneAccounts(client, { 'act-1': [] }, { clear_empty: {}, confirmed: { 'act-1': 9 } });

  assert.equal(report[0].skipped, 'expected set empty - refusing to prune blind');
  assert.deepEqual(client.deleted, []);
  assert.equal(client.reads(), 0);
});

test('a confirmed count below what the account holds deletes nothing and says both numbers', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = pruneClient(THREE_OURS());

  const report = await pruneAccounts(client, { 'act-1': [] }, { clear_empty: { 'act-1': 2 } });

  assert.deepEqual(client.deleted, []);
  assert.equal(report[0].refused, 'holds 3, more than the 2 confirmed - run the audit again');
  assert.equal(report[0].holds, 3);
  assert.equal(report[0].confirmed, 2);
  assert.equal('removed' in report[0], false);
});

test('a confirmed count that is not a positive whole number deletes nothing and is refused', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  for (const bad of [0, -3, 2.5, '3', null, NaN]) {
    const client = pruneClient(THREE_OURS());

    const report = await pruneAccounts(client, { 'act-1': [] }, { clear_empty: { 'act-1': bad } });

    assert.deepEqual(client.deleted, [], String(bad));
    assert.match(report[0].refused, /not a positive whole number/, String(bad));
  }
});

test('the check and the deletes use one read of the account', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = pruneClient(THREE_OURS());

  await pruneAccounts(client, { 'act-1': [] }, { clear_empty: { 'act-1': 3 } });

  assert.equal(client.reads(), 1);
});

test('a clearing request leaves every account it does not name alone', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = {
    ...pruneClient(THREE_OURS()),
    getAccounts: async () => [
      { id: 'act-1', name: 'one' },
      { id: 'act-2', name: 'two' },
    ],
  };

  const report = await pruneAccounts(
    client,
    { 'act-1': [], 'act-2': [{ imported_id: `${hex('9')}:0` }] },
    { clear_empty: { 'act-1': 3 } },
  );

  assert.deepEqual(report.map((e) => e.account_id), ['act-1']);
});

test('an ordinary account refuses when the orphans exceed the count shown, and prunes otherwise', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const keep = [{ imported_id: `${hex('1')}:0` }];

  const over = pruneClient(THREE_OURS());
  const refused = await pruneAccounts(over, { 'act-1': keep }, { confirmed: { 'act-1': 2 } });
  assert.deepEqual(over.deleted, []);
  assert.equal(refused[0].holds, 3);
  assert.equal(refused[0].confirmed, 2);

  const within = pruneClient(THREE_OURS());
  const pruned = await pruneAccounts(within, { 'act-1': keep }, { confirmed: { 'act-1': 3 } });
  assert.deepEqual(within.deleted, ['a', 'b', 'c']);
  assert.equal(pruned[0].removed, 3);

  const unshown = pruneClient(THREE_OURS());
  const asBefore = await pruneAccounts(unshown, { 'act-1': keep }, {});
  assert.deepEqual(unshown.deleted, ['a', 'b', 'c']);
  assert.equal('refused' in asBefore[0], false);
});

test('a malformed confirmed count on an ordinary account refuses rather than pruning', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  for (const bad of ['3', 2.5, null]) {
    const client = pruneClient(THREE_OURS());

    const report = await pruneAccounts(
      client,
      { 'act-1': [{ imported_id: `${hex('1')}:0` }] },
      { confirmed: { 'act-1': bad } },
    );

    assert.deepEqual(client.deleted, [], String(bad));
    assert.ok(report[0].refused, String(bad));
  }
});

test('a duplicate row sharing one imported id is counted, not collapsed', async () => {
  const { partitionAccount, summariseAudit } = await import('./audit.mjs');
  const expected = [{ imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 }];
  const rows = [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 },
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 },
  ];
  const summary = summariseAudit(partitionAccount(expected, rows));
  assert.equal(summary.duplicated, 1);
  assert.deepEqual(summary.duplicated_sample, [{ imported_id: 'ck-1:0', copies: 2 }]);
  assert.equal(summary.present, 1);
});

test('an opening-balance id is ours: the reserved prefix and any non-empty reference', async () => {
  const { isObdiImportedId, isOpeningImportedId } = await import('./audit.mjs');
  for (const id of [
    'obdi-opening:halifax-current',
    'obdi-opening:truelayer:3fc9a1 with spaces',
    'obdi-opening:a',
    'obdi-opening:two\nlines',
  ]) {
    assert.equal(isOpeningImportedId(id), true, id);
    assert.equal(isObdiImportedId(id), true, id);
  }
});

test('the reserved prefix alone, another case, or another id is not an opening id', async () => {
  const { isObdiImportedId, isOpeningImportedId } = await import('./audit.mjs');
  for (const id of [
    'obdi-opening:',
    'OBDI-OPENING:halifax-current',
    ' obdi-opening:halifax-current',
    'xobdi-opening:halifax-current',
    'obdi-opening',
    'FITID-20260803-0001',
    '',
    null,
    undefined,
    42,
  ]) {
    assert.equal(isOpeningImportedId(id), false, String(id));
    assert.equal(isObdiImportedId(id), false, String(id));
  }
});

test('a payment id is still ours and is not mistaken for an opening id', async () => {
  const { isObdiImportedId, isOpeningImportedId } = await import('./audit.mjs');
  assert.equal(isObdiImportedId(`${hex('c')}:3`), true);
  assert.equal(isOpeningImportedId(`${hex('c')}:3`), false);
});

test('an opening row the expected set no longer names is prunable as ours', async () => {
  // The account stopped having an opening balance, so its row is an orphan,
  // and an orphan is deleted only if it is provably ours.
  const expected = new Set([`${hex('1')}:0`]);
  const rows = [
    { id: 'pay', imported_id: `${hex('1')}:0` },
    { id: 'opening', imported_id: 'obdi-opening:halifax-current' },
  ];
  assert.deepEqual((await chosen(expected, rows)).prunable, [
    { id: 'opening', imported_id: 'obdi-opening:halifax-current' },
  ]);
});

test('an opening row the expected set still names is never pruned', async () => {
  const expected = new Set([`${hex('1')}:0`, 'obdi-opening:halifax-current']);
  const rows = [
    { id: 'pay', imported_id: `${hex('1')}:0` },
    { id: 'opening', imported_id: 'obdi-opening:halifax-current' },
  ];
  assert.deepEqual((await chosen(expected, rows)).prunable, []);
});

test('an opening row in the wrong account is an orphan there, and prunable', async () => {
  // A mis-binding leaves one account holding another's opening row; the id
  // names the account it belongs to, so it is not expected in this one.
  const expected = new Set([`${hex('1')}:0`, 'obdi-opening:this-one']);
  const rows = [{ id: 'stray', imported_id: 'obdi-opening:somebody-else' }];
  assert.deepEqual((await chosen(expected, rows)).prunable, [
    { id: 'stray', imported_id: 'obdi-opening:somebody-else' },
  ]);
});

test('an opening row that is linked to a leg that cannot be found is left, like any such orphan', async () => {
  const expected = new Set([`${hex('1')}:0`]);
  const rows = [{ id: 'o', imported_id: 'obdi-opening:x', transfer_id: 'partner' }];
  const { prunable, left } = await chosen(expected, rows);
  assert.deepEqual(prunable, []);
  assert.deepEqual(left, { partner_missing: 1 });
});

// ---- Linked orphans: the selection and the order of the calls, on a stand-in
// engine. The real engine's behaviour is measured in
// linked-orphans.engine.test.mjs; these pin the decisions and the sequence.

const TO_NAT = 'payee-to-nat';
const TO_MAIN = 'payee-to-main';

// Two accounts, MAIN and NAT, holding the rows given. `calls` records every
// change in order; an update that clears the link also clears the other side's
// pointer only if the test says so (the real engine does not), and a delete
// takes a row with it only if it is the one named.
function twoAccounts({ main, nat }, behaviour = {}) {
  const store = { 'act-main': main.map((r) => ({ ...r })), 'act-nat': nat.map((r) => ({ ...r })) };
  const calls = [];
  const all = () => [...store['act-main'], ...store['act-nat']];
  return {
    store,
    calls,
    getAccounts: async () => [
      { id: 'act-main', name: 'MAIN' },
      { id: 'act-nat', name: 'NAT' },
    ],
    getPayees: async () => [
      { id: TO_NAT, transfer_acct: 'act-nat' },
      { id: TO_MAIN, transfer_acct: 'act-main' },
    ],
    getTransactions: async (accountId, from, to) =>
      store[accountId].filter((r) => r.date >= from && r.date <= to).map((r) => ({ ...r })),
    updateTransaction: async (id, fields) => {
      calls.push(['update', id, fields]);
      if (behaviour.failUpdate === id) throw new Error('update refused');
      Object.assign(all().find((r) => r.id === id), fields);
    },
    deleteTransaction: async (id) => {
      calls.push(['delete', id]);
      if (behaviour.failDelete) throw new Error('delete refused');
      for (const rows of Object.values(store)) {
        const at = rows.findIndex((r) => r.id === id);
        if (at >= 0) rows.splice(at, 1);
      }
    },
  };
}

const legPair = (orphanId, partnerId, partnerExtra = {}, orphanExtra = {}) => ({
  main: [
    {
      id: orphanId,
      imported_id: `${hex('a')}:0`,
      date: '2026-09-07',
      amount: -500,
      payee: TO_NAT,
      transfer_id: partnerId,
      ...orphanExtra,
    },
  ],
  nat: [
    {
      id: partnerId,
      imported_id: `${hex('b')}:0`,
      date: '2026-09-09',
      amount: 500,
      payee: TO_MAIN,
      transfer_id: orphanId,
      ...partnerExtra,
    },
  ],
});

const NAT_EXPECTS = [{ imported_id: `${hex('b')}:0` }];
const MAIN_EXPECTS = [{ imported_id: `${hex('1')}:0` }];

test('a linked orphan whose partner is ours is unlinked on both sides, then only the orphan is deleted', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = twoAccounts(legPair('o', 'p'));

  const report = await pruneAccounts(
    client,
    { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
    { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 } },
  );

  assert.deepEqual(client.calls, [
    ['update', 'p', { transfer_id: null, payee: null }],
    ['update', 'o', { transfer_id: null, payee: null }],
    ['delete', 'o'],
  ]);
  const main = report.find((e) => e.account_id === 'act-main');
  assert.equal(main.removed, 1);
  assert.equal(main.unlinked, 1);
  assert.equal(client.store['act-nat'].length, 1, 'the partner is not deleted');
});

test('a linked orphan is left, with its reason, when the partner is not an obdi import', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  for (const imported_id of ['FITID-9', null]) {
    const client = twoAccounts(legPair('o', 'p', { imported_id }));

    const report = await pruneAccounts(
      client,
      { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
      { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 } },
    );

    assert.deepEqual(client.calls, [], String(imported_id));
    const main = report.find((e) => e.account_id === 'act-main');
    assert.deepEqual(main.left, { partner_not_ours: 1 });
    assert.equal(main.removed, 0);
  }
});

test('a linked orphan is left when either leg is reconciled or split, or the partner is linked elsewhere', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const cases = [
    ['reconciled', {}, { reconciled: true }],
    ['reconciled', { reconciled: true }, {}],
    ['split', { is_parent: true }, {}],
    ['split', {}, { is_parent: true }],
    ['partner_linked_elsewhere', { transfer_id: 'somebody-else' }, {}],
  ];
  for (const [reason, partnerExtra, orphanExtra] of cases) {
    const client = twoAccounts(legPair('o', 'p', partnerExtra, orphanExtra));

    const report = await pruneAccounts(
      client,
      { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
      { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 } },
    );

    assert.deepEqual(client.calls, [], reason);
    assert.deepEqual(report.find((e) => e.account_id === 'act-main').left, { [reason]: 1 });
  }
});

test('a partner that no longer points back is not touched: only the orphan is cleared before the delete', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = twoAccounts(legPair('o', 'p', { transfer_id: null }));

  await pruneAccounts(
    client,
    { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
    { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 } },
  );

  assert.deepEqual(client.calls, [
    ['update', 'o', { transfer_id: null, payee: null }],
    ['delete', 'o'],
  ]);
});

test('the count shown covers linked orphans that will be removed: a lower one changes nothing at all', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = twoAccounts(legPair('o', 'p'));

  const report = await pruneAccounts(
    client,
    { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
    { confirmed: { 'act-main': 0 }, settle: { holdMs: 0 } },
  );

  assert.deepEqual(client.calls, []);
  assert.match(report.find((e) => e.account_id === 'act-main').refused, /holds 1, more than the 0/);
});

test('an unlink that fails stops the account before anything is deleted', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = twoAccounts(legPair('o', 'p'), { failUpdate: 'p' });

  const report = await pruneAccounts(
    client,
    { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
    { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 } },
  );

  const main = report.find((e) => e.account_id === 'act-main');
  assert.match(main.stopped, /unlinking failed \(update refused\)/);
  assert.equal(main.removed, 0);
  assert.equal(client.calls.some(([kind]) => kind === 'delete'), false);
});

test('the progress heartbeat counts linked orphans like any other', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = twoAccounts(legPair('o', 'p'));
  const beats = [];

  await pruneAccounts(
    client,
    { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS },
    { confirmed: { 'act-main': 1 }, settle: { holdMs: 0 }, onProgress: (beat) => beats.push(beat) },
  );

  assert.deepEqual(beats, [{ account_id: 'act-main', name: 'MAIN', done: 1, total: 1 }]);
});

test('the audit says how many orphans a removal will take and how many it leaves, by reason', async () => {
  const { auditAccounts } = await import('./audit.mjs');
  const pair = legPair('o', 'p', { imported_id: 'FITID-9' });
  const client = {
    ...twoAccounts({
      main: [
        ...pair.main,
        { id: 'plain', imported_id: `${hex('c')}:0`, date: '2026-09-01', amount: -1 },
        { id: 'fx', imported_id: 'FITID-1', date: '2026-09-01', amount: -2 },
      ],
      nat: pair.nat,
    }),
    getAccountBalance: async () => 0,
  };

  const report = await auditAccounts(client, { 'act-main': MAIN_EXPECTS, 'act-nat': NAT_EXPECTS });

  const main = report.find((e) => e.account_id === 'act-main');
  assert.equal(main.orphaned, 3);
  assert.equal(main.orphaned_will_go, 1);
  assert.deepEqual(main.orphaned_will_stay, { partner_not_ours: 1, foreign: 1 });
  assert.equal(
    main.orphaned_will_go + Object.values(main.orphaned_will_stay).reduce((a, b) => a + b, 0),
    main.orphaned,
    'the two add up to the ceiling',
  );
});

test('prune counts an opening row it removes and still refuses an empty expected set', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const deleted = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => [
      { id: 'a', imported_id: `${hex('1')}:0` },
      { id: 'o', imported_id: 'obdi-opening:halifax-current' },
    ],
    deleteTransaction: async (id) => deleted.push(id),
  };

  const report = await pruneAccounts(client, { 'act-1': [{ imported_id: `${hex('1')}:0` }] });
  assert.deepEqual(deleted, ['o']);
  assert.equal(report[0].removed, 1);

  // With nothing at all expected, even a lone opening row is not removed:
  // an empty set is what a wiped store looks like.
  deleted.length = 0;
  const blind = await pruneAccounts(client, { 'act-1': [] });
  assert.deepEqual(deleted, []);
  assert.ok(blind[0].skipped);
});

test('the audit counts an opening row expected and present, and its balance includes it', async () => {
  const { auditAccounts } = await import('./audit.mjs');
  const expected = [
    { imported_id: 'obdi-opening:halifax-current', date: '2026-08-31', amount: 100000 },
    { imported_id: `${hex('1')}:0`, date: '2026-09-01', amount: -2500 },
  ];
  const client = (openingAmount) => ({
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getAccountBalance: async () => openingAmount - 2500,
    getTransactions: async () => [
      { imported_id: 'obdi-opening:halifax-current', date: '2026-08-31', amount: openingAmount },
      { imported_id: `${hex('1')}:0`, date: '2026-09-01', amount: -2500 },
    ],
  });

  const [right] = await auditAccounts(client(100000), { 'act-1': expected });
  const [altered] = await auditAccounts(client(90000), { 'act-1': expected });

  assert.deepEqual(right.balance, { expected: 97500, actual: 97500, agrees: true });
  assert.equal(right.diverged, 0);
  assert.deepEqual(altered.balance, { expected: 97500, actual: 87500, agrees: false });
  assert.equal(altered.diverged, 1);
});
