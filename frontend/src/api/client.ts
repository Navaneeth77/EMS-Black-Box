/**
 * Backend API client.
 *
 * Requests go to a relative `/api` path so Vite's dev proxy keeps the browser on
 * one origin. That matters more than it looks: the WebSocket stream added in
 * Phase 6 inherits the same origin and avoids a separate CORS setup.
 */

import type { HealthResponse } from '../types/health'

const API_BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/health`, { signal })
  } catch {
    // Distinguish "backend is not running" from "backend returned an error".
    // During scaffolding the former is the common case, and saying so directly
    // is more useful than a generic failure.
    throw new ApiError(
      'Could not reach the backend. Is it running on port 8000? ' +
        'Start it with: ./scripts/dev_backend.sh',
    )
  }

  if (!response.ok) {
    throw new ApiError(`Backend returned ${response.status}`, response.status)
  }

  return (await response.json()) as HealthResponse
}
