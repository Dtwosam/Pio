# PAPER scheduler deployment

These templates run Pio's PAPER-only scheduler as a systemd oneshot every five
minutes. The scheduler itself uses a SQLite lease and deterministic time-bucket
tick IDs, so delayed/repeated timer invocations do not create duplicate paper
mutations.

## Install

1. Install Pio at `/opt/pio` and create the Python virtualenv at
   `/opt/pio/python-learner/.venv`.
2. Copy `.env.example` to `/etc/pio/pio.env` and fill in at least
   `SOLANA_RPC_URL` plus the normal Pio database/API settings.
3. Ensure the service user can write the directory containing
   `PIO_DATABASE_PATH`.
4. Copy the unit files to `/etc/systemd/system/`.
5. Reload and enable a timer for the paper account:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pio-paper@paper.timer
```

Inspect health with:

```bash
sudo systemctl status pio-paper@paper.timer
journalctl -u pio-paper@paper.service
cd /opt/pio/python-learner
.venv/bin/pio paper-scheduler-status --account paper
```

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
