/**
 * Where the ambulance's recovered time actually came from.
 *
 * Delay is attributed from the ambulance's **own recorded traversal** of each
 * signal's approach — baseline against the paired policy run — not from
 * proximity. A signal on the route that the ambulance sailed through is
 * credited 0.0 s and says so, which is the point: green fraction predicts
 * exposure, not delay, and only one of the two signals on this route ever stops
 * the ambulance.
 *
 * The unattributed residual is shown rather than folded into the total. It is
 * time the ambulance lost or gained away from any signal, and crediting it to a
 * policy would overstate what the policy did.
 */

import type { Manifest } from '../scene/lib/types'

export function AttributionPanel({ manifest }: { manifest: Manifest }) {
  const attribution = manifest.comparison?.attribution
  if (!attribution || attribution.intersections.length === 0) return null

  const ranked = [...attribution.intersections].sort(
    (a, b) => b.delay_reduction_s - a.delay_reduction_s,
  )
  const maxAbs = Math.max(1, ...ranked.map((r) => Math.abs(r.delay_reduction_s)))

  return (
    <div className="pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-slate-300">
        Delay attribution
      </div>

      {ranked.map((entry) => {
        const width = (Math.abs(entry.delay_reduction_s) / maxAbs) * 100
        const zero = Math.abs(entry.delay_reduction_s) < 0.05
        return (
          <div key={entry.tls_id} className="mb-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-mono text-[10px] text-slate-300" title={entry.tls_id}>
                {entry.tls_id.slice(0, 22)}…
              </span>
              <span
                className={`font-mono text-[12px] font-semibold ${
                  zero ? 'text-slate-400' : 'text-emerald-300'
                }`}
              >
                {entry.delay_reduction_s.toFixed(1)} s
              </span>
            </div>
            <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded bg-slate-800">
              <div
                className={`h-full rounded ${zero ? 'bg-slate-600' : 'bg-emerald-500/70'}`}
                style={{ width: `${Math.max(width, zero ? 2 : 6)}%` }}
              />
            </div>
            <div className="mt-0.5 flex justify-between text-[10px] text-slate-500">
              <span>
                green {entry.green_fraction_for_ambulance.toFixed(3)}
                {!entry.actionable && ' · always green'}
              </span>
              <span className="font-mono">
                {entry.baseline_traversal_time_s.toFixed(1)} →{' '}
                {entry.counterfactual_traversal_time_s.toFixed(1)} s
              </span>
            </div>
            {zero && (
              <div className="mt-0.5 text-[10px] leading-snug text-slate-500">
                Passed on green — nothing here for a policy to recover.
              </div>
            )}
          </div>
        )
      })}

      <div className="mt-1 flex justify-between border-t border-slate-700/60 pt-1.5 text-[11px]">
        <span className="text-slate-400">Unattributed</span>
        <span className="font-mono text-slate-300">
          {attribution.unattributed_change_s.toFixed(1)} s
        </span>
      </div>
      <div className="mt-1 text-[10px] leading-snug text-slate-500">
        Change on route edges no traffic light controls. Not credited to the
        policy — it is traffic the ambulance met elsewhere.
      </div>
    </div>
  )
}
