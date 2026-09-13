/**
 * Simulation clock and selection, as one external store.
 *
 * There is exactly **one clock** and everything reads it: vehicles, signals,
 * the HUD, the timeline. That is a correctness requirement, not tidiness — if
 * signals advanced on their own timer while vehicles advanced on another, the
 * scene would show a car crossing on red that never crossed on red in SUMO, and
 * the picture would contradict the data it claims to show.
 *
 * Implemented with `useSyncExternalStore` rather than context so that a 60 Hz
 * clock tick does not re-render the whole React tree. Components inside the
 * canvas read the time imperatively via `getState()` in their frame loop; only
 * the HUD subscribes, and it is throttled.
 */

export interface SimState {
  /** Current simulation time in seconds, always inside the exported window. */
  time: number
  playing: boolean
  /** Playback rate: 1 = real time, 0.25 = quarter speed. */
  speed: number
  beginS: number
  endS: number
  /** Interned index of the selected vehicle, or null. */
  selectedVehicle: number | null
  /** TLS id of the selected signal, or null. */
  selectedSignal: string | null
  /** `null` = free orbit, otherwise the interned index of the vehicle to follow. */
  followVehicle: number | null
  showBuildings: boolean
  showSignals: boolean
  /** Markers where the recorded detector found the ambulance halted. */
  showDelayMarkers: boolean
  layerFilter: 'all' | 'surface' | 'elevated'
  /** Which exported scene is in view, and whether the paired view is shown. */
  compareMode: boolean
  /**
   * Simulation time at which playback must stop and freeze: the ambulance's
   * recorded SUMO arrival in DEMO mode, otherwise null. Playback never wraps
   * past it or runs beyond it.
   */
  stopAtS: number | null
  /** Presentation walkthrough: drives the camera and narration from the clock. */
  demoMode: boolean
}

type Listener = () => void

const listeners = new Set<Listener>()

let state: SimState = {
  time: 0,
  playing: false,
  speed: 1,
  beginS: 0,
  endS: 1,
  selectedVehicle: null,
  selectedSignal: null,
  followVehicle: null,
  showBuildings: true,
  showSignals: true,
  showDelayMarkers: true,
  layerFilter: 'all',
  compareMode: false,
  stopAtS: null,
  demoMode: false,
}

export function getState(): SimState {
  return state
}

export function setState(patch: Partial<SimState>): void {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/**
 * Advance the clock, wrapping at the end of the recorded window.
 *
 * Wrapping rather than stopping keeps a loop running for inspection. It never
 * runs past `endS`: there is no recorded state out there, and inventing some is
 * the one thing this renderer must not do.
 */
export function advance(deltaSeconds: number): void {
  if (!state.playing) return
  const span = state.endS - state.beginS
  if (span <= 0) return
  let next = state.time + deltaSeconds * state.speed
  // The demo ends when the ambulance arrives. Clamp to that instant and pause:
  // the final scene freezes on the arrival rather than looping back or showing
  // traffic SUMO was never asked to simulate.
  if (state.stopAtS !== null && next >= state.stopAtS) {
    state = { ...state, time: state.stopAtS, playing: false }
    listeners.forEach((l) => l())
    return
  }
  if (next > state.endS) next = state.beginS + ((next - state.beginS) % span)
  if (next < state.beginS) next = state.beginS
  state = { ...state, time: next }
  listeners.forEach((l) => l())
}

export function seek(time: number): void {
  setState({ time: Math.min(state.endS, Math.max(state.beginS, time)) })
}

/** Step by whole recorded samples, so a stepped frame is always a real one. */
export function stepFrames(times: number[], direction: 1 | -1): void {
  if (times.length === 0) return
  let index = nearestFrameIndex(times, state.time) + direction
  index = Math.min(times.length - 1, Math.max(0, index))
  setState({ time: times[index], playing: false })
}

/** Playback rates offered by the presentation controls. Visualisation speed only. */
export const SPEED_OPTIONS = [1, 2, 4, 8] as const
export type PlaybackOption = 'pause' | (typeof SPEED_OPTIONS)[number]

/**
 * PAUSE stops the clock; a rate sets the speed and plays. Neither touches any
 * recorded value, and the arrival freeze in {@link advance} still applies.
 */
export function setPlayback(option: PlaybackOption): void {
  if (option === 'pause') setState({ playing: false })
  else setState({ speed: option, playing: true })
}

/**
 * The time a pane draws.
 *
 * Side-by-side panes share one clock, so they stay synchronised, but each one
 * freezes on its own ambulance's recorded arrival: the faster run holds its
 * final frame while the slower one is still driving.
 */
export function paneTime(freezeAtS?: number | null): number {
  const { time } = state
  return freezeAtS === null || freezeAtS === undefined ? time : Math.min(time, freezeAtS)
}

export interface RunWindow {
  beginS: number
  endS: number
  arrivalS: number | null
}

/** One clock over two runs: both windows, stopping at the later arrival. */
export function sharedWindow(
  a: RunWindow,
  b: RunWindow,
): { beginS: number; endS: number; stopAtS: number | null } {
  return {
    beginS: Math.min(a.beginS, b.beginS),
    endS: Math.max(a.endS, b.endS),
    stopAtS: a.arrivalS === null || b.arrivalS === null ? null : Math.max(a.arrivalS, b.arrivalS),
  }
}

/** Index of the last recorded sample at or before `t`. Binary search. */
export function nearestFrameIndex(times: number[], t: number): number {
  let low = 0
  let high = times.length - 1
  if (t <= times[0]) return 0
  if (t >= times[high]) return high
  while (low <= high) {
    const mid = (low + high) >> 1
    if (times[mid] === t) return mid
    if (times[mid] < t) low = mid + 1
    else high = mid - 1
  }
  return Math.max(0, high)
}
