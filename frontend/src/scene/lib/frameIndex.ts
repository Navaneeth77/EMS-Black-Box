/**
 * O(1) lookup from a vehicle to its slot in a given frame.
 *
 * The trajectory format is frame-major parallel arrays, which is right for
 * drawing everything at time t but wrong for "where is *this* vehicle" — that
 * needs a scan. Several components need exactly that every frame: the ambulance,
 * the camera when following, and the HUD when something is selected. Scanning a
 * ~300-entry array two or three times per component per frame, or rebuilding a
 * Map of that size 60 times a second, is avoidable work and avoidable garbage.
 *
 * So the inverse mapping is built once at load: for each frame, an
 * `Int32Array[vehicleIndex] -> slot`, with -1 meaning the vehicle is not in that
 * frame. For 721 frames and 468 vehicles that is ~1.3 MB, paid once, and it
 * turns every per-frame lookup into a single array read.
 *
 * `-1` is the explicit "absent" value rather than `undefined`, because absent
 * has to stay distinguishable from slot 0 — conflating them would draw a vehicle
 * that had already left at another vehicle's position.
 */

import type { Trajectories } from './types'

export type FrameIndex = Int32Array[]

export function buildFrameIndex(trajectories: Trajectories): FrameIndex {
  const vehicleCount = trajectories.vehicle_ids.length
  return trajectories.frames.map((frame) => {
    const slots = new Int32Array(vehicleCount).fill(-1)
    for (let k = 0; k < frame.id.length; k++) slots[frame.id[k]] = k
    return slots
  })
}

/** Slot of `vehicle` in frame `i`, or -1 if it is not in the network then. */
export function slotIn(index: FrameIndex, i: number, vehicle: number): number {
  const frame = index[i]
  if (!frame || vehicle < 0 || vehicle >= frame.length) return -1
  return frame[vehicle]
}
