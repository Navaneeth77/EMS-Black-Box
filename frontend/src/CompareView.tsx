/**
 * Side-by-side counterfactual, on one clock.
 *
 * Left: NORMAL. Right: EMS PRIORITY. Two exported runs of the **same** scenario —
 * same network, demand, seed, vehicle mix, ambulance trip and route, and the same
 * disturbance (none, in HISTORICAL_DEMO) — differing only in the signal policy.
 *
 * Both panes read the shared simulation clock and only one advances it, so they
 * cannot drift apart. Each pane freezes on its own ambulance's recorded arrival;
 * the clock runs on until the later of the two, then stops.
 *
 * The comparison figures are the two runs' recorded travel times and the saving
 * computed by the exporter from those records. Nothing is recomputed here.
 */

import { Scene } from './scene/Scene'
import type { SceneData } from './scene/hooks/useSceneData'
import { useStore } from './scene/hooks/useStore'
import type { CameraPreset } from './scene/components/CameraRig'

function runStatus(data: SceneData, time: number): { text: string; arrived: boolean } {
  const amb = data.manifest.ambulance
  const arrival = amb.arrived_at_s ?? null
  if (arrival !== null && time >= arrival) {
    return { text: `ARRIVED · ${amb.travel_time_s?.toFixed(1) ?? '—'} s`, arrived: true }
  }
  if (time < amb.depart_time_s) return { text: 'awaiting dispatch', arrived: false }
  return { text: `en route · ${(time - amb.depart_time_s).toFixed(0)} s elapsed`, arrived: false }
}

/** The queue this run recorded the ambulance joining, once the replay reaches it. */
function queueNote(data: SceneData, time: number): string | null {
  const queue = data.manifest.ambulance.queue_joined
  if (!queue || time < queue.sim_time_s) return null
  return `caught behind ${queue.vehicles_ahead} vehicles`
}

function Pane({
  data,
  title,
  subtitle,
  tone,
  driveClock,
  preset,
  error,
  progress,
}: {
  data: SceneData | null
  title: string
  subtitle: string
  tone: string
  driveClock: boolean
  preset: CameraPreset | null
  error: string | null
  progress: string
}) {
  const time = useStore((s) => s.time)
  const status = data ? runStatus(data, time) : null
  const queue = data ? queueNote(data, time) : null

  return (
    <div className="relative h-full min-w-0 flex-1 border-slate-700/70 [&:not(:last-child)]:border-r">
      <div
        className={`pointer-events-none absolute left-2 top-2 z-10 rounded-lg border bg-slate-950/85 px-2.5 py-1.5 backdrop-blur ${tone}`}
      >
        <div className="text-[11.5px] font-bold tracking-[0.14em]">{title}</div>
        <div className="font-mono text-[9.5px] text-slate-400">{subtitle}</div>
        {status && (
          <div
            className={`mt-0.5 font-mono text-[10.5px] ${status.arrived ? 'text-emerald-300' : 'text-slate-200'}`}
          >
            {status.text}
          </div>
        )}
        {queue && <div className="font-mono text-[9.5px] text-amber-200/90">{queue}</div>}
      </div>
      {error && (
        <div className="flex h-full items-center justify-center p-6 text-center">
          <div className="max-w-sm rounded border border-amber-700/50 bg-amber-950/25 p-4 text-[12px] text-amber-100">
            <div className="mb-1 font-semibold">Comparison run not exported</div>
            <p className="text-slate-300">{error}</p>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-2 text-[11px] text-slate-300">
              python scripts/export_scene.py --historical --policy NORMAL --out compare
            </pre>
          </div>
        </div>
      )}
      {!error && !data && (
        <div className="flex h-full items-center justify-center text-[12px] text-slate-400">
          {progress}
        </div>
      )}
      {data && (
        <Scene
          data={data}
          preset={preset}
          driveClock={driveClock}
          freezeAtS={data.manifest.ambulance.arrived_at_s ?? null}
        />
      )}
    </div>
  )
}

function Cell({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[0.16em] text-slate-400">{label}</div>
      <div className={`font-mono text-[17px] font-bold leading-tight ${tone}`}>{value}</div>
    </div>
  )
}

/** NORMAL, EMS PRIORITY and SAVED, each shown once its recorded run has arrived. */
export function ComparisonPanel({ primary, paired }: { primary: SceneData; paired: SceneData | null }) {
  const time = useStore((s) => s.time)
  const ems = primary.manifest.ambulance
  const normal = paired?.manifest.ambulance ?? null
  const comparison = primary.manifest.comparison
  const emsArrived = ems.arrived_at_s != null && time >= ems.arrived_at_s
  const normalArrived = normal?.arrived_at_s != null && time >= normal.arrived_at_s
  // The saving was computed at export against a specific NORMAL record. If the
  // loaded NORMAL run is not that record, the figure is withheld, not guessed.
  const consistent =
    paired !== null &&
    comparison?.baseline_travel_time_s !== undefined &&
    comparison.baseline_travel_time_s === normal?.travel_time_s &&
    paired.manifest.demand_config_hash === primary.manifest.demand_config_hash
  const saved = comparison?.time_saved_s

  return (
    <div className="pointer-events-none absolute bottom-[6.8rem] left-1/2 z-20 -translate-x-1/2 rounded-xl border border-slate-600/60 bg-slate-950/90 px-5 py-2 shadow-2xl backdrop-blur-md">
      <div className="grid grid-cols-3 gap-6 text-center">
        <Cell
          label="NORMAL"
          value={normalArrived ? `${normal?.travel_time_s?.toFixed(1)} s` : 'en route'}
          tone="text-slate-100"
        />
        <Cell
          label="EMS PRIORITY"
          value={emsArrived ? `${ems.travel_time_s?.toFixed(1)} s` : 'en route'}
          tone="text-emerald-200"
        />
        <Cell
          label="SAVED"
          value={
            consistent && emsArrived && normalArrived && saved !== undefined
              ? `${saved.toFixed(1)} s`
              : '—'
          }
          tone="text-emerald-300"
        />
      </div>
      <div className="mt-1 text-center text-[9px] text-slate-500">
        {paired === null
          ? 'Loading the paired NORMAL run…'
          : consistent
            ? 'Ambulance travel times from the two recorded SUMO runs · same demand, seed and trip'
            : 'Paired records do not match — saving withheld'}
      </div>
    </div>
  )
}

export function CompareView({
  primary,
  paired,
  pairedError,
  pairedProgress,
  preset,
}: {
  primary: SceneData
  paired: SceneData | null
  pairedError: string | null
  pairedProgress: string
  preset: CameraPreset | null
}) {
  return (
    <div className="absolute inset-0 flex flex-col">
      <div className="flex min-h-0 flex-1">
        <Pane
          data={paired}
          title="NORMAL"
          subtitle="no signal priority · same scenario"
          tone="border-slate-500/60 text-slate-100"
          driveClock={false}
          preset={preset}
          error={pairedError}
          progress={pairedProgress}
        />
        <Pane
          data={primary}
          title="EMS PRIORITY"
          subtitle={`${primary.manifest.scenario.policy} · same scenario`}
          tone="border-emerald-500/60 text-emerald-200"
          driveClock
          preset={preset}
          error={null}
          progress=""
        />
      </div>
      <ComparisonPanel primary={primary} paired={paired} />
    </div>
  )
}
