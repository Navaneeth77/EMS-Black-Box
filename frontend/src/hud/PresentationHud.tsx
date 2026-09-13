/**
 * The default interface: compact, and about the response rather than the run.
 *
 * Provenance tables, scenario metadata and raw statistics live behind the
 * "Research details" drawer. What stays on screen is what a viewer needs to
 * follow the replay: the ambulance's state, whether priority is active and where,
 * the historical snapshot the demand is anchored to, and the playback controls.
 *
 * Everything shown is read from recorded data. The EMS state is the policy's own
 * recorded transition; "cross traffic stopped" is checked against the recorded
 * applied signal state at that instant; result figures come from the paired
 * comparison computed at export; and the snapshot text is built by the exporter
 * from the observed-counts and provenance files.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { SceneData } from '../scene/hooks/useSceneData'
import { useStore } from '../scene/hooks/useStore'
import {
  SPEED_OPTIONS,
  getState,
  nearestFrameIndex,
  seek,
  setPlayback,
  setState,
} from '../scene/lib/store'
import {
  displayStatesAt,
  priorityEpisodes,
  priorityStatus,
  type PolicyTransition,
  type PriorityStatus,
} from '../scene/lib/priority'
import type { EvidenceLabel, HistoricalSnapshot } from '../scene/lib/types'

export type CameraKey = 'overview' | 'chase' | 'priority' | 'intersection'

const STATE_LABEL: Record<string, string> = {
  NORMAL: 'Monitoring',
  REQUESTED: 'Priority requested',
  PRIORITY_ACTIVE: 'Priority active',
  CLEARING: 'Clearing',
}
const STATE_TONE: Record<string, string> = {
  NORMAL: 'text-slate-300 border-slate-600/60',
  REQUESTED: 'text-amber-200 border-amber-500/60 bg-amber-500/10',
  PRIORITY_ACTIVE: 'text-emerald-200 border-emerald-500/60 bg-emerald-500/10',
  CLEARING: 'text-sky-200 border-sky-500/60 bg-sky-500/10',
}

const LABEL_TONE: Record<EvidenceLabel, string> = {
  OBSERVED: 'border-sky-500/50 bg-sky-500/10 text-sky-200',
  DERIVED: 'border-teal-500/50 bg-teal-500/10 text-teal-200',
  ESTIMATED: 'border-amber-500/50 bg-amber-500/10 text-amber-200',
  SIMULATED: 'border-violet-500/50 bg-violet-500/10 text-violet-200',
  'NOT REPORTED': 'border-slate-500/50 bg-slate-500/10 text-slate-300',
}

const CAMERAS: [CameraKey, string][] = [
  ['overview', 'Overview'],
  ['chase', 'Chase'],
  ['priority', 'Priority'],
  ['intersection', 'Intersection'],
]

function clock(t: number): string {
  const m = Math.floor(t / 60)
  return `${String(m).padStart(2, '0')}:${Math.floor(t % 60)
    .toString()
    .padStart(2, '0')}`
}

function shortId(id: string): string {
  return id.length > 22 ? `${id.slice(0, 18)}…` : id
}

export function PresentationHud({
  data,
  compareMode,
  camera,
  autoCamera,
  onCamera,
  onAutoCamera,
  onOpenResearch,
}: {
  data: SceneData
  compareMode: boolean
  camera: CameraKey | null
  autoCamera: boolean
  onCamera: (key: CameraKey) => void
  onAutoCamera: () => void
  onOpenResearch: () => void
}) {
  const time = useStore((s) => s.time)
  const playing = useStore((s) => s.playing)
  const speed = useStore((s) => s.speed)
  const beginS = useStore((s) => s.beginS)
  const endS = useStore((s) => s.endS)

  const { manifest, trajectories } = data
  const amb = manifest.ambulance
  const isHistorical = manifest.mode === 'HISTORICAL_DEMO'
  const transitions = (manifest.policy_transitions ?? []) as PolicyTransition[]
  // The EMS run's own time: in a comparison the shared clock runs past this
  // run's arrival, but its recorded state ends there.
  const runTime = Math.min(time, amb.arrived_at_s ?? Infinity)

  const episodes = useMemo(() => priorityEpisodes(transitions), [transitions])

  const ems = useMemo(() => {
    const active = displayStatesAt(manifest, episodes, runTime)
    let best = 'NORMAL'
    for (const entry of active.values()) {
      if (entry.state === 'PRIORITY_ACTIVE') best = 'PRIORITY_ACTIVE'
      else if (best !== 'PRIORITY_ACTIVE') best = 'REQUESTED'
    }
    return { state: best, count: active.size }
  }, [manifest, episodes, runTime])

  const status = useMemo(
    () => priorityStatus(manifest, runTime, episodes),
    [manifest, runTime, episodes],
  )

  /** Live ambulance state at the nearest recorded frame. */
  const live = useMemo(() => {
    const index = trajectories.vehicle_ids.indexOf(amb.vehicle_id)
    const arrivedAt = amb.arrived_at_s ?? null
    const arrived = arrivedAt !== null && time >= arrivedAt
    const elapsed = Math.max(0, runTime - amb.depart_time_s)
    if (index < 0) return { present: false, arrived, speed: 0, elapsed }
    const frame = trajectories.frames[nearestFrameIndex(trajectories.times, time)]
    const k = frame.id.indexOf(index)
    return { present: k >= 0, arrived, speed: k >= 0 ? frame.s[k] : 0, elapsed }
  }, [trajectories, time, runTime, amb])

  const queue = manifest.ambulance.queue_joined ?? null
  const request = (manifest.ambulance.priority_requests ?? []).find(
    (r) => r.tls_id === status?.tlsId,
  )
  const comparison = manifest.comparison
  const arrived = live.arrived

  return (
    <div className="pointer-events-none absolute inset-0 select-none">
      {/* ---------------------------------------------------- top-left card */}
      {!compareMode && (
        <div className="pointer-events-auto absolute left-4 top-4 max-h-[calc(100%-9rem)] w-[262px] overflow-y-auto rounded-xl border border-slate-700/60 bg-slate-900/80 shadow-2xl backdrop-blur-md">
          <div className="flex items-center justify-between border-b border-slate-700/50 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-200">
              EMS Response
            </span>
            <span className="font-mono text-[11px] text-slate-400">{clock(time)}</span>
          </div>
          <div className="px-3 py-2.5">
            <div
              className={`mb-2.5 rounded-lg border px-2.5 py-1.5 text-center text-[12px] font-semibold tracking-wide ${STATE_TONE[ems.state]}`}
            >
              {arrived ? 'Arrived' : STATE_LABEL[ems.state]}
              {ems.count > 0 && !arrived && (
                <span className="ml-1.5 font-mono text-[10px] opacity-70">
                  {ems.count} signal{ems.count > 1 ? 's' : ''}
                </span>
              )}
            </div>
            <Line label="Elapsed" value={`${live.elapsed.toFixed(1)} s`} />
            <Line label="Speed" value={live.present ? `${(live.speed * 3.6).toFixed(0)} km/h` : '—'} />
            <Line label="Run waiting" value={`${amb.waiting_time_s?.toFixed(1) ?? '—'} s`} />
            <Line label="Run stops" value={String(amb.stops ?? '—')} />
            {queue && time >= queue.sim_time_s && (
              <Line
                label="Caught behind"
                value={`${queue.vehicles_ahead} vehicles · ${queue.distance_to_stop_line_m.toFixed(0)} m back`}
              />
            )}
            <Line label="Policy" value={manifest.scenario.policy} />
            {comparison && !comparison.is_baseline && comparison.time_saved_s !== undefined && (
              <div className="mt-2 rounded-lg border border-slate-700/60 bg-slate-950/50 px-2.5 py-2 text-center">
                <div className="text-[9px] uppercase tracking-[0.16em] text-slate-400">
                  Time saved vs NORMAL
                </div>
                <div
                  className={`font-mono text-[19px] font-bold leading-tight ${
                    Math.abs(comparison.time_saved_s) < 0.05 ? 'text-slate-200' : 'text-emerald-300'
                  }`}
                >
                  {comparison.time_saved_s.toFixed(1)} s
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <PriorityBanner
        status={status}
        intersectionName={manifest.intersection?.name ?? null}
        compareMode={compareMode}
        requestedAtM={request?.distance_to_stop_line_m ?? null}
      />

      {manifest.historical && <SnapshotCard snapshot={manifest.historical} compact={compareMode} />}

      {/* ------------------------------------------------- arrival result card */}
      {!compareMode && arrived && (
        <div className="pointer-events-auto absolute left-1/2 top-1/2 z-30 w-[340px] max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-emerald-600/50 bg-slate-900/95 p-5 text-center shadow-2xl backdrop-blur-md">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-emerald-300">
            EMS response complete
          </div>
          <div className="mb-3 text-[10px] text-slate-400">
            Ambulance reached its destination — simulation stopped
          </div>
          <div className="space-y-1 text-left">
            <Line label="Travel time" value={`${amb.travel_time_s?.toFixed(1)} s`} big />
            <Line label="Waiting" value={`${amb.waiting_time_s?.toFixed(1)} s`} big />
            <Line label="Stops" value={String(amb.stops ?? '—')} big />
            {queue && (
              <Line label="Caught behind" value={`${queue.vehicles_ahead} vehicles`} big />
            )}
            {comparison && !comparison.is_baseline && comparison.time_saved_s !== undefined && (
              <Line label="Time saved" value={`${comparison.time_saved_s.toFixed(1)} s`} big />
            )}
            <Line label="EMS policy" value={manifest.scenario.policy} big />
          </div>
          <div className="mt-3 text-[9.5px] leading-snug text-slate-500">
            {isHistorical
              ? 'SUMO replay generated from historical observed demand. Travel times are simulated, not a recorded ambulance journey, and not the research result.'
              : 'Simulated demo scenario — not the research result, and not a measurement of real Bengaluru traffic.'}
          </div>
          <div className="mt-3 flex gap-2">
            <button
              className="flex-1 rounded-lg border border-slate-600 py-1.5 text-[11px] text-slate-200 hover:bg-slate-700/60"
              onClick={() => {
                seek(getState().beginS)
                setState({ playing: true })
              }}
            >
              Replay
            </button>
            <button
              className="flex-1 rounded-lg border border-emerald-600/60 bg-emerald-500/10 py-1.5 text-[11px] text-emerald-200 hover:bg-emerald-500/20"
              onClick={() => {
                setState({ compareMode: true })
                seek(getState().beginS)
                setState({ playing: true })
              }}
            >
              Compare with NORMAL
            </button>
          </div>
        </div>
      )}

      {/* ------------------------------------------------------- bottom bar */}
      <div className="pointer-events-auto absolute bottom-5 left-1/2 w-[min(820px,calc(100%-2rem))] -translate-x-1/2 rounded-xl border border-slate-700/60 bg-slate-900/90 px-3 py-2 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-2">
          <button
            className="rounded-md border border-slate-600 px-2 py-0.5 text-[12px] text-slate-200 hover:bg-slate-700/60"
            onClick={() => {
              seek(beginS)
              setState({ playing: true })
            }}
            title="Replay from the start"
          >
            ⟲
          </button>
          <input
            type="range"
            min={beginS}
            max={endS}
            step={0.1}
            value={time}
            onChange={(e) => seek(Number(e.target.value))}
            className="h-1 min-w-0 flex-1 accent-sky-400"
            aria-label="Simulation time"
          />
          <span className="w-[44px] text-right font-mono text-[11px] text-slate-300">{clock(time)}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <Segmented label="Playback">
            <Segment active={!playing} onClick={() => setPlayback('pause')}>
              PAUSE
            </Segment>
            {SPEED_OPTIONS.map((value) => (
              <Segment key={value} active={playing && speed === value} onClick={() => setPlayback(value)}>
                {value}×
              </Segment>
            ))}
          </Segmented>
          <Segmented label="View">
            <Segment active={!compareMode} onClick={() => setState({ compareMode: false })}>
              SINGLE
            </Segment>
            <Segment active={compareMode} onClick={() => setState({ compareMode: true })}>
              COMPARE
            </Segment>
          </Segmented>
          <Segmented label="Camera">
            {CAMERAS.map(([key, label]) => (
              <Segment key={key} active={!autoCamera && camera === key} onClick={() => onCamera(key)}>
                {label}
              </Segment>
            ))}
            <Segment
              active={autoCamera}
              onClick={onAutoCamera}
              title="Automatic camera: follows the ambulance and cuts to the intersection when priority is recorded there. Choosing any camera turns it off."
            >
              AUTO
            </Segment>
          </Segmented>
          <button
            className="ml-auto shrink-0 whitespace-nowrap rounded-md border border-slate-600/70 px-2 py-1 text-[10.5px] text-slate-300 hover:bg-slate-700/60"
            onClick={onOpenResearch}
            title="Provenance, scenario metadata, attribution and raw metrics"
          >
            Research details
          </button>
        </div>
      </div>

      {/* Data notice. Hidden while the result card is up, which carries it itself. */}
      <div
        hidden={arrived && !compareMode}
        className="pointer-events-none absolute bottom-[7.2rem] left-4 max-w-[250px] text-[9px] leading-tight text-slate-400/80"
      >
        {isHistorical
          ? 'Vehicles and signal states are simulated by SUMO from historical observed demand. They are not GPS traces or signal logs.'
          : 'Simulated scene — SUMO under estimated demand and generated signal timings. Not a measurement of real Bengaluru traffic.'}
      </div>
    </div>
  )
}

function PriorityBanner({
  status,
  intersectionName,
  compareMode,
  requestedAtM,
}: {
  status: PriorityStatus | null
  intersectionName: string | null
  compareMode: boolean
  /** Distance to the stop line when the policy asked, from the recorded transition. */
  requestedAtM: number | null
}) {
  if (!status || (status.state !== 'PRIORITY_ACTIVE' && status.state !== 'REQUESTED')) return null
  // In a comparison the pane labels sit at the top, so the banner drops below them.
  const top = compareMode ? 'top-[4.8rem]' : 'top-4'
  const where = status.isIntersection
    ? `${intersectionName ?? 'Four-way intersection'}${
        status.arm ? ` · ambulance on the ${status.arm} arm${status.road ? ` (${status.road})` : ''}` : ''
      }`
    : status.road
      ? `signal on ${status.road}`
      : `signal ${shortId(status.tlsId)}`

  if (status.state === 'REQUESTED') {
    return (
      <div className={`pointer-events-none absolute left-1/2 ${top} z-20 max-w-[calc(100%-2rem)] -translate-x-1/2 rounded-xl border border-amber-500/60 bg-slate-950/85 px-4 py-2 text-center shadow-2xl backdrop-blur-md`}>
        <div className="text-[12px] font-bold tracking-[0.14em] text-amber-300">EMS PRIORITY REQUESTED</div>
        <div className="mt-0.5 text-[10px] text-slate-400">
          {where} — signal moving to the ambulance&apos;s phase through yellow and all-red
          {requestedAtM !== null && ` · asked ${Math.round(requestedAtM)} m before the stop line`}
        </div>
      </div>
    )
  }

  const stopped = status.ambulanceGreen && status.crossRed
  return (
    <div className={`pointer-events-none absolute left-1/2 ${top} z-20 max-w-[calc(100%-2rem)] -translate-x-1/2 rounded-xl border border-emerald-500/60 bg-slate-950/85 px-4 py-2 text-center shadow-2xl backdrop-blur-md`}>
      <div className="flex flex-wrap items-center justify-center gap-x-2 text-[12.5px] font-bold tracking-[0.12em]">
        <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
        <span className="text-emerald-300">EMS PRIORITY ACTIVE</span>
        <span className="text-slate-500">·</span>
        <span className="text-emerald-200">GREEN CORRIDOR</span>
        {stopped && (
          <>
            <span className="text-slate-500">·</span>
            <span className="text-rose-300">Cross traffic STOPPED</span>
          </>
        )}
      </div>
      <div className="mt-0.5 text-[10px] text-slate-400">
        {where} — recorded SUMO signal state
        {requestedAtM !== null && ` · asked ${Math.round(requestedAtM)} m before the stop line`}
      </div>
    </div>
  )
}

function SnapshotCard({ snapshot, compact }: { snapshot: HistoricalSnapshot; compact: boolean }) {
  // Collapsed while comparing, where it would cover most of the EMS pane.
  const [open, setOpen] = useState(!compact)
  useEffect(() => setOpen(!compact), [compact])
  return (
    <div className="pointer-events-auto absolute right-4 top-14 z-10 w-[300px] max-w-[calc(100%-2rem)] rounded-xl border border-sky-700/40 bg-slate-900/88 shadow-2xl backdrop-blur-md">
      <button
        className="flex w-full items-center justify-between border-b border-slate-700/50 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-sky-200">
          {snapshot.title}
        </span>
        <span className="text-[12px] text-slate-400">{open ? '–' : '+'}</span>
      </button>
      {open && (
        <div className="max-h-[calc(100vh-17rem)] space-y-1.5 overflow-y-auto px-3 py-2 text-[10.5px] leading-snug text-slate-200">
          <Field name="Location">{snapshot.location}</Field>
          <Field name="Survey date">{snapshot.survey_date}</Field>
          <Field name="Period">{snapshot.survey_period}</Field>
          <Field name="Observed traffic">
            {snapshot.observed.map((item) => (
              <Evidence key={item.text} {...item} />
            ))}
          </Field>
          <Field name="Composition">
            {snapshot.composition.map((item) => (
              <Evidence key={item.text} {...item} />
            ))}
          </Field>
          <Field name="Signal">
            <Evidence {...snapshot.signal} />
          </Field>
          <Field name="SUMO demand">
            <Evidence label={snapshot.conversion.label} text={snapshot.conversion.text} />
          </Field>
          <Field name="Source">
            <a
              className="text-sky-300 underline decoration-sky-700/70 hover:text-sky-200"
              href={snapshot.source.url}
              target="_blank"
              rel="noreferrer"
            >
              {snapshot.source.text}
            </a>
            <a
              className="block text-sky-300/80 underline decoration-sky-700/60 hover:text-sky-200"
              href={snapshot.source.secondary_url}
              target="_blank"
              rel="noreferrer"
            >
              {snapshot.source.secondary_text}
            </a>
          </Field>
          <div className="rounded-lg border border-violet-600/40 bg-violet-500/10 px-2 py-1.5">
            <div className="text-[10.5px] font-semibold text-violet-100">{snapshot.replay_statement}</div>
            <div className="mt-1 flex items-start gap-1.5 text-[9.5px] text-violet-200/85">
              <Chip label="SIMULATED" />
              <span>{snapshot.not_gps}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[8.5px] uppercase tracking-[0.14em] text-slate-500">{name}</div>
      <div className="mt-0.5 space-y-0.5">{children}</div>
    </div>
  )
}

function Evidence({ label, text }: { label: EvidenceLabel; text: string }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span>{text}</span>
      <Chip label={label} />
    </div>
  )
}

function Chip({ label }: { label: EvidenceLabel }) {
  return (
    <span
      className={`shrink-0 rounded border px-1 py-px text-[8px] font-semibold tracking-wide ${
        LABEL_TONE[label] ?? LABEL_TONE['NOT REPORTED']
      }`}
    >
      {label}
    </span>
  )
}

function Segmented({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex shrink-0 items-center gap-1" role="group" aria-label={label}>
      <span className="mr-0.5 text-[8.5px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
      <div className="flex overflow-hidden rounded-md border border-slate-600/80">{children}</div>
    </div>
  )
}

function Segment({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
  title?: string
}) {
  return (
    <button
      className={`border-r border-slate-600/60 px-2 py-1 text-[10.5px] font-medium last:border-r-0 ${
        active ? 'bg-sky-500/20 text-sky-100' : 'text-slate-300 hover:bg-slate-700/60'
      }`}
      aria-pressed={active}
      onClick={onClick}
      title={title}
    >
      {children}
    </button>
  )
}

function Line({ label, value, big = false }: { label: string; value: string; big?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[2px]">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={`font-mono text-slate-100 ${big ? 'text-[13px]' : 'text-[12px]'}`}>{value}</span>
    </div>
  )
}
