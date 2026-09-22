use crate::models::{Action, Mode, TradeProposal};

#[derive(Debug, Clone)]
pub struct RiskConfig {
    pub max_capital_per_position: f64,
    pub min_expected_edge: f64,
    pub max_expected_downside: f64,
    pub max_data_age_seconds: u64,
}

#[derive(Debug, Clone)]
pub enum RiskDecision {
    Approve,
    Reject(String),
}

pub fn evaluate(proposal: &TradeProposal, cfg: &RiskConfig) -> RiskDecision {
    if proposal.data_age_seconds > cfg.max_data_age_seconds {
        return RiskDecision::Reject("stale_market_data".into());
    }

    if proposal.capital_quote < 0.0 || proposal.capital_quote > cfg.max_capital_per_position {
        return RiskDecision::Reject("position_size_limit".into());
    }

    if proposal.min_bin_id > proposal.max_bin_id {
        return RiskDecision::Reject("invalid_bin_range".into());
    }

    if proposal.action == Action::Enter && proposal.expected_net_return < cfg.min_expected_edge {
        return RiskDecision::Reject("insufficient_expected_edge".into());
    }

    if proposal.expected_downside > cfg.max_expected_downside {
        return RiskDecision::Reject("expected_downside_limit".into());
    }

    if proposal.mode != Mode::Live {
        return RiskDecision::Reject("non_live_mode".into());
    }

    RiskDecision::Approve
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use uuid::Uuid;

    #[test]
    fn paper_mode_cannot_execute() {
        let p = TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Paper,
            action: Action::Enter,
            pool_address: "pool".into(),
            capital_quote: 10.0,
            min_bin_id: -10,
            max_bin_id: 10,
            strategy: "SPOT".into(),
            expected_net_return: 0.03,
            expected_downside: 0.01,
            model_version: "test".into(),
            data_age_seconds: 1,
        };
        let cfg = RiskConfig {
            max_capital_per_position: 100.0,
            min_expected_edge: 0.001,
            max_expected_downside: 0.10,
            max_data_age_seconds: 30,
        };
        assert!(matches!(evaluate(&p, &cfg), RiskDecision::Reject(_)));
    }
}
