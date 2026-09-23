# ML and Paper Trading Pipeline

Status: experimental / paper-only

## ML action dataset

The ML dataset labels every replay-valid candidate action at a decision point, not only the deterministic baseline choice.

Decision-time features include:
- strategy and range shape
- active-bin movement
- fee state
- liquidity shape
- fee-checkpoint activity
- trailing range survival
- trailing cost-adjusted returns
- counterfactual liquidity share

Forward-only targets include:
- net return
- excess versus hold
- range survival
- positive excess classification

Future observations never enter the feature columns.

## ML lifecycle

Models move through:
1. OFFLINE_CANDIDATE
2. OFFLINE_QUALIFIED
3. PAPER_CHALLENGER
4. CHAMPION

A generic status update cannot create a champion. Champion promotion requires stored qualified paper-validation evidence.

## Paper ledger

Paper mode tracks:
- cash
- open position marks
- fee and reward income
- entry/rebalance/exit costs
- realized PnL
- high-water equity
- drawdown
- idempotent ENTER / MARK / REBALANCE / EXIT events

The automatic observation cycle:
1. records the latest mark and incremental income;
2. evaluates net liquidation PnL;
3. applies safety/stop-loss/take-profit/range rules;
4. HOLDs, EXITs, or recenters and REBALANCEs;
5. refuses to assume zero rebalance cost when the cost is unknown.

None of these paths build, sign or send Solana transactions.

## Promotion principle

Offline model quality is not enough.

A challenger must first beat the deterministic baseline on held-out decisions, then run as a paper challenger, then pass minimum completed-trade, net-return, win-rate, drawdown and baseline-uplift criteria. Only then can qualified evidence be stored and the model promoted to champion.
