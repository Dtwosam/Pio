# Pio

Adaptive Meteora DLMM liquidity bot with a Rust execution/risk layer and a Python data/learning layer.

## Read first

- `docs/SOURCE_OF_TRUTH.md`
- `docs/BUILD_PHASES.md`
- `docs/ARCHITECTURE.md`
- `docs/DATA_PIPELINE.md`

## Current status

- Phase 0: foundation scaffold complete
- Phase 1: market-data engine started
- Default mode: `PAPER`
- Live signing: not implemented yet

## Components

### `rust-executor/`
Hard risk boundary and, later, Solana/Meteora transaction execution. Python never owns the signing key.

### `python-learner/`
Meteora data collection, storage, backtesting, feature engineering and model training.

## Run the collector

```bash
cd python-learner
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pio collect-once
```

The default SQLite database is written to `data/pio.db` relative to the working directory.

## Safety

20% daily return is tracked only as an aspirational benchmark. The system is not allowed to increase risk simply to chase it. A no-trade day is valid.
