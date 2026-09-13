/**
 * HISTORICAL_DEMO presentation logic: playback speeds, the arrival freeze in a
 * side-by-side comparison, what "cross traffic stopped" may mean, the priority
 * episode display rule, the green corridor's extent, and scene selection.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  SPEED_OPTIONS,
  advance,
  getState,
  paneTime,
  setPlayback,
  setState,
  sharedWindow,
} from '../store'
import { approachAspect, aspectFor, crossTrafficStopped } from '../signalState'
import { bodyCentreOffset } from '../coords'
import {
  EPISODE_GAP_S,
  displayStatesAt,
  priorityEpisodes,
  priorityStatesAt,
  priorityStatus,
  type PolicyTransition,
} from '../priority'
import { buildRoutePolyline, corridorSpan } from '../routeCorridor'
import { sceneKeyFrom, scenePaths } from '../sceneBase'
// TrafficSignals re-exports signalState's aspectFor rather than defining its
// own, so importing it from both places bound one function to one name twice.
import { approachFurniture } from '../../components/TrafficSignals'
import type { Lane, Manifest, TrafficLight } from '../types'

describe('playback controls', () => {
  beforeEach(() => {
    setState({ beginS: 540, endS: 1132.5, time: 600, speed: 1, playing: false, stopAtS: 778 })
  })

  it('offers exactly PAUSE, 1x, 2x, 4x and 8x', () => {
    expect([...SPEED_OPTIONS]).toEqual([1, 2, 4, 8])
  })

  it('PAUSE stops the clock and a speed plays at that rate', () => {
    setPlayback(4)
    expect(getState()).toMatchObject({ speed: 4, playing: true })
    advance(1)
    expect(getState().time).toBeCloseTo(604)
    setPlayback('pause')
    advance(1)
    expect(getState().time).toBeCloseTo(604)
    expect(getState().playing).toBe(false)
  })

  it('still freezes at the recorded arrival at 8x', () => {
    setState({ time: 770 })
    setPlayback(8)
    advance(5)
    expect(getState().time).toBe(778)
    expect(getState().playing).toBe(false)
  })
})

describe('side-by-side comparison clock', () => {
  it('spans both runs and stops at the later arrival', () => {
    expect(
      sharedWindow(
        { beginS: 540, endS: 790, arrivalS: 778 },
        { beginS: 540, endS: 1132.5, arrivalS: 1120.5 },
      ),
    ).toEqual({ beginS: 540, endS: 1132.5, stopAtS: 1120.5 })
  })

  it('does not stop if either run never arrived', () => {
    expect(
      sharedWindow({ beginS: 0, endS: 10, arrivalS: 5 }, { beginS: 0, endS: 20, arrivalS: null })
        .stopAtS,
    ).toBeNull()
  })

  it('freezes each pane at its own arrival on the shared clock', () => {
    setState({ time: 900 })
    expect(paneTime(778)).toBe(778)
    expect(paneTime(1120.5)).toBe(900)
    expect(paneTime(null)).toBe(900)
  })
})

describe('cross traffic stopped', () => {
  it('requires the ambulance movement green and every other movement red', () => {
    expect(crossTrafficStopped('rrrrrrGGG', [6, 7, 8])).toEqual({ ambulanceGreen: true, crossRed: true })
    expect(crossTrafficStopped('rrrrGGGGG', [6, 7, 8])).toEqual({ ambulanceGreen: true, crossRed: false })
    expect(crossTrafficStopped('rrrrrryyy', [6, 7, 8])).toEqual({ ambulanceGreen: false, crossRed: true })
    expect(crossTrafficStopped(null, [6, 7, 8])).toEqual({ ambulanceGreen: false, crossRed: false })
  })
})

const transition = (t: number, tls: string, from: string, to: string): PolicyTransition => ({
  sim_time_s: t,
  tls_id: tls,
  previous_state: from,
  new_state: to,
  reason: '',
})

// The recorded pattern at the Silk Board four-way in the HISTORICAL_DEMO EMS run:
// the policy re-enters every time the ambulance crosses a junction interior.
const FOUR = 'silk_board_4way'
const recorded: PolicyTransition[] = [
  transition(614.0, FOUR, 'NORMAL', 'REQUESTED'),
  transition(614.5, FOUR, 'REQUESTED', 'PRIORITY_ACTIVE'),
  transition(617.5, FOUR, 'PRIORITY_ACTIVE', 'CLEARING'),
  transition(618.0, FOUR, 'CLEARING', 'NORMAL'),
  transition(618.5, FOUR, 'NORMAL', 'REQUESTED'),
  transition(619.0, FOUR, 'REQUESTED', 'PRIORITY_ACTIVE'),
  transition(670.5, FOUR, 'PRIORITY_ACTIVE', 'CLEARING'),
  transition(671.5, FOUR, 'CLEARING', 'NORMAL'),
]

function manifestWith(timeline: [number, string][]): Manifest {
  return {
    policy_transitions: recorded,
    signal_timeline: { [FOUR]: timeline },
    route_traffic_lights: [
      { tls_id: FOUR, approach_edge: 'e', green_fraction_for_ambulance: 0.239, has_red_exposure: true, ambulance_link_indices: [6, 7, 8] },
    ],
    intersection: {
      tls_id: FOUR,
      name: 'Central Silk Board four-way (at-grade)',
      centre: [0, 0, 0],
      approaches: [
        { from_edge: 'e', link_indices: [6, 7, 8], node_id: 'n', arm: 'E', arm_bearing_deg: 76, travel_heading_deg: 259, road_name: 'Outer Ring Road' },
      ],
      phases: [],
      cycle_length_s: 450,
      timing_provenance: {},
      replaces_separate_tls_ids: [],
      on_ambulance_route: true,
    },
  } as unknown as Manifest
}

describe('priority episodes (display rule)', () => {
  it('merges re-entries within the gap into one episode', () => {
    const episodes = priorityEpisodes(recorded)
    expect(episodes).toHaveLength(1)
    expect(episodes[0]).toMatchObject({ tlsId: FOUR, start: 614.0, end: 671.5 })
  })

  it('splits when the release lasts longer than the gap', () => {
    const split = [
      transition(600, 'x', 'NORMAL', 'REQUESTED'),
      transition(601, 'x', 'REQUESTED', 'NORMAL'),
      transition(601 + EPISODE_GAP_S + 0.5, 'x', 'NORMAL', 'REQUESTED'),
    ]
    expect(priorityEpisodes(split).map((e) => e.start)).toEqual([600, 601 + EPISODE_GAP_S + 0.5])
  })

  it('shows ACTIVE only while the applied state has the ambulance movement green', () => {
    const manifest = manifestWith([
      [0.5, 'rrrrrrGGG'],
      [640, 'rrrrrryyy'],
    ])
    const episodes = priorityEpisodes(recorded)
    expect(displayStatesAt(manifest, episodes, 614.2).get(FOUR)?.state).toBe('REQUESTED')
    expect(displayStatesAt(manifest, episodes, 617.8).get(FOUR)?.state).toBe('PRIORITY_ACTIVE')
    expect(displayStatesAt(manifest, episodes, 650).get(FOUR)?.state).toBe('REQUESTED')
    expect(displayStatesAt(manifest, episodes, 672).has(FOUR)).toBe(false)
  })

  it('reports the arm, and cross traffic only from the recorded state', () => {
    const status = priorityStatus(manifestWith([[0.5, 'rrrrrrGGG']]), 630)
    expect(status).toMatchObject({
      state: 'PRIORITY_ACTIVE',
      isIntersection: true,
      arm: 'E',
      road: 'Outer Ring Road',
      ambulanceGreen: true,
      crossRed: true,
    })
    const leaky = priorityStatus(manifestWith([[0.5, 'GrrrrrGGG']]), 630)
    expect(leaky?.crossRed).toBe(false)
  })
})

describe('green corridor extent', () => {
  const lane = (edge: string, points: [number, number, number][]): Lane => ({
    id: `${edge}_0`, edge, index: 0, width: 3.2, speed_kmh: 50, type: 'x', layer: 0,
    structure: 'surface', internal: false, points,
  })
  const route = buildRoutePolyline(
    ['a', 'b', 'c'],
    [lane('a', [[0, 0, 0], [50, 0, 0]]), lane('b', [[50, 0, 0], [100, 0, 0]]), lane('c', [[100, 0, 0], [150, 0, 0]])],
  )

  it('runs from the ambulance to the held signal stop line along the route', () => {
    expect(route.edgeEnd.get('b')).toBe(2)
    expect(corridorSpan(route, [2, 0], 'b')).toEqual([0, 2])
  })

  it('is not drawn once the ambulance is past the stop line', () => {
    expect(corridorSpan(route, [120, 0], 'b')).toBeNull()
  })
})

describe('signal heads', () => {
  it('lights exactly the aspect of the SUMO state character', () => {
    expect(['G', 'g', 'y', 'Y', 'r', 'R', 'o', 'O', undefined].map(aspectFor)).toEqual([
      'green', 'green', 'amber', 'amber', 'red', 'red', 'off', 'off', 'off',
    ])
  })

  it('puts one head per link and the pole on the kerb side of lane 0', () => {
    const tls = {
      id: 't',
      link_count: 2,
      programs: [],
      data_class: 'SIMULATED_DATA',
      note: '',
      links: [
        { index: 0, from_lane: 'e_0', from_edge: 'e', position: [10, 0, 0], heading: 0, lane_width: 3.2 },
        { index: 1, from_lane: 'e_1', from_edge: 'e', position: [6.8, 0, 0], heading: 0, lane_width: 3.2 },
      ],
    } as unknown as TrafficLight
    const [approach] = approachFurniture(tls)
    expect(approach.heads.map((h) => h.link.index).sort()).toEqual([0, 1])
    // Travelling north (heading 0), the right-hand kerb is +x of lane 0.
    expect(approach.pole[0]).toBeGreaterThan(10)
  })
})

describe('scene selection', () => {
  it('defaults to HISTORICAL_DEMO and keeps the earlier demo reachable', () => {
    expect(scenePaths(sceneKeyFrom(''))).toEqual({
      primary: '/scene/historical',
      paired: '/scene/historical/compare',
    })
    expect(scenePaths(sceneKeyFrom('?scene=demo')).primary).toBe('/scene')
  })
})

describe('approachAspect', () => {
  const eArm = [6, 7, 8]

  it('reports the one aspect an approach is showing', () => {
    expect(approachAspect('rrrrrrGGG', eArm)).toBe('green')
    expect(approachAspect('rrrrrryyy', eArm)).toBe('amber')
    expect(approachAspect('rrrrrrrrr', eArm)).toBe('red')
  })

  it('refuses to pick a colour for an approach whose heads disagree', () => {
    expect(approachAspect('rrrrrrGrr', eArm)).toBe('mixed')
    expect(approachAspect('rrrrrryGG', eArm)).toBe('mixed')
  })

  it('is dark without a recorded state', () => {
    expect(approachAspect(null, eArm)).toBe('off')
    expect(approachAspect('rrrrrrGGG', [])).toBe('off')
    expect(aspectFor(undefined)).toBe('off')
    expect(aspectFor('o')).toBe('off')
  })
})

describe('bodyCentreOffset (SUMO reports the front bumper)', () => {
  it('puts the body half a length behind the reported point, along the heading', () => {
    // Heading 0 = north = scene -z, so the body sits to +z (behind).
    const north = bodyCentreOffset(0, 6)
    expect(north.x).toBeCloseTo(0, 6)
    expect(north.z).toBeCloseTo(3, 6)

    // Heading 90 = east = scene +x, so the body sits to -x.
    const east = bodyCentreOffset(90, 4.5)
    expect(east.x).toBeCloseTo(-2.25, 6)
    expect(east.z).toBeCloseTo(0, 6)
  })

  it('never moves a vehicle sideways', () => {
    for (const heading of [0, 37, 90, 168, 259.1, 348.2]) {
      const { x, z } = bodyCentreOffset(heading, 5)
      const along = Math.hypot(x, z)
      expect(along).toBeCloseTo(2.5, 6)
      // The offset is exactly opposite the heading: no lateral component.
      const forwardX = Math.sin((heading * Math.PI) / 180)
      const forwardZ = -Math.cos((heading * Math.PI) / 180)
      expect(x * forwardZ - z * forwardX).toBeCloseTo(0, 6)
    }
  })

  it('restores the gap the car-following model left', () => {
    // A queue on a heading-0 lane: SUMO puts the leader's front 6.5 m ahead of the
    // follower's front (leader length 4.5 + follower minGap 2.0).
    const leaderFront = { x: 0, z: 0 }
    const followerFront = { x: 0, z: 6.5 }
    const leaderBack = bodyCentreOffset(0, 4.5)
    const followerBack = bodyCentreOffset(0, 6)
    const leaderTail = leaderFront.z + leaderBack.z + 4.5 / 2
    const followerNose = followerFront.z + followerBack.z - 6 / 2
    expect(followerNose - leaderTail).toBeCloseTo(2.0, 6) // exactly the minGap
  })
})

describe('policy states the display may act on', () => {
  it('does not draw priority for a signal the policy is merely watching', () => {
    // DETECTED means the ambulance is on the route and this signal is ahead of
    // it. Drawing that as an episode would put "priority active" on screen from
    // the moment the ambulance departs.
    const transitions = [
      { sim_time_s: 600, tls_id: 't1', previous_state: 'NORMAL', new_state: 'DETECTED', reason: '' },
      { sim_time_s: 640, tls_id: 't1', previous_state: 'DETECTED', new_state: 'REQUESTED', reason: '' },
      { sim_time_s: 646, tls_id: 't1', previous_state: 'REQUESTED', new_state: 'TRANSITIONING', reason: '' },
      { sim_time_s: 652, tls_id: 't1', previous_state: 'TRANSITIONING', new_state: 'PRIORITY_ACTIVE', reason: '' },
      { sim_time_s: 690, tls_id: 't1', previous_state: 'PRIORITY_ACTIVE', new_state: 'CLEARING', reason: '' },
      { sim_time_s: 690, tls_id: 't1', previous_state: 'CLEARING', new_state: 'RESTORING', reason: '' },
      { sim_time_s: 700, tls_id: 't1', previous_state: 'RESTORING', new_state: 'NORMAL', reason: '' },
    ]
    const episodes = priorityEpisodes(transitions)
    expect(episodes).toHaveLength(1)
    expect(episodes[0].start).toBe(640)
    expect(episodes[0].end).toBe(700)
    expect(episodes[0].activeTimes).toEqual([652])

    expect(priorityStatesAt(transitions, 620).size).toBe(0)
    expect(priorityStatesAt(transitions, 645).get('t1')?.state).toBe('REQUESTED')
    expect(priorityStatesAt(transitions, 660).get('t1')?.state).toBe('PRIORITY_ACTIVE')
    expect(priorityStatesAt(transitions, 999).size).toBe(0)
  })

  it('still reads recordings made before those states existed', () => {
    const legacy = [
      { sim_time_s: 700, tls_id: 't1', previous_state: 'NORMAL', new_state: 'REQUESTED', reason: '' },
      { sim_time_s: 726, tls_id: 't1', previous_state: 'REQUESTED', new_state: 'PRIORITY_ACTIVE', reason: '' },
      { sim_time_s: 784, tls_id: 't1', previous_state: 'PRIORITY_ACTIVE', new_state: 'CLEARING', reason: '' },
      { sim_time_s: 784, tls_id: 't1', previous_state: 'CLEARING', new_state: 'NORMAL', reason: '' },
    ]
    const episodes = priorityEpisodes(legacy)
    expect(episodes).toHaveLength(1)
    expect(episodes[0].start).toBe(700)
    expect(episodes[0].end).toBe(784)
  })
})
