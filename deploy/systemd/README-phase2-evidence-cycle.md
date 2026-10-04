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
  The isolated evidence service/timer now declare systemd conflicts with the
  legacy position-observer service/timer and the legacy full evidence-cycle
  service/timer, so duplicate 15-minute Solana collection fails closed instead
  of consuming redundant RPC capacity.
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

`SOLANA_WS_URL` may be supplied explicitly. The standalone
`pio-phase2-account-watch` binary derives `wss://` from `SOLANA_RPC_URL` when
needed, without placing the credential-bearing URL in argv or emitted
notification records. The canonical `meteora-executor` remains byte-for-byte
unchanged for sealed Phase-7 source lineage.

The stream is reconnectable rather than a polling loop. If an unbounded
WebSocket session ends, the Python worker exits nonzero and systemd may restart
it according to the unit policy.


## Isolated add detector

Use `pio-phase2-isolated-add-detector.service` with the isolated prestate
stream instead of restarting the legacy `pio-phase2-add-detector.service`.

The isolated detector preserves the existing detector cursor and evidence
databases under `/opt/pio/data`, but executes the detector script and Rust
executor from `/opt/pio-phase2-runtime/current`. The Python console script
still comes from the existing virtual environment while `PYTHONPATH` points
at the isolated reviewed learner source.

The isolated unit conflicts with the legacy detector and only restarts after
process failure. RPC credentials remain in `/etc/pio/pio.env`; they are not
placed in argv or unit literals. The detector's reviewed rate-limit handling
retains its cursor and stops the remaining batch after provider rejection
rather than burning calls across the backlog.

The intended topology is:

1. `pio-phase2-isolated-prestate-stream.service` maintains prospective
   prestates from account-change notifications.
2. `pio-phase2-isolated-add-detector.service` discovers standalone adds and
   consumes those prestates without advancing its cursor on failed batches.
3. `pio-phase2-isolated-evidence-cycle.timer` periodically performs bounded
   neutral observations, reinspection, strict verification, and local
   reconciliation/calibration.

Installing unit files is not authorization to enable or start them.


### Multi-pool stream coverage

The detector pool set is explicit through
`PIO_PHASE2_DETECTOR_POOLS=ADDRESS:SECONDS,...`. With the variable unset,
the legacy two-pool coverage and cadences remain unchanged.

For isolated operation, use one
`pio-phase2-isolated-prestate-stream@<POOL>.service` instance per detector
pool. The reviewed isolated detector unit declares both default stream
instances as dependencies and pins the same default pool/cadence set. This
keeps every scanned pool paired with event-driven prospective prestate
coverage without reintroducing blind HTTP polling.

The template's `%i` value is only the public pool address. RPC and WebSocket
credentials continue to come from `/etc/pio/pio.env` and are never embedded
in the instance name, argv, or unit text.
