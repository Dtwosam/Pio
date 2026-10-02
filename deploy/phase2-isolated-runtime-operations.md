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
