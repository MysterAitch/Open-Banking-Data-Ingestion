/**
 * Envelope handling, pure and separately testable.
 *
 * Three shapes arrive. The legacy one is the original flat payload:
 * { actualAccountId: [transactions] }. Version 2 wraps it and adds
 * provisioning: { version: 2, provision: [{canonical_id, label}],
 * accounts: {actualAccountId: [transactions]} }. Version 3 adds the
 * confirmed transfer pairs to link once the rows are in:
 * { version: 3, ..., transfers: [{debit: leg, credit: leg}] } where a leg is
 * { account, imported_id, date, amount }. Version 3 also names each account's
 * opening-balance row, which the import cannot keep exact on its own:
 * { opening_balances: [{ account, imported_id, date, amount }] }.
 * Beside the push, `kind` may name an audit, a prune, an align, or an empty; an
 * empty carries only `empty_accounts`, the rows the person was shown per account.
 * An align carries a push's request and an audit's, and the `confirmed` ceilings
 * and `scope` of its removal step (align.mjs).
 *
 * A declared version this file does not know is refused, never read as the
 * legacy shape: that fallthrough would treat "version", "provision" and
 * "accounts" as three Actual account ids.
 *
 * Provisioning exists so account creation is automated rather than
 * point-and-click: each provision entry becomes an Actual account, and the
 * minted binding flows back to the Python side, which merges it into the
 * account map before building the next envelope - so a provisioned
 * account's transactions ride the following push, not this one.
 */

const isLeg = (leg) =>
  leg &&
  typeof leg === 'object' &&
  typeof leg.account === 'string' &&
  leg.account &&
  typeof leg.imported_id === 'string' &&
  leg.imported_id &&
  Number.isInteger(leg.amount);

function parseTransfers(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((pair) => pair && isLeg(pair.debit) && isLeg(pair.credit))
    .map((pair) => ({ debit: pair.debit, credit: pair.credit }));
}

const isOpening = (entry) =>
  entry &&
  typeof entry === 'object' &&
  typeof entry.account === 'string' &&
  entry.account &&
  typeof entry.imported_id === 'string' &&
  entry.imported_id &&
  typeof entry.date === 'string' &&
  Number.isInteger(entry.amount);

function parseOpenings(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isOpening).map((entry) => ({
    account: entry.account,
    imported_id: entry.imported_id,
    date: entry.date,
    amount: entry.amount,
  }));
}

// The counts a person confirmed before a prune: Actual account id -> whole
// number of rows shown. Absent means none; anything else malformed is refused
// because reading a damaged ceiling as "no ceiling" would delete more than
// the person agreed to.
function parseConfirmedCounts(raw, key) {
  if (raw === undefined) return {};
  const isMap = raw !== null && typeof raw === 'object' && !Array.isArray(raw);
  if (!isMap || !Object.values(raw).every((n) => Number.isInteger(n))) {
    throw new Error(
      `prune request: "${key}" must be an object of account id to whole number, ` +
        `got ${JSON.stringify(raw)}`,
    );
  }
  return { ...raw };
}

// Which accounts an align's removal may touch: Actual account id -> 'all' or
// 'explained'. Required, because a request that said nothing would otherwise be
// read as permission to prune every account.
function parseScope(raw) {
  const isMap = raw !== null && typeof raw === 'object' && !Array.isArray(raw);
  if (!isMap || !Object.values(raw).every((v) => v === 'all' || v === 'explained')) {
    throw new Error(
      'align request: "scope" must be an object of account id to "all" or "explained", ' +
        `got ${JSON.stringify(raw)}`,
    );
  }
  return { ...raw };
}

// What a person was shown before an empty: Actual account id -> rows. Unlike a
// prune's optional ceilings it is REQUIRED and may not be negative: an empty
// request that told nothing would read as "no ceiling" to a careless reader,
// and the applier reads it as "every account is unknown", which refuses.
function parseEmptyAccounts(raw) {
  const isMap = raw !== null && typeof raw === 'object' && !Array.isArray(raw);
  if (!isMap || !Object.values(raw).every((n) => Number.isInteger(n) && n >= 0)) {
    throw new Error(
      'empty request: "empty_accounts" must be an object of account id to ' +
        `non-negative whole number, got ${JSON.stringify(raw)}`,
    );
  }
  return { ...raw };
}

export function parseEnvelope(payload) {
  const declared =
    payload && typeof payload === 'object' && !Array.isArray(payload)
      ? payload.version
      : undefined;
  if (declared !== undefined && declared !== 2 && declared !== 3) {
    throw new Error(
      `unsupported envelope version ${JSON.stringify(declared)} ` +
        '(this applier reads 2 and 3) - applier and store are out of step',
    );
  }
  if (declared === 2 || declared === 3) {
    const provision = Array.isArray(payload.provision) ? payload.provision : [];
    const accounts =
      payload.accounts && typeof payload.accounts === 'object'
        ? payload.accounts
        : {};
    const kind =
      payload.kind === 'audit' ||
      payload.kind === 'prune' ||
      payload.kind === 'empty' ||
      payload.kind === 'marker' ||
      payload.kind === 'align'
        ? payload.kind
        : 'push';
    return {
      // 'audit' asks for a read-back-and-compare instead of an import;
      // 'marker' asks only for the sync marker to be written and carries
      // nothing else the applier reads; anything else is a push.
      kind,
      // Only a prune has counts to confirm; on any other kind they are not
      // even read, so a stray key cannot widen what a push or audit does.
      ...(kind === 'prune'
        ? {
            clear_empty: parseConfirmedCounts(payload.clear_empty, 'clear_empty'),
            confirmed: parseConfirmedCounts(payload.confirmed, 'confirmed'),
          }
        : {}),
      ...(kind === 'empty' ? { empty_accounts: parseEmptyAccounts(payload.empty_accounts) } : {}),
      // Only an audit explains orphans, by the imported ids of rows obdi holds
      // as history. Anything that is not a string is dropped: a damaged list
      // can then only explain less, which makes the removal's guard stricter.
      ...(kind === 'audit' || kind === 'align'
        ? {
            history: Array.isArray(payload.history)
              ? payload.history.filter((id) => typeof id === 'string' && id)
              : [],
          }
        : {}),
      // An align's removal step is bounded by the same ceilings a prune is, and
      // by a scope: which accounts it may touch and whether it may take rows obdi
      // cannot explain. A malformed scope is refused, never read as "everywhere".
      ...(kind === 'align'
        ? {
            confirmed: parseConfirmedCounts(payload.confirmed, 'confirmed'),
            scope: parseScope(payload.scope),
          }
        : {}),
      provision: provision.filter(
        (entry) => entry && typeof entry.canonical_id === 'string' && entry.canonical_id,
      ),
      accounts,
      transfers: declared === 3 ? parseTransfers(payload.transfers) : [],
      openings: declared === 3 ? parseOpenings(payload.opening_balances) : [],
    };
  }
  // Legacy: the whole payload IS the accounts map.
  return {
    kind: 'push',
    provision: [],
    accounts: payload && typeof payload === 'object' ? payload : {},
    transfers: [],
    openings: [],
  };
}

export function mergeBindings(existing, minted) {
  // Dedupe by canonical id; the newest mint wins. Order is stable so the
  // pending-bindings file diffs cleanly between runs.
  const byCanonical = new Map();
  for (const entry of [...(existing ?? []), ...(minted ?? [])]) {
    if (entry && typeof entry.canonical_id === 'string' && entry.canonical_id) {
      byCanonical.set(entry.canonical_id, entry);
    }
  }
  return [...byCanonical.values()].sort((a, b) =>
    a.canonical_id.localeCompare(b.canonical_id),
  );
}

export function byQueuedStamp(a, b) {
  // Queue order is TEMPORAL, not alphabetical: every request filename
  // embeds its queued-at stamp after the kind prefix, and sorting whole
  // names made audit- precede push- within a tick regardless of when
  // each was pressed - the third member of the filename-versus-time bug
  // family in one day. Compare the stamps; the prefixes stay out of it.
  const stampOf = (name) => name.slice(name.indexOf('-') + 1);
  return stampOf(a) < stampOf(b) ? -1 : stampOf(a) > stampOf(b) ? 1 : 0;
}
