import assert from 'node:assert/strict';
import { test } from 'node:test';

import { mergeBindings, parseEnvelope } from './envelope.mjs';

test('a version-1 flat payload is entirely accounts, nothing to provision', () => {
  const parsed = parseEnvelope({ 'act-1': [{ imported_id: 'k:0' }] });
  assert.deepEqual(parsed.provision, []);
  assert.deepEqual(Object.keys(parsed.accounts), ['act-1']);
});

test('a version-2 envelope separates provisioning from transactions', () => {
  const parsed = parseEnvelope({
    version: 2,
    provision: [
      { canonical_id: 'halifax-reward', label: 'Reward (halifax)' },
      { not_valid: true },
    ],
    accounts: { 'act-1': [] },
  });
  assert.deepEqual(parsed.provision, [
    { canonical_id: 'halifax-reward', label: 'Reward (halifax)' },
  ]);
  assert.deepEqual(Object.keys(parsed.accounts), ['act-1']);
});

const LEG_A = { account: 'act-1', imported_id: 'k-a:0', date: '2026-09-02', amount: -150000 };
const LEG_B = { account: 'act-2', imported_id: 'k-b:0', date: '2026-09-02', amount: 150000 };

test('a version-3 envelope carries its transfer pairs alongside the accounts', () => {
  const parsed = parseEnvelope({
    version: 3,
    provision: [],
    accounts: { 'act-1': [], 'act-2': [] },
    transfers: [{ debit: LEG_A, credit: LEG_B }],
  });
  assert.deepEqual(parsed.transfers, [{ debit: LEG_A, credit: LEG_B }]);
  assert.deepEqual(Object.keys(parsed.accounts), ['act-1', 'act-2']);
  assert.equal(parsed.kind, 'push');
});

test('a version-3 envelope with no transfers array parses with none', () => {
  assert.deepEqual(parseEnvelope({ version: 3, accounts: {} }).transfers, []);
  assert.deepEqual(parseEnvelope({ version: 3, accounts: {}, transfers: 'nope' }).transfers, []);
});

test('malformed transfer entries are dropped and well-formed ones kept', () => {
  const parsed = parseEnvelope({
    version: 3,
    accounts: {},
    transfers: [
      { debit: LEG_A, credit: LEG_B },
      null,
      'text',
      { debit: LEG_A },
      { debit: LEG_A, credit: { ...LEG_B, imported_id: '' } },
      { debit: LEG_A, credit: { ...LEG_B, account: 7 } },
      { debit: LEG_A, credit: { ...LEG_B, amount: 1.5 } },
    ],
  });
  assert.equal(parsed.transfers.length, 1);
});

test('version 2 and the legacy flat shape still parse, with no transfers', () => {
  assert.deepEqual(parseEnvelope({ version: 2, accounts: { 'act-1': [] } }).transfers, []);
  const legacy = parseEnvelope({ 'act-1': [{ imported_id: 'k:0' }] });
  assert.deepEqual(legacy.transfers, []);
  assert.deepEqual(Object.keys(legacy.accounts), ['act-1']);
});

test('an unknown declared version is refused, never read as a map of accounts', () => {
  for (const version of [1, 4, 99, '3', null]) {
    assert.throws(
      () => parseEnvelope({ version, provision: [], accounts: {} }),
      /unsupported envelope version/,
      `version ${JSON.stringify(version)} must be refused`,
    );
  }
});

test('minted bindings merge with pending ones, newest winning per canonical', () => {
  const merged = mergeBindings(
    [{ canonical_id: 'a', actual_account_id: 'old' }],
    [
      { canonical_id: 'a', actual_account_id: 'new' },
      { canonical_id: 'b', actual_account_id: 'b-1' },
    ],
  );
  assert.deepEqual(merged, [
    { canonical_id: 'a', actual_account_id: 'new' },
    { canonical_id: 'b', actual_account_id: 'b-1' },
  ]);
});

test('an audit envelope is recognised; unknown kinds stay pushes', () => {
  assert.equal(parseEnvelope({ version: 2, kind: 'audit', accounts: {} }).kind, 'audit');
  assert.equal(parseEnvelope({ version: 2, accounts: {} }).kind, 'push');
  assert.equal(parseEnvelope({ version: 2, kind: 'surprise', accounts: {} }).kind, 'push');
  assert.equal(parseEnvelope({ 'act-1': [] }).kind, 'push');
});

test('the queue drains in the order things were pressed, not the alphabet', async () => {
  const { byQueuedStamp } = await import('./envelope.mjs');
  const names = [
    'audit-20260802T185240377472Z.json',
    'push-20260802T184956216920Z.json',
    'audit-20260802T185427538211Z.json',
  ];
  names.sort(byQueuedStamp);
  assert.deepEqual(names, [
    'push-20260802T184956216920Z.json',
    'audit-20260802T185240377472Z.json',
    'audit-20260802T185427538211Z.json',
  ]);
});
