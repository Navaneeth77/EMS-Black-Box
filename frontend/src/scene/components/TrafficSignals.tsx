/**
 * Traffic signal heads: one three-aspect head per controlled link, on a mast arm
 * per approach, coloured from SUMO's applied state.
 *
 * The state shown is SUMO's own. Index *i* of the state string is link index *i*:
 * `G`/`g` light the green aspect, `y`/`Y` the amber, `r`/`R` the red, and `o`/`O`
 * leaves the head dark. When a run recorded its applied states (every DEMO and
 * HISTORICAL_DEMO run does) the recording is used; reconstruction from the static
 * program is only a fallback, and only for a fixed-time program.
 *
 * In HISTORICAL_DEMO the four Central Silk Board approach stop lines share one
 * controller, `silk_board_4way`, because the demo network variant models them as
 * one four-way intersection. In research scenes they remain four separate
 * controllers and are drawn as such. Nothing here merges or splits a controller.
 *
 * Head, pole and stop-bar *placement* is generated from each controlled lane's
 * stop line; the network has no signal-furniture geometry, so positions are
 * ESTIMATED while the link each head belongs to is exact.
 */

import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { TlsLink, TrafficLight } from '../lib/types'
import { headingToRotationY } from '../lib/coords'
import { paneTime, setState } from '../lib/store'
import { approachAspect, aspectFor, recordedStateAt } from '../lib/signalState'

export { aspectFor, recordedStateAt }

const LIT: Record<'red' | 'amber' | 'green', string> = {
  red: '#ff4a4a',
  amber: '#ffbf2e',
  green: '#33e07c',
}
const UNLIT = '#1c1f24'
const BAR = '#e9e6d8'
/**
 * Head height above the stop line. The Silk Board at-grade junction sits under
 * the flyover, whose underside is drawn 4.9 m up, so heads and mast arms are kept
 * below that. A rendering choice (ESTIMATED), like the rest of the furniture.
 */
const HEAD_HEIGHT = 3.6
const POLE = '#565d67'
const POLE_ON_ROUTE = '#6f7884'

/**
 * Which phase of a **static** program is active at simulation time `t`.
 *
 * `programOffset` is the `tlLogic` offset, which shifts the whole cycle; leaving
 * it out would silently draw a signal that is correct in shape but wrong in
 * time. Only valid for a fixed-time program — see {@link isReplayable}.
 */
export function phaseAt(
  phases: { state: string; duration: number }[],
  t: number,
  programOffset = 0,
): { index: number; state: string; cycle: number; offsetInPhase: number } {
  const cycle = phases.reduce((sum, p) => sum + p.duration, 0)
  if (cycle <= 0) return { index: 0, state: phases[0]?.state ?? '', cycle: 0, offsetInPhase: 0 }
  let offset = (((t + programOffset) % cycle) + cycle) % cycle
  for (let i = 0; i < phases.length; i++) {
    if (offset < phases[i].duration) {
      return { index: i, state: phases[i].state, cycle, offsetInPhase: offset }
    }
    offset -= phases[i].duration
  }
  const last = phases.length - 1
  return { index: last, state: phases[last].state, cycle, offsetInPhase: 0 }
}

/**
 * Whether the scene may reconstruct this program's state from its phase list.
 * A program that is not replayable is drawn dark rather than guessed at.
 */
export function isReplayable(program?: { type?: string; replayable?: boolean }): boolean {
  if (!program) return false
  if (program.replayable !== undefined) return program.replayable
  if (program.type !== undefined) return program.type === 'static'
  return true
}

function laneIndex(link: TlsLink): number {
  const value = Number(link.from_lane.slice(link.from_lane.lastIndexOf('_') + 1))
  return Number.isFinite(value) ? value : 0
}

export interface ApproachFurniture {
  key: string
  heading: number
  pole: [number, number, number]
  heads: { link: TlsLink; position: [number, number, number] }[]
  stopBar: { position: [number, number, number]; length: number; rotationY: number }
  mast: { position: [number, number, number]; length: number; rotationY: number }
}

/**
 * Pole, mast arm, heads and stop bar for each approach (links sharing a from-edge).
 *
 * The network is right-hand (no `lefthand` flag), so lane 0 is the kerb lane and
 * the pole stands beyond it, to the right of travel. Several links leaving one
 * lane get heads spread across that lane rather than stacked in one place.
 */
export function approachFurniture(tls: TrafficLight): ApproachFurniture[] {
  const byEdge = new Map<string, TlsLink[]>()
  for (const link of tls.links) {
    const list = byEdge.get(link.from_edge)
    if (list) list.push(link)
    else byEdge.set(link.from_edge, [link])
  }
  const out: ApproachFurniture[] = []
  for (const [edge, links] of byEdge) {
    const sorted = [...links].sort((a, b) => laneIndex(a) - laneIndex(b) || a.index - b.index)
    const kerb = sorted[0]
    const far = sorted[sorted.length - 1]
    const h = (kerb.heading * Math.PI) / 180
    const right: [number, number] = [Math.cos(h), Math.sin(h)]
    const width = kerb.lane_width || 3.2
    const pole: [number, number, number] = [
      kerb.position[0] + right[0] * (width / 2 + 1.4),
      kerb.position[1],
      kerb.position[2] + right[1] * (width / 2 + 1.4),
    ]

    const perLane = new Map<string, TlsLink[]>()
    for (const link of sorted) {
      const list = perLane.get(link.from_lane)
      if (list) list.push(link)
      else perLane.set(link.from_lane, [link])
    }
    const heads: ApproachFurniture['heads'] = []
    for (const list of perLane.values()) {
      list.forEach((link, j) => {
        const shift = (j - (list.length - 1) / 2) * 0.6
        heads.push({
          link,
          position: [
            link.position[0] + right[0] * shift,
            link.position[1] + HEAD_HEIGHT,
            link.position[2] + right[1] * shift,
          ],
        })
      })
    }

    const spread = Math.hypot(far.position[0] - kerb.position[0], far.position[2] - kerb.position[2])
    const rotationY = -h
    const stopBar = {
      position: [
        (kerb.position[0] + far.position[0]) / 2,
        kerb.position[1] + 0.05,
        (kerb.position[2] + far.position[2]) / 2,
      ] as [number, number, number],
      length: spread + width,
      rotationY,
    }
    // The mast reaches from the pole top across to the head farthest from it.
    const reach = heads.reduce(
      (best, head) =>
        Math.max(best, Math.hypot(head.position[0] - pole[0], head.position[2] - pole[2])),
      0,
    )
    const mast = {
      position: [
        pole[0] - right[0] * (reach / 2),
        kerb.position[1] + HEAD_HEIGHT + 0.75,
        pole[2] - right[1] * (reach / 2),
      ] as [number, number, number],
      length: reach + 0.4,
      rotationY,
    }
    out.push({ key: `${tls.id}:${edge}`, heading: kerb.heading, pole, heads, stopBar, mast })
  }
  return out
}

type LampSet = { red: THREE.Mesh | null; amber: THREE.Mesh | null; green: THREE.Mesh | null }

export function TrafficSignals({
  trafficLights,
  highlight,
  timeline,
  freezeAtS = null,
}: {
  trafficLights: TrafficLight[]
  highlight: Set<string>
  /** Applied states recorded from TraCI: the source of truth when present. */
  timeline?: Record<string, [number, string][]>
  freezeAtS?: number | null
}) {
  const lamps = useRef<Record<string, LampSet>>({})
  const bars = useRef<Record<string, THREE.Mesh | null>>({})
  const lastAspect = useRef<Record<string, string>>({})

  const furniture = useMemo(
    () => trafficLights.map((tls) => ({ tls, approaches: approachFurniture(tls) })),
    [trafficLights],
  )

  useFrame(() => {
    const time = paneTime(freezeAtS)
    for (const { tls, approaches } of furniture) {
      const program = tls.programs[0]
      const recorded = timeline?.[tls.id]
      const recordedState = recorded && recorded.length ? recordedStateAt(recorded, time) : null
      if (!program && recordedState === null) continue
      const replayable = recordedState !== null || isReplayable(program)
      const state =
        recordedState ?? (program ? phaseAt(program.phases, time, program.offset ?? 0).state : '')
      for (const approach of approaches) {
        for (const { link } of approach.heads) {
          const key = `${tls.id}:${link.index}`
          const aspect = replayable ? aspectFor(state[link.index]) : 'off'
          if (lastAspect.current[key] === aspect) continue
          lastAspect.current[key] = aspect
          const set = lamps.current[key]
          if (!set) continue
          for (const colour of ['red', 'amber', 'green'] as const) {
            const mesh = set[colour]
            if (!mesh) continue
            const material = mesh.material as THREE.MeshStandardMaterial
            const on = aspect === colour
            material.color.set(on ? LIT[colour] : UNLIT)
            material.emissive.set(on ? LIT[colour] : '#000000')
            material.emissiveIntensity = on ? 2.4 : 0
          }
        }
        // The stop bar carries the approach's own aspect, so the state of a
        // junction reads at a glance from across it. Only when every head on the
        // approach agrees: a split approach has no single colour, and inventing
        // one would say something the state string does not.
        const single = replayable
          ? approachAspect(state, approach.heads.map((h) => h.link.index))
          : 'off'
        const barKey = `${approach.key}:bar`
        if (lastAspect.current[barKey] === single) continue
        lastAspect.current[barKey] = single
        const bar = bars.current[approach.key]
        if (!bar) continue
        const material = bar.material as THREE.MeshStandardMaterial
        const lit = single === 'red' || single === 'amber' || single === 'green'
        material.color.set(lit ? LIT[single as 'red' | 'amber' | 'green'] : BAR)
        material.emissive.set(lit ? LIT[single as 'red' | 'amber' | 'green'] : '#000000')
        material.emissiveIntensity = lit ? 0.55 : 0
      }
    }
  })

  const lampRef = (key: string, colour: keyof LampSet) => (mesh: THREE.Mesh | null) => {
    const set = lamps.current[key] ?? (lamps.current[key] = { red: null, amber: null, green: null })
    set[colour] = mesh
    delete lastAspect.current[key]
  }

  return (
    <group>
      {furniture.map(({ tls, approaches }) =>
        approaches.map((approach) => {
          const onRoute = highlight.has(tls.id)
          return (
            <group
              key={approach.key}
              onClick={(event) => {
                event.stopPropagation()
                setState({ selectedSignal: tls.id, selectedVehicle: null })
              }}
            >
              <mesh
                position={approach.stopBar.position}
                rotation={[0, approach.stopBar.rotationY, 0]}
                ref={(mesh) => {
                  bars.current[approach.key] = mesh
                  delete lastAspect.current[`${approach.key}:bar`]
                }}
              >
                <boxGeometry args={[approach.stopBar.length, 0.08, 0.6]} />
                <meshStandardMaterial color={BAR} roughness={0.85} />
              </mesh>
              <mesh
                position={[approach.pole[0], approach.pole[1] + (HEAD_HEIGHT + 0.9) / 2, approach.pole[2]]}
                castShadow
              >
                <cylinderGeometry args={[0.11, 0.14, HEAD_HEIGHT + 0.9, 8]} />
                <meshStandardMaterial color={onRoute ? POLE_ON_ROUTE : POLE} roughness={0.7} metalness={0.3} />
              </mesh>
              <mesh position={approach.mast.position} rotation={[0, approach.mast.rotationY, 0]} castShadow>
                <boxGeometry args={[approach.mast.length, 0.16, 0.16]} />
                <meshStandardMaterial color={onRoute ? POLE_ON_ROUTE : POLE} roughness={0.7} metalness={0.3} />
              </mesh>
              {approach.heads.map(({ link, position }) => {
                const key = `${tls.id}:${link.index}`
                return (
                  <group
                    key={key}
                    position={position}
                    rotation={[0, headingToRotationY(link.heading + 180), 0]}
                  >
                    <mesh castShadow>
                      <boxGeometry args={[0.44, 1.12, 0.3]} />
                      <meshStandardMaterial color="#202328" roughness={0.85} />
                    </mesh>
                    {(
                      [
                        ['red', 0.34],
                        ['amber', 0],
                        ['green', -0.34],
                      ] as const
                    ).map(([colour, y]) => (
                      <mesh key={colour} position={[0, y, -0.17]} ref={lampRef(key, colour)}>
                        <sphereGeometry args={[0.12, 12, 10]} />
                        <meshStandardMaterial color={UNLIT} emissive="#000000" emissiveIntensity={0} />
                      </mesh>
                    ))}
                  </group>
                )
              })}
            </group>
          )
        }),
      )}
    </group>
  )
}
