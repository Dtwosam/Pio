# Phase 2 Validation Runbook

Status: v1
Date: 2026-09-23

This runbook collects the real evidence required by Pio's fail-closed Phase 2 gate. It is read-only: no wallet private key or transaction signing is required.

## 1. Environment

From the repository root:

```bash
cd python-learner
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cd ..

export RPC_URL='https://your-solana-rpc'
```

Configure the Python database/API environment as documented for normal collection.

## 2. Keep fresh pool snapshots

Exact composition reconciliation cannot reconstruct historical prestate after the fact.

For each calibration pool, collect Rust pool snapshots frequently enough that future add-liquidity transactions have a nearby slot-bounded prestate:

```bash
(cd rust-executor && cargo run -- inspect-pool "$RPC_URL" <POOL_ADDRESS> 1) \
  | python-learner/.venv/bin/pio ingest-chain-snapshot
```

The snapshot contains capture start/end slots, the active BinArray address, raw on-chain fee/volatility state, effective reward checkpoints and the normal pool/bin state.

## 3. Collect position history

```bash
python-learner/.venv/bin/pio collect-position-history --position <POSITION_ADDRESS>
```

Then inspect the current work queue:

```bash
python-learner/.venv/bin/pio phase2-work-queue
```

The queue emits concrete shell commands for transaction decodes and exact prestate verification where possible.

## 4. Decode lifecycle transactions

For any `INSPECT_TRANSACTION`, `REINSPECT_TRANSACTION` or `REINSPECT_REBALANCE_TRANSACTION` task:

```bash
(cd rust-executor && cargo run -- inspect-transaction-events "$RPC_URL" <SIGNATURE>) \
  | python-learner/.venv/bin/pio ingest-transaction-events
```

This captures:
- AddLiquidity / CompositionFee / RemoveLiquidity / Rebalancing events;
- requested add-liquidity amounts and supported distribution metadata;
- rebalance min/max execution guards;
- network fee;
- compute units;
- success status.

## 5. Prove composition prestate

Find candidate prestate:

```bash
python-learner/.venv/bin/pio composition-prestate --position <POSITION_ADDRESS>
```

For an eligible candidate, verify the pool and active BinArray account gap using the exact capture slots and addresses from the output:

```bash
(cd rust-executor && cargo run -- verify-prestate "$RPC_URL" \
  <SIGNATURE> <CAPTURE_START_SLOT> <CAPTURE_END_SLOT> \
  <POOL_ADDRESS> <ACTIVE_BIN_ARRAY_ADDRESS>) \
  | python-learner/.venv/bin/pio ingest-prestate-verification \
      --snapshot-observed-at '<SNAPSHOT_OBSERVED_AT>' \
      --pool <POOL_ADDRESS>
```

The verifier rejects:
- target transactions at or before snapshot completion;
- intervening touches to the pool or active BinArray;
- same-slot ambiguity;
- missing target references.

A rejected historical candidate is not approximated. Collect another future sample.

## 6. Reconcile composition fees

```bash
python-learner/.venv/bin/pio reconcile-composition --position <POSITION_ADDRESS>
```

An exact formula sample must have:
- verified prestate;
- standard SPL token programs;
- supported exact active-bin allocation;
- raw fee-state metadata;
- target transaction block time;
- positive emitted CompositionFee event.

The prediction must match composition fee X/Y and protocol fee X/Y exactly.

## 7. Check execution calibration

Add-liquidity execution:

```bash
python-learner/.venv/bin/pio add-execution --position <POSITION_ADDRESS>
```

Rebalance execution:

```bash
python-learner/.venv/bin/pio rebalance-execution --position <POSITION_ADDRESS>
```

Transaction costs:

```bash
python-learner/.venv/bin/pio transaction-costs --position <POSITION_ADDRESS>
```

Treat request-decode gaps and guard violations as evidence gaps, not as zero-cost or successful samples.

## 8. Collect exact position snapshots

For amount/fee/reward reconciliation, collect the same real position repeatedly:

```bash
(cd rust-executor && cargo run -- inspect-position "$RPC_URL" <POSITION_ADDRESS>) \
  | python-learner/.venv/bin/pio ingest-position-snapshot
```

Use intervals where position liquidity/range and reward campaign are stable. Positive reward-checkpoint growth is required for reward sample sufficiency.

## 9. Review evidence

```bash
python-learner/.venv/bin/pio reconcile-corpus
python-learner/.venv/bin/pio phase2-evidence
python-learner/.venv/bin/pio phase2-work-queue
```

Do not promote while the work queue shows unexplained exact mismatches or execution-guard violations.

## 10. Run the promotion gate

Example minimums for an early validation corpus:

```bash
python-learner/.venv/bin/pio phase2-gate \
  --min-positions 10 \
  --min-amount-bins 50 \
  --min-fee-intervals 20 \
  --min-fee-bins 50 \
  --min-reward-intervals 10 \
  --min-reward-growth-bins 10 \
  --min-composition-samples 10 \
  --min-add-execution-samples 20 \
  --min-rebalance-guard-samples 10 \
  --min-transaction-fee-samples 20 \
  --min-amount-coverage-rate 0.95 \
  --require-ready
```

These counts are operating thresholds, not protocol truths. Raise them before using Phase 2 outputs as durable training labels.

## Fail-closed rules

- No exact historical prestate: do not reconstruct it approximately.
- No positive CompositionFee event: do not count it as composition-formula evidence.
- Token-2022 transfer-fee mint: exclude from the current high-fidelity path.
- Missing request decode: count against execution coverage.
- Missing receipt: count against transaction-cost coverage.
- Reward campaign/range/liquidity changes: reject the reconciliation interval.
- Exact mismatch or guard violation: keep the gate closed until explained.
