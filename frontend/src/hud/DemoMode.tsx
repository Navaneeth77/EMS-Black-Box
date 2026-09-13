/**
 * The "Research Demo" walkthrough.
 *
 * A guided reading of one recorded run, not a separate animation. Every chapter
 * is anchored to a **simulation time taken from the run's own record** — the
 * incident's declared window, the ambulance's departure, the recorded halt, the
 * policy's first transition — so pressing through the chapters seeks the shared
 * clock and the scene shows whatever SUMO produced at that moment.
 *
 * Chapters whose evidence is absent from this run are shown struck through and
 * are not seekable. That matters more than it looks: on the current scenario the
 * disturbance forms real queues but does **not** measurably delay this ambulance,
 * and a demo that narrated "ambulance delayed by congestion" over that would be
 * describing something that did not happen.
 */

import { useMemo } from 'react'
import type { Manifest } from '../scene/lib/types'
import { seek, setState } from '../scene/lib/store'

export interface Chapter {
  key: string
  title: string
  detail: string
  time: number | null
  available: boolean
}

export function buildChapters(manifest: Manifest): Chapter[] {
  const amb = manifest.ambulance
  const incidentWrapper = manifest.incident
  const incident = incidentWrapper?.incident ?? null
  const transitions = manifest.policy_transitions ?? []
  const halt = (amb.signal_wait_events ?? [])[0] as
    | { sim_time_s: number; stopped_by_signal?: boolean }
    | undefined
  const [beginS] = manifest.scenario.window_s

  const firstRequest = transitions.find((t) => t.new_state === 'REQUESTED')
  const firstActive = transitions.find((t) => t.new_state === 'PRIORITY_ACTIVE')
  const lastClear = [...transitions].reverse().find((t) => t.new_state === 'NORMAL')
  const arrival = amb.travel_time_s !== null ? amb.depart_time_s + amb.travel_time_s : null

  return [
    {
      key: 'normal',
      title: 'Normal traffic',
      detail: 'Recorded SUMO traffic before anything is disturbed.',
      time: beginS,
      available: true,
    },
    {
      key: 'incident',
      title: 'Disturbance begins',
      detail: incident
        ? `Lane ${incident.lane_id} closes. Capacity halves on the signal approach.`
        : 'No disturbance in this run.',
      time: incident ? incident.start_time_s : null,
      available: Boolean(incident),
    },
    {
      key: 'queue',
      title: 'Queues form',
      detail: incident
        ? 'Vehicles merge and the approach saturates. Every queued vehicle got there by driving.'
        : 'No disturbance in this run.',
      time: incident ? incident.start_time_s + 25 : null,
      available: Boolean(incident),
    },
    {
      key: 'approach',
      title: 'Ambulance departs',
      detail: 'The ambulance enters the network as an ordinary SUMO vehicle.',
      time: amb.depart_time_s,
      available: true,
    },
    {
      key: 'delay',
      title: 'Baseline delay',
      detail: halt
        ? halt.stopped_by_signal
          ? 'Held at a red on its own movement — the delay a priority policy could recover.'
          : 'Halted beside a signal that was green for it: blocked by traffic, not the signal.'
        : 'No halt was recorded for the ambulance in this run.',
      time: halt ? halt.sim_time_s : null,
      available: Boolean(halt),
    },
    {
      key: 'request',
      title: 'Priority requested',
      detail: firstRequest
        ? `${firstRequest.reason}`
        : 'This is the NORMAL baseline: no policy is acting, so nothing is requested.',
      time: firstRequest ? firstRequest.sim_time_s : null,
      available: Boolean(firstRequest),
    },
    {
      key: 'active',
      title: 'Priority active',
      detail: firstActive
        ? 'A phase serving the ambulance is green. Reached by ending a green after its minimum — never by truncating a yellow.'
        : 'Priority was never granted in this run.',
      time: firstActive ? firstActive.sim_time_s : null,
      available: Boolean(firstActive),
    },
    {
      key: 'progress',
      title: 'Ambulance progresses',
      detail: 'It moves because the signal changed, not because it was given speed.',
      time: firstActive ? firstActive.sim_time_s + 8 : null,
      available: Boolean(firstActive),
    },
    {
      key: 'clear',
      title: 'Priority clears',
      detail: lastClear
        ? 'The signal returns to its own program through its designed interphases.'
        : 'No priority to clear in this run.',
      time: lastClear ? lastClear.sim_time_s : null,
      available: Boolean(lastClear),
    },
    {
      key: 'recovery',
      title: 'Arrival and recovery',
      detail:
        arrival !== null
          ? `Ambulance completes at t=${arrival.toFixed(1)} s (${amb.travel_time_s?.toFixed(1)} s trip).`
          : 'Ambulance did not complete.',
      time: arrival,
      available: arrival !== null,
    },
  ]
}

export function DemoPanel({ manifest, time }: { manifest: Manifest; time: number }) {
  const chapters = useMemo(() => buildChapters(manifest), [manifest])
  const activeIndex = useMemo(() => {
    let index = 0
    chapters.forEach((c, i) => {
      if (c.available && c.time !== null && time >= c.time) index = i
    })
    return index
  }, [chapters, time])

  return (
    <div className="pointer-events-auto w-[330px] rounded-lg border border-slate-700/60 bg-slate-900/90 p-3 shadow-xl backdrop-blur">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-300">
          Research demo
        </span>
        <button
          className="rounded border border-slate-600 px-2 py-0.5 text-[10px] hover:bg-slate-700/60"
          onClick={() => setState({ demoMode: false })}
        >
          Exit
        </button>
      </div>
      <ol className="space-y-1">
        {chapters.map((chapter, i) => {
          const isActive = i === activeIndex && chapter.available
          return (
            <li key={chapter.key}>
              <button
                disabled={!chapter.available || chapter.time === null}
                onClick={() => {
                  if (chapter.time !== null) {
                    seek(chapter.time)
                    setState({ playing: false })
                  }
                }}
                className={`w-full rounded px-2 py-1 text-left text-[11px] leading-snug transition ${
                  !chapter.available
                    ? 'cursor-not-allowed text-slate-600 line-through'
                    : isActive
                      ? 'bg-sky-500/15 text-sky-100 ring-1 ring-sky-500/50'
                      : 'text-slate-300 hover:bg-slate-700/50'
                }`}
              >
                <span className="mr-1.5 font-mono text-[10px] text-slate-500">{i + 1}.</span>
                <span className="font-semibold">{chapter.title}</span>
                {chapter.time !== null && chapter.available && (
                  <span className="ml-1.5 font-mono text-[10px] text-slate-500">
                    t={chapter.time.toFixed(0)}s
                  </span>
                )}
                <div className="mt-0.5 text-[10px] text-slate-400">{chapter.detail}</div>
              </button>
            </li>
          )
        })}
      </ol>
      <div className="mt-2 border-t border-slate-700/60 pt-2 text-[10px] leading-snug text-slate-400">
        Struck-through chapters did not occur in this run. The demo narrates what
        the recording contains, never a scripted sequence.
      </div>
    </div>
  )
}
