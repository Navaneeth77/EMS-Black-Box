/**
 * Visual definitions per SUMO vType.
 *
 * Dimensions mirror the vTypes in `ems_sim/demand/vehicle_types.py` so a bus in
 * the scene is the length SUMO gave it — a vehicle drawn longer than it is
 * simulated would overlap the gap the car-following model actually left.
 *
 * Colours are a rendering choice and carry no data.
 */

export interface VehicleStyle {
  /** Metres, from the SUMO vType. */
  length: number
  width: number
  height: number
  color: string
  /** Cab/superstructure proportion, purely visual. */
  cab: number
  label: string
}

export const VEHICLE_STYLES: Record<string, VehicleStyle> = {
  motorcycle: { length: 2.2, width: 0.8, height: 1.4, color: '#d97757', cab: 0.35, label: 'Motorcycle' },
  auto: { length: 2.6, width: 1.3, height: 1.7, color: '#f0c14b', cab: 0.6, label: 'Auto-rickshaw' },
  car: { length: 4.5, width: 1.8, height: 1.5, color: '#8ba3c7', cab: 0.55, label: 'Car' },
  van: { length: 5.5, width: 2.0, height: 2.2, color: '#9aa5b1', cab: 0.75, label: 'Van' },
  truck: { length: 7.5, width: 2.4, height: 3.0, color: '#7d8794', cab: 0.8, label: 'Truck' },
  bus: { length: 12.0, width: 2.5, height: 3.2, color: '#4a9d7f', cab: 0.9, label: 'Bus' },
  ambulance: { length: 6.0, width: 2.2, height: 2.6, color: '#f5f5f5', cab: 0.85, label: 'Ambulance' },
}

export const FALLBACK_STYLE: VehicleStyle = {
  length: 4.5,
  width: 1.8,
  height: 1.5,
  color: '#6b7280',
  cab: 0.55,
  label: 'Unknown',
}

export function styleFor(typeName: string): VehicleStyle {
  // SUMO vType ids in this project are exactly the keys above, but demand flows
  // prefix them (e.g. 'f012_bus'), so match on the suffix too.
  if (VEHICLE_STYLES[typeName]) return VEHICLE_STYLES[typeName]
  for (const key of Object.keys(VEHICLE_STYLES)) {
    if (typeName.endsWith(key)) return VEHICLE_STYLES[key]
  }
  return FALLBACK_STYLE
}
