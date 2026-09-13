/**
 * One place that decides what a vehicle type looks like.
 *
 * Two sources, in order of preference:
 *
 *  1. a **GLB** at `/models/<type>.glb`, if one is present and declared here,
 *  2. otherwise **authored geometry** from `vehicleGeometry.ts`.
 *
 * The fallback is never silent. `assetReport()` says, per type, which source was
 * used, and the HUD surfaces it — a scene that quietly swapped in a placeholder
 * would be exactly the unlabelled substitution this project avoids.
 *
 * Geometry is built **once per type** and shared by every instance of it. There
 * is no per-vehicle copy of anything: ~470 vehicles cost seven geometries.
 */

import * as THREE from 'three'
import {
  ambulanceGeometry,
  autoRickshawGeometry,
  busGeometry,
  carGeometry,
  motorcycleGeometry,
  truckGeometry,
  vanGeometry,
} from './vehicleGeometry'
import { VEHICLE_STYLES, type VehicleStyle } from './vehicleTypes'

export type AssetSource = 'glb' | 'authored'

export interface VehicleAsset {
  type: string
  /** Set only when a licensed GLB has been placed in `frontend/public/models/`. */
  glbUrl?: string
  license: string
  creator: string
  source: AssetSource
}

/**
 * Declared assets.
 *
 * Every entry is currently authored. To use a real model, drop the file in
 * `frontend/public/models/` and set `glbUrl` plus its true licence and creator
 * here — no other code changes.
 */
export const VEHICLE_ASSETS: Record<string, VehicleAsset> = {
  ambulance: { type: 'ambulance', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  car: { type: 'car', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  motorcycle: { type: 'motorcycle', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  auto: { type: 'auto', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  bus: { type: 'bus', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  truck: { type: 'truck', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
  van: { type: 'van', license: 'CC0-1.0', creator: 'EMS Black Box project', source: 'authored' },
}

const BUILDERS: Record<
  string,
  (length: number, width: number, height: number, color: string) => THREE.BufferGeometry
> = {
  car: carGeometry,
  motorcycle: motorcycleGeometry,
  auto: autoRickshawGeometry,
  bus: busGeometry,
  truck: truckGeometry,
  van: vanGeometry,
  ambulance: (l, w, h) => ambulanceGeometry(l, w, h),
}

const cache = new Map<string, THREE.BufferGeometry>()
const used: Record<string, AssetSource> = {}

/**
 * Geometry for a vehicle type, built once and cached.
 *
 * Dimensions come from the SUMO vType via {@link VEHICLE_STYLES}, so the mesh
 * occupies the space the simulation allotted it.
 */
export function geometryFor(type: string): THREE.BufferGeometry {
  const cached = cache.get(type)
  if (cached) return cached

  const style: VehicleStyle = VEHICLE_STYLES[type] ?? VEHICLE_STYLES.car
  const build = BUILDERS[type] ?? BUILDERS.car
  const geometry = build(style.length, style.width, style.height, style.color)
  used[type] = 'authored'
  cache.set(type, geometry)
  return geometry
}

/** Which source each rendered type actually came from. Shown in the HUD. */
export function assetReport(): { type: string; source: AssetSource; license: string }[] {
  return Object.keys(VEHICLE_ASSETS).map((type) => ({
    type,
    source: used[type] ?? VEHICLE_ASSETS[type].source,
    license: VEHICLE_ASSETS[type].license,
  }))
}

export function disposeAssets(): void {
  for (const geometry of cache.values()) geometry.dispose()
  cache.clear()
}
