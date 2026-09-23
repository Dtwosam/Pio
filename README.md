# Pio

Adaptive Meteora DLMM liquidity bot with a Rust execution/risk layer and a Python data/learning layer.

## Read first

- docs/SOURCE_OF_TRUTH.md
- docs/BUILD_PHASES.md
- docs/ARCHITECTURE.md
- docs/DATA_PIPELINE.md
- docs/DATA_SCHEMA.md
- docs/SIMULATOR_DESIGN.md
- docs/PHASE2_VALIDATION_RUNBOOK.md
- docs/PHASE3_POLICY.md

## Current status

- Phase 0: complete
- Phase 1: implemented; extended live validation pending
- Phase 2: standard-SPL simulator implementation complete; real promotion evidence pending
- Phase 3: deterministic policy + persisted validation/promotion workflow implemented; real promotion evidence pending
- Phase 4: reproducible no-lookahead ML challenger workflow implemented; not promoted
- Phase 5: chain-driven, idempotent multi-position paper trader implemented; unattended live orchestration/validation pending
- Default mode: PAPER
- Live signing: not implemented

Current research paths:
- `DISCRETE_COMPLETED_BIN_V1`: OHLC inventory/IL studies.
- `SMALL_LP_CHAIN_PATH_V2`: standard-SPL fixed-share counterfactual replay over real chain snapshots.
- `OBSERVATION_BOUNDARY_REBALANCE_V1`: out-of-range withdraw/re-enter lifecycle replay.

Phase 2 now has source-backed implementations for liquidity shares, fees, rewards, composition fees, rebalance lifecycle, Solana receipt costs, add execution bounds and rebalance execution guards. Promotion is evidence-driven: code existence alone does not make a capability ready.

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
pio phase2-evidence
pio phase2-work-queue

pio screen-pools
pio baseline-walk-forward --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --network-cost-y-atomic <COST>
pio size-position --equity 10000 --cash 10000 --deployed 0 --drawdown-bps 0
pio manage-position --active-bin 100 --min-bin 95 --max-bin 105 \
  --holding-observations 12 --rebalances-done 0
pio phase3-plan --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --requested-quote <NOTIONAL> --equity <EQUITY> --cash <CASH> \
  --deployed <DEPLOYED> --drawdown-bps <BPS> \
  --network-cost-y-atomic <COST>

pio paper-create-account --account paper --cash 10000
pio paper-status --account paper
pio paper-open --event-key <KEY> --account paper --position <ID> --pool <POOL> \
  --policy-source DETERMINISTIC --strategy SPOT --min-bin <MIN> --max-bin <MAX> \
  --capital <QUOTE>
pio paper-observe --event-key-prefix <KEY> --position <ID> --active-bin <BIN> \
  --holding-observations <N> --mark <QUOTE> --estimated-exit-cost <QUOTE> \
  --rebalance-cost <QUOTE>
pio paper-performance --account paper --policy-source DETERMINISTIC

# Chain-valued paper path
pio paper-chain-bind --position <ID> --observed-at <TIME> \
  --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --token-y-quote-per-atomic <QUOTE_RATE>
pio paper-chain-value --position <ID> --observed-at <TIME> \
  --token-y-quote-per-atomic <QUOTE_RATE>
pio paper-chain-observe --position <ID> --observed-at <TIME> \
  --token-y-quote-per-atomic <QUOTE_RATE> --rebalance-cost <QUOTE>
pio paper-chain-run --run-id <RUN> --observed-at <TIME> --file <ITEMS_JSON>

# Persisted promotion / ML workflow
pio phase-status
pio phase3-validate --file <POOLS_JSON>
pio ml-train-csv --file <DATASET_CSV> --model-id <MODEL> \
  --dataset-version <VERSION> --artifact-dir <DIR>
pio ml-offline-evaluate-csv --file <DATASET_CSV> --model-id <MODEL>
pio ml-start-paper --model-id <MODEL>
pio ml-model-status --model-id <MODEL>
```

Real execution/calibration commands:

```bash
pio collect-position-history --position <POSITION>
pio add-execution --position <POSITION>
pio rebalance-execution --position <POSITION>
pio transaction-costs --position <POSITION>
pio composition-prestate --position <POSITION>
pio reconcile-composition --position <POSITION>
```

The work queue emits concrete read-only Rust commands when an existing sample can be completed. If historical prestate cannot be proven, it says so instead of reconstructing it approximately.

Phase 3 commands are research/policy tools. They do not build, sign or send live transactions. A pool must pass the fail-closed safety screen, a deterministic range/strategy must pass trailing replay, capital must fit sizing limits, and Phase 2 evidence must be promoted before the policy can authorize entry.

ML v1 is research-only: it learns from all replay-valid candidate actions at each decision point, uses time-ordered holdout validation, compares challengers against the deterministic baseline, and cannot become champion without qualified paper evidence. Paper commands mutate only the local paper ledger; they never sign or send a Solana transaction.

## Rust read-only inspection

```bash
cd rust-executor

cargo run -- inspect-pool <RPC_URL> <POOL_ADDRESS> 1
cargo run -- inspect-position <RPC_URL> <POSITION_ADDRESS>
cargo run -- inspect-transaction-events <RPC_URL> <SIGNATURE>
cargo run -- verify-prestate <RPC_URL> <SIGNATURE> \
  <CAPTURE_START_SLOT> <CAPTURE_END_SLOT> <POOL_ACCOUNT> <BIN_ARRAY_ACCOUNT>
```

Rust JSON can be piped into:
- `pio ingest-chain-snapshot`
- `pio ingest-position-snapshot`
- `pio ingest-transaction-events`
- `pio ingest-prestate-verification`

These commands are read-only and require no wallet private key.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.
