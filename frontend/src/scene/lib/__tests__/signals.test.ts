/**
 * Signal phase resolution.
 *
 * Vehicles and signals must be driven by the same clock, and a signal's state at
 * time t must be the state SUMO's own fixed-time program specifies at t. If this
 * were off, the scene could show a vehicle crossing on red that never did.
 */

import { describe, it, expect } from 'vitest'
import { isReplayable, phaseAt } from '../../components/TrafficSignals'

const PROGRAM = [
  { state: 'GGrr', duration: 30 },
  { state: 'yyrr', duration: 5 },
  { state: 'rrGG', duration: 30 },
  { state: 'rryy', duration: 5 },
]

describe('phaseAt', () => {
  it('reports the cycle length as the sum of phase durations', () => {
    expect(phaseAt(PROGRAM, 0).cycle).toBe(70)
  })

  it('resolves each phase at its own offset', () => {
    expect(phaseAt(PROGRAM, 0).state).toBe('GGrr')
    expect(phaseAt(PROGRAM, 29.9).state).toBe('GGrr')
    expect(phaseAt(PROGRAM, 30).state).toBe('yyrr')
    expect(phaseAt(PROGRAM, 35).state).toBe('rrGG')
    expect(phaseAt(PROGRAM, 65).state).toBe('rryy')
  })

  it('wraps at the cycle boundary', () => {
    expect(phaseAt(PROGRAM, 70).state).toBe(phaseAt(PROGRAM, 0).state)
    expect(phaseAt(PROGRAM, 700 + 31).state).toBe(phaseAt(PROGRAM, 31).state)
  })

  it('handles a simulation time far from zero', () => {
    // Simulation times in this project start at 600 s; the phase must not
    // depend on the clock's origin.
    expect(phaseAt(PROGRAM, 600).state).toBe(phaseAt(PROGRAM, 600 % 70).state)
  })

  it('degrades safely on an empty or zero-duration program', () => {
    expect(phaseAt([], 10).state).toBe('')
    expect(phaseAt([{ state: 'rrrr', duration: 0 }], 10).cycle).toBe(0)
  })
})

describe('program offset', () => {
  it('shifts the whole cycle', () => {
    // Without the offset the signal is right in shape and wrong in time.
    expect(phaseAt(PROGRAM, 0, 30).state).toBe('yyrr')
    expect(phaseAt(PROGRAM, 0, 35).state).toBe('rrGG')
  })

  it('defaults to no shift', () => {
    expect(phaseAt(PROGRAM, 10).state).toBe(phaseAt(PROGRAM, 10, 0).state)
  })

  it('wraps a negative offset', () => {
    expect(phaseAt(PROGRAM, 40, -10).state).toBe(phaseAt(PROGRAM, 30).state)
  })
})

describe('isReplayable', () => {
  it('accepts a static program', () => {
    expect(isReplayable({ type: 'static', replayable: true })).toBe(true)
  })

  it('rejects an actuated program', () => {
    // An actuated program's phase depends on detector occupancy, which is not
    // in the export. Reconstructing it from the phase list would put a signal
    // state on screen that SUMO never produced.
    expect(isReplayable({ type: 'actuated', replayable: false })).toBe(false)
    expect(isReplayable({ type: 'delay_based' })).toBe(false)
  })

  it('rejects a missing program rather than assuming one', () => {
    expect(isReplayable(undefined)).toBe(false)
  })

  it('trusts the explicit flag over the type string', () => {
    expect(isReplayable({ type: 'actuated', replayable: true })).toBe(true)
  })
})
