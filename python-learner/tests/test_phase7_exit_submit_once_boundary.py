from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUBMITTER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-submit-once.rs"
)


def test_exit_submitter_is_single_shot_and_zero_retry():
    source = SUBMITTER.read_text(encoding="utf-8")

    assert "send_transaction_with_config" in source
    assert "max_retries: Some(0)" in source
    assert "skip_preflight: false" in source
    assert "CommitmentLevel::Confirmed" in source
    assert "SIGNED_PENDING_SEND" in source
    assert "RPC_ACCEPTED" in source
    assert "RPC_UNCERTAIN" in source
    assert "existing Phase 7 EXIT submission requires recovery" in source

    assert "send_and_confirm" not in source
    assert "submit_execution_intent_rpc" not in source
    assert "submit_new_execution_intent_rpc" not in source


def test_exit_submitter_persists_signature_before_network_send():
    source = SUBMITTER.read_text(encoding="utf-8")

    claim = source.index("persist_first_claim(&mut journal, request, &signed)?")
    send = source.index("let send_result = send(&signed);")

    assert claim < send
    assert "automatic_retry_performed: false" in source
    assert "uncertain_rpc_requires_recovery: true" in source


def test_exit_submitter_is_live_feature_and_runtime_gated():
    source = SUBMITTER.read_text(encoding="utf-8")

    assert '#[cfg(feature = "live-submit")]' in source
    assert 'const LIVE_SUBMIT_ENV: &str = "PIO_LIVE_SUBMIT_ENABLED"' in source
    assert 'std::env::var(LIVE_SUBMIT_ENV)' in source
    assert "load_executor_keypair_from_env" in source
