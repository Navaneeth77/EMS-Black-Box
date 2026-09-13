/**
 * The scene-side half of the coordinate contract.
 *
 * The Python exporter (`simulation/ems_sim/viz/coords.py`) has already put every
 * position into scene metres, so this module does **not** re-project anything —
 * doing that twice is how a scene ends up subtly mirrored. What lives here is
 * the small amount of maths the renderer still has to do per frame:
 *
 *  - turning a SUMO heading into a Three.js Y rotation,
 *  - interpolating between two *recorded* samples for display.
 *
 * Both are defined against the same conventions the exporter documents, and both
 * are tested numerically against the Python implementation's expectations.
 *
 * Scene frame: **x east, y up, z south**, 1 unit = 1 metre.
 */

/** Transform metadata carried in `network.json`, for display and validation. */
export interface SceneTransformInfo {
  net_offset: [number, number]
  proj_parameter: string
  origin_sumo: [number, number]
  scale: number
  layer_height_m: number
  axes: Record<string, string>
  elevation_data_class: string
  elevation_basis: string
}

const DEG_TO_RAD = Math.PI / 180

/**
 * SUMO heading (degrees clockwise from north) to a Three.js rotation about +Y.
 *
 * SUMO: 0° = north, 90° = east. Scene: north is −Z, east is +X, so direction of
 * travel is `(sin a, 0, −cos a)`. A Y rotation of θ maps a mesh's −Z axis to
 * `(−sin θ, 0, −cos θ)`, so θ = −a reproduces it exactly.
 *
 * **Meshes must be modelled nose-forward along −Z.**
 */
export function headingToRotationY(angleDeg: number): number {
  return -angleDeg * DEG_TO_RAD
}

/** Inverse of {@link headingToRotationY}, normalised to [0, 360). */
export function rotationYToHeading(rotationY: number): number {
  return (((-rotationY / DEG_TO_RAD) % 360) + 360) % 360
}

/** Unit direction of travel in scene space, for camera framing and debug arrows. */
export function headingToDirection(angleDeg: number): [number, number, number] {
  const a = angleDeg * DEG_TO_RAD
  return [Math.sin(a), 0, -Math.cos(a)]
}

/**
 * Shortest-arc interpolation between two headings, in degrees.
 *
 * A vehicle crossing north wraps 359° → 1°. Interpolating those linearly spins
 * it 358° the wrong way in one frame, which reads as a vehicle snapping round on
 * the spot. Going the short way round is the only interpretation consistent with
 * the two recorded samples.
 */
export function lerpHeading(a: number, b: number, t: number): number {
  let delta = ((b - a + 540) % 360) - 180
  return a + delta * t
}

/** Plain linear interpolation, for positions and speeds. */
export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * Fraction of the way from `times[i]` to `times[i+1]` that `t` sits at.
 *
 * Clamped, so a time outside the recorded window pins to an end sample rather
 * than extrapolating. Extrapolated positions would be invented states.
 */
export function frameFraction(t: number, t0: number, t1: number): number {
  if (t1 <= t0) return 0
  return Math.min(1, Math.max(0, (t - t0) / (t1 - t0)))
}


/**
 * SUMO reports a vehicle's **front bumper**, not its centre.
 *
 * `fcd-output` gives the position of the centre of the front bumper, and the gaps
 * the car-following model leaves are measured from it: in a stopped queue the
 * distance between two reported points is the leader's length plus the follower's
 * minGap. A model centred on that point therefore sticks half its length into the
 * vehicle in front — worst for the longest bodies, which is why the 6 m ambulance
 * appeared to drive inside the car ahead of it.
 *
 * So every body is placed half its own length behind the reported point, along its
 * own heading. The same rule for every vehicle, ambulance included; nothing is
 * nudged sideways and no vehicle gets an offset of its own.
 */
export function bodyCentreOffset(
  headingDeg: number,
  length: number,
): { x: number; z: number } {
  const a = (headingDeg * Math.PI) / 180
  // Forward in scene axes: x east, z south (see the transform above).
  return { x: -Math.sin(a) * (length / 2), z: Math.cos(a) * (length / 2) }
}
