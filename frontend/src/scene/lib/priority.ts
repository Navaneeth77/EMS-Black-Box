/**
 * The EMS policy's recorded state, as the scene and HUD are allowed to read it.
 *
 * Two recorded sources, never anything else:
 *
 * - `policy_transitions`: when the policy requested, held and released priority
 *   at each signal;
 * - `signal_timeline`: the state SUMO actually applied at each signal, on change.
 *
 * **Display rule — priority episodes.** The policy re-evaluates every step whether
 * a signal is ahead of the ambulance. While the ambulance crosses a junction's
 * internal lane it has no route index, so the policy records CLEARING -> NORMAL
 * and, a step or two later, REQUESTED -> PRIORITY_ACTIVE again, while the applied
 * signal state does not change at all. Drawn literally, the banner would flicker
 * on and off several times on one approach. A re-entry at the same signal within
 * {@link EPISODE_GAP_S} of the release is therefore drawn as one episode. Within
 * an episode, "PRIORITY ACTIVE" is shown only once the policy has recorded
 * PRIORITY_ACTIVE there **and** the applied state has the ambulance's movement
 * green at that instant; otherwise "REQUESTED". The transitions themselves are
 * unchanged and listed in full in Research details.
 */

import type { Manifest } from './types'
import { crossTrafficStopped, recordedStateAt } from './signalState'

export interface PolicyTransition {
  sim_time_s: number
  tls_id: string
  previous_state: string
  new_state: string
  reason: string
}

/** Longest release-to-re-request gap still drawn as one priority episode. */
export const EPISODE_GAP_S = 3.0

/**
 * Policy states that mean the signal is being *acted on*, and may therefore be
 * drawn as a priority episode.
 *
 * `DETECTED` is deliberately absent. It means "the ambulance is on the route and
 * this signal is ahead of it" — the policy watching, before its activation rule
 * has fired. Drawing that as priority would put "EMS PRIORITY ACTIVE" on screen
 * from the moment the ambulance departs, which is the opposite of the claim the
 * scene exists to make. Mirrors INTERVENING_STATES in
 * `simulation/ems_sim/policies/state.py`.
 *
 * Recordings made before those states existed contain only REQUESTED,
 * PRIORITY_ACTIVE, CLEARING and NORMAL, all of which this handles unchanged.
 */
export const INTERVENING_STATES: ReadonlySet<string> = new Set([
  'REQUESTED',
  'TRANSITIONING',
  'PRIORITY_ACTIVE',
  'CLEARING',
  'RESTORING',
])

/** Whether a recorded policy state means the policy is acting on the signal. */
export function isIntervening(state: string): boolean {
  return INTERVENING_STATES.has(state)
}

/** Raw recorded policy state of each signal at `t` (NORMAL entries omitted). */
export function priorityStatesAt(
  transitions: PolicyTransition[],
  t: number,
): Map<string, { state: string; since: number; reason: string }> {
  const out = new Map<string, { state: string; since: number; reason: string }>()
  for (const transition of transitions) {
    if (transition.sim_time_s > t) break
    out.set(transition.tls_id, {
      state: transition.new_state,
      since: transition.sim_time_s,
      reason: transition.reason,
    })
  }
  for (const [key, value] of [...out]) {
    if (!isIntervening(value.state)) out.delete(key)
  }
  return out
}

export interface PriorityEpisode {
  tlsId: string
  start: number
  /** Time of the final release to NORMAL, or null if the recording ends inside it. */
  end: number | null
  /** Times at which the policy recorded PRIORITY_ACTIVE within the episode. */
  activeTimes: number[]
}

export function priorityEpisodes(
  transitions: PolicyTransition[],
  gapS: number = EPISODE_GAP_S,
): PriorityEpisode[] {
  const open = new Map<string, { episode: PriorityEpisode; releasedAt: number | null }>()
  const out: PriorityEpisode[] = []
  for (const transition of transitions) {
    let current = open.get(transition.tls_id)
    const intervening = isIntervening(transition.new_state)
    if (
      current &&
      current.releasedAt !== null &&
      intervening &&
      transition.sim_time_s - current.releasedAt > gapS
    ) {
      current.episode.end = current.releasedAt
      open.delete(transition.tls_id)
      current = undefined
    }
    if (!intervening) {
      // NORMAL, or DETECTED: the policy is watching, not acting. An episode
      // already open stays open for the merge window, so the re-request that
      // follows a junction interior does not split it in two.
      if (current) current.releasedAt = transition.sim_time_s
      continue
    }
    if (!current) {
      const episode: PriorityEpisode = {
        tlsId: transition.tls_id,
        start: transition.sim_time_s,
        end: null,
        activeTimes: [],
      }
      out.push(episode)
      current = { episode, releasedAt: null }
      open.set(transition.tls_id, current)
    } else {
      current.releasedAt = null
    }
    if (transition.new_state === 'PRIORITY_ACTIVE') current.episode.activeTimes.push(transition.sim_time_s)
  }
  for (const { episode, releasedAt } of open.values()) episode.end = releasedAt
  return out
}

export interface DisplayState {
  tlsId: string
  state: 'REQUESTED' | 'PRIORITY_ACTIVE'
  since: number
  ambulanceGreen: boolean
  crossRed: boolean
  appliedState: string | null
}

/** What may be drawn for each signal at `t`, per the episode rule above. */
export function displayStatesAt(
  manifest: Manifest,
  episodes: PriorityEpisode[],
  t: number,
): Map<string, DisplayState> {
  const out = new Map<string, DisplayState>()
  for (const episode of episodes) {
    if (t < episode.start || (episode.end !== null && t >= episode.end)) continue
    const links =
      manifest.route_traffic_lights.find((r) => r.tls_id === episode.tlsId)?.ambulance_link_indices ??
      []
    const timeline = manifest.signal_timeline?.[episode.tlsId] ?? []
    const appliedState = recordedStateAt(timeline, t)
    const { ambulanceGreen, crossRed } = crossTrafficStopped(appliedState, links)
    const granted = episode.activeTimes.some((x) => x <= t)
    // Without a recorded timeline (older scenes) the policy record is all there is.
    const green = timeline.length === 0 ? true : ambulanceGreen
    out.set(episode.tlsId, {
      tlsId: episode.tlsId,
      state: granted && green ? 'PRIORITY_ACTIVE' : 'REQUESTED',
      since: episode.start,
      ambulanceGreen,
      crossRed,
      appliedState,
    })
  }
  return out
}

export interface PriorityStatus extends DisplayState {
  isIntersection: boolean
  arm: string | null
  road: string | null
}

/**
 * The single most relevant priority state at `t`: an active hold before a
 * request, the four-way intersection before other signals, earliest first.
 */
export function priorityStatus(
  manifest: Manifest,
  t: number,
  episodes: PriorityEpisode[] = priorityEpisodes(
    (manifest.policy_transitions ?? []) as PolicyTransition[],
  ),
): PriorityStatus | null {
  const states = [...displayStatesAt(manifest, episodes, t).values()]
  if (states.length === 0) return null
  const four = manifest.intersection?.tls_id
  states.sort(
    (a, b) =>
      Number(b.state === 'PRIORITY_ACTIVE') - Number(a.state === 'PRIORITY_ACTIVE') ||
      Number(b.tlsId === four) - Number(a.tlsId === four) ||
      a.since - b.since,
  )
  const best = states[0]
  const entry = manifest.route_traffic_lights.find((r) => r.tls_id === best.tlsId)
  const links = entry?.ambulance_link_indices ?? []
  const approach =
    manifest.intersection && best.tlsId === four
      ? manifest.intersection.approaches.find((a) => links.some((i) => a.link_indices.includes(i)))
      : undefined
  return {
    ...best,
    isIntersection: best.tlsId === four,
    arm: approach?.arm ?? null,
    road: approach?.road_name || entry?.approach_road || null,
  }
}
