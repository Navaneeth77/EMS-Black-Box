/**
 * The renderer's half of the coordinate contract.
 *
 * The Python exporter is tested separately; what is tested here is the maths the
 * frontend still does every frame — heading conversion and interpolation between
 * recorded samples — plus the frame lookup that keeps vehicles and signals on one
 * clock.
 *
 * The interpolation tests exist because interpolation is the one place a
 * renderer can quietly invent state. The property being defended is that it
 * never produces anything outside the two recorded samples it sits between.
 */

import { describe, it, expect } from 'vitest'
import {
  headingToRotationY,
  rotationYToHeading,
  headingToDirection,
  lerp,
  lerpHeading,
  frameFraction,
} from '../coords'
import { nearestFrameIndex } from '../store'

describe('heading conversion', () => {
  it('round-trips every quadrant', () => {
    for (const angle of [0, 30, 90, 175, 180, 271, 359.9]) {
      expect(rotationYToHeading(headingToRotationY(angle))).toBeCloseTo(angle, 6)
    }
  })

  it('maps compass directions to the scene axes', () => {
    // north -> -Z, east -> +X, south -> +Z, west -> -X
    const cases: [number, [number, number]][] = [
      [0, [0, -1]],
      [90, [1, 0]],
      [180, [0, 1]],
      [270, [-1, 0]],
    ]
    for (const [angle, [x, z]] of cases) {
      const d = headingToDirection(angle)
      expect(d[0]).toBeCloseTo(x, 9)
      expect(d[2]).toBeCloseTo(z, 9)
    }
  })

  it('points a nose-forward (-Z) mesh along the direction of travel', () => {
    // Rotating (0,0,-1) about +Y by theta gives (-sin t, 0, -cos t).
    for (const angle of [0, 45, 137, 300]) {
      const t = headingToRotationY(angle)
      const noseX = -Math.sin(t)
      const noseZ = -Math.cos(t)
      const d = headingToDirection(angle)
      expect(noseX).toBeCloseTo(d[0], 9)
      expect(noseZ).toBeCloseTo(d[2], 9)
    }
  })
})

describe('heading interpolation', () => {
  it('takes the short arc across the 0/360 wrap', () => {
    // 350 -> 10 is +20 degrees, not -340.
    expect(lerpHeading(350, 10, 0.5) % 360).toBeCloseTo(0, 6)
  })

  it('does not spin the long way round', () => {
    const mid = lerpHeading(10, 350, 0.5)
    // Should pass through 0, not 180.
    expect(Math.abs(((mid % 360) + 360) % 360)).toBeCloseTo(0, 6)
  })

  it('returns the endpoints exactly', () => {
    expect(lerpHeading(20, 100, 0)).toBeCloseTo(20, 9)
    expect(lerpHeading(20, 100, 1)).toBeCloseTo(100, 9)
  })
})

describe('position interpolation', () => {
  it('never leaves the interval between two recorded samples', () => {
    for (let i = 0; i <= 10; i++) {
      const v = lerp(100, 140, i / 10)
      expect(v).toBeGreaterThanOrEqual(100)
      expect(v).toBeLessThanOrEqual(140)
    }
  })

  it('clamps rather than extrapolating outside the recorded window', () => {
    // A time before or after the bracketing samples must pin to an endpoint.
    // Extrapolated positions would be states SUMO never produced.
    expect(frameFraction(90, 100, 110)).toBe(0)
    expect(frameFraction(200, 100, 110)).toBe(1)
    expect(frameFraction(105, 100, 110)).toBeCloseTo(0.5, 9)
  })

  it('handles a zero-length interval without dividing by zero', () => {
    expect(frameFraction(100, 100, 100)).toBe(0)
  })
})

describe('frame lookup (timestep synchronisation)', () => {
  const times = [600, 600.5, 601, 601.5, 602, 602.5, 603]

  it('finds the last sample at or before the requested time', () => {
    expect(nearestFrameIndex(times, 600)).toBe(0)
    expect(nearestFrameIndex(times, 601)).toBe(2)
    expect(nearestFrameIndex(times, 601.4)).toBe(2)
    expect(nearestFrameIndex(times, 601.5)).toBe(3)
  })

  it('pins to the ends outside the window', () => {
    expect(nearestFrameIndex(times, 0)).toBe(0)
    expect(nearestFrameIndex(times, 99999)).toBe(times.length - 1)
  })

  it('agrees with a linear scan at every probe', () => {
    // The binary search is the hot path for every vehicle every frame; a
    // one-index error there desynchronises vehicles from signals.
    for (let t = 599; t <= 604; t += 0.1) {
      let expected = 0
      for (let i = 0; i < times.length; i++) if (times[i] <= t) expected = i
      expect(nearestFrameIndex(times, t)).toBe(expected)
    }
  })
})
