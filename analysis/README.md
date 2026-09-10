# analysis/

Turns SUMO output into the project's reported figures.

| Module | Question it answers |
|---|---|
| `replay_diff.py` | How much travel time did the alternative policy recover? |
| `delay_attribution.py` | Where did the ambulance lose time, and to what cause? |
| `intersection_ranking.py` | Which junctions would benefit most from intervention? |

All stubs — they need simulation output that does not exist yet. The method for
each is written out in its module docstring, including the judgement calls that
have to be made and recorded.

## Two rules

**Everything is computed from simulation output.** No function here produces a
number from an assumption.

**Mismatched runs are refused, not compared.** `replay_diff` verifies both runs
share a `config_hash` and a `random_seed` and differ only in policy, raising
otherwise. A travel-time difference between two subtly different scenarios looks
exactly like a counterfactual result once it reaches a chart — and by then the
discrepancy is invisible.
