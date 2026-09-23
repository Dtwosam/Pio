# PAPER scheduler deployment

These templates run Pio's PAPER-only scheduler as a systemd oneshot every five
minutes. The scheduler itself uses a SQLite lease and deterministic time-bucket
tick IDs, so delayed/repeated timer invocations do not create duplicate paper
mutations.

## Install

1. Install Pio at `/opt/pio` and create the Python virtualenv at
   `/opt/pio/python-learner/.venv`.
2. Build the read-only Rust executor once:
   `cd /opt/pio/rust-executor && cargo build --release`.
3. Create an unprivileged service account, for example:
   `sudo useradd --system --home /opt/pio --shell /usr/sbin/nologin pio`.
4. Copy `.env.example` to `/etc/pio/pio.env` and fill in at least
   `SOLANA_RPC_URL` plus the normal Pio database/API settings. Keep
   `PIO_RUST_EXECUTOR_BIN=rust-executor/target/release/meteora-executor`.
5. Ensure `pio` can write the directory containing `PIO_DATABASE_PATH`
   (the default deployment path is `/opt/pio/data`).
6. Copy the unit files to `/etc/systemd/system/`.
7. Reload and enable a timer for the paper account:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pio-paper@paper.timer
sudo systemctl enable --now pio-paper-health@paper.timer
```

Inspect health with:

```bash
sudo systemctl status pio-paper@paper.timer
sudo systemctl status pio-paper-health@paper.timer
journalctl -u pio-paper@paper.service
journalctl -u pio-paper-health@paper.service
cd /opt/pio/python-learner
.venv/bin/pio paper-scheduler-status --account paper
.venv/bin/pio paper-health --account paper --require-healthy
.venv/bin/pio paper-endurance-report --account paper
```

The health service exits non-zero when an account with open positions is
degraded or unhealthy. That gives systemd and external service monitors a
stable failure signal without adding wallet/signing capability. Sites that
already use a pager or monitoring agent can attach their normal systemd unit
failure alerting to `pio-paper-health@<account>.service`.

## Quotes

External quote refresh is opt-in. To let the scheduled tick refresh token-Y USD
quotes through Jupiter, set:

```bash
PIO_PAPER_SCHEDULER_EXTRA_ARGS=--refresh-jupiter-quotes
```

If it is omitted, Pio only uses already-persisted fresh quotes and waits
fail-closed when a required quote is stale or missing.

## Safety boundary

This service never receives or uses a wallet private key. It may call the Rust
executor only through the read-only `inspect-pool-env` command. Live signing
remains outside Phase 5.


## Phase 9 research progress snapshots

After the Phase 9 research tooling is deployed, enable the observational
progress timer:

```bash
sudo systemctl enable --now pio-phase9-progress.timer
```

Inspect it with:

```bash
sudo systemctl status pio-phase9-progress.timer
journalctl -u pio-phase9-progress.service
cd /opt/pio/python-learner
.venv/bin/pio phase9-progress --require-snapshot --require-integrity
```

The service runs `phase9-work-queue --persist-snapshot` once per hour. It
does not execute the emitted work-queue commands and does not require or store
an RPC URL. Persisted snapshots are append-only and omit shell commands.


## Phase 9 read-only source accumulation

To accumulate real Phase 9 source observations without manually invoking each
capture pass, enable:

```bash
sudo systemctl enable --now pio-phase9-source-capture.timer
```

Inspect it with:

```bash
sudo systemctl status pio-phase9-source-capture.timer
journalctl -u pio-phase9-source-capture.service
```

The timer activates every 70 minutes. The service runs
`phase9-source-capture-run --history-min-observation-interval-seconds 3600`.
That combination enforces at least one hour between persisted history
observations for the same pool while leaving margin for RPC/API execution
time. A too-soon run skips the history sample instead of inflating evidence
depth.

The service receives `SOLANA_RPC_URL` only through `/etc/pio/pio.env`.
It uses the read-only Rust inspection paths and official public-data
collection, writes only local research state under `/opt/pio/data`, and has
no wallet key, signing, submission, LIVE-policy or `live-submit` capability.

Optional bounded source-capture arguments can be supplied with
`PIO_PHASE9_SOURCE_CAPTURE_EXTRA_ARGS` in `/etc/pio/pio.env`. Do not use
that variable to reduce the history interval below the research cadence chosen
for the experiment.


## Phase 9 replay-aware research refresh

After the source-capture timer is enabled, the reproducible automatic research
families can be refreshed with a second timer:

```bash
sudo systemctl enable --now pio-phase9-research-refresh.timer
```

Inspect it with:

```bash
sudo systemctl status pio-phase9-research-refresh.timer
journalctl -u pio-phase9-research-refresh.service
```

The refresh timer starts 40 minutes after boot and then every 70 minutes, so a
normal paired deployment begins 25 minutes after the source-capture timer's
15-minute boot offset. That offset is longer than the source service's 20-minute
timeout. The service is also ordered after
`pio-phase9-source-capture.service` when both are active in the same systemd
transaction.

Both source capture and research refresh use the same 30-minute SQLite maintenance lease, so a manual invocation cannot overlap either scheduled maintenance pass. A busy invocation exits cleanly without running its underlying collector/research job, and an expired lease can be recovered.

It runs only `phase9-research-refresh-run`. That command requires verified
Phase 9 storage and a current Phase 8 promotion, skips replay-verified
automatic families, persists only changed research evidence and may persist a
ready research bundle. When a valid checksum-bound explicit research-input artifact already exists, the refresh may also recompute only the static-hedge and/or portfolio-allocation family whose current qualified evidence no longer replay-verifies; it never invents or mutates those assumptions. It does not run Phase 9 promotion, source RPC capture,
policy authorization, rollout, signing or transaction submission.

Optional flags may be supplied with
`PIO_PHASE9_RESEARCH_REFRESH_EXTRA_ARGS`. Keep promotion commands and any
future LIVE wiring outside this service.
