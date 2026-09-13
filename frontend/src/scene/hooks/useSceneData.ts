/** Loads an exported scene payload once and reports progress. */

import { useEffect, useState } from 'react'
import type {
  BuildingsScene,
  Manifest,
  NetworkScene,
  SignalsScene,
  Trajectories,
} from '../lib/types'
import { setState } from '../lib/store'
import { buildFrameIndex, type FrameIndex } from '../lib/frameIndex'

export interface SceneData {
  manifest: Manifest
  network: NetworkScene
  signals: SignalsScene
  buildings: BuildingsScene
  trajectories: Trajectories
  /** Per-frame vehicle -> slot lookup, built once. See lib/frameIndex.ts. */
  frameIndex: FrameIndex
}

/** Modes whose playback ends on the ambulance's recorded arrival. */
export const STOPS_AT_ARRIVAL = new Set(['DEMO', 'HISTORICAL_DEMO'])

/**
 * `basePath` null loads nothing, so a paired scene is only fetched once asked for.
 * Only a scene with `drivesClock` sets the clock: a comparison pane must read the
 * same clock, never define its own, or the two views would drift apart.
 */
export function useSceneData(basePath: string | null, drivesClock = true) {
  const [data, setData] = useState<SceneData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState('Loading scene…')

  useEffect(() => {
    if (basePath === null) return
    let cancelled = false
    const controller = new AbortController()

    async function load(path: string) {
      try {
        const get = async <T,>(name: string): Promise<T> => {
          const response = await fetch(`${path}/${name}`, { signal: controller.signal })
          if (!response.ok) {
            throw new Error(
              `${path}/${name} not found (${response.status}). ` +
                'Run: python scripts/export_scene.py',
            )
          }
          return (await response.json()) as T
        }

        // The manifest is small and names the scenario, so it is fetched first
        // and shown while the rest load. The other four are independent of each
        // other, so they go in parallel rather than in series.
        setProgress('Reading manifest…')
        const manifest = await get<Manifest>('manifest.json')
        if (cancelled) return

        setProgress('Loading network, buildings and SUMO trajectories…')
        const [network, signals, buildings, trajectories] = await Promise.all([
          get<NetworkScene>('network.json'),
          get<SignalsScene>('signals.json'),
          get<BuildingsScene>('buildings.json'),
          get<Trajectories>('trajectories.json'),
        ])
        if (cancelled) return

        setProgress('Indexing frames…')
        if (drivesClock) {
          const [beginS, endS] = manifest.scenario.window_s
          setState({
            beginS,
            endS,
            time: Math.max(beginS, manifest.ambulance.depart_time_s - 20),
            stopAtS: STOPS_AT_ARRIVAL.has(manifest.mode ?? '')
              ? (manifest.ambulance.arrived_at_s ?? null)
              : null,
            playing: true,
          })
        }
        setData({
          manifest,
          network,
          signals,
          buildings,
          trajectories,
          frameIndex: buildFrameIndex(trajectories),
        })
      } catch (caught) {
        if (cancelled || (caught instanceof DOMException && caught.name === 'AbortError')) return
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    }

    void load(basePath)
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [basePath, drivesClock])

  return { data, error, progress }
}
