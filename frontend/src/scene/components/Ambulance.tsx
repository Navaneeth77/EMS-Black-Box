/**
 * The Candidate D ambulance.
 *
 * Drawn separately from the instanced traffic **only so it can be given a
 * distinct silhouette, a beacon and a route ribbon** — not so it can be given a
 * different motion source. It reads the same interned vehicle out of the same
 * recorded frames as every other vehicle, through the same interpolation. If the
 * FCD recording does not contain it at a given time, nothing is drawn.
 *
 * There is deliberately no fallback path here. An ambulance that kept moving
 * when the record ran out would be a scripted object, and the whole point of the
 * experiment is that it is not one.
 */

import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { Trajectories } from '../lib/types'
import {
  bodyCentreOffset,
  frameFraction,
  headingToRotationY,
  lerp,
  lerpHeading,
} from '../lib/coords'
import { VEHICLE_STYLES } from '../lib/vehicleTypes'
import { geometryFor } from '../lib/assetRegistry'
import { nearestFrameIndex, paneTime, setState } from '../lib/store'
import { slotIn, type FrameIndex } from '../lib/frameIndex'

const STYLE = VEHICLE_STYLES.ambulance

export function Ambulance({
  trajectories,
  frameIndex,
  ambulanceId,
  freezeAtS = null,
}: {
  trajectories: Trajectories
  frameIndex: FrameIndex
  ambulanceId: string
  freezeAtS?: number | null
}) {
  const group = useRef<THREE.Group>(null)
  const beacon = useRef<THREE.Mesh>(null)
  const light = useRef<THREE.PointLight>(null)

  const vehicleIndex = useMemo(
    () => trajectories.vehicle_ids.indexOf(ambulanceId),
    [trajectories, ambulanceId],
  )

  /**
   * The route ribbon is the ambulance's **own recorded track**, not its route
   * edges' centrelines.
   *
   * Drawing lane 0 of each route edge would be a lane-level claim the recording
   * does not support: on a multi-lane approach the ambulance is often not in
   * lane 0, and the ribbon would then diverge from the vehicle it is supposed to
   * describe. Sampling the trajectory instead means the line is, by
   * construction, exactly where SUMO put it.
   */
  const routeGeometry = useMemo(() => {
    if (vehicleIndex < 0) return null
    const points: THREE.Vector3[] = []
    for (let i = 0; i < trajectories.frames.length; i++) {
      const k = slotIn(frameIndex, i, vehicleIndex)
      if (k < 0) continue
      const frame = trajectories.frames[i]
      points.push(new THREE.Vector3(frame.x[k], frame.y[k] + 0.6, frame.z[k]))
    }
    if (points.length < 2) return null
    return new THREE.BufferGeometry().setFromPoints(points)
  }, [trajectories, frameIndex, vehicleIndex])

  useEffect(() => {
    return () => {
      routeGeometry?.dispose()
    }
  }, [routeGeometry])

  useFrame((state) => {
    if (!group.current || vehicleIndex < 0) return
    const time = paneTime(freezeAtS)
    const times = trajectories.times
    const i = nearestFrameIndex(times, time)
    const j = Math.min(times.length - 1, i + 1)
    const t = frameFraction(time, times[i], times[j])
    const a = trajectories.frames[i]
    const b = trajectories.frames[j]

    const ka = slotIn(frameIndex, i, vehicleIndex)
    if (ka < 0) {
      group.current.visible = false
      return
    }
    const kb = slotIn(frameIndex, j, vehicleIndex)
    group.current.visible = true

    const x = kb < 0 ? a.x[ka] : lerp(a.x[ka], b.x[kb], t)
    const y = kb < 0 ? a.y[ka] : lerp(a.y[ka], b.y[kb], t)
    const z = kb < 0 ? a.z[ka] : lerp(a.z[ka], b.z[kb], t)
    const angle = kb < 0 ? a.a[ka] : lerpHeading(a.a[ka], b.a[kb], t)

    // Exactly the rule every other vehicle gets: SUMO's point is the front bumper,
    // so the body sits half its length behind it. No ambulance-only offset.
    const back = bodyCentreOffset(angle, STYLE.length)
    group.current.position.set(x + back.x, y, z + back.z)
    group.current.rotation.y = headingToRotationY(angle)

    // The beacon flashes on wall-clock time, not simulation time: the flashing
    // itself is a rendering cue and nothing in the recording drives its rate.
    // What the siren *does* is simulated — in the EMS runs the ambulance carries
    // SUMO's bluelight device and the traffic around it reacts — but that is in
    // the recorded positions, not in this animation.
    const flash = (Math.sin(state.clock.elapsedTime * 8) + 1) / 2
    if (beacon.current) {
      const material = beacon.current.material as THREE.MeshStandardMaterial
      material.emissiveIntensity = 0.5 + flash * 3
    }
    if (light.current) light.current.intensity = 4 + flash * 18
  })

  if (vehicleIndex < 0) return null

  return (
    <group>
      {routeGeometry && (
        <line>
          <primitive object={routeGeometry} attach="geometry" />
          <lineBasicMaterial color="#e05b5b" transparent opacity={0.75} />
        </line>
      )}
      <group
        ref={group}
        onClick={(event) => {
          event.stopPropagation()
          setState({ selectedVehicle: vehicleIndex, selectedSignal: null })
        }}
      >
        <mesh geometry={geometryFor('ambulance')} castShadow>
          <meshStandardMaterial vertexColors roughness={0.45} metalness={0.05} />
        </mesh>
        <mesh ref={beacon} position={[0, STYLE.height * 1.0, -STYLE.length * 0.2]}>
          <boxGeometry args={[0.9, 0.22, 0.36]} />
          <meshStandardMaterial color="#ff4d4d" emissive="#ff2d2d" emissiveIntensity={1} />
        </mesh>
        <pointLight ref={light} color="#ff5555" distance={38} intensity={8} position={[0, 3, 0]} />
      </group>
    </group>
  )
}
