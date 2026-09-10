/**
 * Data-class labels, mirroring `backend/app/models/provenance.py`.
 *
 * The frontend carries these because the UI is where the measured/assumed
 * distinction is most easily lost. A number rendered in the same style as a
 * measurement will be read as one, whatever a caption says elsewhere on the
 * page.
 *
 * See `docs/DATA_INTEGRITY.md`.
 */
export type DataClass =
  /** From a primary source AND independently checked against it. */
  | 'VERIFIED_REAL_DATA'
  /** From a public, citable source; not independently verified. */
  | 'PUBLICLY_SOURCED_DATA'
  /** An assumption made by this project. Never presented as real. */
  | 'ESTIMATED_DATA'
  /** Produced by SUMO or downstream analysis. Reproducible from config + seed. */
  | 'SIMULATED_DATA'

/** Tailwind text colour per data class. Estimated is warning-coloured on purpose. */
export const DATA_CLASS_COLOR: Record<DataClass, string> = {
  VERIFIED_REAL_DATA: 'text-provenance-verified',
  PUBLICLY_SOURCED_DATA: 'text-provenance-sourced',
  ESTIMATED_DATA: 'text-provenance-estimated',
  SIMULATED_DATA: 'text-provenance-simulated',
}

/** Short labels for badges next to a value. */
export const DATA_CLASS_LABEL: Record<DataClass, string> = {
  VERIFIED_REAL_DATA: 'VERIFIED',
  PUBLICLY_SOURCED_DATA: 'SOURCED',
  ESTIMATED_DATA: 'ESTIMATED',
  SIMULATED_DATA: 'SIMULATED',
}
