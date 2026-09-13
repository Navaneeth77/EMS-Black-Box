/**
 * The road surface, drawn from SUMO lane shapes.
 *
 * Everything here is static geometry built once and merged, so the whole network
 * costs a handful of draw calls rather than thousands. Lanes are grouped by
 * *structure* — surface, bridge, tunnel, junction interior — because they need
 * different materials and because grade separation has to be visible: a flyover
 * has to read as a structure with depth, standing on piers, rather than a road
 * drawn on top of another one.
 */

import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { Lane, Junction, Kerb } from '../lib/types'
import { buildRibbons, toGeometry, laneMarkingLines, edgeLines, kerbLines } from '../lib/ribbon'

const ASPHALT = '#4a4f57'
const ELEVATED_DECK = '#565c66'
const JUNCTION = '#53585f'
const MARKING = '#e8e4d2'
const KERB = '#8d939c'
const SOFFIT = '#7a8088'
const FASCIA = '#a2a8af'
const PARAPET = '#c0c5cb'
const PIER = '#9aa0a7'
const PIER_CAP = '#8a9097'

/** Structural depth of the deck below its running surface. ESTIMATED, a rendering choice. */
export const DECK_DEPTH_M = 1.1
/** Target spacing between piers along a deck. ESTIMATED, a rendering choice. */
export const PIER_SPACING_M = 26
/** Piers are only placed where the underside clears the ground by at least this. */
export const MIN_PIER_CLEARANCE_M = 2.6

function MergedRibbon({
  lines,
  color,
  yOffset = 0,
  roughness = 0.95,
  polygonOffset = 0,
}: {
  lines: { points: [number, number, number][]; width: number }[]
  color: string
  yOffset?: number
  roughness?: number
  polygonOffset?: number
}) {
  const geometry = useMemo(() => toGeometry(buildRibbons(lines, yOffset)), [lines, yOffset])
  // Large buffers, rebuilt when the layer filter changes; disposed so paired
  // comparison views do not leave old geometry resident on the GPU.
  useEffect(() => () => geometry.dispose(), [geometry])
  if (lines.length === 0) return null
  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial
        color={color}
        roughness={roughness}
        metalness={0}
        side={THREE.DoubleSide}
        polygonOffset={polygonOffset !== 0}
        polygonOffsetFactor={polygonOffset}
        polygonOffsetUnits={polygonOffset}
      />
    </mesh>
  )
}

/** Junction interiors as filled polygons, so intersections read as surfaces. */
function JunctionSurfaces({ junctions }: { junctions: Junction[] }) {
  const geometry = useMemo(() => {
    const positions: number[] = []
    const indices: number[] = []
    let base = 0
    for (const junction of junctions) {
      const ring = junction.shape
      if (ring.length < 3) continue
      const y = ring[0][1]
      for (const [x, , z] of ring) positions.push(x, y + 0.02, z)
      // Fan triangulation. Junction shapes from netconvert are convex enough for
      // this; a concave shape shows a small artefact, never a wrong position.
      for (let i = 1; i < ring.length - 1; i++) indices.push(base, base + i, base + i + 1)
      base += ring.length
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    g.setIndex(indices)
    g.computeVertexNormals()
    g.computeBoundingSphere()
    return g
  }, [junctions])

  useEffect(() => () => geometry.dispose(), [geometry])

  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial color={JUNCTION} roughness={0.95} side={THREE.DoubleSide} />
    </mesh>
  )
}

type Segment = { ax: number; az: number; bx: number; bz: number; half: number }

/** A coarse grid of ground-level carriageway segments, to keep piers off roads. */
export function groundIndex(lanes: Lane[], cell = 16): Map<string, Segment[]> {
  const grid = new Map<string, Segment[]>()
  for (const lane of lanes) {
    if (lane.layer > 0) continue
    for (let i = 1; i < lane.points.length; i++) {
      const [ax, , az] = lane.points[i - 1]
      const [bx, , bz] = lane.points[i]
      const half = lane.width / 2
      const segment = { ax, az, bx, bz, half }
      const x0 = Math.floor((Math.min(ax, bx) - half) / cell)
      const x1 = Math.floor((Math.max(ax, bx) + half) / cell)
      const z0 = Math.floor((Math.min(az, bz) - half) / cell)
      const z1 = Math.floor((Math.max(az, bz) + half) / cell)
      for (let gx = x0; gx <= x1; gx++) {
        for (let gz = z0; gz <= z1; gz++) {
          const key = `${gx}:${gz}`
          const list = grid.get(key)
          if (list) list.push(segment)
          else grid.set(key, [segment])
        }
      }
    }
  }
  return grid
}

export function onGroundCarriageway(
  grid: Map<string, Segment[]>,
  x: number,
  z: number,
  margin = 1.0,
  cell = 16,
): boolean {
  const list = grid.get(`${Math.floor(x / cell)}:${Math.floor(z / cell)}`)
  if (!list) return false
  for (const s of list) {
    const dx = s.bx - s.ax
    const dz = s.bz - s.az
    const lengthSq = dx * dx + dz * dz || 1
    const t = Math.max(0, Math.min(1, ((x - s.ax) * dx + (z - s.az) * dz) / lengthSq))
    const px = s.ax + dx * t
    const pz = s.az + dz * t
    if (Math.hypot(x - px, z - pz) < s.half + margin) return true
  }
  return false
}

export interface Pier {
  position: [number, number, number]
  angle: number
  width: number
}

/**
 * Pier positions along each deck: evenly spaced, only where the underside clears
 * the ground, and nudged along the deck — or skipped — rather than standing in a
 * carriageway below. Spacing is a rendering choice (ESTIMATED), not the real
 * structure's.
 */
export function placePiers(decks: Lane[], ground: Map<string, Segment[]>): Pier[] {
  const byEdge = new Map<string, Lane[]>()
  for (const lane of decks) {
    if (lane.internal) continue
    const list = byEdge.get(lane.edge)
    if (list) list.push(lane)
    else byEdge.set(lane.edge, [lane])
  }
  const piers: Pier[] = []
  const seen = new Set<string>()
  for (const list of byEdge.values()) {
    list.sort((a, b) => a.index - b.index)
    const centre = list[Math.floor(list.length / 2)]
    const width = list.reduce((sum, l) => sum + l.width, 0)
    let since = PIER_SPACING_M / 2
    for (let i = 1; i < centre.points.length; i++) {
      const p = centre.points[i - 1]
      const [x, y, z] = centre.points[i]
      const length = Math.hypot(x - p[0], z - p[2])
      since += length
      if (since < PIER_SPACING_M || y - DECK_DEPTH_M < MIN_PIER_CLEARANCE_M) continue
      const ux = (x - p[0]) / (length || 1)
      const uz = (z - p[2]) / (length || 1)
      const spot = [0, -5, 5, -10, 10]
        .map((shift) => [x + ux * shift, z + uz * shift] as const)
        .find(([px, pz]) => !onGroundCarriageway(ground, px, pz))
      if (!spot) continue
      const key = `${Math.round(spot[0] / 8)}:${Math.round(spot[1] / 8)}`
      since = 0
      if (seen.has(key)) continue
      seen.add(key)
      piers.push({ position: [spot[0], y, spot[1]], angle: Math.atan2(ux, uz), width })
    }
  }
  return piers
}

/** A vertical wall along each polyline, from `bottom(y)` to `top(y)` at every point. */
function wallGeometry(
  lines: [number, number, number][][],
  bottom: (y: number) => number,
  top: (y: number) => number,
): THREE.BufferGeometry {
  const positions: number[] = []
  const indices: number[] = []
  let base = 0
  for (const points of lines) {
    if (points.length < 2) continue
    for (const [x, y, z] of points) positions.push(x, bottom(y), z, x, top(y), z)
    for (let i = 0; i < points.length - 1; i++) {
      const a = base + i * 2
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2)
    }
    base += points.length * 2
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  g.setIndex(indices)
  g.computeVertexNormals()
  g.computeBoundingSphere()
  return g
}

/**
 * Elevated structure: soffit, side fascia, parapets and piers.
 *
 * The deck's plan position is the network's own lane geometry and its height
 * comes from the exported points, which in HISTORICAL_DEMO are ramped where a
 * structure meets the road it joins. The structure follows those heights point
 * by point, so a ramp's underside, walls and parapets come down to the ground
 * with it instead of ending in mid-air. Depth, parapet height and pier spacing are
 * rendering choices (ESTIMATED), chosen to read as an urban viaduct.
 */
function ElevatedStructure({ decks, ground }: { decks: Lane[]; ground: Lane[] }) {
  const built = useMemo(() => {
    const byEdge = new Map<string, Lane[]>()
    for (const lane of decks) {
      if (lane.internal) continue
      const list = byEdge.get(lane.edge)
      if (list) list.push(lane)
      else byEdge.set(lane.edge, [lane])
    }
    const edges: [number, number, number][][] = []
    for (const list of byEdge.values()) {
      list.sort((a, b) => a.index - b.index)
      const inner = list[0]
      const outer = list[list.length - 1]
      edges.push(offsetPolyline3(inner.points, inner.width / 2 + 0.25))
      edges.push(offsetPolyline3(outer.points, -(outer.width / 2 + 0.25)))
    }
    const underside = (y: number) => Math.max(0.03, y - DECK_DEPTH_M)
    const soffit = toGeometry(
      buildRibbons(
        decks.map((l) => ({
          points: l.points.map(([x, y, z]) => [x, underside(y), z] as [number, number, number]),
          width: l.width + 0.5,
        })),
        0,
      ),
    )
    const fascia = wallGeometry(edges, underside, (y) => y)
    const parapet = wallGeometry(edges, (y) => y, (y) => y + 1.05)
    const piers = placePiers(decks, groundIndex(ground))
    return { soffit, fascia, parapet, piers }
  }, [decks, ground])

  useEffect(
    () => () => {
      built.soffit.dispose()
      built.fascia.dispose()
      built.parapet.dispose()
    },
    [built],
  )

  const columns = useRef<THREE.InstancedMesh>(null)
  const caps = useRef<THREE.InstancedMesh>(null)
  useLayoutEffect(() => {
    const matrix = new THREE.Matrix4()
    const quaternion = new THREE.Quaternion()
    const position = new THREE.Vector3()
    const scale = new THREE.Vector3()
    built.piers.forEach((pier, i) => {
      const top = pier.position[1] - DECK_DEPTH_M
      quaternion.setFromEuler(new THREE.Euler(0, pier.angle, 0))
      if (columns.current) {
        position.set(pier.position[0], (top - 0.7) / 2, pier.position[2])
        scale.set(1, Math.max(0.5, top - 0.7), 1)
        columns.current.setMatrixAt(i, matrix.compose(position, quaternion, scale))
      }
      if (caps.current) {
        position.set(pier.position[0], top - 0.35, pier.position[2])
        scale.set(Math.max(3, pier.width * 0.85), 1, 1)
        caps.current.setMatrixAt(i, matrix.compose(position, quaternion, scale))
      }
    })
    if (columns.current) columns.current.instanceMatrix.needsUpdate = true
    if (caps.current) caps.current.instanceMatrix.needsUpdate = true
  }, [built])

  const count = Math.max(1, built.piers.length)
  return (
    <group>
      <mesh geometry={built.soffit} castShadow receiveShadow>
        <meshStandardMaterial color={SOFFIT} roughness={0.95} side={THREE.DoubleSide} />
      </mesh>
      <mesh geometry={built.fascia} castShadow>
        <meshStandardMaterial color={FASCIA} roughness={0.85} side={THREE.DoubleSide} />
      </mesh>
      <mesh geometry={built.parapet} castShadow>
        <meshStandardMaterial color={PARAPET} roughness={0.8} side={THREE.DoubleSide} />
      </mesh>
      {built.piers.length > 0 && (
        <>
          <instancedMesh ref={columns} args={[undefined, undefined, count]} castShadow frustumCulled={false}>
            <cylinderGeometry args={[0.85, 0.95, 1, 16]} />
            <meshStandardMaterial color={PIER} roughness={0.85} />
          </instancedMesh>
          <instancedMesh ref={caps} args={[undefined, undefined, count]} castShadow frustumCulled={false}>
            <boxGeometry args={[1, 0.7, 1.9]} />
            <meshStandardMaterial color={PIER_CAP} roughness={0.8} />
          </instancedMesh>
        </>
      )}
    </group>
  )
}

/** Offset a 3D polyline sideways, preserving its height at every point. */
function offsetPolyline3(
  points: [number, number, number][],
  distance: number,
): [number, number, number][] {
  const out: [number, number, number][] = []
  for (let i = 0; i < points.length; i++) {
    const a = points[Math.max(0, i - 1)]
    const b = points[Math.min(points.length - 1, i + 1)]
    const dx = b[0] - a[0]
    const dz = b[2] - a[2]
    const len = Math.hypot(dx, dz) || 1
    out.push([points[i][0] - (dz / len) * distance, points[i][1], points[i][2] + (dx / len) * distance])
  }
  return out
}

export function RoadNetwork({
  lanes,
  junctions,
  kerbs,
  layerFilter,
}: {
  lanes: Lane[]
  junctions: Junction[]
  kerbs: Kerb[]
  layerFilter: 'all' | 'surface' | 'elevated'
}) {
  const visible = useMemo(() => {
    if (layerFilter === 'all') return lanes
    if (layerFilter === 'elevated') return lanes.filter((l) => l.layer > 0)
    return lanes.filter((l) => l.layer <= 0)
  }, [lanes, layerFilter])

  const surface = useMemo(
    () =>
      visible
        .filter((l) => !l.internal && l.layer <= 0)
        .map((l) => ({ points: l.points, width: l.width })),
    [visible],
  )
  const elevated = useMemo(() => visible.filter((l) => l.layer > 0), [visible])
  const ground = useMemo(() => lanes.filter((l) => l.layer <= 0), [lanes])
  const elevatedRibbons = useMemo(
    () => elevated.map((l) => ({ points: l.points, width: l.width })),
    [elevated],
  )
  const internal = useMemo(
    () =>
      visible
        .filter((l) => l.internal && l.layer <= 0)
        .map((l) => ({ points: l.points, width: l.width })),
    [visible],
  )
  const markings = useMemo(() => laneMarkingLines(visible), [visible])
  const edges = useMemo(() => edgeLines(visible), [visible])
  const kerbRibbons = useMemo(() => kerbLines(kerbs), [kerbs])

  return (
    <group>
      <MergedRibbon lines={surface} color={ASPHALT} />
      <MergedRibbon lines={internal} color={JUNCTION} yOffset={0.01} />
      <JunctionSurfaces junctions={junctions} />
      <MergedRibbon lines={elevatedRibbons} color={ELEVATED_DECK} />
      {elevated.length > 0 && <ElevatedStructure decks={elevated} ground={ground} />}
      <MergedRibbon lines={kerbRibbons} color={KERB} yOffset={0.0} />
      <MergedRibbon lines={markings} color={MARKING} yOffset={0.04} polygonOffset={-2} />
      <MergedRibbon lines={edges} color={MARKING} yOffset={0.04} polygonOffset={-2} />
    </group>
  )
}
