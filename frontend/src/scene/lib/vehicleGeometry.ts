/**
 * Vehicle geometry, authored in code.
 *
 * No suitable CC0/permissively-licensed GLB set covering these seven types was
 * obtainable — see `data/provenance/3d_assets.json` for what was searched and
 * why the reachable candidates (a milk truck, a toy car, a concept car) were
 * rejected. Rather than ship recognisable-but-wrong vehicles or fall back to
 * cubes, each type is built here as a silhouette that is identifiable at
 * traffic-scene distance.
 *
 * **Length and width come from the SUMO vType**, so a vehicle occupies the space
 * the simulation gave it — draw a bus longer than 12 m and it eats the gap the
 * car-following model actually left. Heights, proportions and colour are
 * authored and carry no data.
 *
 * All meshes are built nose-forward along **−Z**, matching the heading
 * convention in `coords.ts`. Each is a single merged, indexed BufferGeometry so
 * a whole vehicle class is one instanced draw call.
 */

import * as THREE from 'three'

function box(w: number, h: number, d: number, x = 0, y = 0, z = 0): THREE.BufferGeometry {
  const g = new THREE.BoxGeometry(w, h, d)
  g.translate(x, y, z)
  return g
}

function cyl(r: number, h: number, x: number, y: number, z: number, segments = 8) {
  const g = new THREE.CylinderGeometry(r, r, h, segments)
  g.rotateZ(Math.PI / 2) // axle across the vehicle
  g.translate(x, y, z)
  return g
}

/** Merge into one indexed geometry. Keeps each vehicle class to a single draw. */
function merge(parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = []
  const normals: number[] = []
  const colors: number[] = []
  for (const part of parts) {
    const g = part.index ? part.toNonIndexed() : part
    const p = g.getAttribute('position')
    const n = g.getAttribute('normal')
    const c = g.getAttribute('color')
    for (let i = 0; i < p.count; i++) {
      positions.push(p.getX(i), p.getY(i), p.getZ(i))
      normals.push(n.getX(i), n.getY(i), n.getZ(i))
      if (c) colors.push(c.getX(i), c.getY(i), c.getZ(i))
      else colors.push(1, 1, 1)
    }
    part.dispose()
    if (g !== part) g.dispose()
  }
  const out = new THREE.BufferGeometry()
  out.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  out.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3))
  out.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
  out.computeBoundingSphere()
  return out
}

/** Paint a part with a vertex colour, so one material can serve a whole vehicle. */
function tint(g: THREE.BufferGeometry, hex: string): THREE.BufferGeometry {
  const color = new THREE.Color(hex)
  const count = g.getAttribute('position').count
  const colors = new Float32Array(count * 3)
  for (let i = 0; i < count; i++) {
    colors[i * 3] = color.r
    colors[i * 3 + 1] = color.g
    colors[i * 3 + 2] = color.b
  }
  g.setAttribute('color', new THREE.BufferAttribute(colors, 3))
  return g
}

const GLASS = '#2b3a4a'
const TYRE = '#15171a'

function wheels(length: number, width: number, radius: number, pairs = 2) {
  const out: THREE.BufferGeometry[] = []
  const halfW = width / 2 - radius * 0.35
  for (let p = 0; p < pairs; p++) {
    const z = pairs === 1 ? 0 : -length / 2 + length * (0.22 + (0.56 * p) / (pairs - 1))
    out.push(tint(cyl(radius, width * 0.16, -halfW, radius, z), TYRE))
    out.push(tint(cyl(radius, width * 0.16, halfW, radius, z), TYRE))
  }
  return out
}

/** A four-wheeled car: bonnet, cabin with glazing, boot. */
export function carGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.19
  return merge([
    tint(box(width, height * 0.42, length, 0, r + height * 0.21, 0), body),
    tint(box(width * 0.92, height * 0.34, length * 0.46, 0, r + height * 0.58, -length * 0.03), GLASS),
    tint(box(width * 0.99, height * 0.1, length * 0.99, 0, r + height * 0.05, 0), '#000000'),
    ...wheels(length, width, r),
  ])
}

/** A two-wheeler with a rider — the rider is what makes it read as one. */
export function motorcycleGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.24
  return merge([
    tint(box(width * 0.9, height * 0.2, length * 0.75, 0, r + height * 0.12, 0), body),
    tint(box(width * 0.55, height * 0.34, length * 0.3, 0, r + height * 0.42, length * 0.05), '#33383f'),
    tint(box(width * 1.5, height * 0.06, length * 0.06, 0, r + height * 0.3, -length * 0.32), '#4a4f57'),
    tint(cyl(r, width * 0.18, 0, r, -length * 0.34), TYRE),
    tint(cyl(r, width * 0.18, 0, r, length * 0.34), TYRE),
  ])
}

/** Auto-rickshaw: three wheels and a canopy — the diagnostic silhouette. */
export function autoRickshawGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.17
  return merge([
    tint(box(width, height * 0.36, length * 0.86, 0, r + height * 0.18, 0), body),
    tint(box(width * 0.96, height * 0.36, length * 0.8, 0, r + height * 0.56, length * 0.04), '#f2f2ef'),
    tint(box(width * 0.9, height * 0.26, length * 0.06, 0, r + height * 0.52, -length * 0.4), GLASS),
    tint(cyl(r, width * 0.16, 0, r, -length * 0.36), TYRE),
    tint(cyl(r, width * 0.16, -width / 2 + r * 0.4, r, length * 0.3), TYRE),
    tint(cyl(r, width * 0.16, width / 2 - r * 0.4, r, length * 0.3), TYRE),
  ])
}

/** Bus: long, tall, banded glazing, three axles' worth of wheels. */
export function busGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.16
  return merge([
    tint(box(width, height * 0.74, length, 0, r + height * 0.37, 0), body),
    tint(box(width * 1.005, height * 0.2, length * 0.9, 0, r + height * 0.56, 0), GLASS),
    tint(box(width * 0.96, height * 0.28, length * 0.04, 0, r + height * 0.5, -length / 2), GLASS),
    ...wheels(length, width, r, 3),
  ])
}

/** Truck: separate cab and load body, so it is not read as a bus. */
export function truckGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.15
  return merge([
    tint(box(width, height * 0.46, length * 0.28, 0, r + height * 0.25, -length * 0.36), body),
    tint(box(width * 0.9, height * 0.18, length * 0.06, 0, r + height * 0.4, -length * 0.5), GLASS),
    tint(box(width * 0.98, height * 0.62, length * 0.66, 0, r + height * 0.36, length * 0.15), '#b9bcc0'),
    ...wheels(length, width, r, 3),
  ])
}

/** Van: one continuous box body, taller than a car, no separate boot. */
export function vanGeometry(length: number, width: number, height: number, body: string) {
  const r = height * 0.16
  return merge([
    tint(box(width, height * 0.62, length, 0, r + height * 0.33, 0), body),
    tint(box(width * 0.94, height * 0.22, length * 0.28, 0, r + height * 0.5, -length * 0.3), GLASS),
    ...wheels(length, width, r),
  ])
}

/**
 * Ambulance — the one vehicle a viewer must recognise instantly.
 *
 * Built as a box-body ambulance rather than a plain van: a lower cab with a
 * raked windscreen, a taller square patient compartment stepped up behind it,
 * a full-length Battenburg-style livery band, a roof light bar with separate
 * red and blue lenses, headlights, tail lights, a rear door line and wheels
 * with visible arches.
 *
 * Every dimension is derived from the SUMO vType (6.0 x 2.2 m), so it still
 * occupies exactly the space the simulation gave it. The detail is silhouette
 * work, not extra size.
 */
export function ambulanceGeometry(length: number, width: number, height: number) {
  const r = height * 0.155
  const wheelbaseFront = -length * 0.3
  const wheelbaseRear = length * 0.3
  const bodyY = r + height * 0.34

  const parts: THREE.BufferGeometry[] = [
    // Chassis rail, visible under the body between the wheels.
    tint(box(width * 0.86, height * 0.1, length * 0.9, 0, r * 0.9, 0), '#2a2d31'),

    // Patient compartment: the tall square box that makes it an ambulance.
    tint(box(width, height * 0.62, length * 0.62, 0, bodyY + height * 0.1, length * 0.17), '#f7f7f5'),
    // Cab: lower and slightly narrower, stepped down from the box.
    tint(box(width * 0.94, height * 0.42, length * 0.4, 0, bodyY - height * 0.02, -length * 0.3), '#f7f7f5'),
    // Bonnet.
    tint(box(width * 0.9, height * 0.2, length * 0.12, 0, bodyY - height * 0.12, -length * 0.46), '#f0f0ee'),

    // Raked windscreen and cab side glass.
    tint(box(width * 0.86, height * 0.26, length * 0.03, 0, bodyY + height * 0.14, -length * 0.47), GLASS),
    tint(box(width * 1.005, height * 0.2, length * 0.3, 0, bodyY + height * 0.1, -length * 0.3), GLASS),
    // Patient compartment windows, upper band only.
    tint(box(width * 1.005, height * 0.16, length * 0.34, 0, bodyY + height * 0.26, length * 0.12), GLASS),

    // Battenburg-style livery band along the full body.
    tint(box(width * 1.015, height * 0.14, length * 0.98, 0, bodyY - height * 0.04, 0), '#d1362f'),
    // Thin secondary stripe above it.
    tint(box(width * 1.012, height * 0.04, length * 0.98, 0, bodyY + height * 0.05, 0), '#1f4f8f'),

    // Roof light bar: separate red and blue lenses on a dark base.
    tint(box(width * 0.72, height * 0.05, length * 0.1, 0, bodyY + height * 0.44, -length * 0.06), '#26292e'),
    tint(box(width * 0.3, height * 0.07, length * 0.09, -width * 0.18, bodyY + height * 0.48, -length * 0.06), '#ff3b30'),
    tint(box(width * 0.3, height * 0.07, length * 0.09, width * 0.18, bodyY + height * 0.48, -length * 0.06), '#2f6bff'),

    // Headlights and tail lights.
    tint(box(width * 0.22, height * 0.08, length * 0.02, -width * 0.32, bodyY - height * 0.1, -length * 0.51), '#fff6d8'),
    tint(box(width * 0.22, height * 0.08, length * 0.02, width * 0.32, bodyY - height * 0.1, -length * 0.51), '#fff6d8'),
    tint(box(width * 0.2, height * 0.1, length * 0.02, -width * 0.33, bodyY + height * 0.02, length * 0.48), '#c62828'),
    tint(box(width * 0.2, height * 0.1, length * 0.02, width * 0.33, bodyY + height * 0.02, length * 0.48), '#c62828'),

    // Rear door split line.
    tint(box(width * 0.03, height * 0.5, length * 0.02, 0, bodyY + height * 0.12, length * 0.48), '#d8d8d4'),

    // Wheel arches, so the wheels read as attached rather than floating.
    tint(box(width * 1.02, height * 0.16, length * 0.16, 0, r + height * 0.06, wheelbaseFront), '#e6e6e3'),
    tint(box(width * 1.02, height * 0.16, length * 0.16, 0, r + height * 0.06, wheelbaseRear), '#e6e6e3'),
  ]

  const halfW = width / 2 - r * 0.3
  for (const z of [wheelbaseFront, wheelbaseRear]) {
    parts.push(tint(cyl(r, width * 0.16, -halfW, r, z, 10), TYRE))
    parts.push(tint(cyl(r, width * 0.16, halfW, r, z, 10), TYRE))
    // Hub faces.
    parts.push(tint(cyl(r * 0.45, width * 0.17, -halfW, r, z, 8), '#9aa0a6'))
    parts.push(tint(cyl(r * 0.45, width * 0.17, halfW, r, z, 8), '#9aa0a6'))
  }

  return merge(parts)
}
