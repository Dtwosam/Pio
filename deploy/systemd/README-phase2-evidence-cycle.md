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


## Event-driven prestate stream

For prospective composition-prestate evidence, prefer
`pio-phase2-isolated-prestate-stream.service` over the legacy continuous
`pio-phase2-prestate-watch.service`.

The isolated stream subscribes to the pool account over Solana WebSocket,
captures one initial pool baseline, and refreshes that baseline only after an
account-change notification. It writes the same
`phase2-prestate-cache.db/prestate_snapshots` schema consumed by the add
detector. It does not move the detector cursor and does not evaluate promotion.

The isolated evidence-cycle timer and the isolated prestate stream may run
together: the former performs bounded evidence work, while the latter maintains
prospective prestates. Do not run the legacy prestate watcher at the same time;
the isolated stream unit declares a systemd conflict with it.

`SOLANA_WS_URL` may be supplied explicitly. When it is absent, the executor
derives `wss://` from `SOLANA_RPC_URL` without placing the credential-bearing
URL in argv or emitted notification records.

The stream is reconnectable rather than a polling loop. If an unbounded
WebSocket session ends, the Python worker exits nonzero and systemd may restart
it according to the unit policy.
