/**
 * The research HUD.
 *
 * Its job is not decoration. This project's central discipline is that a
 * measured number and an assumed one must never look alike, and a 3D scene is
 * the easiest place in the whole system to lose that distinction — everything
 * rendered at the same fidelity reads as equally real. So every panel that shows
 * a value shows its data class next to it, in the project's own vocabulary.
 */

import { useMemo } from 'react'
import type { SceneData } from '../scene/hooks/useSceneData'
import { useStore } from '../scene/hooks/useStore'
import { getState, seek, setState, stepFrames, nearestFrameIndex } from '../scene/lib/store'
import { styleFor } from '../scene/lib/vehicleTypes'
import { isReplayable, phaseAt } from '../scene/components/TrafficSignals'
import type { DataClass } from '../scene/lib/types'
import { DisturbancePanel } from './DisturbancePanel'
import { EmsStatusPanel, type StateTransition } from './EmsStatusPanel'
import { DemoPanel } from './DemoMode'
import { ResultsPanel } from './ResultsPanel'
import { AttributionPanel } from './AttributionPanel'
import { assetReport } from '../scene/lib/assetRegistry'

const CLASS_STYLE: Record<DataClass, string> = {
  VERIFIED_REAL_DATA: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  PUBLICLY_SOURCED_DATA: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  ESTIMATED_DATA: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  SIMULATED_DATA: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
}

const CLASS_SHORT: Record<DataClass, string> = {
  VERIFIED_REAL_DATA: 'VERIFIED',
  PUBLICLY_SOURCED_DATA: 'SOURCED',
  ESTIMATED_DATA: 'ESTIMATED',
  SIMULATED_DATA: 'SIMULATED',
}

function Badge({ kind }: { kind: DataClass }) {
  return (
    <span
      className={`inline-block rounded border px-1.5 py-px text-[10px] font-semibold tracking-wide ${CLASS_STYLE[kind]}`}
      title={kind}
    >
      {CLASS_SHORT[kind]}
    </span>
  )
}

function Row({ label, value, kind }: { label: string; value: React.ReactNode; kind?: DataClass }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className="flex items-center gap-1.5 font-mono text-[12px] text-slate-100">
        {value}
        {kind && <Badge kind={kind} />}
      </span>
    </div>
  )
}

function Panel({
  title,
  children,
  className = '',
}: {
  title: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={`pointer-events-auto rounded-lg border border-slate-700/60 bg-slate-900/85 p-3 shadow-xl backdrop-blur ${className}`}
    >
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-slate-300">
        {title}
      </div>
      {children}
    </div>
  )
}

function formatClock(t: number): string {
  const m = Math.floor(t / 60)
  const s = t % 60
  return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`
}

export function Hud({ data, onCamera }: { data: SceneData; onCamera: (p: string) => void }) {
  const time = useStore((s) => s.time)
  const playing = useStore((s) => s.playing)
  const speed = useStore((s) => s.speed)
  const beginS = useStore((s) => s.beginS)
  const endS = useStore((s) => s.endS)
  const selectedVehicle = useStore((s) => s.selectedVehicle)
  const selectedSignal = useStore((s) => s.selectedSignal)
  const followVehicle = useStore((s) => s.followVehicle)
  const layerFilter = useStore((s) => s.layerFilter)
  const showBuildings = useStore((s) => s.showBuildings)
  const showSignals = useStore((s) => s.showSignals)
  const showDelayMarkers = useStore((s) => s.showDelayMarkers)
  const compareMode = useStore((s) => s.compareMode)
  const demoMode = useStore((s) => s.demoMode)

  const { manifest, trajectories, signals } = data
  const scenario = manifest.scenario

  /** State of the selected vehicle at the *nearest recorded frame*. */
  const vehicleInfo = useMemo(() => {
    if (selectedVehicle === null) return null
    const i = nearestFrameIndex(trajectories.times, time)
    const frame = trajectories.frames[i]
    const k = frame.id.indexOf(selectedVehicle)
    const id = trajectories.vehicle_ids[selectedVehicle]
    const typeName = trajectories.vehicle_types[trajectories.vehicle_type_index[selectedVehicle]]
    if (k < 0) {
      return { id, typeName, present: false, sampleTime: trajectories.times[i] }
    }
    const lane = trajectories.lane_ids[frame.l[k]]
    return {
      id,
      typeName,
      present: true,
      sampleTime: trajectories.times[i],
      speed: frame.s[k],
      heading: frame.a[k],
      lane,
      edge: lane.startsWith(':') ? lane : lane.replace(/_\d+$/, ''),
      laneIndex: lane.replace(/^.*_/, ''),
      elevation: frame.y[k],
    }
  }, [selectedVehicle, time, trajectories])

  const signalInfo = useMemo(() => {
    if (!selectedSignal) return null
    const tls = signals.traffic_lights.find((t) => t.id === selectedSignal)
    if (!tls) return null
    const program = tls.programs[0]
    const phase = program ? phaseAt(program.phases, time, program.offset ?? 0) : null
    const replayable = isReplayable(program)
    const onRoute = manifest.route_traffic_lights.find((t) => t.tls_id === tls.id)
    return { tls, phase, onRoute, replayable }
  }, [selectedSignal, time, signals, manifest])

  const ambulance = manifest.ambulance
  const inTrip =
    time >= ambulance.depart_time_s &&
    time <= ambulance.depart_time_s + (ambulance.travel_time_s ?? 0)

  return (
    <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-3 text-slate-200">
      {/* ---------------------------------------------------------- top row */}
      <div className="flex items-start justify-between gap-3">
        <Panel title="Scenario" className="w-[290px]">
          <Row label="Study area" value={scenario.area} kind="PUBLICLY_SOURCED_DATA" />
          <Row label="Trip" value={scenario.trip} kind="ESTIMATED_DATA" />
          <Row label="Policy" value={scenario.policy} kind="SIMULATED_DATA" />
          <Row label="Seed" value={scenario.seed} />
          <Row label="Variant" value={scenario.variant} />
          <Row label="SUMO" value={data.manifest.sumo_version} />
          <div className="mt-2 border-t border-slate-700/60 pt-2 text-[10px] leading-snug text-amber-300/90">
            Simulated scene. Not a measurement of real traffic or of any real
            ambulance journey in Bengaluru.
          </div>
        </Panel>

        {demoMode ? (
          <DemoPanel manifest={manifest} time={time} />
        ) : (
        <Panel title="Provenance" className="w-[330px]">
          <div className="space-y-1 text-[11px] leading-snug">
            <div className="flex gap-2">
              <Badge kind="PUBLICLY_SOURCED_DATA" />
              <span className="text-slate-300">
                Road geometry, building footprints — OpenStreetMap
              </span>
            </div>
            <div className="flex gap-2">
              <Badge kind="ESTIMATED_DATA" />
              <span className="text-slate-300">
                Road elevation, building heights, kerbs, signal programs
              </span>
            </div>
            <div className="flex gap-2">
              <Badge kind="SIMULATED_DATA" />
              <span className="text-slate-300">
                Vehicle positions, speeds, signal states — SUMO
              </span>
            </div>
            <div className="flex gap-2 opacity-45">
              <Badge kind="VERIFIED_REAL_DATA" />
              <span className="text-slate-400">Nothing in this project carries this class</span>
            </div>
            <div className="mt-1.5 border-t border-slate-700/60 pt-1.5 text-[10px] leading-snug text-slate-400">
              Vehicle models: {assetReport().filter((a) => a.source === 'authored').length} of{' '}
              {assetReport().length} authored by this project (CC0). No suitable
              licensed GLB set covering these types was obtainable — see
              <span className="font-mono"> data/provenance/3d_assets.json</span>. Nothing
              falls back to a cube silently.
            </div>
          </div>
        </Panel>
        )}
      </div>

      {/* ------------------------------------------------------ middle right */}
      <div className="flex items-start justify-end gap-3">
        <div className="flex w-[330px] flex-col gap-3">
          <ResultsPanel manifest={manifest} />
          <AttributionPanel manifest={manifest} />
          <EmsStatusPanel
            manifest={manifest}
            transitions={(manifest.policy_transitions ?? []) as StateTransition[]}
            time={time}
          />
          <DisturbancePanel manifest={manifest} time={time} />
          {vehicleInfo && (
            <Panel title={vehicleInfo.id === ambulance.vehicle_id ? 'Ambulance' : 'Selected vehicle'}>
              <Row label="ID" value={vehicleInfo.id} kind="SIMULATED_DATA" />
              <Row label="Type" value={styleFor(vehicleInfo.typeName).label} />
              {vehicleInfo.present ? (
                <>
                  <Row
                    label="Speed"
                    value={`${vehicleInfo.speed!.toFixed(2)} m/s · ${(vehicleInfo.speed! * 3.6).toFixed(1)} km/h`}
                    kind="SIMULATED_DATA"
                  />
                  <Row label="Heading" value={`${vehicleInfo.heading!.toFixed(1)}°`} />
                  <Row label="Edge" value={vehicleInfo.edge!} />
                  <Row label="Lane" value={vehicleInfo.lane!} />
                  <Row
                    label="Elevation"
                    value={`${vehicleInfo.elevation!.toFixed(1)} m`}
                    kind="ESTIMATED_DATA"
                  />
                </>
              ) : (
                <div className="py-1 text-[11px] text-slate-400">
                  Not in the network at this simulation time.
                </div>
              )}
              <Row label="Sample time" value={`${vehicleInfo.sampleTime.toFixed(1)} s`} />
              {vehicleInfo.id === ambulance.vehicle_id && (
                <div className="mt-2 space-y-1 border-t border-slate-700/60 pt-2">
                  <Row
                    label="Emergency status"
                    value={inTrip ? 'ON CALL — en route' : 'not yet departed / arrived'}
                  />
                  <Row
                    label="Trip travel time"
                    value={`${ambulance.travel_time_s?.toFixed(1)} s`}
                    kind="SIMULATED_DATA"
                  />
                  <Row label="Waiting time" value={`${ambulance.waiting_time_s?.toFixed(1)} s`} />
                  <Row label="Route edges" value={ambulance.route_edges.length} />
                  <div className="pt-1 text-[10px] leading-snug text-slate-400">
                    No siren, no speed bonus, no right-of-way override. Under{' '}
                    {scenario.policy} it is an ordinary SUMO vehicle that is observed,
                    never driven.
                  </div>
                  <div className="pt-1 text-[10px] leading-snug text-amber-300/80">
                    The flashing beacon is a rendering cue only — it runs on wall-clock
                    time and has no counterpart in the simulation, which models no siren.
                  </div>
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  className="pointer-events-auto rounded border border-slate-600 px-2 py-1 text-[11px] hover:bg-slate-700/60"
                  onClick={() =>
                    setState({
                      followVehicle: followVehicle === selectedVehicle ? null : selectedVehicle,
                    })
                  }
                >
                  {followVehicle === selectedVehicle ? 'Stop following' : 'Follow'}
                </button>
              </div>
            </Panel>
          )}

          {signalInfo && (
            <Panel title="Selected signal">
              <Row label="TLS ID" value={<span className="break-all">{signalInfo.tls.id}</span>} />
              <Row label="Controlled links" value={signalInfo.tls.link_count} />
              {signalInfo.phase && (
                <>
                  <Row label="Phase" value={`${signalInfo.phase.index}`} kind="SIMULATED_DATA" />
                  <Row label="Cycle" value={`${signalInfo.phase.cycle.toFixed(0)} s`} kind="ESTIMATED_DATA" />
                  <div className="mt-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">State</div>
                    <div className="mt-1 break-all font-mono text-[12px]">
                      {signalInfo.phase.state.split('').map((c, i) => (
                        <span
                          key={i}
                          className={
                            'gG'.includes(c)
                              ? 'text-emerald-400'
                              : 'yY'.includes(c)
                                ? 'text-amber-400'
                                : 'rR'.includes(c)
                                  ? 'text-rose-400'
                                  : 'text-slate-500'
                          }
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                </>
              )}
              {signalInfo.onRoute && (
                <div className="mt-2 border-t border-slate-700/60 pt-2">
                  <Row label="On ambulance route" value="yes" />
                  <Row
                    label="Green fraction"
                    value={signalInfo.onRoute.green_fraction_for_ambulance.toFixed(3)}
                    kind="ESTIMATED_DATA"
                  />
                  <Row
                    label="Actionable"
                    value={signalInfo.onRoute.has_red_exposure ? 'yes' : 'no — always green'}
                  />
                </div>
              )}
              <div className="mt-2 text-[10px] leading-snug text-amber-300/80">
                Signal programs are netconvert-generated, not observed Bengaluru timings.
              </div>
              {!signalInfo.replayable && (
                <div className="mt-1 rounded border border-rose-600/50 bg-rose-950/30 p-1.5 text-[10px] leading-snug text-rose-200">
                  This program is not fixed-time, so its state cannot be reconstructed
                  from its phase list. The scene shows it dark rather than guessing.
                </div>
              )}
            </Panel>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------- bottom bar */}
      <Panel title="Playback" className="w-full">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1">
            <button
              className="rounded border border-slate-600 px-3 py-1 text-[12px] hover:bg-slate-700/60"
              onClick={() => setState({ playing: !playing })}
            >
              {playing ? '❚❚ Pause' : '▶ Play'}
            </button>
            <button
              className="rounded border border-slate-600 px-2 py-1 text-[12px] hover:bg-slate-700/60"
              onClick={() => stepFrames(trajectories.times, -1)}
              title="Step one recorded frame back"
            >
              ⏴
            </button>
            <button
              className="rounded border border-slate-600 px-2 py-1 text-[12px] hover:bg-slate-700/60"
              onClick={() => stepFrames(trajectories.times, 1)}
              title="Step one recorded frame forward"
            >
              ⏵
            </button>
            <button
              className="rounded border border-slate-600 px-2 py-1 text-[12px] hover:bg-slate-700/60"
              onClick={() => {
                seek(getState().beginS)
                setState({ playing: false })
              }}
            >
              ⟲ Reset
            </button>
          </div>

          <div className="flex items-center gap-2">
            <span className="font-mono text-sm text-slate-100">{formatClock(time)}</span>
            <span className="font-mono text-[11px] text-slate-400">t = {time.toFixed(1)} s</span>
          </div>

          <input
            type="range"
            min={beginS}
            max={endS}
            step={0.1}
            value={time}
            onChange={(e) => seek(Number(e.target.value))}
            className="h-1 min-w-[240px] flex-1 accent-sky-400"
          />

          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            Speed
            <select
              className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 text-[12px] text-slate-100"
              value={speed}
              onChange={(e) => setState({ speed: Number(e.target.value) })}
            >
              {[0.25, 0.5, 1, 2, 4, 8].map((v) => (
                <option key={v} value={v}>
                  {v}×
                </option>
              ))}
            </select>
          </label>

          <div className="flex items-center gap-1">
            {(
              [
                ['overview', 'Overview'],
                ['junction', 'Junction'],
                ['ambulance', 'Ambulance'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                className="rounded border border-slate-600 px-2 py-1 text-[11px] hover:bg-slate-700/60"
                onClick={() => onCamera(key)}
              >
                {label}
              </button>
            ))}
            {followVehicle !== null && (
              <button
                className="rounded border border-amber-600/60 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-500/20"
                onClick={() => setState({ followVehicle: null })}
              >
                Free camera
              </button>
            )}
          </div>

          <button
            className={`rounded border px-2 py-1 text-[11px] ${
              compareMode
                ? 'border-sky-500/70 bg-sky-500/15 text-sky-200'
                : 'border-slate-600 hover:bg-slate-700/60'
            }`}
            onClick={() => setState({ compareMode: !compareMode })}
            title="Baseline vs EMS priority, same seed and scenario, one clock"
          >
            {compareMode ? '◫ Comparing' : '◫ Compare'}
          </button>
          <button
            className={`rounded border px-2 py-1 text-[11px] ${
              demoMode
                ? 'border-emerald-500/70 bg-emerald-500/15 text-emerald-200'
                : 'border-slate-600 hover:bg-slate-700/60'
            }`}
            onClick={() => setState({ demoMode: !demoMode })}
          >
            ▶ Demo
          </button>

          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={showBuildings}
                onChange={(e) => setState({ showBuildings: e.target.checked })}
              />
              Buildings
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={showSignals}
                onChange={(e) => setState({ showSignals: e.target.checked })}
              />
              Signals
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={showDelayMarkers}
                onChange={(e) => setState({ showDelayMarkers: e.target.checked })}
              />
              Delay events
            </label>
            <select
              className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 text-[11px] text-slate-100"
              value={layerFilter}
              onChange={(e) => setState({ layerFilter: e.target.value as never })}
            >
              <option value="all">All levels</option>
              <option value="surface">Surface only</option>
              <option value="elevated">Elevated only</option>
            </select>
          </div>

          <div className="ml-auto font-mono text-[11px] text-slate-500">
            {trajectories.counts.vehicles} vehicles · {trajectories.counts.frames} frames @{' '}
            {scenario.fcd_period_s}s
          </div>
        </div>
      </Panel>
    </div>
  )
}
