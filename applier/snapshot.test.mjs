/**
 * Refreshing the server's snapshot: the call order and what is reported when
 * either call fails, with a stand-in for the library's handle. The upload
 * itself is exercised against a real server in snapshot.server.test.mjs.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { refreshSnapshot } from './lib.mjs';

const NOW = new Date('2026-10-02T21:30:00Z');
const clock = () => NOW;

function handleReturning(answers) {
  const calls = [];
  return {
    calls,
    send: async (name) => {
      calls.push(name);
      const answer = answers[name];
      if (answer instanceof Error) throw answer;
      return answer;
    },
  };
}

test('refreshSnapshot syncs to completion first and only then uploads, reporting the time', async () => {
  const handle = handleReturning({ sync: { messages: [] }, 'upload-budget': {} });

  const result = await refreshSnapshot(handle, clock);

  assert.deepEqual(handle.calls, ['sync', 'upload-budget']);
  assert.deepEqual(result, { refreshed: true, at: '2026-10-02T21:30:00.000Z' });
});

test('refreshSnapshot uploads nothing when the final sync did not complete, and says why', async () => {
  const handle = handleReturning({ sync: { error: { reason: 'network-failure' } } });

  const result = await refreshSnapshot(handle, clock);

  assert.deepEqual(handle.calls, ['sync'], 'no upload after a failed sync');
  assert.equal(result.refreshed, false);
  assert.match(result.error, /final sync did not complete \(network-failure\); nothing was uploaded/);
  assert.equal(result.at, '2026-10-02T21:30:00.000Z');
});

test('refreshSnapshot reports a refused upload as a result, not as a thrown error', async () => {
  const handle = handleReturning({
    sync: { messages: [] },
    'upload-budget': { error: { type: 'FileUploadError', reason: 'unauthorized' } },
  });

  const result = await refreshSnapshot(handle, clock);

  assert.deepEqual(handle.calls, ['sync', 'upload-budget']);
  assert.equal(result.refreshed, false);
  assert.match(result.error, /upload was refused \(unauthorized\)/);
});

test('refreshSnapshot reports a handler that throws as a result, not as a thrown error', async () => {
  const handle = handleReturning({ sync: new Error('socket hang up') });

  const result = await refreshSnapshot(handle, clock);

  assert.equal(result.refreshed, false);
  assert.match(result.error, /refresh threw \(socket hang up\)/);
});

test('refreshSnapshot reads an error given as plain text as well as one given as an object', async () => {
  const handle = handleReturning({ sync: { messages: [] }, 'upload-budget': { error: 'internal' } });

  const result = await refreshSnapshot(handle, clock);

  assert.match(result.error, /upload was refused \(internal\)/);
});
