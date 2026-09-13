/**
 * The measured counterfactual result, shown exactly as the research recorded it.
 *
 * This panel is where the visualisation and the numbers are forced to agree. It
 * reads the paired comparison embedded at export time — it does not recompute
 * anything from what is on screen, so the scene cannot drift away from the
 * result it is illustrating.
 *
 * **A zero is printed as a zero.** If a policy saved 0.0 s, the panel says
 * "0.0 s" in neutral type, with no green, no arrow and no "improvement" framing.
 * Colour is used only to distinguish a saving from a loss, and neither is
 * emphasised: a visualisation that made a null result look like a win would be
 * the most damaging thing this project could ship.
 */

import type { Manifest } from '../scene/lib/types'

function Metric({
  label,
  baseline,
  policy,
  unit = 's',
  digits = 1,
}: {
  label: string
  baseline: number | undefined
  policy: number | undefined
  unit?: string
  digits?: number
}) {
  if (baseline === undefined || policy === undefined) return null
  return (
    <div className="flex items-baseline justify-between gap-2 py-[3px]">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className="font-mono text-[12px] text-slate-100">
        <span className="text-slate-400">{baseline.toFixed(digits)}</span>
        <span className="mx-1 text-slate-600">→</span>
        {policy.toFixed(digits)}
        <span className="ml-0.5 text-slate-500">{unit}</span>
      </span>
    </div>
  )
}

export function ResultsPanel({ manifest }: { manifest: Manifest }) {
  const c = manifest.comparison

  if (!c) {
    return (
      <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Counterfactual result
        </div>
        <div className="text-[11px] leading-snug text-slate-400">
          No paired comparison was exported for this run, so no saving is shown.
          An absent measurement is not a zero.
        </div>
      </div>
    )
  }

  if (c.is_baseline) {
    return (
      <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Counterfactual result
        </div>
        <div className="text-[11px] leading-snug text-slate-400">{c.note}</div>
      </div>
    )
  }

  const saved = c.time_saved_s ?? 0
  const isZero = Math.abs(saved) < 0.05
  const tone = isZero ? 'text-slate-200' : saved > 0 ? 'text-emerald-300' : 'text-rose-300'

  return (
    <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Counterfactual result
        </span>
        <span className="font-mono text-[10px] text-slate-500">
          {c.policy} vs {c.baseline_policy}
        </span>
      </div>

      <div className="mb-2 rounded border border-slate-700/60 bg-slate-950/50 px-2 py-2 text-center">
        <div className="text-[10px] uppercase tracking-widest text-slate-400">Delay saved</div>
        <div className={`font-mono text-[22px] font-bold leading-tight ${tone}`}>
          {saved.toFixed(1)} s
        </div>
        {c.improvement_percent !== undefined && (
          <div className="font-mono text-[11px] text-slate-400">
            {c.improvement_percent.toFixed(2)}% of baseline travel time
          </div>
        )}
        {isZero && (
          <div className="mt-1 text-[10px] leading-snug text-slate-400">
            This policy changed the ambulance's travel time by nothing measurable
            in this run. Shown as zero, not as a small benefit.
          </div>
        )}
      </div>

      <Metric label="Travel time" baseline={c.baseline_travel_time_s} policy={c.policy_travel_time_s} />
      <Metric label="Waiting time" baseline={c.baseline_waiting_s} policy={c.policy_waiting_s} />
      <Metric label="Stops" baseline={c.baseline_stops} policy={c.policy_stops} unit="" digits={0} />

      {c.traffic_delta_total_time_loss_s !== undefined && (
        <div className="mt-2 border-t border-slate-700/60 pt-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] uppercase tracking-wide text-slate-400">
              Traffic Δ time loss
            </span>
            <span className="font-mono text-[12px] text-slate-300">
              {c.traffic_delta_total_time_loss_s > 0 ? '+' : ''}
              {c.traffic_delta_total_time_loss_s.toFixed(0)} s
            </span>
          </div>
          <div className="mt-1 rounded border border-amber-600/40 bg-amber-950/20 p-1.5 text-[10px] leading-snug text-amber-200/90">
            <span className="font-bold">DIAGNOSTIC ONLY.</span> Not a cost or benefit
            of signal priority. Two controls reproduced changes of this size with no
            ambulance in the network, and across seeds the term has no stable sign.
          </div>
        </div>
      )}

      <div className="mt-2 text-[10px] leading-snug text-slate-500">
        Measured by the paired run, not recomputed from the scene. Source:{' '}
        <span className="font-mono">{c.source}</span>
      </div>
    </div>
  )
}
