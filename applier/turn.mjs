/**
 * Hand the event loop a turn from inside a long per-row loop.
 *
 * A loop of awaited client calls that resolve without real I/O is one
 * unbroken chain of microtasks, and timers only run between macrotasks. A
 * removal of 4,519 rows ran for twelve minutes that way: the keepalive's
 * interval never got a turn, the heartbeat went silent, and the page told
 * the person to look at the container while the applier was working
 * correctly. Every loop that awaits a client call per row calls this.
 */

//: Rows between turns. Small enough that a beat is never late by more than a
//: handful of calls, large enough that the yield costs nothing measurable.
export const YIELD_EVERY = 25;

export function makeYielder(every = YIELD_EVERY) {
  let since = 0;
  return async function maybeYield() {
    since += 1;
    if (since < every) return;
    since = 0;
    // setImmediate is a macrotask: unlike an awaited resolved promise, it
    // lets due timers and pending I/O run before the loop continues.
    await new Promise((resolve) => setImmediate(resolve));
  };
}
