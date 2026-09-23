# Meteora Adaptive LP Bot — Build Phases

Status: v0.8

## Phase 0 — Foundation
Status: complete.

## Phase 1 — Market Data Engine
Status: implementation complete; extended live validation pending.

## Phase 2 — Position Simulator / Backtester
Status: standard-SPL implementation complete; promotion evidence pending.

Built:
- high-precision DLMM bin math
- SDK-equivalent Spot/Curve/Bid-Ask allocation
- verified empty/existing-bin liquidity-share formulas
- withdrawal/pro-rata amount math
- exact fee-checkpoint math
- effective reward-checkpoint math
- target-time dynamic fee-state replay
- active-bin composition-fee model
- slot-bounded pre-add pool captures
- exact pool/bin-array prestate gap verification
- exact positive CompositionFee event reconciliation
- read-only pool/bin/DynamicPosition Rust inspection
- multi-snapshot small-LP chain replay
- reward attribution
- observation-boundary rebalance lifecycle replay
- Token-2022 fail-closed gate
- exact amount/fee/reward DynamicPosition reconciliation
- full-history reconciliation corpus
- Anchor event-CPI decoding for AddLiquidity, CompositionFee, RemoveLiquidity and Rebalancing
- transaction receipt fee/compute-unit capture
- requested-vs-actual add execution calibration
- rebalance transaction guard decoding and calibration
- evidence-driven Phase 2 capability gate
- actionable Phase 2 calibration work queue
- chain-backed candidate scanner

Promotion still requires real evidence:
- a meaningful fresh real-position corpus with positive reward growth
- enough verified positive composition-fee samples
- complete add execution request/event coverage for the selected corpus
- enough real rebalance guard samples with no unexplained violations
- real transaction-fee receipt coverage
- extended high-frequency chain collection

Token-2022 transfer-fee support remains outside the current high-fidelity path and fails closed.

Exit condition:
- exact deterministic amount/fee/reward reconciliation passes;
- exact positive composition-fee samples pass;
- add/rebalance execution guards pass at the configured sample minimums;
- transaction-cost sample minimums pass;
- operator-selected coverage/sample thresholds pass;
- no required capability remains unvalidated.

## Phase 3 — Deterministic Baseline Strategy
Status: deterministic research policy implemented; promotion blocked by Phase 2 evidence and broader validation.

Built:
- fail-closed pool universe safety screen
- standard-SPL/token-program eligibility check
- TVL, volume, age, blacklist and dynamic-fee filters
- trailing chain candidate comparison by range/strategy
- decision-time proposal recentering
- cost-aware excess-versus-hold economics
- non-overlapping no-lookahead walk-forward evaluation
- drawdown-aware capital sizing
- cash reserve / per-position / total-deployment caps
- deterministic HOLD / REBALANCE / EXIT policy
- hard safety exit and stop-loss override
- optional take-profit and max-hold exit
- rebalance-count cap
- Phase 3 entry authorization gate
- end-to-end research planner

Still needed before Phase 3 promotion:
- Phase 2 evidence gate must pass on real samples
- standardized cross-pool notional/value normalization
- multi-pool out-of-sample comparison
- paper-trading state/account-equity loop
- larger walk-forward corpus across market regimes
- explicit policy-promotion thresholds

The baseline never ranks by headline APR alone. Research output can exist before Phase 2 promotion, but entry authorization remains blocked. Live transaction construction/signing remains outside Phase 3.

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
