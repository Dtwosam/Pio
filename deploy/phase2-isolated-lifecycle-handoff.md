# Phase 2 isolated lifecycle handoff

Use `deploy/tools/check_phase2_isolated_lifecycle_handoff.py` when the local
Phase-2 machine state is unclear and you want one zero-RPC answer for the next
guarded step.

The handoff composes the existing reviewed activation, smoke, timer-readiness,
and operator-status checks. It does not replace their safety rules.

## Boundary

The handoff is read-only. It:

- makes no Solana or Jupiter RPC calls;
- performs no database writes;
- performs no systemd control;
- performs no daemon reload;
- does not move detector cursors;
- does not evaluate or perform phase promotion;
- does not touch capital.

## State progression

A normal deployment progresses through these states:

1. `RUNTIME_NOT_READY`
   - next action: `STAGE_REVIEWED_RUNTIME`
   - tool: `stage_phase2_isolated_runtime.py`
2. `SYSTEMD_UNITS_NOT_READY`
   - next action: `INSTALL_REVIEWED_UNITS`
   - tool: `install_phase2_isolated_systemd_units.py`
3. `DETECTOR_ACTIVATION_READY`
   - next action: `ACTIVATE_PRESTATE_STREAMS_AND_DETECTOR`
   - tool: `activate_phase2_isolated_detector.py`
4. `SMOKE_REQUIRED`
   - next action: `RUN_ONE_SHOT_SMOKE`
   - tool: `run_phase2_isolated_smoke.py`
5. `TIMER_ACTIVATION_READY`
   - next action: `ACTIVATE_EVIDENCE_TIMER`
   - tool: `activate_phase2_isolated_timer.py`
6. `RUNNING_HEALTHY`
   - next action: `MONITOR_ZERO_RPC_STATUS`
   - tool: `check_phase2_isolated_operator_status.py`

If prerequisites are incomplete, the handoff returns a categorical blocker list
rather than guessing a mutation.

## Rate-limit recovery

When repeated provider rejection is active and the timer is already paused, the
handoff returns `RATE_LIMIT_PAUSED` and explicitly keeps the recurring timer
paused. It does not recommend a smoke or resume attempt until the active
provider incident has cleared.

If a running timer still requires the reviewed pause action, the handoff returns
`RATE_LIMIT_PAUSE_REQUIRED` and points at
`autopause_phase2_isolated_timer.py`.

A fresh one-shot smoke remains required before a paused timer can be resumed.
