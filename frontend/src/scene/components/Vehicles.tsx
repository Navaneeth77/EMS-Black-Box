/**
 * Vehicles, driven entirely by the recorded FCD trajectory.
 *
 * One `InstancedMesh` per vType — seven draw calls for ~470 vehicles instead of
 * one each. Every instance's position, heading and visibility comes from the
 * recording; the component holds no motion model of its own, so there is nothing
 * here that *could* invent a path.
 *
 * Between two recorded samples the transform is linearly interpolated so motion
 * reads smoothly at 60 Hz from 2 Hz data. That is a display convenience and it
 * is bounded by the samples on either side: it never runs past the last recorded
 * frame, and a vehicle absent from either bracketing frame is hidden rather than
 * drawn somewhere plausible. Headings interpolate along the shortest arc, which
 * is the only reading consistent with two recorded angles.
 */

import { useMemo, useRef } from 'react'
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

const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0)

export function Vehicles({
  trajectories,
  frameIndex,
  ambulanceId,
  onPick,
  freezeAtS = null,
}: {
  trajectories: Trajectories
  frameIndex: FrameIndex
  ambulanceId: string
  onPick: (vehicleIndex: number) => void
  /** A comparison pane holds its last frame from its own ambulance's arrival on. */
  freezeAtS?: number | null
}) {
  const meshes = useRef<Record<string, THREE.InstancedMesh | null>>({})
  const matrix = useMemo(() => new THREE.Matrix4(), [])
  const quaternion = useMemo(() => new THREE.Quaternion(), [])
  const euler = useMemo(() => new THREE.Euler(), [])
  const scaleOne = useMemo(() => new THREE.Vector3(1, 1, 1), [])
  const position = useMemo(() => new THREE.Vector3(), [])

  const ambulanceIndex = useMemo(
    () => trajectories.vehicle_ids.indexOf(ambulanceId),
    [trajectories, ambulanceId],
  )

  /** Which vType each interned vehicle belongs to, resolved once. */
  const typeKeys = useMemo(() => {
    const keys = Object.keys(VEHICLE_STYLES)
    return trajectories.vehicle_type_index.map((t) => {
      const name = trajectories.vehicle_types[t] ?? ''
      for (const key of keys) if (name === key || name.endsWith(key)) return key
      return 'car'
    })
  }, [trajectories])

  /** Vehicles grouped per vType, so each instanced mesh has a stable slot map. */
  const groups = useMemo(() => {
    const out: Record<string, number[]> = {}
    for (const key of Object.keys(VEHICLE_STYLES)) out[key] = []
    typeKeys.forEach((key, vehicleIndex) => {
      if (vehicleIndex === ambulanceIndex) return // drawn separately, always visible
      out[key].push(vehicleIndex)
    })
    return out
  }, [typeKeys, ambulanceIndex])

  const slotOf = useMemo(() => {
    const map = new Map<number, number>()
    for (const list of Object.values(groups)) list.forEach((v, i) => map.set(v, i))
    return map
  }, [groups])

  // One shared geometry per type, from the asset registry. Disposal is the
  // registry's job, because the geometry outlives any single mount.
  const geometries = useMemo(() => {
    const out: Record<string, THREE.BufferGeometry> = {}
    for (const key of Object.keys(VEHICLE_STYLES)) out[key] = geometryFor(key)
    return out
  }, [])

  useFrame(() => {
    const time = paneTime(freezeAtS)
    const times = trajectories.times
    const i = nearestFrameIndex(times, time)
    const j = Math.min(times.length - 1, i + 1)
    const t = frameFraction(time, times[i], times[j])
    const a = trajectories.frames[i]
    const b = trajectories.frames[j]

    const counts: Record<string, number> = {}
    for (const key of Object.keys(groups)) counts[key] = 0
    const written = new Set<number>()

    for (let k = 0; k < a.id.length; k++) {
      const vehicle = a.id[k]
      const key = typeKeys[vehicle]
      const mesh = meshes.current[key]
      if (!mesh) continue
      const slot = slotOf.get(vehicle)
      if (slot === undefined) continue

      const k2 = slotIn(frameIndex, j, vehicle)
      // Present in this frame but not the next: it left the network between
      // samples. Hold it at its last recorded position rather than sliding it
      // toward some other vehicle's slot.
      const x = k2 < 0 ? a.x[k] : lerp(a.x[k], b.x[k2], t)
      const y = k2 < 0 ? a.y[k] : lerp(a.y[k], b.y[k2], t)
      const z = k2 < 0 ? a.z[k] : lerp(a.z[k], b.z[k2], t)
      const angle = k2 < 0 ? a.a[k] : lerpHeading(a.a[k], b.a[k2], t)

      // SUMO reports the front bumper; the mesh is centred on its own body, so it
      // is placed half a length back along its heading. See bodyCentreOffset.
      const back = bodyCentreOffset(angle, VEHICLE_STYLES[key].length)
      position.set(x + back.x, y, z + back.z)
      euler.set(0, headingToRotationY(angle), 0)
      quaternion.setFromEuler(euler)
      matrix.compose(position, quaternion, scaleOne)
      mesh.setMatrixAt(slot, matrix)
      written.add(vehicle)
      counts[key] = Math.max(counts[key], slot + 1)
    }

    // Anything not in this frame is scaled to zero: absent from the recording
    // means absent from the scene, never parked at the origin.
    for (const [key, list] of Object.entries(groups)) {
      const mesh = meshes.current[key]
      if (!mesh) continue
      for (let slot = 0; slot < list.length; slot++) {
        if (!written.has(list[slot])) mesh.setMatrixAt(slot, HIDDEN)
      }
      mesh.count = list.length
      mesh.instanceMatrix.needsUpdate = true
    }
  })

  return (
    <group>
      {Object.entries(groups).map(([key, list]) => {
        return (
          <instancedMesh
            key={key}
            ref={(mesh) => {
              meshes.current[key] = mesh
            }}
            args={[geometries[key], undefined, Math.max(1, list.length)]}
            castShadow
            frustumCulled={false}
            onClick={(event) => {
              event.stopPropagation()
              const slot = event.instanceId
              if (slot === undefined) return
              const vehicle = list[slot]
              if (vehicle !== undefined) {
                setState({ selectedSignal: null })
                onPick(vehicle)
              }
            }}
          >
            <meshStandardMaterial vertexColors roughness={0.55} metalness={0.05} />
          </instancedMesh>
        )
      })}
    </group>
  )
}
