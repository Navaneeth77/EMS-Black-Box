/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Data-class colours. Measured and assumed values must never be
        // visually interchangeable in this UI - see docs/DATA_INTEGRITY.md.
        provenance: {
          verified: '#16a34a',   // VERIFIED_REAL_DATA
          sourced: '#0891b2',    // PUBLICLY_SOURCED_DATA
          estimated: '#d97706',  // ESTIMATED_DATA - deliberately warning-coloured
          simulated: '#7c3aed',  // SIMULATED_DATA
        },
      },
    },
  },
  plugins: [],
}
