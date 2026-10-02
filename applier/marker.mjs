/**
 * The sync marker: one off-budget account in Actual, holding no rows, whose
 * NAME carries the time obdi last wrote to the budget.
 *
 * Why it exists: Actual shows no "data as of" anywhere, and a phone that has
 * downloaded a stale copy looks exactly like one that is current. The account
 * list is on every screen of every device, so a name that changes with each
 * write is the one thing a device can show that says how far it has caught up.
 * Measured on the pinned library (26.7.0): an account with no rows reads a
 * balance of 0 and moves neither the on-budget nor the off-budget total, and
 * `createAccount({ offbudget: true })` and `updateAccount({ name })` both work.
 * Creating the account also creates a transfer payee carrying its name, which
 * `updateAccount` renames with it; that payee is the one trace outside the
 * account list.
 *
 * The applier is stateless (every job downloads the budget fresh), so the
 * marker is found again by its name: an account ending with MARKER_SUFFIX.
 * The stamp comes FIRST because a phone's sidebar truncates a long name after
 * roughly 17 characters, and the suffix is what is expendable.
 *
 * Who may write it is decided by the caller, not here: a push and a request of
 * the marker kind write it; an audit only reads it (an audit must change
 * nothing) and a prune neither reads nor writes it. Every place that walks
 * "all accounts in Actual" must skip it: see isMarkerName's uses.
 */

export const MARKER_SUFFIX = ' obdi marker';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const two = (n) => String(n).padStart(2, '0');

/** `02 Oct 20:41Z obdi marker`: UTC, as every other time on obdi's pages is. */
export function markerName(instant) {
  const at = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(at.getTime())) {
    throw new Error(`${String(instant)} is not a valid instant for a marker name`);
  }
  return (
    `${two(at.getUTCDate())} ${MONTHS[at.getUTCMonth()]} ` +
    `${two(at.getUTCHours())}:${two(at.getUTCMinutes())}Z${MARKER_SUFFIX}`
  );
}

// An ending, never a containment: an account somebody called "obdi marker
// notes" is theirs, and treating it as the marker would rename it.
export function isMarkerName(name) {
  return typeof name === 'string' && name.endsWith(MARKER_SUFFIX);
}

export function markerAccounts(accounts) {
  return accounts.filter((account) => isMarkerName(account.name));
}

/**
 * What an audit may do with the marker: look. Never creates or renames.
 *
 * `accounts` names each marker account with its row count, because the audit's
 * account list leaves them out (they are not strays) while an empty is checked
 * against that list and refuses a budget holding an account it was not told
 * about (see empty.mjs). The rows are read with the same bounds as
 * readAccountRows in audit.mjs, which cannot be imported here without a cycle.
 */
export async function readMarker(client) {
  const found = markerAccounts(await client.getAccounts());
  const accounts = [];
  for (const account of found) {
    const rows = await client.getTransactions(account.id, '1900-01-01', '2999-12-31');
    accounts.push({
      account_id: account.id,
      name: account.name,
      rows: rows.filter((row) => !row.is_child).length,
    });
  }
  return {
    found: found.length,
    name: found[0]?.name ?? null,
    names: found.map((account) => account.name),
    accounts,
  };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// updateAccount and createAccount resolve before every reader sees the change
// (the same lag as updateTransaction, measured elsewhere in this applier), so
// the result is read back until it is as asked, and a marker that never shows
// is an error rather than a claim.
async function waitForName(client, id, name, settle) {
  const deadline = Date.now() + settle.timeoutMs;
  for (;;) {
    const account = (await client.getAccounts()).find((candidate) => candidate.id === id);
    if (account?.name === name) return;
    if (Date.now() >= deadline) {
      throw new Error(`the sync marker did not read back as "${name}"`);
    }
    await sleep(settle.intervalMs);
  }
}

/**
 * Create the marker, or rename the one there, to the name for `instant`.
 *
 * With more than one marker (two devices, or a sync conflict) the first is
 * renamed and the rest are left exactly as they are: deleting an account is
 * not this job's call, and the result says how many were found.
 */
export async function writeMarker(
  client,
  instant,
  settle = { timeoutMs: 5000, intervalMs: 50 },
) {
  const name = markerName(instant);
  const existing = markerAccounts(await client.getAccounts());
  const result = { name, at: new Date(instant).toISOString(), found: existing.length };
  if (existing.length === 0) {
    result.id = await client.createAccount({ name, offbudget: true }, 0);
    result.action = 'created';
  } else {
    result.id = existing[0].id;
    result.action = 'renamed';
    await client.updateAccount(existing[0].id, { name });
    if (existing.length > 1) {
      const others = existing.length - 1;
      result.note =
        `${existing.length} marker accounts were found; the first was renamed and ` +
        `${others} other${others === 1 ? '' : 's'} ${others === 1 ? 'was' : 'were'} left alone`;
    }
  }
  await waitForName(client, result.id, name, settle);
  return result;
}
