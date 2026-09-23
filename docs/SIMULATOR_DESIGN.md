# Simulator Design

Status: Phase 2 standard-SPL implementation complete; promotion blocked by real sample sufficiency.

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

Fails closed on missing bin coverage, zero historical supply, oversized hypothetical share, Token-2022/non-standard programs and reward campaign changes.

### OBSERVATION_BOUNDARY_REBALANCE_V1

Holds a position until an observed active bin exits the configured range. At that observation it withdraws principal, carries idle X/Y forward and re-enters around the new active bin when future observations remain.

Fees and rewards remain separate from principal. Automatic fee compounding is rejected until independently validated. The model does not claim an exact intra-interval exit time.

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

## Exact composition-fee reconciliation

Pio no longer treats a nearby historical snapshot as exact prestate.

A composition sample is eligible only when:
1. the pool snapshot records RPC capture start/end slots;
2. the target add transaction is in a later slot;
3. the pool account and active BinArray show no intervening transaction from capture start through the target, except the target itself;
4. same-slot ambiguity is absent;
5. the active-bin allocation can be reconstructed exactly from a supported standard-SPL add instruction;
6. raw on-chain volatility/reference state is available;
7. the total fee rate is replayed at the target transaction block time;
8. a positive CompositionFee event was actually emitted.

The predicted composition fee and protocol split are then compared exactly with the event values. Zero-fee/no-event adds do not count as formula-validation samples.

Currently exact allocation supports:
- explicit BPS distributions;
- SDK imbalanced SPOT/CURVE/BID_ASK strategy variants.

Weighted/balanced forms remain ineligible until their active-bin allocation is mirrored exactly.

## Real amount/fee/reward reconciliation

Pio reconciles:
- position token amounts;
- pending X/Y fees;
- pending reward slot 0/1;
- every consecutive eligible stored position interval.

Reward intervals reject legacy snapshots, campaign changes, limit-order pools, known claims, liquidity changes and checkpoint decreases. Positive checkpoint growth is counted separately from zero-growth observations.

## Liquidity execution calibration

Rather than inventing a generic LP "slippage %" where the instruction itself exposes hard guards, Pio records and validates the bounds actually submitted on-chain.

Add calibration measures:
- requested vs actual deposited X/Y;
- active-bin drift against max active-bin slippage.

Rebalance calibration measures:
- active-bin drift;
- actual withdraw X/Y against minimum-withdraw bounds;
- actual redeposit X/Y against maximum-deposit bounds;
- guard headroom;
- emitted rebalance fee amounts;
- Solana network fee and compute units.

Missing request decodes reduce coverage instead of disappearing.

## Evidence-driven promotion

`phase2-gate` combines deterministic reconciliation with real calibration evidence. Normal CLI defaults require at least one:
- positive exact composition sample;
- matched add-execution sample;
- rebalance guard sample;
- transaction-fee sample.

Operators can raise these minimums. The gate cannot pass merely because the code path exists.

`phase2-evidence` summarizes current evidence. `phase2-work-queue` turns gaps into actionable transaction/prestate tasks and explicitly marks historical prestate that cannot be reconstructed safely.

## Remaining work before promotion

- collect statistically meaningful fresh samples at higher frequency;
- resolve any exact mismatches or guard violations;
- set production promotion sample/coverage thresholds;
- support Token-2022 transfer-fee extensions if those pools enter scope.
