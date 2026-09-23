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
- Phase 2: advanced implementation; real calibration still pending
- Default mode: PAPER
- Live signing: not implemented

Current research paths:
- `DISCRETE_COMPLETED_BIN_V1`: OHLC inventory/IL studies.
- `SMALL_LP_CHAIN_PATH_V2`: standard-SPL fixed-share counterfactual replay over real chain snapshots.
- `OBSERVATION_BOUNDARY_REBALANCE_V1`: out-of-range withdraw/re-enter lifecycle replay.

Chain replay now models:
- exact Q64 bin prices and liquidity shares
- Meteora fee checkpoints
- effective reward checkpoints
- active-bin composition fees
- real token program / dynamic fee state
- counterfactual dilution guards
- reward campaign guards
- observation-boundary rebalances

Phase 2 remains fail-closed until real composition-fee reconciliation and broader slippage calibration are complete.

## Main research commands

```bash
cd python-learner
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pio collect-once
pio data-status
pio replay-chain --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --min-bin <MIN> --max-bin <MAX> --strategy SPOT --observations 12

pio replay-rebalance --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --half-width 5 --strategy CURVE --observations 24

pio scan-chain --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y>
pio reconcile-position --position <POSITION>
pio reconcile-corpus
```

Collect real position lifecycle/calibration data:

```bash
pio collect-position-history --position <POSITION>
pio composition-labels --position <POSITION>
pio transaction-costs --position <POSITION>
pio add-execution --position <POSITION>
```

## Rust read-only inspection

```bash
cd rust-executor

cargo run -- inspect-pool <RPC_URL> <POOL_ADDRESS> 1
cargo run -- inspect-position <RPC_URL> <POSITION_ADDRESS>
cargo run -- inspect-transaction-events <RPC_URL> <SIGNATURE>
```

Rust JSON can be piped into:
- `pio ingest-chain-snapshot`
- `pio ingest-position-snapshot`
- `pio ingest-transaction-events`

These commands are read-only and require no wallet private key.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
