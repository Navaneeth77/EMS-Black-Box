# frontend/

React + TypeScript + Vite + Three.js + React Three Fiber + Drei + Tailwind.

```bash
npm install
npm run dev        # http://localhost:5173
npm run build
npm run typecheck
```

`/api` is proxied to `http://127.0.0.1:8000` in development (`vite.config.ts`),
so the browser stays on one origin — which the Phase 6 WebSocket stream inherits.

## The rule this app exists under

**Render simulation state; do not produce it.**

Vehicle positions come from the backend, which gets them from SUMO. The frontend
never invents, extrapolates or animates motion the simulation did not report.
Interpolating between two received frames for display smoothness is fine;
continuing motion past the newest frame is not, and no display-side value may
feed back into a reported number.

The picture is meant to be evidence of what the model produced. That stops being
true as soon as the browser fills a gap on its own initiative.

## Layout

| Path | Contents |
|---|---|
| `src/api/` | Backend client |
| `src/types/` | Types mirroring the backend Pydantic models |
| `src/scene/` | R3F scene — empty; Phase 6, see its README |
| `src/App.tsx` | Status page: frontend running + real backend health |

## Not built yet

The 3D scene, run controls and the results view. `App.tsx` shows an explicit
"no simulation data" state rather than mocked figures — a sample chart rendered
in the same UI as a measurement gets read as one, and screenshots outlive their
caveats. See `docs/DATA_INTEGRITY.md`.
