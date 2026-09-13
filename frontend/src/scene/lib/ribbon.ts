/**
 * Turn lane centrelines into road surface geometry.
 *
 * A SUMO lane is a centreline plus a width. To draw it we extrude a flat ribbon
 * of that width along the polyline. This is done once at load and **merged into
 * a handful of buffers**, because the network has ~7,000 lanes and one draw call
 * each would dominate the frame budget for geometry that never moves.
 *
 * The ribbon follows the recorded shape exactly. Corners are mitre-free — each
 * vertex is offset along the average of its adjacent segment normals — which
 * leaves a small notch on very sharp bends. That is a cosmetic approximation on
 * geometry that is otherwise the network's own, and it never moves a lane's
 * centreline.
 */

import * as THREE from 'three'
import type { Lane, Kerb } from './types'

export interface RibbonBuffers {
  positions: Float32Array
  normals: Float32Array
  uvs: Float32Array
  indices: Uint32Array
}

/** Offset directions at each vertex of a polyline, in the XZ plane. */
function sideNormals(points: [number, number, number][]): [number, number][] {
  const out: [number, number][] = []
  for (let i = 0; i < points.length; i++) {
    const a = points[Math.max(0, i - 1)]
    const b = points[Math.min(points.length - 1, i + 1)]
    const dx = b[0] - a[0]
    const dz = b[2] - a[2]
    const len = Math.hypot(dx, dz) || 1
    // Left-hand normal in the XZ plane.
    out.push([-dz / len, dx / len])
  }
  return out
}

/** Build one merged ribbon mesh from many polylines with per-line widths. */
export function buildRibbons(
  lines: { points: [number, number, number][]; width: number }[],
  yOffset = 0,
): RibbonBuffers {
  let vertexCount = 0
  let indexCount = 0
  for (const line of lines) {
    if (line.points.length < 2) continue
    vertexCount += line.points.length * 2
    indexCount += (line.points.length - 1) * 6
  }

  const positions = new Float32Array(vertexCount * 3)
  const normals = new Float32Array(vertexCount * 3)
  const uvs = new Float32Array(vertexCount * 2)
  const indices = new Uint32Array(indexCount)

  let v = 0
  let i = 0
  for (const line of lines) {
    const points = line.points
    if (points.length < 2) continue
    const half = line.width / 2
    const sides = sideNormals(points)
    const base = v / 3 / 1 // vertex index of this line's first vertex
    const firstVertex = base

    let distance = 0
    for (let p = 0; p < points.length; p++) {
      const [x, y, z] = points[p]
      const [nx, nz] = sides[p]
      if (p > 0) {
        distance += Math.hypot(x - points[p - 1][0], z - points[p - 1][2])
      }
      // Left edge
      positions[v] = x + nx * half
      positions[v + 1] = y + yOffset
      positions[v + 2] = z + nz * half
      // Right edge
      positions[v + 3] = x - nx * half
      positions[v + 4] = y + yOffset
      positions[v + 5] = z - nz * half

      normals[v + 1] = 1
      normals[v + 4] = 1

      const u = v / 3
      uvs[u * 2] = 0
      uvs[u * 2 + 1] = distance / 8
      uvs[(u + 1) * 2] = 1
      uvs[(u + 1) * 2 + 1] = distance / 8

      v += 6
    }

    for (let p = 0; p < points.length - 1; p++) {
      const a = firstVertex + p * 2
      indices[i++] = a
      indices[i++] = a + 1
      indices[i++] = a + 2
      indices[i++] = a + 1
      indices[i++] = a + 3
      indices[i++] = a + 2
    }
  }

  return { positions, normals, uvs, indices }
}

export function toGeometry(buffers: RibbonBuffers): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(buffers.positions, 3))
  geometry.setAttribute('normal', new THREE.BufferAttribute(buffers.normals, 3))
  geometry.setAttribute('uv', new THREE.BufferAttribute(buffers.uvs, 2))
  geometry.setIndex(new THREE.BufferAttribute(buffers.indices, 1))
  geometry.computeBoundingSphere()
  return geometry
}

/** Lane divider stripes: thin ribbons on the shared edge between adjacent lanes. */
export function laneMarkingLines(lanes: Lane[]): {
  points: [number, number, number][]
  width: number
}[] {
  const byEdge = new Map<string, Lane[]>()
  for (const lane of lanes) {
    if (lane.internal) continue
    const list = byEdge.get(lane.edge)
    if (list) list.push(lane)
    else byEdge.set(lane.edge, [lane])
  }
  const out: { points: [number, number, number][]; width: number }[] = []
  for (const list of byEdge.values()) {
    if (list.length < 2) continue
    list.sort((a, b) => a.index - b.index)
    // A divider sits on the boundary between lane i and lane i+1, which is half
    // a lane width to the left of the lower lane's centreline.
    for (let k = 0; k < list.length - 1; k++) {
      const lane = list[k]
      const sides = sideNormals(lane.points)
      const shifted = lane.points.map((p, idx) => {
        const [nx, nz] = sides[idx]
        const half = lane.width / 2
        return [p[0] + nx * half, p[1], p[2] + nz * half] as [number, number, number]
      })
      out.push({ points: shifted, width: 0.18 })
    }
  }
  return out
}

/** Outer edge lines along the kerb side of every non-internal lane group. */
export function edgeLines(lanes: Lane[]): {
  points: [number, number, number][]
  width: number
}[] {
  const byEdge = new Map<string, Lane[]>()
  for (const lane of lanes) {
    if (lane.internal) continue
    const list = byEdge.get(lane.edge)
    if (list) list.push(lane)
    else byEdge.set(lane.edge, [lane])
  }
  const out: { points: [number, number, number][]; width: number }[] = []
  for (const list of byEdge.values()) {
    list.sort((a, b) => a.index - b.index)
    const outer = list[list.length - 1]
    const sides = sideNormals(outer.points)
    out.push({
      points: outer.points.map((p, idx) => {
        const [nx, nz] = sides[idx]
        const half = outer.width / 2 - 0.12
        return [p[0] + nx * half, p[1], p[2] + nz * half] as [number, number, number]
      }),
      width: 0.14,
    })
  }
  return out
}

export function kerbLines(kerbs: Kerb[]): {
  points: [number, number, number][]
  width: number
}[] {
  return kerbs.map((k) => ({ points: k.points, width: k.width }))
}
