/**
 * Reading and judging transfer pairs, shared by the linking push and the
 * audit so the two cannot disagree about what "linked" means.
 *
 * A pair arrives from obdi as two legs, each naming an Actual account and
 * the imported_id of a row that an ordinary import has already put there.
 * Judging a pair is a pure function of the rows found; reading the rows
 * is the same wide-bounds read the audit has always used.
 */

import { isObdiImportedId, readAccountRows } from './audit.mjs';

/**
 * The rows the pairs touch, in two views: `byAccount` maps account then
 * imported_id to the row, and `byId` maps Actual's row id to the row, for
 * finding the partner a leg currently points at.
 *
 * A second row under one imported_id is recorded as ambiguous rather than
 * silently replacing the first: linking the wrong twin would be invisible.
 * `byId` also holds the rows of any account a leg's transfer payee names that
 * no pair touches, and rows with no imported_id: a partner that is somebody's
 * hand-made row must be found in order to be refused.
 */
export async function indexPairRows(client, transfers) {
  const accountIds = new Set();
  for (const pair of transfers) {
    accountIds.add(pair.debit.account);
    accountIds.add(pair.credit.account);
  }
  const byAccount = new Map();
  const byId = new Map();
  for (const accountId of accountIds) {
    const index = new Map();
    for (const row of await readAccountRows(client, accountId)) {
      byId.set(row.id, row);
      if (row.is_child || !row.imported_id) continue;
      index.set(row.imported_id, index.has(row.imported_id) ? 'ambiguous' : row);
    }
    byAccount.set(accountId, index);
  }
  const stray = [...byId.values()].filter((row) => row.transfer_id && !byId.has(row.transfer_id));
  if (stray.length) {
    const accountOfPayee = new Map();
    for (const payee of await client.getPayees()) {
      if (payee.transfer_acct) accountOfPayee.set(payee.id, payee.transfer_acct);
    }
    const further = new Set(
      stray.map((row) => accountOfPayee.get(row.payee)).filter((id) => id && !accountIds.has(id)),
    );
    for (const accountId of further) {
      for (const row of await readAccountRows(client, accountId)) byId.set(row.id, row);
    }
  }
  return { byAccount, byId };
}

function legRow(index, leg) {
  return index.byAccount.get(leg.account)?.get(leg.imported_id) ?? null;
}

/**
 * The verdict on one pair, from the rows as they are now. The order of the
 * checks is the order of the safest refusal: nothing is ever created, so a
 * leg that is not there ends the matter before anything else is weighed.
 *
 * Verdicts: leg_missing, leg_ambiguous, same_account, amounts_not_opposite,
 * reconciled, linked_elsewhere, linked, linkable, relinkable. A half-linked
 * pair (one row already pointing at the other) is linkable: completing it is
 * the point of the second update.
 *
 * THE RE-LINK RULE, stated here and nowhere else. A leg linked to a row other
 * than the one the pair names is `relinkable` only when every row involved is
 * obdi's own: the leg, the partner it is linked to now, the new partner, and
 * the partner the new partner is linked to now, if any. Obdi's own means the
 * row carries an obdi imported id (a leg is named by one, so only the current
 * partners are asked). A current partner that is somebody's hand-made row, or
 * another importer's, is `linked_elsewhere` and is never touched, not even to
 * unlink it. So is a current partner that is split, cannot be found, or is
 * itself linked to a third row, because clearing it would change that row's
 * link too. A reconciled row anywhere in the set is `reconciled`: the library
 * will not change it. A `relinkable` verdict carries `unlink`, the leg and
 * partner pairs to unlink before the pair can be linked.
 */
export function judgePair(pair, index) {
  if (pair.debit.account === pair.credit.account) return { verdict: 'same_account' };
  const a = legRow(index, pair.debit);
  const b = legRow(index, pair.credit);
  if (!a || !b) return { verdict: 'leg_missing' };
  if (a === 'ambiguous' || b === 'ambiguous') return { verdict: 'leg_ambiguous' };
  if (a.amount !== -b.amount) return { verdict: 'amounts_not_opposite', a, b };
  if (a.reconciled || b.reconciled) return { verdict: 'reconciled', a, b };
  const aLink = a.transfer_id ?? null;
  const bLink = b.transfer_id ?? null;
  if (aLink === b.id && bLink === a.id) return { verdict: 'linked', a, b };
  if ((aLink && aLink !== b.id) || (bLink && bLink !== a.id)) {
    return judgeRelink(index, a, b);
  }
  return { verdict: 'linkable', a, b };
}

function judgeRelink(index, a, b) {
  const unlink = [];
  const partners = [];
  for (const [leg, other] of [[a, b], [b, a]]) {
    const link = leg.transfer_id ?? null;
    if (!link || link === other.id) continue;
    const partner = index.byId.get(link);
    if (!partner) return { verdict: 'linked_elsewhere', a, b };
    partners.push(partner);
    unlink.push({ leg, partner });
  }
  if (partners.some((partner) => partner.reconciled)) return { verdict: 'reconciled', a, b };
  for (const partner of partners) {
    if (!isObdiImportedId(partner.imported_id)) return { verdict: 'linked_elsewhere', a, b };
    if (partner.is_parent || partner.is_child) return { verdict: 'linked_elsewhere', a, b };
  }
  for (const { leg, partner } of unlink) {
    if (partner.transfer_id && partner.transfer_id !== leg.id) {
      return { verdict: 'linked_elsewhere', a, b };
    }
  }
  return { verdict: 'relinkable', a, b, unlink };
}

/**
 * How many pairs are linked, unlinked, or missing a leg, for the audit.
 * "Unlinked" includes every verdict short of linked that is not a missing
 * leg, because from the budget's side they are all the same fact: the two
 * rows are not one transfer. `by_account` counts a pair under both of the
 * accounts it touches, so a page can say "linked 3 of 4" beside each.
 */
export async function auditTransfers(client, transfers) {
  const index = await indexPairRows(client, transfers);
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
    const { verdict } = judgePair(pair, index);
    if (verdict === 'linked') counts.linked += 1;
    else if (verdict === 'leg_missing') counts.leg_missing += 1;
    else counts.unlinked += 1;
    tally(pair.debit.account, verdict === 'linked');
    tally(pair.credit.account, verdict === 'linked');
  }
  return counts;
}
