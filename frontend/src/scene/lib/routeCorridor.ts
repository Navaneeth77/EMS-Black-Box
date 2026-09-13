/**
 * The ambulance's route as one polyline, for the green-corridor overlay.
 *
 * Built from the route's own SUMO edges and their lane shapes, so the corridor
 * follows the roads the ambulance is routed over rather than a straight line to
 * the signal. It is only ever drawn between the ambulance's recorded position
 * and the stop line of a signal whose recorded policy state is PRIORITY_ACTIVE.
 */

import type { Lane } from './types'

export interface RoutePolyline {
  points: [number, number, number][]
  /** Index in `points` of the last point of each route edge (its stop line). */
  edgeEnd: Map<string, number>
}

export function buildRoutePolyline(routeEdges: string[], lanes: Lane[]): RoutePolyline {
  const byEdge = new Map<string, Lane[]>()
  for (const lane of lanes) {
    if (lane.internal) continue
    const list = byEdge.get(lane.edge)
    if (list) list.push(lane)
    else byEdge.set(lane.edge, [lane])
  }
  const points: [number, number, number][] = []
  const edgeEnd = new Map<string, number>()
  for (const edge of routeEdges) {
    const list = byEdge.get(edge)
    if (!list || list.length === 0) continue
    const sorted = [...list].sort((a, b) => a.index - b.index)
    const lane = sorted[Math.floor((sorted.length - 1) / 2)]
    for (const point of lane.points) {
      const last = points[points.length - 1]
      if (last && Math.hypot(point[0] - last[0], point[2] - last[2]) < 0.5) continue
      points.push(point)
    }
    if (points.length > 0) edgeEnd.set(edge, points.length - 1)
  }
  return { points, edgeEnd }
}

export function nearestPointIndex(points: [number, number, number][], x: number, z: number): number {
  let best = -1
  let bestDistance = Infinity
  for (let i = 0; i < points.length; i++) {
    const d = (points[i][0] - x) ** 2 + (points[i][2] - z) ** 2
    if (d < bestDistance) {
      bestDistance = d
      best = i
    }
  }
  return best
}

/** Point range from the ambulance to a stop line ahead of it, or null if none. */
export function corridorSpan(
  route: RoutePolyline,
  ambulance: [number, number],
  stopEdge: string,
): [number, number] | null {
  const end = route.edgeEnd.get(stopEdge)
  if (end === undefined) return null
  const start = nearestPointIndex(route.points, ambulance[0], ambulance[1])
  if (start < 0 || start >= end) return null
  return [start, end]
}
