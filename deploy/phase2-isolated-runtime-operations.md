# Phase 2 isolated runtime operations

The isolated Phase-2 runtime has two distinct surfaces:

- **Runtime release**: the pinned source commit staged below
  `/opt/pio-phase2-runtime/releases/`. Systemd services execute only runtime
  collectors and the standalone rate-limit autopause guard from this tree.
- **Control checkout**: a reviewed current Pio checkout used to run deployment,
  readiness, staging, installation, smoke, health, pause, and activation tools.

Do not use deployment/readiness tools from
`/opt/pio-phase2-runtime/current/deploy/tools` as the control plane. The
runtime release is intentionally pinned to an earlier reviewed source commit,
while the control checkout contains the later validator that authenticates that
pin.

The automatic failure path is the exception by design:
`pio-phase2-isolated-rate-limit-pause.service` executes only
`autopause_phase2_isolated_timer.py` from the runtime release. That script is
self-contained, reads persisted Phase-2 evidence telemetry from SQLite in
read-only mode, loads no RPC environment, makes no network calls, and may
disable only `pio-phase2-isolated-evidence-cycle.timer` after repeated **fresh**
provider rate-limit evidence.

This separation prevents a runtime self-validation loop from interfering with
the emergency pause path while keeping all deployment mutations behind the
reviewed control checkout.


## Zero-RPC operator status

Use the control checkout to inspect the whole isolated Phase-2 operating state
without spending provider credits:

```bash
python3 deploy/tools/check_phase2_isolated_operator_status.py
```

The aggregate status combines the running timer-health view with the local
RPC-efficiency/autopause view. It reads only local files, SQLite, safe
non-secret environment metadata, and systemd state. It does not call RPC,
write the database, or control services.

The top-level `state` is intentionally categorical:

- `HEALTHY`: topology is complete, the timer is running, the latest cycle is
  fresh, and there is no active provider-rate-limit incident.
- `RATE_LIMIT_PAUSE_REQUIRED`: repeated fresh provider rejection is present
  while the recurring timer can still schedule more cycles.
- `RATE_LIMIT_PAUSED`: repeated fresh provider rejection is present and the
  recurring timer is already disabled/inactive.
- `TOPOLOGY_NOT_READY`: runtime, installed-unit, environment, detector,
  stream, data-state, or legacy-collector isolation checks are not all ready.
- `TIMER_NOT_RUNNING`: the non-rate-limited topology is ready but the bounded
  recurring evidence timer is not both active and enabled.
- `COLLECTION_ATTENTION`: topology/timer are running but recent collection is
  stale, failed, or otherwise not healthy.

Collector attempt counts are explicitly not Helius/provider credit counts.
Pio does not infer provider billing from local attempts.
