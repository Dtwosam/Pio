from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUBMITTER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-submit-verified-once.rs"
)


def _production_source() -> str:
    source = SUBMITTER.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_verified_exit_submitter_never_loads_or_uses_keypair():
    source = _production_source()

    assert "verify_exact_signed_transaction" in source
    assert "signature.verify" in source
    assert "unsigned_message != signed_message" in source
    assert "SIGNED_TRANSACTION_BASE64_FILE" in source

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "wallet.rs" not in source
    assert "Keypair" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "transaction.signatures[0] =" not in source


def test_verified_exit_submitter_is_single_shot_zero_retry():
    source = _production_source()

    assert "send_transaction_with_config" in source
    assert "max_retries: Some(0)" in source
    assert "skip_preflight: false" in source
    assert "CommitmentLevel::Confirmed" in source
    assert "VERIFIED_SIGNED_PENDING_SEND" in source
    assert "RPC_ACCEPTED" in source
    assert "RPC_UNCERTAIN" in source
    assert "existing Phase 7 EXIT submission requires recovery" in source

    assert "send_and_confirm" not in source
    assert "automatic_retry_performed: false" in source


def test_verified_exit_submitter_persists_before_rpc_send():
    source = _production_source()

    claim = source.index(
        "persist_first_claim(&mut journal, request, &signed)?"
    )
    send = source.index("let send_result = send(&signed);")

    assert claim < send
    assert "signed_transaction_sha256 TEXT NOT NULL UNIQUE" in source
    assert "uncertain_rpc_requires_recovery: true" in source


def test_verified_exit_submitter_is_feature_and_runtime_gated():
    source = _production_source()

    assert '#[cfg(feature = "live-submit")]' in source
    assert 'const LIVE_SUBMIT_ENV: &str = "PIO_LIVE_SUBMIT_ENABLED"' in source
    assert 'std::env::var(LIVE_SUBMIT_ENV)' in source
    assert "requires the live-submit Cargo feature" in source
