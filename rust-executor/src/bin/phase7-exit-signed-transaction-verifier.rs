use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use std::path::Path;
use std::str::FromStr;

#[path = "../simulation.rs"]
mod simulation;


const FORMAT_VERSION: u64 = 1;
const ARTIFACT_TYPE: &str =
    "PHASE7_CONTROLLED_LIVE_EXIT_SINGLE_EXECUTION_REQUEST_V1";


#[derive(Debug, Clone, Deserialize)]
struct ExitSingleExecutionRequest {
    format_version: u64,
    artifact_type: String,
    request_sha256: String,
    executor_wallet_pubkey: String,
    final_transaction_base64: String,
    final_transaction_sha256: String,
    recent_blockhash: String,
    single_execution_request_ready: bool,
    exact_transaction_authorization_verified: bool,
    keypair_identity_verified: bool,
    blockhash_not_expired: bool,
    final_transaction_unsigned: bool,
    exact_simulation_succeeded: bool,
    automatic_retry_prohibited: bool,
}


#[derive(Debug, Clone, Serialize)]
struct SignedExitVerificationReport {
    request_sha256: String,
    final_transaction_sha256: String,
    signed_transaction_sha256: String,
    signature: String,
    executor_wallet_pubkey: String,
    recent_blockhash: String,
    unsigned_message_matches_signed_message: bool,
    fee_payer_matches_executor: bool,
    blockhash_matches_request: bool,
    single_required_signature: bool,
    signed_transaction_has_one_signature: bool,
    signature_non_default: bool,
    signature_verified: bool,
    exact_signed_exit_transaction_verified: bool,
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


fn validate_request(request: &ExitSingleExecutionRequest) -> Result<()> {
    if request.format_version != FORMAT_VERSION {
        anyhow::bail!(
            "unsupported Phase 7 EXIT single-execution request format"
        );
    }
    if request.artifact_type != ARTIFACT_TYPE {
        anyhow::bail!(
            "unexpected Phase 7 EXIT single-execution request type"
        );
    }
    validate_hex_digest("request_sha256", &request.request_sha256)?;
    validate_hex_digest(
        "final_transaction_sha256",
        &request.final_transaction_sha256,
    )?;
    Pubkey::from_str(request.executor_wallet_pubkey.trim())
        .context("executor_wallet_pubkey is invalid")?;
    if request.final_transaction_base64.trim().is_empty() {
        anyhow::bail!("final_transaction_base64 is required");
    }
    if request.recent_blockhash.trim().is_empty() {
        anyhow::bail!("recent_blockhash is required");
    }
    for (name, value) in [
        (
            "single_execution_request_ready",
            request.single_execution_request_ready,
        ),
        (
            "exact_transaction_authorization_verified",
            request.exact_transaction_authorization_verified,
        ),
        ("keypair_identity_verified", request.keypair_identity_verified),
        ("blockhash_not_expired", request.blockhash_not_expired),
        (
            "final_transaction_unsigned",
            request.final_transaction_unsigned,
        ),
        (
            "exact_simulation_succeeded",
            request.exact_simulation_succeeded,
        ),
        (
            "automatic_retry_prohibited",
            request.automatic_retry_prohibited,
        ),
    ] {
        if !value {
            anyhow::bail!(
                "Phase 7 EXIT signed-transaction verifier requires {name}=true"
            );
        }
    }

    let expected_digest =
        sha256_hex(request.final_transaction_base64.as_bytes());
    if expected_digest != request.final_transaction_sha256 {
        anyhow::bail!(
            "final transaction bytes do not match request SHA-256"
        );
    }
    Ok(())
}


fn verify_signed_transaction(
    request: &ExitSingleExecutionRequest,
    signed_transaction_base64: &str,
) -> Result<SignedExitVerificationReport> {
    validate_request(request)?;
    if signed_transaction_base64.trim().is_empty() {
        anyhow::bail!("signed transaction base64 is required");
    }

    let unsigned = simulation::decode_transaction_base64(
        &request.final_transaction_base64,
    )?;
    if !unsigned
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default())
    {
        anyhow::bail!(
            "sealed Phase 7 EXIT transaction is not actually unsigned"
        );
    }

    let signed = simulation::decode_transaction_base64(
        signed_transaction_base64,
    )?;
    let unsigned_message = unsigned.message.serialize();
    let signed_message = signed.message.serialize();
    if unsigned_message != signed_message {
        anyhow::bail!(
            "signed EXIT transaction message differs from sealed unsigned message"
        );
    }

    let expected_wallet = Pubkey::from_str(
        request.executor_wallet_pubkey.trim(),
    )
    .context("executor_wallet_pubkey is invalid")?;
    let (fee_payer, required_signatures, blockhash) =
        message_metadata(&signed.message)?;
    if fee_payer != expected_wallet {
        anyhow::bail!(
            "signed EXIT transaction fee payer differs from expected executor wallet"
        );
    }
    if required_signatures != 1 {
        anyhow::bail!(
            "signed EXIT transaction must require exactly one signature"
        );
    }
    if signed.signatures.len() != 1 {
        anyhow::bail!(
            "signed EXIT transaction must contain exactly one signature slot"
        );
    }
    let signature = signed.signatures[0];
    if signature == Signature::default() {
        anyhow::bail!(
            "signed EXIT transaction still contains the default signature"
        );
    }
    if blockhash != request.recent_blockhash {
        anyhow::bail!(
            "signed EXIT transaction blockhash differs from sealed request"
        );
    }
    if !signature.verify(expected_wallet.as_ref(), &signed_message) {
        anyhow::bail!(
            "signed EXIT transaction signature does not verify for expected executor wallet"
        );
    }

    Ok(SignedExitVerificationReport {
        request_sha256: request.request_sha256.clone(),
        final_transaction_sha256:
            request.final_transaction_sha256.clone(),
        signed_transaction_sha256:
            sha256_hex(signed_transaction_base64.trim().as_bytes()),
        signature: signature.to_string(),
        executor_wallet_pubkey: expected_wallet.to_string(),
        recent_blockhash: blockhash,
        unsigned_message_matches_signed_message: true,
        fee_payer_matches_executor: true,
        blockhash_matches_request: true,
        single_required_signature: true,
        signed_transaction_has_one_signature: true,
        signature_non_default: true,
        signature_verified: true,
        exact_signed_exit_transaction_verified: true,
    })
}


fn load_request(path: &Path) -> Result<ExitSingleExecutionRequest> {
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


fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let request_path = args
        .next()
        .context("EXIT_SINGLE_EXECUTION_REQUEST_JSON is required")?;
    let signed_transaction_path = args
        .next()
        .context("SIGNED_TRANSACTION_BASE64_FILE is required")?;
    if args.next().is_some() {
        anyhow::bail!(
            "phase7-exit-signed-transaction-verifier accepts exactly two arguments"
        );
    }

    let request = load_request(Path::new(&request_path))?;
    let signed_transaction_base64 =
        std::fs::read_to_string(&signed_transaction_path)
            .with_context(|| {
                format!(
                    "failed to read signed transaction base64 file: {signed_transaction_path}"
                )
            })?;
    let report = verify_signed_transaction(
        &request,
        signed_transaction_base64.trim(),
    )?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;
    use base64::{engine::general_purpose, Engine as _};
    use solana_sdk::hash::Hash;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::signature::{Keypair, Signer};
    use solana_sdk::transaction::VersionedTransaction;

    fn fixture(
        keypair: &Keypair,
    ) -> (ExitSingleExecutionRequest, VersionedTransaction) {
        let mut message = Message::new(&[], Some(&keypair.pubkey()));
        let blockhash = Hash::new_unique();
        message.recent_blockhash = blockhash;
        let unsigned = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        let unsigned_base64 = general_purpose::STANDARD.encode(
            bincode::serialize(&unsigned).unwrap(),
        );
        let request = ExitSingleExecutionRequest {
            format_version: FORMAT_VERSION,
            artifact_type: ARTIFACT_TYPE.into(),
            request_sha256: "a".repeat(64),
            executor_wallet_pubkey: keypair.pubkey().to_string(),
            final_transaction_sha256:
                sha256_hex(unsigned_base64.as_bytes()),
            final_transaction_base64: unsigned_base64,
            recent_blockhash: blockhash.to_string(),
            single_execution_request_ready: true,
            exact_transaction_authorization_verified: true,
            keypair_identity_verified: true,
            blockhash_not_expired: true,
            final_transaction_unsigned: true,
            exact_simulation_succeeded: true,
            automatic_retry_prohibited: true,
        };
        (request, unsigned)
    }

    fn sign(
        unsigned: &VersionedTransaction,
        keypair: &Keypair,
    ) -> String {
        let mut signed = unsigned.clone();
        let message = signed.message.serialize();
        signed.signatures[0] = keypair.sign_message(&message);
        general_purpose::STANDARD.encode(
            bincode::serialize(&signed).unwrap(),
        )
    }

    #[test]
    fn exact_message_and_expected_wallet_verify() {
        let keypair = Keypair::new();
        let (request, unsigned) = fixture(&keypair);
        let signed = sign(&unsigned, &keypair);

        let report = verify_signed_transaction(
            &request,
            &signed,
        )
        .unwrap();

        assert!(report.exact_signed_exit_transaction_verified);
        assert!(report.signature_verified);
        assert_eq!(
            report.executor_wallet_pubkey,
            keypair.pubkey().to_string()
        );
    }

    #[test]
    fn different_message_is_rejected() {
        let keypair = Keypair::new();
        let (request, _) = fixture(&keypair);

        let mut other_message =
            Message::new(&[], Some(&keypair.pubkey()));
        other_message.recent_blockhash = Hash::new_unique();
        let other = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(other_message),
        };
        let signed = sign(&other, &keypair);

        assert!(
            verify_signed_transaction(&request, &signed).is_err()
        );
    }

    #[test]
    fn wrong_wallet_signature_is_rejected() {
        let expected = Keypair::new();
        let other = Keypair::new();
        let (request, unsigned) = fixture(&expected);
        let signed = sign(&unsigned, &other);

        assert!(
            verify_signed_transaction(&request, &signed).is_err()
        );
    }

    #[test]
    fn default_signature_is_rejected() {
        let keypair = Keypair::new();
        let (request, unsigned) = fixture(&keypair);
        let encoded = general_purpose::STANDARD.encode(
            bincode::serialize(&unsigned).unwrap(),
        );

        assert!(
            verify_signed_transaction(&request, &encoded).is_err()
        );
    }
}
