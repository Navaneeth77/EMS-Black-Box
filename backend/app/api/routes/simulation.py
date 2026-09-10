"""Simulation control endpoints.

Deliberately empty of behaviour. The routes that will live here — start a run,
step it, fetch results, launch a counterfactual replay — all return numbers, and
a route that returns a number before the simulation exists would return a made-up
one. The shape of these endpoints is also not yet knowable: it should follow from
what the TraCI loop actually produces, not from a guess made in advance.

Planned surface (see docs/ARCHITECTURE.md and docs/ROADMAP.md):

    POST   /api/simulation/runs            start a run from a saved config + seed
    GET    /api/simulation/runs/{run_id}   run status and metadata
    POST   /api/simulation/runs/{run_id}/counterfactual
                                           replay the same run under another policy
    GET    /api/simulation/runs/{run_id}/results
                                           travel times, delay attribution, ranking
    WS     /api/simulation/runs/{run_id}/stream
                                           per-step state for the 3D view

Every one of these carries a config hash and a random seed, so that any figure it
returns can be regenerated exactly.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/simulation", tags=["simulation"])

# No routes are registered yet. Adding them belongs to the roadmap phase that
# first produces real simulation output.
