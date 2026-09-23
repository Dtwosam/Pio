use crate::models::{Action, Mode, TradeProposal};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskConfig {
    pub max_capital_per_position_pct: f64,
    pub max_total_deployed_pct: f64,
    pub max_daily_drawdown_pct: f64,
    pub min_expected_edge_pct: f64,
    pub max_expected_downside_pct: f64,
    pub max_data_age_seconds: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "decision", content = "reason", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RiskDecision {
    Approve,
    Reject(String),
}

fn finite(values: &[f64]) -> bool {
    values.iter().all(|value| value.is_finite())
}

fn valid_strategy(value: &str) -> bool {
    matches!(value, "SPOT" | "CURVE" | "BID_ASK")
}

fn config_is_valid(cfg: &RiskConfig) -> bool {
    finite(&[
        cfg.max_capital_per_position_pct,
        cfg.max_total_deployed_pct,
        cfg.max_daily_drawdown_pct,
        cfg.min_expected_edge_pct,
        cfg.max_expected_downside_pct,
    ]) && cfg.max_capital_per_position_pct > 0.0
        && cfg.max_capital_per_position_pct <= 100.0
        && cfg.max_total_deployed_pct > 0.0
        && cfg.max_total_deployed_pct <= 100.0
        && cfg.max_capital_per_position_pct <= cfg.max_total_deployed_pct
        && cfg.max_daily_drawdown_pct >= 0.0
        && cfg.max_daily_drawdown_pct <= 100.0
        && cfg.min_expected_edge_pct >= 0.0
        && cfg.max_expected_downside_pct >= 0.0
        && cfg.max_data_age_seconds > 0
}

pub fn evaluate(proposal: &TradeProposal, cfg: &RiskConfig) -> RiskDecision {
    if !config_is_valid(cfg) {
        return RiskDecision::Reject("invalid_risk_config".into());
    }

    // The executor never signs BACKTEST/PAPER proposals.
    if proposal.mode != Mode::Live {
        return RiskDecision::Reject("non_live_mode".into());
    }

    // SKIP is a model decision, not an executable transaction.
    if proposal.action == Action::Skip {
        return RiskDecision::Reject("skip_has_no_execution".into());
    }

    if proposal.pool_address.trim().is_empty() {
        return RiskDecision::Reject("missing_pool_address".into());
    }

    // EXIT remains available when entry/rebalance gates are blocked by stale
    // market data, drawdown, or model economics. Transaction simulation and
    // wallet/position ownership checks are enforced later in the pipeline.
    if proposal.action == Action::Exit {
        return RiskDecision::Approve;
    }

    if !finite(&[
        proposal.capital_quote,
        proposal.account_equity_quote,
        proposal.portfolio_deployed_quote,
        proposal.daily_drawdown_pct,
        proposal.expected_net_return_pct,
        proposal.expected_downside_pct,
    ]) {
        return RiskDecision::Reject("non_finite_numeric_input".into());
    }

    if proposal.data_age_seconds > cfg.max_data_age_seconds {
        return RiskDecision::Reject("stale_market_data".into());
    }

    if proposal.account_equity_quote <= 0.0 {
        return RiskDecision::Reject("invalid_account_equity".into());
    }

    if proposal.capital_quote <= 0.0 || proposal.portfolio_deployed_quote < 0.0 {
        return RiskDecision::Reject("invalid_capital_snapshot".into());
    }

    if proposal.daily_drawdown_pct < 0.0 {
        return RiskDecision::Reject("invalid_drawdown_snapshot".into());
    }

    if proposal.expected_downside_pct < 0.0 {
        return RiskDecision::Reject("invalid_expected_downside".into());
    }

    if proposal.daily_drawdown_pct >= cfg.max_daily_drawdown_pct {
        return RiskDecision::Reject("daily_drawdown_limit".into());
    }

    if proposal.min_bin_id > proposal.max_bin_id {
        return RiskDecision::Reject("invalid_bin_range".into());
    }

    if !valid_strategy(proposal.strategy.as_str()) {
        return RiskDecision::Reject("unsupported_strategy".into());
    }

    let position_pct = proposal.capital_quote / proposal.account_equity_quote * 100.0;
    if position_pct > cfg.max_capital_per_position_pct {
        return RiskDecision::Reject("position_size_limit".into());
    }

    let projected_deployed_quote = match proposal.action {
        Action::Enter => proposal.portfolio_deployed_quote + proposal.capital_quote,
        Action::Rebalance => {
            if proposal.capital_quote > proposal.portfolio_deployed_quote + f64::EPSILON {
                return RiskDecision::Reject("capital_exceeds_deployed_on_rebalance".into());
            }
            proposal.portfolio_deployed_quote
        }
        Action::Skip | Action::Exit => unreachable!(),
    };
    let projected_deployed_pct =
        projected_deployed_quote / proposal.account_equity_quote * 100.0;
    if projected_deployed_pct > cfg.max_total_deployed_pct {
        return RiskDecision::Reject("portfolio_exposure_limit".into());
    }

    if proposal.expected_net_return_pct < cfg.min_expected_edge_pct {
        return RiskDecision::Reject("insufficient_expected_edge".into());
    }

    if proposal.expected_downside_pct > cfg.max_expected_downside_pct {
        return RiskDecision::Reject("expected_downside_limit".into());
    }

    RiskDecision::Approve
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use uuid::Uuid;

    fn proposal(mode: Mode, action: Action) -> TradeProposal {
        TradeProposal {
            decision_id: Uuid::new_v4(),
            mode,
            action,
            pool_address: "pool".into(),
            capital_quote: 10.0,
            account_equity_quote: 1_000.0,
            portfolio_deployed_quote: 100.0,
            daily_drawdown_pct: 0.5,
            min_bin_id: -10,
            max_bin_id: 10,
            strategy: "SPOT".into(),
            expected_net_return_pct: 1.0,
            expected_downside_pct: 0.5,
            model_version: "test".into(),
            data_age_seconds: 1,
        }
    }

    fn config() -> RiskConfig {
        RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        }
    }

    #[test]
    fn paper_mode_cannot_execute() {
        assert!(matches!(
            evaluate(&proposal(Mode::Paper, Action::Enter), &config()),
            RiskDecision::Reject(_)
        ));
    }

    #[test]
    fn live_exit_remains_available_during_stale_data() {
        let mut p = proposal(Mode::Live, Action::Exit);
        p.data_age_seconds = 10_000;
        p.daily_drawdown_pct = 99.0;
        assert_eq!(evaluate(&p, &config()), RiskDecision::Approve);
    }

    #[test]
    fn oversized_position_is_rejected_as_percentage_of_equity() {
        let mut p = proposal(Mode::Live, Action::Enter);
        p.capital_quote = 30.0; // 3% of 1,000; limit is 2%.
        assert_eq!(
            evaluate(&p, &config()),
            RiskDecision::Reject("position_size_limit".into())
        );
    }

    #[test]
    fn daily_drawdown_blocks_new_risk() {
        let mut p = proposal(Mode::Live, Action::Enter);
        p.daily_drawdown_pct = 3.0;
        assert_eq!(
            evaluate(&p, &config()),
            RiskDecision::Reject("daily_drawdown_limit".into())
        );
    }

    #[test]
    fn rebalance_does_not_double_count_existing_position_capital() {
        let mut cfg = config();
        cfg.max_capital_per_position_pct = 15.0;
        let mut p = proposal(Mode::Live, Action::Rebalance);
        p.capital_quote = 100.0;
        p.portfolio_deployed_quote = 190.0;
        assert_eq!(evaluate(&p, &cfg), RiskDecision::Approve);
    }

    #[test]
    fn rebalance_rejects_inconsistent_capital_snapshot() {
        let mut cfg = config();
        cfg.max_capital_per_position_pct = 20.0;
        let mut p = proposal(Mode::Live, Action::Rebalance);
        p.capital_quote = 150.0;
        p.portfolio_deployed_quote = 100.0;
        assert_eq!(
            evaluate(&p, &cfg),
            RiskDecision::Reject("capital_exceeds_deployed_on_rebalance".into())
        );
    }

    #[test]
    fn unsupported_strategy_fails_closed() {
        let mut p = proposal(Mode::Live, Action::Enter);
        p.strategy = "MAGIC".into();
        assert_eq!(
            evaluate(&p, &config()),
            RiskDecision::Reject("unsupported_strategy".into())
        );
    }

    #[test]
    fn invalid_risk_config_fails_closed() {
        let mut cfg = config();
        cfg.max_total_deployed_pct = f64::NAN;
        assert_eq!(
            evaluate(&proposal(Mode::Live, Action::Enter), &cfg),
            RiskDecision::Reject("invalid_risk_config".into())
        );
    }
}
