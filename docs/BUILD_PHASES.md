# Meteora Adaptive LP Bot — Build Phases

Status: v1.2

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
Status: deterministic implementation complete; promotion evidence pending.

Built:
- fail-closed pool universe safety screen
- standard-SPL/token-program eligibility check
- TVL, volume, age, blacklist and dynamic-fee filters
- trailing chain candidate comparison by range/strategy
- decision-time proposal recentering
- cost-aware excess-versus-hold economics
- entry-value normalized return / hold / excess metrics
- equal-notional cross-pool research comparison
- non-overlapping no-lookahead walk-forward evaluation
- multi-pool walk-forward validation workflow
- drawdown-aware capital sizing
- cash reserve / per-position / total-deployment caps
- deterministic HOLD / REBALANCE / EXIT policy
- hard safety exit and stop-loss override
- optional take-profit and max-hold exit
- rebalance-count cap
- Phase 3 entry authorization gate
- end-to-end single-pool and multi-pool research planners
- evidence-driven Phase 3 promotion thresholds
- persistent Phase 2 / Phase 3 promotion evidence and status

Promotion still requires:
- persisted Phase 2 promotion from real reconciliation/calibration evidence
- a meaningful multi-pool, regime-diverse walk-forward corpus
- configured Phase 3 sample, hit-rate, excess-return and downside thresholds to pass
- persisted Phase 3 promotion evidence before downstream ML/paper gates can treat the policy as promoted

The baseline never ranks by headline APR alone. Research output can exist before promotion, but downstream promotion-sensitive workflows read persisted gate state rather than caller-supplied booleans. Live transaction construction/signing remains outside Phase 3.

## Phase 4 — ML v1
Status: experimental infrastructure implemented; promotion evidence pending.

Built:
- no-lookahead decision-time feature dataset
- all-candidate action labels for Spot / Curve / Bid-Ask alternatives
- forward net-return / excess-vs-hold / range-survival labels
- time-ordered train/validation split
- expected net-return model
- excess-vs-hold model
- downside model
- range-survival model
- positive-edge probability model
- research-only risk-adjusted inference
- held-out challenger versus deterministic-baseline evaluation
- persistent model registry
- reproducible model artifact save/load with metadata checks
- persisted offline challenger evidence
- staged OFFLINE_CANDIDATE -> OFFLINE_QUALIFIED -> PAPER_CHALLENGER lifecycle
- persistent Phase 3 promotion required before offline qualification
- evidence-gated CHAMPION promotion
- single-champion guard and rollback state
- CLI workflow for train/register, offline evaluation, paper start and model status

Still needed:
- materially larger multi-pool action dataset
- regime-diverse time windows
- stable real-data offline challenger qualification
- repeated paper validation before any challenger becomes champion
- retraining cadence and drift monitoring for Phase 8

## Phase 5 — Live Paper Trader
Status: unattended chain-driven PAPER orchestration implemented; extended live validation pending.

Built:
- persistent paper accounts
- persistent paper positions and idempotent event ledger
- cash/equity/high-water/drawdown accounting
- fee/reward/cost/realized-PnL accounting
- deterministic paper HOLD / REBALANCE / EXIT evaluation
- automatic single-position paper observation cycle
- idempotent multi-position paper observation batches with restart recovery
- stop-loss and safety exits on net liquidation economics
- same-width deterministic recentering for paper rebalances
- persistent counterfactual per-bin liquidity-share/checkpoint state
- chain-derived inventory marks from real bin snapshots
- incremental chain-derived fee and supported reward valuation
- explicit current token-Y quote-rate support
- fail-closed handling for unvalued reward tokens / unsupported token programs
- prepared/applied chain valuation state for idempotent recovery
- counterfactual atomic-state reset after paper rebalances
- multi-position chain-valued paper runner
- latest-chain grouping across pools with idempotent cycle IDs
- fail-closed live pool safety derived from local normalized state
- restart recovery after prepared valuation and partially-applied mark events
- automatic account-level discovery of open chain-bound paper positions
- stale-first capped portfolio scheduling
- explicit token-Y quote-map requirement with missing-quote skips
- fresh persisted external reward-token quote valuation with no-lookahead lookup and fail-closed missing/stale handling
- ledger-derived Phase 3 paper entry workflow
- atomic paper position + ENTER event + counterfactual chain binding
- preflight counterfactual validation before capital debit
- focused Data API refresh for pools behind open positions
- read-only Rust refresh for missing/stale chain snapshots
- persisted token quote registry with freshness checks
- optional Jupiter USD-per-atomic token-Y quote refresh
- idempotent end-to-end paper ticks
- durable per-account scheduler lease and health state
- persisted scheduler lifecycle event history for lease acquisition, overlap, stale-worker recovery and tick completion
- deterministic time-bucket tick IDs for cron/systemd retries
- stale RUNNING tick recovery after expired scheduler leases
- prebuilt Rust executor support for hardened unattended refresh
- unprivileged systemd service/timer deployment templates
- current health report with Prometheus-compatible gauges
- evidence-based PAPER endurance report with configurable runtime, reliability, dependency-blockage and applied-valuation thresholds
- paper cohort performance metrics
- ML paper challenger versus deterministic baseline validation
- stored paper evidence required for champion promotion

Still needed:
- external reward-token valuation beyond token X/Y rewards
- long endurance/restart runs across many observations and positions
- extended paper validation on real live observations
- external alert delivery/integration around the implemented health metrics, if required by deployment

## Phase 6 — Rust Transaction Executor

Wallet isolation, instruction building, simulation, signing, confirmation/retry, idempotency and emergency close.

## Phase 7 — Controlled Live Trading

Start with explicit small capital caps.

## Phase 8 — Continuous Learning

Champion/challenger retraining, walk-forward validation, paper validation, promotion gates and rollback.

## Phase 9 — Advanced Edge

Optional token risk, wallet flow, regime models, adaptive ranges, portfolio allocation, hedging and contextual bandits.
