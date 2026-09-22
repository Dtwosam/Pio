use crate::models::{Action, Mode, TradeProposal};

#[derive(Debug, Clone)]
pub struct RiskConfig {
    pub max_capital_per_position_pct: f64,
    pub max_total_deployed_pct: f64,
    pub max_daily_drawdown_pct: f64,
    pub min_expected_edge_pct: f64,
    pub max_expected_downside_pct: f64,
    pub max_data_age_seconds: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum RiskDecision {
    Approve,
    Reject(String),
}

pub fn evaluate(proposal: &TradeProposal, cfg: &RiskConfig) -> RiskDecision {
    // The executor never signs BACKTEST/PAPER proposals.
    if proposal.mode != Mode::Live {
        return RiskDecision::Reject("non_live_mode".into());
    }

    // SKIP is a model decision, not an executable transaction.
    if proposal.action == Action::Skip {
        return RiskDecision::Reject("skip_has_no_execution".into());
    }

    // Exits must remain available even when entry data is stale or a drawdown stop is active.
    // Transaction simulation and wallet checks are enforced later in the execution pipeline.
    if proposal.action == Action::Exit {
        return RiskDecision::Approve;
    }

    if proposal.data_age_seconds > cfg.max_data_age_seconds {
        return RiskDecision::Reject("stale_market_data".into());
    }

    if proposal.account_equity_quote <= 0.0 {
        return RiskDecision::Reject("invalid_account_equity".into());
    }

    if proposal.capital_quote < 0.0 || proposal.portfolio_deployed_quote < 0.0 {
        return RiskDecision::Reject("invalid_capital_snapshot".into());
    }

    if proposal.daily_drawdown_pct >= cfg.max_daily_drawdown_pct {
        return RiskDecision::Reject("daily_drawdown_limit".into());
    }

    let position_pct = proposal.capital_quote / proposal.account_equity_quote * 100.0;
    if position_pct > cfg.max_capital_per_position_pct {
        return RiskDecision::Reject("position_size_limit".into());
    }

    let projected_deployed_pct =
        (proposal.portfolio_deployed_quote + proposal.capital_quote) / proposal.account_equity_quote
            * 100.0;
    if projected_deployed_pct > cfg.max_total_deployed_pct {
        return RiskDecision::Reject("portfolio_exposure_limit".into());
    }

    if proposal.min_bin_id > proposal.max_bin_id {
        return RiskDecision::Reject("invalid_bin_range".into());
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
}
