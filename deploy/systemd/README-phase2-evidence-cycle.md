# Phase 2 evidence-cycle systemd units

These units are deployment definitions only. Nothing in this directory enables,
starts, restarts, or deploys them.

The evidence-cycle service runs the bounded read-only Phase-2 collection step:
prospective quotes, neutral position observations, bounded transaction
reinspection, strict prestate verification, reconciliation/calibration status,
and an opt-in compact non-qualified progress record.

Operational dependencies:

- `PIO_PHASE2_POSITION_POOL` and RPC configuration must come from
  `/etc/pio/pio.env`.
- The deployed executor must include the reviewed env-only Phase-2 commands.
- The deployed state reader must include the reviewed combined single-slot pool
  and position capture changes.
- The detector cursor is unrelated to this service and must never be changed by
  enabling this timer.
- Do not enable `pio-phase2-position-observer.timer` at the same time as this
  timer. The evidence cycle already performs that position-observation stage.
- The higher-frequency research quote timer may remain separate when additional
  prospective quote density is desired.

Before any production enablement, the active detector batch must have committed,
the production-local source capture/import work from Issue #6 must be resolved,
and the selective deployment preflight must pass without overwriting local
production fixes.
