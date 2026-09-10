"""Phase 4: baseline calibration and uncertainty.

Phase 3 produced a baseline that runs. This package asks how much to trust it.

The distinction it maintains throughout: **quantifying uncertainty is not the
same as removing it.** Running five seeds tells you how much the answer moves
when only the random stream changes; it says nothing about whether the demand
resembles Bengaluru. Nothing here makes the simulation empirically calibrated,
and the language in every output is chosen so that it cannot be read as claiming
otherwise.
"""
