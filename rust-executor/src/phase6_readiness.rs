use crate::phase5_gate::Phase5PromotionGateReport;
use crate::transaction_guard::TransactionGuardConfig;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;
use std::str::FromStr;

const METEORA_DLMM_PROGRAM: &str =
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Phase6ReadinessReport {
    pub accepted: bool,
    pub reason: String,
    pub phase5: Phase5PromotionGateReport,
    pub wallet_pubkey: String,
    pub policy_fee_payer_matches_wallet: bool,
    pub meteora_program_allowed: bool,
    pub requires_unsigned: bool,
    pub requires_pool_binding: bool,
    pub requires_instruction_policy: bool,
    pub address_lookup_tables_disabled: bool,
    pub enter_policy_present: bool,
    pub rebalance_policy_present: bool,
    pub exit_policy_present: bool,
}

pub fn evaluate_phase6_readiness(
    phase5: Phase5PromotionGateReport,
    wallet_pubkey: &Pubkey,
    config: &TransactionGuardConfig,
) -> Result<Phase6ReadinessReport> {
    let configured_payer = Pubkey::from_str(
        config.expected_fee_payer.trim(),
    )
    .context("transaction policy expected_fee_payer is invalid")?;
    let meteora_program = Pubkey::from_str(METEORA_DLMM_PROGRAM)
        .context("hard-coded Meteora DLMM program id is invalid")?;

    let allowed_programs = config
        .allowed_program_ids
        .iter()
        .map(|value| {
            Pubkey::from_str(value.trim())
                .with_context(|| {
                    format!("invalid allowed program id: {value}")
                })
        })
        .collect::<Result<Vec<_>>>()?;

    let policy_fee_payer_matches_wallet =
        configured_payer == *wallet_pubkey;
    let meteora_program_allowed =
        allowed_programs.contains(&meteora_program);

    let action_present = |action: crate::models::Action| {
        config.instruction_policies.iter().any(|policy| {
            policy.program_id == METEORA_DLMM_PROGRAM
                && policy.allowed_actions.contains(&action)
                && !policy.allowed_data_prefixes_hex.is_empty()
        })
    };
    let enter_policy_present =
        action_present(crate::models::Action::Enter);
    let rebalance_policy_present =
        action_present(crate::models::Action::Rebalance);
    let exit_policy_present =
        action_present(crate::models::Action::Exit);

    let reason = if !phase5.accepted {
        "phase5_promotion_gate_rejected"
    } else if !policy_fee_payer_matches_wallet {
        "transaction_policy_fee_payer_does_not_match_executor_wallet"
    } else if !meteora_program_allowed {
        "meteora_program_not_allowed"
    } else if !config.require_unsigned {
        "transaction_policy_does_not_require_unsigned_input"
    } else if !config.require_proposal_pool_account {
        "transaction_policy_does_not_require_pool_binding"
    } else if !config.require_instruction_policy {
        "transaction_policy_does_not_require_instruction_policy"
    } else if config.allow_address_lookup_tables {
        "address_lookup_tables_must_remain_disabled_for_controlled_live"
    } else if !enter_policy_present {
        "enter_instruction_policy_missing"
    } else if !rebalance_policy_present {
        "rebalance_instruction_policy_missing"
    } else if !exit_policy_present {
        "exit_instruction_policy_missing"
    } else {
        "approved"
    };

    Ok(Phase6ReadinessReport {
        accepted: reason == "approved",
        reason: reason.into(),
        phase5,
        wallet_pubkey: wallet_pubkey.to_string(),
        policy_fee_payer_matches_wallet,
        meteora_program_allowed,
        requires_unsigned: config.require_unsigned,
        requires_pool_binding: config.require_proposal_pool_account,
        requires_instruction_policy: config.require_instruction_policy,
        address_lookup_tables_disabled:
            !config.allow_address_lookup_tables,
        enter_policy_present,
        rebalance_policy_present,
        exit_policy_present,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Action;
    use crate::transaction_guard::ProgramInstructionPolicy;

    fn phase5(accepted: bool) -> Phase5PromotionGateReport {
        Phase5PromotionGateReport {
            accepted,
            reason: if accepted {
                "approved".into()
            } else {
                "phase5_promotion_evidence_missing".into()
            },
            phase_name: "PHASE5".into(),
            evidence_type: Some("PHASE5_PROMOTION_V1".into()),
            promoted_at: Some("2026-09-23T12:00:00+00:00".into()),
            qualified: accepted,
            evidence_promotion_ready: accepted,
            evidence_endurance_passing: accepted,
            evidence_ledger_audit_passing: accepted,
            evidence_phase3_promoted: accepted,
        }
    }

    fn config(wallet: Pubkey) -> TransactionGuardConfig {
        TransactionGuardConfig {
            expected_fee_payer: wallet.to_string(),
            allowed_program_ids: vec![METEORA_DLMM_PROGRAM.into()],
            max_instructions: 8,
            max_static_accounts: 96,
            allow_address_lookup_tables: false,
            require_unsigned: true,
            require_proposal_pool_account: true,
            required_account_pubkeys: vec![],
            require_instruction_policy: true,
            instruction_policies: vec![
                ProgramInstructionPolicy {
                    program_id: METEORA_DLMM_PROGRAM.into(),
                    allowed_actions: vec![Action::Enter],
                    allowed_data_prefixes_hex: vec!["01".into()],
                },
                ProgramInstructionPolicy {
                    program_id: METEORA_DLMM_PROGRAM.into(),
                    allowed_actions: vec![Action::Rebalance],
                    allowed_data_prefixes_hex: vec!["02".into()],
                },
                ProgramInstructionPolicy {
                    program_id: METEORA_DLMM_PROGRAM.into(),
                    allowed_actions: vec![Action::Exit],
                    allowed_data_prefixes_hex: vec!["03".into()],
                },
            ],
        }
    }

    #[test]
    fn ready_configuration_passes() {
        let wallet = Pubkey::new_unique();
        let report = evaluate_phase6_readiness(
            phase5(true),
            &wallet,
            &config(wallet),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
    }

    #[test]
    fn phase5_must_be_promoted() {
        let wallet = Pubkey::new_unique();
        let report = evaluate_phase6_readiness(
            phase5(false),
            &wallet,
            &config(wallet),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "phase5_promotion_gate_rejected");
    }

    #[test]
    fn wallet_must_match_policy_fee_payer() {
        let wallet = Pubkey::new_unique();
        let other = Pubkey::new_unique();
        let report = evaluate_phase6_readiness(
            phase5(true),
            &wallet,
            &config(other),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "transaction_policy_fee_payer_does_not_match_executor_wallet"
        );
    }

    #[test]
    fn missing_action_policy_fails_closed() {
        let wallet = Pubkey::new_unique();
        let mut cfg = config(wallet);
        cfg.instruction_policies.retain(|policy| {
            !policy.allowed_actions.contains(&Action::Exit)
        });

        let report = evaluate_phase6_readiness(
            phase5(true),
            &wallet,
            &cfg,
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "exit_instruction_policy_missing");
    }
}
