use crate::models::{Action, Mode, TradeProposal};
use crate::risk::{evaluate, RiskConfig, RiskDecision};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskCheckReport {
    pub decision_id: Uuid,
    pub mode: Mode,
    pub action: Action,
    pub accepted: bool,
    pub reason: String,
}

pub fn check_proposal(proposal: &TradeProposal, config: &RiskConfig) -> RiskCheckReport {
    let decision = evaluate(proposal, config);
    match decision {
        RiskDecision::Approve => RiskCheckReport {
            decision_id: proposal.decision_id,
            mode: proposal.mode.clone(),
            action: proposal.action.clone(),
            accepted: true,
            reason: "approved".into(),
        },
        RiskDecision::Reject(reason) => RiskCheckReport {
            decision_id: proposal.decision_id,
            mode: proposal.mode.clone(),
            action: proposal.action.clone(),
            accepted: false,
            reason,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proposal() -> TradeProposal {
        TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Enter,
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
            model_version: "baseline".into(),
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
    fn report_preserves_decision_identity() {
        let proposal = proposal();
        let report = check_proposal(&proposal, &config());
        assert_eq!(report.decision_id, proposal.decision_id);
        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
    }

    #[test]
    fn report_exposes_rejection_reason() {
        let mut proposal = proposal();
        proposal.mode = Mode::Paper;
        let report = check_proposal(&proposal, &config());
        assert!(!report.accepted);
        assert_eq!(report.reason, "non_live_mode");
    }
}
