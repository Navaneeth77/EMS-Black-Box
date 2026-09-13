/**
 * EMS priority state, replayed from the run's own recorded transitions.
 *
 * The state shown is whichever transition the policy last recorded **at or
 * before** the current simulation time. It is never inferred from where the
 * ambulance is, and never shown unless the simulation actually entered it —
 * the point of the state machine is that `PRIORITY_ACTIVE` means priority was
 * granted, not that the ambulance was nearby.
 *
 * Under NORMAL there are no transitions, so the panel says so rather than
 * showing an idle-looking "NORMAL" that could be mistaken for a policy running.
 */

import { useMemo } from 'react'
import type { Manifest } from '../scene/lib/types'

export interface StateTransition {
  sim_time_s: number
  tls_id: string
  previous_state: string
  new_state: string
  reason: string
  ambulance_distance_to_tls_m?: number
}

const STATE_STYLE: Record<string, string> = {
  NORMAL: 'bg-slate-700/50 text-slate-200',
  REQUESTED: 'bg-amber-500/20 text-amber-200 border border-amber-500/40',
  PRIORITY_ACTIVE: 'bg-emerald-500/20 text-emerald-200 border border-emerald-500/50',
  CLEARING: 'bg-sky-500/20 text-sky-200 border border-sky-500/40',
}

export function EmsStatusPanel({
  manifest,
  transitions,
  time,
}: {
  manifest: Manifest
  transitions: StateTransition[]
  time: number
}) {
  const state = useMemo(() => {
    const perTls = new Map<string, StateTransition>()
    let requests = 0
    let activeSince: number | null = null
    let activeTotal = 0
    for (const t of transitions) {
      if (t.sim_time_s > time) break
      perTls.set(t.tls_id, t)
      if (t.new_state === 'REQUESTED') requests++
      if (t.new_state === 'PRIORITY_ACTIVE') activeSince = t.sim_time_s
      if (t.new_state === 'CLEARING' && activeSince !== null) {
        activeTotal += t.sim_time_s - activeSince
        activeSince = null
      }
    }
    if (activeSince !== null) activeTotal += time - activeSince
    const current = [...perTls.values()]
    const priority = current.find((t) => t.new_state === 'PRIORITY_ACTIVE')
    const requested = current.find((t) => t.new_state === 'REQUESTED')
    const clearing = current.find((t) => t.new_state === 'CLEARING')
    const active = priority ?? requested ?? clearing ?? null
    return {
      label: active?.new_state ?? 'NORMAL',
      tls: active?.tls_id ?? null,
      reason: active?.reason ?? null,
      distance: active?.ambulance_distance_to_tls_m ?? null,
      requests,
      activeTotal,
      intersections: new Set(transitions.filter((t) => t.sim_time_s <= time).map((t) => t.tls_id)).size,
    }
  }, [transitions, time])

  const policy = manifest.scenario.policy

  return (
    <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          EMS priority
        </span>
        <span className="font-mono text-[11px] text-slate-400">{policy}</span>
      </div>

      {transitions.length === 0 ? (
        <div className="text-[11px] leading-snug text-slate-400">
          This run is the <span className="font-mono text-slate-200">{policy}</span> baseline.
          No priority was requested, because no policy was acting — not because a
          policy ran and did nothing.
        </div>
      ) : (
        <>
          <div
            className={`mb-2 rounded px-2 py-1.5 text-center font-mono text-[13px] font-bold tracking-wide ${
              STATE_STYLE[state.label] ?? STATE_STYLE.NORMAL
            }`}
          >
            {state.label.replace('_', ' ')}
          </div>
          {state.tls && (
            <div className="mb-1 break-all text-[10px] leading-snug text-slate-400">
              at <span className="font-mono text-slate-300">{state.tls.slice(0, 30)}…</span>
              {state.distance !== null && (
                <span className="ml-1 text-slate-300">({state.distance.toFixed(0)} m out)</span>
              )}
            </div>
          )}
          {state.reason && (
            <div className="mb-2 text-[10px] leading-snug text-slate-400">“{state.reason}”</div>
          )}
          <div className="flex justify-between border-t border-slate-700/60 pt-1.5 text-[11px]">
            <span className="text-slate-400">Requests</span>
            <span className="font-mono text-slate-100">{state.requests}</span>
          </div>
          <div className="flex justify-between text-[11px]">
            <span className="text-slate-400">Priority held</span>
            <span className="font-mono text-slate-100">{state.activeTotal.toFixed(1)} s</span>
          </div>
          <div className="flex justify-between text-[11px]">
            <span className="text-slate-400">Intersections</span>
            <span className="font-mono text-slate-100">{state.intersections}</span>
          </div>
        </>
      )}
    </div>
  )
}
