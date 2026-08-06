# Real-time screening fixtures

Two repositories for S-2.8, and the pairing is the point.

| Directory | Role | Expected |
|---|---|---|
| `flight_controller/` | genuine markers in all four categories | **refused** |
| `task_tracker/` | every innocent word a naive detector would flag | **cleared** |

Refusing the first proves the detector works. Clearing the second proves it
discriminates, which is the harder and more valuable claim — see ADR 006.
