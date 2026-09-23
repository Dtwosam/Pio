# Simulator Design

Status: Phase 2 advanced implementation; promotion blocked by real calibration gaps.

## Rule

Simulator outputs are not authoritative ML labels until the relevant fidelity gate passes against real Meteora outcomes.

## Fidelity paths

### DISCRETE_COMPLETED_BIN_V1

OHLC research only. Models completed-bin inventory conversion, IL, hold benchmark and explicit supplied costs. It does not infer active-bin partial fills.

### SMALL_LP_CHAIN_PATH_V2

Standard-SPL counterfactual replay over repeated on-chain snapshots.

Uses:
- exact active bin
- raw Q64 bin price
- bin X/Y inventory and liquidity supply
- verified liquidity-share mint/withdraw formulas
- fee checkpoints
- effective reward checkpoints
- reward campaign identity
- deposit-time dynamic fee
- composition-fee accounting
- interval-by-interval counterfactual dilution

Fails closed on:
- missing bin coverage
- zero historical supply
- oversized hypothetical share
- Token-2022/non-standard programs
- reward campaign changes

Legacy snapshots without reward metadata return reward fidelity as unavailable instead of zero.

### OBSERVATION_BOUNDARY_REBALANCE_V1

Holds a position until an observed active bin exits the configured range. At that observation it withdraws principal, carries idle X/Y forward and re-enters around the new active bin when future observations remain.

Fees and rewards remain separate from principal. Automatic fee compounding is rejected until validated. The model does not claim the exact intra-interval exit time.

## Verified deterministic math

Liquidity:
- `L_in = P_q64 * X + (Y << 64)`
- empty bin share = `L_in`
- existing share = `floor(L_in * supply / L_bin)`
- withdrawal = pro-rata share of bin X/Y

Fees/rewards:
- `scaled_share = liquidity_share >> 64`
- accrued amount = `floor(scaled_share * checkpoint_delta / 2^64)`

Reward snapshots include the active-bin time accrual used by Meteora before DynamicPosition calculates pending rewards.

## Real reconciliation

Pio now reconciles:
- position token amounts
- pending X/Y fees
- pending reward slot 0/1
- every consecutive eligible stored position interval

Reward intervals reject:
- legacy snapshots without checkpoint metadata
- reward campaign changes
- limit-order pools
- known reward claims inside the interval
- liquidity changes
- checkpoint decreases

The corpus counts bins with positive reward-checkpoint growth separately; zero-growth observations do not satisfy the reward-growth sample minimum.

## Transaction/event calibration

Rust decodes Anchor event-CPI payloads for:
- AddLiquidity
- CompositionFee
- RemoveLiquidity
- Rebalancing

Transaction inspection also captures:
- network fee in lamports
- compute units consumed
- transaction success
- requested X/Y for common two-sided add-liquidity variants
- observed active id and max active-bin slippage for strategy/weight variants

Python can compare requested add amounts and active-bin guard values against the actual AddLiquidity event.

## Remaining blockers

1. Composition-fee formula reconciliation needs exact real pre-deposit bin state; the emitted CompositionFee label alone is not enough to independently recompute it.
2. Broader slippage calibration remains incomplete.
3. More fresh, high-frequency chain and position samples are required before promotion.
4. Token-2022 transfer-fee behavior remains outside the high-fidelity path.
