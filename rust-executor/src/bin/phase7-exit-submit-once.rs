use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::{Keypair, Signature, Signer};
use std::path::Path;
use std::str::FromStr;

#[path = "../simulation.rs"]
mod simulation;
#[path = "../wallet.rs"]
mod wallet;


const FORMAT_VERSION: u64 = 1;
const ARTIFACT_TYPE: &str =
    "PHASE7_CONTROLLED_LIVE_EXIT_SINGLE_EXECUTION_REQUEST_V1";
const LIVE_SUBMIT_ENV: &str = "PIO_LIVE_SUBMIT_ENABLED";
const STATUS_PENDING: &str = "SIGNED_PENDING_SEND";
const STATUS_ACCEPTED: &str = "RPC_ACCEPTED";
const STATUS_UNCERTAIN: &str = "RPC_UNCERTAIN";


#[derive(Debug, Clone, Deserialize)]
struct ExitSubmissionRequest {
    format_version: u64,
    artifact_type: String,
    request_sha256: String,
    exit_decision_id: String,
    executor_wallet_pubkey: String,
    final_transaction_base64: String,
    final_transaction_sha256: String,
    recent_blockhash: String,
    last_valid_block_height: u64,
    single_execution_request_ready: bool,
    runtime_live_submit_opt_in_required: bool,
    dedicated_submission_journal_required: bool,
    atomic_first_submission_claim_required: bool,
    signature_persist_before_rpc_send_required: bool,
    rpc_max_retries_zero_required: bool,
    automatic_retry_prohibited: bool,
    uncertain_rpc_requires_recovery: bool,
}


#[derive(Debug, Clone, Serialize)]
struct SignedExitTransaction {
    signature: String,
    transaction_base64: String,
}


#[derive(Debug, Clone, Serialize)]
struct ExitSubmissionReport {
    request_sha256: String,
    exit_decision_id: String,
    final_transaction_sha256: String,
    signature: String,
    journal_status: String,
    journal_claim_persisted_before_rpc_send: bool,
    current_block_height: u64,
    last_valid_block_height: u64,
    blockhash_not_expired_at_execution: bool,
    rpc_max_retries: usize,
    automatic_retry_performed: bool,
    rpc_accepted: bool,
    rpc_error: Option<String>,
    uncertain_rpc_requires_recovery: bool,
    signing_performed: bool,
    submission_attempted_once: bool,
}


fn sha256_hex(payload: &[u8]) -> String {
    solana_sdk::hash::hash(payload)
        .to_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>()
}


fn validate_hex_digest(name: &str, value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        anyhow::bail!("{name} must be a lowercase SHA-256 hex digest");
    }
    Ok(())
}


fn validate_request(request: &ExitSubmissionRequest) -> Result<()> {
    if request.format_version != FORMAT_VERSION {
        anyhow::bail!("unsupported Phase 7 EXIT single-execution request format");
    }
    if request.artifact_type != ARTIFACT_TYPE {
        anyhow::bail!("unexpected Phase 7 EXIT single-execution request type");
    }
    validate_hex_digest("request_sha256", &request.request_sha256)?;
    validate_hex_digest(
        "final_transaction_sha256",
        &request.final_transaction_sha256,
    )?;
    if request.exit_decision_id.trim().is_empty() {
        anyhow::bail!("exit_decision_id is required");
    }
    Pubkey::from_str(request.executor_wallet_pubkey.trim())
        .context("executor_wallet_pubkey is invalid")?;
    if request.final_transaction_base64.trim().is_empty() {
        anyhow::bail!("final_transaction_base64 is required");
    }
    if request.recent_blockhash.trim().is_empty() {
        anyhow::bail!("recent_blockhash is required");
    }
    if request.last_valid_block_height == 0 {
        anyhow::bail!("last_valid_block_height must be positive");
    }
    for (name, value) in [
        (
            "single_execution_request_ready",
            request.single_execution_request_ready,
        ),
        (
            "runtime_live_submit_opt_in_required",
            request.runtime_live_submit_opt_in_required,
        ),
        (
            "dedicated_submission_journal_required",
            request.dedicated_submission_journal_required,
        ),
        (
            "atomic_first_submission_claim_required",
            request.atomic_first_submission_claim_required,
        ),
        (
            "signature_persist_before_rpc_send_required",
            request.signature_persist_before_rpc_send_required,
        ),
        (
            "rpc_max_retries_zero_required",
            request.rpc_max_retries_zero_required,
        ),
        (
            "automatic_retry_prohibited",
            request.automatic_retry_prohibited,
        ),
        (
            "uncertain_rpc_requires_recovery",
            request.uncertain_rpc_requires_recovery,
        ),
    ] {
        if !value {
            anyhow::bail!("Phase 7 EXIT submitter requires {name}=true");
        }
    }
    let transaction_digest =
        sha256_hex(request.final_transaction_base64.as_bytes());
    if transaction_digest != request.final_transaction_sha256 {
        anyhow::bail!(
            "final transaction bytes do not match request SHA-256"
        );
    }
    Ok(())
}


fn message_metadata(
    message: &VersionedMessage,
) -> Result<(Pubkey, usize, String)> {
    match message {
        VersionedMessage::Legacy(message) => Ok((
            *message
                .account_keys
                .first()
                .context("EXIT transaction has no fee payer")?,
            usize::from(message.header.num_required_signatures),
            message.recent_blockhash.to_string(),
        )),
        VersionedMessage::V0(message) => Ok((
            *message
                .account_keys
                .first()
                .context("EXIT transaction has no fee payer")?,
            usize::from(message.header.num_required_signatures),
            message.recent_blockhash.to_string(),
        )),
    }
}


fn sign_exact_transaction(
    request: &ExitSubmissionRequest,
    keypair: &Keypair,
) -> Result<SignedExitTransaction> {
    let expected_wallet = Pubkey::from_str(
        request.executor_wallet_pubkey.trim(),
    )
    .context("executor_wallet_pubkey is invalid")?;
    if keypair.pubkey() != expected_wallet {
        anyhow::bail!(
            "loaded executor keypair differs from EXIT execution request wallet"
        );
    }

    let mut transaction = simulation::decode_transaction_base64(
        &request.final_transaction_base64,
    )?;
    if !transaction
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default())
    {
        anyhow::bail!("EXIT transaction already contains a signature");
    }

    let (fee_payer, required_signatures, blockhash) =
        message_metadata(&transaction.message)?;
    if fee_payer != expected_wallet {
        anyhow::bail!(
            "EXIT transaction fee payer differs from expected executor wallet"
        );
    }
    if required_signatures != 1 {
        anyhow::bail!(
            "EXIT submitter requires exactly one transaction signer; found {required_signatures}"
        );
    }
    if transaction.signatures.len() != required_signatures {
        anyhow::bail!(
            "EXIT transaction signature vector length does not match required signer count"
        );
    }
    if blockhash != request.recent_blockhash {
        anyhow::bail!(
            "EXIT transaction blockhash differs from execution request"
        );
    }

    let message_bytes = transaction.message.serialize();
    let signature = keypair.sign_message(&message_bytes);
    transaction.signatures[0] = signature;
    let bytes = bincode::serialize(&transaction)
        .context("failed to serialize signed EXIT transaction")?;

    Ok(SignedExitTransaction {
        signature: signature.to_string(),
        transaction_base64: general_purpose::STANDARD.encode(bytes),
    })
}


fn open_journal(path: &Path) -> Result<Connection> {
    let connection = Connection::open(path)
        .with_context(|| {
            format!(
                "failed to open Phase 7 EXIT submission journal: {}",
                path.display()
            )
        })?;
    connection.execute_batch(
        "
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS phase7_exit_submission_journal (
            request_sha256 TEXT PRIMARY KEY,
            final_transaction_sha256 TEXT NOT NULL UNIQUE,
            exit_decision_id TEXT NOT NULL,
            signature TEXT NOT NULL,
            signed_transaction_base64 TEXT NOT NULL,
            status TEXT NOT NULL,
            rpc_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        ",
    )?;
    Ok(connection)
}


fn existing_claim(
    connection: &Connection,
    request: &ExitSubmissionRequest,
) -> Result<Option<(String, String)>> {
    connection
        .query_row(
            "
            SELECT status, signature
            FROM phase7_exit_submission_journal
            WHERE request_sha256 = ?1
               OR final_transaction_sha256 = ?2
            LIMIT 1
            ",
            params![
                request.request_sha256,
                request.final_transaction_sha256,
            ],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .context("failed to inspect Phase 7 EXIT submission journal")
}


fn persist_first_claim(
    connection: &mut Connection,
    request: &ExitSubmissionRequest,
    signed: &SignedExitTransaction,
) -> Result<()> {
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .context("failed to begin Phase 7 EXIT first-submission claim")?;

    if let Some((status, signature)) = existing_claim(&tx, request)? {
        anyhow::bail!(
            "existing Phase 7 EXIT submission requires recovery; status={status}; signature={signature}"
        );
    }

    tx.execute(
        "
        INSERT INTO phase7_exit_submission_journal (
            request_sha256,
            final_transaction_sha256,
            exit_decision_id,
            signature,
            signed_transaction_base64,
            status,
            rpc_error
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL)
        ",
        params![
            request.request_sha256,
            request.final_transaction_sha256,
            request.exit_decision_id,
            signed.signature,
            signed.transaction_base64,
            STATUS_PENDING,
        ],
    )
    .context("failed to persist Phase 7 EXIT signed first-submission claim")?;
    tx.commit()
        .context("failed to commit Phase 7 EXIT first-submission claim")?;
    Ok(())
}


fn update_status(
    connection: &Connection,
    request_sha256: &str,
    status: &str,
    rpc_error: Option<&str>,
) -> Result<()> {
    let changed = connection.execute(
        "
        UPDATE phase7_exit_submission_journal
        SET status = ?2,
            rpc_error = ?3,
            updated_at = CURRENT_TIMESTAMP
        WHERE request_sha256 = ?1
          AND status = ?4
        ",
        params![
            request_sha256,
            status,
            rpc_error,
            STATUS_PENDING,
        ],
    )?;
    if changed != 1 {
        anyhow::bail!(
            "Phase 7 EXIT journal state changed before RPC result could be persisted"
        );
    }
    Ok(())
}


fn submit_once_with<F>(
    request: &ExitSubmissionRequest,
    journal_path: &Path,
    keypair: &Keypair,
    current_block_height: u64,
    send: F,
) -> Result<ExitSubmissionReport>
where
    F: FnOnce(&SignedExitTransaction) -> Result<String>,
{
    validate_request(request)?;
    if current_block_height > request.last_valid_block_height {
        anyhow::bail!(
            "Phase 7 EXIT transaction blockhash expired before submission"
        );
    }

    let signed = sign_exact_transaction(request, keypair)?;
    let mut journal = open_journal(journal_path)?;
    persist_first_claim(&mut journal, request, &signed)?;

    let send_result = send(&signed);
    match send_result {
        Ok(observed_signature) => {
            let observed = observed_signature.trim();
            if observed == signed.signature {
                update_status(
                    &journal,
                    &request.request_sha256,
                    STATUS_ACCEPTED,
                    None,
                )?;
                Ok(ExitSubmissionReport {
                    request_sha256: request.request_sha256.clone(),
                    exit_decision_id: request.exit_decision_id.clone(),
                    final_transaction_sha256:
                        request.final_transaction_sha256.clone(),
                    signature: signed.signature,
                    journal_status: STATUS_ACCEPTED.into(),
                    journal_claim_persisted_before_rpc_send: true,
                    current_block_height,
                    last_valid_block_height:
                        request.last_valid_block_height,
                    blockhash_not_expired_at_execution: true,
                    rpc_max_retries: 0,
                    automatic_retry_performed: false,
                    rpc_accepted: true,
                    rpc_error: None,
                    uncertain_rpc_requires_recovery: true,
                    signing_performed: true,
                    submission_attempted_once: true,
                })
            } else {
                let error = format!(
                    "RPC returned signature {observed} but persisted signed transaction is {}",
                    signed.signature
                );
                update_status(
                    &journal,
                    &request.request_sha256,
                    STATUS_UNCERTAIN,
                    Some(&error),
                )?;
                Ok(ExitSubmissionReport {
                    request_sha256: request.request_sha256.clone(),
                    exit_decision_id: request.exit_decision_id.clone(),
                    final_transaction_sha256:
                        request.final_transaction_sha256.clone(),
                    signature: signed.signature,
                    journal_status: STATUS_UNCERTAIN.into(),
                    journal_claim_persisted_before_rpc_send: true,
                    current_block_height,
                    last_valid_block_height:
                        request.last_valid_block_height,
                    blockhash_not_expired_at_execution: true,
                    rpc_max_retries: 0,
                    automatic_retry_performed: false,
                    rpc_accepted: false,
                    rpc_error: Some(error),
                    uncertain_rpc_requires_recovery: true,
                    signing_performed: true,
                    submission_attempted_once: true,
                })
            }
        }
        Err(error) => {
            let error_text = error.to_string();
            update_status(
                &journal,
                &request.request_sha256,
                STATUS_UNCERTAIN,
                Some(&error_text),
            )?;
            Ok(ExitSubmissionReport {
                request_sha256: request.request_sha256.clone(),
                exit_decision_id: request.exit_decision_id.clone(),
                final_transaction_sha256:
                    request.final_transaction_sha256.clone(),
                signature: signed.signature,
                journal_status: STATUS_UNCERTAIN.into(),
                journal_claim_persisted_before_rpc_send: true,
                current_block_height,
                last_valid_block_height: request.last_valid_block_height,
                blockhash_not_expired_at_execution: true,
                rpc_max_retries: 0,
                automatic_retry_performed: false,
                rpc_accepted: false,
                rpc_error: Some(error_text),
                uncertain_rpc_requires_recovery: true,
                signing_performed: true,
                submission_attempted_once: true,
            })
        }
    }
}


fn load_request(path: &Path) -> Result<ExitSubmissionRequest> {
    let raw = std::fs::read_to_string(path)
        .with_context(|| {
            format!(
                "failed to read Phase 7 EXIT single-execution request: {}",
                path.display()
            )
        })?;
    serde_json::from_str(&raw)
        .context("invalid Phase 7 EXIT single-execution request JSON")
}


#[cfg(feature = "live-submit")]
fn main() -> Result<()> {
    use solana_client::rpc_client::RpcClient;
    use solana_client::rpc_config::RpcSendTransactionConfig;
    use solana_sdk::commitment_config::CommitmentLevel;

    if std::env::var(LIVE_SUBMIT_ENV).unwrap_or_default() != "1" {
        anyhow::bail!(
            "Phase 7 EXIT live submission is runtime-disabled; set PIO_LIVE_SUBMIT_ENABLED=1 only for an explicitly approved single EXIT execution"
        );
    }

    let mut args = std::env::args().skip(1);
    let request_path = args
        .next()
        .context("EXIT_SINGLE_EXECUTION_REQUEST_JSON is required")?;
    let journal_path = args
        .next()
        .context("EXIT_SUBMISSION_JOURNAL_DB is required")?;
    if args.next().is_some() {
        anyhow::bail!(
            "phase7-exit-submit-once accepts exactly two arguments"
        );
    }

    let request = load_request(Path::new(&request_path))?;
    validate_request(&request)?;
    let keypair = wallet::load_executor_keypair_from_env()?;
    let rpc_url = std::env::var("SOLANA_RPC_URL")
        .or_else(|_| std::env::var("RPC_URL"))
        .context(
            "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
        )?;
    let client = RpcClient::new(rpc_url);
    let current_block_height = client
        .get_block_height()
        .context("failed to fetch current Solana block height")?;

    let report = submit_once_with(
        &request,
        Path::new(&journal_path),
        &keypair,
        current_block_height,
        |signed| {
            let transaction = simulation::decode_transaction_base64(
                &signed.transaction_base64,
            )?;
            let observed = client
                .send_transaction_with_config(
                    &transaction,
                    RpcSendTransactionConfig {
                        skip_preflight: false,
                        preflight_commitment: Some(
                            CommitmentLevel::Confirmed,
                        ),
                        max_retries: Some(0),
                        ..RpcSendTransactionConfig::default()
                    },
                )
                .context("Phase 7 EXIT send_transaction RPC failed")?;
            Ok(observed.to_string())
        },
    )?;

    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.rpc_accepted {
        std::process::exit(2);
    }
    Ok(())
}


#[cfg(not(feature = "live-submit"))]
fn main() -> Result<()> {
    anyhow::bail!(
        "phase7-exit-submit-once requires the live-submit Cargo feature"
    )
}


#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::hash::Hash;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::transaction::VersionedTransaction;
    use std::cell::Cell;
    use uuid::Uuid;

    fn temp_db() -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "pio-phase7-exit-submission-{}.db",
            Uuid::new_v4()
        ))
    }

    fn fixture(
        keypair: &Keypair,
        last_valid_block_height: u64,
    ) -> ExitSubmissionRequest {
        let mut message = Message::new(&[], Some(&keypair.pubkey()));
        let blockhash = Hash::new_unique();
        message.recent_blockhash = blockhash;
        let transaction = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        let encoded = general_purpose::STANDARD.encode(
            bincode::serialize(&transaction).unwrap(),
        );
        ExitSubmissionRequest {
            format_version: FORMAT_VERSION,
            artifact_type: ARTIFACT_TYPE.into(),
            request_sha256: "1".repeat(64),
            exit_decision_id:
                "01234567-89ab-4def-8123-456789abcdef".into(),
            executor_wallet_pubkey: keypair.pubkey().to_string(),
            final_transaction_sha256: sha256_hex(encoded.as_bytes()),
            final_transaction_base64: encoded,
            recent_blockhash: blockhash.to_string(),
            last_valid_block_height,
            single_execution_request_ready: true,
            runtime_live_submit_opt_in_required: true,
            dedicated_submission_journal_required: true,
            atomic_first_submission_claim_required: true,
            signature_persist_before_rpc_send_required: true,
            rpc_max_retries_zero_required: true,
            automatic_retry_prohibited: true,
            uncertain_rpc_requires_recovery: true,
        }
    }

    #[test]
    fn signature_is_persisted_before_send() {
        let keypair = Keypair::new();
        let request = fixture(&keypair, 1000);
        let path = temp_db();
        let saw_pending = Cell::new(false);

        let report = submit_once_with(
            &request,
            &path,
            &keypair,
            900,
            |signed| {
                let connection = open_journal(&path).unwrap();
                let row: (String, String) = connection
                    .query_row(
                        "
                        SELECT status, signature
                        FROM phase7_exit_submission_journal
                        WHERE request_sha256 = ?1
                        ",
                        params![request.request_sha256],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .unwrap();
                saw_pending.set(
                    row.0 == STATUS_PENDING
                        && row.1 == signed.signature,
                );
                Ok(signed.signature.clone())
            },
        )
        .unwrap();

        assert!(saw_pending.get());
        assert!(report.rpc_accepted);
        assert_eq!(report.journal_status, STATUS_ACCEPTED);
        assert_eq!(report.rpc_max_retries, 0);
        assert!(!report.automatic_retry_performed);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rerun_is_refused_before_second_send() {
        let keypair = Keypair::new();
        let request = fixture(&keypair, 1000);
        let path = temp_db();
        let first_called = Cell::new(false);

        submit_once_with(
            &request,
            &path,
            &keypair,
            900,
            |signed| {
                first_called.set(true);
                Ok(signed.signature.clone())
            },
        )
        .unwrap();
        assert!(first_called.get());

        let second_called = Cell::new(false);
        let result = submit_once_with(
            &request,
            &path,
            &keypair,
            901,
            |_| {
                second_called.set(true);
                Ok("unexpected".into())
            },
        );

        assert!(result.is_err());
        assert!(!second_called.get());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("requires recovery")
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rpc_failure_becomes_uncertain_and_cannot_retry() {
        let keypair = Keypair::new();
        let request = fixture(&keypair, 1000);
        let path = temp_db();

        let report = submit_once_with(
            &request,
            &path,
            &keypair,
            900,
            |_| anyhow::bail!("network outcome unknown"),
        )
        .unwrap();

        assert!(!report.rpc_accepted);
        assert_eq!(report.journal_status, STATUS_UNCERTAIN);
        assert!(report.uncertain_rpc_requires_recovery);

        let second_called = Cell::new(false);
        let result = submit_once_with(
            &request,
            &path,
            &keypair,
            901,
            |_| {
                second_called.set(true);
                Ok("unexpected".into())
            },
        );
        assert!(result.is_err());
        assert!(!second_called.get());

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn expired_blockhash_rejects_before_journal_claim() {
        let keypair = Keypair::new();
        let request = fixture(&keypair, 1000);
        let path = temp_db();
        let called = Cell::new(false);

        let result = submit_once_with(
            &request,
            &path,
            &keypair,
            1001,
            |_| {
                called.set(true);
                Ok("unexpected".into())
            },
        );

        assert!(result.is_err());
        assert!(!called.get());
        assert!(!path.exists());
    }

    #[test]
    fn wrong_keypair_rejects_before_journal_claim() {
        let expected = Keypair::new();
        let other = Keypair::new();
        let request = fixture(&expected, 1000);
        let path = temp_db();

        let result = submit_once_with(
            &request,
            &path,
            &other,
            900,
            |_| Ok("unexpected".into()),
        );

        assert!(result.is_err());
        assert!(!path.exists());
    }

    #[test]
    fn signed_input_is_rejected() {
        let keypair = Keypair::new();
        let mut request = fixture(&keypair, 1000);
        let signed = sign_exact_transaction(&request, &keypair).unwrap();
        request.final_transaction_base64 = signed.transaction_base64;
        request.final_transaction_sha256 =
            sha256_hex(request.final_transaction_base64.as_bytes());
        let path = temp_db();

        let result = submit_once_with(
            &request,
            &path,
            &keypair,
            900,
            |_| Ok("unexpected".into()),
        );

        assert!(result.is_err());
        assert!(!path.exists());
    }
}
