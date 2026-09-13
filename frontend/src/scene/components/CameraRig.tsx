/**
 * Camera control: orbit by default, with optional follow.
 *
 * Follow mode moves the orbit *target* to the followed vehicle and lets the user
 * keep full orbit control around it, rather than seizing the camera. A rig that
 * takes the controls away is disorienting and makes it hard to look at anything
 * next to the thing being followed — which, for delay attribution, is usually
 * the interesting part.
 *
 * The target is damped toward the vehicle so a 2 Hz trajectory does not make the
 * camera stutter. Damping applies to the camera only; no vehicle is moved.
 */

import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import * as THREE from 'three'
import type { Trajectories } from '../lib/types'
import { getState, nearestFrameIndex, paneTime } from '../lib/store'
import { slotIn, type FrameIndex } from '../lib/frameIndex'
import { lerp, frameFraction } from '../lib/coords'

export interface CameraPreset {
  position: [number, number, number]
  target: [number, number, number]
}

export function CameraRig({
  trajectories,
  frameIndex,
  preset,
  freezeAtS = null,
}: {
  trajectories: Trajectories
  frameIndex: FrameIndex
  preset: CameraPreset | null
  freezeAtS?: number | null
}) {
  const controls = useRef<OrbitControlsImpl>(null)
  const { camera } = useThree()
  const desired = useRef(new THREE.Vector3())

  useEffect(() => {
    if (!preset || !controls.current) return
    camera.position.set(...preset.position)
    controls.current.target.set(...preset.target)
    controls.current.update()
  }, [preset, camera])

  useFrame((_, delta) => {
    const { followVehicle } = getState()
    if (followVehicle === null || !controls.current) return
    const time = paneTime(freezeAtS)
    const times = trajectories.times
    const i = nearestFrameIndex(times, time)
    const j = Math.min(times.length - 1, i + 1)
    const t = frameFraction(time, times[i], times[j])
    const a = trajectories.frames[i]
    const b = trajectories.frames[j]
    const ka = slotIn(frameIndex, i, followVehicle)
    if (ka < 0) return
    const kb = slotIn(frameIndex, j, followVehicle)
    desired.current.set(
      kb < 0 ? a.x[ka] : lerp(a.x[ka], b.x[kb], t),
      (kb < 0 ? a.y[ka] : lerp(a.y[ka], b.y[kb], t)) + 2,
      kb < 0 ? a.z[ka] : lerp(a.z[ka], b.z[kb], t),
    )
    const offset = camera.position.clone().sub(controls.current.target)
    const damping = 1 - Math.pow(0.001, delta)
    controls.current.target.lerp(desired.current, damping)
    camera.position.copy(controls.current.target).add(offset)
    controls.current.update()
  })

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      dampingFactor={0.08}
      maxPolarAngle={Math.PI / 2.06}
      minDistance={12}
      maxDistance={3500}
      zoomSpeed={1.1}
    />
  )
}
