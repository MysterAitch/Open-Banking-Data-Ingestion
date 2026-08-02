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
