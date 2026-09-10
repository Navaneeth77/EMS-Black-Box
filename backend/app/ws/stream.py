"""Simulation state streaming over WebSocket.

Stub. The transport is straightforward; the contract is the part that needs care,
so it is written down here before any code depends on it.

Intended shape once the TraCI loop exists:

* The TraCI loop advances SUMO one step and produces a state frame. The frame is
  the authoritative record of that step: simulation time, step number, vehicle
  positions/speeds/types, traffic-light phases, queue lengths.
* This module serialises frames and fans them out to connected clients. It never
  synthesises a frame, and it never interpolates between frames — a client that
  wants smooth motion interpolates locally, for display only.
* Every frame carries ``step`` and ``sim_time_s`` so a client can tell dropped
  frames from a paused simulation, and so nothing downstream has to infer timing
  from wall-clock arrival.
* Backpressure is resolved by dropping frames, never by inventing or averaging
  them. A gap in the picture is honest; a smoothed-over gap is not.

Frames are read-only. Control actions (start, pause, set policy) go through the
REST routes in ``app.api.routes.simulation`` so that every state change is
recorded against a run identifier.
"""

from __future__ import annotations

__all__: list[str] = []
