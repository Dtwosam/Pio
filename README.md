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
- Phase 1: implemented; extended live validation pending
- Phase 2: in progress
- Default mode: PAPER
- Live signing: not implemented

Current simulator fidelity is DISCRETE_COMPLETED_BIN_V1. It models completed-bin inventory conversion and IL, but does not claim exact active-bin fills or position fee attribution yet.

## Useful commands

    cd python-learner
    python -m venv .venv
    source .venv/bin/activate
    pip install -e '.[dev]'

    pio collect-once
    pio data-status
    pio backtest-inventory --pool <POOL_ADDRESS> --capital 100

The inventory backtest intentionally uses zero simulated fees unless position-attributable fees are supplied by code. It must not be read as full profit performance.

## Components

### rust-executor/
Hard risk boundary and later Solana/Meteora transaction execution. Python never owns the signing key.

### python-learner/
Meteora data collection, storage, data quality, candidate generation, simulation, validation, feature engineering and model training.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
