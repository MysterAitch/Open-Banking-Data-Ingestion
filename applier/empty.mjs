/**
 * Empty the whole budget: every account, closed ones included, and every
 * transaction in them, so that the next push rebuilds Actual from nothing.
 *
 * Actual is a disposable read-only view of obdi, so nothing in it is worth
 * keeping that a push cannot re-create - except rows entered by hand, which
 * this deletes too and which the person was told about before pressing.
 *
 * What the pinned library (26.7.0) was measured to do, in empty.engine.test.mjs
 * (reading it had been wrong here before, so each line is an executed test):
 * - deleteAccount deletes the account, every row in it (split children,
 *   reconciled rows and linked legs included) and the account's transfer
 *   payee, and all of it is readable as gone the moment the call resolves.
 *   3,000 rows took about 140 ms in one call; deleting them row by row is
 *   what took twelve minutes for 4,500 on the real server.
 * - The other leg of a transfer in ANOTHER account survives, unlinked, with
 *   no payee. It is only ever deleted by its own account's deletion, so
 *   deleting every account leaves no row behind; a run that stops partway
 *   leaves such legs behind as ordinary unlinked rows.
 * - deleteAccount on a CLOSED account does nothing and says nothing: the
 *   library's forced delete returns early for one. getAccounts lists closed
 *   accounts (closed: true), so each is reopened first.
 * - A schedule that names a deleted account stays, with no account. Categories,
 *   category groups, rules, and payees other than transfer payees are not
 *   touched.
 * So the account is the unit: delete it directly, after reopening if closed,
 * and prove it gone by reading, rather than deleting transactions first.
 *
 * The request carries what the person was shown (Actual account id -> rows,
 * from the newest audit). It is a ceiling and nothing more: the budget is
 * re-counted here on a fresh download, and a budget holding an account the
 * request did not name, or more rows in any account than it said, is refused
 * before anything changes. Fewer is fine.
 */

import { readAccountRows } from './audit.mjs';
import { makeYielder } from './turn.mjs';

const SETTLE = { timeoutMs: 10000, intervalMs: 50 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// What an empty leaves alone, in the words the result says it in.
export const LEFT_ALONE = [
  'categories and category groups',
  'rules',
  'schedules (one that named a deleted account stays, with no account)',
  "payees other than the accounts' own transfer payees",
];

async function waitUntil(settle, read, done) {
  const deadline = Date.now() + settle.timeoutMs;
  for (;;) {
    const now = await read();
    if (done(now)) return true;
    if (Date.now() >= deadline) return false;
    await sleep(settle.intervalMs);
  }
}

const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const nameOf = (account) => account.name || account.id;

async function plan(client) {
  const held = [];
  for (const account of await client.getAccounts()) {
    const rows = await readAccountRows(client, account.id);
    held.push({
      id: account.id,
      name: nameOf(account),
      closed: account.closed === true,
      rows: rows.filter((row) => !row.is_child).length,
    });
  }
  return held;
}

// Why the budget holds more than the person was shown, or null. Every reason
// is gathered so that the result names them all and not the first.
function whyRefused(held, told) {
  const reasons = [];
  const unknown = held.filter((account) => !hasOwn(told, account.id));
  if (unknown.length) {
    reasons.push(
      `${unknown.length} account${unknown.length === 1 ? '' : 's'} not told about ` +
        `(${unknown.map((a) => a.name).join(', ')})`,
    );
  }
  for (const account of held) {
    if (hasOwn(told, account.id) && account.rows > told[account.id]) {
      reasons.push(`${account.name} holds ${account.rows}, more than the ${told[account.id]} told`);
    }
  }
  return reasons.length ? reasons.join('; ') : null;
}

export async function emptyBudget(client, told, options = {}) {
  const settle = { ...SETTLE, ...(options.settle ?? {}) };
  const onProgress = options.onProgress;
  const maybeYield = makeYielder();
  const held = await plan(client);
  const heldRows = held.reduce((sum, account) => sum + account.rows, 0);
  const summary = {
    accounts_held: held.length,
    rows_held: heldRows,
    accounts_told: Object.keys(told).length,
    rows_told: Object.values(told).reduce((sum, n) => sum + n, 0),
  };
  const refused = whyRefused(held, told);
  if (refused) {
    return {
      complete: false,
      refused: `nothing was changed: the budget holds more than you were shown - ${refused}`,
      ...summary,
      removed: [],
      remaining: held.map(({ id, name }) => ({ account_id: id, name })),
      rows_removed: 0,
      accounts_removed: 0,
      left_alone: LEFT_ALONE,
    };
  }

  const removed = [];
  let stopped = null;
  for (const account of held) {
    try {
      if (account.closed) {
        try {
          await client.reopenAccount(account.id);
        } catch (error) {
          throw new Error(`could not be reopened for deletion (${error?.message ?? error})`);
        }
      }
      try {
        await client.deleteAccount(account.id);
      } catch (error) {
        throw new Error(
          `${account.closed ? 'reopened, but ' : ''}the delete failed (${error?.message ?? error})`,
        );
      }
      const gone = await waitUntil(
        settle,
        async () => ({
          listed: (await client.getAccounts()).some((a) => a.id === account.id),
          rows: (await readAccountRows(client, account.id)).length,
        }),
        (now) => !now.listed && now.rows === 0,
      );
      if (!gone) throw new Error('the account is still there after the delete');
    } catch (error) {
      stopped =
        `${account.name} (${account.rows} rows): ${error?.message ?? error}; ` +
        `${removed.length} of ${held.length} accounts were removed before this, ` +
        'nothing after it was attempted';
      break;
    }
    removed.push({ account_id: account.id, name: account.name, rows: account.rows });
    onProgress?.({ done: removed.length, total: held.length });
    await maybeYield();
  }

  const removedIds = new Set(removed.map((a) => a.account_id));
  const remaining = held
    .filter((account) => !removedIds.has(account.id))
    .map(({ id, name }) => ({ account_id: id, name }));
  let complete = !stopped && remaining.length === 0;
  let verifiedNote = null;
  if (complete) {
    // The last word is the budget's own list, not the loop's tally: a budget
    // that still lists an account is not empty whatever each delete said.
    const empty = await waitUntil(
      settle,
      () => client.getAccounts(),
      (accounts) => accounts.length === 0,
    );
    if (!empty) {
      complete = false;
      verifiedNote = 'the budget still lists accounts after every delete resolved';
    }
  }
  return {
    complete,
    ...(stopped || verifiedNote ? { stopped: stopped ?? verifiedNote } : {}),
    ...summary,
    removed,
    remaining,
    rows_removed: removed.reduce((sum, account) => sum + account.rows, 0),
    accounts_removed: removed.length,
    left_alone: LEFT_ALONE,
  };
}
