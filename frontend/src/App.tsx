import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchHealth } from './api/client'
import type { ComponentStatus, HealthResponse } from './types/health'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ok'; health: HealthResponse }
  | { kind: 'error'; message: string }

const STATUS_STYLE: Record<ComponentStatus, string> = {
  ready: 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10',
  not_configured: 'text-slate-400 border-slate-600/50 bg-slate-500/10',
  unavailable: 'text-amber-400 border-amber-500/40 bg-amber-500/10',
}

function StatusPill({ status }: { status: ComponentStatus }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${STATUS_STYLE[status]}`}
    >
      {status.replace('_', ' ')}
    </span>
  )
}

function BackendPanel({ state, onRetry }: { state: LoadState; onRetry: () => void }) {
  if (state.kind === 'loading') {
    return <p className="text-slate-400">Checking backend…</p>
  }

  if (state.kind === 'error') {
    return (
      <div className="space-y-3">
        <p className="text-amber-400">Backend not reachable</p>
        <p className="text-sm text-slate-400">{state.message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-slate-600 px-3 py-1 text-sm text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
        >
          Retry
        </button>
      </div>
    )
  }

  const { health } = state

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-emerald-400">Backend reachable</span>
        <span className="text-sm text-slate-500">
          {health.service} v{health.version} · {health.environment}
        </span>
      </div>

      <dl className="space-y-2">
        {Object.entries(health.components).map(([name, component]) => (
          <div key={name} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <dt className="w-24 shrink-0 font-mono text-sm text-slate-300">{name}</dt>
            <dd className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <StatusPill status={component.status} />
              <span className="text-sm text-slate-400">{component.detail}</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  const load = useCallback(() => {
    const controller = new AbortController()
    setState({ kind: 'loading' })

    fetchHealth(controller.signal)
      .then((health) => setState({ kind: 'ok', health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({
          kind: 'error',
          message: error instanceof ApiError ? error.message : String(error),
        })
      })

    return () => controller.abort()
  }, [])

  useEffect(() => load(), [load])

  const simulationReady = state.kind === 'ok' && state.health.simulation_ready

  return (
    <main className="min-h-full bg-slate-950 px-6 py-12 text-slate-200">
      <div className="mx-auto max-w-3xl space-y-10">
        <header className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight text-white">EMS Black Box</h1>
          <p className="text-slate-400">
            Counterfactual replay of ambulance trips through simulated Bengaluru traffic.
            Study area: Central Silk Board Junction.
          </p>
        </header>

        <section className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-5">
          <h2 className="font-medium text-emerald-400">Frontend is running</h2>
          <p className="mt-1 text-sm text-slate-400">
            React + TypeScript + Vite. Three.js and React Three Fiber are installed but the
            3D scene is not built yet.
          </p>
        </section>

        <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="mb-4 font-medium text-white">Backend</h2>
          <BackendPanel state={state} onRetry={load} />
        </section>

        {/*
          The empty state below is load-bearing, not a placeholder to delete.

          This page will eventually show travel times and recovered seconds. Until
          a simulation has actually run there is nothing to show, and the honest
          thing to render is "no data" — not a sample chart, not a mocked figure,
          not a preview built from plausible numbers. A mock rendered in the same
          UI as a measurement gets read as a measurement, and screenshots outlive
          the caveats that accompanied them.

          See docs/DATA_INTEGRITY.md.
        */}
        <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="mb-2 font-medium text-white">Simulation</h2>
          {simulationReady ? (
            <p className="text-sm text-slate-400">
              A SUMO scenario is available. Run controls are not implemented yet.
            </p>
          ) : (
            <p className="text-sm text-slate-400">
              No simulation data. No map has been ingested and no scenario has been built,
              so there are no results to display.
            </p>
          )}
        </section>

        <footer className="border-t border-slate-800 pt-6 text-sm text-slate-500">
          <p>
            SUMO is the source of truth for traffic and vehicle movement. This interface
            renders simulation state; it does not compute vehicle motion.
          </p>
        </footer>
      </div>
    </main>
  )
}
