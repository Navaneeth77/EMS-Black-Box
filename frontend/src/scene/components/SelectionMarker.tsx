/**
 * A ring under whatever is currently selected.
 *
 * Selecting a vehicle used to change the HUD and nothing else, which makes it
 * hard to tell *which* of ~180 vehicles you actually clicked — and in a
 * comparison view, hard to keep track of the ambulance at all.
 *
 * The marker reads the same recorded frames as the vehicle it follows, through
 * the same O(1) frame index and the same clamped interpolation, so it cannot
 * drift off the thing it is marking. When the selected vehicle is not in the
 * network at the current time the marker hides rather than sitting at its last
 * position, because a marker on an empty road implies a vehicle is there.
 */

import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { Trajectories } from '../lib/types'
import { getState, nearestFrameIndex, paneTime } from '../lib/store'
import { slotIn, type FrameIndex } from '../lib/frameIndex'
import { bodyCentreOffset, frameFraction, lerp } from '../lib/coords'
import { FALLBACK_STYLE, VEHICLE_STYLES } from '../lib/vehicleTypes'

export function SelectionMarker({
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
  const ring = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (!group.current) return
    const { selectedVehicle } = getState()
    const time = paneTime(freezeAtS)
    if (selectedVehicle === null) {
      group.current.visible = false
      return
    }
    const times = trajectories.times
    const i = nearestFrameIndex(times, time)
    const j = Math.min(times.length - 1, i + 1)
    const ka = slotIn(frameIndex, i, selectedVehicle)
    if (ka < 0) {
      group.current.visible = false
      return
    }
    const kb = slotIn(frameIndex, j, selectedVehicle)
    const t = frameFraction(time, times[i], times[j])
    const a = trajectories.frames[i]
    const b = trajectories.frames[j]

    group.current.visible = true
    // Under the vehicle's body, which sits half a length behind SUMO's reported
    // front bumper — the same offset the vehicle itself is drawn with.
    const style =
      VEHICLE_STYLES[
        (trajectories.vehicle_types[trajectories.vehicle_type_index[selectedVehicle]] ??
          '') as keyof typeof VEHICLE_STYLES
      ] ?? FALLBACK_STYLE
    const back = bodyCentreOffset(kb < 0 ? a.a[ka] : b.a[kb], style.length)
    group.current.position.set(
      (kb < 0 ? a.x[ka] : lerp(a.x[ka], b.x[kb], t)) + back.x,
      (kb < 0 ? a.y[ka] : lerp(a.y[ka], b.y[kb], t)) + 0.08,
      (kb < 0 ? a.z[ka] : lerp(a.z[ka], b.z[kb], t)) + back.z,
    )
    if (ring.current) {
      // A slow pulse so the marker is findable in a dense scene without
      // implying anything is happening in the simulation.
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.08
      ring.current.scale.setScalar(pulse)
    }
  })

  const isAmbulance =
    getState().selectedVehicle !== null &&
    trajectories.vehicle_ids[getState().selectedVehicle as number] === ambulanceId
  const color = isAmbulance ? '#ff6b6b' : '#4dd0ff'

  return (
    <group ref={group} visible={false}>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[2.6, 3.4, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.85} depthTest={false} />
      </mesh>
      <mesh position={[0, 6, 0]}>
        <cylinderGeometry args={[0.09, 0.09, 12, 6]} />
        <meshBasicMaterial color={color} transparent opacity={0.45} depthTest={false} />
      </mesh>
    </group>
  )
}
