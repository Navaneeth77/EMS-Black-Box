"""Export of SUMO simulation state into a form a 3D scene can render.

Nothing in this package simulates. It reads committed SUMO artefacts — the
network, the traffic-light programs, an FCD trajectory recording — and writes
them out in scene coordinates. Every value it emits is either read from those
artefacts or derived from them by a documented rule, and anything derived is
labelled.
"""
