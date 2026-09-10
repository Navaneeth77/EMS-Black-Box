"""EMS Black Box backend service.

Thin transport layer. This package owns HTTP/WebSocket concerns only; it holds
no traffic model of its own. Simulation lives in ``simulation/`` and analysis in
``analysis/``, and the backend forwards what those produce.

Keeping the boundary sharp is what makes "SUMO is the source of truth"
enforceable: if the backend cannot compute a vehicle position, it cannot
accidentally invent one.
"""

__version__ = "0.1.0"
