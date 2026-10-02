# Phase 2 isolated production rollout

This runbook deploys the reviewed Phase-2 collection stack without modifying
the dirty production checkout at `/opt/pio`.

The rollout is intentionally split into read-only gates and explicit mutation
steps. No step promotes Phase 2, authorizes PAPER/LIVE capital, signs or submits
transactions, or changes the detector cursor outside the reviewed detector
process.

## Invariants

- Never use `git pull`, `git checkout`, `git reset`, `git restore`, or
  `git clean` inside `/opt/pio`.
- The legacy services
  `pio-phase2-add-detector.service` and
  `pio-phase2-prestate-watch.service` must remain inactive and disabled.
- The isolated runtime lives under `/opt/pio-phase2-runtime`.
- RPC credentials stay in `/etc/pio/pio.env`; they are never passed on argv.
- The event-driven prestate streams replace blind successful HTTP polling.
- The bounded evidence timer is not enabled until one manual smoke cycle has
  produced a verified receipt backed by immutable database evidence.

## 1. Prepare a pinned side runtime

Clone the reviewed repository commit outside `/opt/pio`, then run:

```bash
sudo python3 deploy/tools/prepare_phase2_isolated_runtime.py \
  --source-tree /path/to/reviewed/source
```

Require `compat_ready=true`.

Then prepare the side tree:

```bash
sudo python3 deploy/tools/prepare_phase2_isolated_runtime.py \
  --source-tree /path/to/reviewed/source \
  --prepare
```

Require `runtime_ready=true`.

This applies only the reviewed production compatibility patch inside the side
tree and builds both reviewed Rust binaries. It does not touch `/opt/pio`,
systemd, RPC, or detector state.

## 2. Atomically stage the runtime

Preflight:

```bash
sudo python3 deploy/tools/stage_phase2_isolated_runtime.py \
  --source-tree /path/to/reviewed/source
```

Then stage:

```bash
sudo python3 deploy/tools/stage_phase2_isolated_runtime.py \
  --source-tree /path/to/reviewed/source \
  --apply
```

The prepared tree is copied into
`/opt/pio-phase2-runtime/releases/<PINNED_SOURCE_HEAD>` and
`/opt/pio-phase2-runtime/current` is atomically repointed to that release.

No service is controlled.

## 3. Install isolated systemd unit files

Preflight:

```bash
sudo python3 deploy/tools/install_phase2_isolated_systemd_units.py \
  --source-tree /opt/pio-phase2-runtime/current
```

The current symlink itself is not accepted as a source by the installer. If the
tool is invoked directly, pass the resolved release directory:

```bash
RUNTIME_RELEASE="$(readlink -f /opt/pio-phase2-runtime/current)"

sudo python3 deploy/tools/install_phase2_isolated_systemd_units.py \
  --source-tree "$RUNTIME_RELEASE"
```

Require every unit status to be either `READY_CREATE` or `ALREADY_TARGET`.

Install exact unit files:

```bash
sudo python3 deploy/tools/install_phase2_isolated_systemd_units.py \
  --source-tree "$RUNTIME_RELEASE" \
  --apply
```

This copies unit files only. It does not daemon-reload, enable, start, stop, or
restart anything.

## 4. Run the read-only activation preflight

```bash
sudo python3 deploy/tools/check_phase2_isolated_activation.py
```

Require `activation_ready=true`.

The gate verifies:

- the staged runtime and both Rust executables;
- exact installed unit bytes;
- `SOLANA_RPC_URL` and `PIO_PHASE2_POSITION_POOL` are configured without
  printing their values;
- the evidence pool belongs to the exact detector topology in the installed
  detector unit;
- `pio.db`, `phase2-prestate-cache.db`, and
  `phase2-add-detector-state.json` are regular files;
- detector state is valid JSON with nonempty cursors for every configured pool;
- legacy collectors are inactive and disabled;
- all new isolated services/timers are still inactive, and the detector/timer
  are disabled.

This gate makes no RPC calls and performs no service control.

## 5. Activate only prestate streams and the add detector

Dry run:

```bash
sudo python3 deploy/tools/activate_phase2_isolated_detector.py
```

Then, after reviewing the gate:

```bash
sudo python3 deploy/tools/activate_phase2_isolated_detector.py --apply
```

The executor:

1. reruns the activation preflight;
2. daemon-reloads systemd;
3. starts and verifies both event-driven prestate streams;
4. enables, starts, and verifies the isolated add detector;
5. rolls back detector/stream changes if a later step fails.

The bounded evidence timer is deliberately untouched.

Starting these services may initiate RPC traffic. The tool itself never places
RPC credentials on argv.

## 6. Run the read-only smoke-readiness gate

```bash
sudo python3 deploy/tools/check_phase2_isolated_smoke_readiness.py
```

Require `smoke_ready=true`.

At this point:

- both event-driven prestate streams are active;
- the isolated detector is active and enabled;
- legacy collectors remain quiescent;
- the evidence service is inactive;
- the evidence timer is inactive and disabled;
- runtime/configuration/data/cursor checks remain valid.

No RPC call is made by this gate.

## 7. Run exactly one manual bounded evidence smoke

Dry run:

```bash
sudo python3 deploy/tools/run_phase2_isolated_smoke.py
```

Then explicitly execute one smoke:

```bash
sudo python3 deploy/tools/run_phase2_isolated_smoke.py --apply
```

This step may perform RPC reads and writes observed evidence to
`/opt/pio/data/pio.db`.

A smoke is accepted when:

- the output is valid structured evidence-cycle JSON;
- no stage is `FAILED` or `SKIPPED`;
- no `RPC_RATE_LIMITED` or `RPC_CIRCUIT_OPEN` category appears;
- a compact progress record was persisted.

`PARTIAL` stages are allowed because Phase-2 evidence gaps are expected before
promotion readiness.

A successful run atomically writes:

`/opt/pio/data/phase2-isolated-smoke-receipt.json`

The receipt contains no RPC credentials and does not authorize phase promotion.

## 8. Verify timer readiness

```bash
sudo python3 deploy/tools/check_phase2_isolated_timer_readiness.py
```

Require `timer_ready=true`.

This read-only gate verifies that the smoke receipt:

- belongs to the currently staged runtime;
- is recent;
- contains no failed/skipped/rate-limited stages;
- references a real immutable
  `PHASE2_EVIDENCE_CYCLE_PROGRESS_V1` row in `pio.db`;
- references `COLLECTION_SUCCESS` or `COLLECTION_PARTIAL`;
- remains `qualified=false`, `actionable=false`,
  `live_authorized=false`, with no phase-promotion evaluation or mutation.

## 9. Enable the bounded evidence timer

Dry run:

```bash
sudo python3 deploy/tools/activate_phase2_isolated_timer.py
```

Then:

```bash
sudo python3 deploy/tools/activate_phase2_isolated_timer.py --apply
```

The executor reruns the timer readiness gate, daemon-reloads systemd, enables
and starts only `pio-phase2-isolated-evidence-cycle.timer`, verifies the timer,
and disables/stops it again if verification fails.

Enabling the timer may trigger future RPC-backed evidence cycles. It does not
change Phase-2 thresholds or authorize any PAPER/LIVE action.

## Expected steady-state topology

Active/enabled:

- `pio-phase2-isolated-add-detector.service`
- two `pio-phase2-isolated-prestate-stream@<POOL>.service` instances, started
  as detector dependencies;
- `pio-phase2-isolated-evidence-cycle.timer`

Inactive/disabled:

- `pio-phase2-add-detector.service`
- `pio-phase2-prestate-watch.service`

The one-shot evidence service becomes active only when the timer runs it.

## Credit-efficiency behavior

The steady-state design preserves useful capacity rather than imposing a quota:

- prestate capture is driven by account-change notifications instead of blind
  successful HTTP polling;
- complete pool-position discovery can be reused for one hour by default while
  position observation itself continues normally;
- truncated discovery is never cached;
- confirmed Solana RPC rate limits stop the current RPC stage and open a
  cycle-local circuit that suppresses later rejected network work;
- the add detector retains its cursor on failed/rate-limited batches;
- healthy event streams are not throttled; reconnect backoff applies only after
  stream exit.

No daily/monthly RPC ceiling is introduced.
