# Simulator Design

Status: Phase 2 in progress.

## Non-negotiable rule

Simulator outputs are not treated as authoritative ML labels until reconciled against real Meteora position outcomes.

## Fidelity paths

### DISCRETE_COMPLETED_BIN_V1

OHLC-based research path.

It models:
- Meteora bin-price ladder
- Spot / Curve / Bid-Ask deposit shapes
- X/Y inventory by bin
- completed-bin conversion
- hold benchmark
- IL
- explicit costs
- range survival

It intentionally does not infer active-bin partial fill from candles. ZERO-fee OHLC backtests remain inventory/IL studies, not profitability claims.

### SMALL_LP_CHAIN_PATH_V2

Higher-fidelity standard-SPL counterfactual path over repeated on-chain snapshots.

It uses:
- exact active-bin observations
- raw Q64 bin prices
- bin X/Y inventory
- bin liquidity supply
- per-token fee checkpoints
- token program IDs
- deposit-time dynamic fee rate
- protocol-share configuration
- source-backed liquidity-share minting
- source-backed withdrawal/pro-rata amount math
- active-bin composition-fee accounting
- interval-by-interval fee checkpoint attribution

The hypothetical share is held fixed across the replay. Each observation rechecks the share against the real pool supply. Historical fee growth is diluted interval-by-interval using the real starting supply for that interval.

## Verified liquidity-share formula

Meteora's current source-backed formula is:

- Deposit liquidity: `L_in = P * X + Y`.
- Empty bin: `share = L_in`.
- Existing bin: `floor(L_in * existing_supply / L_bin)`.
- Withdrawal amounts: `floor(share * bin_amount / bin_supply)`.

Python implements the integer Q64.64 form of those equations.

## Deposit distribution

For standard SPL tokens, Pio mirrors Meteora's side-local deposit allocation:
- Spot: uniform.
- Curve: concentrated toward the active area on each side.
- Bid-Ask: concentrated toward the outer edges.

Token-2022 deposits fail closed until mint-extension transfer-fee behavior is reproduced.

## Composition fee

For an active existing bin, Pio records composition fee as an entry cost separate from subsequent swap-fee earnings.

The formula uses Meteora's 1e9 fee-rate precision and protocol-share basis points. The Rust snapshot computes the deposit-time total fee after applying the current volatility reference/decay update against the Solana clock.

The composition target-side calculation is source-backed by the current SDK helper and still requires real add-liquidity reconciliation before promotion to authoritative labels.

## Counterfactual guard

Historical chain state did not include the hypothetical LP.

To limit path distortion:
- replay rejects candidates whose projected share exceeds the configured fraction of any observed real bin supply;
- replay rejects historical zero-supply bins because the hypothetical LP would change the empty/non-empty path;
- replay rejects missing bin coverage;
- replay rejects Token-2022/non-standard token programs.

Default maximum share is 500 bps (5%) and should be reduced for stricter validation.

## Candidate scanning

The chain scanner evaluates width × center offset × Spot/Curve/Bid-Ask combinations.

It does not produce a synthetic winner score yet. For each accepted candidate it returns:
- ending token inventory
- swap-fee earnings by token
- entry composition fees
- max observed counterfactual share
- range survival
- interval diagnostics

Rejected candidates remain in output with the exact reason.

## Validation sources

Meteora Data API:
- `GET /positions/{pool_address}/pnl?user=...`
- `GET /positions/{position_address}/historical`

On-chain Rust reader:
- `inspect-position`

Validation metrics:
- PnL MAE/RMSE/bias
- return error
- per-bin token amount error
- per-bin liquidity-share error
- fee X/Y error
- composition-fee error

## Remaining fidelity work

- collect a meaningful real-position reconciliation corpus;
- validate active-bin composition outcomes against real adds;
- support Token-2022 transfer-fee extensions;
- model rewards for hypothetical positions;
- simulate rebalance lifecycle and transaction/slippage costs;
- increase on-chain snapshot frequency so intra-interval supply changes are smaller.
