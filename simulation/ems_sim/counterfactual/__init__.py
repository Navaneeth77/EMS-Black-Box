"""Phase 5: EMS signal priority and counterfactual replay.

A counterfactual is only a counterfactual if everything except the intervention
is held identical. This package exists to enforce that rather than assume it: it
verifies scenario identity between paired runs, drives a signal policy through
TraCI, and computes differences only against the paired NORMAL run of the same
seed.
"""
