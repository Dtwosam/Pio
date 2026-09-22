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
- Phase 2: in progress with chain-backed replay
- Default mode: PAPER
- Live signing: not implemented

Pio now has two research fidelity paths:

- `DISCRETE_COMPLETED_BIN_V1`: OHLC-based inventory/IL studies.
- `SMALL_LP_CHAIN_PATH_V2`: standard-SPL counterfactual replay over repeated on-chain Meteora snapshots.

The chain path uses source-backed Meteora liquidity-share formulas, real bin inventory/supply, real fee checkpoints, deposit-time dynamic fee state, and explicit active-bin composition fees. It still fails closed where the historical path would be materially changed by the hypothetical LP.

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

Replay one explicit candidate over recent chain snapshots:

```bash
pio replay-chain \
  --pool <POOL_ADDRESS> \
  --amount-x <ATOMIC_X> \
  --amount-y <ATOMIC_Y> \
  --min-bin <MIN_BIN> \
  --max-bin <MAX_BIN> \
  --strategy SPOT \
  --observations 12
```

Compare a range/strategy grid without inventing a single profitability score:

```bash
pio scan-chain \
  --pool <POOL_ADDRESS> \
  --amount-x <ATOMIC_X> \
  --amount-y <ATOMIC_Y> \
  --observations 12 \
  --half-widths 0,1,2,5,10 \
  --center-offsets 0 \
  --strategies SPOT,CURVE,BID_ASK
```

Rejected candidates are returned with their fail-closed reason.

## Rust read-only chain inspection

The Rust layer pins Meteora's official `commons` integration library and is exercised by CI.

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

Meteora Data API ingestion, on-chain snapshot storage, data quality, candidate generation, chain replay, validation, feature engineering and model training.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
