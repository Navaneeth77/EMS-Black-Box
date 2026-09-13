/**
 * Disturbance state, read from the run that produced the scene.
 *
 * Every value here is either the declared incident configuration or a SUMO
 * measurement from the same run. Nothing is computed in the browser: the
 * halting counts come from `lane.getLastStepHaltingNumber` during the run, and
 * the queue lengths from SUMO's own `--queue-output`. The panel shows "not in
 * this scenario" rather than zeros when no incident was configured, because a
 * zero looks like a measurement.
 */

import type { Manifest } from '../scene/lib/types'

function Row({ label, value, warn = false }: { label: string; value: React.ReactNode; warn?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={`font-mono text-[12px] ${warn ? 'text-amber-300' : 'text-slate-100'}`}>{value}</span>
    </div>
  )
}

export function DisturbancePanel({ manifest, time }: { manifest: Manifest; time: number }) {
  const wrapper = manifest.incident
  const incident = wrapper?.incident ?? null

  if (!incident) {
    return (
      <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Disturbance
        </div>
        <div className="text-[11px] leading-snug text-slate-400">
          No disturbance in this scenario. Load the incident export to see one —
          this panel shows nothing rather than zeros, because a zero reads as a
          measurement.
        </div>
      </div>
    )
  }

  const active = time >= incident.start_time_s && time < incident.end_time_s
  const q = manifest.queues ?? {}
  const num = (key: string) => {
    const v = q[key]
    return typeof v === 'number' ? v : null
  }

  return (
    <div
      className={`pointer-events-auto rounded-lg border p-3 shadow-xl backdrop-blur ${
        active ? 'border-amber-500/70 bg-amber-950/40' : 'border-slate-700/60 bg-slate-900/85'
      }`}
    >
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Disturbance
        </span>
        {active ? (
          <span className="animate-pulse rounded bg-amber-500/25 px-1.5 py-px text-[10px] font-bold tracking-wide text-amber-200">
            ● ACTIVE
          </span>
        ) : (
          <span className="rounded bg-slate-700/50 px-1.5 py-px text-[10px] tracking-wide text-slate-300">
            {time < incident.start_time_s ? 'PENDING' : 'CLEARED'}
          </span>
        )}
      </div>

      <Row label="Type" value={incident.incident_type.replace('_', ' ').toLowerCase()} />
      <Row label="Road" value={incident.edge_id} />
      <Row label="Lane" value={incident.lane_id} />
      <Row label="Start" value={`${incident.start_time_s.toFixed(0)} s`} />
      <Row label="Duration" value={`${incident.duration_s.toFixed(0)} s`} />
      {num('peak_halting_during_incident') !== null && (
        <Row label="Peak halted veh." value={num('peak_halting_during_incident')} warn={active} />
      )}
      {num('max_halting_vehicles') !== null && (
        <Row label="Max halted (run)" value={num('max_halting_vehicles')} />
      )}
      {num('route_max_queue_length_m') !== null && (
        <Row label="Max queue on route" value={`${num('route_max_queue_length_m')} m`} />
      )}
      {num('network_max_queue_length_m') !== null && (
        <Row label="Max queue (network)" value={`${num('network_max_queue_length_m')} m`} />
      )}
      {num('recovery_time_s') !== null && (
        <Row label="Recovery after clear" value={`${num('recovery_time_s')} s`} />
      )}

      <div className="mt-2 border-t border-slate-700/60 pt-2 text-[10px] leading-snug text-amber-300/90">
        SIMULATED_SCENARIO — a hypothetical incident declared by this project. Not
        a record of any real incident at Silk Board.
      </div>
      <div className="mt-1 text-[10px] leading-snug text-slate-400">
        The blockage closes a lane. No vehicle was moved, stopped or rerouted by
        it; every queue is SUMO's own.
      </div>
    </div>
  )
}
