"""EMS Black Box simulation package.

Owns everything between a raw OpenStreetMap extract and a stream of simulation
state: network construction, the TraCI control loop, traffic-signal policies and
the ambulance trip.

The package is the *only* place vehicle motion is produced. Backend and frontend
consume what it emits.
"""

__version__ = "0.1.0"
