/**
 * Building massing from OSM footprints.
 *
 * Footprints are real (`PUBLICLY_SOURCED_DATA`, OSM building ways). Heights are
 * almost all estimated — 3 of 3,179 ways carry a `height` tag — by a
 * deterministic rule documented in `ems_sim/viz/buildings.py`.
 *
 * They are rendered in a deliberately flat, desaturated material so they read as
 * context massing rather than as surveyed buildings. Extruded once into a single
 * merged buffer: 2,500 separate meshes would cost more than the roads and the
 * traffic combined, and none of it moves.
 */

import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import type { Building } from '../lib/types'

/**
 * Three tones keyed to the OSM `building` tag, which is real data.
 *
 * A single flat grey reads as a monotone field of blocks at close range. The
 * variation here is driven by the tag the footprint already carries, so it adds
 * no information that was not in the source; it is not randomised shading.
 */
// Kept in the mid-grey range: under the scene's lighting, lighter tones rendered as
// near-white blocks that read as placeholder boxes rather than context massing.
const TONES: Record<string, string> = {
  apartments: '#80868e',
  residential: '#80868e',
  house: '#8f887e',
  commercial: '#767c84',
  office: '#767c84',
  retail: '#767c84',
  industrial: '#6c7279',
  warehouse: '#6c7279',
}
const DEFAULT_TONE = '#7c8289'

function toneFor(type: string): string {
  return TONES[type] ?? DEFAULT_TONE
}

export function Buildings({ buildings }: { buildings: Building[] }) {
  const groups = useMemo(() => {
    const byTone = new Map<string, Building[]>()
    for (const building of buildings) {
      const tone = toneFor(building.type)
      const list = byTone.get(tone)
      if (list) list.push(building)
      else byTone.set(tone, [building])
    }
    return [...byTone.entries()]
  }, [buildings])

  return (
    <group>
      {groups.map(([tone, list]) => (
        <BuildingBatch key={tone} buildings={list} color={tone} />
      ))}
    </group>
  )
}

function BuildingBatch({ buildings, color }: { buildings: Building[]; color: string }) {
  const geometry = useMemo(() => {
    const shapes: THREE.ExtrudeGeometry[] = []
    for (const building of buildings) {
      if (building.footprint.length < 3) continue
      // ExtrudeGeometry lays the shape in XY and extrudes along +Z. Rotating
      // by -90 deg about X sends +Z to +Y (so the building rises) and shape-Y
      // to -Z. The footprint is therefore built with its z negated, so that
      // second flip lands it back on the exported world position rather than
      // mirroring the city about the origin.
      const shape = new THREE.Shape()
      const [x0, z0] = building.footprint[0]
      shape.moveTo(x0, -z0)
      for (let i = 1; i < building.footprint.length; i++) {
        shape.lineTo(building.footprint[i][0], -building.footprint[i][1])
      }
      shape.closePath()
      const extruded = new THREE.ExtrudeGeometry(shape, {
        depth: building.height,
        bevelEnabled: false,
        curveSegments: 1,
      })
      extruded.rotateX(-Math.PI / 2)
      shapes.push(extruded)
    }
    if (shapes.length === 0) return new THREE.BufferGeometry()

    const positions: number[] = []
    const normals: number[] = []
    for (const shapeGeometry of shapes) {
      const nonIndexed = shapeGeometry.index ? shapeGeometry.toNonIndexed() : shapeGeometry
      const position = nonIndexed.getAttribute('position')
      const normal = nonIndexed.getAttribute('normal')
      for (let i = 0; i < position.count; i++) {
        positions.push(position.getX(i), position.getY(i), position.getZ(i))
        normals.push(normal.getX(i), normal.getY(i), normal.getZ(i))
      }
      shapeGeometry.dispose()
    }
    const merged = new THREE.BufferGeometry()
    merged.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    merged.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3))
    merged.computeBoundingSphere()
    return merged
  }, [buildings])

  useEffect(() => () => geometry.dispose(), [geometry])

  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color={color} roughness={0.95} metalness={0} flatShading />
    </mesh>
  )
}
