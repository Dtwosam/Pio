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
