/**
 * EMS Black Box — 3D digital twin.
 *
 * Renders committed SUMO runs. It does not simulate, and it holds no motion
 * model: every vehicle position, heading and signal state on screen came out of
 * SUMO and was transformed once, by a documented transform, on the way here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Scene } from './scene/Scene'
import { CompareView } from './CompareView'
import { Hud } from './hud/Hud'
import { PresentationHud, type CameraKey } from './hud/PresentationHud'
import { ResearchDrawer } from './hud/ResearchDrawer'
import { STOPS_AT_ARRIVAL, useSceneData, type SceneData } from './scene/hooks/useSceneData'
import type { CameraPreset } from './scene/components/CameraRig'
import {
  getState,
  nearestFrameIndex,
  seek,
  setState,
  sharedWindow,
  subscribe,
} from './scene/lib/store'
import { useStore } from './scene/hooks/useStore'
import { sceneKeyFrom, scenePaths } from './scene/lib/sceneBase'
import { priorityEpisodes, type PolicyTransition } from './scene/lib/priority'

const OVERVIEW: CameraPreset = { position: [520, 430, 520], target: [0, 0, 0] }
const CAMERA_KEYS = new Set<string>(['overview', 'chase', 'priority', 'intersection'])

/** `?cam=px,py,pz,tx,ty,tz` fixes the opening camera, for inspection and screenshots. */
function cameraFromSearch(search: string): CameraPreset | null {
  const raw = new URLSearchParams(search).get('cam')
  if (!raw) return null
  const v = raw.split(',').map(Number)
  if (v.length !== 6 || v.some((n) => !Number.isFinite(n))) return null
  return { position: [v[0], v[1], v[2]], target: [v[3], v[4], v[5]] }
}

const arrivalOf = (data: SceneData | null) => data?.manifest.ambulance.arrived_at_s ?? null

export default function App() {
  const compareMode = useStore((st) => st.compareMode)
  const time = useStore((st) => st.time)
  const [researchOpen, setResearchOpen] = useState(false)
  const [expertHud, setExpertHud] = useState(false)
  const paths = useMemo(() => scenePaths(sceneKeyFrom(window.location.search)), [])
  const fixedCamera = useMemo(() => cameraFromSearch(window.location.search), [])
  const { data, error, progress } = useSceneData(paths.primary, true)

  // The paired NORMAL run is only fetched once the comparison is asked for.
  const [pairedRequested, setPairedRequested] = useState(false)
  const paired = useSceneData(pairedRequested ? paths.paired : null, false)
  useEffect(() => {
    if (compareMode) setPairedRequested(true)
  }, [compareMode])

  const [preset, setPreset] = useState<CameraPreset | null>(null)
  const [camera, setCamera] = useState<CameraKey | null>('overview')
  const [autoCamera, setAutoCamera] = useState(fixedCamera === null)
  const autoRef = useRef(autoCamera)
  autoRef.current = autoCamera

  // Development-only handle on the store, used by the browser walkthrough to
  // seek and inspect. Not present in a production build.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    ;(window as unknown as { __ems?: unknown }).__ems = { getState, setState, seek }
  }, [])

  /**
   * One clock. In a comparison it spans both runs' windows and stops at the later
   * arrival; each pane freezes at its own. Back in the single view it returns to
   * the primary run's window and arrival.
   */
  useEffect(() => {
    if (!data) return
    const [beginS, endS] = data.manifest.scenario.window_s
    const stops = STOPS_AT_ARRIVAL.has(data.manifest.mode ?? '')
    if (compareMode && paired.data) {
      const [pairedBegin, pairedEnd] = paired.data.manifest.scenario.window_s
      const shared = sharedWindow(
        { beginS, endS, arrivalS: arrivalOf(data) },
        { beginS: pairedBegin, endS: pairedEnd, arrivalS: arrivalOf(paired.data) },
      )
      setState({ beginS: shared.beginS, endS: shared.endS, stopAtS: stops ? shared.stopAtS : null })
    } else {
      const now = getState().time
      setState({
        beginS,
        endS,
        stopAtS: stops ? arrivalOf(data) : null,
        time: Math.min(Math.max(now, beginS), endS),
      })
    }
  }, [data, paired.data, compareMode])

  /**
   * Camera presets derived from the scene's own geometry and recorded state.
   * The intersection view centres on the four-way controller's own stop lines;
   * the chase view is resolved from the recording at the current time.
   */
  const goTo = useCallback(
    (key: string) => {
      if (!data) return
      const { manifest } = data
      const attributed = manifest.route_traffic_lights.find((t) => t.has_red_exposure)
      const tls = data.signals.traffic_lights.find((t) => t.id === attributed?.tls_id)
      const at = tls?.links[0]?.position ?? [0, 0, 0]

      if (key === 'intersection' || key === 'junction') {
        const four = manifest.intersection
        const c = four?.centre ?? at
        // Look across the four-way from behind the ambulance's own approach, so the
        // heads facing it — the ones priority acts on — are in view.
        const own = manifest.route_traffic_lights.find((t) => t.tls_id === four?.tls_id)
        const links = (data.signals.traffic_lights.find((t) => t.id === four?.tls_id)?.links ?? []).filter(
          (l) => own?.ambulance_link_indices.includes(l.index),
        )
        // The exporter computes this shot from the controller's own stop-line
        // geometry, so the camera, the scene and the validator agree on it.
        if (four?.camera) {
          setPreset({ position: four.camera.position, target: four.camera.target })
          setState({ followVehicle: null })
          return
        }
        if (four && links.length > 0) {
          const sx = links.reduce((s, l) => s + l.position[0], 0) / links.length
          const sz = links.reduce((s, l) => s + l.position[2], 0) / links.length
          const h = (links[0].heading * Math.PI) / 180
          const travel = [Math.sin(h), -Math.cos(h)]
          const right = [Math.cos(h), Math.sin(h)]
          // Same framing the exporter writes into the manifest, kept here only for
          // scenes exported before it carried one: far enough back down the approach
          // to hold the queue and the junction in one frame, and off to the side so
          // the shot looks along the corridor rather than down onto it.
          setPreset({
            position: [
              sx - travel[0] * 130 + right[0] * 38,
              c[1] + 46,
              sz - travel[1] * 130 + right[1] * 38,
            ],
            target: [sx - travel[0] * 30, c[1] + 2, sz - travel[1] * 30],
          })
        } else {
          setPreset({ position: [c[0] + 78, c[1] + 64, c[2] + 78], target: [c[0], c[1] + 2, c[2]] })
        }
        setState({ followVehicle: null })
        return
      }

      if (key === 'chase' || key === 'ambulance') {
        const { trajectories } = data
        const vehicle = trajectories.vehicle_ids.indexOf(manifest.ambulance.vehicle_id)
        if (vehicle < 0) return
        let now = getState().time
        if (now < trajectories.vehicle_first_seen_s[vehicle] || now > trajectories.vehicle_last_seen_s[vehicle]) {
          now = trajectories.vehicle_first_seen_s[vehicle]
          seek(now)
        }
        const frame = trajectories.frames[nearestFrameIndex(trajectories.times, now)]
        const k = frame.id.indexOf(vehicle)
        if (k < 0) return
        // Behind the ambulance along its own heading, so the shot leads down the road.
        const a = (frame.a[k] * Math.PI) / 180
        const back = 34
        setPreset({
          position: [frame.x[k] - Math.sin(a) * back, frame.y[k] + 16, frame.z[k] + Math.cos(a) * back],
          target: [frame.x[k], frame.y[k] + 1.5, frame.z[k]],
        })
        setState({ selectedVehicle: vehicle, followVehicle: vehicle })
        return
      }

      // Priority: jump to the first moment priority was actually granted — at the
      // four-way if it was granted there — and frame that signal. If the run never
      // granted priority there is nothing to show, and nothing is faked.
      if (key === 'priority') {
        const transitions = (manifest.policy_transitions ?? []) as PolicyTransition[]
        const four = manifest.intersection?.tls_id
        const granted =
          transitions.find((t) => t.new_state === 'PRIORITY_ACTIVE' && t.tls_id === four) ??
          transitions.find((t) => t.new_state === 'PRIORITY_ACTIVE') ??
          transitions.find((t) => t.new_state === 'REQUESTED')
        if (!granted) {
          setPreset({ position: [at[0] + 90, at[1] + 70, at[2] + 90], target: [at[0], at[1], at[2]] })
          return
        }
        seek(Math.max(getState().beginS, granted.sim_time_s - 6))
        const target =
          granted.tls_id === four && manifest.intersection
            ? manifest.intersection.centre
            : (data.signals.traffic_lights.find((t) => t.id === granted.tls_id)?.links[0]?.position ?? at)
        setPreset({
          position: [target[0] + 52, target[1] + 34, target[2] + 52],
          target: [target[0], target[1] + 2, target[2]],
        })
        setState({ followVehicle: null })
        return
      }

      setPreset(OVERVIEW)
      setState({ followVehicle: null })
    },
    [data],
  )

  useEffect(() => {
    if (data && preset === null) setPreset(fixedCamera ?? OVERVIEW)
  }, [data, preset, fixedCamera])

  const chooseCamera = useCallback(
    (key: string) => {
      setAutoCamera(false)
      setCamera(CAMERA_KEYS.has(key) ? (key as CameraKey) : null)
      goTo(key)
    },
    [goTo],
  )

  /**
   * Auto-director (DEMO and HISTORICAL_DEMO): chase the ambulance once it is in the
   * network, cut to the four-way when the policy's recorded request there begins,
   * and back to the chase once that signal has returned to normal. Any camera the
   * viewer picks switches it off; AUTO switches it back on.
   */
  const cuts = useRef({ chase: false, intersection: false, after: false })
  const enableAuto = useCallback(() => {
    cuts.current = { chase: false, intersection: false, after: false }
    setAutoCamera(true)
  }, [])

  useEffect(() => {
    if (!data || !STOPS_AT_ARRIVAL.has(data.manifest.mode ?? '')) return
    const depart = data.manifest.ambulance.depart_time_s
    const transitions = (data.manifest.policy_transitions ?? []) as PolicyTransition[]
    const four = data.manifest.intersection?.tls_id
    // The first priority episode at the four-way (see lib/priority.ts): from the
    // recorded request to the recorded release, merging the policy's re-entries
    // while the ambulance crosses junction interiors.
    const episode = four ? priorityEpisodes(transitions).find((e) => e.tlsId === four) : undefined
    const request = episode ? { sim_time_s: episode.start } : undefined
    const release = episode && episode.end !== null ? { sim_time_s: episode.end } : undefined
    const cut = (key: CameraKey) => {
      setCamera(key)
      goTo(key)
    }
    return subscribe(() => {
      if (!autoRef.current) return
      const now = getState().time
      if (now < depart) {
        cuts.current = { chase: false, intersection: false, after: false }
        return
      }
      const c = cuts.current
      const beforeRequest = !request || now < request.sim_time_s
      if (!c.chase && now >= depart + 1 && beforeRequest) {
        c.chase = true
        cut('chase')
      }
      if (request && !c.intersection && now >= request.sim_time_s && (!release || now < release.sim_time_s)) {
        c.intersection = true
        cut('intersection')
      }
      if (release && !c.after && now >= release.sim_time_s + 2) {
        c.after = true
        cut('chase')
      }
    })
  }, [data, goTo])

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 p-8 text-slate-200">
        <div className="max-w-lg rounded-lg border border-rose-700/50 bg-rose-950/30 p-5">
          <div className="mb-2 font-semibold text-rose-300">Scene data not available</div>
          <p className="text-sm text-slate-300">{error}</p>
          <pre className="mt-3 overflow-x-auto rounded bg-slate-900 p-3 text-[12px] text-slate-300">
            python scripts/export_scene.py --historical --policy EMS_ROLLING
          </pre>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-slate-950 text-slate-300">
        <div className="text-sm">{progress}</div>
        <div className="h-1 w-56 overflow-hidden rounded bg-slate-800">
          <div className="h-full w-1/3 animate-pulse rounded bg-sky-500" />
        </div>
      </div>
    )
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950">
      {compareMode ? (
        <CompareView
          primary={data}
          paired={paired.data}
          pairedError={paired.error}
          pairedProgress={paired.progress}
          preset={preset}
        />
      ) : (
        <Scene data={data} preset={preset} />
      )}
      {expertHud ? (
        <Hud data={data} onCamera={chooseCamera} />
      ) : (
        <PresentationHud
          data={data}
          compareMode={compareMode}
          camera={camera}
          autoCamera={autoCamera}
          onCamera={chooseCamera}
          onAutoCamera={enableAuto}
          onOpenResearch={() => setResearchOpen(true)}
        />
      )}
      <ResearchDrawer
        data={data}
        open={researchOpen}
        onClose={() => setResearchOpen(false)}
        time={time}
      />
      <button
        className="pointer-events-auto absolute right-4 top-4 z-10 rounded-lg border border-slate-700/60 bg-slate-900/70 px-2 py-1 text-[10px] text-slate-400 backdrop-blur hover:bg-slate-800/80"
        onClick={() => setExpertHud((v) => !v)}
        title="Toggle the full engineering HUD"
      >
        {expertHud ? 'Presentation view' : 'Expert view'}
      </button>
    </div>
  )
}
