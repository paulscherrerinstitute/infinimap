// What the scrubber's handles mean, as arithmetic rather than as event
// handlers.

import { instantAt } from "./instant";
import { LIVE, type TimeState } from "./state";
import { nearest, nextChange, pairFor, prevChange, type Sweep } from "./changes";
import { rangeOf } from "./window";

/** Which handle a drag is moving. `at` covers live and point, which share one. */
export type Grip = "at" | "since" | "until";

export interface Handle {
  grip: Grip;
  t: number;
}

/** How far a clamped endpoint is pushed off the one it collided with. Only ever
 *  reached by dragging a handle past its partner. */
const MINUTE = 60_000;

/**
 * Where the handles are, in order. Derived from `rangeOf` rather than switching
 * on the mode again, so the two cannot disagree.
 *
 * Live and point share a handle: live is that handle parked at the newest
 * sweep, so dragging it left enters point mode and dropping it back returns.
 */
export function handlesOf(time: TimeState, now: number): Handle[] {
  const { lo, hi } = rangeOf(time, now);
  return time.mode === "compare"
    ? [{ grip: "since", t: lo }, { grip: "until", t: hi }]
    : [{ grip: "at", t: lo }];
}

/** Where a release lands. `head` is "follow now" - live, or an open-ended
 *  `until` - rather than any particular instant. */
export type Landing =
  | { kind: "head" }
  | { kind: "sweep"; sweep: Sweep }
  | { kind: "instant"; at: number };

/**
 * Snap a release to a sweep, to the head, or to nothing.
 *
 * The head competes as one more candidate rather than being a special case.
 * The obvious rule -- "at or past the newest sweep means live" -- holds on one
 * pixel, and an incomplete head sweep (which `targetable` drops) leaves nothing
 * that can satisfy it at all, making live a mode the scrubber can leave but
 * never re-enter. Ties go to the head.
 */
export function landing(targets: Sweep[], release: number, now: number): Landing {
  const snap = nearest(targets, release);
  if (!snap) {
    // Nothing to be nearer to. Pinning the raw instant is the honest degrade:
    // the server resolves it to whatever sweep precedes it.
    return release >= now ? { kind: "head" } : { kind: "instant", at: release };
  }
  return Math.abs(release - now) <= Math.abs(release - snap.at)
    ? { kind: "head" }
    : { kind: "sweep", sweep: snap };
}

/**
 * The state a released handle commits to, or null if the gesture means nothing
 * in this mode. Endpoints are held a minute apart rather than allowed to cross:
 * a range whose `since` is its `until` resolves to one sweep and reports itself
 * unchanged, which reads exactly like a bug.
 */
export function moveHandle(
  time: TimeState,
  grip: Grip,
  release: number,
  targets: Sweep[],
  now: number,
): TimeState | null {
  const land = landing(targets, release, now);
  const head = land.kind === "head";
  const at = land.kind === "sweep" ? land.sweep.at : land.kind === "instant" ? land.at : now;
  // A sweep is named by the server's own stamp, to the microsecond. Anything
  // else is an instant the user pointed at, which never had that precision.
  const stamp = land.kind === "sweep" ? land.sweep.iso : instantAt(at);

  if (grip === "at") return head ? LIVE : { mode: "point", at: stamp };
  if (time.mode !== "compare") return null;

  if (grip === "since") {
    const ceiling = time.until === null ? now : Date.parse(time.until);
    return { ...time, since: !head && at < ceiling ? stamp : instantAt(ceiling - MINUTE) };
  }
  if (head) return { ...time, until: null }; // …to now
  const floor = Date.parse(time.since);
  return { ...time, until: at > floor ? stamp : instantAt(floor + MINUTE) };
}

/**
 * Jump to the next or previous sweep that recorded a change, or null when there
 * is none to jump to.
 * 
 * Searching outward from the edge being moved - forward from `until`, backward
 * from `since` - keeps "previous" from walking back through the range already on
 * screen.
 */
export function jumpTo(
  time: TimeState,
  dir: 1 | -1,
  targets: Sweep[],
  now: number,
): TimeState | null {
  const hs = handlesOf(time, now);
  const anchor = (dir === 1 ? hs[hs.length - 1] : hs[0]).t;
  const target = dir === 1 ? nextChange(targets, anchor) : prevChange(targets, anchor);
  if (!target) return null;

  if (time.mode === "compare") {
    // The changed sweep against the one before it: one click gives the diff of
    // exactly that event, rather than a handle the user still has to pair up.
    const pair = pairFor(targets, target);
    return pair ? { mode: "compare", since: pair.since, until: pair.until } : null;
  }
  return { mode: "point", at: target.iso };
}
