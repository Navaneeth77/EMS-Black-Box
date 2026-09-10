/** Response shape of `GET /api/health`, mirroring `backend/app/models/schemas.py`. */

export type ComponentStatus = 'ready' | 'not_configured' | 'unavailable'

export interface ComponentHealth {
  status: ComponentStatus
  detail: string
}

export interface HealthResponse {
  status: string
  service: string
  version: string
  environment: string
  /** True only once a runnable SUMO scenario exists. False during scaffolding. */
  simulation_ready: boolean
  components: Record<string, ComponentHealth>
}
