/**
 * Everything the presentation HUD moved out of the way.
 *
 * **Nothing was deleted.** Provenance, scenario metadata, the disturbance
 * record, per-signal attribution, the paired comparison and the raw queue
 * statistics all still exist and are all still shown — behind a drawer, so the
 * default view is about the ambulance rather than about the run's bookkeeping.
 *
 * A viewer who wants to check where a number came from can open this and find
 * it; a viewer watching the demo is not made to read it first.
 */

import type { SceneData } from '../scene/hooks/useSceneData'
import { assetReport } from '../scene/lib/assetRegistry'
import { AttributionPanel } from './AttributionPanel'
import { ResultsPanel } from './ResultsPanel'
import { DisturbancePanel } from './DisturbancePanel'
import type { DataClass } from '../scene/lib/types'

const CLASS_STYLE: Record<DataClass, string> = {
  VERIFIED_REAL_DATA: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  PUBLICLY_SOURCED_DATA: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  ESTIMATED_DATA: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  SIMULATED_DATA: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
}

function Badge({ kind, children }: { kind: DataClass; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-0.5">
      <span
        className={`h-fit shrink-0 rounded border px-1.5 py-px text-[9px] font-semibold tracking-wide ${CLASS_STYLE[kind]}`}
      >
        {kind.replace('_DATA', '').replace('_', ' ')}
      </span>
      <span className="text-[10.5px] leading-snug text-slate-300">{children}</span>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[2px]">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className="max-w-[60%] truncate text-right font-mono text-[11px] text-slate-100">
        {value}
      </span>
    </div>
  )
}

export function ResearchDrawer({
  data,
  open,
  onClose,
  time,
}: {
  data: SceneData
  open: boolean
  onClose: () => void
  time: number
}) {
  if (!open) return null
  const { manifest } = data
  const s = manifest.scenario
  const authored = assetReport().filter((a) => a.source === 'authored').length

  return (
    <div className="pointer-events-auto absolute right-0 top-0 z-20 flex h-full w-[360px] flex-col border-l border-slate-700/60 bg-slate-950/95 shadow-2xl backdrop-blur-md">
      <div className="flex items-center justify-between border-b border-slate-700/60 px-3 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-200">
          Research details
        </span>
        <button
          className="rounded border border-slate-600 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-700/60"
          onClick={onClose}
        >
          Close
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        <section>
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
            Scenario
          </h3>
          <Row label="Mode" value={manifest.mode ?? 'RESEARCH'} />
          <Row label="Study area" value={s.area} />
          <Row label="Trip" value={s.trip} />
          <Row label="Policy" value={s.policy} />
          {typeof manifest.ambulance.policy_parameters?.activation_formula === 'string' && (
            <Row
              label="Priority asked when"
              value={manifest.ambulance.policy_parameters.activation_formula}
            />
          )}
          {manifest.ambulance.priority_requests?.map((request) => (
            <Row
              key={request.tls_id}
              label="First request"
              value={
                `${request.tls_id.slice(0, 18)} · t=${request.sim_time_s.toFixed(1)} s · ` +
                `${request.distance_to_stop_line_m?.toFixed(0) ?? '—'} m out at ` +
                `${request.ambulance_speed_ms?.toFixed(1) ?? '—'} m/s · asks from ` +
                `${request.activation_distance_m?.toFixed(0) ?? '—'} m`
              }
            />
          ))}
          <Row label="Seed" value={s.seed} />
          <Row label="Variant" value={s.variant} />
          <Row label="Demand id" value={s.demand_id} />
          <Row label="SUMO" value={manifest.sumo_version} />
          <Row label="Network SHA-256" value={manifest.network_sha256.slice(0, 16) + '…'} />
          {manifest.demo_note && (
            <p className="mt-1.5 rounded border border-amber-600/40 bg-amber-950/20 p-1.5 text-[10px] leading-snug text-amber-200/90">
              {manifest.demo_note}
            </p>
          )}
        </section>

        <section>
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
            Provenance
          </h3>
          <Badge kind="PUBLICLY_SOURCED_DATA">
            Road geometry and building footprints — OpenStreetMap
          </Badge>
          <Badge kind="ESTIMATED_DATA">
            Demand and vehicle mix; signal programs (netconvert-generated, not observed
            Bengaluru timings); road elevation; building heights; kerbs
          </Badge>
          <Badge kind="SIMULATED_DATA">
            Vehicle positions, speeds, headings, signal states, queues — SUMO
          </Badge>
          <div className="mt-1 flex gap-2 py-0.5 opacity-45">
            <span className="h-fit shrink-0 rounded border border-emerald-500/30 bg-emerald-500/15 px-1.5 py-px text-[9px] font-semibold text-emerald-300">
              VERIFIED
            </span>
            <span className="text-[10.5px] leading-snug text-slate-400">
              Nothing in this project carries this class
            </span>
          </div>
          <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
            No real-time or historical traffic feed is used. Sources were investigated and
            none was usable — see{' '}
            <span className="font-mono">data/traffic/provenance/</span>. Vehicle models:{' '}
            {authored} of {assetReport().length} authored by this project (CC0), not
            downloaded GLB assets.
          </p>
        </section>

        <ResultsPanel manifest={manifest} />
        <AttributionPanel manifest={manifest} />
        <DisturbancePanel manifest={manifest} time={time} />
      </div>
    </div>
  )
}
