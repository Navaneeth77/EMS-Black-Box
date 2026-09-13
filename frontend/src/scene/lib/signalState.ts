/**
 * Reading SUMO signal states, and nothing more.
 *
 * Every function here takes a state string that SUMO reported through TraCI and
 * answers a question about it. None of them produces a state: the scene must
 * never show a lamp colour the simulation did not apply.
 */

/**
 * The applied state of one traffic light at time `t`, from SUMO's recording.
 *
 * `timeline` is `[time, state]` pairs stored on change. The state in force at
 * `t` is the last entry at or before it. Before the first entry there is no
 * recorded state, so `null` is returned rather than a guess.
 */
export function recordedStateAt(timeline: [number, string][], t: number): string | null {
  if (timeline.length === 0 || t < timeline[0][0]) return null
  let low = 0
  let high = timeline.length - 1
  while (low < high) {
    const mid = (low + high + 1) >> 1
    if (timeline[mid][0] <= t) low = mid
    else high = mid - 1
  }
  return timeline[low][1]
}

const GREEN = new Set(['G', 'g'])
const RED = new Set(['r', 'R'])

/**
 * Whether the ambulance's own movement is green and every other movement at the
 * same controller is red, in one recorded state.
 *
 * This is what "cross traffic stopped" is allowed to mean on screen. It is
 * evaluated on the applied state string at the current time, so the label can
 * only appear when SUMO really held every conflicting movement at red.
 */
export function crossTrafficStopped(
  state: string | null,
  ambulanceLinks: number[],
): { ambulanceGreen: boolean; crossRed: boolean } {
  if (!state || ambulanceLinks.length === 0) return { ambulanceGreen: false, crossRed: false }
  const own = new Set(ambulanceLinks)
  const ambulanceGreen = ambulanceLinks.every((i) => i < state.length && GREEN.has(state[i]))
  let others = 0
  let crossRed = true
  for (let i = 0; i < state.length; i++) {
    if (own.has(i)) continue
    others++
    if (!RED.has(state[i])) crossRed = false
  }
  return { ambulanceGreen, crossRed: others > 0 && crossRed }
}

export type Aspect = 'red' | 'amber' | 'green' | 'off'

const AMBER = new Set(['y', 'Y', 'u'])

/** The aspect a SUMO state character lights. Anything else leaves the head dark. */
export function aspectFor(character: string | undefined): Aspect {
  if (character !== undefined && GREEN.has(character)) return 'green'
  if (character !== undefined && AMBER.has(character)) return 'amber'
  if (character !== undefined && RED.has(character)) return 'red'
  return 'off'
}

/**
 * The one aspect an approach is showing, or `mixed` when its heads disagree.
 *
 * Used to tint the stop bar so a junction's state reads from across it. A split
 * approach — a green arrow beside a red through-movement — genuinely has no
 * single colour, and `mixed` keeps the scene from inventing one.
 */
export function approachAspect(state: string | null, links: number[]): Aspect | 'mixed' {
  if (!state || links.length === 0) return 'off'
  const shown = new Set(links.map((i) => aspectFor(state[i])))
  return shown.size === 1 ? [...shown][0] : 'mixed'
}
