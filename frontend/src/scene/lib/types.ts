/** Shapes of the exported scene files. Mirrors `ems_sim/viz/*`. */

import type { SceneTransformInfo } from './coords'

export type DataClass =
  | 'VERIFIED_REAL_DATA'
  | 'PUBLICLY_SOURCED_DATA'
  | 'ESTIMATED_DATA'
  | 'SIMULATED_DATA'

export interface Lane {
  id: string
  edge: string
  index: number
  width: number
  speed_kmh: number
  type: string
  layer: number
  structure: 'surface' | 'bridge' | 'tunnel' | 'internal'
  internal: boolean
  /** Scene-space [x, y, z] polyline. */
  points: [number, number, number][]
}

export interface Kerb {
  edge: string
  width: number
  layer: number
  points: [number, number, number][]
  data_class: DataClass
}

export interface Junction {
  id: string
  type: string
  layer: number
  position: [number, number, number]
  shape: [number, number, number][]
  tls: boolean
}

export interface NetworkScene {
  lanes: Lane[]
  kerbs: Kerb[]
  junctions: Junction[]
  counts: Record<string, number>
  transform: SceneTransformInfo
}

export interface TlsLink {
  index: number
  from_lane: string
  from_edge: string
  position: [number, number, number]
  heading: number
  lane_width: number
}

export interface TrafficLight {
  id: string
  links: TlsLink[]
  link_count: number
  programs: {
    id: string
    type?: string
    offset?: number
    replayable?: boolean
    phases: { state: string; duration: number }[]
  }[]
  data_class: DataClass
  note: string
}

export interface SignalsScene {
  traffic_lights: TrafficLight[]
  count: number
}

export interface Building {
  id: string
  type: string
  height: number
  height_class: DataClass
  area_m2: number
  /** Scene-space [x, z] footprint ring. */
  footprint: [number, number][]
}

export interface BuildingsScene {
  buildings: Building[]
  counts: Record<string, number>
  footprint_data_class: DataClass
  height_data_class: DataClass
  height_basis: string
}

/** Frame-major trajectory state. Parallel arrays, interned ids. */
export interface Frame {
  id: number[]
  x: number[]
  y: number[]
  z: number[]
  /** SUMO heading, degrees clockwise from north. */
  a: number[]
  /** m/s. */
  s: number[]
  l: number[]
}

export interface Trajectories {
  schema: string
  times: number[]
  frames: Frame[]
  vehicle_ids: string[]
  vehicle_types: string[]
  lane_ids: string[]
  vehicle_type_index: number[]
  vehicle_first_seen_s: number[]
  vehicle_last_seen_s: number[]
  window: { begin_s: number | null; end_s: number | null }
  counts: Record<string, number>
  angle_convention: string
  data_class: DataClass
  note: string
}

export interface Manifest {
  generated_at: string
  sumo_version: string
  scenario: {
    area: string
    trip: string
    seed: number
    policy: string
    variant: string
    demand_id: string
    window_s: [number, number]
    fcd_period_s: number
    step_length_s: number
  }
  mode?: string
  demo_note?: string | null
  network_file: string
  network_sha256: string
  ambulance: {
    vehicle_id: string
    travel_time_s: number | null
    waiting_time_s: number | null
    time_loss_s: number | null
    route_edges: string[]
    signal_wait_events: unknown[]
    depart_time_s: number
    arrived_at_s?: number | null
    stops?: number
    /** HISTORICAL_DEMO: the ambulance's first halt in the four-way's queue. */
    queue_joined?: {
      sim_time_s: number
      edge_id: string
      distance_to_stop_line_m: number
      vehicles_ahead: number
      vehicles_ahead_halted: number
      stopped_by_signal: boolean
    } | null
    /** HISTORICAL_DEMO: where the ambulance was when priority was first requested. */
    priority_requests?: {
      tls_id: string
      sim_time_s: number
      distance_to_stop_line_m?: number | null
      ambulance_speed_ms?: number | null
      /** How far out the policy's own formula said to ask, at that speed. */
      activation_distance_m?: number | null
      eta_s?: number | null
      lead_time_s?: number | null
      transition_s?: number | null
      queue_clearance_s?: number | null
      reason: string
    }[]
    policy_parameters?: Record<string, unknown>
  }
  incident?: {
    incident: {
      incident_id: string
      incident_type: string
      start_time_s: number
      end_time_s: number
      duration_s: number
      edge_id: string
      lane_id: string
      lane_index: number
      blockage: string
      description: string
      selection_basis: string
      data_class: string
      provenance: string
      config_hash: string
    } | null
    events: { event: string; sim_time_s: number; lane_id: string }[]
    applied: boolean
    note?: string
  } | null
  queues?: Record<string, number | string | null>
  /** Teleports, conflicts and backlog for this run, from its own measurements. */
  run_quality?: Record<string, number | string>
  comparison?: {
    policy: string
    is_baseline?: boolean
    note?: string
    baseline_policy?: string
    baseline_travel_time_s?: number
    policy_travel_time_s?: number
    time_saved_s?: number
    improvement_percent?: number
    baseline_waiting_s?: number
    policy_waiting_s?: number
    baseline_stops?: number
    policy_stops?: number
    traffic_delta_total_time_loss_s?: number
    traffic_metric_status?: string
    source?: string
    attribution?: {
      intersections: {
        tls_id: string
        approach_edge: string
        actionable: boolean
        green_fraction_for_ambulance: number
        baseline_traversal_time_s: number
        counterfactual_traversal_time_s: number
        delay_reduction_s: number
        state_transitions_here?: number
      }[]
      unattributed_change_s: number
      unattributed_note?: string
    }
  } | null
  /** Applied signal state per TLS as SUMO reported it, [time, state] on change. */
  signal_timeline?: Record<string, [number, string][]>
  policy_transitions?: {
    sim_time_s: number
    tls_id: string
    previous_state: string
    new_state: string
    reason: string
    ambulance_distance_to_tls_m?: number
  }[]
  route_traffic_lights: {
    tls_id: string
    approach_edge?: string
    /** Network edge name of the approach, where the export recorded it. */
    approach_road?: string
    green_fraction_for_ambulance: number
    has_red_exposure: boolean
    ambulance_link_indices: number[]
  }[]
  transform: SceneTransformInfo
  provenance: Record<string, string>
  not_a_real_world_claim: string
  /** `flat` layer rule, or `ramped` structure ends (HISTORICAL_DEMO). */
  elevation_model?: string
  demand_config_hash?: string
  historical?: HistoricalSnapshot
  intersection?: IntersectionInfo
}

export type EvidenceLabel = 'OBSERVED' | 'DERIVED' | 'ESTIMATED' | 'SIMULATED' | 'NOT REPORTED'

/** The HISTORICAL TRAFFIC SNAPSHOT, built by the exporter from the observed files. */
export interface HistoricalSnapshot {
  title: string
  location: string
  survey_date: string
  survey_period: string
  observed: { label: EvidenceLabel; text: string }[]
  composition: { label: EvidenceLabel; text: string }[]
  signal: { label: EvidenceLabel; text: string }
  source: { text: string; url: string; secondary_text: string; secondary_url: string }
  conversion: { label: EvidenceLabel; k: number; sumo_input_vph: number; text: string }
  replay_statement: string
  not_gps: string
  files: Record<string, string>
}

export interface IntersectionCamera {
  position: [number, number, number]
  target: [number, number, number]
  stop_line: [number, number, number]
  basis: string
}

export interface IntersectionApproach {
  from_edge: string
  link_indices: number[]
  node_id: string
  arm: string
  arm_bearing_deg: number
  travel_heading_deg: number
  road_name: string
}

/** The signalised four-way the demo's EMS priority acts on. */
export interface IntersectionInfo {
  tls_id: string
  name: string
  centre: [number, number, number]
  approaches: IntersectionApproach[]
  phases: { duration_s: number; state: string }[]
  cycle_length_s: number
  timing_provenance: Record<string, { value: string; label: string; note: string }>
  replaces_separate_tls_ids: string[]
  on_ambulance_route: boolean
  camera?: IntersectionCamera
}
