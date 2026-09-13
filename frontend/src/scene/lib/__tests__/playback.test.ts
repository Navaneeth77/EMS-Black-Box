/**
 * Playback correctness for the demo: signals replay what SUMO applied, and the
 * run freezes on the ambulance's arrival.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import { recordedStateAt } from '../../components/TrafficSignals'
import { advance, getState, setState } from '../store'

describe('recordedStateAt', () => {
  const timeline: [number, string][] = [
    [600, 'GGrr'],
    [630, 'yyrr'],
    [633, 'rrGG'],
    [651.5, 'GGrr'], // e.g. a priority phase reached early by the policy
  ]

  it('returns the state in force at a time', () => {
    expect(recordedStateAt(timeline, 600)).toBe('GGrr')
    expect(recordedStateAt(timeline, 629.9)).toBe('GGrr')
    expect(recordedStateAt(timeline, 630)).toBe('yyrr')
    expect(recordedStateAt(timeline, 640)).toBe('rrGG')
    expect(recordedStateAt(timeline, 700)).toBe('GGrr')
  })

  it('refuses to guess before the first recorded state', () => {
    expect(recordedStateAt(timeline, 599.5)).toBeNull()
    expect(recordedStateAt([], 650)).toBeNull()
  })

  it('agrees with a linear scan', () => {
    for (let t = 598; t <= 660; t += 0.25) {
      let expected: string | null = null
      for (const [time, state] of timeline) if (time <= t) expected = state
      expect(recordedStateAt(timeline, t)).toBe(expected)
    }
  })

  it('never produces a state that was not recorded', () => {
    const recorded = new Set(timeline.map(([, state]) => state))
    for (let t = 600; t <= 700; t += 0.5) {
      expect(recorded.has(recordedStateAt(timeline, t) as string)).toBe(true)
    }
  })
})

describe('arrival stop', () => {
  beforeEach(() => {
    setState({ beginS: 540, endS: 972.5, time: 950, speed: 1, playing: true, stopAtS: 960.5 })
  })

  it('clamps to the arrival time and pauses', () => {
    advance(20)
    expect(getState().time).toBe(960.5)
    expect(getState().playing).toBe(false)
  })

  it('does not wrap back to the start after arrival', () => {
    advance(500)
    expect(getState().time).toBe(960.5)
  })

  it('stays frozen once stopped', () => {
    advance(20)
    advance(20)
    expect(getState().time).toBe(960.5)
    expect(getState().playing).toBe(false)
  })

  it('plays normally before arrival', () => {
    advance(5)
    expect(getState().time).toBeCloseTo(955, 6)
    expect(getState().playing).toBe(true)
  })

  it('wraps as before when there is no arrival stop', () => {
    setState({ stopAtS: null, time: 970, playing: true })
    advance(5)
    expect(getState().time).toBeLessThan(970)
  })
})
