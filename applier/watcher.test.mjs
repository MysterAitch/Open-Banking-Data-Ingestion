import test from 'node:test';
import assert from 'node:assert/strict';


test('a running request keeps beating until it finishes, so a long import never reads as a dead applier', async () => {
  const { startKeepalive } = await import('./watcher.mjs');
  const beats = [];
  const stop = startKeepalive(async () => beats.push(Date.now()), 5);

  await new Promise((resolve) => setTimeout(resolve, 40));
  const whileRunning = beats.length;
  stop();
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.ok(whileRunning >= 2, `expected repeated beats, saw ${whileRunning}`);
  assert.equal(beats.length, whileRunning, 'beats continued after the request finished');
});

test('a failing beat does not stop the keepalive: the next renewal still happens', async () => {
  const { startKeepalive } = await import('./watcher.mjs');
  let attempts = 0;
  const stop = startKeepalive(async () => {
    attempts += 1;
    throw new Error('transient write failure');
  }, 5);

  await new Promise((resolve) => setTimeout(resolve, 30));
  stop();

  assert.ok(attempts >= 2, `expected retries after a failure, saw ${attempts}`);
});
