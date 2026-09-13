/**
 * Where the ambulance was actually stopped, and by what.
 *
 * This is the first piece of research visualisation in the scene, and it is
 * built only from recorded data. `signal_wait_events` in the manifest is the
 * corrected detector's output: each entry is a halt, with the simulation time,
 * the distance to the stop line, the state of the ambulance's own controlled
 * links at that instant, and whether that state was red.
 *
 * The marker's **position** is not taken from the event — the event has no
 * coordinates. It is looked up in the trajectory recording at the event's own
 * timestamp, so the marker stands exactly where SUMO had the ambulance when the
 * halt was detected.
 *
 * The red/amber distinction matters and is the whole point of the corrected
 * detector: a halt at a red is time a priority policy could recover, and a halt
 * next to a green signal is a queue the policy can do nothing about. Colouring
 * them the same would put back the conflation the detector correction removed.
 *
 * A marker appears when its halt happens in the replay, never before it: a stop
 * drawn ahead of time would show an event the replay has not reached yet.
 */

import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Billboard, Text } from '@react-three/drei'
import type * as THREE from 'three'
import type { Trajectories } from '../lib/types'
import { nearestFrameIndex, paneTime } from '../lib/store'
import { slotIn, type FrameIndex } from '../lib/frameIndex'

export interface SignalWaitEvent {
  sim_time_s: number
  edge_id: string
  lane_position_m: number
  stopped_by_signal?: boolean
  classification?: string
  distance_to_stop_line_m?: Record<string, number>
  ambulance_own_movement?: Record<string, { link_states: string; red: boolean }>
  /** Recorded by HISTORICAL_DEMO: traffic between the ambulance and the next signal. */
  queue_ahead?: { count: number; halted: number }
  distance_to_tls_m?: Record<string, number>
}

const MERGE_WINDOW_S = 20
const MERGE_DISTANCE_M = 25

interface Marker {
  event: SignalWaitEvent
  position: [number, number, number]
  red: boolean
  label: string
  distance: number | null
  ahead: number | null
  halts: number
}

export function DelayMarkers({
  events,
  trajectories,
  frameIndex,
  ambulanceId,
  freezeAtS = null,
}: {
  events: SignalWaitEvent[]
  trajectories: Trajectories
  frameIndex: FrameIndex
  ambulanceId: string
  freezeAtS?: number | null
}) {
  const markers = useMemo(() => {
    const vehicle = trajectories.vehicle_ids.indexOf(ambulanceId)
    if (vehicle < 0) return []
    return events
      .map((event) => {
        const i = nearestFrameIndex(trajectories.times, event.sim_time_s)
        const frame = trajectories.frames[i]
        const k = slotIn(frameIndex, i, vehicle)
        if (k < 0) return null
        const distances = Object.values(event.distance_to_stop_line_m ?? {})
        // The label names the recorded state of the ambulance's own links at the
        // halt. Amber is not green: calling a halt on amber "green" would be wrong.
        const states = Object.values(event.ambulance_own_movement ?? {})
          .map((m) => m.link_states)
          .join('')
        const ahead = event.queue_ahead?.count ?? null
        const label = event.stopped_by_signal
          ? 'HELD AT RED'
          : /[yY]/.test(states)
            ? 'HALTED — signal was amber'
            : /[gG]/.test(states)
              ? 'HALTED — signal was green (queue ahead)'
              : ahead !== null
                ? 'CAUGHT IN THE QUEUE'
                : 'HALTED near a signal'
        return {
          event,
          position: [frame.x[k], frame.y[k], frame.z[k]] as [number, number, number],
          red: Boolean(event.stopped_by_signal),
          label,
          distance: distances.length ? distances[0] : null,
          ahead,
          halts: 1,
        }
      })
      .filter((m): m is NonNullable<typeof m> => m !== null)
      // Creeping forward in a queue records a halt every second or two. They are
      // all real, and they are all the same stop: merged here so the scene shows
      // one marker per stop rather than a stack of five in one place. The count
      // is kept, and the underlying events are untouched.
      .reduce<Marker[]>((kept, marker) => {
        const previous = kept[kept.length - 1]
        const near =
          previous &&
          marker.event.sim_time_s - previous.event.sim_time_s <= MERGE_WINDOW_S &&
          Math.hypot(
            marker.position[0] - previous.position[0],
            marker.position[2] - previous.position[2],
          ) <= MERGE_DISTANCE_M
        if (near) {
          previous.halts += 1
          previous.ahead = Math.max(previous.ahead ?? 0, marker.ahead ?? 0) || previous.ahead
          return kept
        }
        kept.push(marker)
        return kept
      }, [])
  }, [events, trajectories, frameIndex, ambulanceId])

  const groups = useRef<(THREE.Group | null)[]>([])
  useFrame(() => {
    const time = paneTime(freezeAtS)
    markers.forEach((marker, i) => {
      const group = groups.current[i]
      if (group) group.visible = time >= marker.event.sim_time_s
    })
  })

  if (markers.length === 0) return null

  return (
    <group>
      {markers.map((marker, i) => {
        const color = marker.red ? '#e5484d' : '#f5c542'
        return (
          <group
            key={i}
            ref={(node) => {
              groups.current[i] = node
            }}
            position={marker.position}
            visible={false}
          >
            <mesh position={[0, 6.6, 0]}>
              <coneGeometry args={[1.0, 2.6, 12]} />
              <meshStandardMaterial
                color={color}
                emissive={color}
                emissiveIntensity={0.8}
                transparent
                opacity={0.9}
              />
            </mesh>
            <mesh position={[0, 3.2, 0]}>
              <cylinderGeometry args={[0.12, 0.12, 4.6, 6]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.5} />
            </mesh>
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.3, 0]}>
              <ringGeometry args={[1.8, 2.5, 28]} />
              <meshBasicMaterial color={color} transparent opacity={0.6} />
            </mesh>
            <Billboard position={[0, 10, 0]}>
              <Text fontSize={1.5} color={color} anchorX="center" outlineWidth={0.08} outlineColor="#000">
                {marker.label}
              </Text>
              <Text
                position={[0, -1.9, 0]}
                fontSize={1.05}
                color="#e6e8ea"
                anchorX="center"
                outlineWidth={0.06}
                outlineColor="#000"
              >
                {`t=${marker.event.sim_time_s.toFixed(1)}s` +
                  (marker.distance !== null ? ` · ${marker.distance.toFixed(1)} m to stop line` : '') +
                  (marker.ahead !== null ? ` · ${marker.ahead} vehicles ahead` : '') +
                  (marker.halts > 1 ? ` · ${marker.halts} halts here` : '')}
              </Text>
            </Billboard>
          </group>
        )
      })}
    </group>
  )
}
