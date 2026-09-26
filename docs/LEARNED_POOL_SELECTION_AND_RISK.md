# Learned Pool Selection and Economic Risk

## Status

Research-only design and dataset work. This document does not authorize live
capital movement and does not bypass the existing promotion lineage.

Related roadmap issues:

- #3 — market-wide Meteora pool discovery, comparison and rotation
- #4 — learned market-risk and pool-opportunity dataset

## Core principle

Pio should learn economic opportunity and economic downside from observed market
outcomes instead of treating hand-written thresholds as truth.

Examples of decisions intended to become evidence-driven include:

- which pools deserve capital;
- how volume, liquidity, volatility and fee generation interact;
- when a volatile pool is still economically attractive;
- when deterioration in price/liquidity makes remaining unattractive;
- how much capital an opportunity deserves;
- whether switching pools is worth its cost;
- when staying, reducing exposure, switching or exiting has the best expected
  outcome.

This does **not** mean every guard becomes learned. Technical and data-integrity
constraints remain deterministic and fail-closed. Examples include malformed or
missing observations, unsupported transaction/token structures, impossible
accounting states, stale evidence when freshness is required, and upstream
promotion gates.

## Why continuous targets come first

The first dataset layer should preserve what actually happened rather than
inventing labels such as "safe", "unsafe" or "token collapse".

Useful continuous forward outcomes include:

- LP net return;
- excess return versus hold;
- fee return;
- price return;
- maximum price drawdown;
- TVL/liquidity change and maximum deterioration;
- range survival;
- execution, exit, slippage, composition and rebalance costs.

If a binary policy boundary is later useful, its threshold should be justified
from validation evidence rather than selected merely because it sounds prudent.

## Market-wide learning loop

The intended full-system loop is:

1. discover the Meteora pool universe;
2. capture comparable pool/token/market state;
3. build no-lookahead features;
4. observe later economic outcomes;
5. train/version candidate models;
6. evaluate them with walk-forward and cross-pool validation;
7. paper-test pool selection and rotation;
8. allow learned outputs to influence capital only after upstream gates and
   promotion requirements pass;
9. persist realized outcomes and feed them back into later retraining.

High raw trading volume is therefore an input, not the objective. Pio should
learn whether volume is valuable after accounting for liquidity competition,
fees, volatility, price/liquidity deterioration, execution costs and the
realized LP outcome.

## Initial implementation

`pool_market_learning.py` builds an explicitly no-lookahead research dataset
from chronological pool observations.

Decision-time features currently include:

- price, TVL, 24h volume and 24h fees;
- one-step price/TVL/volume changes;
- trailing realized volatility and price drawdown;
- volume acceleration;
- volume/TVL, fees/TVL and fees/volume ratios;
- amount of prior history available.

Forward observations are used only for continuous targets:

- forward price return;
- forward TVL return;
- maximum forward price drawdown;
- maximum forward TVL drawdown;
- end-of-horizon volume/TVL;
- end-of-horizon fees/TVL.

This is intentionally only a foundation. It does not create a risk score, rank
pools, impose economic cutoffs, or connect to execution.


## Implemented research components

The current research branch adds the following components:

- `market_pool_universe.py`
  - paginates through the Meteora pool universe;
  - deduplicates pool addresses;
  - stores raw and normalized observations;
  - stops on source exhaustion/repetition/max-page guard;
  - deliberately applies no economic ranking or risk cutoff.

- `pool_market_data_quality.py`
  - reports observation counts per pool;
  - reports valid price/TVL/volume/fee coverage;
  - reports median observation interval and maximum gap;
  - remains descriptive rather than pass/fail.

- `pool_market_learning.py`
  - builds no-lookahead market features and continuous future targets;
  - can load chronological normalized pool snapshots directly from the
    research store;
  - keeps feature and target windows separate.

- `pool_market_training.py`
  - purges training examples whose forward labels overlap validation time;
  - trains continuous regressors for every market target;
  - compares model MAE with a simple train-median baseline;
  - produces no safety label, rank, allocation or execution decision.

- `pool_market_walk_forward.py`
  - performs expanding-window evaluation through time;
  - purges overlapping forward labels on every fold;
  - reports per-target predictive error and baseline improvement;
  - produces descriptive metrics only, not a policy qualification verdict.

- `pool_market_research_cycle.py`
  - orchestrates capture -> coverage -> dataset -> walk-forward evaluation;
  - returns `COLLECTING_HISTORY` when real history is insufficient;
  - never fills missing history with synthetic outcomes.

- `pool_market_research_cli.py`
  - exposes the research workflow as `pio-market-research`;
  - supports stored-data-only runs with `--no-capture`;
  - prints a compact JSON report rather than dumping the training corpus.

- `pool_market_report_artifact.py`
  - optionally persists versioned research reports;
  - stores checksum metadata;
  - validates research-only / non-actionable / non-execution status on load;
  - refuses accidental overwrite of an existing report id.

## Validation discipline

The current implementation uses three different anti-leakage protections:

1. decision-time features are calculated only from observations available at
   or before the decision timestamp;
2. future observations appear only in continuous targets;
3. training examples are purged when their target window reaches into the
   validation period.

This is important because a learner can otherwise look excellent in historical
testing while indirectly using information that was not available at the time.

## Still intentionally missing

The research branch does not yet:

- choose a live pool;
- rank pools for capital allocation;
- decide a live entry, stay, rebalance, switch or exit action;
- move capital;
- replace the Phase 2 / Phase 3 / paper / controlled-live promotion lineage;
- claim that predictive accuracy is sufficient for economic profitability.

Later work should add richer features and realized LP outcomes, including chain
liquidity competition, token/mint state, execution costs, composition costs,
rebalance costs and actual position PnL, before learned outputs can be evaluated
as a candidate capital-allocation policy.
