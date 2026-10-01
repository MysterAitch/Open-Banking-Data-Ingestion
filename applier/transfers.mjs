/**
 * Reading and judging transfer pairs, shared by the linking push and the
 * audit so the two cannot disagree about what "linked" means.
 *
 * A pair arrives from obdi as two legs, each naming an Actual account and
 * the imported_id of a row that an ordinary import has already put there.
 * Judging a pair is a pure function of the two rows found; reading the rows
 * is the same wide-bounds read the audit has always used.
 */

import { readAccountRows } from './audit.mjs';

/**
 * Every row of every account the pairs touch, by account then imported_id.
 * A second row under one imported_id is recorded as ambiguous rather than
 * silently replacing the first: linking the wrong twin would be invisible.
 */
export async function indexPairRows(client, transfers) {
  const accountIds = new Set();
  for (const pair of transfers) {
    accountIds.add(pair.debit.account);
    accountIds.add(pair.credit.account);
  }
  const byAccount = new Map();
  for (const accountId of accountIds) {
    const index = new Map();
    for (const row of await readAccountRows(client, accountId)) {
      if (row.is_child || !row.imported_id) continue;
      index.set(row.imported_id, index.has(row.imported_id) ? 'ambiguous' : row);
    }
    byAccount.set(accountId, index);
  }
  return byAccount;
}

function legRow(rowsByAccount, leg) {
  return rowsByAccount.get(leg.account)?.get(leg.imported_id) ?? null;
}

/**
 * The verdict on one pair, from the rows as they are now. The order of the
 * checks is the order of the safest refusal: nothing is ever created, so a
 * leg that is not there ends the matter before anything else is weighed.
 *
 * Verdicts: leg_missing, leg_ambiguous, same_account, amounts_not_opposite,
 * reconciled, linked_elsewhere, linked, linkable. A half-linked pair (one
 * row already pointing at the other) is linkable: completing it is the
 * point of the second update.
 */
export function judgePair(pair, rowsByAccount) {
  if (pair.debit.account === pair.credit.account) return { verdict: 'same_account' };
  const a = legRow(rowsByAccount, pair.debit);
  const b = legRow(rowsByAccount, pair.credit);
  if (!a || !b) return { verdict: 'leg_missing' };
  if (a === 'ambiguous' || b === 'ambiguous') return { verdict: 'leg_ambiguous' };
  if (a.amount !== -b.amount) return { verdict: 'amounts_not_opposite', a, b };
  if (a.reconciled || b.reconciled) return { verdict: 'reconciled', a, b };
  const aLink = a.transfer_id ?? null;
  const bLink = b.transfer_id ?? null;
  if (aLink === b.id && bLink === a.id) return { verdict: 'linked', a, b };
  if ((aLink && aLink !== b.id) || (bLink && bLink !== a.id)) {
    return { verdict: 'linked_elsewhere', a, b };
  }
  return { verdict: 'linkable', a, b };
}

/**
 * How many pairs are linked, unlinked, or missing a leg, for the audit.
 * "Unlinked" includes every verdict short of linked that is not a missing
 * leg, because from the budget's side they are all the same fact: the two
 * rows are not one transfer. `by_account` counts a pair under both of the
 * accounts it touches, so a page can say "linked 3 of 4" beside each.
 */
export async function auditTransfers(client, transfers) {
  const rowsByAccount = await indexPairRows(client, transfers);
  const counts = {
    pairs: transfers.length,
    linked: 0,
    unlinked: 0,
    leg_missing: 0,
    by_account: {},
  };
  const tally = (accountId, linked) => {
    const entry = (counts.by_account[accountId] ??= { pairs: 0, linked: 0 });
    entry.pairs += 1;
    if (linked) entry.linked += 1;
  };
  for (const pair of transfers) {
    const { verdict } = judgePair(pair, rowsByAccount);
    if (verdict === 'linked') counts.linked += 1;
    else if (verdict === 'leg_missing') counts.leg_missing += 1;
    else counts.unlinked += 1;
    tally(pair.debit.account, verdict === 'linked');
    tally(pair.credit.account, verdict === 'linked');
  }
  return counts;
}
