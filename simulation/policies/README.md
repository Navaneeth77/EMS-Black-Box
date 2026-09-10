# simulation/policies/

Artifacts for signal-policy runs. The policy **code** lives in
`simulation/ems_sim/policies/` alongside the rest of the importable library:

| Module | Contents |
|---|---|
| `state.py` | The `NORMAL → REQUESTED → PRIORITY_ACTIVE → CLEARING` machine and its transition log |
| `tls_map.py` | Which traffic lights a route passes, and which of their links it uses |
| `base.py` | Shared preemption mechanics — phase selection, minimum green, interphase handling |
| `policies.py` | NORMAL, EMS_NEXT, EMS_ROLLING, EMS_FULL_PREEMPTION |

Policies never write a signal state string. They select among the phases
netconvert generated, so conflicting greens are structurally impossible rather
than merely checked for. See `docs/EMS_SIGNAL_POLICIES.md`.
