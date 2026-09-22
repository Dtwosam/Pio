# Pio

Adaptive Meteora DLMM liquidity bot with a Rust execution/risk layer and a Python data/learning layer.

## Read first

- docs/SOURCE_OF_TRUTH.md
- docs/BUILD_PHASES.md
- docs/ARCHITECTURE.md
- docs/DATA_PIPELINE.md
- docs/DATA_SCHEMA.md
- docs/SIMULATOR_DESIGN.md

## Current status

- Phase 0: complete
- Phase 1: implemented; live API validation pending
- Phase 2: started
- Default mode: PAPER
- Live signing: not implemented

Phase 2 currently includes verified DLMM bin-price math, market feature generation and range-path labels. Full DLMM PnL simulation is not considered valid until reconciled against real Meteora position outcomes.

## Components

### rust-executor/
Hard risk boundary and later Solana/Meteora transaction execution. Python never owns the signing key.

### python-learner/
Meteora data collection, storage, data quality, backtesting, feature engineering and model training.

## Run the collector

    cd python-learner
    python -m venv .venv
    source .venv/bin/activate
    pip install -e '.[dev]'
    pio collect-once
    pio data-status

The default SQLite database is data/pio.db relative to the working directory.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
