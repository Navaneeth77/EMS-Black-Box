/**
 * Makes the EMS priority mechanism visible, without inventing any of it.
 *
 * Everything drawn here is keyed to the policy's **recorded state transitions**
 * and the **recorded applied signal states** for the run being shown:
 *
 * - a label appears above a signal only while its recorded policy state is
 *   REQUESTED or PRIORITY_ACTIVE;
 * - "cross traffic held" appears only while the applied state at that instant has
 *   the ambulance's movement green and every other movement red;
 * - the green corridor is drawn only while a signal is PRIORITY_ACTIVE, along the
 *   ambulance's own route from its recorded position to that signal's stop line.
 *
 * Nothing animates on its own clock, anticipates a state, or turns a signal or a
 * road green that SUMO did not. The vocabulary is kept restrained: a translucent
 * strip and small labels, closer to a traffic-management display than a game.
 */

import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Billboard, Text } from '@react-three/drei'
import * as THREE from 'three'
import type { Lane, Manifest, Trajectories, TrafficLight } from '../lib/types'
import { nearestFrameIndex, paneTime } from '../lib/store'
import { slotIn, type FrameIndex } from '../lib/frameIndex'
import { frameFraction, lerp } from '../lib/coords'
import {
  displayStatesAt,
  priorityEpisodes,
  priorityStatesAt,
  type PolicyTransition,
} from '../lib/priority'
import { buildRoutePolyline, corridorSpan } from '../lib/routeCorridor'

export { priorityStatesAt }
export type { PolicyTransition }

const ACTIVE = '#3ddc84'
const REQUESTED = '#f5b93f'
const CORRIDOR_HALF_WIDTH = 2.3
const CORRIDOR_LIFT = 0.3

/** One continuous ribbon over the whole route; a draw range shows a slice of it. */
function ribbon(points: [number, number, number][]): THREE.BufferGeometry {
  const positions = new Float32Array(points.length * 6)
  const indices = new Uint32Array((points.length - 1) * 6)
  for (let i = 0; i < points.length; i++) {
    const a = points[Math.max(0, i - 1)]
    const b = points[Math.min(points.length - 1, i + 1)]
    const dx = b[0] - a[0]
    const dz = b[2] - a[2]
    const len = Math.hypot(dx, dz) || 1
    const nx = -dz / len
    const nz = dx / len
    const [x, y, z] = points[i]
    positions.set(
      [
        x + nx * CORRIDOR_HALF_WIDTH,
        y + CORRIDOR_LIFT,
        z + nz * CORRIDOR_HALF_WIDTH,
        x - nx * CORRIDOR_HALF_WIDTH,
        y + CORRIDOR_LIFT,
        z - nz * CORRIDOR_HALF_WIDTH,
      ],
      i * 6,
    )
  }
  for (let i = 0; i < points.length - 1; i++) {
    const a = i * 2
    indices.set([a, a + 2, a + 1, a + 1, a + 2, a + 3], i * 6)
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setIndex(new THREE.BufferAttribute(indices, 1))
  geometry.computeBoundingSphere()
  return geometry
}

export function EmsPriorityOverlay({
  manifest,
  trajectories,
  frameIndex,
  trafficLights,
  lanes,
  freezeAtS = null,
}: {
  manifest: Manifest
  trajectories: Trajectories
  frameIndex: FrameIndex
  trafficLights: TrafficLight[]
  lanes: Lane[]
  freezeAtS?: number | null
}) {
  const transitions = (manifest.policy_transitions ?? []) as PolicyTransition[]
  const episodes = useMemo(() => priorityEpisodes(transitions), [transitions])
  const ambulanceIndex = useMemo(
    () => trajectories.vehicle_ids.indexOf(manifest.ambulance.vehicle_id),
    [trajectories, manifest],
  )
  const route = useMemo(
    () => buildRoutePolyline(manifest.ambulance.route_edges ?? [], lanes),
    [manifest, lanes],
  )
  const geometry = useMemo(() => (route.points.length > 1 ? ribbon(route.points) : null), [route])
  useEffect(() => () => geometry?.dispose(), [geometry])

  const onRoute = useMemo(
    () =>
      new Map(
        manifest.route_traffic_lights.map((t) => [
          t.tls_id,
          { approach: t.approach_edge ?? '', links: t.ambulance_link_indices },
        ]),
      ),
    [manifest],
  )
  const anchors = useMemo(() => {
    const out = new Map<string, [number, number, number]>()
    for (const tls of trafficLights) {
      if (!onRoute.has(tls.id) || tls.links.length === 0) continue
      const n = tls.links.length
      out.set(tls.id, [
        tls.links.reduce((s, l) => s + l.position[0], 0) / n,
        Math.max(...tls.links.map((l) => l.position[1])),
        tls.links.reduce((s, l) => s + l.position[2], 0) / n,
      ])
    }
    return out
  }, [trafficLights, onRoute])

  const corridor = useRef<THREE.Mesh>(null)
  const labels = useRef<
    Record<string, { active: THREE.Group | null; requested: THREE.Group | null; held: THREE.Group | null }>
  >({})
  const slot = (id: string) =>
    labels.current[id] ?? (labels.current[id] = { active: null, requested: null, held: null })

  useFrame(() => {
    const time = paneTime(freezeAtS)
    const active = displayStatesAt(manifest, episodes, time)

    for (const id of anchors.keys()) {
      const label = labels.current[id]
      if (!label) continue
      const entry = active.get(id)
      const isActive = entry?.state === 'PRIORITY_ACTIVE'
      if (label.active) label.active.visible = isActive
      if (label.requested) label.requested.visible = entry?.state === 'REQUESTED'
      if (label.held) label.held.visible = isActive && entry.ambulanceGreen && entry.crossRed
    }

    const mesh = corridor.current
    if (!mesh || !geometry) return
    let span: [number, number] | null = null
    if (ambulanceIndex >= 0 && active.size > 0) {
      const times = trajectories.times
      const i = nearestFrameIndex(times, time)
      const j = Math.min(times.length - 1, i + 1)
      const ka = slotIn(frameIndex, i, ambulanceIndex)
      if (ka >= 0) {
        const kb = slotIn(frameIndex, j, ambulanceIndex)
        const f = frameFraction(time, times[i], times[j])
        const a = trajectories.frames[i]
        const b = trajectories.frames[j]
        const ax = kb < 0 ? a.x[ka] : lerp(a.x[ka], b.x[kb], f)
        const az = kb < 0 ? a.z[ka] : lerp(a.z[ka], b.z[kb], f)
        for (const [id, entry] of active) {
          if (entry.state !== 'PRIORITY_ACTIVE') continue
          const candidate = corridorSpan(route, [ax, az], onRoute.get(id)?.approach ?? '')
          if (candidate && (!span || candidate[1] < span[1])) span = candidate
        }
      }
    }
    if (!span) {
      mesh.visible = false
      return
    }
    mesh.visible = true
    geometry.setDrawRange(span[0] * 6, (span[1] - span[0]) * 6)
  })

  if (transitions.length === 0) return null

  return (
    <group>
      {geometry && (
        <mesh ref={corridor} geometry={geometry} visible={false} renderOrder={2} frustumCulled={false}>
          <meshBasicMaterial
            color={ACTIVE}
            transparent
            opacity={0.3}
            depthWrite={false}
            side={THREE.DoubleSide}
            polygonOffset
            polygonOffsetFactor={-4}
            polygonOffsetUnits={-4}
          />
        </mesh>
      )}

      {[...anchors.entries()].map(([id, position]) => (
        <group key={id} position={[position[0], position[1] + 10, position[2]]}>
          <group ref={(node) => void (slot(id).active = node)} visible={false}>
            <Billboard>
              <mesh>
                <planeGeometry args={[15, 3.2]} />
                <meshBasicMaterial color="#0d1014" transparent opacity={0.78} depthWrite={false} />
              </mesh>
              <Text fontSize={1.5} color={ACTIVE} anchorX="center" anchorY="middle" position={[0, 0, 0.1]}>
                EMS PRIORITY
              </Text>
            </Billboard>
          </group>
          <group ref={(node) => void (slot(id).requested = node)} visible={false}>
            <Billboard>
              <mesh>
                <planeGeometry args={[19, 3.2]} />
                <meshBasicMaterial color="#0d1014" transparent opacity={0.78} depthWrite={false} />
              </mesh>
              <Text fontSize={1.4} color={REQUESTED} anchorX="center" anchorY="middle" position={[0, 0, 0.1]}>
                PRIORITY REQUESTED
              </Text>
            </Billboard>
          </group>
          <group ref={(node) => void (slot(id).held = node)} visible={false} position={[0, -3.4, 0]}>
            <Billboard>
              <mesh>
                <planeGeometry args={[17, 2.4]} />
                <meshBasicMaterial color="#0d1014" transparent opacity={0.7} depthWrite={false} />
              </mesh>
              <Text fontSize={1.05} color="#f3f4f6" anchorX="center" anchorY="middle" position={[0, 0, 0.1]}>
                CROSS TRAFFIC HELD AT RED
              </Text>
            </Billboard>
          </group>
        </group>
      ))}
    </group>
  )
}
