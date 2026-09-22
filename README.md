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

Current simulator fidelity is `DISCRETE_COMPLETED_BIN_V1`.

It models completed-bin inventory conversion and IL. It does **not** claim exact hypothetical active-bin fills or fee attribution yet.

## Python commands

```bash
cd python-learner
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pio collect-once
pio data-status
pio backtest-inventory --pool <POOL_ADDRESS> --capital 100
```

The inventory backtest uses zero simulated fees unless position-attributable fees are explicitly supplied.

## Rust read-only chain inspection

The Rust layer now pins Meteora's official `commons` integration library.

```bash
cd rust-executor

cargo run -- inspect-pool <RPC_URL> <POOL_ADDRESS> 1
cargo run -- inspect-position <RPC_URL> <POSITION_ADDRESS>
```

Pool JSON can be piped into Python storage:

```bash
cargo run -- inspect-pool <RPC_URL> <POOL_ADDRESS> 1 \
  | ../python-learner/.venv/bin/pio ingest-chain-snapshot
```

Position JSON can be piped the same way:

```bash
cargo run -- inspect-position <RPC_URL> <POSITION_ADDRESS> \
  | ../python-learner/.venv/bin/pio ingest-position-snapshot
```

These commands are read-only and require no wallet private key.

## Components

### rust-executor/

Hard risk boundary, read-only Solana/Meteora state inspection, and later transaction execution. Python never owns the signing key.

### python-learner/

Meteora Data API ingestion, on-chain snapshot storage, data quality, candidate generation, simulation, validation, feature engineering and model training.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
