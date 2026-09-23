# Meteora Adaptive LP Bot — Build Phases

Status: v0.6

## Phase 0 — Foundation
Status: complete.

## Phase 1 — Market Data Engine
Status: implementation complete; extended live validation pending.

Built:
- Meteora Data API ingestion
- raw + normalized pool/OHLCV/volume storage
- protocol-fee separation
- quality checks
- retry/rate limiting
- endpoint failure isolation

## Phase 2 — Position Simulator / Backtester
Status: advanced implementation; promotion gate remains closed.

Built:
- high-precision DLMM bin math
- SDK-equivalent Spot/Curve/Bid-Ask allocation
- verified empty/existing-bin liquidity-share formulas
- withdrawal/pro-rata amount math
- exact fee-checkpoint math
- effective reward-checkpoint math
- active-bin composition-fee model
- read-only pool/bin/DynamicPosition Rust inspection
- reward campaign metadata and checkpoint storage
- multi-snapshot small-LP chain replay
- reward attribution
- observation-boundary rebalance lifecycle replay
- counterfactual size/zero-supply guards
- Token-2022 fail-closed gate
- exact amount/fee/reward DynamicPosition reconciliation
- full-history reconciliation corpus
- operator-supplied Phase 2 sample thresholds
- fail-closed capability promotion gate
- position-history event collection
- Anchor event-CPI decoding for AddLiquidity, CompositionFee, RemoveLiquidity and Rebalancing
- transaction receipt fee/compute-unit capture
- requested-vs-actual add execution calibration
- chain-backed candidate scanner

Still blocking Phase 2 promotion:
- collect a meaningful fresh real-position corpus with reward growth
- independently reconcile composition-fee formula against real pre-deposit state
- broader execution/slippage calibration
- extended high-frequency chain collection
- Token-2022 transfer-fee support if Token-2022 pools enter scope

Current capability gate deliberately remains false for:
- composition_formula_reconciliation
- slippage_calibration

Exit condition:
- exact deterministic reconciliation passes;
- operator-selected sample minimums pass;
- all required fidelity capabilities pass.

## Phase 3 — Deterministic Baseline Strategy
Status: candidate comparison exists; policy not promoted.

Next after Phase 2:
- pool safety filters
- comparable value/PnL normalization
- deterministic entry/range/strategy policy
- conservative sizing
- exit/rebalance policy
- walk-forward evaluation

The baseline must consume validated Phase 2 outputs. It must not rank candidates using pool APR as if it were position profit.

## Phase 4 — ML v1

Walk-forward supervised models for future net return, downside, fee yield, range survival and holding-period quality.

## Phase 5 — Live Paper Trader

Run the full decision loop on live data without signing.

## Phase 6 — Rust Transaction Executor

Wallet isolation, instruction building, simulation, signing, confirmation/retry, idempotency and emergency close.

## Phase 7 — Controlled Live Trading

Start with explicit small capital caps.

## Phase 8 — Continuous Learning

Champion/challenger retraining, walk-forward validation, paper validation, promotion gates and rollback.

## Phase 9 — Advanced Edge

Optional token risk, wallet flow, regime models, adaptive ranges, portfolio allocation, hedging and contextual bandits.
