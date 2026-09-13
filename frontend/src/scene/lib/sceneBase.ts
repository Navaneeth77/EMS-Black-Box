/**
 * Which exported scene the app loads.
 *
 * HISTORICAL_DEMO is the default. The earlier DEMO export stays available at
 * `?scene=demo`. The paired NORMAL run for side-by-side comparison always lives
 * in the `compare/` subdirectory of the primary scene, so the two can never be
 * mixed across scenarios.
 */

export type SceneKey = 'historical' | 'demo'

export function sceneKeyFrom(search: string): SceneKey {
  return new URLSearchParams(search).get('scene') === 'demo' ? 'demo' : 'historical'
}

export function scenePaths(key: SceneKey): { primary: string; paired: string } {
  const primary = key === 'demo' ? '/scene' : '/scene/historical'
  return { primary, paired: `${primary}/compare` }
}
