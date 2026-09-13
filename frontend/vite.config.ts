import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // React must resolve to exactly one copy. @react-three/fiber ships its own
  // reconciler, and if Vite pre-bundles a second React the Canvas mounts
  // against a different dispatcher and every hook inside it throws
  // "Invalid hook call". Deduping three as well keeps drei and our own imports
  // sharing one WebGLRenderer class.
  resolve: {
    dedupe: ['react', 'react-dom', 'three'],
  },
  optimizeDeps: {
    include: ['react', 'react-dom', 'three', '@react-three/fiber', '@react-three/drei'],
  },
  server: {
    port: 5173,
    // Proxying /api keeps the browser on a single origin in development, which
    // sidesteps CORS entirely for the WebSocket stream that Phase 6 will add.
    // The backend also sets CORS headers, so direct calls work too.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
