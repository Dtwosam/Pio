use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{LbPair, PositionV2};
use commons::{
    derive_bin_array_bitmap_extension, derive_event_authority_pda, dlmm,
    pod_read_unaligned_skip_disc, LbPairExtension, PositionExtension,
};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::instruction::Instruction;
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::str::FromStr;


const SPL_TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmergencyExitRequest {
    pub position: String,
    pub lb_pair: String,
    pub bin_array_bitmap_extension: Option<String>,
    pub user_token_x: String,
    pub user_token_y: String,
    pub reserve_x: String,
    pub reserve_y: String,
    pub token_x_mint: String,
    pub token_y_mint: String,
    pub bin_array_lower: String,
    pub bin_array_upper: String,
    pub sender: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedEmergencyExitRequest {
    pub position: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub sender: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmergencyExitChainValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub token_programs_standard_spl: bool,
    pub user_token_x_valid: bool,
    pub user_token_y_valid: bool,
    pub bin_array_lower: String,
    pub bin_array_upper: String,
    pub bitmap_extension: Option<String>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedEmergencyExitReport {
    pub validation: EmergencyExitChainValidation,
    pub request: EmergencyExitRequest,
    pub build: EmergencyExitBuildReport,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmergencyExitBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub removes_all_liquidity: bool,
    pub claims_fees_or_rewards: bool,
    pub closes_position_account: bool,
}


fn parse_pubkey(name: &str, value: &str) -> Result<Pubkey> {
    Pubkey::from_str(value.trim())
        .with_context(|| format!("{name} is not a valid Solana pubkey"))
}


fn require_account<'a>(
    name: &str,
    account: &'a Option<solana_sdk::account::Account>,
) -> Result<&'a solana_sdk::account::Account> {
    account.as_ref().with_context(|| format!("{name} account is missing"))
}


fn validate_standard_token_account(
    name: &str,
    account: &solana_sdk::account::Account,
    expected_mint: &Pubkey,
    expected_owner: &Pubkey,
    token_program: &Pubkey,
) -> Result<()> {
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by the standard SPL Token program");
    }
    if account.data.len() < 64 {
        anyhow::bail!("{name} token account data is too short");
    }
    let mint = Pubkey::new_from_array(
        account.data[0..32]
            .try_into()
            .context("invalid token-account mint bytes")?,
    );
    let owner = Pubkey::new_from_array(
        account.data[32..64]
            .try_into()
            .context("invalid token-account owner bytes")?,
    );
    if mint != *expected_mint {
        anyhow::bail!("{name} mint does not match the DLMM pool mint");
    }
    if owner != *expected_owner {
        anyhow::bail!("{name} token-account owner does not match sender");
    }
    Ok(())
}


pub fn build_standard_spl_emergency_exit_from_chain(
    rpc_url: &str,
    request: &ChainResolvedEmergencyExitRequest,
) -> Result<ChainResolvedEmergencyExitReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let user_token_x_key = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y_key = parse_pubkey("user_token_y", &request.user_token_y)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
    let client = RpcClient::new(rpc_url.to_string());

    let position_account = client
        .get_account(&position_key)
        .context("failed to fetch emergency-exit position")?;
    if position_account.owner != dlmm::ID {
        anyhow::bail!("position is not owned by the Meteora DLMM program");
    }
    let position: PositionV2 =
        pod_read_unaligned_skip_disc(&position_account.data)
            .context("failed to decode Meteora PositionV2")?;
    if position.owner != sender {
        anyhow::bail!("executor sender does not own the Meteora position");
    }

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch emergency-exit DLMM pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("DLMM pool is not owned by the Meteora program");
    }
    let lb_pair: LbPair = pod_read_unaligned_skip_disc(&lb_pair_account.data)
        .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    if token_x_program != token_program || token_y_program != token_program {
        anyhow::bail!(
            "chain-resolved emergency exit currently supports standard SPL pools only"
        );
    }

    let bin_arrays = position.get_bin_array_keys_coverage()?;
    if bin_arrays.len() != 2 {
        anyhow::bail!(
            "expected exactly two bin-array accounts for PositionV2, got {}",
            bin_arrays.len()
        );
    }
    let bin_array_lower = bin_arrays[0];
    let bin_array_upper = bin_arrays[1];

    let (bitmap_key, _) = derive_bin_array_bitmap_extension(lb_pair_key);
    let accounts = client
        .get_multiple_accounts(&[
            user_token_x_key,
            user_token_y_key,
            lb_pair.reserve_x,
            lb_pair.reserve_y,
            lb_pair.token_x_mint,
            lb_pair.token_y_mint,
            bin_array_lower,
            bin_array_upper,
            bitmap_key,
        ])
        .context("failed to fetch emergency-exit dependent accounts")?;
    if accounts.len() != 9 {
        anyhow::bail!("unexpected emergency-exit account fetch result");
    }

    validate_standard_token_account(
        "user_token_x",
        require_account("user_token_x", &accounts[0])?,
        &lb_pair.token_x_mint,
        &sender,
        &token_program,
    )?;
    validate_standard_token_account(
        "user_token_y",
        require_account("user_token_y", &accounts[1])?,
        &lb_pair.token_y_mint,
        &sender,
        &token_program,
    )?;

    for (name, account) in [
        ("reserve_x", &accounts[2]),
        ("reserve_y", &accounts[3]),
        ("token_x_mint", &accounts[4]),
        ("token_y_mint", &accounts[5]),
        ("bin_array_lower", &accounts[6]),
        ("bin_array_upper", &accounts[7]),
    ] {
        require_account(name, account)?;
    }
    if require_account("reserve_x", &accounts[2])?.owner != token_program
        || require_account("reserve_y", &accounts[3])?.owner != token_program
    {
        anyhow::bail!("DLMM reserve accounts are not standard SPL Token accounts");
    }
    if require_account("token_x_mint", &accounts[4])?.owner != token_program
        || require_account("token_y_mint", &accounts[5])?.owner != token_program
    {
        anyhow::bail!("DLMM mint accounts are not standard SPL Token mints");
    }
    if require_account("bin_array_lower", &accounts[6])?.owner != dlmm::ID
        || require_account("bin_array_upper", &accounts[7])?.owner != dlmm::ID
    {
        anyhow::bail!("position bin arrays are not owned by the Meteora program");
    }

    let bitmap_extension = match &accounts[8] {
        Some(account) => {
            if account.owner != dlmm::ID {
                anyhow::bail!(
                    "bin-array bitmap extension is not owned by Meteora DLMM"
                );
            }
            Some(bitmap_key.to_string())
        }
        None => None,
    };

    let resolved = EmergencyExitRequest {
        position: position_key.to_string(),
        lb_pair: lb_pair_key.to_string(),
        bin_array_bitmap_extension: bitmap_extension.clone(),
        user_token_x: user_token_x_key.to_string(),
        user_token_y: user_token_y_key.to_string(),
        reserve_x: lb_pair.reserve_x.to_string(),
        reserve_y: lb_pair.reserve_y.to_string(),
        token_x_mint: lb_pair.token_x_mint.to_string(),
        token_y_mint: lb_pair.token_y_mint.to_string(),
        bin_array_lower: bin_array_lower.to_string(),
        bin_array_upper: bin_array_upper.to_string(),
        sender: sender.to_string(),
    };
    let build = build_standard_spl_emergency_exit(&resolved)?;

    Ok(ChainResolvedEmergencyExitReport {
        validation: EmergencyExitChainValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            token_programs_standard_spl: true,
            user_token_x_valid: true,
            user_token_y_valid: true,
            bin_array_lower: bin_array_lower.to_string(),
            bin_array_upper: bin_array_upper.to_string(),
            bitmap_extension,
        },
        request: resolved,
        build,
    })
}


pub fn build_standard_spl_emergency_exit(
    request: &EmergencyExitRequest,
) -> Result<EmergencyExitBuildReport> {
    let position = parse_pubkey("position", &request.position)?;
    let lb_pair = parse_pubkey("lb_pair", &request.lb_pair)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let reserve_x = parse_pubkey("reserve_x", &request.reserve_x)?;
    let reserve_y = parse_pubkey("reserve_y", &request.reserve_y)?;
    let token_x_mint = parse_pubkey("token_x_mint", &request.token_x_mint)?;
    let token_y_mint = parse_pubkey("token_y_mint", &request.token_y_mint)?;
    let bin_array_lower =
        parse_pubkey("bin_array_lower", &request.bin_array_lower)?;
    let bin_array_upper =
        parse_pubkey("bin_array_upper", &request.bin_array_upper)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
    let bitmap_extension = match request.bin_array_bitmap_extension.as_deref() {
        Some(value) => Some(parse_pubkey(
            "bin_array_bitmap_extension",
            value,
        )?),
        None => Some(dlmm::ID),
    };
    let (event_authority, _) = derive_event_authority_pda();

    let accounts = dlmm::client::accounts::RemoveAllLiquidity {
        position,
        lb_pair,
        bin_array_bitmap_extension: bitmap_extension,
        user_token_x,
        user_token_y,
        reserve_x,
        reserve_y,
        token_x_mint,
        token_y_mint,
        bin_array_lower,
        bin_array_upper,
        sender,
        token_x_program: token_program,
        token_y_program: token_program,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);

    let data = dlmm::client::args::RemoveAllLiquidity {}.data();
    let instruction = Instruction {
        program_id: dlmm::ID,
        accounts,
        data,
    };
    let message = Message::new(&[instruction], Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "emergency exit unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize emergency exit transaction")?,
    );

    Ok(EmergencyExitBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position.to_string(),
        lb_pair: lb_pair.to_string(),
        sender: sender.to_string(),
        removes_all_liquidity: true,
        claims_fees_or_rewards: false,
        closes_position_account: false,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::transaction_guard::{
        check_transaction, ProgramInstructionPolicy, TransactionGuardConfig,
    };
    use uuid::Uuid;

    fn request() -> EmergencyExitRequest {
        EmergencyExitRequest {
            position: Pubkey::new_unique().to_string(),
            lb_pair: Pubkey::new_unique().to_string(),
            bin_array_bitmap_extension: None,
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            reserve_x: Pubkey::new_unique().to_string(),
            reserve_y: Pubkey::new_unique().to_string(),
            token_x_mint: Pubkey::new_unique().to_string(),
            token_y_mint: Pubkey::new_unique().to_string(),
            bin_array_lower: Pubkey::new_unique().to_string(),
            bin_array_upper: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
        }
    }

    #[test]
    fn emergency_exit_builds_unsigned_dlmm_transaction() {
        let request = request();
        let report = build_standard_spl_emergency_exit(&request).unwrap();

        assert!(report.removes_all_liquidity);
        assert!(!report.claims_fees_or_rewards);
        assert!(!report.closes_position_account);
        assert_eq!(report.program_id, dlmm::ID.to_string());

        let proposal = TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Exit,
            pool_address: request.lb_pair.clone(),
            capital_quote: 0.0,
            account_equity_quote: 0.0,
            portfolio_deployed_quote: 0.0,
            daily_drawdown_pct: 99.0,
            min_bin_id: 0,
            max_bin_id: 0,
            strategy: "SPOT".into(),
            expected_net_return_pct: 0.0,
            expected_downside_pct: 0.0,
            model_version: "emergency".into(),
            data_age_seconds: u64::MAX,
        };
        let guard = check_transaction(
            &proposal,
            &report.transaction_base64,
            &TransactionGuardConfig {
                expected_fee_payer: request.sender.clone(),
                allowed_program_ids: vec![dlmm::ID.to_string()],
                max_instructions: 2,
                max_static_accounts: 32,
                allow_address_lookup_tables: false,
                require_unsigned: true,
                require_instruction_policy: true,
                instruction_policies: vec![ProgramInstructionPolicy {
                    program_id: dlmm::ID.to_string(),
                    allowed_data_prefixes_hex: vec![
                        "0a333d2370691855".into(),
                    ],
                }],
            },
        )
        .unwrap();

        assert!(guard.accepted);
        assert!(guard.pool_account_present);
        assert!(guard.signatures_all_default);
    }

    #[test]
    fn invalid_account_key_fails_closed() {
        let mut request = request();
        request.position = "not-a-pubkey".into();
        assert!(build_standard_spl_emergency_exit(&request).is_err());
    }
}
