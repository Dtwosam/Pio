# Simulator Design

Status: Phase 2 started.

## Rule

The simulator must not claim DLMM-accurate PnL until its outputs are reconciled against real Meteora position outcomes.

## Verified bin-price primitive

Meteora's current SDK computes raw bin price as:

price(bin_id) = (1 + bin_step / 10000) ^ bin_id

UI token price then applies the token decimal difference.

Pio implements this primitive in python-learner/src/meteora_learner/dlmm_math.py.

## Simulator inputs

Per pool:
- bin_step
- token decimals
- historical price path
- selected min/max bin
- strategy/distribution weights
- capital allocated
- fee history or estimated per-bin fee share
- rebalance/transaction/slippage cost assumptions

For higher-fidelity simulation we will also need:
- active-bin history
- bin liquidity/distribution
- position share of each bin
- fee growth or swap-flow allocation by bin

## Simulation outputs

Each simulated position must produce:
- starting capital
- ending marked value
- fees
- rewards
- inventory change
- IL versus hold
- transaction/rebalance costs
- time in range
- max adverse excursion
- max favorable excursion
- realized net PnL

## Validation gate

Before model training uses simulator labels:
1. choose real Meteora positions with observable PnL/history;
2. reconstruct their ranges and holding periods;
3. run the simulator over the same interval;
4. compare value, fee and PnL error;
5. document error distribution;
6. reject simulator versions whose error exceeds the configured tolerance.

No reinforcement learning is allowed before this gate passes.
